from recovery_os import ledger
from recovery_os.domain import Episode, LedgerStep, PolicyStatus
from recovery_os.orchestrator import run_episode
from recovery_os.policy import PolicyEngine
from recovery_os.providers import SimulatedProvider


def _run(payment_id, db, provider=None):
    return run_episode(payment_id, provider=provider or SimulatedProvider(),
                       engine=PolicyEngine(), db_path=db)


def test_run_is_reproducible(tmp_path):
    a = _run("pay_repro", str(tmp_path / "a.db"))
    b = _run("pay_repro", str(tmp_path / "b.db"))
    assert (a.cause, a.intervention, a.executed, a.recovered) == \
           (b.cause, b.intervention, b.executed, b.recovered)


def test_full_ledger_trail_on_execution(tmp_path):
    db = str(tmp_path / "t.db")
    # try payment_ids until we hit one the gate approves (so it runs to the end)
    for i in range(20):
        report = _run(f"pay_{i}", db)
        if report.policy_status is not PolicyStatus.blocked:
            steps = [e.step for e in ledger.read(report.episode_id, db_path=db)]
            assert steps == [
                LedgerStep.episode, LedgerStep.diagnosis, LedgerStep.proposal,
                LedgerStep.policy, LedgerStep.mandate, LedgerStep.execution,
                LedgerStep.verification, LedgerStep.attribution,
            ]
            # the mandate row carries a signature; nothing else does
            rows = ledger.read(report.episode_id, db_path=db)
            assert [r.signature is not None for r in rows].count(True) == 1
            return
    raise AssertionError("no approved episode in 20 tries — check the simulator")


def test_blocked_never_executes(tmp_path):
    """An over-ceiling money action is blocked and never reaches execute()."""
    db = str(tmp_path / "t.db")
    engine = PolicyEngine()
    prov = SimulatedProvider()
    for i in range(40):
        report = run_episode(f"pay_block_{i}", provider=prov, engine=engine, db_path=db)
        if report.policy_status is PolicyStatus.blocked:
            steps = [e.step for e in ledger.read(report.episode_id, db_path=db)]
            assert LedgerStep.execution not in steps
            assert LedgerStep.mandate not in steps
            assert report.executed is None and report.recovered is False
            return
    raise AssertionError("no blocked episode in 40 tries — check the ceiling")
