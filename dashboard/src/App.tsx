import { useState } from "react";
import { Activity, BarChart3, MessagesSquare, ShieldCheck } from "lucide-react";
import { Trace } from "./views/Trace";
import { Scorecard } from "./views/Scorecard";
import { Ask } from "./views/Ask";
import type { AskFile, Comparison, TraceFile } from "./types";
import scorecardJson from "./data/scorecard.json";
import traceJson from "./data/trace.json";
import askJson from "./data/ask.json";

const comparison = scorecardJson as unknown as Comparison;
const traceData = traceJson as unknown as TraceFile;
const askData = askJson as unknown as AskFile;

type ViewId = "trace" | "scorecard" | "ask";

const NAV: { id: ViewId; label: string; icon: typeof Activity; count: string }[] = [
  { id: "trace", label: "Live Trace", icon: Activity, count: String(traceData.episodes.length) },
  { id: "scorecard", label: "Scorecard", icon: BarChart3, count: String(comparison.scorecards.length) },
  { id: "ask", label: "Ask", icon: MessagesSquare, count: String(askData.entries.length) },
];

export function App() {
  const [view, setView] = useState<ViewId>("trace");

  return (
    <div className="app">
      <aside className="rail">
        <div className="wordmark">
          <span className="wordmark__badge">
            <ShieldCheck size={15} strokeWidth={2} />
          </span>
          <div>
            <div className="wordmark__name">Recovery OS</div>
            <div className="wordmark__sub">AUDIT CONSOLE</div>
          </div>
        </div>

        <nav className="nav">
          {NAV.map(({ id, label, icon: Icon, count }) => (
            <button
              key={id}
              className={`nav__item${view === id ? " is-active" : ""}`}
              onClick={() => setView(id)}
              aria-current={view === id}
            >
              <Icon size={16} strokeWidth={1.75} />
              {label}
              <span className="nav__num">{count}</span>
            </button>
          ))}
        </nav>

        <div className="rail__spacer" />

        <div className="source">
          <div className="eyebrow">Data source</div>
          <div className="source__row"><b>scorecard</b> ·json</div>
          <div className="source__row"><b>trace</b> ·json</div>
          <div className="source__row"><b>ask</b> ·json</div>
          <div className="source__note">
            <span className="dot-live" /> static export · seed {comparison.seed} · read-only
          </div>
        </div>
      </aside>

      <main className="main">
        {view === "trace" && <Trace data={traceData} />}
        {view === "scorecard" && <Scorecard data={comparison} />}
        {view === "ask" && <Ask data={askData} />}
      </main>
    </div>
  );
}
