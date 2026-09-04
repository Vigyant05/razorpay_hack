import type { Comparison, Scorecard as Card } from "../types";
import { pct, rupees, signedPct, titleCase } from "../format";

const POLICY_LABEL: Record<string, string> = {
  agent: "Agent (cause-aware)",
  immediate: "Immediate retry",
  fixed_schedule: "Fixed-schedule dunning",
  never: "Never (self-recovery)",
};

export function Scorecard({ data }: { data: Comparison }) {
  const agent = data.scorecards.find((s) => s.policy === "agent") ?? data.scorecards[0];
  const maxRate = Math.max(...data.scorecards.map((s) => s.incremental_recovery_rate), 0.0001);

  return (
    <div className="view">
      <div className="view__head">
        <div className="eyebrow">Scorecard</div>
        <h1 className="view__title">Incremental recovery, measured honestly</h1>
        <p className="view__desc">
          Headline is lift over a seeded self-recovery holdout — not raw recovery. Confidence intervals,
          false effort, and gate-blocked exceptions are shown, not smoothed over.
        </p>
      </div>

      {/* headline */}
      <div className="glass headline">
        <div>
          <div className="metric__label">
            <span className="eyebrow">Incremental recovery</span>
            <span className="pill brass">headline</span>
          </div>
          <div className="metric__value xl">{signedPct(agent.incremental_recovery_rate)}</div>
          <div className="metric__sub">
            95% CI{" "}
            <span className="ci">
              [{signedPct(agent.incremental_recovery_ci_low)}, {signedPct(agent.incremental_recovery_ci_high)}]
            </span>
          </div>
        </div>
        <div>
          <div className="metric__label"><span className="eyebrow">Incremental recovered</span></div>
          <div className="metric__value lg">{rupees(agent.incremental_amount_paise)}</div>
          <div className="metric__sub">counterfactual-adjusted</div>
        </div>
        <div>
          <div className="metric__label">
            <span className="eyebrow">Raw recovery</span>
            <span className="secondary-tag">secondary</span>
          </div>
          <div className="metric__value sec">{pct(agent.raw_recovery_rate)}</div>
          <div className="metric__sub">not the headline — includes would-recover-anyway</div>
        </div>
      </div>
      <div className="glass context-strip" style={{ marginTop: 1, borderRadius: 0 }}>
        <span><b>policy</b> {POLICY_LABEL[agent.policy]}</span>
        <span><b>episodes</b> {agent.n_episodes}</span>
        <span><b>treatment</b> {agent.n_treatment}</span>
        <span><b>control</b> {agent.n_control}</span>
        <span><b>blocked</b> {agent.n_blocked}</span>
        <span><b>false effort</b> {agent.false_effort_actions} actions · {rupees(agent.false_effort_amount_paise)}</span>
      </div>

      {/* baseline comparison */}
      <div className="section-label">
        <span className="eyebrow">Baseline comparison</span>
        <span className="hint">incremental recovery — agent vs three baselines, same seeded batch</span>
      </div>
      <div className="panel bars">
        {data.scorecards.map((s) => {
          const w = Math.max(0, (s.incremental_recovery_rate / maxRate) * 100);
          const ciL = Math.max(0, (s.incremental_recovery_ci_low / maxRate) * 100);
          const ciH = Math.min(100, (s.incremental_recovery_ci_high / maxRate) * 100);
          const isAgent = s.policy === "agent";
          return (
            <div key={s.policy} className={`bar-row${isAgent ? " is-agent" : ""}`}>
              <span className="bar-row__name">{POLICY_LABEL[s.policy] ?? s.policy}</span>
              <span className="bar-track">
                <span className="bar-fill" style={{ width: `${w}%` }} />
                <span className="bar-ci" style={{ left: `${ciL}%`, width: `${Math.max(1, ciH - ciL)}%` }} />
              </span>
              <span className="bar-row__val">
                {signedPct(s.incremental_recovery_rate)}
                <div className="muted" style={{ fontSize: 11 }}>{s.false_effort_actions} wasted</div>
              </span>
            </div>
          );
        })}
      </div>

      {/* per-cause */}
      <div className="section-label">
        <span className="eyebrow">Per cause · {POLICY_LABEL[agent.policy]}</span>
        <span className="hint">lift = treatment − self-recovery; blocked excluded from the denominator</span>
      </div>
      <PerCauseTable card={agent} />

      {/* exceptions */}
      {agent.exceptions.length > 0 && (
        <>
          <div className="section-label">
            <span className="eyebrow">Exceptions · gate-blocked</span>
            <span className="hint">{agent.exceptions.length} episodes the gate refused — excluded from lift</span>
          </div>
          <div className="panel exceptions">
            {agent.exceptions.map((e) => (
              <div key={e.episode_id} className="exc-row">
                <span className="eid">{e.episode_id}</span>
                <span className="muted">{titleCase(e.cause)}</span>
                <span className="rule" style={{ marginLeft: "auto" }}>{e.reason}</span>
              </div>
            ))}
          </div>
        </>
      )}

      {/* assumptions */}
      <div className="section-label"><span className="eyebrow">Modeling assumptions</span></div>
      <div className="panel assumptions">
        <div>
          <p className="assumptions__note">{agent.assumptions_note}</p>
          <div className="assumptions__rates">
            {Object.entries(agent.assumptions).map(([cause, rate]) => (
              <span key={cause} className="pill">{titleCase(cause)} {pct(rate, 0)}</span>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

function PerCauseTable({ card }: { card: Card }) {
  return (
    <div className="table-wrap">
      <table className="data">
        <thead>
          <tr>
            <th>Cause</th><th>n_t</th><th>n_c</th><th>blk</th>
            <th>Treat%</th><th>Self%</th><th>Lift</th><th>95% CI</th><th>Incr ₹</th>
          </tr>
        </thead>
        <tbody>
          {card.per_cause.map((c) => {
            const neg = c.incremental_lift < 0;
            return (
              <tr key={c.cause}>
                <td>{titleCase(c.cause)}</td>
                <td className="num">{c.n_treatment}</td>
                <td className="num">{c.n_control}</td>
                <td className="num muted">{c.n_blocked}</td>
                <td className="num">{pct(c.treatment_recovery_rate, 0)}</td>
                <td className="num muted">{pct(c.control_recovery_rate, 0)}</td>
                <td className={`num ${neg ? "neg" : "pos"}`}>{signedPct(c.incremental_lift, 0)}</td>
                <td className="num ci-cell">
                  [{signedPct(c.lift_ci_low, 0)}, {signedPct(c.lift_ci_high, 0)}]
                </td>
                <td className={`num ${c.incremental_amount_paise < 0 ? "neg" : ""}`}>
                  {rupees(c.incremental_amount_paise)}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
