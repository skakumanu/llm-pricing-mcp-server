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
    
    # Endpoints is now a list of EndpointInfo objects
    endpoint_paths = [e["path"] for e in data["endpoints"]]
    assert "/" in endpoint_paths
    assert "/pricing" in endpoint_paths


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


def test_batch_cost_estimate_endpoint():
    """Test the batch cost estimate endpoint."""
    # Get some valid model names
    pricing_response = client.get("/pricing")
    assert pricing_response.status_code == 200
    models = pricing_response.json()["models"]
    assert len(models) >= 2
    
    # Use first few models for batch estimation
    test_models = [m["model_name"] for m in models[:3]]
    
    request_data = {
        "model_names": test_models,
        "input_tokens": 1000,
        "output_tokens": 500
    }
    
    response = client.post("/cost-estimate/batch", json=request_data)
    assert response.status_code == 200
    data = response.json()
    
    # Verify response structure
    assert "input_tokens" in data
    assert "output_tokens" in data
    assert "models" in data
    assert "cheapest_model" in data
    assert "most_expensive_model" in data
    assert "cost_range" in data
    assert "currency" in data
    assert "timestamp" in data
    
    # Verify values
    assert data["input_tokens"] == 1000
    assert data["output_tokens"] == 500
    assert len(data["models"]) == len(test_models)
    assert data["currency"] == "USD"
    
    # Verify cost range
    if data["cost_range"]:
        assert "min" in data["cost_range"]
        assert "max" in data["cost_range"]
        assert data["cost_range"]["min"] <= data["cost_range"]["max"]


def test_batch_cost_estimate_model_comparison():
    """Test that batch estimate properly compares model costs."""
    pricing_response = client.get("/pricing")
    models = pricing_response.json()["models"]
    test_models = [m["model_name"] for m in models[:3]]
    
    request_data = {
        "model_names": test_models,
        "input_tokens": 1000,
        "output_tokens": 500
    }
    
    response = client.post("/cost-estimate/batch", json=request_data)
    assert response.status_code == 200
    data = response.json()
    
    # Verify each model comparison
    for model_comparison in data["models"]:
        assert "model_name" in model_comparison
        assert "provider" in model_comparison
        assert "input_cost" in model_comparison
        assert "output_cost" in model_comparison
        assert "total_cost" in model_comparison
        assert "cost_per_1m_tokens" in model_comparison
        assert "is_available" in model_comparison
        
        if model_comparison["is_available"]:
            # Total cost should equal sum of input and output costs
            assert abs(
                model_comparison["total_cost"] - 
                (model_comparison["input_cost"] + model_comparison["output_cost"])
            ) < 0.0001


def test_batch_cost_estimate_with_nonexistent_model():
    """Test batch estimate with some non-existent models."""
    pricing_response = client.get("/pricing")
    models = pricing_response.json()["models"]
    valid_model = models[0]["model_name"]
    
    request_data = {
        "model_names": [valid_model, "nonexistent-model-1", "nonexistent-model-2"],
        "input_tokens": 1000,
        "output_tokens": 500
    }
    
    response = client.post("/cost-estimate/batch", json=request_data)
    assert response.status_code == 200
    data = response.json()
    
    # Should return all models with availability status
    assert len(data["models"]) == 3
    
    # Check that unavailable models are marked correctly
    unavailable = [m for m in data["models"] if not m["is_available"]]
    assert len(unavailable) == 2
    
    for model in unavailable:
        assert "error_message" in model
        assert model["error_message"] is not None


def test_performance_endpoint():
    """Test the performance metrics endpoint."""
    response = client.get("/performance")
    assert response.status_code == 200
    data = response.json()
    
    # Verify response structure
    assert "models" in data
    assert "total_models" in data
    assert "best_throughput" in data
    assert "lowest_latency" in data
    assert "largest_context" in data
    assert "best_value" in data
    assert "provider_status" in data
    assert "timestamp" in data
    
    assert isinstance(data["models"], list)
    assert data["total_models"] > 0
    assert len(data["models"]) == data["total_models"]


def test_performance_model_structure():
    """Test that performance models have the required fields."""
    response = client.get("/performance")
    assert response.status_code == 200
    data = response.json()
    
    if data["models"]:
        model = data["models"][0]
        required_fields = [
            "model_name",
            "provider",
            "cost_per_input_token",
            "cost_per_output_token"
        ]
        for field in required_fields:
            assert field in model
        
        # Optional fields should exist but may be None
        optional_fields = [
            "throughput",
            "latency_ms",
            "context_window",
            "performance_score",
            "value_score"
        ]
        for field in optional_fields:
            assert field in model


def test_performance_with_provider_filter():
    """Test the performance endpoint with provider filter."""
    response = client.get("/performance?provider=openai")
    assert response.status_code == 200
    data = response.json()
    
    # All models should be from OpenAI
    assert all(model["provider"] == "OpenAI" for model in data["models"])


