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
5. **Reproducibility.** All randomness from `config.rng()` (seeded). Any future LLM
   calls at temperature 0. Same seed -> identical results.

## Conventions
- **Money is `int` minor units (paise). Never a float.**
- No untyped dicts across module boundaries — pass pydantic models.
- Small, single-responsibility modules. Simple over clever.
- Ask before adding scope/deps.

## Module map
`config` settings+RNG · `domain` enums+models · `signing` gate+ed25519 ·
`policy` rules · `providers` adapter · `ledger` audit store · `cli`/`api` entry stubs.

## Domain vocabulary
Failure causes: issuer_downtime, insufficient_funds, expired_instrument,
network_error, abandonment, mandate_failure.
Interventions: smart_retry, method_switch, mandate_reauth, customer_nudge,
human_escalation, do_nothing.

## Deliberately NOT built yet (stubs only)
LLM diagnosis/decision, simulator failure generation, batch runner, scorecard/
incrementality math, RAG, nudges, dispute-defense, real Razorpay calls.
