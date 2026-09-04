import { useMemo, useState } from "react";
import { Search } from "lucide-react";
import type { AskEntry, AskFile, AskRow } from "../types";
import { titleCase } from "../format";

type Mode = "deterministic" | "groq";

export function Ask({ data }: { data: AskFile }) {
  const [q, setQ] = useState("");
  const [sel, setSel] = useState(0);
  const [mode, setMode] = useState<Mode>(data.groq_available ? "groq" : "deterministic");

  const filtered = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return data.entries
      .map((e, i) => ({ e, i }))
      .filter(({ e }) =>
        !needle ||
        e.question.toLowerCase().includes(needle) ||
        e.narration.deterministic.answer.toLowerCase().includes(needle));
  }, [q, data]);

  const active: AskEntry = data.entries[sel];
  const groq = active.narration.groq;
  const shown = mode === "groq" && groq ? groq.answer : active.narration.deterministic.answer;

  return (
    <div className="view">
      <div className="view__head">
        <div className="eyebrow">Ask</div>
        <h1 className="view__title">Question the ledger</h1>
        <p className="view__desc">
          Retrieval is deterministic — a typed filter over ledger fields. Both narrators phrase the same
          retrieved rows: the offline template, and {data.model}. The rows never change; only the wording
          does. Saved answers; no live calls here.
        </p>
      </div>

      <div className="ask-grid">
        <div>
          <div className="ask-search">
            <Search size={15} strokeWidth={1.75} />
            <input
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="Filter saved questions…"
              aria-label="Filter saved questions"
            />
          </div>
          <div className="ask-list">
            {filtered.length === 0 && <div className="ask-empty">No saved question matches that filter.</div>}
            {filtered.map(({ e, i }) => (
              <button
                key={i}
                className={`ask-q${i === sel ? " is-active" : ""}`}
                onClick={() => setSel(i)}
              >
                {e.question}
              </button>
            ))}
          </div>
        </div>

        <div>
          <div className="answer__q">{active.question}</div>

          <div className="answer__meta">
            <span className="pill brass">{active.matched} matched</span>
            <div className="toggle" role="tablist" aria-label="Narrator">
              <button
                role="tab"
                aria-selected={mode === "deterministic"}
                className={`toggle__opt${mode === "deterministic" ? " is-on" : ""}`}
                onClick={() => setMode("deterministic")}
              >
                Deterministic
              </button>
              <button
                role="tab"
                aria-selected={mode === "groq"}
                className={`toggle__opt${mode === "groq" ? " is-on" : ""}`}
                onClick={() => groq && setMode("groq")}
                disabled={!groq}
                title={groq ? undefined : "Re-run export.py with GROQ_API_KEY to add this"}
              >
                Groq
              </button>
            </div>
            {mode === "groq" && groq && <span className="pill ok">{groq.model}</span>}
            {mode === "deterministic" && <span className="pill">template narrator</span>}
          </div>

          <div className="glass answer__body">{shown}</div>

          <div className="section-label cited-label">
            <span className="eyebrow">Cited ledger rows</span>
            <span className="hint">the grounding — identical for both narrators; no row, no claim</span>
          </div>
          <CitedRows rows={active.rows} />
        </div>
      </div>
    </div>
  );
}

function CitedRows({ rows }: { rows: AskRow[] }) {
  if (rows.length === 0) return <div className="ask-empty">No matching ledger records.</div>;
  return (
    <div className="table-wrap glass">
      <table className="data">
        <thead>
          <tr>
            <th>Episode</th><th>Cause</th><th>Action</th><th>Policy</th>
            <th>Executed</th><th>Recovered</th><th>Fault</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.episode_id}>
              <td>{r.episode_id}</td>
              <td>{r.cause ? titleCase(r.cause) : "—"}</td>
              <td>{r.intervention ? r.intervention : "—"}</td>
              <td>
                {r.policy_status === "blocked"
                  ? <span className="pill bad">blocked</span>
                  : r.policy_status ?? "—"}
              </td>
              <td>{r.executed ?? "—"}</td>
              <td className={r.recovered === true ? "pos" : r.recovered === false ? "neg" : "muted"}>
                {r.recovered === undefined ? "—" : String(r.recovered)}
              </td>
              <td className="muted">{r.fault_reason ?? "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
