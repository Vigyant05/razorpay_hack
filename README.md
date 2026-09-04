# Recovery OS

A bounded, self-auditing agent that handles **failed payments** end to end.
Razorpay buildathon, Track 3 (AI Revenue Recovery).

The loop, one immutable ledger row per step:

```
detect → diagnose → propose → policy gate → sign → execute → verify → attribute
```

## Design guarantees
- **LLM proposes, deterministic engine disposes** — money-moving actions run only
  after a non-LLM policy gate, enforced by the type system (you cannot call a
  provider's `execute` without a policy-passed, signed mandate). The LLM's output
  is untrusted input to the gate, nothing more.
- **Append-only audit ledger** — every step is one immutable SQLite row.
- **Signed mandates** — ed25519 signatures make the money path tamper-evident.
- **Swappable providers** — simulated vs. real Razorpay test, identical interface.
- **Reproducible** — all randomness seeded; LLM calls are cached (record-replay),
  so the same seed → byte-identical scorecard, with zero API calls in CI.

See [CLAUDE.md](CLAUDE.md) for the invariants in full.

## Install

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## Test

```bash
pytest
```

Runs fully offline (no API key): signing round-trip + tamper, ledger write/read,
the gate blocking an over-ceiling action, batch reproducibility + honest
incremental scoring, and the LLM proposer (parse, fallback, gate-block) replayed
from committed fixtures.

## Run one episode

```bash
recovery-os run pay_ABC123                 # heuristic diagnosis/proposal
recovery-os run pay_ABC123 --proposer llm  # LLM diagnosis/proposal (cache-first)
```

## Batch + honest scorecard

The headline is **incremental** recovery — lift over a self-recovery holdout —
not raw recovery.

```bash
recovery-os batch --n 100 --seed 42 --compare          # our agent vs 3 baselines
recovery-os batch --n 100 --seed 42 --policy agent      # one policy
recovery-os batch --n 100 --seed 42 --proposer llm      # LLM as the agent proposer
recovery-os batch --n 100 --seed 42 --out scorecard.json
```

Policies: `agent` (cause-aware), `immediate` (retry-always), `fixed_schedule`
(dunning), `never` (pure self-recovery baseline). Treatment/control is a
seeded, stratified holdout; the scorecard shows per-cause lift with 95% CIs,
false-effort cost, gate-blocked exceptions, and the self-recovery assumptions.

## The LLM proposer (Phase 3)

Claude proposes a typed `Diagnosis` + `ProposedAction` via one strict tool-use
call per episode; the deterministic gate still disposes. Everything is cached to
disk (keyed by a hash of the episode signals), so runs are reproducible and free
after the first pass. On any fault (API error, cache miss with no key, off-enum,
malformed) it logs a `fault` ledger row and falls back to the heuristic proposer —
a bad response never crashes a batch or skips the gate.

**Testing without an API key:** already the default — `pytest` and any warm-cache
run need no key. To record *real* model answers you need a backend once:

- **Anthropic** (default): `export ANTHROPIC_API_KEY=...` → uses `claude-opus-5`.
- **Groq** (free tier, OpenAI-compatible): `export GROQ_API_KEY=...` (or a line in
  `.env`) → uses `openai/gpt-oss-120b` at `temperature=0`. Groq is picked first
  when its key is set. Override the model with `RECOVERY_OS_LLM_MODEL=<id>` if that
  id is retired (list yours via `GET /openai/v1/models`).

The first `--proposer llm` run makes ~`n_treatment` calls and writes `llm_cache/`
(gitignored); every rerun is 0 calls and byte-identical. Regenerate the test
fixtures with `python tests/build_fixtures.py`.

## Ask — RAG Q&A over the ledger (Phase 4)

Natural-language questions over the append-only ledger. Retrieval is
**deterministic** (a typed filter over ledger fields); the LLM only translates the
question and narrates the retrieved rows, citing the exact episodes — no row, no
claim. On a cache-miss / off-enum / no-key it falls back to a keyword filter + a
template narrator, so it answers fully offline.

```bash
recovery-os batch --n 50 --seed 42 --db batch.db          # build a ledger to query
recovery-os ask "list every gate-blocked action and the rule that blocked it" --db batch.db
```

## Dashboard — read-only audit console

A React + Vite viewer over three committed JSON exports (trace, scorecard, ask).
No recovery logic, no live backend — it renders what the backend produced. Three
views: the pipeline trace (with the LLM fault breaking the audit spine), the
incremental scorecard, and the saved Ask answers (toggle between the offline
template narrator and Groq over the *same* cited rows).

```bash
cd dashboard && npm install        # once
npm run dev -- --open              # http://localhost:5173, fully offline
```

Regenerate the exports from the real backend (deterministic; add `GROQ_API_KEY`
to `.env` for the Groq narration): `python dashboard/export.py`. See
[dashboard/README.md](dashboard/README.md).

## Config
Copy `.env.example` to `.env` and adjust. Modeling knobs (self-recovery rates,
amount range, dunning attempts) live in `recovery_os/config.py`, labeled as
tunable assumptions.

## Layout
```
recovery_os/  config · domain · signing · policy · providers · ledger ·
              orchestrator · batch · scorecard · llm · rag · cli · api
tests/        signing · ledger · policy · orchestrator · batch · llm · rag (+ fixtures)
dashboard/    read-only React viewer + export.py (src/data/*.json)
```
