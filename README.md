# Recovery OS

A **bounded, self-auditing agent** that handles failed payments end to end —
Razorpay buildathon, Track 3 (AI Revenue Recovery).

An LLM can *propose* what to do about a failed payment, but a deterministic
policy gate *disposes*: nothing moves money until it passes a non-LLM rule check
and is cryptographically signed. Every step is written to an append-only,
tamper-evident ledger, and results are measured as **incremental** recovery over
a holdout — not the flattering raw number.

```
detect → diagnose → propose → policy gate → sign → execute → verify → attribute
```

---

## The one guarantee

> **The LLM proposes. The deterministic engine disposes.**

It is enforced by the type system, not by convention: `PaymentProvider.execute`
accepts only a `SignedMandate`, and the only way to obtain one is
`signing.issue_mandate`, which raises `PolicyViolation` on a blocked decision.
There is **no code path** from a raw `ProposedAction` to `execute`. A malformed
or malicious LLM output is just untrusted input to the same gate.

---

## Architecture

```mermaid
flowchart LR
  CLI["cli.py<br/>run · batch · ask"]

  subgraph backend["Python backend"]
    direction TB
    ORC["orchestrator<br/>run loop + seams"]
    POL["policy<br/>PolicyEngine (gate)"]
    SIGN["signing<br/>ed25519 mandate"]
    PROV["providers<br/>Simulated · RazorpayTest"]
    LLM["llm<br/>Groq/Anthropic + record-replay cache"]
    BATCH["batch + scorecard<br/>holdout · incremental lift"]
    RAG["rag<br/>read-only Q&amp;A"]
    LED[("ledger<br/>append-only SQLite")]
  end

  subgraph dash["dashboard/ — READ-ONLY"]
    direction TB
    EXP["export.py"] --> JSON[("src/data/*.json")] --> UI["React + Vite viewer"]
  end

  CLI --> ORC
  BATCH --> ORC
  ORC --> POL --> SIGN --> PROV
  ORC -->|"one row / step"| LED
  ORC -. "diagnose / propose seam" .-> LLM
  RAG -->|read| LED
  LED -. export .-> EXP
  BATCH -. export .-> EXP
  LLM -. "optional narration" .-> EXP
```

**Reading it:** the CLI (or the batch runner) drives `orchestrator.run_episode`.
The loop asks the seam for a diagnosis + proposal (heuristic by default, LLM when
selected), routes it through the gate, signs it, executes via the active
provider, and appends one immutable row per step to the ledger. `batch` runs the
same loop over many episodes; `scorecard` measures them; `rag` answers questions
by *reading* the ledger. The dashboard renders exported JSON and touches none of
this.

---

## The recovery loop (workflow)

```mermaid
flowchart TD
  A["Failed payment"] --> B["detect<br/>provider.fetch_payment"]
  B --> C["diagnose<br/>cause + confidence"]
  C --> D["propose<br/>one bounded intervention"]
  D --> E{"policy gate"}
  E -->|"blocked<br/>(amount_ceiling, max_attempts)"| X["exception<br/>no mandate · no execute"]
  E -->|approved| F["sign<br/>issue_mandate · ed25519"]
  F --> G["execute<br/>provider.execute(mandate)"]
  G --> H["verify<br/>did it recover?"]
  H --> I["attribute<br/>incremental vs self-recovery"]

  C -.->|"LLM fault:<br/>cache-miss · off-enum · API error"| Z["log fault row<br/>fall back to heuristic"]
  Z -.-> D

  B --> L[("ledger")]
  C --> L
  D --> L
  E --> L
  F --> L
  G --> L
  H --> L
  I --> L
  Z --> L
```

Failure is handled, not hidden: a bad LLM response logs a `fault` row and the run
continues on the heuristic — it never crashes a batch or skips the gate. A
gate-blocked action stops before any mandate exists.

Interventions are a fixed enum (`smart_retry`, `method_switch`, `mandate_reauth`,
`customer_nudge`, `human_escalation`, `do_nothing`); causes likewise
(`issuer_downtime`, `insufficient_funds`, `expired_instrument`, `network_error`,
`abandonment`, `mandate_failure`).

---

## The LLM proposer — reproducible & safe

The LLM plugs into the `diagnose`/`propose` seam and **only** there. One
structured tool-use call per episode, temperature 0, wrapped in a disk
record-replay cache so runs are deterministic and free after the first pass.

```mermaid
sequenceDiagram
  participant O as orchestrator
  participant L as LLMProposer
  participant C as disk cache
  participant M as Groq / Anthropic
  O->>L: diagnose(episode)
  L->>C: lookup(sha256 of episode signals)
  alt cache hit
    C-->>L: cached JSON
  else miss, key present
    L->>M: one tool-use call (temp 0)
    M-->>L: typed Diagnosis + Action
    L->>C: write-through
  else miss / off-enum / error / no key
    L-->>O: log fault row, return heuristic
  end
  L-->>O: Diagnosis + ProposedAction  (untrusted → gate)
```

