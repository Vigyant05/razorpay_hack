"""Env-based settings and the single seeded RNG.

Invariant #5 (reproducibility): all randomness comes from `rng()`, seeded from
settings. Same seed -> identical results. Do not call `random` directly elsewhere.
"""

from __future__ import annotations

import random
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

from .domain import FailureCause

# --- modeling assumptions (NOT measured facts) -------------------------------
# Per-cause probability that a failed payment recovers on its own with NO
# intervention. Drives the control/holdout baseline in the scorecard.
# Grounding: soft-decline self-recovery runs ~53% (Recurly), top-quartile card
# recovery 60-70%. These are deliberately conservative, tunable priors — edit
# them here to re-tune the whole batch. Same seed still -> identical results.
SELF_RECOVERY: dict[FailureCause, float] = {
    FailureCause.insufficient_funds: 0.45,  # customers often retry after funding
    FailureCause.expired_instrument: 0.10,  # needs a card update; rarely self-fixes
    FailureCause.issuer_downtime: 0.55,     # transient; high natural recovery
    FailureCause.network_error: 0.50,       # transient
    FailureCause.abandonment: 0.20,
    FailureCause.mandate_failure: 0.15,
}
# Retry attempts a fixed-schedule dunning policy fires per episode.
FIXED_SCHEDULE_ATTEMPTS = 3

# Simulated failed-payment amount range (paise), uniform. Tunable here. With the
# 72_000 auto-approval ceiling below, ~10% of the range sits above it, so the
# gate blocks a realistic minority rather than a third of the batch.
AMOUNT_MIN_PAISE = 5_000
AMOUNT_MAX_PAISE = 80_000

ASSUMPTIONS_NOTE = (
    "Self-recovery rates are modeling assumptions, not measured facts "
    "(conservative priors from published dunning benchmarks: Recurly ~53% "
    "soft-decline self-recovery, top-quartile card recovery 60-70%). Tunable "
    "in config.SELF_RECOVERY."
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="RECOVERY_OS_", env_file=".env")

    seed: int = 42
    db_path: str = "recovery_os.db"
    key_path: str = "ed25519_key.pem"
    max_auto_amount: int = 72_000  # paise; auto-approval ceiling (~10% of the amount range blocks)
    max_attempts: int = 3
    provider: str = "simulated"  # "simulated" | "razorpay_test"


@lru_cache
def get_settings() -> Settings:
    return Settings()


def rng() -> random.Random:
    """The one seeded RNG. Fresh instance per call, seeded identically -> reproducible."""
    return random.Random(get_settings().seed)
