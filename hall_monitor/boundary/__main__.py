"""Serve the claims records server's tools over HTTP, behind the boundary, on this machine.

    uv run python -m hall_monitor.boundary serve                 # http://127.0.0.1:8770/mcp
    uv run python -m hall_monitor.boundary token --sub risk_fraud --case claim_004 \\
        --policy WTP-ESS-100231 --tools claims_history policy_lookup

Demo mode only: the issuer's key is derived from a public seed (demo_issuer_key), like the demo
adjusters' keys, so anyone can mint a token for it. A real deployment holds only the public key
of the firm's identity provider, which mints the tokens. Tool calls go through the claims
gateway on the synthetic records and into var/boundary/audit.sqlite. No model is called.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import time
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from hall_monitor.boundary.app import LOCAL_HOSTS, create_app
from hall_monitor.boundary.tokens import MAX_LIFETIME_S, issue

DEMO_ISSUER = "hall-monitor-demo-issuer"
DEMO_TENANT = "demo-insurer"
DEMO_SEED = b"hall-monitor demo token issuer, public by design, never use for real access: "
DEFAULT_PORT = 8770
DEFAULT_AUDIT = Path("var/boundary/audit.sqlite")


def demo_issuer_key() -> Ed25519PrivateKey:
    return Ed25519PrivateKey.from_private_bytes(hashlib.sha256(DEMO_SEED).digest())


def audience(port: int) -> str:
    return f"http://127.0.0.1:{port}/mcp"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m hall_monitor.boundary")
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve", help="serve the claims tools over HTTP (demo issuer)")
    serve.add_argument("--port", type=int, default=DEFAULT_PORT)
    serve.add_argument("--audit-db", type=Path, default=DEFAULT_AUDIT)
    token = sub.add_parser("token", help="mint a demo token")
    token.add_argument("--sub", required=True, help="the agent ID the token is for")
    token.add_argument("--case", required=True)
    token.add_argument("--policy", required=True)
    token.add_argument("--tools", nargs="+", required=True)
    token.add_argument("--port", type=int, default=DEFAULT_PORT)
    token.add_argument("--lifetime", type=int, default=120, help=f"at most {MAX_LIFETIME_S}")
    args = parser.parse_args(argv)

    if args.command == "token":
        print(
            issue(
                demo_issuer_key(),
                iss=DEMO_ISSUER,
                aud=audience(args.port),
                sub=args.sub,
                tools=args.tools,
                tenant=DEMO_TENANT,
                case=args.case,
                policy=args.policy,
                now=int(time.time()),
                lifetime_s=args.lifetime,
            )
        )
        return 0

    import uvicorn

    from hall_monitor.claims.gateway import claims_gateway
    from hall_monitor.governance.audit import AuditLog
    from hall_monitor.registry import Registry

    args.audit_db.parent.mkdir(parents=True, exist_ok=True)
    app = create_app(
        claims_gateway(Registry.from_dir()),
        audience=audience(args.port),
        tenant=DEMO_TENANT,
        issuers={DEMO_ISSUER: demo_issuer_key().public_key()},
        audit=AuditLog(args.audit_db),
    )
    print(f"serving {audience(args.port)} (demo issuer; hosts {', '.join(LOCAL_HOSTS)})")
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    sys.exit(main())
