"""LLM backend + phase-3 proposer. Claude/Groq propose; the engine disposes.

The backend is a generic one-forced-tool call `call_fn(system, user, tool) -> dict`
(Groq or Anthropic), wrapped by a disk record-replay cache so runs are
deterministic and free after the first pass. Phase 3 (LLMProposer) and phase 4
(rag) both reuse this. Any fault (API error, cache miss with no backend, off-enum,
malformed) is caught by the caller, which falls back to a deterministic path.
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


class _Fault(Exception):
    def __init__(self, reason: str, excerpt: str = ""):
        self.reason = reason
        self.excerpt = excerpt[:200]


# --- generic backends: call_fn(system, user, tool) -> tool-input dict ---------

def _anthropic_call_fn(model: str):
    import anthropic
    client = anthropic.Anthropic()

    def call(system: str, user: str, tool: dict) -> dict:
        resp = client.messages.create(
            model=model, max_tokens=1024, output_config={"effort": "low"},
            system=system, tools=[tool],
            tool_choice={"type": "tool", "name": tool["name"]},
            messages=[{"role": "user", "content": user}],
        )
        for block in resp.content:
            if getattr(block, "type", None) == "tool_use" and block.name == tool["name"]:
                return dict(block.input)
        raise _Fault("malformed", "no tool_use block in response")

    return call


def _groq_call_fn(model: str, api_key: str):
    """Groq via its OpenAI-compatible endpoint (stdlib only). temperature=0."""
    import time
    import urllib.error
    import urllib.request

    def call(system: str, user: str, tool: dict) -> dict:
        body = json.dumps({
            "model": model, "temperature": 0,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
            "tools": [{"type": "function", "function": {
                "name": tool["name"], "description": tool["description"],
                "parameters": tool["input_schema"]}}],
            "tool_choice": {"type": "function", "function": {"name": tool["name"]}},
        }).encode()
        req = urllib.request.Request(
            "https://api.groq.com/openai/v1/chat/completions", data=body,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json",
                     # Groq's edge blocks the default Python-urllib UA (Cloudflare 1010).
                     "User-Agent": "recovery-os/0.1"})
        for attempt in range(4):  # retry the free-tier rate limit before giving up
            try:
                with urllib.request.urlopen(req, timeout=30) as r:
                    data = json.loads(r.read())
                break
            except urllib.error.HTTPError as e:
                if e.code == 429 and attempt < 3:
                    wait = float(e.headers.get("retry-after") or 2)
                    time.sleep(min(wait, 10))
                    continue
                raise _Fault("api_error", f"groq {e.code}: {e.read().decode()[:180]}")
        args = data["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"]
        return json.loads(args)

    return call


def resolve_backend(model: str | None = None) -> tuple[object | None, str]:
    """Pick a backend from the environment. Groq first (free tier), then Anthropic,
    then None (offline: cache/fixtures only). Returns (call_fn, model)."""
    override = os.getenv("RECOVERY_OS_LLM_MODEL")
    if os.getenv("GROQ_API_KEY"):
        # Groq rotates its catalog; override with RECOVERY_OS_LLM_MODEL if this id
        # 404s (list via GET /openai/v1/models). gpt-oss-120b has strong tool use.
        m = model or override or "openai/gpt-oss-120b"
        return _groq_call_fn(m, os.environ["GROQ_API_KEY"]), m
    if os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN"):
        m = model or override or "claude-opus-5"
        try:
            return _anthropic_call_fn(m), m
        except Exception:
            pass
    return None, model or override or "claude-opus-5"


# --- record-replay cache -----------------------------------------------------

def default_cache_dir() -> Path:
    return Path(os.getenv("RECOVERY_OS_LLM_CACHE_DIR") or "llm_cache")


def cache_key(model: str, payload: dict) -> str:
    blob = json.dumps({"v": SCHEMA_VERSION, "model": model, **payload}, sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()


def cached_tool_call(call_fn, model: str, cache_dir: Path, key_payload: dict,
                     system: str, user: str, tool: dict) -> dict:
    """Cache-first one-tool call. Raises _Fault on miss-without-backend or API error."""
    p = Path(cache_dir) / f"{cache_key(model, key_payload)}.json"
    if p.exists():
        return json.loads(p.read_text())
    if call_fn is None:
        raise _Fault("cache_miss", "no cached response and no backend")
    try:
        raw = call_fn(system, user, tool)
    except _Fault:
        raise
    except Exception as e:  # any network/SDK/parse error
        raise _Fault("api_error", str(e))
    Path(cache_dir).mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(raw, sort_keys=True, indent=2))
    return raw


# --- phase-3 proposer (unchanged behavior + cache keys) ----------------------

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


_UNSET = object()


class LLMProposer:
    def __init__(self, call_fn=_UNSET, model: str | None = None,
                 cache_dir: str | None = None, db_path: str | None = None):
        # call_fn=None explicitly forces offline (cache/fixtures only). Omitting it
        # resolves a backend (Groq or Anthropic) from the environment, else offline.
        if call_fn is _UNSET:
            self.call_fn, self.model = resolve_backend(model)
        else:
            self.call_fn, self.model = call_fn, (model or "claude-opus-5")
        self.cache_dir = Path(cache_dir) if cache_dir else default_cache_dir()
        self.db_path = db_path
        self._memo: dict[str, tuple[Diagnosis, ProposedAction]] = {}

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
        try:
            raw = cached_tool_call(self.call_fn, self.model, self.cache_dir, signals,
                                   SYSTEM, _user_prompt(signals), TOOL)
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
