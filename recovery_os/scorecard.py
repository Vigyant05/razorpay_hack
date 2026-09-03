"""Honest, incremental scorecard for a batch run.

Headline number is INCREMENTAL recovery — lift over a self-recovery holdout —
not raw recovery. Raw is shown but labeled non-headline. Pure functions over
typed RunReports; no I/O, no randomness.
"""

from __future__ import annotations

from pydantic import BaseModel

from .config import ASSUMPTIONS_NOTE, SELF_RECOVERY
from .domain import ExecStatus, FailureCause, Intervention, PolicyStatus, RunReport


class CauseStat(BaseModel):
    cause: FailureCause
    n_treatment: int
    n_control: int
    treatment_recovery_rate: float
    control_recovery_rate: float  # self-recovery baseline
    incremental_lift: float  # treatment − control
    incremental_amount_paise: int  # counterfactual-adjusted ₹


class ExceptionItem(BaseModel):
    episode_id: str
    cause: FailureCause
    reason: str


class Scorecard(BaseModel):
    policy: str
    seed: int
    control_frac: float
    n_episodes: int
    n_treatment: int
    n_control: int
    # HEADLINE — incremental
    incremental_recovery_rate: float
    incremental_amount_paise: int
    # non-headline (shown, labeled)
    raw_recovery_rate: float
    false_effort_actions: int
    false_effort_amount_paise: int
    per_cause: list[CauseStat]
    exceptions: list[ExceptionItem]
    assumptions: dict[str, float]
    assumptions_note: str


class Comparison(BaseModel):
    seed: int
    n_episodes: int
    control_frac: float
    scorecards: list[Scorecard]


def _rate(recovered: int, n: int) -> float:
    return recovered / n if n else 0.0


def build(
    policy: str,
    seed: int,
    control_frac: float,
    reports: list[RunReport],
    control_ids: set[str],
) -> Scorecard:
    treatment = [r for r in reports if r.episode_id not in control_ids]
    control = [r for r in reports if r.episode_id in control_ids]

    per_cause: list[CauseStat] = []
    incremental_amount_total = 0
    for cause in FailureCause:
        t = [r for r in treatment if r.cause is cause]
        c = [r for r in control if r.cause is cause]
        if not t and not c:
            continue
        t_rate = _rate(sum(r.recovered for r in t), len(t))
        c_rate = _rate(sum(r.recovered for r in c), len(c))
        t_recovered_amount = sum(r.amount for r in t if r.recovered)
        t_total_amount = sum(r.amount for r in t)
        # counterfactual-adjusted ₹: actual treatment recovery minus what the
        # self-recovery baseline (control rate) would have recovered anyway.
        inc_amount = round(t_recovered_amount - c_rate * t_total_amount)
        incremental_amount_total += inc_amount
        per_cause.append(CauseStat(
            cause=cause, n_treatment=len(t), n_control=len(c),
            treatment_recovery_rate=t_rate, control_recovery_rate=c_rate,
            incremental_lift=t_rate - c_rate, incremental_amount_paise=inc_amount,
        ))

    overall_t_rate = _rate(sum(r.recovered for r in treatment), len(treatment))
    overall_c_rate = _rate(sum(r.recovered for r in control), len(control))

    # false effort: wasted retry actions and the ₹ of treated-but-unrecovered
    # episodes where an action actually fired (control's do_nothing costs nothing).
    acted = [r for r in treatment
             if r.executed is not None and r.intervention is not Intervention.do_nothing]
    false_effort_actions = sum(r.wasted_actions for r in treatment)
    false_effort_amount = sum(r.amount for r in acted if not r.recovered)

    exceptions = [
        ExceptionItem(
            episode_id=r.episode_id, cause=r.cause,
            reason=(f"gate_blocked: {r.rule_fired}" if r.policy_status is PolicyStatus.blocked
                    else "do_nothing (agent declined)"),
        )
        for r in treatment
        if r.policy_status is PolicyStatus.blocked or r.intervention is Intervention.do_nothing
    ]

    return Scorecard(
        policy=policy, seed=seed, control_frac=control_frac,
        n_episodes=len(reports), n_treatment=len(treatment), n_control=len(control),
        incremental_recovery_rate=overall_t_rate - overall_c_rate,
        incremental_amount_paise=incremental_amount_total,
        raw_recovery_rate=_rate(sum(r.recovered for r in reports), len(reports)),
        false_effort_actions=false_effort_actions,
        false_effort_amount_paise=false_effort_amount,
        per_cause=per_cause, exceptions=exceptions,
        assumptions={c.value: rate for c, rate in SELF_RECOVERY.items()},
        assumptions_note=ASSUMPTIONS_NOTE,
    )


# --- rendering ---------------------------------------------------------------

def _rupees(paise: int) -> str:
    return f"₹{paise / 100:,.2f}"


def to_table(s: Scorecard) -> str:
    L = [
        f"SCORECARD — policy={s.policy}  seed={s.seed}  n={s.n_episodes}"
        f"  (treatment {s.n_treatment} / control {s.n_control}, control_frac {s.control_frac})",
        "",
        f"  HEADLINE  incremental recovery : {s.incremental_recovery_rate:+.1%}"
        f"   incremental recovered : {_rupees(s.incremental_amount_paise)}",
        f"  (non-headline) raw recovery    : {s.raw_recovery_rate:.1%}",
        f"  false effort : {s.false_effort_actions} wasted actions,"
        f" {_rupees(s.false_effort_amount_paise)} on acted-but-unrecovered",
        "",
        "  per cause             n_t  n_c   treat%   self%    lift    incr ₹",
        "  " + "-" * 62,
    ]
    for c in s.per_cause:
        L.append(
            f"  {c.cause.value:<20} {c.n_treatment:>4} {c.n_control:>4}"
            f"  {c.treatment_recovery_rate:>6.0%}  {c.control_recovery_rate:>6.0%}"
            f"  {c.incremental_lift:>+6.0%}  {_rupees(c.incremental_amount_paise):>10}"
        )
    if s.exceptions:
        L.append(f"\n  exceptions ({len(s.exceptions)}): "
                 + ", ".join(f"{e.episode_id}[{e.reason}]" for e in s.exceptions[:5])
                 + (" …" if len(s.exceptions) > 5 else ""))
    L.append(f"\n  assumptions: {s.assumptions}")
    L.append(f"  note: {s.assumptions_note}")
    return "\n".join(L)


def to_comparison_table(cmp: Comparison) -> str:
    L = [
        f"BASELINE COMPARISON — seed={cmp.seed}  n={cmp.n_episodes}"
        f"  control_frac={cmp.control_frac}",
        "",
        "  policy            incr recovery   incr ₹        raw    wasted   false ₹",
        "  " + "-" * 68,
    ]
    for s in cmp.scorecards:
        L.append(
            f"  {s.policy:<16} {s.incremental_recovery_rate:>+11.1%}"
            f"  {_rupees(s.incremental_amount_paise):>12}  {s.raw_recovery_rate:>5.0%}"
            f"  {s.false_effort_actions:>6}  {_rupees(s.false_effort_amount_paise):>9}"
        )
    L.append(f"\n  headline = incremental recovery (lift over self-recovery holdout).")
    return "\n".join(L)
