"""
Python client library for Rate Limiter microservice - Updated for new database schema

Usage:
    from ratelimiter.client.ratelimiter_client2 import RateLimiterClient

    limiter = RateLimiterClient("http://localhost:8002")
    
    # Rate limit by account (logged-in user)
    result = limiter.check(scope="account", identifier="123", endpoint="/api/sync")
    
    # Rate limit by email account (specific Gmail/Outlook account)
    result = limiter.check(scope="email_account", identifier="456", endpoint="/api/emails")
    
    if not result["allowed"]:
        raise HTTPException(429, "Rate limited")
"""

import requests
from typing import Optional, Dict, Any


class RateLimiterClient:
    """Client for interacting with the Rate Limiter microservice
    
    Updated to work with new database schema:
    - Account: The logged-in user (account_id)
    - EmailAccount: A connected Gmail/Outlook account (email_account_id)
    """

    def __init__(self, base_url: str = "http://localhost:8002", timeout: int = 5):
        """
        Initialize the rate limiter client

        Args:
            base_url: Base URL of the rate limiter service
            timeout: Request timeout in seconds
        """
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def check(
        self,
        scope: str,
        identifier: str,
        endpoint: str,
        tokens: int = 1,
        capacity: Optional[int] = None,
        refill_rate: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Check if a request should be allowed based on rate limits

        Args:
            scope: Scope type ("account", "email_account", "global", "endpoint", "custom")
                  - "account": Rate limit per Account (logged-in user)
                  - "email_account": Rate limit per EmailAccount (Gmail/Outlook account)
                  - "global": Global rate limiting across all users
                  - "endpoint": Per-endpoint rate limiting
                  - "custom": Custom scope for special resources
            identifier: Account ID, EmailAccount ID, "all", or custom identifier
                       - For scope="account": use account_id (e.g., "123")
                       - For scope="email_account": use email_account_id (e.g., "456")
                       - For scope="global": use "all"
            endpoint: API endpoint or resource name (e.g., "/api/emails", "/api/sync")
            tokens: Number of tokens to consume (default: 1)
            capacity: Optional maximum tokens for this bucket (only used on first creation)
            refill_rate: Optional tokens per hour for this bucket (only used on first creation)

        Returns:
            Dictionary with:
                - allowed (bool): Whether request is allowed
                - remaining (int): Tokens remaining
                - limit (int): Maximum capacity
                - reset_after_seconds (int): Seconds until bucket is full
                - retry_after_seconds (int): Seconds to wait before retry

        Raises:
            requests.RequestException: If request fails
        """
        try:
            payload = {
                "scope": scope,
                "identifier": identifier,
                "endpoint": endpoint,
                "tokens": tokens
            }

            # Add optional config parameters if provided
            # If these are not provided (since optional), the server uses defaults (these become nil pointers and pattern matched).
            if capacity is not None:
                payload["capacity"] = capacity
            if refill_rate is not None:
                payload["refill_rate"] = refill_rate

            response = requests.post(
                f"{self.base_url}/check",
                json=payload,
                timeout=self.timeout
            )

            # Rate limiter returns 200 for allowed, 429 for denied
            # Both cases return valid JSON response
            if response.status_code in (200, 429):
                return response.json()
            else:
                response.raise_for_status()

        except requests.RequestException as e:
            # If rate limiter is down, allow the request (fail open)
            # Log the error and let the request proceed
            print(f"Warning: Rate limiter unavailable: {e}")
            return {
                "allowed": True,
                "remaining": -1,
                "limit": -1,
                "reset_after_seconds": 0,
                "retry_after_seconds": 0
            }

    def get_status(
        self,
        scope: str,
        identifier: str,
        endpoint: str
    ) -> Dict[str, Any]:
        """
        Get current status of a rate limit bucket

        Args:
            scope: Scope type ("account", "email_account", "global", "endpoint", "custom")
            identifier: Account ID, EmailAccount ID, "all", or custom identifier
            endpoint: Endpoint

        Returns:
            Dictionary with bucket status
        """
        response = requests.get(
            f"{self.base_url}/status",
            params={
                "scope": scope,
                "identifier": identifier,
                "endpoint": endpoint
            },
            timeout=self.timeout
        )
        response.raise_for_status()
        return response.json()

    def reset(
        self,
        scope: str,
        identifier: str,
        endpoint: str
    ) -> Dict[str, Any]:
        """
        Reset a rate limit bucket to full capacity (admin operation)

        Args:
            scope: Scope type
            identifier: Identifier
            endpoint: Endpoint

        Returns:
            Dictionary with success status
        """
        response = requests.delete(
            f"{self.base_url}/reset",
            params={
                "scope": scope,
                "identifier": identifier,
                "endpoint": endpoint
            },
            timeout=self.timeout
        )
        response.raise_for_status()
        return response.json()

    def health(self) -> Dict[str, Any]:
        """
        Check service health and get statistics

        Returns:
            Dictionary with health status and stats
        """
        response = requests.get(
            f"{self.base_url}/health",
            timeout=self.timeout
        )
        response.raise_for_status()
        return response.json()

    # Convenience methods for new database schema
    
    def check_account_limit(
        self,
        account_id: int,
        endpoint: str,
        tokens: int = 1,
        capacity: Optional[int] = None,
        refill_rate: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Check rate limit for a specific Account (logged-in user)
        
        Args:
            account_id: The Account ID (from accounts table)
            endpoint: API endpoint or resource name
            tokens: Number of tokens to consume (default: 1)
            capacity: Optional maximum tokens for this bucket
            refill_rate: Optional tokens per hour for this bucket
            
        Returns:
            Dictionary with rate limit check result
        """
        return self.check(
            scope="account",
            identifier=str(account_id),
            endpoint=endpoint,
            tokens=tokens,
            capacity=capacity,
            refill_rate=refill_rate
        )
    
    def check_email_account_limit(
        self,
        email_account_id: int,
        endpoint: str,
        tokens: int = 1,
        capacity: Optional[int] = None,
        refill_rate: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Check rate limit for a specific EmailAccount (Gmail/Outlook account)
        
        Args:
            email_account_id: The EmailAccount ID (from email_accounts table)
            endpoint: API endpoint or resource name
            tokens: Number of tokens to consume (default: 1)
            capacity: Optional maximum tokens for this bucket
            refill_rate: Optional tokens per hour for this bucket
            
        Returns:
            Dictionary with rate limit check result
        """
        return self.check(
            scope="email_account",
            identifier=str(email_account_id),
            endpoint=endpoint,
            tokens=tokens,
            capacity=capacity,
            refill_rate=refill_rate
        )
    
    def check_global_limit(
        self,
        endpoint: str,
        tokens: int = 1,
        capacity: Optional[int] = None,
        refill_rate: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Check global rate limit (applies to all users)
        
        Args:
            endpoint: API endpoint or resource name
            tokens: Number of tokens to consume (default: 1)
            capacity: Optional maximum tokens for this bucket
            refill_rate: Optional tokens per hour for this bucket
            
        Returns:
            Dictionary with rate limit check result
        """
        return self.check(
            scope="global",
            identifier="all",
            endpoint=endpoint,
            tokens=tokens,
            capacity=capacity,
            refill_rate=refill_rate
        )


# FastAPI integration helpers

def create_rate_limit_dependency(
    limiter: RateLimiterClient,
    scope: str = "account",
    endpoint: Optional[str] = None
):
    """
    Create a FastAPI dependency for rate limiting

    Usage:
        limiter = RateLimiterClient()
        rate_limit = create_rate_limit_dependency(limiter, scope="account")

        @app.get("/api/sync", dependencies=[Depends(rate_limit)])
        async def sync_emails(account_id: int):
            return {"status": "ok"}

    Args:
        limiter: RateLimiterClient instance
        scope: Scope type ("account", "email_account", "global", etc.)
        endpoint: Endpoint (if None, will use request path)

    Returns:
        FastAPI dependency function
    """
    from fastapi import HTTPException, Request

    async def rate_limit_check(request: Request):
        # Get identifier from request based on scope
        if scope == "account":
            # Get account ID from request (assumes authentication middleware sets this)
            identifier = str(getattr(request.state, "account_id", "anonymous"))
        elif scope == "email_account":
            # Get email account ID from request
            identifier = str(getattr(request.state, "email_account_id", "anonymous"))
        elif scope == "global":
            identifier = "all"
        else:
            # For custom scopes, try to get from request state
            identifier = str(getattr(request.state, f"{scope}_id", "anonymous"))

        # Use provided endpoint or request path
        endpoint_path = endpoint or request.url.path

        # Check rate limit
        result = limiter.check(
            scope=scope,
            identifier=identifier,
            endpoint=endpoint_path
        )

        if not result["allowed"]:
            raise HTTPException(
                status_code=429,
                detail="Rate limit exceeded",
                headers={
                    "X-RateLimit-Limit": str(result["limit"]),
                    "X-RateLimit-Remaining": "0",
                    "X-RateLimit-Reset": str(result["reset_after_seconds"]),
                    "Retry-After": str(result["retry_after_seconds"])
                }
            )

        # Add rate limit headers to response
        request.state.rate_limit_remaining = result["remaining"]
        request.state.rate_limit_limit = result["limit"]

    return rate_limit_check


# Decorator for easy integration
def rate_limited(
    limiter: RateLimiterClient,
    scope: str = "account",
    identifier_key: str = "account_id",
    endpoint: Optional[str] = None
):
    """
    Decorator for rate limiting FastAPI endpoints

    Usage:
        limiter = RateLimiterClient()

        @app.get("/api/sync")
        @rate_limited(limiter, scope="account", identifier_key="account_id")
        async def sync_emails(account_id: int):
            return {"status": "ok"}
        
        # For email account specific rate limiting:
        @app.get("/api/emails")
        @rate_limited(limiter, scope="email_account", identifier_key="email_account_id")
        async def get_emails(email_account_id: int):
            return {"emails": []}

    Args:
        limiter: RateLimiterClient instance
        scope: Scope type ("account", "email_account", "global", etc.)
        identifier_key: Parameter name to use as identifier
        endpoint: Endpoint (if None, will use function name)
    """
    from functools import wraps
    from fastapi import HTTPException

    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            # Get identifier from kwargs based on identifier_key
            if scope == "global":
                identifier = "all"
            else:
                identifier = kwargs.get(identifier_key, "anonymous")

            # Use provided endpoint or function name
            endpoint_path = endpoint or f"/api/{func.__name__}"

            # Check rate limit
            result = limiter.check(
                scope=scope,
                identifier=str(identifier),
                endpoint=endpoint_path
            )

            if not result["allowed"]:
                raise HTTPException(
                    status_code=429,
                    detail="Rate limit exceeded",
                    headers={
                        "Retry-After": str(result["retry_after_seconds"])
                    }
                )

            # Call original function
            return await func(*args, **kwargs)

        return wrapper
    return decorator


# Helper function to extract account/email_account IDs from request
def get_identifier_from_request(
    request,
    scope: str,
    account_id_key: str = "account_id",
    email_account_id_key: str = "email_account_id"
) -> str:
    """
    Helper to extract the correct identifier from a request based on scope
    
    Args:
        request: FastAPI Request object
        scope: Scope type ("account", "email_account", "global", etc.)
        account_id_key: Key to look for account_id in request state
        email_account_id_key: Key to look for email_account_id in request state
        
    Returns:
        String identifier for rate limiting
    """
    if scope == "account":
        return str(getattr(request.state, account_id_key, "anonymous"))
    elif scope == "email_account":
        return str(getattr(request.state, email_account_id_key, "anonymous"))
    elif scope == "global":
        return "all"
    else:
        # For custom scopes, try to get from request state
        return str(getattr(request.state, f"{scope}_id", "anonymous"))

