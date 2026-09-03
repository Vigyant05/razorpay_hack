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


def _default_client():
    # Offline-safe: no key -> no client -> straight to cache/fallback, no network.
    if not (os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN")):
        return None
    try:
        import anthropic
        return anthropic.Anthropic()
    except Exception:
        return None


class _Fault(Exception):
    def __init__(self, reason: str, excerpt: str = ""):
        self.reason = reason
        self.excerpt = excerpt[:200]


_UNSET = object()


class LLMProposer:
    def __init__(self, client=_UNSET, model: str | None = None,
                 cache_dir: str | None = None, db_path: str | None = None):
        # client=None explicitly forces "no client" (offline); omitting it resolves
        # a default client only when a key is present.
        self.client = _default_client() if client is _UNSET else client
        self.model = model or "claude-opus-5"
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

    # --- the one call ------------------------------------------------------
    def _call_api(self, signals: dict[str, object]) -> dict:
        resp = self.client.messages.create(
            model=self.model,
            max_tokens=1024,
            output_config={"effort": "low"},
            system=SYSTEM,
            tools=[TOOL],
            tool_choice={"type": "tool", "name": _TOOL_NAME},
            messages=[{"role": "user", "content": _user_prompt(signals)}],
        )
        for block in resp.content:
            if getattr(block, "type", None) == "tool_use" and block.name == _TOOL_NAME:
                return dict(block.input)
        raise _Fault("malformed", "no tool_use block in response")

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
                if self.client is None:
                    raise _Fault("cache_miss", "no cached response and no API client")
                try:
                    raw = self._call_api(signals)
                except _Fault:
                    raise
                except Exception as e:  # any SDK/network error
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
