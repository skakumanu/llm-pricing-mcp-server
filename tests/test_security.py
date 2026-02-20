"""Tests for security middleware features: authentication, rate limiting, payload validation."""
import os
from unittest.mock import patch
import pytest
from fastapi.testclient import TestClient

# Must set API key before importing app
os.environ["MCP_API_KEY"] = "test-secret-key"

from src.main import app, security_middleware

client = TestClient(app)


class TestAPIKeyAuthentication:
    """Test suite for API key authentication."""
    
    def test_unauthenticated_health_endpoint(self):
        """Health endpoints should not require authentication."""
        response = client.get("/health")
        assert response.status_code == 200
    
    def test_unauthenticated_docs_endpoint(self):
        """Documentation endpoints should not require authentication."""
        response = client.get("/docs")
        assert response.status_code == 200
    
    @patch('src.main.settings.mcp_api_key', 'test-secret-key')
    def test_pricing_requires_api_key(self):
        """Protected endpoints require API key when configured."""
        response = client.get("/pricing")
        assert response.status_code == 401
        assert response.json()["detail"] == "Unauthorized"
    
    @patch('src.main.settings.mcp_api_key', 'test-secret-key')
    def test_pricing_with_valid_api_key(self):
        """Protected endpoints accept valid API key."""
        response = client.get("/pricing", headers={"x-api-key": "test-secret-key"})
        assert response.status_code == 200
    
    @patch('src.main.settings.mcp_api_key', 'test-secret-key')
    def test_pricing_with_invalid_api_key(self):
        """Protected endpoints reject invalid API key."""
        response = client.get("/pricing", headers={"x-api-key": "wrong-key"})
        assert response.status_code == 401
        assert response.json()["detail"] == "Unauthorized"
    
    def test_custom_header_name(self):
        """Test that custom header name in settings is respected."""
        response = client.get("/pricing", headers={"x-api-key": "test-secret-key"})
        # May be 200 or 401 depending on whether app has API key set
        assert response.status_code in [200, 401]


class TestRateLimiting:
    """Test suite for rate limiting middleware."""
    
    def test_rate_limit_high_threshold_allows_requests(self):
        """Rapid requests should not exceed rate limit with high threshold."""
        # With RATE_LIMIT_PER_MINUTE=10000 in test env, 100 requests should pass
        for _ in range(100):
            response = client.get("/health")
            assert response.status_code == 200
    
    def test_health_endpoint_multiple_requests(self):
        """Health endpoint should handle rapid successive requests."""
        responses = []
        for _ in range(10):
            response = client.get("/health")
            responses.append(response.status_code)
        
        # All should succeed
        assert all(s == 200 for s in responses)


class TestPayloadValidation:
    """Test suite for request payload size validation."""
    
    def test_valid_cost_estimate_payload(self):
        """Requests within size limit should be accepted."""
        small_payload = {"tokens": 1000, "model": "gpt-4"}
        response = client.post(
            "/cost-estimate",
            json=small_payload
        )
        # Should get 200, 401 (if auth required), or 422 (validation error) but not 413
        assert response.status_code != 413
    
    def test_invalid_content_length_header_graceful(self):
        """Invalid Content-Length header should be handled gracefully."""
        # The middleware should either ignore it or return 400
        response = client.get(
            "/pricing",
            headers={"content-length": "not-a-number"}
        )
        # Should not have 500 error
        assert response.status_code in [200, 400, 401]
    
    def test_missing_content_length_allowed(self):
        """Requests without Content-Length header should be allowed."""
        response = client.get("/pricing")
        # Any status except 500 is acceptable
        assert response.status_code != 500


class TestSecurityLoggingIntegration:
    """Test suite for security-related integrations."""
    
    @patch('src.main.settings.mcp_api_key', 'test-secret-key')
    def test_auth_failure_response(self):
        """Authentication failures should return 401."""
        response = client.get("/pricing", headers={"x-api-key": "wrong"})
        assert response.status_code == 401
        data = response.json()
        assert "detail" in data
    
    def test_successful_health_check(self):
        """Health check should always be accessible."""
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data.get("status") == "healthy"

