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


def test_cost_estimate_endpoint():
    """Test the cost estimate endpoint with valid model."""
    # First, get a valid model name from the pricing endpoint
    pricing_response = client.get("/pricing")
    assert pricing_response.status_code == 200
    models = pricing_response.json()["models"]
    assert len(models) > 0
    
    # Use the first model for cost estimation
    test_model = models[0]["model_name"]
    
    # Test cost estimation
    request_data = {
        "model_name": test_model,
        "input_tokens": 1000,
        "output_tokens": 500
    }
    
    response = client.post("/cost-estimate", json=request_data)
    assert response.status_code == 200
    data = response.json()
    
    # Verify response structure
    assert "model_name" in data
    assert "provider" in data
    assert "input_tokens" in data
    assert "output_tokens" in data
    assert "input_cost" in data
    assert "output_cost" in data
    assert "total_cost" in data
    assert "currency" in data
    assert "timestamp" in data
    
    # Verify values
    assert data["model_name"] == test_model
    assert data["input_tokens"] == 1000
    assert data["output_tokens"] == 500
    assert data["total_cost"] == data["input_cost"] + data["output_cost"]
    assert data["currency"] == "USD"


def test_cost_estimate_with_zero_tokens():
    """Test cost estimation with zero tokens."""
    pricing_response = client.get("/pricing")
    models = pricing_response.json()["models"]
    test_model = models[0]["model_name"]
    
    request_data = {
        "model_name": test_model,
        "input_tokens": 0,
        "output_tokens": 0
    }
    
    response = client.post("/cost-estimate", json=request_data)
    assert response.status_code == 200
    data = response.json()
    
    assert data["input_cost"] == 0.0
    assert data["output_cost"] == 0.0
    assert data["total_cost"] == 0.0


def test_cost_estimate_nonexistent_model():
    """Test cost estimation with a non-existent model."""
    request_data = {
        "model_name": "nonexistent-model-xyz",
        "input_tokens": 1000,
        "output_tokens": 500
    }
    
    response = client.post("/cost-estimate", json=request_data)
    assert response.status_code == 404
    data = response.json()
    assert "detail" in data
    assert "not found" in data["detail"].lower()


def test_cost_estimate_case_insensitive():
    """Test that model name matching is case-insensitive."""
    # Get a valid model
    pricing_response = client.get("/pricing")
    models = pricing_response.json()["models"]
    test_model = models[0]["model_name"]
    
    # Test with uppercase version
    request_data = {
        "model_name": test_model.upper(),
        "input_tokens": 100,
        "output_tokens": 50
    }
    
    response = client.post("/cost-estimate", json=request_data)
    assert response.status_code == 200
    data = response.json()
    
    # Should return the original model name from pricing data
    assert data["model_name"].lower() == test_model.lower()


def test_cost_estimate_negative_tokens():
    """Test that negative token counts are rejected."""
    pricing_response = client.get("/pricing")
    models = pricing_response.json()["models"]
    test_model = models[0]["model_name"]
    
    request_data = {
        "model_name": test_model,
        "input_tokens": -100,
        "output_tokens": 500
    }
    
    response = client.post("/cost-estimate", json=request_data)
    assert response.status_code == 422  # Validation error
