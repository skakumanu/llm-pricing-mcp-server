"""Tests for async pricing service functionality."""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from src.services.openai_pricing import OpenAIPricingService
from src.services.anthropic_pricing import AnthropicPricingService
from src.services.pricing_aggregator import PricingAggregatorService
from src.services.base_provider import ProviderStatus
import httpx


@pytest.mark.asyncio
async def test_openai_fetch_pricing_data():
    """Test OpenAI async pricing fetch."""
    service = OpenAIPricingService()
    pricing_data = await service.fetch_pricing_data()
    
    assert len(pricing_data) > 0
    assert all(model.provider == "OpenAI" for model in pricing_data)
    assert all(model.cost_per_input_token > 0 for model in pricing_data)
    assert all(model.cost_per_output_token > 0 for model in pricing_data)
    assert all(model.currency == "USD" for model in pricing_data)
    assert all(model.unit == "per_token" for model in pricing_data)
    assert all(model.source is not None for model in pricing_data)


@pytest.mark.asyncio
async def test_anthropic_fetch_pricing_data():
    """Test Anthropic async pricing fetch."""
    service = AnthropicPricingService()
    pricing_data = await service.fetch_pricing_data()
    
    assert len(pricing_data) > 0
    assert all(model.provider == "Anthropic" for model in pricing_data)
    assert all(model.cost_per_input_token > 0 for model in pricing_data)
    assert all(model.cost_per_output_token > 0 for model in pricing_data)
    assert all(model.currency == "USD" for model in pricing_data)
    assert all(model.unit == "per_token" for model in pricing_data)
    assert all(model.source is not None for model in pricing_data)


@pytest.mark.asyncio
async def test_openai_get_pricing_with_status_success():
    """Test OpenAI get_pricing_with_status returns correct status on success."""
    service = OpenAIPricingService()
    pricing_data, status = await service.get_pricing_with_status()
    
    assert status.provider_name == "OpenAI"
    assert status.is_available is True
    assert status.error_message is None
    assert len(pricing_data) > 0


@pytest.mark.asyncio
async def test_anthropic_get_pricing_with_status_success():
    """Test Anthropic get_pricing_with_status returns correct status on success."""
    service = AnthropicPricingService()
    pricing_data, status = await service.get_pricing_with_status()
    
    assert status.provider_name == "Anthropic"
    assert status.is_available is True
    assert status.error_message is None
    assert len(pricing_data) > 0


@pytest.mark.asyncio
async def test_provider_status_on_failure():
    """Test that provider returns correct status on failure."""
    service = OpenAIPricingService()
    
    # Mock the fetch_pricing_data to raise an exception
    with patch.object(service, 'fetch_pricing_data', side_effect=Exception("API Error")):
        pricing_data, status = await service.get_pricing_with_status()
        
        assert status.provider_name == "OpenAI"
        assert status.is_available is False
        assert status.error_message == "API Error"
        assert len(pricing_data) == 0


@pytest.mark.asyncio
async def test_aggregator_by_provider_async():
    """Test async aggregator filtering by provider."""
    aggregator = PricingAggregatorService()
    
    # Test OpenAI filter
    openai_pricing, statuses = await aggregator.get_pricing_by_provider_async("openai")
    assert len(openai_pricing) > 0
    assert all(model.provider == "OpenAI" for model in openai_pricing)
    assert len(statuses) == 1
    assert statuses[0].provider_name == "OpenAI"
    
    # Test Anthropic filter
    anthropic_pricing, statuses = await aggregator.get_pricing_by_provider_async("anthropic")
    assert len(anthropic_pricing) > 0
    assert all(model.provider == "Anthropic" for model in anthropic_pricing)
    assert len(statuses) == 1
    assert statuses[0].provider_name == "Anthropic"
    
    # Test case insensitivity
    openai_pricing_upper, _ = await aggregator.get_pricing_by_provider_async("OPENAI")
    assert len(openai_pricing_upper) == len(openai_pricing)


@pytest.mark.asyncio
async def test_aggregator_invalid_provider_async():
    """Test async aggregator with invalid provider."""
    aggregator = PricingAggregatorService()
    invalid_pricing, statuses = await aggregator.get_pricing_by_provider_async("invalid_provider")
    
    assert len(invalid_pricing) == 0
    assert len(statuses) == 0


@pytest.mark.asyncio
async def test_openai_api_key_verification_success():
    """Test OpenAI API key verification with valid key."""
    service = OpenAIPricingService(api_key="sk-test-key")
    
    # Mock the httpx client
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.raise_for_status = MagicMock()
    
    with patch('httpx.AsyncClient') as mock_client:
        mock_client.return_value.__aenter__.return_value.get = AsyncMock(return_value=mock_response)
        
        result = await service._verify_api_key()
        assert result is True


@pytest.mark.asyncio
async def test_openai_api_key_verification_failure():
    """Test OpenAI API key verification with invalid key."""
    service = OpenAIPricingService(api_key="invalid-key")
    
    # Mock the httpx client to raise 401 error
    mock_response = MagicMock()
    mock_response.status_code = 401
    
    with patch('httpx.AsyncClient') as mock_client:
        mock_get = AsyncMock(side_effect=httpx.HTTPStatusError("Unauthorized", request=MagicMock(), response=mock_response))
        mock_client.return_value.__aenter__.return_value.get = mock_get
        
        with pytest.raises(Exception, match="Invalid OpenAI API key"):
            await service._verify_api_key()


@pytest.mark.asyncio
async def test_anthropic_api_key_verification():
    """Test Anthropic API key verification."""
    service = AnthropicPricingService(api_key="sk-ant-test-key")
    
    result = await service._verify_api_key()
    assert result is True
    
    # Test invalid format - should return False, not raise
    service_invalid = AnthropicPricingService(api_key="invalid-key")
    result = await service_invalid._verify_api_key()
    assert result is False



