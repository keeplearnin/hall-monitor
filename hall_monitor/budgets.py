"""Approved spending limits and model prices (HM-34, ADR 0030).

registry/budgets.yaml holds each workflow's case budget (steps, cost, seconds), its daily and
monthly spending caps and owner, and the price table every cost is computed from. It is approved
like a hall pass: `python -m hall_monitor.approvals` records its digest in approvals.yaml, and
approved_budgets() refuses a file whose digest isn't there. The pipelines read it at the start of
every case, so a reviewed change takes effect without a restart and an unreviewed edit sends
every case to a person.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, PositiveFloat, PositiveInt

from hall_monitor.registry import (
    APPROVALS_FILENAME,
    BUDGETS_FILENAME,
    DEFAULT_REGISTRY_DIR,
    OWNERS_FILENAME,
    load_owners,
)

Workflow = Literal["claims", "complaints", "leave"]
WORKFLOWS: tuple[Workflow, ...] = ("claims", "complaints", "leave")


class SpendRefused(Exception):
    """A case refused before any model call: its workflow's budgets aren't approved, or the
    case would pass a spending cap. The message is a fixed template (no free text)."""


class BudgetsNotApproved(SpendRefused):
    pass


class ModelPrice(BaseModel):
    model_config = ConfigDict(extra="forbid")
    input: float = Field(ge=0)
    output: float = Field(ge=0)


class Prices(BaseModel):
    model_config = ConfigDict(extra="forbid")
    as_of: str
    per_mtok_usd: dict[str, ModelPrice]
    cache_write_multiplier: float = Field(ge=1)
    cache_read_multiplier: float = Field(ge=0, le=1)


class CaseBudget(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_steps: PositiveInt
    max_cost_usd: PositiveFloat
    max_seconds: PositiveFloat


class WorkflowBudget(BaseModel):
    model_config = ConfigDict(extra="forbid")
    owner: str
    case: CaseBudget
    daily_usd: PositiveFloat
    monthly_usd: PositiveFloat


class Budgets(BaseModel):
    model_config = ConfigDict(extra="forbid")
    prices: Prices
    workflows: dict[Workflow, WorkflowBudget]

    def digest(self) -> str:
        """Hash of the canonical content, so comments and layout don't count as a change."""
        canonical = json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode()).hexdigest()

    def check(self, owners: frozenset[str]) -> None:
        missing = [w for w in WORKFLOWS if w not in self.workflows]
        if missing:
            raise ValueError(f"{BUDGETS_FILENAME}: no budget for {', '.join(missing)}")
        for name, budget in self.workflows.items():
            if budget.owner not in owners:
                raise ValueError(
                    f"{BUDGETS_FILENAME}: {name}'s owner {budget.owner!r} is not a person listed "
                    f"in {OWNERS_FILENAME}"
                )
            if budget.daily_usd > budget.monthly_usd:
                raise ValueError(f"{BUDGETS_FILENAME}: {name}'s daily cap is above its monthly")
            if budget.case.max_cost_usd > budget.daily_usd:
                raise ValueError(f"{BUDGETS_FILENAME}: {name}'s case budget is above its daily cap")


def load_budgets(path: Path = DEFAULT_REGISTRY_DIR / BUDGETS_FILENAME) -> Budgets:
    budgets = Budgets.model_validate(yaml.safe_load(path.read_text()))
    budgets.check(frozenset(p["id"] for p in load_owners(path.parent / OWNERS_FILENAME)))
    return budgets


def approved_budget_digest(registry_dir: Path = DEFAULT_REGISTRY_DIR) -> str | None:
    approvals = yaml.safe_load((registry_dir / APPROVALS_FILENAME).read_text()) or {}
    return (approvals.get("budgets") or {}).get("digest")


def approved_budgets(registry_dir: Path = DEFAULT_REGISTRY_DIR) -> Budgets:
    """The budgets file, if its digest is the approved one; otherwise BudgetsNotApproved. A file
    that can't be read or doesn't validate is refused the same way."""
    try:
        budgets = load_budgets(registry_dir / BUDGETS_FILENAME)
    except Exception as exc:
        raise BudgetsNotApproved(
            f"spend budgets not approved: {BUDGETS_FILENAME} can't be read ({type(exc).__name__})"
        ) from None
    if budgets.digest() != approved_budget_digest(registry_dir):
        raise BudgetsNotApproved(
            f"spend budgets not approved: {BUDGETS_FILENAME} is not the approved version"
        )
    return budgets
