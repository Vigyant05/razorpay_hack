from recovery_os import ledger
from recovery_os.domain import Diagnosis, FailureCause, LedgerStep


def test_append_and_read(tmp_path):
    db = str(tmp_path / "t.db")
    diag = Diagnosis(
        episode_id="ep1", cause=FailureCause.issuer_downtime, confidence=0.9, rationale="bank down"
    )
    entry = ledger.append("ep1", LedgerStep.diagnosis, diag, db_path=db)
    assert entry.id is not None

    rows = ledger.read("ep1", db_path=db)
    assert len(rows) == 1
    assert rows[0].step == LedgerStep.diagnosis
    assert Diagnosis.model_validate_json(rows[0].payload).cause == FailureCause.issuer_downtime


def test_read_isolated_by_episode(tmp_path):
    db = str(tmp_path / "t.db")
    d = Diagnosis(episode_id="ep2", cause=FailureCause.network_error, confidence=0.5, rationale="x")
    ledger.append("ep2", LedgerStep.diagnosis, d, db_path=db)
    assert ledger.read("nope", db_path=db) == []