Backends are picked from the environment: **Groq** first (free tier,
`openai/gpt-oss-120b`, `temperature=0`), then **Anthropic**
(`claude-opus-5`), else fully offline (cache/fixtures only). Set a key via
`export GROQ_API_KEY=...` or a line in `.env`; override the model with
`RECOVERY_OS_LLM_MODEL=<id>`.

---

## Measurement — incremental, not raw

`batch.py` runs the unchanged loop over a **seeded, stratified** treatment/control
holdout. `scorecard.py` reports **incremental lift** (treatment recovery − control
self-recovery), with 95% confidence intervals, false-effort cost, and
gate-blocked episodes excluded from the lift denominator. Raw recovery is shown
but explicitly labeled secondary. Self-recovery rates are tunable, cited
assumptions in `config.py`.

```mermaid
flowchart LR
  N["N seeded episodes"] --> S{"stratified split<br/>by cause"}
  S -->|treatment| T["real intervention"]
  S -->|control| K["do_nothing<br/>self-recovery only"]
  T --> R["treatment recovery %"]
  K --> B["baseline recovery %"]
  R --> LIFT["incremental lift = T − B<br/>+ 95% CI · incr ₹"]
  B --> LIFT
```

Four policies run the same batch for comparison: `agent` (cause-aware),
`immediate` (retry-always), `fixed_schedule` (dunning), `never` (pure
self-recovery baseline).

---

## Ask — RAG Q&A over the ledger (read-only)

Retrieval is **deterministic**: each episode is rebuilt into a typed
`EpisodeView` and filtered over real fields. The LLM is the language layer only —
it translates the question to a `LedgerFilter` and narrates the retrieved rows,
citing episode ids. No row → no claim; invented citations are dropped.

```mermaid
flowchart LR
  Q["NL question"] --> TR{translate}
  TR -->|LLM| FTR["LedgerFilter"]
  TR -->|"fault → fallback"| KW["keyword filter"] --> FTR
  LED[("ledger")] --> VW["build_views"] --> QRY["deterministic query"]
  FTR --> QRY --> ROWS["cited rows"]
  ROWS --> NAR{narrate}
  NAR -->|LLM| A1["prose + cited ids<br/>(subset-checked)"]
  NAR -->|fallback| A2["template answer"]
```

It never writes to the ledger and never changes a Phase 0–3 contract.

---

## Dashboard — a read-only audit console

`dashboard/` is a React + Vite + TypeScript viewer over three committed JSON
exports. **It contains no recovery logic, makes no live calls, and never mutates
anything** — it renders what the backend produced. Three views: the pipeline
trace (with the LLM fault visibly breaking the audit spine), the incremental
scorecard, and the saved Ask answers (toggle between the offline template
narrator and Groq over the *same* cited rows).

`export.py` is the only bridge — a read-only reshaper of a seeded ledger +
scorecard into `src/data/*.json`. It optionally uses a Groq key from `.env` to
add the second Ask narration; the trace/scorecard numbers are fixed by the seed.

---

## Install & run (backend)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

```bash
recovery-os run pay_ABC123                 # one episode (heuristic)
recovery-os run pay_ABC123 --proposer llm  # one episode (LLM, cache-first)
recovery-os batch --n 100 --seed 42 --compare        # agent vs 3 baselines
recovery-os batch --n 50  --seed 42 --db batch.db    # build a ledger to query
recovery-os ask "list every gate-blocked action and the rule that blocked it" --db batch.db
```

## Run the dashboard (offline)

```bash
cd dashboard && npm install     # once
npm run dev -- --open           # http://localhost:5173
```

Refresh the data it shows (with the dev server running, it hot-reloads):

```bash
python dashboard/export.py      # regenerates dashboard/src/data/*.json
```

## Test

```bash
pytest        # 37 tests, fully offline — no API key, no network
```

Covers the signing round-trip + tamper detection, append-only ledger, the gate
blocking an over-ceiling action, batch reproducibility + incremental math, the
LLM proposer (parse / fallback / gate-block) replayed from committed fixtures,
and the RAG query/translate/narrate paths.

## Config

Copy `.env.example` to `.env`. Modeling knobs (self-recovery rates, amount range,
dunning attempts) live in `recovery_os/config.py`, labeled as tunable
assumptions. Keys (`GROQ_API_KEY` / `ANTHROPIC_API_KEY`) are read from `.env` by
the CLI.

## Layout

```
recovery_os/  config · domain · signing · policy · providers · ledger ·
              orchestrator · batch · scorecard · llm · rag · cli · api
tests/        signing · ledger · policy · orchestrator · batch · llm · rag (+ fixtures)
dashboard/    read-only React viewer + export.py (src/data/*.json)
```

See [CLAUDE.md](CLAUDE.md) for the full invariants, and
[dashboard/README.md](dashboard/README.md) for the viewer.
