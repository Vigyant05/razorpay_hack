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
    # extra="ignore": the .env also holds non-RECOVERY_OS keys (GROQ_API_KEY,
    # ANTHROPIC_API_KEY); Settings must not choke on them.
    model_config = SettingsConfigDict(
        env_prefix="RECOVERY_OS_", env_file=".env", extra="ignore")

    seed: int = 42
    db_path: str = "recovery_os.db"
    key_path: str = "ed25519_key.pem"
    max_auto_amount: int = 72_000  # paise; auto-approval ceiling (~10% of the amount range blocks)
    max_attempts: int = 3
    provider: str = "simulated"  # "simulated" | "razorpay_test"


def load_env_file(path: str = ".env") -> None:
    """Best-effort load of KEY=VALUE lines from a .env into os.environ (no override).

    pydantic-settings already reads .env for RECOVERY_OS_* fields; this also exposes
    plain keys like GROQ_API_KEY / ANTHROPIC_API_KEY to os.getenv. Called from the
    CLI entrypoint only — library code stays env-pure so tests are hermetic.
    """
    import os
    from pathlib import Path

    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        val = val.strip()
        if val[:1] in ("'", '"'):  # quoted value: keep everything inside the quotes
            end = val.find(val[0], 1)
            val = val[1:end] if end != -1 else val[1:]
        else:  # unquoted: drop an inline comment ( space + # ), then trim
            val = val.split(" #", 1)[0].split("\t#", 1)[0].strip()
        os.environ.setdefault(key.strip(), val)


@lru_cache
def get_settings() -> Settings:
    return Settings()


def rng() -> random.Random:
    """The one seeded RNG. Fresh instance per call, seeded identically -> reproducible."""
    return random.Random(get_settings().seed)
