import { useMemo, useState } from "react";
import { Search } from "lucide-react";
import type { AskEntry, AskRow } from "../types";
import { titleCase } from "../format";

export function Ask({ data }: { data: AskEntry[] }) {
  const [q, setQ] = useState("");
  const [sel, setSel] = useState(0);

  const filtered = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return data
      .map((e, i) => ({ e, i }))
      .filter(({ e }) => !needle || e.question.toLowerCase().includes(needle)
        || e.answer.toLowerCase().includes(needle));
  }, [q, data]);

  const active = data[sel];

  return (
    <div className="view">
      <div className="view__head">
        <div className="eyebrow">Ask</div>
        <h1 className="view__title">Question the ledger</h1>
        <p className="view__desc">
          Retrieval is deterministic — a typed filter over ledger fields. The model only phrases the rows
          it was given, and every answer cites the exact episodes behind it. Saved answers; no live calls.
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
            <span className={`pill ${active.used_llm ? "ok" : ""}`}>
              {active.used_llm ? "llm narrated" : "deterministic"}
            </span>
            {active.translation_fallback && <span className="pill fault">keyword filter</span>}
            {active.narration_fallback && <span className="pill fault">template narrator</span>}
          </div>

          <div className="answer__body">{active.answer}</div>

          <div className="section-label cited-label">
            <span className="eyebrow">Cited ledger rows</span>
            <span className="hint">the grounding — no row, no claim</span>
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
    <div className="table-wrap">
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
