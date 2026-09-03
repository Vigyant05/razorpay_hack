"""Batch runner (phase 2). Sits ON TOP of the unchanged phase-1 loop.

Generates N seeded episodes, assigns a seeded treatment/control holdout, runs
each through `run_episode` (gate, signing, ledger all intact), and aggregates
into a Scorecard. Simulator-only: you cannot batch-replay real payments.
"""

from __future__ import annotations

import hashlib

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

def _control_ids(n: int, seed: int, control_frac: float) -> set[str]:
    """Deterministic holdout: rank episodes by a seeded hash, take the first slice."""
    order = sorted(
        range(n),
        key=lambda i: hashlib.sha256(f"{seed}:assign:{i}".encode()).hexdigest(),
    )
    n_control = round(control_frac * n)
    return {f"ep_pay_{seed}_{i}" for i in order[:n_control]}


# --- runner ------------------------------------------------------------------

def run_batch(
    n: int,
    seed: int,
    control_frac: float = 0.2,
    policy: str = "agent",
    db_path: str | None = None,
) -> Scorecard:
    if policy not in POLICIES:
        raise ValueError(f"unknown policy {policy!r}; choose one of {list(POLICIES)}")
    provider = SimulatedProvider(seed=seed)  # one instance holds all episode state
    engine = PolicyEngine()
    control_ids = _control_ids(n, seed, control_frac)
    treatment_proposer = POLICIES[policy]

    reports = []
    for i in range(n):
        pid = f"pay_{seed}_{i}"
        is_control = f"ep_{pid}" in control_ids
        proposer = _CONTROL_PROPOSER if is_control else treatment_proposer
        reports.append(
            run_episode(pid, provider=provider, engine=engine,
                        proposer=proposer, db_path=db_path)
        )
    return build(policy, seed, control_frac, reports, control_ids)


def run_compare(
    n: int, seed: int, control_frac: float = 0.2, db_path: str | None = None
) -> Comparison:
    """Run all four policies on the same seeded batch + holdout."""
    cards = [run_batch(n, seed, control_frac, p, db_path) for p in POLICIES]
    return Comparison(seed=seed, n_episodes=n, control_frac=control_frac, scorecards=cards)
