# Integration Example with Mail Agent

This document shows how to integrate the rate limiter with the Mail Agent project.

## Step 1: Start the Rate Limiter

```bash
cd ratelimiter
./ratelimiter &
```

Or build and run:
```bash
cd ratelimiter
go build -o ratelimiter
./ratelimiter &
```

## Step 2: Add Rate Limiting to Mail Agent Endpoints

### Example: Rate Limit the Email Sync Endpoint

Edit `backend/app.py`:

```python
# Add import at the top
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + "/..")
from ratelimiter.client.ratelimiter_client import RateLimiterClient

# Initialize rate limiter
limiter = RateLimiterClient("http://localhost:8002")

# Modify the /api/sync endpoint
@app.get("/api/sync")
async def sync_emails(user_id: Optional[int] = None) -> Dict:
    """Sync emails from Gmail for a specific user"""
    if user_id is None:
        raise HTTPException(status_code=400, detail="user_id parameter is required")

    # ============ ADD RATE LIMITING HERE ============
    # Check rate limit: 10 syncs per hour per user
    result = limiter.check(
        scope="user",
        identifier=str(user_id),
        endpoint="/api/sync"
    )

    if not result["allowed"]:
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded. Please wait {result['retry_after_seconds']} seconds before syncing again.",
            headers={
                "X-RateLimit-Limit": str(result["limit"]),
                "X-RateLimit-Remaining": "0",
                "X-RateLimit-Reset": str(result["reset_after_seconds"]),
                "Retry-After": str(result["retry_after_seconds"])
            }
        )
    # ===============================================

    # Rest of the sync logic remains the same
    try:
        # Get Gmail service
        service = get_service(user_id)

        # ... rest of existing code ...
```

### Example: Rate Limit Vector Database Queries

Limit expensive AI searches:

```python
@app.get("/api/query")
async def query_emails(query: str, top_k: int = 3) -> Dict:
    """Query vector database and get AI answer"""

    # ============ ADD RATE LIMITING HERE ============
    # Global limit: 100 AI queries per hour across all users
    result = limiter.check(
        scope="global",
        identifier="all",
        endpoint="/api/query"
    )

    if not result["allowed"]:
        raise HTTPException(
            status_code=429,
            detail="AI query service is at capacity. Please try again later.",
            headers={"Retry-After": str(result["retry_after_seconds"])}
        )
    # ===============================================

    # Rest of the query logic remains the same
    results = await query_vector_db(query, top_k)
    # ... rest of existing code ...
```

### Example: Rate Limit Calendar Event Creation

Prevent spam event creation:

```python
@app.post("/api/calendar/events")
async def create_calendar_event(event_data: dict, user_id: int):
    """Create a new calendar event"""

    # ============ ADD RATE LIMITING HERE ============
    # Per-user limit: 50 event creations per hour
    result = limiter.check(
        scope="user",
        identifier=str(user_id),
        endpoint="/api/calendar/events"
    )

    if not result["allowed"]:
        raise HTTPException(
            status_code=429,
            detail="Too many calendar events created. Please wait before creating more.",
            headers={"Retry-After": str(result["retry_after_seconds"])}
        )
    # ===============================================

    # Rest of the event creation logic remains the same
    service, error = get_calendar_service(user_id)
    # ... rest of existing code ...
```

## Step 3: Configure Rate Limits Per Endpoint

You can use different limits for different endpoints by modifying the rate limiter defaults or using different scopes:

### Light Operations (High Limit)
```python
# Listing emails: 1000 requests per hour
result = limiter.check(scope="user", identifier=str(user_id), endpoint="/api/emails")
```

### Medium Operations (Medium Limit)
```python
# Syncing emails: 50 requests per hour
result = limiter.check(scope="user", identifier=str(user_id), endpoint="/api/sync")
```

### Heavy Operations (Low Limit)
```python
# AI queries: 20 requests per hour
result = limiter.check(scope="user", identifier=str(user_id), endpoint="/api/query")
```

