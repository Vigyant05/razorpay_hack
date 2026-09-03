"""The recovery run loop (phase 1).

Wires one failed payment through: detect -> diagnose -> propose -> policy gate
-> sign -> execute -> verify -> attribute, writing one immutable ledger row per
step. The money path routes through the gate by construction (invariant #1): the
only way to `execute` is a `SignedMandate`, and the only source of one is
`issue_mandate`, which we call only on a non-blocked decision.

`diagnose` and `propose` are deterministic heuristics. They are the seam where
the LLM proposer lands in a later phase — same signatures, temperature 0. The
deterministic policy engine keeps disposing regardless.
"""

from __future__ import annotations

from collections.abc import Callable

from . import ledger, signing
from .domain import (
    ERROR_CODES,
    Attribution,
    Diagnosis,
    Episode,
    ExecStatus,
    FailureCause,
    Intervention,
    LedgerStep,
    PolicyStatus,
    ProposedAction,
    RunReport,
)
from .policy import PolicyEngine
from .providers import PaymentProvider, get_provider

# The strategy seams: diagnosis and proposal. The LLM (or a heuristic) plugs in
# here; the deterministic gate/sign/execute path downstream is identical either way.
Diagnoser = Callable[[Episode], Diagnosis]
Proposer = Callable[[Diagnosis, Episode], ProposedAction]

_CAUSE_BY_CODE = {code: cause for cause, code in ERROR_CODES.items()}

# The agent's belief about which intervention fits which cause. Deliberately
# separate from the simulator's ground truth — the agent can be wrong.
_PLAYBOOK: dict[FailureCause, Intervention] = {
    FailureCause.issuer_downtime: Intervention.smart_retry,
    FailureCause.network_error: Intervention.smart_retry,
    FailureCause.insufficient_funds: Intervention.customer_nudge,
    FailureCause.expired_instrument: Intervention.method_switch,
    FailureCause.abandonment: Intervention.customer_nudge,
    FailureCause.mandate_failure: Intervention.mandate_reauth,
}


def diagnose(episode: Episode) -> Diagnosis:
    """Map the provider's error code to a cause. LLM replaces this later."""
    cause = _CAUSE_BY_CODE.get(episode.raw_error_code or "")
    if cause is not None:
        return Diagnosis(
            episode_id=episode.episode_id, cause=cause, confidence=0.9,
            rationale=f"error code {episode.raw_error_code!r} maps to {cause.value}",
        )
    return Diagnosis(
        episode_id=episode.episode_id, cause=FailureCause.network_error, confidence=0.4,
        rationale="unrecognised error code; defaulting to network_error",
    )


def propose(diagnosis: Diagnosis, episode: Episode) -> ProposedAction:
    """Pick an intervention from the playbook. LLM proposer replaces this later."""
    intervention = _PLAYBOOK.get(diagnosis.cause, Intervention.human_escalation)
    return ProposedAction(
        episode_id=episode.episode_id, intervention=intervention,
        rationale=f"playbook: {diagnosis.cause.value} -> {intervention.value}",
    )


def _attribute(episode: Episode, action: ProposedAction, recovered: bool) -> Attribution:
    # ponytail: naive attribution (assume 0% baseline). Real incrementality math
    # is the scorecard phase; upgrade path is a control arm / would-recover-anyway rate.
    return Attribution(
        episode_id=episode.episode_id, intervention=action.intervention, recovered=recovered,
        counterfactual="assume 0% recovery without action",
        incremental=recovered,
        note="naive attribution; incrementality math lands in the scorecard phase",
    )


def run_episode(
    payment_id: str,
    provider: PaymentProvider | None = None,
    engine: PolicyEngine | None = None,
    diagnoser: Diagnoser | None = None,
    proposer: Proposer | None = None,
    db_path: str | None = None,
) -> RunReport:
    """Run the full recovery loop for one failed payment. Reproducible per seed.

    `diagnoser`/`proposer` are the strategy seams (invariant #1): swap in the LLM
    or a baseline policy. Both default to the cause-aware heuristics. The LLM only
    proposes here — the gate/sign/execute path below is identical for any source.
    """
    provider = provider or get_provider()  # one instance: holds the episode's state
    engine = engine or PolicyEngine()
    diagnoser = diagnoser or diagnose
    proposer = proposer or propose

    episode = provider.fetch_payment(payment_id)
    ledger.append(episode.episode_id, LedgerStep.episode, episode, db_path=db_path)

    diagnosis = diagnoser(episode)
    ledger.append(episode.episode_id, LedgerStep.diagnosis, diagnosis, db_path=db_path)

    action = proposer(diagnosis, episode)
    ledger.append(episode.episode_id, LedgerStep.proposal, action, db_path=db_path)

    decision = engine.decide(action, episode)
    ledger.append(episode.episode_id, LedgerStep.policy, decision, db_path=db_path)

    if decision.status is PolicyStatus.blocked:
        # No mandate is issued -> execute() is unreachable. Terminal for phase 1;
        # re-proposal / auto-escalation on block is a later phase.
        return RunReport(
            episode_id=episode.episode_id, cause=diagnosis.cause,
            intervention=action.intervention, amount=episode.amount,
            policy_status=decision.status, rule_fired=decision.rule_fired,
            executed=None, recovered=False,
        )

    mandate = signing.issue_mandate(decision)
    ledger.append(episode.episode_id, LedgerStep.mandate, mandate,
                  signature=mandate.signature, db_path=db_path)

    result = provider.execute(mandate)
    ledger.append(episode.episode_id, LedgerStep.execution, result, db_path=db_path)

    verification = provider.verify(episode.episode_id)
    ledger.append(episode.episode_id, LedgerStep.verification, verification, db_path=db_path)

    attribution = _attribute(episode, decision.effective_action, verification.recovered)
    ledger.append(episode.episode_id, LedgerStep.attribution, attribution, db_path=db_path)

    return RunReport(
        episode_id=episode.episode_id, cause=diagnosis.cause,
        intervention=decision.effective_action.intervention, amount=episode.amount,
        policy_status=decision.status, rule_fired=decision.rule_fired,
        executed=result.status, recovered=verification.recovered,
        wasted_actions=result.wasted_actions,
    )
