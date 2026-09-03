"""ed25519 signing — the tamper-evidence mechanism (invariant #4) and the
structural policy gate (invariant #1).

`issue_mandate` is the ONLY way to build a SignedMandate, and it refuses to sign
a blocked decision. `PaymentProvider.execute` requires a SignedMandate, so no
money-moving action can run without a passing PolicyDecision + a signature.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.exceptions import InvalidSignature

from .config import get_settings
from .domain import PolicyDecision, PolicyStatus, ProposedAction, SignedMandate


class PolicyViolation(Exception):
    """Raised on any attempt to sign an action the policy engine blocked."""


def _canonical(action: ProposedAction) -> bytes:
    """Stable byte payload for an action. Sorted keys -> same action, same bytes."""
    return action.model_dump_json().encode("utf-8")  # pydantic dumps field order-stably


def load_or_create_key(path: str | None = None) -> Ed25519PrivateKey:
    """Load the ed25519 private key from disk, generating (and saving) one if absent."""
    p = Path(path or get_settings().key_path)
    if p.exists():
        return serialization.load_pem_private_key(p.read_bytes(), password=None)  # type: ignore[return-value]
    key = Ed25519PrivateKey.generate()
    p.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    return key


def _public_hex(key: Ed25519PrivateKey) -> str:
    return key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    ).hex()


def issue_mandate(decision: PolicyDecision, key: Ed25519PrivateKey | None = None) -> SignedMandate:
    """Sign the decision's effective action. Refuses blocked decisions.

    This is the gate: the returned SignedMandate is the only key that opens
    `PaymentProvider.execute`.
    """
    if decision.status is PolicyStatus.blocked:
        raise PolicyViolation(f"cannot sign blocked action (rule: {decision.rule_fired})")

    key = key or load_or_create_key()
    action = decision.effective_action
    payload = _canonical(action)
    signature = key.sign(payload)
    return SignedMandate(
        action=action,
        decision=decision,
        payload_sha256=hashlib.sha256(payload).hexdigest(),
        signature=signature.hex(),
        public_key=_public_hex(key),
    )


def verify(mandate: SignedMandate) -> bool:
    """True iff the signature matches the action under the embedded public key."""
    payload = _canonical(mandate.action)
    if hashlib.sha256(payload).hexdigest() != mandate.payload_sha256:
        return False
    pub = Ed25519PublicKey.from_public_bytes(bytes.fromhex(mandate.public_key))
    try:
        pub.verify(bytes.fromhex(mandate.signature), payload)
        return True
    except InvalidSignature:
        return False
