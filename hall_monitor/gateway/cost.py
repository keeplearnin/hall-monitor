"""Cost of a model call, cache-aware. Used for every claim, not just in evals."""

from __future__ import annotations

from hall_monitor.models import AgentCall

# USD per million tokens (input, output), Anthropic list prices as of 2026-10. Haiku 5.5's rate
# is for prompts up to 100K tokens; ours are far below that. Output includes thinking tokens.
PRICES_PER_MTOK: dict[str, tuple[float, float]] = {
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-sonnet-5-5": (2.00, 10.00),
}
# Prompt caching (5-minute entries): writing costs 1.25x the input price, reading 0.1x.
CACHE_WRITE_MULTIPLIER = 1.25
CACHE_READ_MULTIPLIER = 0.10


def call_cost(call: AgentCall) -> float:
    """input_tokens counts only uncached input; cached reads and writes are priced separately."""
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
