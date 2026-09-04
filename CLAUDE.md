# Recovery OS — invariants (read before touching money-path code)

Bounded, self-auditing agent for AI payment recovery. Razorpay buildathon, Track 3.

## The five invariants (structural, not aspirational)

1. **LLM proposes, deterministic engine disposes.** Money-moving actions run only
   after passing `policy.PolicyEngine`. Enforced by types: `PaymentProvider.execute`
   takes a `SignedMandate`; the only way to get one is `signing.issue_mandate`,
   which raises `PolicyViolation` on a blocked decision. There is no code path from
   a raw `ProposedAction` to `execute`. Keep it that way — never add an `execute`
   overload that takes an unsigned action.
2. **Provider adapter.** One `PaymentProvider` Protocol, two swappable backends
   (`SimulatedProvider`, `RazorpayTestProvider`), identical signatures. Core logic
   calls `providers.get_provider()` and never branches on which is active.
3. **Append-only ledger.** `ledger.py` exposes only `append` and `read`. Do not add
   update/delete. One row per run-step.
4. **Signed mandates.** ed25519. Signature + public key stored on the mandate and in
   the ledger. `signing.verify` proves tamper-evidence.
5. **Reproducibility.** All randomness from `config.rng()` (seeded); the simulator
   seeds per-episode. LLM calls go through a disk record-replay cache keyed by a
   hash of the episode signals, so same seed + warm cache -> identical results and
   zero API calls in CI.

## The proposer seam (Phase 3)
`orchestrator.run_episode` injects `diagnoser` + `proposer` (both default to the
heuristics). The LLM plugs in here and ONLY here — it outputs a typed `Diagnosis`
+ `ProposedAction` and never executes, signs, or bypasses the gate. Its output is
untrusted input to the same `PolicyDecision -> issue_mandate -> execute` path. Any
LLM fault (API error, cache miss, off-enum, malformed) logs a `LedgerStep.fault`
row and falls back to the heuristic — never crashes a batch, never skips the gate.
Backends are swappable via a `call_fn` (Anthropic default, Groq optional); adding
one must not add a path from a raw proposal to `execute`.

## Measurement (Phase 2)
`batch.py` runs the unchanged loop over a seeded, stratified treatment/control
holdout; `scorecard.py` reports INCREMENTAL lift over the self-recovery baseline
(not raw), with 95% CIs, false-effort, and gate-blocked exceptions excluded from
the lift denominator. Self-recovery rates + amount range are labeled tunable
assumptions in `config.py`. Do not headline raw recovery.

## Conventions
- **Money is `int` minor units (paise). Never a float.**
- No untyped dicts across module boundaries — pass pydantic models.
- Small, single-responsibility modules. Simple over clever.
- Ask before adding scope/deps.

## RAG Q&A (Phase 4) — READ-ONLY
`rag.py` answers NL questions over the ledger. Retrieval is deterministic: rebuild
each episode into a typed `EpisodeView`, filter over real fields. The LLM only
translates the question to a typed `LedgerFilter` and narrates the retrieved rows,
citing episode ids — no row, no claim; invented citations are dropped. Reuses the
`llm.py` `call_fn`/cache/fallback (keyword filter + template narrator offline). It
never writes to the ledger or changes any Phase 0–3 contract.

## Dashboard (`dashboard/`) — READ-ONLY viewer
React+Vite+TS app over three committed JSON exports (trace, scorecard, ask). No
recovery logic, no pipeline, no live calls — it renders what the backend produced.
`dashboard/export.py` reshapes a seeded ledger + scorecard into `src/data/*.json`
(deterministic; uses a Groq key from `.env` only to add a second Ask narration).
Do not put backend logic in the frontend.

## Module map
`config` settings+RNG+assumptions · `domain` enums+models · `signing` gate+ed25519 ·
`policy` rules · `providers` adapter+simulator · `ledger` audit store ·
`orchestrator` run loop + heuristic seams · `batch` runner+holdout ·
`scorecard` incremental math+tables · `llm` cached backend + proposer ·
`rag` read-only ledger Q&A · `cli`/`api` entries.

## Domain vocabulary
Failure causes: issuer_downtime, insufficient_funds, expired_instrument,
network_error, abandonment, mandate_failure.
Interventions: smart_retry, method_switch, mandate_reauth, customer_nudge,
human_escalation, do_nothing.

## Deliberately NOT built yet
Vernacular nudges, dispute-defense, real Razorpay API calls
(`RazorpayTestProvider` is still a stub).
