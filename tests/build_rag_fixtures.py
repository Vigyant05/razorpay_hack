"""Build phase-4 fixtures — offline, no API key.

- build_ledger(db): a deterministic ledger from a seeded offline `--proposer llm`
  batch (cache-miss faults -> heuristic fallback give fault rows; control gives
  do_nothing; over-ceiling gives gate_blocked). Not committed (binary) — rebuilt
  at test/demo time.
- committed LLM cache fixtures for the translate/narrate parsing tests.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from recovery_os.llm import cache_key

MODEL = "claude-opus-5"
FIXTURE_DIR = Path(__file__).parent / "fixtures" / "llm_cache"

# questions/views the parsing tests pin to
Q_PARSE = "what did the agent do for insufficient_funds cases?"
Q_MALFORMED = "show me the odd episodes"
Q_NARRATE = "why did these episodes fail?"
NARRATE_IDS = ["ep_pay_1", "ep_pay_2"]

# name -> (cache key_payload, authored raw response)
LLM_FIXTURES = {
    "translate_ok": ({"task": "translate", "question": Q_PARSE},
                     {"cause": "insufficient_funds"}),
    "translate_bad": ({"task": "translate", "question": Q_MALFORMED},
                      {"cause": "banana"}),  # off-enum -> must fall back to keywords
    "narrate": ({"task": "narrate", "question": Q_NARRATE, "episodes": NARRATE_IDS},
                {"answer": "Both episodes failed on insufficient funds.",
                 "cited_episode_ids": ["ep_pay_1", "ep_pay_2", "ep_bogus_999"]}),  # bogus dropped
}


def build_llm_fixtures(cache_dir: Path | str = FIXTURE_DIR) -> None:
    d = Path(cache_dir)
    d.mkdir(parents=True, exist_ok=True)
    for payload, answer in LLM_FIXTURES.values():
        (d / f"{cache_key(MODEL, payload)}.json").write_text(json.dumps(answer, sort_keys=True))


def build_ledger(db_path: str | Path, n: int = 25, seed: int = 42) -> None:
    """Deterministic offline ledger. Forces offline so the fixture never depends
    on a live key (treatment episodes cache-miss -> fault -> heuristic fallback)."""
    db_path = str(db_path)
    saved = {k: os.environ.pop(k, None)
             for k in ("GROQ_API_KEY", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")}
    os.environ["RECOVERY_OS_LLM_CACHE_DIR"] = db_path + ".emptycache"
    try:
        from recovery_os.batch import run_batch
        run_batch(n, seed=seed, policy="agent", proposer_kind="llm", db_path=db_path)
    finally:
        for k, v in saved.items():
            if v is not None:
                os.environ[k] = v


if __name__ == "__main__":
    build_llm_fixtures()
    print(f"wrote {len(LLM_FIXTURES)} llm fixtures to {FIXTURE_DIR}")
