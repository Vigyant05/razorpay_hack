"""Read-only exporter: turn Recovery OS ledger + scorecard into the three static
JSON files the dashboard renders. NO recovery logic here — it reads what the
backend produced and reshapes it for the viewer. Re-run to refresh the samples:

    python dashboard/export.py

Deterministic and offline (no API key). Writes to dashboard/src/data/.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
OUT = Path(__file__).resolve().parent / "src" / "data"

SEED, N = 42, 60


def _rows(db: str):
    c = sqlite3.connect(db)
    out = defaultdict(list)
    for eid, step, payload, sig in c.execute(
            "select episode_id, step, payload, signature from ledger order by id"):
        out[eid].append({"step": step, "payload": json.loads(payload), "signature": sig})
    c.close()
    return out


def _episode(steps: list[dict]) -> dict:
    ep = next(s["payload"] for s in steps if s["step"] == "episode")
    return {
        "episode_id": ep["episode_id"],
        "meta": {"amount": ep["amount"], "method": ep["method"],
                 "error_code": ep["raw_error_code"], "attempt": ep["attempt"]},
        "steps": [{"step": s["step"], "payload": s["payload"], "signature": s["signature"]}
                  for s in steps],
    }


def _has(steps, name) -> bool:
    return any(s["step"] == name for s in steps)


def _recovered(steps) -> bool:
    v = next((s["payload"] for s in steps if s["step"] == "verification"), None)
    return bool(v and v.get("recovered"))


def build_trace(llm_db: str, heur_db: str) -> dict:
    llm, heur = _rows(llm_db), _rows(heur_db)

    def _eid(steps) -> str:
        return next(s["payload"]["episode_id"] for s in steps if s["step"] == "episode")

    def pick(rows, pred, exclude):
        cands = [s for s in rows.values() if pred(s) and _eid(s) not in exclude]
        cands.sort(key=lambda s: (not _recovered(s), -len(s)))  # prefer recovered, richer
        return cands[0] if cands else None

    seen: set[str] = set()
    picks = [
        ("fault", pick(llm, lambda s: _has(s, "fault") and _has(s, "mandate"), seen)),
    ]
    seen.add(_eid(picks[0][1])) if picks[0][1] else None
    picks.append(("blocked", pick(heur, lambda s: any(
        st["step"] == "policy" and st["payload"].get("status") == "blocked" for st in s), seen)))
    seen.add(_eid(picks[1][1])) if picks[1][1] else None
    picks.append(("clean", pick(heur, lambda s: _has(s, "mandate")
                  and not _has(s, "fault") and _recovered(s), seen)))

    episodes = []
    for label, steps in picks:
        if steps is None:
            continue
        e = _episode(steps)
        e["example"] = label
        episodes.append(e)
    return {"seed": SEED, "episodes": episodes}


QUESTIONS = [
    "why did the agent act on ep_pay_42_0?",
    "show every episode the agent refused to act on and why",
    "which episodes had an LLM fault and what happened?",
    "list every gate-blocked action and the rule that blocked it",
    "what did the agent do for insufficient_funds cases?",
]


def build_ask(db: str) -> dict:
    """Same deterministic retrieval for every question; two narrations over the
    SAME rows — the offline template narrator, and (if a key is present) Groq.
    Grounding is identical; only the phrasing differs."""
    from recovery_os import rag

    cache = db + ".askcache"
    views = rag.build_views(db)
    call_fn, model = rag.resolve_backend()  # Groq/Anthropic if a key is set, else None

    entries = []
    for q in QUESTIONS:
        f = rag.keyword_filter(q)  # deterministic filter -> stable rows for both narrators
        rows = rag.query(f, views)
        det = rag.deterministic_answer(q, rows)[0]
        groq = None
        if call_fn is not None and rows:
            ans, _cited, used_llm, nfb = rag.narrate(q, rows, call_fn, model, Path(cache))
            if used_llm and not nfb:
                groq = {"answer": ans, "model": model}
        entries.append({
            "question": q,
            "filter": f.model_dump(mode="json"),
            "matched": len(rows),
            "cited_episode_ids": [v.episode_id for v in rows],
            "rows": [v.model_dump(mode="json", exclude_none=True) for v in rows],
            "narration": {"deterministic": {"answer": det}, "groq": groq},
        })
    return {"groq_available": call_fn is not None, "model": model, "entries": entries}


def main() -> None:
    from recovery_os.config import load_env_file
    load_env_file()  # pick up GROQ_API_KEY from .env for the Groq narration
    OUT.mkdir(parents=True, exist_ok=True)
    tmp = OUT / "_build"
    tmp.mkdir(exist_ok=True)
    llm_db, heur_db = str(tmp / "llm.db"), str(tmp / "heur.db")

    import build_rag_fixtures as fx
    from recovery_os.batch import run_batch, run_compare

    fx.build_ledger(llm_db, n=N, seed=SEED)  # llm path: produces fault rows
    run_batch(N, seed=SEED, policy="agent", proposer_kind="heuristic", db_path=heur_db)  # clean rows

    cmp = run_compare(N, seed=SEED, db_path=str(tmp / "cmp.db"))
    (OUT / "scorecard.json").write_text(cmp.model_dump_json(indent=1))
    (OUT / "trace.json").write_text(json.dumps(build_trace(llm_db, heur_db), indent=1))
    (OUT / "ask.json").write_text(json.dumps(build_ask(llm_db), indent=1))
    print(f"wrote scorecard.json, trace.json, ask.json to {OUT}")


if __name__ == "__main__":
    main()
