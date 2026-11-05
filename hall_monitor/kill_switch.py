"""The kill switch: stopping an agent needs no approval; restarting it does.

    uv run python -m hall_monitor.kill_switch stop intake --by own-301 --reason "wrong payouts"
    uv run python -m hall_monitor.kill_switch check-append-only BASE_FILE HEAD_FILE

A stop is an entry in ``registry/stops.yaml``. The registry reads that file on every call, so a
stop takes effect on the agent's next model or tool call without a restart, and adding one
changes no manifest or approval. The agent stays stopped until its entry in
``registry/approvals.yaml`` lists the stop under ``cleared_stops``, which is an approval and is
reviewed as one (``python -m hall_monitor.approvals --resume STOP_ID``). Stops are never removed:
CI checks that a pull request only appends to the file, so deleting a stop isn't a way round the
review.

This module only reads and writes the file; the registry decides what a stop means.
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

STOPS_FILENAME = "stops.yaml"
STOP_ID_PATTERN = r"^stop-\d{4,}$"

HEADER = (
    "# Agent stops: the kill switch (hall_monitor/kill_switch.py).\n"
    "# Add an entry to stop an agent; it takes effect on the agent's next call and needs no\n"
    "# approval. Never remove or edit an entry (CI checks this file is append-only). To restart\n"
    "# the agent, clear the stop in a reviewed approval:\n"
    "#   uv run python -m hall_monitor.approvals --resume STOP_ID\n"
)


class Stop(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=STOP_ID_PATTERN)
    agent: str = Field(min_length=1)
    stopped_by: str = Field(min_length=1, description="Owner ID from registry/owners.yaml.")
    stopped_at: datetime
    reason: str = Field(min_length=1, description="Kept in this file only, never in the log.")


class StopList(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    stops: tuple[Stop, ...]

    @model_validator(mode="after")
    def _unique_ids(self) -> StopList:
        ids = [s.id for s in self.stops]
        duplicates = sorted({i for i in ids if ids.count(i) > 1})
        if duplicates:
            raise ValueError(f"duplicate stop id: {', '.join(duplicates)}")
        return self


class AppendOnlyError(ValueError):
    pass


def load_stops(path: Path) -> StopList:
    """Parse the stop list. Any problem raises, so the caller can refuse every agent."""
    return StopList.model_validate(yaml.safe_load(path.read_text()))


def _raw(path: Path) -> list[dict]:
    return (yaml.safe_load(path.read_text()) or {}).get("stops") or []


def next_stop_id(path: Path) -> str:
    numbers = [int(m.group(1)) for s in _raw(path) if (m := re.match(r"stop-(\d+)$", s["id"]))]
    return f"stop-{max(numbers, default=0) + 1:04d}"


def add_stop(
    path: Path,
    *,
    agent: str,
    by: str,
    reason: str,
    stop_id: str | None = None,
    now: datetime | None = None,
) -> Stop:
    """Append a stop. Only appends: existing entries are written back unchanged."""
    existing = _raw(path)
    stop = Stop(
        id=stop_id or next_stop_id(path),
        agent=agent,
        stopped_by=by,
        stopped_at=now or datetime.now(UTC).replace(microsecond=0),
        reason=reason,
    )
    if any(s.get("id") == stop.id for s in existing):
        raise ValueError(f"stop id {stop.id} is already used")
    entries = [*existing, stop.model_dump(mode="json")]
    path.write_text(HEADER + yaml.safe_dump({"stops": entries}, sort_keys=False))
    return stop


def check_append_only(base: dict, head: dict) -> None:
    """Every stop in base must still be in head, unchanged."""
    head_by_id = {s.get("id"): s for s in (head or {}).get("stops") or []}
    changed = [
        s.get("id") for s in (base or {}).get("stops") or [] if head_by_id.get(s.get("id")) != s
    ]
    if changed:
        raise AppendOnlyError(
            f"stops removed or edited: {', '.join(changed)}. A stop is cleared in "
            "registry/approvals.yaml, never by changing registry/stops.yaml"
        )


def main(argv: list[str] | None = None) -> int:
    from hall_monitor.registry import DEFAULT_REGISTRY_DIR

    parser = argparse.ArgumentParser(prog="python -m hall_monitor.kill_switch")
    commands = parser.add_subparsers(dest="command", required=True)
    stop = commands.add_parser("stop", help="stop an agent (no approval needed)")
    stop.add_argument("agent")
    stop.add_argument("--by", required=True, help="your owner ID from registry/owners.yaml")
    stop.add_argument("--reason", required=True)
    stop.add_argument("--stops", type=Path, default=DEFAULT_REGISTRY_DIR / STOPS_FILENAME)
    check = commands.add_parser("check-append-only", help="fail if a stop was removed or edited")
    check.add_argument("base", type=Path)
    check.add_argument("head", type=Path)
    args = parser.parse_args(argv)

    if args.command == "stop":
        # A stop naming an unknown agent or person refuses every agent, so catch typos first.
        from hall_monitor.registry import OWNERS_FILENAME, Registry, load_owners

        registry_dir = args.stops.parent
        if args.agent not in {m.id for m in Registry.from_dir(registry_dir)}:
            parser.error(f"no agent {args.agent!r} in {registry_dir}")
        if args.by not in {p["id"] for p in load_owners(registry_dir / OWNERS_FILENAME)}:
            parser.error(f"{args.by!r} is not an owner ID in {OWNERS_FILENAME}")
        added = add_stop(args.stops, agent=args.agent, by=args.by, reason=args.reason)
        print(f"{added.id}: {added.agent} stopped from its next call")
        return 0
    try:
        check_append_only(
            yaml.safe_load(args.base.read_text()), yaml.safe_load(args.head.read_text())
        )
    except AppendOnlyError as exc:
        print(exc, file=sys.stderr)
        return 1
    print("stops.yaml: append-only")
    return 0


if __name__ == "__main__":
    sys.exit(main())
