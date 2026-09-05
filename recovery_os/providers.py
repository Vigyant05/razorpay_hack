"""Payment provider adapter (invariant #2).

One Protocol, two swappable backends with identical signatures: a seeded
SimulatedProvider and RazorpayTestProvider, which makes real calls against
Razorpay TEST MODE. Core logic never learns which is active — it calls
`get_provider()`.

`execute` takes a SignedMandate, not a raw action: the only way to move money is
through a signed, policy-passed mandate (invariant #1).
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import random
import urllib.error
import urllib.request
from typing import Protocol

from . import ledger
from .config import (
    AMOUNT_MAX_PAISE,
    AMOUNT_MIN_PAISE,
    SELF_RECOVERY,
    get_settings,
    ssl_context,
)
from .domain import (
    ERROR_CODES,
    DiagnosisFault,
    Episode,
    ExecStatus,
    ExecutionResult,
    FailureCause,
    Intervention,
    LedgerStep,
    SignedMandate,
    VerificationResult,
)
from .signing import verify


class PaymentProvider(Protocol):
    name: str

    def fetch_payment(self, payment_id: str) -> Episode: ...
    def execute(self, mandate: SignedMandate) -> ExecutionResult: ...
    def verify(self, episode_id: str) -> VerificationResult: ...


# The intervention that actually works for each cause. Match -> high recovery
# odds; mismatch -> low. This is the simulator's ground truth, not the agent's
# knowledge (the agent must diagnose + choose and can be wrong).
_BEST_FIX: dict[FailureCause, Intervention] = {
    FailureCause.issuer_downtime: Intervention.smart_retry,
    FailureCause.network_error: Intervention.smart_retry,
    FailureCause.insufficient_funds: Intervention.customer_nudge,
    FailureCause.expired_instrument: Intervention.method_switch,
    FailureCause.abandonment: Intervention.customer_nudge,
    FailureCause.mandate_failure: Intervention.mandate_reauth,
}
_MATCH_RATE = 0.75
_MISMATCH_RATE = 0.15


class _GatedProvider:
    """Shared base: refuses any mandate whose signature doesn't verify.

    A second, defence-in-depth check on top of the type-level gate — even a
    valid SignedMandate object is rejected here if it's been tampered with.
    """

    name = "base"

    def _guard(self, mandate: SignedMandate) -> None:
        if not verify(mandate):
            raise PermissionError("mandate signature invalid; refusing to execute")


class SimulatedProvider(_GatedProvider):
    """Seeded fake failures for reproducible runs (invariant #5).

    Deterministic in (seed, payment_id): the same call always yields the same
    episode and the same execute/verify outcome. Ground-truth cause and the
    recovery outcome are held in memory per episode, keyed off `fetch_payment`.
    """

    name = "simulated"

    def __init__(self, seed: int | None = None) -> None:
        self._seed = seed if seed is not None else get_settings().seed
        self._cause: dict[str, FailureCause] = {}
        self._recovered: dict[str, bool] = {}

    def _seeded(self, salt: str) -> random.Random:
        h = hashlib.sha256(f"{self._seed}:{salt}".encode()).digest()
        return random.Random(int.from_bytes(h[:8], "big"))

    def _self_recovers(self, episode_id: str, cause: FailureCause) -> bool:
        """Latent, seeded draw: would this failure have recovered with no action?"""
        return self._seeded(f"self:{episode_id}").random() < SELF_RECOVERY[cause]

    def peek_cause(self, payment_id: str) -> FailureCause:
        """The cause fetch_payment will assign — same seeded draw, no state/ledger
        write. Lets the batch runner stratify the holdout by cause up front."""
        return self._seeded(f"fetch:{payment_id}").choice(list(FailureCause))

    def fetch_payment(self, payment_id: str) -> Episode:
        r = self._seeded(f"fetch:{payment_id}")
        cause = r.choice(list(FailureCause))
        episode_id = f"ep_{payment_id}"
        self._cause[episode_id] = cause
        return Episode(
            episode_id=episode_id,
            payment_id=payment_id,
            customer_id=f"cust_{r.randrange(1000, 9999)}",
            amount=r.randrange(AMOUNT_MIN_PAISE, AMOUNT_MAX_PAISE, 100),
            method=r.choice(["card", "upi", "netbanking"]),
            raw_error_code=ERROR_CODES[cause],
            attempt=1,
        )

    def execute(self, mandate: SignedMandate) -> ExecutionResult:
        self._guard(mandate)
        eid = mandate.action.episode_id
        cause = self._cause[eid]  # KeyError if execute precedes fetch — intended
        iv = mandate.action.intervention
        self_recovered = self._self_recovers(eid, cause)

        if iv is Intervention.do_nothing:
            # No action fired; outcome is pure self-recovery (this is the control arm).
            recovered, wasted = self_recovered, 0
            status = ExecStatus.success
            detail = f"no-op; self-recovery={'yes' if self_recovered else 'no'}"
        elif iv is Intervention.human_escalation:
            recovered, wasted = False, 0
            status = ExecStatus.pending
            detail = "handed to human queue"
        else:
            # Fire up to `attempts` seeded retry draws; stop on the first hit.
            attempts = max(1, int(mandate.action.params.get("attempts", "1")))
            rate = _MATCH_RATE if _BEST_FIX[cause] is iv else _MISMATCH_RATE
            r = self._seeded(f"exec:{eid}:{iv.value}")
            hit, fired = False, 0
            for _ in range(attempts):
                fired += 1
                if r.random() < rate:
                    hit = True
                    break
            wasted = fired - (1 if hit else 0)  # non-hit attempts are wasted effort
            recovered = self_recovered or hit
            status = ExecStatus.success if recovered else ExecStatus.failed
            via = "intervention" if hit else ("self-recovery" if self_recovered else "none")
            detail = f"{iv.value} vs {cause.value}: {fired} attempt(s), recovered via {via}"

        self._recovered[eid] = recovered
        return ExecutionResult(
            episode_id=eid, signature=mandate.signature, status=status,
            provider=self.name, detail=detail, wasted_actions=wasted,
        )

    def verify(self, episode_id: str) -> VerificationResult:
        recovered = self._recovered.get(episode_id, False)
        return VerificationResult(
            episode_id=episode_id, recovered=recovered,
            detail="payment settled" if recovered else "still unpaid",
        )


class RazorpayError(Exception):
    """A non-2xx / unparseable response from the Razorpay API."""


class _RazorpayHTTP:
    """Minimal Basic-auth JSON client. stdlib only — a handful of endpoints
    doesn't earn an SDK dependency."""

    BASE = "https://api.razorpay.com/v1"

    def __init__(self, key_id: str, key_secret: str, timeout: int = 30) -> None:
        token = base64.b64encode(f"{key_id}:{key_secret}".encode()).decode()
        self._ssl = ssl_context()
        self._headers = {
            "Authorization": f"Basic {token}",
            "Content-Type": "application/json",
            "User-Agent": "recovery-os/0.1",
        }
        self._timeout = timeout

    def request(self, method: str, path: str, body: dict | None = None) -> dict:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(
            f"{self.BASE}{path}", data=data, method=method, headers=self._headers)
        try:
            with urllib.request.urlopen(req, timeout=self._timeout, context=self._ssl) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            raw = e.read().decode()[:400]
            try:
                err = json.loads(raw)["error"]
                desc = f"{err.get('code')}: {err.get('description')}"
            except Exception:
                desc = raw
            raise RazorpayError(f"{method} {path} -> {e.code} {desc}") from None
        except (urllib.error.URLError, TimeoutError, ValueError) as e:
            raise RazorpayError(f"{method} {path} -> {type(e).__name__}: {e}") from None


def _cause_from_error(code: str | None, reason: str | None, description: str | None) -> FailureCause:
    """Map a real Razorpay payment error onto our cause vocabulary.

    Razorpay's error taxonomy is coarser and wordier than ours, so we match on the
    joined blob rather than pinning exact codes that Razorpay is free to rename.
    """
    blob = " ".join(x for x in (reason, code, description) if x).lower()
    for needle, cause in (
        ("insufficient", FailureCause.insufficient_funds),
        ("expire", FailureCause.expired_instrument),
        ("mandate", FailureCause.mandate_failure),
        ("token", FailureCause.mandate_failure),
        ("timeout", FailureCause.network_error),
        ("abandon", FailureCause.abandonment),
        ("gateway", FailureCause.issuer_downtime),
        ("issuer", FailureCause.issuer_downtime),
        ("downtime", FailureCause.issuer_downtime),
    ):
        if needle in blob:
            return cause
    return FailureCause.network_error  # same fallback the heuristic diagnoser uses


class RazorpayTestProvider(_GatedProvider):
    """Real Razorpay TEST-MODE API behind the same Protocol as SimulatedProvider.

    Real calls: create order, create payment link, fetch payment, fetch payment
    link, create refund. What test mode *cannot* do is summon issuer downtime or
    an insufficient-funds decline on demand — so `smart_retry` / `mandate_reauth`
    (which need a real re-charge against a saved instrument) log a fault and
    return `failed` rather than pretending. Failure injection stays on the
    SimulatedProvider; that split is the design, not a gap.

    Every API error is caught, logged as a `LedgerStep.fault` row (the same
    mechanism phase 3 uses for LLM faults) and turned into a failed/unrecovered
    result — a bad response never crashes a run.
    """

    name = "razorpay_test"

    def __init__(self, amount: int = 5_000, db_path: str | None = None) -> None:
        key_id = os.getenv("RAZORPAY_KEY_ID", "").strip()
        key_secret = os.getenv("RAZORPAY_KEY_SECRET", "").strip()
        if not key_id or not key_secret:
            raise RuntimeError(
                "RazorpayTestProvider needs RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET "
                "(put them in .env — gitignored — or the environment)")
        if not key_id.startswith("rzp_test_"):
            raise RuntimeError(
                f"refusing to run: {key_id[:12]}… is not a test key. This provider is "
                "test mode only; a live key would move real money.")
        self._http = _RazorpayHTTP(key_id, key_secret)
        self._amount = amount  # origination amount, paise
        self._db_path = db_path
        self._order: dict[str, str] = {}    # episode_id -> originating order id
        self._link: dict[str, str] = {}     # episode_id -> payment link id, if one was minted
        self._payment: dict[str, str] = {}  # episode_id -> payment id, once known

    # --- the four (+1) real calls -------------------------------------------

    def _create_order(self, receipt: str, amount: int) -> dict:
        return self._http.request("POST", "/orders", {
            "amount": amount, "currency": "INR", "receipt": receipt[:40],
            "notes": {"source": "recovery-os"},
        })

    def _create_link(self, description: str, amount: int, notes: dict[str, str]) -> dict:
        return self._http.request("POST", "/payment_links", {
            "amount": amount, "currency": "INR", "description": description[:2048],
            # notify off: this is a demo, we are not SMS-ing anyone from test mode.
            "notify": {"sms": False, "email": False},
            "reminder_enable": False,
            "notes": notes,
        })

    def _fetch_payment(self, payment_id: str) -> dict:
        return self._http.request("GET", f"/payments/{payment_id}")

    def _fetch_link(self, link_id: str) -> dict:
        return self._http.request("GET", f"/payment_links/{link_id}")

    def _fetch_order(self, order_id: str) -> dict:
        return self._http.request("GET", f"/orders/{order_id}")

    def _order_payments(self, order_id: str) -> list[dict]:
        return self._http.request("GET", f"/orders/{order_id}/payments").get("items", [])

    def refund(self, payment_id: str, amount: int | None = None) -> dict:
        """Create a real refund. Deliberately NOT on the PaymentProvider Protocol:
        the recovery loop never refunds, and adding it would force a simulator twin
        and give core logic something to branch on (invariant #2)."""
        body: dict[str, object] = {"speed": "optimum"}
        if amount is not None:
            body["amount"] = amount
        return self._http.request("POST", f"/payments/{payment_id}/refund", body)

    # --- fault logging -------------------------------------------------------

    def _fault(self, episode_id: str, reason: str, excerpt: str) -> None:
        ledger.append(
            episode_id, LedgerStep.fault,
            DiagnosisFault(episode_id=episode_id, reason=reason,
                           raw_excerpt=excerpt, fell_back_to="none"),
            db_path=self._db_path)

    # --- Protocol ------------------------------------------------------------

    def fetch_payment(self, payment_id: str) -> Episode:
        """A real Razorpay episode, two ways.

        `pay_…` -> fetch that real payment and diagnose from its real error fields.
        Anything else -> originate one with a real order. Orders are uncapped in
        test mode; payment links are NOT (30 per business, for the life of the
        account), so no link is minted here — only later, if the gate approves an
        intervention that needs one.
        """
        episode_id = f"ep_{payment_id}"

        if payment_id.startswith("pay_"):
            p = self._fetch_payment(payment_id)
            self._payment[episode_id] = payment_id
            cause = _cause_from_error(
                p.get("error_code"), p.get("error_reason"), p.get("error_description"))
            return Episode(
                episode_id=episode_id, payment_id=payment_id,
                customer_id=str(p.get("customer_id") or p.get("email") or "cust_unknown"),
                amount=int(p["amount"]), currency=p.get("currency", "INR"),
                method=str(p.get("method") or "unknown"),
                raw_error_code=ERROR_CODES[cause] if p.get("status") == "failed" else None,
            )

        order = self._create_order(receipt=payment_id, amount=self._amount)
        self._order[episode_id] = order["id"]
        return Episode(
            episode_id=episode_id, payment_id=order["id"], customer_id=f"cust_{payment_id}",
            amount=int(order["amount"]), currency=order.get("currency", "INR"),
            method="order",
            # a real order sitting at status=created with attempts=0 IS an abandoned
            # checkout — our real ground truth here, not a stubbed-in cause
            raw_error_code=ERROR_CODES[FailureCause.abandonment],
        )

    def execute(self, mandate: SignedMandate) -> ExecutionResult:
        self._guard(mandate)
        eid = mandate.action.episode_id
        iv = mandate.action.intervention

        def result(status: ExecStatus, detail: str, wasted: int = 0) -> ExecutionResult:
            return ExecutionResult(episode_id=eid, signature=mandate.signature,
                                   status=status, provider=self.name, detail=detail,
                                   wasted_actions=wasted)

        if iv is Intervention.do_nothing:
            return result(ExecStatus.success, "no-op; no Razorpay call made")
        if iv is Intervention.human_escalation:
            return result(ExecStatus.pending, "handed to human queue; no Razorpay call made")

        if iv in (Intervention.customer_nudge, Intervention.method_switch):
            try:
                # A nudge is a REMINDER about the still-unpaid link, so re-send that
                # one rather than minting a second. method_switch is the one that
                # needs a fresh link — a new way to pay. (Also halves our usage of
                # test mode's 30-payment-link cap.)
                existing = self._link.get(eid)
                if iv is Intervention.customer_nudge and existing:
                    link = self._fetch_link(existing)
                    if link.get("status") == "created":
                        return result(ExecStatus.success,
                                      f"customer_nudge: re-sent existing link "
                                      f"{link['id']} {link.get('short_url', '')}")
                    # already paid/expired/cancelled — a reminder is pointless, issue a new one
                link = self._create_link(  # the only capped call, and only post-gate
                    description=f"Recovery OS · {iv.value}", amount=self._amount,
                    notes={"source": "recovery-os", "intervention": iv.value,
                           "mandate": mandate.signature[:32], "stage": "recovery",
                           "order_id": self._order.get(eid, "")})
            except RazorpayError as e:
                self._fault(eid, "api_error", str(e))
                return result(ExecStatus.failed, f"razorpay error: {e}", wasted=1)
            self._link[eid] = link["id"]
            return result(ExecStatus.success,
                          f"{iv.value} sent: {link['id']} {link.get('short_url', '')}")

        # smart_retry / mandate_reauth: a real re-charge needs a saved token and a
        # real prior failure; test mode can produce neither. Fault, don't pretend.
        self._fault(eid, "unsupported_in_test_mode",
                    f"{iv.value} needs a real saved instrument; unavailable in test mode")
        return result(ExecStatus.failed,
                      f"{iv.value} unsupported in Razorpay test mode "
                      "(needs a real saved instrument); no call made", wasted=1)

    def verify(self, episode_id: str) -> VerificationResult:
        """Real ground truth: did Razorpay actually take the money?"""
        payment_id = self._payment.get(episode_id)
        link_id = self._link.get(episode_id)
        try:
            if link_id:
                link = self._fetch_link(link_id)
                payments = link.get("payments") or []
                if link.get("status") == "paid" and payments:
                    payment_id = payments[0]["payment_id"]
                else:
                    return VerificationResult(
                        episode_id=episode_id, recovered=False,
                        detail=f"payment link {link_id} status={link.get('status')}, "
                               f"amount_paid={link.get('amount_paid')}")
            elif not payment_id and (order_id := self._order.get(episode_id)):
                # no link was minted — the order itself is the ground truth
                order = self._fetch_order(order_id)
                paid = self._order_payments(order_id) if order.get("status") == "paid" else []
                if not paid:
                    return VerificationResult(
                        episode_id=episode_id, recovered=False,
                        detail=f"order {order_id} status={order.get('status')}, "
                               f"attempts={order.get('attempts')}, "
                               f"amount_paid={order.get('amount_paid')}")
                payment_id = paid[0]["id"]
            if not payment_id:
                return VerificationResult(episode_id=episode_id, recovered=False,
                                          detail="no payment or link to verify against")
            p = self._fetch_payment(payment_id)
            recovered = p.get("status") in ("captured", "authorized")
            self._payment[episode_id] = payment_id
            return VerificationResult(
                episode_id=episode_id, recovered=recovered,
                detail=f"payment {payment_id} status={p.get('status')}")
        except RazorpayError as e:
            self._fault(episode_id, "api_error", str(e))
            return VerificationResult(episode_id=episode_id, recovered=False,
                                      detail=f"razorpay error: {e}")


def get_provider(name: str | None = None) -> PaymentProvider:
    """Return the configured provider (or `name`, to override for one run).

    Constructed lazily: RazorpayTestProvider raises without test keys, and that
    must never break the simulated path.
    """
    name = name or get_settings().provider
    if name == "simulated":
        return SimulatedProvider()
    if name == "razorpay_test":
        return RazorpayTestProvider()
    raise ValueError(f"unknown provider {name!r}; choose one of ['simulated', 'razorpay_test']")
