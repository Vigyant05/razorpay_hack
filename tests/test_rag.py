import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import build_rag_fixtures as fx  # noqa: E402

from recovery_os.domain import FailureCause, Intervention  # noqa: E402
from recovery_os.rag import (  # noqa: E402
    EpisodeView,
    LedgerFilter,
    LedgerNotFound,
    ask,
    build_views,
    keyword_filter,
    narrate,
    query,
    translate,
)

FIX = fx.FIXTURE_DIR


@pytest.fixture(scope="module")
def ledger_db(tmp_path_factory):
    db = str(tmp_path_factory.mktemp("rag") / "ledger.db")
    fx.build_ledger(db, n=25, seed=42)
    return db


@pytest.fixture(autouse=True)
def _offline(monkeypatch, tmp_path):
    # force offline so ask() uses keyword translate + deterministic narrate
    for k in ("GROQ_API_KEY", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "RECOVERY_OS_LLM_MODEL"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("RECOVERY_OS_LLM_CACHE_DIR", str(tmp_path / "empty"))


# --- deterministic query layer ----------------------------------------------

def test_query_layer_deterministic(ledger_db):
    v1 = build_views(ledger_db)
    v2 = build_views(ledger_db)
    assert [x.model_dump() for x in v1] == [x.model_dump() for x in v2]
    nudges = query(LedgerFilter(intervention=Intervention.customer_nudge), v1)
    assert nudges and all(x.intervention is Intervention.customer_nudge for x in nudges)


def test_keyword_fallback():
    assert keyword_filter("why did the agent act on pay_42_3?").episode_id == "ep_pay_42_3"
    assert keyword_filter("which episodes had an LLM fault?").has_fault is True
    assert keyword_filter("list every gate-blocked action").gate_blocked is True
    assert keyword_filter("episodes the agent refused to act on").declined is True
    assert keyword_filter("what did the agent do for insufficient_funds?").cause \
        is FailureCause.insufficient_funds


# --- LLM translate / narrate parsing (from committed fixtures) ---------------

def test_translation_parses_fixture():
    f, fell_back = translate(fx.Q_PARSE, call_fn=None, model=fx.MODEL, cache_dir=FIX)
    assert fell_back is False
    assert f.cause is FailureCause.insufficient_funds


def test_malformed_translation_falls_back():
    # cached response has cause="banana" -> parse fails -> keyword fallback
    f, fell_back = translate(fx.Q_MALFORMED, call_fn=None, model=fx.MODEL, cache_dir=FIX)
    assert fell_back is True
    assert f.cause is None  # keywords found nothing usable in "odd episodes"


def test_narrator_cites_only_retrieved():
    views = [EpisodeView(episode_id="ep_pay_1", cause=FailureCause.insufficient_funds),
             EpisodeView(episode_id="ep_pay_2", cause=FailureCause.insufficient_funds)]
    answer, cited, used_llm, nfb = narrate(
        fx.Q_NARRATE, views, call_fn=None, model=fx.MODEL, cache_dir=FIX)
    assert used_llm is True and nfb is False
    assert cited == ["ep_pay_1", "ep_pay_2"]  # bogus id dropped, reals kept


# --- grounding: unanswerable -> honest no-match ------------------------------

def test_missing_ledger_raises(tmp_path):
    with pytest.raises(LedgerNotFound):
        build_views(str(tmp_path / "nope.db"))


def test_ask_no_match_is_honest(ledger_db):
    r = ask("why did the agent act on ep_pay_does_not_exist?", db_path=ledger_db)
    assert r.matched == 0
    assert "No matching ledger records" in r.answer
    assert r.cited_episode_ids == []


# --- the five acceptance questions (offline deterministic path) --------------

def test_ask_why_episode(ledger_db):
    eid = build_views(ledger_db)[0].episode_id
    r = ask(f"why did the agent act on {eid}?", db_path=ledger_db)
    assert r.matched == 1 and eid in r.cited_episode_ids


def test_ask_declined(ledger_db):
    views = build_views(ledger_db)
    expected = {v.episode_id for v in views if v.declined}
    r = ask("show every episode the agent refused to act on and why", db_path=ledger_db)
    assert r.matched == len(expected) and set(r.cited_episode_ids) == expected


def test_ask_faults(ledger_db):
    views = build_views(ledger_db)
    expected = {v.episode_id for v in views if v.fault_reason is not None}
    r = ask("which episodes had an LLM fault and what happened?", db_path=ledger_db)
    assert r.matched == len(expected) and set(r.cited_episode_ids) == expected


def test_ask_gate_blocked(ledger_db):
    views = build_views(ledger_db)
    expected = {v.episode_id for v in views if v.gate_blocked}
    r = ask("list every gate-blocked action and the rule that blocked it", db_path=ledger_db)
    assert r.matched == len(expected)
    matched = query(r.filter, views)
    assert all(v.gate_blocked and v.rule_fired for v in matched)


def test_ask_by_cause(ledger_db):
    views = build_views(ledger_db)
    expected = {v.episode_id for v in views if v.cause is FailureCause.insufficient_funds}
    r = ask("what did the agent do for insufficient_funds cases?", db_path=ledger_db)
    assert r.matched == len(expected) and set(r.cited_episode_ids) == expected
