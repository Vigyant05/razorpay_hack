"""Deterministic policy engine — the safety spine (invariant #1).

Non-LLM. Given a ProposedAction (which an LLM may have proposed) and its
Episode, returns a PolicyDecision. Rules run in order; the first that fires
decides. No rule fires -> approved.
"""

from __future__ import annotations

from collections.abc import Callable

from .config import Settings, get_settings
from .domain import (
    Episode,
    Intervention,
    PolicyDecision,
    PolicyStatus,
    ProposedAction,
)

# A rule inspects (action, episode, settings) and returns a PolicyDecision to
# stop with, or None to defer to the next rule.
Rule = Callable[[ProposedAction, Episode, Settings], PolicyDecision | None]

# Interventions that move money / hit the customer and thus need the gate.
_MONEY_MOVING = {Intervention.smart_retry, Intervention.method_switch, Intervention.mandate_reauth}


def _amount_ceiling(action: ProposedAction, ep: Episode, s: Settings) -> PolicyDecision | None:
    if action.intervention in _MONEY_MOVING and ep.amount > s.max_auto_amount:
        return PolicyDecision(
            status=PolicyStatus.blocked,
            rule_fired="amount_ceiling",
            reason=f"amount {ep.amount} > ceiling {s.max_auto_amount}",
            original=action,
        )
    return None


def _max_attempts(action: ProposedAction, ep: Episode, s: Settings) -> PolicyDecision | None:
    if action.intervention in _MONEY_MOVING and ep.attempt >= s.max_attempts:
        return PolicyDecision(
            status=PolicyStatus.blocked,
            rule_fired="max_attempts",
            reason=f"attempt {ep.attempt} >= max {s.max_attempts}",
            original=action,
        )
    return None


def _escalation_is_terminal(action: ProposedAction, ep: Episode, s: Settings) -> PolicyDecision | None:
    # human_escalation and do_nothing never move money; always allow.
    if action.intervention in {Intervention.human_escalation, Intervention.do_nothing}:
        return PolicyDecision(
            status=PolicyStatus.approved,
            rule_fired="escalation_is_terminal",
            reason="non-money-moving action",
            original=action,
        )
    return None


DEFAULT_RULES: list[Rule] = [_amount_ceiling, _max_attempts, _escalation_is_terminal]


class PolicyEngine:
    def __init__(self, rules: list[Rule] | None = None, settings: Settings | None = None) -> None:
        self.rules = rules if rules is not None else DEFAULT_RULES
        self.settings = settings or get_settings()

    def decide(self, action: ProposedAction, episode: Episode) -> PolicyDecision:
        for rule in self.rules:
            decision = rule(action, episode, self.settings)
            if decision is not None:
                return decision
        return PolicyDecision(
            status=PolicyStatus.approved,
            rule_fired=None,
            reason="no rule fired; within bounds",
            original=action,
        )