def test_performance_with_sorting():
    """Test the performance endpoint with different sort options."""
    # Test sorting by cost
    response = client.get("/performance?sort_by=cost")
    assert response.status_code == 200
    data = response.json()
    
    # Verify models are sorted by cost (ascending)
    costs = [
        (m["cost_per_input_token"] + m["cost_per_output_token"]) / 2 
        for m in data["models"]
    ]
    assert costs == sorted(costs)
    
    # Test sorting by context_window
    response = client.get("/performance?sort_by=context_window")
    assert response.status_code == 200
    data = response.json()
    
    # Verify sorting (models without context_window will be at the end)
    context_windows = [m["context_window"] or 0 for m in data["models"]]
    assert context_windows == sorted(context_windows, reverse=True)


def test_performance_score_calculation():
    """Test that performance scores are calculated correctly."""
    response = client.get("/performance")
    assert response.status_code == 200
    data = response.json()
    
    for model in data["models"]:
        # If model has throughput, it should have a performance score
        if model["throughput"] and model["cost_per_input_token"] > 0:
            assert model["performance_score"] is not None
            assert model["performance_score"] > 0
        
        # If model has context_window, it should have a value score
        if model["context_window"] and model["cost_per_input_token"] > 0:
            assert model["value_score"] is not None
            assert model["value_score"] > 0


def test_root_endpoint_includes_new_endpoints():
    """Test that root endpoint lists all new endpoints."""
    response = client.get("/")
    assert response.status_code == 200
    data = response.json()
    
    # Check that endpoints is now a list of EndpointInfo objects
    assert "endpoints" in data
    assert isinstance(data["endpoints"], list)
    
    # Check that each endpoint has method and description
    for endpoint in data["endpoints"]:
        assert "path" in endpoint
        assert "method" in endpoint
        assert "description" in endpoint
    
    # Check for specific endpoints
    endpoint_paths = [e["path"] for e in data["endpoints"]]
    assert "/cost-estimate/batch" in endpoint_paths
    assert "/performance" in endpoint_paths
    assert "/models" in endpoint_paths
    
    # Check that sample_models and quick_start_guide are present
    assert "sample_models" in data
    assert isinstance(data["sample_models"], list)
    assert len(data["sample_models"]) > 0
    assert "quick_start_guide" in data


def test_models_endpoint():
    """Test the models endpoint returns all available model names."""
    response = client.get("/models")
    assert response.status_code == 200
    data = response.json()
    
    # Verify response structure
    assert "total_models" in data
    assert "providers" in data
    assert "models_by_provider" in data
    assert "all_models" in data
    
    # Verify data types
    assert isinstance(data["total_models"], int)
    assert isinstance(data["providers"], list)
    assert isinstance(data["models_by_provider"], dict)
    assert isinstance(data["all_models"], list)
    
    # Verify consistency
    assert data["total_models"] == len(data["all_models"])
    assert data["total_models"] > 0
    
    # Verify providers have model lists
    for provider in data["providers"]:
        assert provider in data["models_by_provider"]
        assert isinstance(data["models_by_provider"][provider], list)
        assert len(data["models_by_provider"][provider]) > 0


def test_models_endpoint_with_provider_filter():
    """Test the models endpoint with provider filter."""
    # Test OpenAI filter
    response = client.get("/models?provider=openai")
    assert response.status_code == 200
    data = response.json()
    
    # Should only have OpenAI provider
    assert len(data["providers"]) == 1
    assert "OpenAI" in data["providers"]
    
    # Test Anthropic filter
    response = client.get("/models?provider=anthropic")
    assert response.status_code == 200
    data = response.json()
    
    # Should only have Anthropic provider
    assert len(data["providers"]) == 1
    assert "Anthropic" in data["providers"]


def test_endpoint_methods_are_clear():
    """Test that root endpoint clearly shows HTTP methods for each endpoint."""
    response = client.get("/")
    assert response.status_code == 200
    data = response.json()
    
    # Find POST endpoints
    post_endpoints = [e for e in data["endpoints"] if e["method"] == "POST"]
    assert len(post_endpoints) >= 2  # At least /cost-estimate and /cost-estimate/batch
    
    # Find GET endpoints
    get_endpoints = [e for e in data["endpoints"] if e["method"] == "GET"]
    assert len(get_endpoints) >= 6  # /, /pricing, /models, /performance, /health, /docs, /redoc
    
    # Verify specific endpoints have correct methods
    endpoint_methods = {e["path"]: e["method"] for e in data["endpoints"]}
    assert endpoint_methods["/cost-estimate"] == "POST"
    assert endpoint_methods["/cost-estimate/batch"] == "POST"
    assert endpoint_methods["/models"] == "GET"
    assert endpoint_methods["/pricing"] == "GET"
    assert endpoint_methods["/performance"] == "GET"
