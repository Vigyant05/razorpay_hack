"""Batch runner (phase 2). Sits ON TOP of the unchanged phase-1 loop.

Generates N seeded episodes, assigns a seeded treatment/control holdout, runs
each through `run_episode` (gate, signing, ledger all intact), and aggregates
into a Scorecard. Simulator-only: you cannot batch-replay real payments.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict

from .config import FIXED_SCHEDULE_ATTEMPTS
from .domain import Diagnosis, Episode, Intervention, ProposedAction
from .orchestrator import Proposer, propose, run_episode
from .policy import PolicyEngine
from .providers import SimulatedProvider
from .scorecard import Comparison, Scorecard, build


# --- policies (the strategy seam; all plug into run_episode's proposer) -------

def _retry(attempts: int) -> Proposer:
    def policy(diag: Diagnosis, ep: Episode) -> ProposedAction:
        return ProposedAction(
            episode_id=ep.episode_id, intervention=Intervention.smart_retry,
            params={"attempts": str(attempts)},
            rationale=f"blind retry x{attempts} regardless of cause",
        )
    return policy


def _never(diag: Diagnosis, ep: Episode) -> ProposedAction:
    return ProposedAction(
        episode_id=ep.episode_id, intervention=Intervention.do_nothing,
        rationale="never intervene; pure self-recovery",
    )


POLICIES: dict[str, Proposer] = {
    "agent": propose,                              # cause-aware heuristic (phase 1)
    "immediate": _retry(1),                        # retry-always, once
    "fixed_schedule": _retry(FIXED_SCHEDULE_ATTEMPTS),  # fixed-cadence dunning
    "never": _never,                               # pure self-recovery baseline
}
_CONTROL_PROPOSER = _never  # control arm always gets do_nothing


# --- holdout assignment ------------------------------------------------------

def _rank_key(seed: int, payment_id: str) -> str:
    return hashlib.sha256(f"{seed}:assign:{payment_id}".encode()).hexdigest()


def _control_ids(
    payment_ids: list[str], seed: int, control_frac: float, provider: SimulatedProvider
) -> set[str]:
    """Stratified holdout: assign control WITHIN each cause so treatment and
    control share the same cause mix. Seeded and reproducible."""
    by_cause: dict[object, list[str]] = defaultdict(list)
    for pid in payment_ids:
        by_cause[provider.peek_cause(pid)].append(pid)

    control: set[str] = set()
    for pids in by_cause.values():
        ordered = sorted(pids, key=lambda p: _rank_key(seed, p))
        k = round(control_frac * len(pids))
        control.update(f"ep_{p}" for p in ordered[:k])
    return control


# --- runner ------------------------------------------------------------------

def run_batch(
    n: int,
    seed: int,
    control_frac: float = 0.2,
    policy: str = "agent",
    proposer_kind: str = "heuristic",  # "heuristic" | "llm"
    db_path: str | None = None,
) -> Scorecard:
    if policy not in POLICIES:
        raise ValueError(f"unknown policy {policy!r}; choose one of {list(POLICIES)}")
    provider = SimulatedProvider(seed=seed)  # one instance holds all episode state
    engine = PolicyEngine()
    payment_ids = [f"pay_{seed}_{i}" for i in range(n)]
    control_ids = _control_ids(payment_ids, seed, control_frac, provider)
    treatment_proposer = POLICIES[policy]

    # The LLM drives diagnosis+proposal on TREATMENT episodes only (control does
    # nothing; baselines are deterministic) — that bounds the call count.
    llm = None
    if proposer_kind == "llm":
        if policy != "agent":
            raise ValueError("proposer_kind='llm' only applies to the agent policy")
        from .llm import LLMProposer
        llm = LLMProposer(db_path=db_path)

    reports = []
    for pid in payment_ids:
        is_control = f"ep_{pid}" in control_ids
        if is_control:
            diagnoser, proposer = None, _CONTROL_PROPOSER  # heuristic diagnose + do_nothing
        elif llm is not None:
            diagnoser, proposer = llm.diagnose, llm.propose
        else:
            diagnoser, proposer = None, treatment_proposer
        reports.append(
            run_episode(pid, provider=provider, engine=engine,
                        diagnoser=diagnoser, proposer=proposer, db_path=db_path)
        )
    return build(policy, seed, control_frac, reports, control_ids)


def run_compare(
    n: int, seed: int, control_frac: float = 0.2, db_path: str | None = None
) -> Comparison:
    """Run all four policies on the same seeded batch + holdout."""
    cards = [run_batch(n, seed, control_frac, p, db_path) for p in POLICIES]
    return Comparison(seed=seed, n_episodes=n, control_frac=control_frac, scorecards=cards)
