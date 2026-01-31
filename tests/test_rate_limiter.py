"""
Integration tests for rate limiter with actual FastAPI endpoints.

These tests verify that the rate limiter correctly protects the application's
endpoints by making real HTTP requests to the running FastAPI server.

Prerequisites:
- FastAPI server running on http://localhost:8000
- Rate limiter service running on http://localhost:8002
- Database with at least one user (user_id=1)

Run: pytest tests/test_rate_limiter.py -v
"""

import pytest
import requests
import time
from typing import Dict, Any

# Base URLs
API_BASE_URL = "http://localhost:8000"
RATE_LIMITER_URL = "http://localhost:8002"


def reset_bucket(scope: str, identifier: str, endpoint: str) -> None:
    """Helper function to reset a rate limit bucket."""
    url = f"{RATE_LIMITER_URL}/reset"
    params = {
        "scope": scope,
        "identifier": identifier,
        "endpoint": endpoint
    }
    response = requests.delete(url, params=params)
    assert response.status_code == 200, f"Failed to reset bucket: {response.text}"


def get_bucket_status(scope: str, identifier: str, endpoint: str) -> Dict[str, Any]:
    """Helper function to get bucket status."""
    url = f"{RATE_LIMITER_URL}/status"
    params = {
        "scope": scope,
        "identifier": identifier,
        "endpoint": endpoint
    }
    response = requests.get(url, params=params)
    assert response.status_code == 200
    return response.json()


class TestEmailsEndpoint:
    """Test rate limiting on GET /api/emails endpoint."""
    
    ENDPOINT_KEY = "api/emails"
    CAPACITY = 10
    REFILL_RATE = 10
    
    def test_emails_endpoint_allows_requests_within_limit(self):
        """Test that requests within the limit are allowed."""
        reset_bucket("global", "all", self.ENDPOINT_KEY)
        
        # Make a request
        response = requests.get(f"{API_BASE_URL}/api/emails", params={"user_id": 1, "limit": 5})
        
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
    
    def test_emails_endpoint_custom_capacity(self):
        """Test that custom capacity (10) is applied."""
        reset_bucket("global", "all", self.ENDPOINT_KEY)
        
        # First request creates the bucket
        requests.get(f"{API_BASE_URL}/api/emails", params={"user_id": 1})
        
        # Check bucket status
        status = get_bucket_status("global", "all", self.ENDPOINT_KEY)
        assert status["limit"] == self.CAPACITY, f"Expected capacity {self.CAPACITY}, got {status['limit']}"
        assert status["remaining"] == self.CAPACITY - 1
    
    def test_emails_endpoint_rate_limit_exhaustion(self):
        """Test that requests are denied after exhausting the limit."""
        reset_bucket("global", "all", self.ENDPOINT_KEY)
        
        # Make 10 requests (should all succeed)
        for i in range(self.CAPACITY):
            response = requests.get(f"{API_BASE_URL}/api/emails", params={"user_id": 1})
            assert response.status_code == 200, f"Request {i+1} should succeed"
        
        # 11th request should be rate limited
        response = requests.get(f"{API_BASE_URL}/api/emails", params={"user_id": 1})
        assert response.status_code == 429
        assert "Rate limit exceeded" in response.json()["detail"]
        assert "Retry-After" in response.headers
    
    def test_emails_endpoint_rate_limit_headers(self):
        """Test that rate limit headers are present in responses."""
        reset_bucket("global", "all", self.ENDPOINT_KEY)
        
        # Make requests until rate limited
        for _ in range(self.CAPACITY):
            requests.get(f"{API_BASE_URL}/api/emails", params={"user_id": 1})
        
        # Get rate limited response
        response = requests.get(f"{API_BASE_URL}/api/emails", params={"user_id": 1})
        
        assert response.status_code == 429
        assert "X-RateLimit-Limit" in response.headers
        assert "X-RateLimit-Remaining" in response.headers
        assert "Retry-After" in response.headers
        assert response.headers["X-RateLimit-Limit"] == str(self.CAPACITY)
        assert response.headers["X-RateLimit-Remaining"] == "0"


class TestSyncEndpoint:
    """Test rate limiting on GET /api/sync endpoint."""
    
    ENDPOINT_KEY = "api/sync"
    CAPACITY = 10
    REFILL_RATE = 10
    
    def test_sync_endpoint_custom_capacity(self):
        """Test that custom capacity (10) is applied to sync endpoint."""
        reset_bucket("global", "all", self.ENDPOINT_KEY)
        
        # First request creates the bucket
        # Note: This endpoint might fail if Gmail auth is not set up, but rate limiting happens first
        response = requests.get(f"{API_BASE_URL}/api/sync", params={"user_id": 1})
        
        # Check bucket status (rate limiting happens before the actual sync logic)
        status = get_bucket_status("global", "all", self.ENDPOINT_KEY)
        assert status["limit"] == self.CAPACITY
        assert status["remaining"] == self.CAPACITY - 1
    
    def test_sync_endpoint_rate_limit_exhaustion(self):
        """Test that sync endpoint is rate limited after 10 requests."""
        reset_bucket("global", "all", self.ENDPOINT_KEY)
        
        # Make 10 requests
        for i in range(self.CAPACITY):
            requests.get(f"{API_BASE_URL}/api/sync", params={"user_id": 1})
        
        # 11th request should be rate limited
        response = requests.get(f"{API_BASE_URL}/api/sync", params={"user_id": 1})
        assert response.status_code == 429


