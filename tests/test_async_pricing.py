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
async def test_aggregator_get_all_pricing_async():
    """Test async aggregator returns all pricing data with status."""
    aggregator = PricingAggregatorService()
    all_pricing, provider_statuses = await aggregator.get_all_pricing_async()
    
    assert len(all_pricing) > 0
    assert len(provider_statuses) == 2  # OpenAI and Anthropic
    
    # Check provider statuses
    provider_names = {status.provider_name for status in provider_statuses}
    assert "OpenAI" in provider_names
    assert "Anthropic" in provider_names
    
    # All should be available
    assert all(status.is_available for status in provider_statuses)
    
    # Models count should match
    total_models = sum(status.models_count for status in provider_statuses)
    assert total_models == len(all_pricing)


@pytest.mark.asyncio
async def test_aggregator_partial_failure():
    """Test aggregator handles partial provider failures gracefully."""
    aggregator = PricingAggregatorService()
    
    # Mock OpenAI to fail
    with patch.object(
        aggregator.openai_service,
        'get_pricing_with_status',
        return_value=([], ProviderStatus("OpenAI", False, "Connection error"))
    ):
        all_pricing, provider_statuses = await aggregator.get_all_pricing_async()
        
        # Should still have Anthropic data
        assert len(all_pricing) > 0
        assert all(model.provider == "Anthropic" for model in all_pricing)
        
        # Check statuses
        assert len(provider_statuses) == 2
        openai_status = next(s for s in provider_statuses if s.provider_name == "OpenAI")
        anthropic_status = next(s for s in provider_statuses if s.provider_name == "Anthropic")
        
        assert openai_status.is_available is False
        assert openai_status.error_message == "Connection error"
        assert anthropic_status.is_available is True


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


@pytest.mark.asyncio
async def test_concurrent_provider_fetching():
    """Test that providers are fetched concurrently."""
    import time
    
    aggregator = PricingAggregatorService()
    
    start_time = time.time()
    all_pricing, provider_statuses = await aggregator.get_all_pricing_async()
    elapsed_time = time.time() - start_time
    
    # Should complete quickly since both run concurrently
    # Using a generous threshold to avoid flaky tests in CI environments
    assert elapsed_time < 5.0
    assert len(all_pricing) > 0
    assert len(provider_statuses) == 2
