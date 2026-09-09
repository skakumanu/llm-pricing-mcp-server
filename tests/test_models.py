"""Tests for Pydantic models."""
import pytest
from datetime import datetime
from src.models.pricing import PricingMetrics, PricingResponse


def test_pricing_metrics_creation():
    """Test creating a PricingMetrics instance."""
    metrics = PricingMetrics(
        model_name="gpt-4",
        provider="OpenAI",
        cost_per_input_token=0.00003,
        cost_per_output_token=0.00006,
        throughput=20.0,
        latency_ms=2500.0,
        context_window=8192
    )

    assert metrics.model_name == "gpt-4"
    assert metrics.provider == "OpenAI"
    assert metrics.cost_per_input_token == 0.00003
    assert metrics.cost_per_output_token == 0.00006
    assert metrics.throughput == 20.0
    assert metrics.latency_ms == 2500.0
    assert metrics.context_window == 8192
    assert isinstance(metrics.last_updated, datetime)


def test_pricing_metrics_optional_fields():
    """Test PricingMetrics with optional fields."""
    metrics = PricingMetrics(
        model_name="test-model",
        provider="TestProvider",
        cost_per_input_token=0.001,
        cost_per_output_token=0.002
    )

    assert metrics.model_name == "test-model"
    assert metrics.provider == "TestProvider"
    assert metrics.throughput is None
    assert metrics.latency_ms is None
    assert metrics.context_window is None


def test_pricing_response_creation():
    """Test creating a PricingResponse instance."""
    metrics_list = [
        PricingMetrics(
            model_name="model1",
            provider="Provider1",
            cost_per_input_token=0.001,
            cost_per_output_token=0.002
        ),
        PricingMetrics(
            model_name="model2",
            provider="Provider2",
            cost_per_input_token=0.003,
            cost_per_output_token=0.004
        )
    ]

    response = PricingResponse(
        models=metrics_list,
        total_models=2
    )

    assert len(response.models) == 2
    assert response.total_models == 2
    assert isinstance(response.timestamp, datetime)


def test_pricing_metrics_validation():
    """Test that PricingMetrics validates required fields."""
    with pytest.raises(Exception):
        # Missing required fields should raise an error
        PricingMetrics()


def test_cost_at_10k_tokens_absolute_value():
    """cost_at_10k_tokens must equal rate * 10,000 — not rate/1000 * 10,000."""
    metrics = PricingMetrics(
        model_name="rate-model",
        provider="TestProvider",
        cost_per_input_token=0.000003,  # $3 / 1M input tokens
        cost_per_output_token=0.000015,  # $15 / 1M output tokens
    )
    cost = metrics.cost_at_10k_tokens
    expected_input = 0.000003 * 10000
    expected_output = 0.000015 * 10000
    assert abs(cost.input_cost - round(expected_input, 4)) < 1e-6
    assert abs(cost.output_cost - round(expected_output, 4)) < 1e-6
    assert abs(cost.total_cost - round((expected_input + expected_output) / 2, 4)) < 1e-6


def test_cost_at_100k_tokens_absolute_value():
    """cost_at_100k_tokens must equal rate * 100,000 — not rate/1000 * 100,000."""
    metrics = PricingMetrics(
        model_name="rate-model",
        provider="TestProvider",
        cost_per_input_token=0.000003,
        cost_per_output_token=0.000015,
    )
    cost = metrics.cost_at_100k_tokens
    expected_input = 0.000003 * 100000
    expected_output = 0.000015 * 100000
    assert abs(cost.input_cost - round(expected_input, 4)) < 1e-6
    assert abs(cost.output_cost - round(expected_output, 4)) < 1e-6
    assert abs(cost.total_cost - round((expected_input + expected_output) / 2, 4)) < 1e-6


def test_cost_at_1m_tokens_absolute_value():
    """cost_at_1m_tokens must equal rate * 1,000,000 — not rate/1000 * 1,000,000."""
    metrics = PricingMetrics(
        model_name="rate-model",
        provider="TestProvider",
        cost_per_input_token=0.000003,  # $3 / 1M input tokens
        cost_per_output_token=0.000015,  # $15 / 1M output tokens
    )
    cost = metrics.cost_at_1m_tokens
    # At 1M tokens, the per-token rate * 1,000,000 should equal the
    # published "$X per million tokens" price exactly.
    assert abs(cost.input_cost - 3.0) < 1e-6
    assert abs(cost.output_cost - 15.0) < 1e-6
    assert abs(cost.total_cost - 9.0) < 1e-6
