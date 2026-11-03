"""Token prices for cost per claim. Real-time gateway cost tracking comes in milestone 7."""

from __future__ import annotations

from hall_monitor.models import AgentCall

# USD per million tokens (input, output), Anthropic list prices as of 2026-10. Haiku 5.5's rate
# is for prompts up to 100K tokens; ours are far below that. Output includes thinking tokens.
PRICES_PER_MTOK: dict[str, tuple[float, float]] = {
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-sonnet-5-5": (2.00, 10.00),
}


def call_cost(call: AgentCall) -> float:
    input_price, output_price = PRICES_PER_MTOK[call.model]
    return (call.input_tokens * input_price + call.output_tokens * output_price) / 1_000_000