class TestCalendarEventsEndpoint:
    """Test rate limiting on POST /api/calendar/events endpoint."""
    
    ENDPOINT_KEY = "api/calendar/events"
    CAPACITY = 100
    REFILL_RATE = 100
    
    def test_calendar_events_high_capacity(self):
        """Test that calendar events endpoint has high capacity (100)."""
        reset_bucket("global", "all", self.ENDPOINT_KEY)
        
        # Make a request
        event_data = {
            "user_id": 1,
            "event_data": {
                "title": "Test Event",
                "date": "2026-02-01",
                "time": "10:00 AM"
            }
        }
        
        # First request creates the bucket (might fail due to calendar auth, but rate limiting happens first)
        requests.post(f"{API_BASE_URL}/api/calendar/events", json=event_data)
        
        # Check bucket status
        status = get_bucket_status("global", "all", self.ENDPOINT_KEY)
        assert status["limit"] == self.CAPACITY, f"Expected capacity {self.CAPACITY}, got {status['limit']}"
        assert status["remaining"] == self.CAPACITY - 1
    
    def test_calendar_events_allows_many_requests(self):
        """Test that calendar events endpoint allows many requests due to high capacity."""
        reset_bucket("global", "all", self.ENDPOINT_KEY)
        
        event_data = {
            "user_id": 1,
            "event_data": {
                "title": "Test Event",
                "date": "2026-02-01"
            }
        }
        
        # Make 50 requests (should all be allowed)
        for i in range(50):
            response = requests.post(f"{API_BASE_URL}/api/calendar/events", json=event_data)
            # Rate limiting happens before calendar logic, so we check it wasn't rate limited
            assert response.status_code != 429, f"Request {i+1} should not be rate limited"
        
        # Check remaining tokens
        status = get_bucket_status("global", "all", self.ENDPOINT_KEY)
        assert status["remaining"] == self.CAPACITY - 50


class TestServerTotalBucket:
    """Test server-wide rate limiting (shared bucket across endpoints)."""
    
    ENDPOINT_KEY = "server_total"
    CAPACITY = 20
    TOKENS_PER_REQUEST = 2
    
    def test_server_total_shared_bucket(self):
        """Test that /api/query and /api/llm-query share the same bucket."""
        reset_bucket("global", "all", self.ENDPOINT_KEY)
        
        # Make request to /api/query
        response1 = requests.get(f"{API_BASE_URL}/api/query", params={"query": "test"})
        
        # Check bucket status (should have consumed 2 tokens)
        status = get_bucket_status("global", "all", self.ENDPOINT_KEY)
        assert status["limit"] == self.CAPACITY
        assert status["remaining"] == self.CAPACITY - self.TOKENS_PER_REQUEST
        
        # Make request to /api/llm-query (shares same bucket)
        llm_data = {
            "query": "test query",
            "user_id": 1
        }
        response2 = requests.post(f"{API_BASE_URL}/api/llm-query", json=llm_data)
        
        # Check bucket status (should have consumed another 2 tokens)
        status = get_bucket_status("global", "all", self.ENDPOINT_KEY)
        assert status["remaining"] == self.CAPACITY - (2 * self.TOKENS_PER_REQUEST)
    
    def test_server_total_custom_token_consumption(self):
        """Test that server_total endpoints consume 2 tokens per request."""
        reset_bucket("global", "all", self.ENDPOINT_KEY)
        
        # Make one request
        requests.get(f"{API_BASE_URL}/api/query", params={"query": "test"})
        
        # Check that 2 tokens were consumed
        status = get_bucket_status("global", "all", self.ENDPOINT_KEY)
        assert status["remaining"] == self.CAPACITY - 2, "Should consume 2 tokens per request"
    
    def test_server_total_exhaustion(self):
        """Test that server_total bucket can be exhausted."""
        reset_bucket("global", "all", self.ENDPOINT_KEY)
        
        # Make 10 requests (10 * 2 tokens = 20 tokens, exhausts the bucket)
        for i in range(10):
            response = requests.get(f"{API_BASE_URL}/api/query", params={"query": f"test{i}"})
            if i < 9:  # First 9 should succeed
                assert response.status_code != 429, f"Request {i+1} should not be rate limited"
        
        # 11th request should be denied (would need 2 more tokens but bucket is empty)
        response = requests.get(f"{API_BASE_URL}/api/query", params={"query": "test_final"})
        assert response.status_code == 429
        assert "Rate limit exceeded" in response.json()["detail"]


