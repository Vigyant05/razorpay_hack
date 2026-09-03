"""Payment provider adapter (invariant #2).

One Protocol, two swappable stubs with identical signatures. Core logic never
learns which is active — it calls `get_provider()`.

`execute` takes a SignedMandate, not a raw action: the only way to move money is
through a signed, policy-passed mandate (invariant #1). Bodies are stubs this
phase.
"""

from __future__ import annotations

from typing import Protocol

from .config import get_settings
from .domain import Episode, ExecutionResult, SignedMandate, VerificationResult
from .signing import verify


class PaymentProvider(Protocol):
    name: str

    def fetch_payment(self, payment_id: str) -> Episode: ...
    def execute(self, mandate: SignedMandate) -> ExecutionResult: ...
    def verify(self, episode_id: str) -> VerificationResult: ...


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
    """Seeded fake failures for reproducible runs. Stub this phase."""

    name = "simulated"

    def fetch_payment(self, payment_id: str) -> Episode:
        raise NotImplementedError("SimulatedProvider.fetch_payment — phase 1")

    def execute(self, mandate: SignedMandate) -> ExecutionResult:
        self._guard(mandate)
        raise NotImplementedError("SimulatedProvider.execute — phase 1")

    def verify(self, episode_id: str) -> VerificationResult:
        raise NotImplementedError("SimulatedProvider.verify — phase 1")


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
