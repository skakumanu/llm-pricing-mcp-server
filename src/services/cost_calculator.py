"""Shared token-cost arithmetic for the REST / MCP-tool pairs that duplicate it.

Single implementation for the cost math behind:
  - POST /cost-estimate         <-> mcp/tools/estimate_cost.py
  - POST /cost-estimate/batch   <-> mcp/tools/compare_costs.py
  - POST /usage                 <-> mcp/tools/record_usage.py

Deliberately takes raw per-token rates and token counts rather than a
PricingMetrics object, keeping it decoupled from the pricing models and
trivially unit-testable. Returns unrounded floats — callers keep doing their
own rounding/formatting exactly as before, so no call site's output shape
changes.
"""
from dataclasses import dataclass

# v1.82.0: cap on models per comparison (POST /cost-estimate/batch and the
# compare_costs MCP tool). Both are public; without a cap one request could ask
# for an unbounded list and do a catalogue lookup per entry.
MAX_COMPARE_MODELS = 50


@dataclass(frozen=True)
class TokenCost:
    """Unrounded cost breakdown for one model's token usage."""

    input_cost: float
    output_cost: float
    total_cost: float


def compute_token_cost(
    cost_per_input_token: float,
    cost_per_output_token: float,
    input_tokens: int,
    output_tokens: int,
) -> TokenCost:
    """
    Compute input/output/total cost for a given token usage at given per-token rates.

    Args:
        cost_per_input_token: Dollars per single input token.
        cost_per_output_token: Dollars per single output token.
        input_tokens: Number of input tokens.
        output_tokens: Number of output tokens.

    Returns:
        TokenCost with unrounded input_cost, output_cost, and total_cost.
    """
    input_cost = cost_per_input_token * input_tokens
    output_cost = cost_per_output_token * output_tokens
    total_cost = input_cost + output_cost
    return TokenCost(input_cost=input_cost, output_cost=output_cost, total_cost=total_cost)
