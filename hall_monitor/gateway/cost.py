"""Cost of a model call, cache-aware. Used for every claim, not just in evals."""

from __future__ import annotations

from collections.abc import Iterable

from hall_monitor.budgets import SpendRefused, approved_budgets
from hall_monitor.registry import APPROVALS_FILENAME, BUDGETS_FILENAME, DEFAULT_REGISTRY_DIR
from hall_monitor.shared_models import AgentCall

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


def _price(model: str) -> tuple[float, float]:
    if model not in PRICES_PER_MTOK:
        raise UnpricedModelError(f"no price for model {model!r} in PRICES_PER_MTOK")
    return PRICES_PER_MTOK[model]


def _cost(
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_creation_input_tokens: int = 0,
    cache_read_input_tokens: int = 0,
) -> float:
    """input_tokens counts only uncached input; cached reads and writes are priced separately.
    Uses the prices as last read; the public functions below refresh them first."""
    input_price, output_price = _price(model)
    cents = (
        input_tokens * input_price
        + cache_creation_input_tokens * input_price * CACHE_WRITE_MULTIPLIER
        + cache_read_input_tokens * input_price * CACHE_READ_MULTIPLIER
        + output_tokens * output_price
    )
    return cents / 1_000_000


def _of(call: AgentCall) -> float:
    return _cost(
        call.model,
        call.input_tokens,
        call.output_tokens,
        call.cache_creation_input_tokens,
        call.cache_read_input_tokens,
    )


def require_price(model: str) -> None:
    _refresh()  # prices from the approved budgets.yaml, read again when it changes (HM-34)
    _price(model)


def usage_cost(
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_creation_input_tokens: int = 0,
    cache_read_input_tokens: int = 0,
) -> float:
    _refresh()
    return _cost(
        model, input_tokens, output_tokens, cache_creation_input_tokens, cache_read_input_tokens
    )


def call_cost(call: AgentCall) -> float:
    _refresh()
    return _of(call)


def total_cost(calls: Iterable[AgentCall]) -> float:
    """A case's cost: the prices are refreshed once, not once per call (the pipelines sum
    every call before each model request)."""
    _refresh()
    return sum(_of(call) for call in calls)


def cost_by_agent(calls: list[AgentCall]) -> dict[str, float]:
    _refresh()
    totals: dict[str, float] = {}
    for call in calls:
        totals[call.agent_id] = totals.get(call.agent_id, 0.0) + _of(call)
    return totals
