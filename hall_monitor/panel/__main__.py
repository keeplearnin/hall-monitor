"""The control panel: read-only pages and JSON over the governance core.

    uv run python -m hall_monitor.panel serve                    # http://127.0.0.1:8765
    uv run python -m hall_monitor.panel demo                     # build demo data in var/demo/
    uv run python -m hall_monitor.panel serve --demo             # serve the demo data
    uv run python -m hall_monitor.panel snapshot --demo --out panel-snapshot

`snapshot` saves every page and its JSON as static files, for publishing or for a record of
what the panel showed at a moment. The panel has no sign-in yet, so it serves this machine only.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from starlette.testclient import TestClient

from hall_monitor.panel import demo, views
from hall_monitor.panel.app import LOCAL_HOSTS, create_app

DEFAULT_PORT = 8765


def _sources(args: argparse.Namespace) -> views.Sources:
    if args.demo:
        if not (demo.DEFAULT_OUT / "audit.sqlite").exists():
            raise SystemExit("no demo data: run `python -m hall_monitor.panel demo` first")
        return views.Sources(
            registry_dir=demo.DEFAULT_OUT / "registry",
            audit_db=demo.DEFAULT_OUT / "audit.sqlite",
            demo=True,
        )
    return views.Sources(audit_db=args.audit_db) if args.audit_db else views.Sources()


def _loopback(host: str) -> bool:
    """Only the addresses the host check answers to (app.LOCAL_HOSTS): IPv4 loopback."""
    return host in LOCAL_HOSTS


def snapshot(src: views.Sources, out: Path) -> list[str]:
    """Save every page and its JSON under `out`, at the paths the server uses. A case whose ID
    can't safely be a file name gets no page; its row says so."""
    client = TestClient(create_app(src), base_url="http://localhost")
    paths = [
        "panel.css",
        "index.html",
        "agents.html",
        "queue.html",
        "cases.html",
        "audit.html",
        "evals.html",
        "controls.html",
        *(f"api/{name}.json" for name in ("overview", "agents", "queue", "cases", "audit")),
        *(f"api/{name}.json" for name in ("evals", "controls")),
    ]
    for agent in views.agents(src)["agents"]:
        paths += [f"agents/{agent['id']}.html", f"api/agents/{agent['id']}.json"]
    saved = [c["claim_id"] for c in views.cases(src) if c["linkable"]]
    if len({c.lower() for c in saved}) != len(saved):
        # One would overwrite the other on a case-insensitive disk (the macOS default).
        raise ValueError("claim IDs that differ only in case can't be saved as separate pages")
    for case in views.cases(src):
        if case["linkable"]:
            paths += [f"cases/{case['claim_id']}.html", f"api/cases/{case['claim_id']}.json"]
    root = out.resolve()
    for path in paths:
        target = (out / path).resolve()
        if root not in target.parents:  # only IDs that match SAFE_ID reach here; belt and braces
            raise ValueError(f"refusing to write {path} outside {out}")
        response = client.get("/" + path)
        response.raise_for_status()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(response.content)
    return paths


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m hall_monitor.panel")
    commands = parser.add_subparsers(dest="command", required=True)
    serve_cmd = commands.add_parser("serve", help="serve the panel on this machine")
    serve_cmd.add_argument("--host", default="127.0.0.1")
    serve_cmd.add_argument("--port", type=int, default=DEFAULT_PORT)
    snap_cmd = commands.add_parser("snapshot", help="save every page as static files")
    snap_cmd.add_argument("--out", type=Path, required=True)
    for cmd in (serve_cmd, snap_cmd):
        cmd.add_argument("--demo", action="store_true", help="read the demo data in var/demo/")
        cmd.add_argument("--audit-db", type=Path, help="audit log to read (default var/)")
    commands.add_parser("demo", help="build demo data in var/demo/ with a scripted model")
    args = parser.parse_args(argv)

    if args.command == "demo":
        print(json.dumps(demo.build(), indent=2))
        return 0
    src = _sources(args)
    if args.command == "snapshot":
        paths = snapshot(src, args.out)
        print(f"saved {len(paths)} files to {args.out}")
        return 0
    if not _loopback(args.host):
        print(
            f"refusing to serve on {args.host}: the panel has no sign-in yet, so it serves this "
            "machine only",
            file=sys.stderr,
        )
        return 2
    import uvicorn

    uvicorn.run(create_app(src), host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    sys.exit(main())
