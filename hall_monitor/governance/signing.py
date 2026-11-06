"""Adjuster signatures: a decision counts only if the adjuster's own key signed it.

Each adjuster in registry/adjusters.yaml has an Ed25519 public key. A decision is signed over
the claim ID and every decision field, evidence hash included, so a signature can't be moved to
another claim, another payout or other evidence. For a real adjuster the system would hold the
public key only, so it could check a decision but never make one.

The two fictional adjusters have demo keys derived from their IDs (demo_private_key), so the demo
runs end to end and anyone, this code included, can sign as them. They are public by
construction and marked `demo_key: true`; a real deployment issues keys from the firm's identity
system (a smart card, an HSM, or signing behind single sign-on).
"""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

DEMO_SEED = b"hall-monitor demo adjuster key, public by design, never use for real decisions: "


def decision_message(claim_id: str, fields: dict) -> bytes:
    """What is signed: the claim ID and the decision fields, signature excluded."""
    body = {k: v for k, v in fields.items() if k != "signature"} | {"claim_id": claim_id}
    return json.dumps(body, sort_keys=True, separators=(",", ":")).encode()


def demo_private_key(adjuster_id: str) -> Ed25519PrivateKey:
    seed = hashlib.sha256(DEMO_SEED + adjuster_id.encode()).digest()
    return Ed25519PrivateKey.from_private_bytes(seed)


def load_private_key(path: Path) -> Ed25519PrivateKey:
    key = serialization.load_pem_private_key(path.read_bytes(), password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise ValueError(f"{path} is not an Ed25519 private key")
    return key


def public_key_b64(key: Ed25519PrivateKey) -> str:
    raw = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return base64.b64encode(raw).decode()


def sign(message: bytes, key: Ed25519PrivateKey) -> str:
    return base64.b64encode(key.sign(message)).decode()


def verify(message: bytes, signature: str, public_key: str) -> bool:
    try:
        Ed25519PublicKey.from_public_bytes(base64.b64decode(public_key)).verify(
            base64.b64decode(signature, validate=True), message
        )
    except (InvalidSignature, ValueError):
        return False
    return True
