import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import build_fixtures  # noqa: E402

from recovery_os import ledger  # noqa: E402
from recovery_os.domain import (  # noqa: E402
    ERROR_CODES,
    DiagnosisFault,
    Episode,
    FailureCause,
    Intervention,
    LedgerStep,
    PolicyStatus,
)
from recovery_os.llm import LLMProposer, _signals, cache_key  # noqa: E402
from recovery_os.policy import PolicyEngine  # noqa: E402

FIX = build_fixtures.FIXTURE_DIR


def _proposer(cache_dir, db):
    return LLMProposer(client=None, cache_dir=str(cache_dir), db_path=db)


def test_parses_cached_response(tmp_path):
    ep, _ = build_fixtures.FIXTURES["good"]
    llm = _proposer(FIX, str(tmp_path / "t.db"))
    diag = llm.diagnose(ep)
    action = llm.propose(diag, ep)
    assert diag.cause is FailureCause.issuer_downtime
    assert diag.confidence == 0.9
    assert action.intervention is Intervention.smart_retry


def test_off_enum_falls_back_and_logs_fault(tmp_path):
    ep, _ = build_fixtures.FIXTURES["offenum"]  # authored cause="banana"
    db = str(tmp_path / "t.db")
    llm = _proposer(FIX, db)
    diag = llm.diagnose(ep)
    action = llm.propose(diag, ep)
    # fell back to the heuristic, which reads the (valid) error code
    assert diag.cause is FailureCause.insufficient_funds
    assert action.intervention is Intervention.customer_nudge
    faults = [r for r in ledger.read(ep.episode_id, db_path=db) if r.step is LedgerStep.fault]
    assert len(faults) == 1
    assert DiagnosisFault.model_validate_json(faults[0].payload).reason == "off_enum"


def test_cache_miss_without_client_falls_back(tmp_path):
    ep = Episode(episode_id="ep_miss", payment_id="miss", customer_id="c",
                 amount=33_300, method="upi", raw_error_code=ERROR_CODES[FailureCause.network_error])
    db = str(tmp_path / "t.db")
    llm = _proposer(tmp_path / "empty_cache", db)  # empty dir, no client
    diag = llm.diagnose(ep)
    assert diag.cause is FailureCause.network_error  # heuristic fallback
    faults = [r for r in ledger.read(ep.episode_id, db_path=db) if r.step is LedgerStep.fault]
    assert DiagnosisFault.model_validate_json(faults[0].payload).reason == "cache_miss"


def test_gate_blocks_over_ceiling_llm_proposal(tmp_path):
    ep, _ = build_fixtures.FIXTURES["blocked"]  # amount 90_000, proposes smart_retry
    llm = _proposer(FIX, str(tmp_path / "t.db"))
    action = llm.propose(llm.diagnose(ep), ep)
    assert action.intervention is Intervention.smart_retry
    decision = PolicyEngine().decide(action, ep)
    assert decision.status is PolicyStatus.blocked
    assert decision.rule_fired == "amount_ceiling"


def _warm_batch_cache(cache_dir, n, seed, control_frac):
    """Author a valid LLM response for each treatment episode of a seeded batch."""
    from recovery_os.batch import _control_ids
    from recovery_os.providers import SimulatedProvider

    cause_by_code = {v: k for k, v in ERROR_CODES.items()}
    prov = SimulatedProvider(seed=seed)
    pids = [f"pay_{seed}_{i}" for i in range(n)]
    control = _control_ids(pids, seed, control_frac, prov)
    Path(cache_dir).mkdir(parents=True, exist_ok=True)
    for pid in pids:
        if f"ep_{pid}" in control:
            continue
        ep = prov.fetch_payment(pid)
        answer = {
            "cause": cause_by_code[ep.raw_error_code].value, "confidence": 0.8,
            "diagnosis_rationale": "from error code", "intervention": "smart_retry",
            "proposal_rationale": "retry",
        }
        key = cache_key("claude-opus-5", _signals(ep))
        (Path(cache_dir) / f"{key}.json").write_text(json.dumps(answer, sort_keys=True))


def test_llm_batch_reproducible_from_cache(tmp_path, monkeypatch):
    from recovery_os.batch import run_batch

    cache = tmp_path / "cache"
    _warm_batch_cache(cache, n=20, seed=7, control_frac=0.2)
    monkeypatch.setenv("RECOVERY_OS_LLM_CACHE_DIR", str(cache))

    a = run_batch(20, seed=7, proposer_kind="llm", db_path=str(tmp_path / "a.db"))
    b = run_batch(20, seed=7, proposer_kind="llm", db_path=str(tmp_path / "b.db"))
    assert a.model_dump_json() == b.model_dump_json()
