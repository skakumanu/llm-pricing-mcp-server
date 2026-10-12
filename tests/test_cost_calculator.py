"""Tests for src.services.cost_calculator.compute_token_cost.

This is the single shared implementation behind the 3 confirmed duplicate
pairs: POST /cost-estimate <-> estimate_cost, POST /cost-estimate/batch <->
compare_costs, POST /usage <-> record_usage. It must be arithmetically
identical to what each site did inline before this change (cost_per_token *
token_count, summed), and must not round or otherwise reshape the numbers —
callers keep doing their own rounding exactly as before.
"""
from src.services.cost_calculator import TokenCost, compute_token_cost


def test_basic_multiplication():
    result = compute_token_cost(
        cost_per_input_token=0.000003,
        cost_per_output_token=0.000015,
        input_tokens=1000,
        output_tokens=500,
    )
    assert result.input_cost == 0.000003 * 1000
    assert result.output_cost == 0.000015 * 500
    assert result.total_cost == result.input_cost + result.output_cost


def test_returns_token_cost_dataclass():
    result = compute_token_cost(0.000003, 0.000015, 1000, 500)
    assert isinstance(result, TokenCost)


def test_zero_tokens_yields_zero_cost():
    result = compute_token_cost(0.000003, 0.000015, 0, 0)
    assert result.input_cost == 0.0
    assert result.output_cost == 0.0
    assert result.total_cost == 0.0


def test_zero_rate_yields_zero_cost_regardless_of_tokens():
    """A free/unpriced model (rate 0.0) must cost 0.0, not divide-by-zero or error."""
    result = compute_token_cost(0.0, 0.0, 1_000_000, 1_000_000)
    assert result.total_cost == 0.0


def test_only_input_tokens():
    result = compute_token_cost(0.000003, 0.000015, 1000, 0)
    assert result.output_cost == 0.0
    assert result.total_cost == result.input_cost


def test_only_output_tokens():
    result = compute_token_cost(0.000003, 0.000015, 0, 500)
    assert result.input_cost == 0.0
    assert result.total_cost == result.output_cost


def test_large_token_counts_do_not_lose_precision_relationally():
    """Doubling tokens must double cost — catches a future regression that scales
    all models equally rather than being proportional to usage."""
    small = compute_token_cost(0.000003, 0.000015, 1000, 500)
    large = compute_token_cost(0.000003, 0.000015, 2000, 1000)
    assert large.input_cost == small.input_cost * 2
    assert large.output_cost == small.output_cost * 2
    assert large.total_cost == small.total_cost * 2


def test_result_is_unrounded():
    """Callers are responsible for their own rounding; compute_token_cost must not
    pre-round, or a caller's existing round(x, 6) call sites would silently change
    behavior (e.g. double-rounding)."""
    result = compute_token_cost(0.0000037, 0.0000091, 333, 777)
    # The raw float product, not rounded to 6dp like estimate_cost.py's output.
    assert result.input_cost == 0.0000037 * 333
    assert result.output_cost == 0.0000091 * 777


def test_token_cost_is_frozen():
    result = compute_token_cost(0.000003, 0.000015, 1000, 500)
    try:
        result.total_cost = 0.0  # type: ignore[misc]
        assert False, "TokenCost should be immutable (frozen dataclass)"
    except AttributeError:
        pass
