"""Cost of a model call, cache-aware. Used for every claim, not just in evals."""

from __future__ import annotations

from hall_monitor.budgets import SpendRefused, approved_budgets
from hall_monitor.models import AgentCall
from hall_monitor.registry import APPROVALS_FILENAME, BUDGETS_FILENAME, DEFAULT_REGISTRY_DIR

_REGISTRY_DIR = DEFAULT_REGISTRY_DIR


def _approved_prices() -> tuple[dict[str, tuple[float, float]], float, float]:
    """The price table from the approved registry/budgets.yaml (HM-34). If that file isn't the
    approved version, no model has a price, so every case's cost is unknown and the pipelines
    send it to a person (an unpriced cost stops the agentic loops, and is a failure on every
    path)."""
    try:
        prices = approved_budgets(_REGISTRY_DIR).prices
    except SpendRefused:
        return {}, 1.25, 0.10
    table = {m: (p.input, p.output) for m, p in prices.per_mtok_usd.items()}
    return table, prices.cache_write_multiplier, prices.cache_read_multiplier


def _source() -> tuple | None:
    """What the prices were read from: the two files' change times and sizes."""
    try:
        return tuple(
            (s.st_mtime_ns, s.st_size)
            for s in (
                (_REGISTRY_DIR / BUDGETS_FILENAME).stat(),
                (_REGISTRY_DIR / APPROVALS_FILENAME).stat(),
            )
        )
    except OSError:
        return None


def _refresh() -> None:
    """Read the prices again when the budgets or approvals file has changed, so an approved
    price change reaches a long-running process (the forms, the panel) without a restart, and
    an unapproved edit unprices every model at once."""
    global CACHE_WRITE_MULTIPLIER, CACHE_READ_MULTIPLIER, _SOURCE
    source = _source()
    if source is not None and source == _SOURCE:
        return
    table, CACHE_WRITE_MULTIPLIER, CACHE_READ_MULTIPLIER = _approved_prices()
    PRICES_PER_MTOK.clear()
    PRICES_PER_MTOK.update(table)
    _SOURCE = source


# USD per million tokens (input, output). Haiku 5.5's rate is for prompts up to 100K tokens;
# ours are far below that. Output includes thinking tokens. Prompt caching (5-minute entries):
# writing costs CACHE_WRITE_MULTIPLIER times the input price, reading CACHE_READ_MULTIPLIER.
PRICES_PER_MTOK: dict[str, tuple[float, float]] = {}
CACHE_WRITE_MULTIPLIER = CACHE_READ_MULTIPLIER = 0.0
_SOURCE: tuple | None = None
_refresh()


class UnpricedModelError(ValueError):
    pass


def call_cost(call: AgentCall) -> float:
    """input_tokens counts only uncached input; cached reads and writes are priced separately."""
    _refresh()
    if call.model not in PRICES_PER_MTOK:
        raise UnpricedModelError(f"no price for model {call.model!r} in PRICES_PER_MTOK")
    input_price, output_price = PRICES_PER_MTOK[call.model]
    cents = (
        call.input_tokens * input_price
        + call.cache_creation_input_tokens * input_price * CACHE_WRITE_MULTIPLIER
        + call.cache_read_input_tokens * input_price * CACHE_READ_MULTIPLIER
        + call.output_tokens * output_price
    )
    return cents / 1_000_000


def cost_by_agent(calls: list[AgentCall]) -> dict[str, float]:
    totals: dict[str, float] = {}
    for call in calls:
        totals[call.agent_id] = totals.get(call.agent_id, 0.0) + call_cost(call)
    return totals
