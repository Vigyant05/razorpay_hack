"""Live integration test — REAL Razorpay test-mode API calls.

Skipped entirely unless RAZORPAY_KEY_ID / RAZORPAY_KEY_SECRET are present, so the
offline suite stays green with no keys and no network. This is the acceptance
test for the provider swap: one episode all the way through the unchanged
gate/sign/execute path, against live Razorpay infra.
"""

import os

import pytest

from recovery_os import ledger
from recovery_os.config import load_env_file
from recovery_os.domain import FailureCause, LedgerStep, PolicyStatus
from recovery_os.orchestrator import run_episode
from recovery_os.policy import PolicyEngine

load_env_file()  # .env is gitignored; keys live there

pytestmark = pytest.mark.skipif(
    not (os.getenv("RAZORPAY_KEY_ID") and os.getenv("RAZORPAY_KEY_SECRET")),
    reason="live Razorpay test keys not set (RAZORPAY_KEY_ID / RAZORPAY_KEY_SECRET)",
)


def test_refuses_a_live_key(monkeypatch):
    from recovery_os.providers import RazorpayTestProvider

    monkeypatch.setenv("RAZORPAY_KEY_ID", "rzp_live_XXXXXXXX")
    with pytest.raises(RuntimeError, match="not a test key"):
        RazorpayTestProvider()


def test_one_real_episode_end_to_end(tmp_path):
    """Origination -> gate -> signed mandate -> real nudge -> real verification."""
    from recovery_os.providers import RazorpayTestProvider

    db = str(tmp_path / "live.db")
    payment_id = "livedemo"
    provider = RazorpayTestProvider(amount=5_000, db_path=db)  # Rs 50, under the ceiling

    report = run_episode(payment_id, provider=provider, engine=PolicyEngine(), db_path=db)

    # an unpaid real payment link is genuine abandonment; the gate approved it
    assert report.cause is FailureCause.abandonment
    assert report.policy_status is not PolicyStatus.blocked

    rows = ledger.read(report.episode_id, db_path=db)
    steps = [r.step for r in rows]
    # the eight canonical steps, in order; a fault row may be interleaved (see below)
    assert [s for s in steps if s is not LedgerStep.fault] == [
        LedgerStep.episode, LedgerStep.diagnosis, LedgerStep.proposal,
        LedgerStep.policy, LedgerStep.mandate, LedgerStep.execution,
        LedgerStep.verification, LedgerStep.attribution,
    ]
    assert [r.signature is not None for r in rows].count(True) == 1  # only the mandate

    # the ids in the trail are real Razorpay test-mode ids, not simulated ones
    assert '"payment_id":"order_' in rows[0].payload.replace(" ", "")
    execution = rows[steps.index(LedgerStep.execution)].payload
    assert '"provider":"razorpay_test"' in execution.replace(" ", "")
    # Either a real payment link was minted, or the account's lifetime test-mode cap
    # (30 links per business) was hit — in which case the point is that it degraded
    # into a logged fault instead of crashing. Both outcomes prove the real round trip.
    # Match on the HTTP status, not Razorpay's prose: it returns both
    # "RATE_LIMIT_EXCEEDED" and "BAD_REQUEST_ERROR: Too many requests" for a 429.
    assert "plink_" in execution or "-> 429" in execution

    # nobody has paid, so real verification must say unrecovered
    assert report.recovered is False


def test_refund_hits_the_real_endpoint():
    """create_refund is wired to the real API — either it refunds a captured test
    payment, or Razorpay tells us why not. Both are real round-trips."""
    from recovery_os.providers import RazorpayError, RazorpayTestProvider

    provider = RazorpayTestProvider()
    payment_id = os.getenv("RAZORPAY_CAPTURED_PAYMENT_ID")

    if payment_id:
        refund = provider.refund(payment_id, amount=100)  # Rs 1 partial
        assert refund["entity"] == "refund"
        assert refund["status"] in ("pending", "processed")
        assert refund["payment_id"] == payment_id
        return

    # no captured payment on hand: prove we reach Razorpay and parse its real error
    with pytest.raises(RazorpayError) as e:
        provider.refund("pay_nonexistent00000")
    assert "/payments/pay_nonexistent00000/refund" in str(e.value)


@pytest.mark.skipif(not os.getenv("GROQ_API_KEY"), reason="GROQ_API_KEY not set")
def test_llm_proposal_flows_through_the_gate_into_the_real_provider(tmp_path):
    """The one seam nothing else covers: an LLM proposal reaching a REAL provider.

    Untrusted LLM output still goes through the same PolicyDecision -> issue_mandate
    -> execute path — it just happens to move a real Razorpay payment link at the end.
    Costs one Groq call ever: the cache is keyed on the episode's signals, not its id,
    and every originated live episode has identical signals.
    """
    from recovery_os.llm import LLMProposer
    from recovery_os.providers import RazorpayTestProvider

    db = str(tmp_path / "llm_live.db")
    llm = LLMProposer(db_path=db)
    provider = RazorpayTestProvider(amount=5_000, db_path=db)

    report = run_episode("livellm", provider=provider, engine=PolicyEngine(),
                         diagnoser=llm.diagnose, proposer=llm.propose, db_path=db)

    assert report.policy_status is not PolicyStatus.blocked
    rows = ledger.read(report.episode_id, db_path=db)
    steps = [r.step for r in rows]
    assert LedgerStep.mandate in steps and LedgerStep.execution in steps
    assert [r.signature is not None for r in rows].count(True) == 1

    # the LLM diagnosed for real, and its proposal reached the real provider
    diagnosis = rows[steps.index(LedgerStep.diagnosis)].payload
    assert '"rationale":""' not in diagnosis.replace(" ", "")  # a real rationale, not empty
    execution = rows[steps.index(LedgerStep.execution)].payload
    assert '"provider":"razorpay_test"' in execution.replace(" ", "")
    assert "plink_" in execution or "-> 429" in execution
