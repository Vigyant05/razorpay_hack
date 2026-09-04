"""Phase 4: natural-language Q&A over the audit ledger. READ-ONLY.

The ledger is structured, so retrieval is deterministic: we reconstruct each
episode into a typed EpisodeView and filter over real fields. The LLM is the
language layer only — it translates a question into a typed filter and narrates
the retrieved rows, citing episode ids. It never decides relevance by vibes and
never invents ledger contents: no row -> no claim. Offline (no backend), a
keyword translator + a deterministic narrator answer the same questions.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from pydantic import BaseModel, ValidationError
from sqlalchemy.exc import OperationalError
from sqlmodel import Session, create_engine, select

from .domain import ExecStatus, FailureCause, Intervention, PolicyStatus
from .ledger import LedgerEntry
from .llm import _Fault, cached_tool_call, default_cache_dir, resolve_backend


class LedgerNotFound(Exception):
    """The db has no ledger to query (missing file or no rows yet)."""

_CAUSES = [c.value for c in FailureCause]
_INTERVENTIONS = [i.value for i in Intervention]


# --- typed view + filter (deterministic core) --------------------------------

class EpisodeView(BaseModel):
    episode_id: str
    amount: int | None = None
    method: str | None = None
    cause: FailureCause | None = None
    confidence: float | None = None
    intervention: Intervention | None = None
    policy_status: PolicyStatus | None = None
    rule_fired: str | None = None
    executed: ExecStatus | None = None
    recovered: bool | None = None
    fault_reason: str | None = None
    steps: list[str] = []

    @property
    def declined(self) -> bool:
        return (self.intervention is Intervention.do_nothing
                or self.policy_status is PolicyStatus.blocked)

    @property
    def gate_blocked(self) -> bool:
        return self.policy_status is PolicyStatus.blocked


class LedgerFilter(BaseModel):
    episode_id: str | None = None
    cause: FailureCause | None = None
    intervention: Intervention | None = None
    recovered: bool | None = None
    gate_blocked: bool | None = None
    declined: bool | None = None
    has_fault: bool | None = None
    amount_min: int | None = None
    amount_max: int | None = None


class AnswerResult(BaseModel):
    question: str
    filter: LedgerFilter
    matched: int
    cited_episode_ids: list[str]
    answer: str
    used_llm: bool
    translation_fallback: bool
    narration_fallback: bool


def build_views(db_path: str) -> list[EpisodeView]:
    """Reconstruct one EpisodeView per episode from the append-only rows. Read-only."""
    if not Path(db_path).exists():
        raise LedgerNotFound(f"no ledger database at {db_path!r}")
    engine = create_engine(f"sqlite:///{db_path}")
    try:
        with Session(engine) as s:
            rows = list(s.exec(select(LedgerEntry).order_by(LedgerEntry.id)))
    except OperationalError:  # file exists but no ledger table (never written to)
        raise LedgerNotFound(f"{db_path!r} has no ledger data")

    views: dict[str, EpisodeView] = {}
    for row in rows:
        v = views.setdefault(row.episode_id, EpisodeView(episode_id=row.episode_id))
        v.steps.append(row.step.value)
        p = json.loads(row.payload)
        step = row.step.value
        if step == "episode":
            v.amount, v.method = p.get("amount"), p.get("method")
        elif step == "diagnosis":
            v.cause, v.confidence = FailureCause(p["cause"]), p.get("confidence")
        elif step == "proposal":
            v.intervention = Intervention(p["intervention"])
        elif step == "policy":
            v.policy_status, v.rule_fired = PolicyStatus(p["status"]), p.get("rule_fired")
        elif step == "execution":
            v.executed = ExecStatus(p["status"])
        elif step == "verification":
            v.recovered = p.get("recovered")
        elif step == "fault":
            v.fault_reason = p.get("reason")
    return list(views.values())


def query(f: LedgerFilter, views: list[EpisodeView]) -> list[EpisodeView]:
    """Deterministic AND of the provided predicates. No LLM."""
    out = []
    for v in views:
        if f.episode_id is not None and v.episode_id != f.episode_id:
            continue
        if f.cause is not None and v.cause is not f.cause:
            continue
        if f.intervention is not None and v.intervention is not f.intervention:
            continue
        if f.recovered is not None and v.recovered is not f.recovered:
            continue
        if f.gate_blocked is not None and v.gate_blocked != f.gate_blocked:
            continue
        if f.declined is not None and v.declined != f.declined:
            continue
        if f.has_fault is not None and (v.fault_reason is not None) != f.has_fault:
            continue
        if f.amount_min is not None and (v.amount is None or v.amount < f.amount_min):
            continue
        if f.amount_max is not None and (v.amount is None or v.amount > f.amount_max):
            continue
        out.append(v)
    return out


# --- question -> filter (LLM translate, keyword fallback) ---------------------

TOOL_FILTER = {
    "name": "submit_filter",
    "description": "A structured filter over ledger fields for the user's question.",
    "input_schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "episode_id": {"type": "string"},
            "cause": {"enum": _CAUSES},
            "intervention": {"enum": _INTERVENTIONS},
            "recovered": {"type": "boolean"},
            "gate_blocked": {"type": "boolean"},
            "declined": {"type": "boolean", "description": "agent did not act (do_nothing or gate-blocked)"},
            "has_fault": {"type": "boolean", "description": "an LLM diagnosis fault occurred"},
            "amount_min": {"type": "integer"},
            "amount_max": {"type": "integer"},
        },
    },
}

SYSTEM_TRANSLATE = (
    "Translate the user's question about a payment-recovery audit ledger into a "
    "structured filter by calling submit_filter. Only set fields the question "
    "clearly implies; leave the rest unset. Use enum values exactly. Do not answer "
    "the question — only build the filter."
)


def keyword_filter(question: str) -> LedgerFilter:
    """Deterministic fallback translator: scan the question for known tokens."""
    q = question.lower()
    f = LedgerFilter()
    m = re.search(r"\b((?:ep_)?pay_[a-z0-9_]+)\b", q)
    if m:
        eid = m.group(1)
        f.episode_id = eid if eid.startswith("ep_") else f"ep_{eid}"
    if "fault" in q:
        f.has_fault = True
    if "block" in q or "gate" in q:
        f.gate_blocked = True
    if "refus" in q or "declin" in q or "do nothing" in q or "do_nothing" in q \
            or "not act" in q or "didn't act" in q or "did not act" in q:
        f.declined = True
    if "unrecovered" in q or "not recover" in q or "still fail" in q or "still unpaid" in q:
        f.recovered = False
    elif "recovered" in q:
        f.recovered = True
    for c in FailureCause:
        if c.value in q or c.value.replace("_", " ") in q:
            f.cause = c
    for i in Intervention:
        if i.value in q or i.value.replace("_", " ") in q:
            f.intervention = i
    return f


def _parse_filter(raw: dict) -> LedgerFilter:
    return LedgerFilter(**{k: v for k, v in raw.items() if v is not None})


def translate(question: str, call_fn, model: str, cache_dir: Path) -> tuple[LedgerFilter, bool]:
    """Return (filter, used_fallback)."""
    try:
        raw = cached_tool_call(call_fn, model, cache_dir,
                               {"task": "translate", "question": question},
                               SYSTEM_TRANSLATE, question, TOOL_FILTER)
        return _parse_filter(raw), False
    except (_Fault, ValidationError, ValueError, KeyError, TypeError):
        return keyword_filter(question), True


# --- rows -> answer (LLM narrate, deterministic fallback) ---------------------

TOOL_ANSWER = {
    "name": "submit_answer",
    "description": "Answer the question using ONLY the supplied ledger rows; cite episode ids.",
    "input_schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["answer", "cited_episode_ids"],
        "properties": {
            "answer": {"type": "string"},
            "cited_episode_ids": {"type": "array", "items": {"type": "string"}},
        },
    },
}

SYSTEM_NARRATE = (
    "You answer questions about a payment-recovery audit ledger. Use ONLY the rows "
    "provided — never invent episodes, causes, or outcomes. Cite the episode ids you "
    "used. If the rows do not contain the answer, say so plainly. Call submit_answer."
)


def _facts(v: EpisodeView) -> dict:
    return v.model_dump(exclude={"steps"}, exclude_none=True) | {"steps": v.steps}


def _line(v: EpisodeView) -> str:
    bits = []
    if v.cause:
        bits.append(f"cause={v.cause.value}" + (f" (conf {v.confidence})" if v.confidence is not None else ""))
    if v.intervention:
        bits.append(f"action={v.intervention.value}")
    if v.policy_status:
        bits.append(f"policy={v.policy_status.value}" + (f" [{v.rule_fired}]" if v.rule_fired else ""))
    if v.executed:
        bits.append(f"executed={v.executed.value}")
    if v.recovered is not None:
        bits.append(f"recovered={v.recovered}")
    if v.fault_reason:
        bits.append(f"fault={v.fault_reason}")
    return f"- {v.episode_id}: " + ", ".join(bits) + f"  [cites: {', '.join(dict.fromkeys(v.steps))}]"


def deterministic_answer(question: str, views: list[EpisodeView]) -> tuple[str, list[str], bool]:
    """Grounded template answer. Returns (text, cited_ids, used_llm=False)."""
    if not views:
        return "No matching ledger records for that question.", [], False
    header = f"{len(views)} matching episode(s):"
    body = "\n".join(_line(v) for v in views)
    return f"{header}\n{body}", [v.episode_id for v in views], False


def narrate(question: str, views: list[EpisodeView], call_fn, model: str,
            cache_dir: Path) -> tuple[str, list[str], bool, bool]:
    """Return (answer, cited_ids, used_llm, narration_fallback)."""
    if not views:
        text, cited, _ = deterministic_answer(question, views)
        return text, cited, False, False
    ids = {v.episode_id for v in views}
    user = (f"Question: {question}\n\nLedger rows (answer only from these):\n"
            + json.dumps([_facts(v) for v in views], sort_keys=True))
    try:
        raw = cached_tool_call(call_fn, model, cache_dir,
                               {"task": "narrate", "question": question,
                                "episodes": sorted(ids)},
                               SYSTEM_NARRATE, user, TOOL_ANSWER)
        answer = str(raw["answer"])
        cited = [c for c in raw.get("cited_episode_ids", []) if c in ids]  # drop invented ids
        if not answer.strip():
            raise _Fault("malformed", "empty answer")
        return answer, cited, True, False
    except (_Fault, KeyError, ValueError, TypeError):
        text, cited, _ = deterministic_answer(question, views)
        return text, cited, False, True


def ask(question: str, db_path: str, call_fn=None, model: str | None = None,
        cache_dir: str | None = None) -> AnswerResult:
    if call_fn is None and model is None:
        call_fn, model = resolve_backend()
    cdir = Path(cache_dir) if cache_dir else default_cache_dir()

    views_all = build_views(db_path)
    filt, tfb = translate(question, call_fn, model, cdir)
    matched = query(filt, views_all)
    answer, cited, used_llm, nfb = narrate(question, matched, call_fn, model, cdir)

    if tfb or nfb:
        print(f"[ask] fell back to {'keyword filter' if tfb else 'LLM'}"
              f"{' + template narrator' if nfb else ''}", file=sys.stderr)
    return AnswerResult(
        question=question, filter=filt, matched=len(matched), cited_episode_ids=cited,
        answer=answer, used_llm=used_llm, translation_fallback=tfb, narration_fallback=nfb,
    )
