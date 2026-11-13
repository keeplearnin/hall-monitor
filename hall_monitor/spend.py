"""The spend ledger: daily and monthly caps per workflow, held with reservations (HM-34).

Before a case's first model call, start_case reserves the case's whole budget (its max_cost_usd)
against its workflow's daily and monthly caps. If that would pass either cap, the case is refused
with no model call, and the pipeline sends it to a person. When the case ends, finish_case
settles the reservation at what the case actually cost, which frees the rest.

The ledger is a SQLite file (var/spend.sqlite by default) shared by every process on the machine:
a reservation is taken inside BEGIN IMMEDIATE, so two processes can't both take the last of a
cap. A reservation a crash leaves open keeps counting at its full amount (conservative); a cost
that can't be priced is left reserved the same way. Days and months are UTC.

The ledger holds workflow names, case IDs, amounts and times: nothing from the case itself.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from hall_monitor.budgets import (
    Budgets,
    CaseBudget,
    SpendRefused,
    Workflow,
    approved_budgets,
)
from hall_monitor.registry import DEFAULT_REGISTRY_DIR

DEFAULT_SPEND_DB = Path("var/spend.sqlite")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS spend (
    id INTEGER PRIMARY KEY,
    workflow TEXT NOT NULL,
    case_id TEXT NOT NULL,
    day TEXT NOT NULL,
    month TEXT NOT NULL,
    reserved_usd REAL NOT NULL,
    actual_usd REAL,
    opened_at TEXT NOT NULL,
    settled_at TEXT
);
CREATE INDEX IF NOT EXISTS spend_day ON spend (workflow, day);
CREATE INDEX IF NOT EXISTS spend_month ON spend (workflow, month);
"""


class SpendCapReached(SpendRefused):
    pass


class SpendLedgerUnavailable(SpendRefused):
    """The ledger couldn't be read or written (locked past its timeout, a disk error): the cap
    can't be checked, so the case is refused like one over the cap, blaming no agent."""


@dataclass(frozen=True)
class Reservation:
    id: int
    workflow: Workflow
    case_id: str
    reserved_usd: float


@dataclass(frozen=True)
class CaseStart:
    """What a case may spend, and the reservation holding it (None without a ledger)."""

    budget: CaseBudget
    reservation: Reservation | None


class SpendLedger:
    def __init__(self, path: Path = DEFAULT_SPEND_DB, clock: Callable[[], datetime] | None = None):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._clock = clock or (lambda: datetime.now(UTC))
        with self._connect() as db:
            db.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        db.execute("PRAGMA busy_timeout = 30000")
        return db

    def reserve(self, budgets: Budgets, workflow: Workflow, case_id: str) -> Reservation:
        """Hold the case's budget, or raise SpendCapReached if that would pass a cap."""
        limits = budgets.workflows[workflow]
        amount = limits.case.max_cost_usd
        now = self._clock().astimezone(UTC)
        day, month = now.strftime("%Y-%m-%d"), now.strftime("%Y-%m")
        db = self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            for period, key, cap in (
                ("day", day, limits.daily_usd),
                ("month", month, limits.monthly_usd),
            ):
                (committed,) = db.execute(
                    f"SELECT COALESCE(SUM(COALESCE(actual_usd, reserved_usd)), 0) FROM spend "
                    f"WHERE workflow = ? AND {period} = ?",
                    (workflow, key),
                ).fetchone()
                if committed + amount > cap + 1e-9:
                    db.execute("ROLLBACK")
                    label = "daily" if period == "day" else "monthly"
                    raise SpendCapReached(
                        f"spend cap reached: {label} {workflow} cap of {cap:.2f} USD"
                    )
            cursor = db.execute(
                "INSERT INTO spend (workflow, case_id, day, month, reserved_usd, opened_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (workflow, case_id, day, month, amount, now.isoformat()),
            )
            db.execute("COMMIT")
            return Reservation(cursor.lastrowid, workflow, case_id, amount)
        except BaseException:
            if db.in_transaction:
                db.execute("ROLLBACK")
            raise
        finally:
            db.close()

    def settle(self, reservation: Reservation, actual_usd: float | None) -> None:
        """Record what the case cost. None (a cost that can't be priced) leaves it reserved."""
        if actual_usd is None:
            return
        with self._connect() as db:
            db.execute(
                "UPDATE spend SET actual_usd = ?, settled_at = ? "
                "WHERE id = ? AND actual_usd IS NULL",
                (actual_usd, self._clock().astimezone(UTC).isoformat(), reservation.id),
            )

    def committed(self, workflow: Workflow, *, day: str | None = None, month: str | None = None):
        """Settled cost plus open reservations for a workflow's day or month (UTC)."""
        period, key = ("day", day) if day is not None else ("month", month)
        with self._connect() as db:
            (total,) = db.execute(
                f"SELECT COALESCE(SUM(COALESCE(actual_usd, reserved_usd)), 0) FROM spend "
                f"WHERE workflow = ? AND {period} = ?",
                (workflow, key),
            ).fetchone()
        return round(total, 6)


def start_case(
    workflow: Workflow,
    case_id: str,
    spend: SpendLedger | None,
    registry_dir: Path = DEFAULT_REGISTRY_DIR,
) -> CaseStart:
    """Before a case's first model call: its approved budget, and a reservation against its
    workflow's caps when a ledger is given. Raises SpendRefused (budgets not approved, or a cap
    reached); the pipelines send such a case to a person with no model call."""
    budgets = approved_budgets(registry_dir)
    reservation = None
    if spend is not None:
        try:
            reservation = spend.reserve(budgets, workflow, case_id)
        except SpendRefused:
            raise
        except Exception as exc:  # the type only: a fixed template
            raise SpendLedgerUnavailable(
                f"spend ledger unavailable ({type(exc).__name__})"
            ) from None
    return CaseStart(budgets.workflows[workflow].case, reservation)


def finish_case(
    spend: SpendLedger | None, start: CaseStart | None, actual_usd: float | None
) -> None:
    """Settle a case's reservation. The case has already been routed, so a ledger that fails
    here doesn't fail the case: its reservation stays counted in full (conservative)."""
    if spend is not None and start is not None and start.reservation is not None:
        try:
            spend.settle(start.reservation, actual_usd)
        except Exception:
            pass
