"""Tests for API endpoints."""
import pytest
from fastapi.testclient import TestClient
from src.main import app

client = TestClient(app)


def test_root_endpoint():
    """Test the root endpoint returns server information."""
    response = client.get("/")
    assert response.status_code == 200
    data = response.json()
    
    assert "name" in data
    assert "version" in data
    assert "description" in data
    assert "endpoints" in data
    assert data["name"] == "LLM Pricing MCP Server"
    assert "/" in data["endpoints"]
    assert "/pricing" in data["endpoints"]


def test_pricing_endpoint():
    """Test the pricing endpoint returns model pricing data."""
    response = client.get("/pricing")
    assert response.status_code == 200
    data = response.json()
    
    assert "models" in data
    assert "total_models" in data
    assert "timestamp" in data
    assert isinstance(data["models"], list)
    assert data["total_models"] > 0
    assert len(data["models"]) == data["total_models"]


def test_pricing_endpoint_with_provider_filter():
    """Test the pricing endpoint with provider filter."""
    # Test OpenAI filter
    response = client.get("/pricing?provider=openai")
    assert response.status_code == 200
    data = response.json()
    assert all(model["provider"] == "OpenAI" for model in data["models"])
    
    # Test Anthropic filter
    response = client.get("/pricing?provider=anthropic")
    assert response.status_code == 200
    data = response.json()
    assert all(model["provider"] == "Anthropic" for model in data["models"])


def test_pricing_model_structure():
    """Test that pricing models have the required fields."""
    response = client.get("/pricing")
    assert response.status_code == 200
    data = response.json()
    
    if data["models"]:
        model = data["models"][0]
        required_fields = [
            "model_name",
            "provider",
            "cost_per_input_token",
            "cost_per_output_token",
            "last_updated"
        ]
        for field in required_fields:
            assert field in model


def test_health_endpoint():
    """Test the health check endpoint."""
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    
    assert "status" in data
    assert "service" in data
    assert "version" in data
    assert data["status"] == "healthy"


def test_openapi_docs():
    """Test that OpenAPI documentation is available."""
    response = client.get("/docs")
    assert response.status_code == 200
    
    response = client.get("/openapi.json")
    assert response.status_code == 200