### Global Resource Protection
```python
# Protect external API (OpenAI): 100 requests per hour globally
result = limiter.check(scope="global", identifier="all", endpoint="openai_api")
```

## Step 4: Customizing Limits

To change the default limits (100 req/hour), edit `ratelimiter/limiter.go`:

```go
func DefaultConfig() Config {
    return Config{
        DefaultCapacity:   50,  // Burst capacity: 50 requests at once
        DefaultRefillRate: 50,  // Sustained rate: 50 requests per hour
    }
}
```

Then rebuild:
```bash
cd ratelimiter
go build -o ratelimiter
```

## Step 5: Monitoring Rate Limits

### Check Service Health
```bash
curl http://localhost:8002/health
```

### Check User's Remaining Tokens
```python
status = limiter.get_status(
    scope="user",
    identifier=str(user_id),
    endpoint="/api/sync"
)
print(f"Remaining: {status['remaining']}/{status['limit']}")
print(f"Resets in: {status['reset_after_seconds']} seconds")
```

### Reset a User's Limit (Admin)
```python
result = limiter.reset(
    scope="user",
    identifier=str(user_id),
    endpoint="/api/sync"
)
print(result['message'])
```

## Complete Example: Protected Endpoint

```python
from ratelimiter.client.ratelimiter_client import RateLimiterClient
from fastapi import FastAPI, HTTPException

app = FastAPI()
limiter = RateLimiterClient("http://localhost:8002")

@app.post("/api/protected")
async def protected_endpoint(user_id: int, data: dict):
    """
    A protected endpoint with rate limiting
    - Per-user limit: 100 req/hour
    - Global limit: 1000 req/hour
    """

    # Check per-user limit
    user_result = limiter.check(
        scope="user",
        identifier=str(user_id),
        endpoint="/api/protected"
    )

    if not user_result["allowed"]:
        raise HTTPException(
            status_code=429,
            detail=f"You've made too many requests. Try again in {user_result['retry_after_seconds']} seconds.",
            headers={
                "X-RateLimit-Limit": str(user_result["limit"]),
                "X-RateLimit-Remaining": "0",
                "Retry-After": str(user_result["retry_after_seconds"])
            }
        )

    # Check global limit (protect backend)
    global_result = limiter.check(
        scope="global",
        identifier="all",
        endpoint="/api/protected"
    )

    if not global_result["allowed"]:
        raise HTTPException(
            status_code=503,
            detail="Service is at capacity. Please try again later.",
            headers={"Retry-After": str(global_result["retry_after_seconds"])}
        )

    # Process request
    result = process_data(data)

    # Add rate limit info to response
    return {
        "status": "success",
        "data": result,
        "rate_limit": {
            "remaining": user_result["remaining"],
            "limit": user_result["limit"],
            "reset_in_seconds": user_result["reset_after_seconds"]
        }
    }
```

## Testing the Integration

```bash
# Start rate limiter
cd ratelimiter
./ratelimiter &

# Start Mail Agent
cd ..
python -m uvicorn backend.app:app --reload --port 8000

# Test rate limited endpoint
for i in {1..5}; do
    curl "http://localhost:8000/api/sync?user_id=1"
    echo "---"
done
```

## Troubleshooting

### Rate limiter not working
1. Check if service is running: `curl http://localhost:8002/health`
2. Check logs for errors
3. Python client fails open (allows requests) if service is down

### Different limits needed per endpoint
Use different scope/identifier combinations or modify DefaultConfig in limiter.go

### Need to reset a limit
```python
limiter.reset(scope="user", identifier=str(user_id), endpoint="/api/endpoint")
```

## Production Deployment

For production, run both services together:

```bash
# Start rate limiter
cd ratelimiter
./ratelimiter &

# Start Mail Agent
cd ..
python -m uvicorn backend.app:app --host 0.0.0.0 --port 8000
```

Both services should be managed by systemd, supervisor, or Docker Compose.
