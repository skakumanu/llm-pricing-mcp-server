"""Tests for Pydantic models."""
import pytest
from datetime import datetime
from src.models.pricing import PricingMetrics, PricingResponse, ServerInfo


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


def test_server_info_creation():
    """Test creating a ServerInfo instance."""
    info = ServerInfo(
        name="Test Server",
        version="1.0.0",
        description="A test server",
        endpoints=["/", "/pricing"]
    )
    
    assert info.name == "Test Server"
    assert info.version == "1.0.0"
    assert info.description == "A test server"
    assert len(info.endpoints) == 2
    assert "/" in info.endpoints
    assert "/pricing" in info.endpoints


def test_pricing_metrics_validation():
    """Test that PricingMetrics validates required fields."""
    with pytest.raises(Exception):
        # Missing required fields should raise an error
        PricingMetrics()
