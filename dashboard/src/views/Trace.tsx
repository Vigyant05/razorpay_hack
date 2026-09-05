import { useState } from "react";
import { Check } from "lucide-react";
import type { TraceEpisode, TraceFile, TraceStep } from "../types";
import { pct, rupees, shortHash, titleCase } from "../format";

const EXAMPLE_LABEL: Record<string, string> = {
  fault: "fault handled",
  blocked: "gate blocked",
  clean: "clean run",
  live: "live razorpay",
};

function KV({ k, v }: { k: string; v: React.ReactNode }) {
  return (
    <span className="kv">
      {k} <b>{v}</b>
    </span>
  );
}

function stepContent(step: TraceStep): React.ReactNode {
  const p = step.payload as Record<string, unknown>;
  switch (step.step) {
    case "episode":
      return (
        <>
          <KV k="amount" v={rupees(Number(p.amount))} />
          <KV k="method" v={String(p.method)} />
          <KV k="error" v={String(p.raw_error_code)} />
          <KV k="attempt" v={String(p.attempt)} />
        </>
      );
    case "fault":
      return (
        <>
          <KV k="reason" v={String(p.reason)} />
          <KV k="→" v={`fell back to ${String(p.fell_back_to)}`} />
          {p.raw_excerpt ? <span className="kv muted">{String(p.raw_excerpt)}</span> : null}
        </>
      );
    case "diagnosis":
      return (
        <>
          <KV k="cause" v={String(p.cause)} />
          <KV k="confidence" v={pct(Number(p.confidence), 0)} />
          <span className="kv muted">{String(p.rationale)}</span>
        </>
      );
    case "proposal":
      return (
        <>
          <KV k="intervention" v={String(p.intervention)} />
          <span className="kv muted">{String(p.rationale)}</span>
        </>
      );
    case "policy": {
      const status = String(p.status);
      const blocked = status === "blocked";
      return (
        <>
          <span className={`pill ${blocked ? "bad" : "ok"}`}>{status}</span>
          {p.rule_fired ? <KV k="rule" v={String(p.rule_fired)} /> : <span className="kv muted">no rule fired</span>}
          {p.reason ? <span className="kv muted">{String(p.reason)}</span> : null}
        </>
      );
    }
    case "mandate": {
      const sig = step.signature || String((p as { signature?: string }).signature ?? "");
      return (
        <>
          <span className="pill ok">
            <span className="tick"><Check size={11} strokeWidth={2.5} /></span> ed25519 present
          </span>
          <KV k="sig" v={shortHash(sig)} />
        </>
      );
    }
    case "execution": {
      const status = String(p.status);
      const ok = status === "success";
      return (
        <>
          <span className={`pill ${ok ? "ok" : status === "failed" ? "bad" : ""}`}>{status}</span>
          {p.detail ? <span className="kv muted">{String(p.detail)}</span> : null}
        </>
      );
    }
    case "verification": {
      const rec = Boolean(p.recovered);
      return (
        <>
          <span className={`pill ${rec ? "ok" : "bad"}`}>{rec ? "recovered" : "not recovered"}</span>
          {p.detail ? <span className="kv muted">{String(p.detail)}</span> : null}
        </>
      );
    }
    case "attribution":
      return (
        <>
          <KV k="recovered" v={String(p.recovered)} />
          <KV k="incremental" v={String(p.incremental)} />
          <span className="kv muted">{String(p.counterfactual)}</span>
        </>
      );
    default:
      return <span className="kv muted">{JSON.stringify(p)}</span>;
  }
}

function Step({ step }: { step: TraceStep }) {
  const isFault = step.step === "fault";
  return (
    <div className={`step${isFault ? " is-fault" : ""}`}>
      <div className="step__rail">
        <span className="step__dot" />
      </div>
      <div className="step__body">
        <div className="step__top">
          <span className="step__name">{step.step === "verification" ? "verify" : step.step}</span>
        </div>
        <div className="step__content">{stepContent(step)}</div>
      </div>
    </div>
  );
}

export function Trace({ data }: { data: TraceFile }) {
  const [idx, setIdx] = useState(0);
  const ep: TraceEpisode = data.episodes[idx];

  return (
    <div className="view">
      <div className="view__head">
        <div className="eyebrow">Live Trace</div>
        <h1 className="view__title">One payment through the pipeline</h1>
        <p className="view__desc">
          detect → diagnose → propose → policy gate → sign → execute → verify → attribute. Every step is
          one immutable ledger row. A fault (LLM cache-miss) is not hidden — it breaks the spine and the
          run continues on the heuristic.
        </p>
      </div>

      <div className="episode-tabs">
        {data.episodes.map((e, i) => (
          <button
            key={e.episode_id}
            className={`episode-tab${i === idx ? " is-active" : ""}`}
            onClick={() => setIdx(i)}
          >
            <span className="episode-tab__k">{EXAMPLE_LABEL[e.example] ?? e.example}</span>
            <span className="episode-tab__v">{e.episode_id}</span>
          </button>
        ))}
      </div>

      <div className="episode-meta">
        <div className="meta-cell"><span className="eyebrow">Episode</span><b>{ep.episode_id}</b></div>
        <div className="meta-cell"><span className="eyebrow">Amount</span><b>{rupees(ep.meta.amount)}</b></div>
        <div className="meta-cell"><span className="eyebrow">Method</span><b>{ep.meta.method}</b></div>
        <div className="meta-cell"><span className="eyebrow">Error code</span><b>{ep.meta.error_code}</b></div>
        <div className="meta-cell"><span className="eyebrow">Cause</span><b>{titleCase(causeOf(ep))}</b></div>
        {ep.provider && (
          <div className="meta-cell"><span className="eyebrow">Provider</span><b>{ep.provider}</b></div>
        )}
      </div>

      <div className="panel spine">
        {ep.steps.map((s, i) => (
          <Step key={i} step={s} />
        ))}
      </div>
    </div>
  );
}

function causeOf(ep: TraceEpisode): string {
  const d = ep.steps.find((s) => s.step === "diagnosis");
  return d ? String((d.payload as { cause?: string }).cause ?? "—") : "—";
}
