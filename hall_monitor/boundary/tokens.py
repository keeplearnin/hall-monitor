"""Bearer tokens for the HTTP boundary: compact JWS, Ed25519 only, every claim checked (RI-29).

A token says who is calling (sub: a registered agent), on whose behalf it was issued (iss: an
issuer whose public key the boundary holds), for which server (aud: the boundary's own resource
URL, as RFC 8707 resource indicators bind it), for how long (iat, exp, at most MAX_LIFETIME_S,
optional nbf), for which tenant (tnt), for which case and policy (case, policy: the gateway's
scope), with which tools (scope: "tools:<name>" entries), and once (jti, refused if seen again
while it is live).

Parsing is strict and refuses rather than guesses: the header must be exactly
{"alg": "EdDSA", "typ": "JWT"}, so "none", HMAC and key-in-header tricks are refused before any
key is used; base64url without padding only; JSON objects only; every listed claim present and
of its type. A refusal carries a fixed code, never the token or a claim's value, so it can go to
the audit log and back to the caller as is.
"""

from __future__ import annotations

import base64
import binascii
import json
import re
import secrets
from dataclasses import dataclass

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

HEADER = {"alg": "EdDSA", "typ": "JWT"}
MAX_TOKEN_BYTES = 4096
MAX_LIFETIME_S = 300  # a token lives five minutes at most
CLOCK_SKEW_S = 30
SCOPE_PREFIX = "tools:"
REQUIRED = {
    "iss": str,
    "sub": str,
    "aud": str,
    "iat": int,
    "exp": int,
    "jti": str,
    "tnt": str,
    "case": str,
    "policy": str,
    "scope": str,
}
OPTIONAL = {"nbf": int}
CODE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_B64URL = re.compile(r"^[A-Za-z0-9_-]+$")


class TokenRefused(Exception):
    """code is one of a fixed set, safe to log and to return."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class Claims:
    iss: str
    sub: str
    aud: str
    iat: int
    exp: int
    jti: str
    tnt: str
    case: str
    policy: str
    tools: frozenset[str]
    nbf: int | None = None


def _b64e(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64d(part: str) -> bytes:
    if not part or not _B64URL.fullmatch(part) or len(part) % 4 == 1:
        raise TokenRefused("malformed")
    try:
        return base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))
    except (binascii.Error, ValueError):
        raise TokenRefused("malformed") from None


def _json_object(part: str) -> dict:
    try:
        value = json.loads(_b64d(part))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise TokenRefused("malformed") from None
    if not isinstance(value, dict):
        raise TokenRefused("malformed")
    return value


def issue(
    key: Ed25519PrivateKey,
    *,
    iss: str,
    aud: str,
    sub: str,
    tools: list[str],
    tenant: str,
    case: str,
    policy: str,
    now: int,
    lifetime_s: int = 120,
    jti: str | None = None,
    extra: dict | None = None,
    header: dict | None = None,
) -> str:
    """A signed token. extra and header exist for tests that need a malformed one."""
    claims = {
        "iss": iss,
        "sub": sub,
        "aud": aud,
        "iat": now,
        "exp": now + lifetime_s,
        "jti": jti or secrets.token_hex(16),
        "tnt": tenant,
        "case": case,
        "policy": policy,
        "scope": " ".join(f"{SCOPE_PREFIX}{t}" for t in tools),
    } | (extra or {})
    head = _b64e(json.dumps(header or HEADER, separators=(",", ":")).encode())
    body = _b64e(json.dumps(claims, separators=(",", ":")).encode())
    signature = _b64e(key.sign(f"{head}.{body}".encode()))
    return f"{head}.{body}.{signature}"


def verify(
    token: str,
    *,
    issuers: dict[str, Ed25519PublicKey],
    audience: str,
    tenant: str,
    now: int,
) -> Claims:
    """The token's claims, if every check passes; TokenRefused(code) otherwise."""
    if not isinstance(token, str) or len(token) > MAX_TOKEN_BYTES or token.count(".") != 2:
        raise TokenRefused("malformed")
    head, body, signature = token.split(".")
    if _json_object(head) != HEADER:
        raise TokenRefused("bad_header")
    claims = _json_object(body)
    # The issuer names the key, so it is read before the signature is checked; nothing else is.
    key = issuers.get(claims.get("iss")) if isinstance(claims.get("iss"), str) else None
    if key is None:
        raise TokenRefused("unknown_issuer")
    try:
        key.verify(_b64d(signature), f"{head}.{body}".encode())
    except InvalidSignature:
        raise TokenRefused("bad_signature") from None
    for name, kind in (REQUIRED | OPTIONAL).items():
        if name not in claims:
            if name in REQUIRED:
                raise TokenRefused("missing_claim")
            continue
        value = claims[name]
        # bool is an int in Python; a claim of true is not a time.
        if not isinstance(value, kind) or isinstance(value, bool):
            raise TokenRefused("bad_claim")
    if claims["aud"] != audience:
        raise TokenRefused("wrong_audience")
    if claims["tnt"] != tenant:
        raise TokenRefused("wrong_tenant")
    if claims["exp"] - claims["iat"] > MAX_LIFETIME_S or claims["exp"] <= claims["iat"]:
        raise TokenRefused("bad_lifetime")
    if claims["iat"] > now + CLOCK_SKEW_S:
        raise TokenRefused("not_yet_valid")
    if "nbf" in claims and claims["nbf"] > now + CLOCK_SKEW_S:
        raise TokenRefused("not_yet_valid")
    if claims["exp"] <= now - CLOCK_SKEW_S:
        raise TokenRefused("expired")
    if not all(CODE.fullmatch(claims[c]) for c in ("sub", "jti", "case", "policy")):
        raise TokenRefused("bad_claim")
    entries = claims["scope"].split(" ")
    if not claims["scope"] or not all(
        e.startswith(SCOPE_PREFIX) and CODE.fullmatch(e[len(SCOPE_PREFIX) :]) for e in entries
    ):
        raise TokenRefused("bad_scope")
    return Claims(
        iss=claims["iss"],
        sub=claims["sub"],
        aud=claims["aud"],
        iat=claims["iat"],
        exp=claims["exp"],
        jti=claims["jti"],
        tnt=claims["tnt"],
        case=claims["case"],
        policy=claims["policy"],
        tools=frozenset(e[len(SCOPE_PREFIX) :] for e in entries),
        nbf=claims.get("nbf"),
    )
