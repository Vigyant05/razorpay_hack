# Recovery OS

A bounded, self-auditing agent that handles **failed payments** end to end.
Razorpay buildathon, Track 3 (AI Revenue Recovery). **This is the foundation
phase** — skeleton, typed domain models, and the safety spine. The full
detect → diagnose → propose → gate → sign → execute → verify → attribute loop
is built on top of this in later phases.

## Design guarantees
- **LLM proposes, deterministic engine disposes** — money-moving actions run only
  after a non-LLM policy gate, enforced by the type system (you cannot call a
  provider's `execute` without a policy-passed, signed mandate).
- **Append-only audit ledger** — every step is one immutable SQLite row.
- **Signed mandates** — ed25519 signatures make the money path tamper-evident.
- **Swappable providers** — simulated vs. real Razorpay test, identical interface.
- **Reproducible** — all randomness seeded.

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

Smoke tests cover: signing round-trip + tamper detection, ledger write/read,
and the policy gate blocking an over-ceiling action (and that a blocked action
cannot be signed into a mandate).

## Run (stubs)

```bash
recovery-os info            # prints active config; run loop not implemented yet
uvicorn recovery_os.api:app # FastAPI app with a /health endpoint
```

## Config
Copy `.env.example` to `.env` and adjust. All values have defaults; see the file
for the money ceiling, seed, DB path, and active provider.

## Layout
```
recovery_os/  config · domain · signing · policy · providers · ledger · cli · api
tests/        test_signing · test_ledger · test_policy
```
