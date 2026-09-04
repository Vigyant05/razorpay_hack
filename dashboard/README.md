# Recovery OS — audit console

A **read-only** viewer over data the Recovery OS backend already produced. It
renders three committed JSON exports and computes nothing — if a number isn't in
the JSON, it isn't shown. No API key, no network, no backend needed to run.

Three views:
- **Live Trace** — one payment through the pipeline (detect → diagnose → propose
  → policy gate → sign → execute → verify → attribute), one row per ledger step.
  A fault (LLM cache-miss → heuristic fallback) visibly breaks the audit spine.
- **Scorecard** — incremental recovery + 95% CI (headline), incremental ₹, raw
  recovery (secondary), per-cause lift table, false effort, gate-blocked
  exceptions, and the baseline comparison. Assumptions + benchmark citation shown.
- **Ask** — the saved RAG answers; each cites the exact ledger rows behind it.

## Run it offline

```bash
cd dashboard
npm install        # once (needs network once; fonts are then bundled locally)
npm run dev        # http://localhost:5173
```

Or build a static bundle and serve it:

```bash
npm run build && npm run preview   # http://localhost:4173
```

The build is fully self-contained (fonts included) — `dist/` serves from any
static host with no network.

## Data

The three files in `src/data/` are committed samples so the app renders out of
the box. Regenerate them from the real backend (deterministic, offline):

```bash
python dashboard/export.py          # rewrites src/data/{scorecard,trace,ask}.json
```

`export.py` is read-only: it reads a seeded ledger + scorecard and reshapes them
for the viewer. Shapes are documented in `src/types.ts`.

- `scorecard.json` — `Comparison` (agent + 3 baselines).
- `trace.json` — three example episodes (fault-handled / gate-blocked / clean).
- `ask.json` — the five must-answer questions with their cited rows.

## Stack

React + Vite + TypeScript, hand-written CSS (no UI framework), `lucide-react`
icons, `@fontsource` for Inter + JetBrains Mono. One accent (muted brass);
true-dark neutral base.
