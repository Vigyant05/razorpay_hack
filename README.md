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
- **Groq** (free tier, OpenAI-compatible): `export GROQ_API_KEY=...` → uses
  `llama-3.3-70b-versatile` at `temperature=0`. Groq is picked first when its key
  is set. Override the model with `RECOVERY_OS_LLM_MODEL=<id>`.

The first `--proposer llm` run makes ~`n_treatment` calls and writes `llm_cache/`
(gitignored); every rerun is 0 calls and byte-identical. Regenerate the test
fixtures with `python tests/build_fixtures.py`.

## Config
Copy `.env.example` to `.env` and adjust. Modeling knobs (self-recovery rates,
amount range, dunning attempts) live in `recovery_os/config.py`, labeled as
tunable assumptions.

## Layout
```
recovery_os/  config · domain · signing · policy · providers · ledger ·
              orchestrator · batch · scorecard · llm · cli · api
tests/        signing · ledger · policy · orchestrator · batch · llm (+ fixtures)
```
