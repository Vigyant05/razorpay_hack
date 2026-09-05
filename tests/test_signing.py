import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from recovery_os.domain import (
    Intervention,
    PolicyDecision,
    PolicyStatus,
    ProposedAction,
)
from recovery_os.signing import PolicyViolation, issue_mandate, verify


def _action() -> ProposedAction:
    return ProposedAction(
        episode_id="ep1",
        intervention=Intervention.smart_retry,
        rationale="issuer back up",
    )


def _approved() -> PolicyDecision:
    a = _action()
    return PolicyDecision(status=PolicyStatus.approved, rule_fired=None, reason="ok", original=a)


def test_sign_verify_roundtrip():
    key = Ed25519PrivateKey.generate()
    mandate = issue_mandate(_approved(), key=key)
    assert verify(mandate) is True


def test_tamper_fails():
    key = Ed25519PrivateKey.generate()
    mandate = issue_mandate(_approved(), key=key)
    tampered = mandate.model_copy(deep=True)
    tampered.action.intervention = Intervention.method_switch  # change the signed content
    assert verify(tampered) is False


def test_blocked_cannot_be_signed():
    blocked = PolicyDecision(
        status=PolicyStatus.blocked,
        rule_fired="amount_ceiling",
        reason="too big",
        original=_action(),
    )
    with pytest.raises(PolicyViolation):
        issue_mandate(blocked, key=Ed25519PrivateKey.generate())
