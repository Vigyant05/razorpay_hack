import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from recovery_os.domain import Episode, Intervention, PolicyStatus, ProposedAction
from recovery_os.policy import PolicyEngine
from recovery_os.signing import PolicyViolation, issue_mandate


def _episode(amount: int, attempt: int = 1) -> Episode:
    return Episode(
        episode_id="ep1", payment_id="pay_1", customer_id="cust_1",
        amount=amount, method="card", attempt=attempt,
    )


def _retry() -> ProposedAction:
    return ProposedAction(episode_id="ep1", intervention=Intervention.smart_retry, rationale="r")


def test_over_ceiling_is_blocked():
    engine = PolicyEngine()  # default ceiling 72_000
    decision = engine.decide(_retry(), _episode(amount=80_000))
    assert decision.status is PolicyStatus.blocked
    assert decision.rule_fired == "amount_ceiling"


def test_within_ceiling_is_approved():
    engine = PolicyEngine()
    decision = engine.decide(_retry(), _episode(amount=10_000))
    assert decision.status is PolicyStatus.approved


def test_max_attempts_blocks():
    engine = PolicyEngine()  # default max_attempts 3
    decision = engine.decide(_retry(), _episode(amount=100, attempt=3))
    assert decision.status is PolicyStatus.blocked
    assert decision.rule_fired == "max_attempts"


def test_gate_cannot_be_bypassed():
    """The blocked decision cannot be turned into a mandate -> execute() is unreachable."""
    engine = PolicyEngine()
    decision = engine.decide(_retry(), _episode(amount=80_000))
    with pytest.raises(PolicyViolation):
        issue_mandate(decision, key=Ed25519PrivateKey.generate())
