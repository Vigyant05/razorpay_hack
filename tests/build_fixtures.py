"""Build the committed LLM record-replay fixtures — offline, no API key.

A cached response is just structured data; we author answers for a few fixed
episodes and write them under the real cache key computed by the production
code. Run `python tests/build_fixtures.py` to (re)generate the committed cache.
"""

from __future__ import annotations

import json
from pathlib import Path

from recovery_os.domain import ERROR_CODES, Episode, FailureCause
from recovery_os.llm import _signals, cache_key

MODEL = "claude-opus-5"  # must match LLMProposer's default
FIXTURE_DIR = Path(__file__).parent / "fixtures" / "llm_cache"


def _ep(episode_id: str, cause: FailureCause, amount: int, attempt: int = 1) -> Episode:
    return Episode(
        episode_id=episode_id, payment_id=episode_id.removeprefix("ep_"),
        customer_id="cust_x", amount=amount, method="card",
        raw_error_code=ERROR_CODES[cause], attempt=attempt,
    )


# name -> (episode, authored tool response)
FIXTURES: dict[str, tuple[Episode, dict]] = {
    "good": (
        _ep("ep_fix_good", FailureCause.issuer_downtime, 10_000),
        {"cause": "issuer_downtime", "confidence": 0.9,
         "diagnosis_rationale": "GATEWAY_ERROR indicates the issuing bank was down",
         "intervention": "smart_retry",
         "proposal_rationale": "transient; retry once the bank recovers"},
    ),
    "blocked": (  # money-moving action on an over-ceiling amount -> gate blocks
        _ep("ep_fix_blocked", FailureCause.issuer_downtime, 90_000),
        {"cause": "issuer_downtime", "confidence": 0.8,
         "diagnosis_rationale": "same signal, larger amount",
         "intervention": "smart_retry",
         "proposal_rationale": "retry"},
    ),
    "offenum": (  # invalid cause -> must fall back to heuristic (insufficient_funds)
        _ep("ep_fix_offenum", FailureCause.insufficient_funds, 12_000),
        {"cause": "banana", "confidence": 0.5,
         "diagnosis_rationale": "nonsense",
         "intervention": "smart_retry", "proposal_rationale": "nonsense"},
    ),
}


def build(cache_dir: Path | str) -> None:
    d = Path(cache_dir)
    d.mkdir(parents=True, exist_ok=True)
    for episode, answer in FIXTURES.values():
        key = cache_key(MODEL, _signals(episode))
        (d / f"{key}.json").write_text(json.dumps(answer, sort_keys=True, indent=2))


if __name__ == "__main__":
    build(FIXTURE_DIR)
    print(f"wrote {len(FIXTURES)} fixtures to {FIXTURE_DIR}")
