"""Tests for pricing services."""
import pytest
from src.services.openai_pricing import OpenAIPricingService
from src.services.anthropic_pricing import AnthropicPricingService
from src.services.pricing_aggregator import PricingAggregatorService


def test_openai_pricing_service():
    """Test OpenAI pricing service returns data."""
    service = OpenAIPricingService()
    pricing_data = service.get_pricing_data()
    
    assert len(pricing_data) > 0
    assert all(model.provider == "OpenAI" for model in pricing_data)
    assert all(model.cost_per_input_token > 0 for model in pricing_data)
    assert all(model.cost_per_output_token > 0 for model in pricing_data)


def test_anthropic_pricing_service():
    """Test Anthropic pricing service returns data."""
    service = AnthropicPricingService()
    pricing_data = service.get_pricing_data()
    
    assert len(pricing_data) > 0
    assert all(model.provider == "Anthropic" for model in pricing_data)
    assert all(model.cost_per_input_token > 0 for model in pricing_data)
    assert all(model.cost_per_output_token > 0 for model in pricing_data)


def test_pricing_aggregator_all_pricing():
    """Test pricing aggregator returns all pricing data."""
    aggregator = PricingAggregatorService()
    all_pricing = aggregator.get_all_pricing()
    
    assert len(all_pricing) > 0
    
    # Should have both OpenAI and Anthropic models
    providers = set(model.provider for model in all_pricing)
    assert "OpenAI" in providers
    assert "Anthropic" in providers


def test_pricing_aggregator_by_provider():
    """Test pricing aggregator filtering by provider."""
    aggregator = PricingAggregatorService()
    
    # Test OpenAI filter
    openai_pricing = aggregator.get_pricing_by_provider("openai")
    assert len(openai_pricing) > 0
    assert all(model.provider == "OpenAI" for model in openai_pricing)
    
    # Test Anthropic filter
    anthropic_pricing = aggregator.get_pricing_by_provider("anthropic")
    assert len(anthropic_pricing) > 0
    assert all(model.provider == "Anthropic" for model in anthropic_pricing)
    
    # Test case insensitivity
    openai_pricing_upper = aggregator.get_pricing_by_provider("OPENAI")
    assert len(openai_pricing_upper) == len(openai_pricing)


def test_pricing_aggregator_invalid_provider():
    """Test pricing aggregator with invalid provider."""
    aggregator = PricingAggregatorService()
    invalid_pricing = aggregator.get_pricing_by_provider("invalid_provider")
    
    assert len(invalid_pricing) == 0
