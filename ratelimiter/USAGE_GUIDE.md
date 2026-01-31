## Main behaviors

1) Rate-limit individual endpoints - per user (or other identifier like ip)
2) Rate-limit individual endpoints - gobal (full limiter for the endpoint, regardless of user)
3) Server-wide limit - Simulates all marked endpoints as a single bucket (multiple buckets acting the same way)
4) Custom rate-limit configuration

#### Notes:
- The code snippets above show complete examples with error handling. For actual implementation, only the `result = limiter.check(...)` call is essential - customize the rejection handling as needed for your application.
- The default configuration for limits is defined in limiter.go. This can be changed
- This guide is designed to ease implementation of this rate-limiting algorihtm with PYTHON (FastAPI). However,
the go code may be reused as it runs as its own server on port 8002.

### How to set up

0) Imports and limiter initialization:

```python
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + "/..")  # Depends on directory structure
from ratelimiter.client.ratelimiter_client import RateLimiterClient

# Initialize the ratae limiter object
limiter = RateLimiterClient("http://localhost:8002") 

```


### 1. Rate-limit individual endpoints - per user

Add this block of code at the start of each endpoint to mark individually.
Different users / identifiers have unique buckets, one user does not affect the other.

```python
result = limiter.check(
        scope="user",
        identifier=str(user_id),   # The unique identifier
        endpoint="/api/emails"     # The endpoint name
)

# Rejection handling, can be modified.
if not result["allowed"]:
    raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded! You can only view emails {result['limit']} times per hour. Wait {result['retry_after_seconds']} seconds.",
            headers={
                "X-RateLimit-Limit": str(result["limit"]),
                "X-RateLimit-Remaining": "0",
                "Retry-After": str(result["retry_after_seconds"])
        }
    )

```

### 2. Rate limit individual endpoints - global

Add this block of code at the start of each endpoint to mark individually.
Ensures a bucket for an endpoint is shared accross all requests to the endpoint.

```python
result = limiter.check(
        scope="global",
        identifier="all",          # The unique identifier
        endpoint="/api/emails"     # The endpoint name
)

if not result["allowed"]:
    raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded! You can only view emails {result['limit']} times per hour. Wait {result['retry_after_seconds']} seconds.",
            headers={
                "X-RateLimit-Limit": str(result["limit"]),
                "X-RateLimit-Remaining": "0",
                "Retry-After": str(result["retry_after_seconds"])
        }
    )

```



### 3. Server-wide limit

Add this block of code at the start of each endpoint that will act as using a shared bucket.
Calling any of these endpoints will deduct tokens from a shared bucket (abstracted as shared, not actually).

```python
result = limiter.check(
        scope="global",
        identifier="all",
        endpoint="server_total"     # IMPORTANT: Same endpoint key for all.
)

if not result["allowed"]:
    raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded! You can only view emails {result['limit']} times per hour. Wait {result['retry_after_seconds']} seconds.",
            headers={
                "X-RateLimit-Limit": str(result["limit"]),
                "X-RateLimit-Remaining": "0",
                "Retry-After": str(result["retry_after_seconds"])
        }
    )
```

### 4. Custom Rate Limit Configuration

You can override the token usage, default capacity and refill rate for specific endpoints by passing custom parameters. This is useful when different endpoints have different resource requirements.

**Important Notes:**
- Custom parameters (`capacity`, `refill_rate`, `tokens`) are **only applied when the bucket is first created**
- Once a bucket exists for a given (scope, identifier, endpoint) combination, subsequent requests use the existing bucket's configuration
- To apply new custom parameters, you must either:
  - Restart the rate limiter service (wipes all buckets from memory)
  - Use the reset endpoint: `curl -X DELETE "http://localhost:8002/reset?scope=<scope>&identifier=<id>&endpoint=<endpoint>"`
  - Use a different endpoint name

**Parameters:**
- `tokens` (optional, default: 1): Number of tokens to consume per request
- `capacity` (optional, default: 5): Maximum tokens in the bucket (burst capacity)
- `refill_rate` (optional, default: 5): Tokens added per hour


**Example - Expensive operation consuming multiple tokens:**

```python
# Very expensive operation: consumes 5 tokens per request
result = limiter.check(
        scope="user",
        identifier=str(user_id),
        endpoint="/api/expensive",
        tokens=5,              # Consume 5 tokens per request
        capacity=20,           # Max 20 tokens (allows 4 requests when full)
        refill_rate=20         # Refill 20 tokens per hour
)

if not result["allowed"]:
    raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded. This operation costs 5 tokens. Wait {result['retry_after_seconds']} seconds.",
            headers={
                "X-RateLimit-Limit": str(result["limit"]),
                "X-RateLimit-Remaining": str(result["remaining"]),
                "Retry-After": str(result["retry_after_seconds"])
        }
    )
```
