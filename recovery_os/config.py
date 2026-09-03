"""Env-based settings and the single seeded RNG.

Invariant #5 (reproducibility): all randomness comes from `rng()`, seeded from
settings. Same seed -> identical results. Do not call `random` directly elsewhere.
"""

from __future__ import annotations

import random
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="RECOVERY_OS_", env_file=".env")

    seed: int = 42
    db_path: str = "recovery_os.db"
    key_path: str = "ed25519_key.pem"
    max_auto_amount: int = 50_000  # minor units (paise); ceiling for auto-approval
    max_attempts: int = 3
    provider: str = "simulated"  # "simulated" | "razorpay_test"


@lru_cache
def get_settings() -> Settings:
    return Settings()


def rng() -> random.Random:
    """The one seeded RNG. Fresh instance per call, seeded identically -> reproducible."""
    return random.Random(get_settings().seed)
