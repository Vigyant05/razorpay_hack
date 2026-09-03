from recovery_os.batch import POLICIES, run_batch
from recovery_os.domain import (
    ExecStatus,
    FailureCause,
    Intervention,
    PolicyStatus,
    RunReport,
)
from recovery_os.scorecard import build


def _report(eid, recovered, intervention=Intervention.smart_retry,
            executed=ExecStatus.success, wasted=0, amount=10_000):
    return RunReport(
        episode_id=eid, cause=FailureCause.issuer_downtime, intervention=intervention,
        amount=amount, policy_status=PolicyStatus.approved, rule_fired=None,
        executed=executed, recovered=recovered, wasted_actions=wasted,
    )


def test_scorecard_reproducible(tmp_path):
    a = run_batch(60, seed=42, policy="agent", db_path=str(tmp_path / "a.db"))
    b = run_batch(60, seed=42, policy="agent", db_path=str(tmp_path / "b.db"))
    assert a.model_dump_json() == b.model_dump_json()


def test_control_rates_are_policy_independent(tmp_path):
    """Control episodes get do_nothing regardless of treatment policy -> same baseline."""
    agent = run_batch(80, seed=7, policy="agent", db_path=str(tmp_path / "a.db"))
    imm = run_batch(80, seed=7, policy="immediate", db_path=str(tmp_path / "i.db"))
    assert agent.n_control == imm.n_control
    a_ctrl = {c.cause: (c.n_control, c.control_recovery_rate) for c in agent.per_cause}
    i_ctrl = {c.cause: (c.n_control, c.control_recovery_rate) for c in imm.per_cause}
    assert a_ctrl == i_ctrl


def test_never_policy_costs_no_effort(tmp_path):
    card = run_batch(80, seed=7, policy="never", db_path=str(tmp_path / "n.db"))
    assert card.false_effort_actions == 0
    assert card.false_effort_amount_paise == 0


def test_each_baseline_runs(tmp_path):
    for p in POLICIES:
        card = run_batch(30, seed=1, policy=p, db_path=str(tmp_path / f"{p}.db"))
        assert card.policy == p and card.n_episodes == 30


def test_incremental_math_handchecked():
    # 6 issuer_downtime episodes, ₹100 each; 2 control, 4 treatment.
    control = [_report("c0", True, Intervention.do_nothing),
               _report("c1", False, Intervention.do_nothing)]
    treatment = [_report("t0", True), _report("t1", True), _report("t2", True),
                 _report("t3", False, executed=ExecStatus.failed, wasted=1)]
    reports = control + treatment
    control_ids = {"c0", "c1"}

    s = build("agent", seed=42, control_frac=1 / 3, reports=reports, control_ids=control_ids)

    cause = s.per_cause[0]
    assert cause.control_recovery_rate == 0.5
    assert cause.treatment_recovery_rate == 0.75
    assert cause.incremental_lift == 0.25
    assert cause.incremental_amount_paise == 10_000  # 30000 - 0.5*40000
    assert s.incremental_recovery_rate == 0.25
    assert s.incremental_amount_paise == 10_000
    assert abs(s.raw_recovery_rate - 4 / 6) < 1e-9  # 1 control + 3 treatment recovered
    assert s.false_effort_actions == 1
    assert s.false_effort_amount_paise == 10_000
    assert s.exceptions == []
