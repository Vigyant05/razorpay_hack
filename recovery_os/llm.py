"""LLM proposer (phase 3). Claude proposes; the deterministic engine disposes.

The model outputs a typed Diagnosis + ProposedAction via a strict tool schema.
It NEVER executes, signs, or bypasses the gate — its output is untrusted input
to the same policy path a heuristic proposal takes. Determinism + zero-cost CI
come from a disk record-replay cache keyed by a hash of the episode inputs; on
any fault (API error, cache miss with no key, off-enum, malformed) it logs a
fault to the ledger and falls back to the heuristic proposer.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from . import ledger
from .domain import (
    Diagnosis,
    DiagnosisFault,
    Episode,
    FailureCause,
    Intervention,
    LedgerStep,
    ProposedAction,
)
from .orchestrator import diagnose as heuristic_diagnose
from .orchestrator import propose as heuristic_propose

SCHEMA_VERSION = 1
_TOOL_NAME = "submit_recovery_plan"
_CAUSES = [c.value for c in FailureCause]
_INTERVENTIONS = [i.value for i in Intervention]

SYSTEM = (
    "You are the diagnosis engine for a payment-recovery system. Given one failed "
    "payment, identify the most likely failure cause and choose one bounded recovery "
    "intervention. You only PROPOSE — a separate deterministic policy gate decides "
    "whether the action runs. Call submit_recovery_plan exactly once. Choose cause "
    "and intervention only from the allowed enums."
)

TOOL = {
    "name": _TOOL_NAME,
    "description": "Submit the diagnosis and the proposed recovery intervention.",
    "strict": True,
    "input_schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["cause", "confidence", "diagnosis_rationale",
                     "intervention", "proposal_rationale"],
        "properties": {
            "cause": {"enum": _CAUSES},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "diagnosis_rationale": {"type": "string"},
            "intervention": {"enum": _INTERVENTIONS},
            "proposal_rationale": {"type": "string"},
        },
    },
}


def _signals(episode: Episode) -> dict[str, object]:
    """The exact fields shown to the model — and hashed for the cache key."""
    return {
        "error_code": episode.raw_error_code,
        "method": episode.method,
        "amount_paise": episode.amount,
        "attempt": episode.attempt,
    }


def cache_key(model: str, signals: dict[str, object]) -> str:
    blob = json.dumps({"v": SCHEMA_VERSION, "model": model, **signals}, sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()


def _user_prompt(signals: dict[str, object]) -> str:
    return (
        "A payment failed. Diagnose the cause and propose one intervention.\n"
        f"- provider error code: {signals['error_code']}\n"
        f"- method: {signals['method']}\n"
        f"- amount (paise): {signals['amount_paise']}\n"
        f"- attempt number: {signals['attempt']}\n"
        f"Allowed causes: {_CAUSES}\n"
        f"Allowed interventions: {_INTERVENTIONS}"
    )


# A call_fn maps the episode signals to the raw structured dict {cause, confidence,
# diagnosis_rationale, intervention, proposal_rationale}. The rest of LLMProposer
# (cache, validation, fallback, fault-logging) is backend-agnostic.

def _anthropic_call_fn(model: str):
    import anthropic
    client = anthropic.Anthropic()

    def call(signals: dict[str, object]) -> dict:
        resp = client.messages.create(
            model=model, max_tokens=1024, output_config={"effort": "low"},
            system=SYSTEM, tools=[TOOL],
            tool_choice={"type": "tool", "name": _TOOL_NAME},
            messages=[{"role": "user", "content": _user_prompt(signals)}],
        )
        for block in resp.content:
            if getattr(block, "type", None) == "tool_use" and block.name == _TOOL_NAME:
                return dict(block.input)
        raise _Fault("malformed", "no tool_use block in response")

    return call


def _groq_call_fn(model: str, api_key: str):
    """Groq via its OpenAI-compatible endpoint (stdlib only). temperature=0."""
    import urllib.error
    import urllib.request

    def call(signals: dict[str, object]) -> dict:
        body = json.dumps({
            "model": model, "temperature": 0,
            "messages": [{"role": "system", "content": SYSTEM},
                         {"role": "user", "content": _user_prompt(signals)}],
            "tools": [{"type": "function", "function": {
                "name": _TOOL_NAME, "description": TOOL["description"],
                "parameters": TOOL["input_schema"]}}],
            "tool_choice": {"type": "function", "function": {"name": _TOOL_NAME}},
        }).encode()
        req = urllib.request.Request(
            "https://api.groq.com/openai/v1/chat/completions", data=body,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json",
                     # Groq's edge blocks the default Python-urllib UA (Cloudflare 1010).
                     "User-Agent": "recovery-os/0.1"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                data = json.loads(r.read())
        except urllib.error.HTTPError as e:
            # surface Groq's JSON error body (e.g. decommissioned model) not "HTTP 400"
            raise _Fault("api_error", f"groq {e.code}: {e.read().decode()[:180]}")
        args = data["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"]
        return json.loads(args)

    return call


def _default_backend(model: str | None) -> tuple[object | None, str]:
    """Pick a backend from the environment. Groq first (free tier), then Anthropic,
    then None (offline: cache/fixtures only, no network). Returns (call_fn, model)."""
    override = os.getenv("RECOVERY_OS_LLM_MODEL")
    gk = os.getenv("GROQ_API_KEY")
    if gk:
        # Groq rotates its catalog; override with RECOVERY_OS_LLM_MODEL if this id
        # 404s (list via GET /openai/v1/models). gpt-oss-120b has strong tool use.
        m = model or override or "openai/gpt-oss-120b"
        return _groq_call_fn(m, gk), m
    if os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN"):
        m = model or override or "claude-opus-5"
        try:
            return _anthropic_call_fn(m), m
        except Exception:
            pass
    return None, model or override or "claude-opus-5"


class _Fault(Exception):
    def __init__(self, reason: str, excerpt: str = ""):
        self.reason = reason
        self.excerpt = excerpt[:200]


_UNSET = object()


class LLMProposer:
    def __init__(self, call_fn=_UNSET, model: str | None = None,
                 cache_dir: str | None = None, db_path: str | None = None):
        # call_fn=None explicitly forces offline (cache/fixtures only). Omitting it
        # resolves a backend (Groq or Anthropic) from the environment, else offline.
        if call_fn is _UNSET:
            self.call_fn, self.model = _default_backend(model)
        else:
            self.call_fn, self.model = call_fn, (model or "claude-opus-5")
        self.cache_dir = Path(
            cache_dir or os.getenv("RECOVERY_OS_LLM_CACHE_DIR") or "llm_cache")
        self.db_path = db_path
        self._memo: dict[str, tuple[Diagnosis, ProposedAction]] = {}

    # --- cache -------------------------------------------------------------
    def _cache_path(self, key: str) -> Path:
        return self.cache_dir / f"{key}.json"

    def _cache_get(self, key: str) -> dict | None:
        p = self._cache_path(key)
        if p.exists():
            return json.loads(p.read_text())
        return None

    def _cache_set(self, key: str, raw: dict) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._cache_path(key).write_text(json.dumps(raw, sort_keys=True, indent=2))

    def _validate(self, raw: dict, episode: Episode) -> tuple[Diagnosis, ProposedAction]:
        try:
            cause = FailureCause(raw["cause"])
            intervention = Intervention(raw["intervention"])
            confidence = float(raw["confidence"])
        except (KeyError, ValueError, TypeError) as e:
            raise _Fault("off_enum", f"{e}: {json.dumps(raw)[:150]}")
        if not 0.0 <= confidence <= 1.0:
            raise _Fault("malformed", f"confidence out of range: {confidence}")
        diagnosis = Diagnosis(
            episode_id=episode.episode_id, cause=cause, confidence=confidence,
            rationale=str(raw.get("diagnosis_rationale", ""))[:500],
        )
        action = ProposedAction(
            episode_id=episode.episode_id, intervention=intervention,
            rationale=str(raw.get("proposal_rationale", ""))[:500],
        )
        return diagnosis, action

    def _fault(self, episode: Episode, fault: _Fault) -> tuple[Diagnosis, ProposedAction]:
        ledger.append(
            episode.episode_id, LedgerStep.fault,
            DiagnosisFault(episode_id=episode.episode_id, reason=fault.reason,
                           raw_excerpt=fault.excerpt),
            db_path=self.db_path,
        )
        diagnosis = heuristic_diagnose(episode)
        return diagnosis, heuristic_propose(diagnosis, episode)

    def _plan(self, episode: Episode) -> tuple[Diagnosis, ProposedAction]:
        if episode.episode_id in self._memo:
            return self._memo[episode.episode_id]

        signals = _signals(episode)
        key = cache_key(self.model, signals)
        try:
            raw = self._cache_get(key)
            if raw is None:
                if self.call_fn is None:
                    raise _Fault("cache_miss", "no cached response and no backend")
                try:
                    raw = self.call_fn(signals)
                except _Fault:
                    raise
                except Exception as e:  # any network/SDK/parse error
                    raise _Fault("api_error", str(e))
                self._cache_set(key, raw)
            result = self._validate(raw, episode)
        except _Fault as fault:
            result = self._fault(episode, fault)

        self._memo[episode.episode_id] = result
        return result

    # --- seam functions ----------------------------------------------------
    def diagnose(self, episode: Episode) -> Diagnosis:
        return self._plan(episode)[0]

    def propose(self, diagnosis: Diagnosis, episode: Episode) -> ProposedAction:
        return self._plan(episode)[1]