class TestRateLimiterFeatures:
    """Test various rate limiter features across different endpoints."""
    
    def test_different_endpoints_have_different_limits(self):
        """Test that different endpoints have different rate limit configurations."""
        # Reset all buckets
        reset_bucket("global", "all", "api/emails")
        reset_bucket("global", "all", "api/calendar/events")
        reset_bucket("global", "all", "server_total")
        
        # Make requests to each endpoint
        requests.get(f"{API_BASE_URL}/api/emails", params={"user_id": 1})
        requests.post(f"{API_BASE_URL}/api/calendar/events", json={"user_id": 1, "event_data": {"title": "Test", "date": "2026-02-01"}})
        requests.get(f"{API_BASE_URL}/api/query", params={"query": "test"})
        
        # Check each bucket has different limits
        status_emails = get_bucket_status("global", "all", "api/emails")
        status_calendar = get_bucket_status("global", "all", "api/calendar/events")
        status_server = get_bucket_status("global", "all", "server_total")
        
        assert status_emails["limit"] == 10
        assert status_calendar["limit"] == 100
        assert status_server["limit"] == 20
    
    def test_bucket_persistence_across_requests(self):
        """Test that bucket state persists across multiple requests."""
        reset_bucket("global", "all", "api/emails")
        
        # Make 3 requests
        for i in range(3):
            requests.get(f"{API_BASE_URL}/api/emails", params={"user_id": 1})
        
        # Check bucket state
        status = get_bucket_status("global", "all", "api/emails")
        assert status["remaining"] == 7, "Should have 7 tokens remaining (10 - 3)"
        
        # Make 2 more requests
        for i in range(2):
            requests.get(f"{API_BASE_URL}/api/emails", params={"user_id": 1})
        
        # Check bucket state again
        status = get_bucket_status("global", "all", "api/emails")
        assert status["remaining"] == 5, "Should have 5 tokens remaining (10 - 5)"
    
    def test_global_scope_affects_all_users(self):
        """Test that global scope rate limiting affects all users."""
        reset_bucket("global", "all", "api/emails")
        
        # User 1 makes 5 requests
        for _ in range(5):
            requests.get(f"{API_BASE_URL}/api/emails", params={"user_id": 1})
        
        # User 2 makes 5 requests (shares same bucket because scope is global)
        for _ in range(5):
            response = requests.get(f"{API_BASE_URL}/api/emails", params={"user_id": 2})
        
        # Bucket should be exhausted
        status = get_bucket_status("global", "all", "api/emails")
        assert status["remaining"] == 0
        
        # Next request from any user should be denied
        response = requests.get(f"{API_BASE_URL}/api/emails", params={"user_id": 1})
        assert response.status_code == 429


class TestRateLimiterIntegration:
    """Test integration between FastAPI app and rate limiter service."""
    
    def test_rate_limiter_service_is_running(self):
        """Verify that the rate limiter service is accessible."""
        response = requests.get(f"{RATE_LIMITER_URL}/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert "stats" in data
    
    def test_fastapi_app_is_running(self):
        """Verify that the FastAPI application is accessible."""
        # Try to access a non-rate-limited endpoint
        response = requests.get(f"{API_BASE_URL}/api/users")
        assert response.status_code == 200
    
    def test_rate_limiter_fail_open_behavior(self):
        """
        Test that if rate limiter is down, requests are still allowed.
        Note: This test requires manually stopping the rate limiter service.
        Skipped by default.
        """
        pytest.skip("Requires manual intervention to stop rate limiter service")


class TestEdgeCases:
    """Test edge cases and error handling."""
    
    def test_missing_user_id_parameter(self):
        """Test that missing required parameters return 400, not rate limit error."""
        # Don't provide user_id
        response = requests.get(f"{API_BASE_URL}/api/emails")
        assert response.status_code == 400
        assert "user_id parameter is required" in response.json()["detail"]
    
    def test_rate_limit_reset_works(self):
        """Test that resetting a bucket works correctly."""
        reset_bucket("global", "all", "api/emails")
        
        # Exhaust the bucket
        for _ in range(10):
            requests.get(f"{API_BASE_URL}/api/emails", params={"user_id": 1})
        
        # Verify it's exhausted
        response = requests.get(f"{API_BASE_URL}/api/emails", params={"user_id": 1})
        assert response.status_code == 429
        
        # Reset the bucket
        reset_bucket("global", "all", "api/emails")
        
        # Should work again
        response = requests.get(f"{API_BASE_URL}/api/emails", params={"user_id": 1})
        assert response.status_code == 200


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
