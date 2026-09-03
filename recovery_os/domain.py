"""Typed domain vocabulary and models. Pure pydantic, no I/O.

Money is always an int in minor units (paise). Never a float.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field


def _now() -> datetime:
    return datetime.now(timezone.utc)


# --- enums -------------------------------------------------------------------

class FailureCause(str, Enum):
    issuer_downtime = "issuer_downtime"
    insufficient_funds = "insufficient_funds"
    expired_instrument = "expired_instrument"
    network_error = "network_error"
    abandonment = "abandonment"
    mandate_failure = "mandate_failure"


class Intervention(str, Enum):
    smart_retry = "smart_retry"
    method_switch = "method_switch"
    mandate_reauth = "mandate_reauth"
    customer_nudge = "customer_nudge"
    human_escalation = "human_escalation"
    do_nothing = "do_nothing"


class PolicyStatus(str, Enum):
    approved = "approved"
    modified = "modified"
    blocked = "blocked"


class ExecStatus(str, Enum):
    success = "success"
    failed = "failed"
    pending = "pending"


class LedgerStep(str, Enum):
    episode = "episode"
    diagnosis = "diagnosis"
    proposal = "proposal"
    policy = "policy"
    mandate = "mandate"
    execution = "execution"
    verification = "verification"
    attribution = "attribution"


# --- models ------------------------------------------------------------------

class Episode(BaseModel):
    """A failed payment we may try to recover."""
    episode_id: str
    payment_id: str
    customer_id: str
    amount: int  # minor units (paise)
    currency: str = "INR"
    method: str  # card | upi | netbanking | ...
    raw_error_code: str | None = None
    attempt: int = 1
    created_at: datetime = Field(default_factory=_now)


class Diagnosis(BaseModel):
    episode_id: str
    cause: FailureCause
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str
    created_at: datetime = Field(default_factory=_now)


class ProposedAction(BaseModel):
    episode_id: str
    intervention: Intervention
    params: dict[str, str] = Field(default_factory=dict)
    rationale: str
    created_at: datetime = Field(default_factory=_now)


class PolicyDecision(BaseModel):
    status: PolicyStatus
    rule_fired: str | None
    reason: str
    original: ProposedAction
    modified: ProposedAction | None = None

    @property
    def effective_action(self) -> ProposedAction:
        """The action that would actually run: the modification if any, else the original."""
        return self.modified or self.original


class SignedMandate(BaseModel):
    """Proof that an action passed the policy gate and was signed.

    Only `signing.issue_mandate` constructs these, and only for non-blocked
    decisions. `PaymentProvider.execute` requires one -> the gate cannot be
    bypassed by construction (invariant #1).
    """
    action: ProposedAction
    decision: PolicyDecision
    payload_sha256: str
    signature: str  # hex
    public_key: str  # hex
    signed_at: datetime = Field(default_factory=_now)


class ExecutionResult(BaseModel):
    episode_id: str
    signature: str  # links back to the mandate that authorized this
    status: ExecStatus
    provider: str
    detail: str = ""
    executed_at: datetime = Field(default_factory=_now)


class VerificationResult(BaseModel):
    episode_id: str
    recovered: bool
    detail: str = ""
    verified_at: datetime = Field(default_factory=_now)


class Attribution(BaseModel):
    """Honest incremental-recovery accounting (fleshed out in a later phase)."""
    episode_id: str
    intervention: Intervention
    recovered: bool
    counterfactual: str  # what we assume would have happened with no action
    incremental: bool  # recovery attributable to the action vs. would-recover-anyway
    note: str = ""
