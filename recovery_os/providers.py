"""Payment provider adapter (invariant #2).

One Protocol, two swappable stubs with identical signatures. Core logic never
learns which is active — it calls `get_provider()`.

`execute` takes a SignedMandate, not a raw action: the only way to move money is
through a signed, policy-passed mandate (invariant #1). Bodies are stubs this
phase.
"""

from __future__ import annotations

import hashlib
import random
from typing import Protocol

from .config import get_settings
from .domain import (
    ERROR_CODES,
    Episode,
    ExecStatus,
    ExecutionResult,
    FailureCause,
    Intervention,
    SignedMandate,
    VerificationResult,
)
from .signing import verify


class PaymentProvider(Protocol):
    name: str

    def fetch_payment(self, payment_id: str) -> Episode: ...
    def execute(self, mandate: SignedMandate) -> ExecutionResult: ...
    def verify(self, episode_id: str) -> VerificationResult: ...


# The intervention that actually works for each cause. Match -> high recovery
# odds; mismatch -> low. This is the simulator's ground truth, not the agent's
# knowledge (the agent must diagnose + choose and can be wrong).
_BEST_FIX: dict[FailureCause, Intervention] = {
    FailureCause.issuer_downtime: Intervention.smart_retry,
    FailureCause.network_error: Intervention.smart_retry,
    FailureCause.insufficient_funds: Intervention.customer_nudge,
    FailureCause.expired_instrument: Intervention.method_switch,
    FailureCause.abandonment: Intervention.customer_nudge,
    FailureCause.mandate_failure: Intervention.mandate_reauth,
}
_MATCH_RATE = 0.75
_MISMATCH_RATE = 0.15


class _GatedProvider:
    """Shared base: refuses any mandate whose signature doesn't verify.

    A second, defence-in-depth check on top of the type-level gate — even a
    valid SignedMandate object is rejected here if it's been tampered with.
    """

    name = "base"

    def _guard(self, mandate: SignedMandate) -> None:
        if not verify(mandate):
            raise PermissionError("mandate signature invalid; refusing to execute")


class SimulatedProvider(_GatedProvider):
    """Seeded fake failures for reproducible runs (invariant #5).

    Deterministic in (seed, payment_id): the same call always yields the same
    episode and the same execute/verify outcome. Ground-truth cause and the
    recovery outcome are held in memory per episode, keyed off `fetch_payment`.
    """

    name = "simulated"

    def __init__(self) -> None:
        self._seed = get_settings().seed
        self._cause: dict[str, FailureCause] = {}
        self._recovered: dict[str, bool] = {}

    def _seeded(self, salt: str) -> random.Random:
        h = hashlib.sha256(f"{self._seed}:{salt}".encode()).digest()
        return random.Random(int.from_bytes(h[:8], "big"))

    def fetch_payment(self, payment_id: str) -> Episode:
        r = self._seeded(f"fetch:{payment_id}")
        cause = r.choice(list(FailureCause))
        episode_id = f"ep_{payment_id}"
        self._cause[episode_id] = cause
        return Episode(
            episode_id=episode_id,
            payment_id=payment_id,
            customer_id=f"cust_{r.randrange(1000, 9999)}",
            amount=r.randrange(5_000, 80_000, 100),  # paise; some exceed the ceiling
            method=r.choice(["card", "upi", "netbanking"]),
            raw_error_code=ERROR_CODES[cause],
            attempt=1,
        )

    def execute(self, mandate: SignedMandate) -> ExecutionResult:
        self._guard(mandate)
        eid = mandate.action.episode_id
        cause = self._cause[eid]  # KeyError if execute precedes fetch — intended
        iv = mandate.action.intervention

        if iv is Intervention.do_nothing:
            status, recovered, detail = ExecStatus.success, False, "no-op"
        elif iv is Intervention.human_escalation:
            status, recovered, detail = ExecStatus.pending, False, "handed to human queue"
        else:
            rate = _MATCH_RATE if _BEST_FIX[cause] is iv else _MISMATCH_RATE
            hit = self._seeded(f"exec:{eid}:{iv.value}").random() < rate
            status = ExecStatus.success if hit else ExecStatus.failed
            recovered = hit
            detail = f"{iv.value} vs {cause.value}: {'recovered' if hit else 'still failed'}"

        self._recovered[eid] = recovered
        return ExecutionResult(
            episode_id=eid, signature=mandate.signature, status=status,
            provider=self.name, detail=detail,
        )

    def verify(self, episode_id: str) -> VerificationResult:
        recovered = self._recovered.get(episode_id, False)
        return VerificationResult(
            episode_id=episode_id, recovered=recovered,
            detail="payment settled" if recovered else "still unpaid",
        )


class RazorpayTestProvider(_GatedProvider):
    """Real Razorpay test API. Stub this phase (no live calls)."""

    name = "razorpay_test"

    def fetch_payment(self, payment_id: str) -> Episode:
        raise NotImplementedError("RazorpayTestProvider.fetch_payment — phase 1")

    def execute(self, mandate: SignedMandate) -> ExecutionResult:
        self._guard(mandate)
        raise NotImplementedError("RazorpayTestProvider.execute — phase 1")

    def verify(self, episode_id: str) -> VerificationResult:
        raise NotImplementedError("RazorpayTestProvider.verify — phase 1")


def get_provider() -> PaymentProvider:
    """Return the configured provider. Core logic calls this, not the classes."""
    name = get_settings().provider
    providers: dict[str, PaymentProvider] = {
        "simulated": SimulatedProvider(),
        "razorpay_test": RazorpayTestProvider(),
    }
    if name not in providers:
        raise ValueError(f"unknown provider {name!r}; choose one of {list(providers)}")
    return providers[name]
