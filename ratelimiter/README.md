# Rate Limiter Microservice

A plug-and-play Go microservice for flexible, high-performance rate limiting using the Token Bucket algorithm. Works with any REST API framework (FastAPI, Flask, Express, etc.).

## Features

- **Token Bucket Algorithm** - Allows natural request bursts while enforcing average rate
- **Flexible Scoping** - Per-user, global, per-endpoint, or custom rate limiting
- **Zero Configuration** - Works out of the box with sensible defaults (100 req/hour)
- **High Performance** - In-memory state with mutex-based concurrency, handles thousands of requests/sec
- **Language Agnostic** - Simple HTTP REST API, integrate from any language
- **Fail-Open Design** - If service is down, requests proceed (Python client handles gracefully)
- **Plug-and-Play** - Copy to any project, no external dependencies

## Quick Start

### 1. Start the Go Server

```bash
cd ratelimiter
go run .
```

Server runs on port **8002** by default.

### 2. Test with curl

```bash
# Check if request is allowed (per-user rate limiting)
curl -X POST http://localhost:8002/check \
  -H "Content-Type: application/json" \
  -d '{
    "scope": "user",
    "identifier": "123",
    "endpoint": "/api/sync",
    "tokens": 1
  }'

# Response (200 OK if allowed):
{
  "allowed": true,
  "remaining": 99,
  "limit": 100,
  "reset_after_seconds": 3600,
  "retry_after_seconds": 0
}

# Get bucket status (doesn't consume tokens)
curl "http://localhost:8002/status?scope=user&identifier=123&endpoint=/api/sync"

# Health check
curl http://localhost:8002/health
```

### 3. Integrate with Python/FastAPI

```python
from ratelimiter.client.ratelimiter_client import RateLimiterClient
from fastapi import FastAPI, HTTPException

app = FastAPI()
limiter = RateLimiterClient("http://localhost:8002")

@app.get("/api/sync")
async def sync_emails(user_id: int):
    # Check rate limit
    result = limiter.check(
        scope="user",
        identifier=str(user_id),
        endpoint="/api/sync"
    )

    if not result["allowed"]:
        raise HTTPException(
            status_code=429,
            detail="Rate limit exceeded. Try again later.",
            headers={
                "Retry-After": str(result["retry_after_seconds"])
            }
        )

    # Process request
    return {"status": "syncing emails"}
```

## API Reference

### POST /check

Check if a request should be allowed based on rate limits.

**Request Body:**
```json
{
  "scope": "user",           // "user", "global", "endpoint", "custom"
  "identifier": "123",       // User ID, "all", or custom string
  "endpoint": "/api/sync",   // API endpoint or resource name
  "tokens": 1                // Tokens to consume (optional, default: 1)
}
```

**Response (200 OK if allowed, 429 Too Many Requests if denied):**
```json
{
  "allowed": true,
  "remaining": 49,
  "limit": 100,
  "reset_after_seconds": 3600,
  "retry_after_seconds": 0
}
```

### GET /status

Get current status of a rate limit bucket without consuming tokens.

**Query Parameters:**
- `scope` - Scope type
- `identifier` - Identifier
- `endpoint` - Endpoint

**Response (200 OK):**
```json
{
  "scope": "user",
  "identifier": "123",
  "endpoint": "/api/sync",
  "remaining": 50,
  "limit": 100,
  "reset_after_seconds": 3600
}
```

### DELETE /reset

Reset a rate limit bucket to full capacity (admin operation).

**Query Parameters:**
- `scope` - Scope type
- `identifier` - Identifier
- `endpoint` - Endpoint

**Response (200 OK):**
```json
{
  "success": true,
  "message": "Rate limit bucket reset successfully"
}
```

### GET /health

Health check and service statistics.

**Response (200 OK):**
```json
{
  "status": "healthy",
  "version": "1.0.0",
  "stats": {
    "total_requests": 1234,
    "allowed": 1200,
    "denied": 34,
    "active_buckets": 42,
    "uptime_seconds": 86400
  }
}
```

## Scope Types Explained

The rate limiter supports flexible scoping - choose what fits your project:

### Per-User Rate Limiting
Each user has independent limits.

```python
# User 123 can make 100 req/hour to /api/sync
limiter.check(scope="user", identifier="123", endpoint="/api/sync")

# User 456 has separate limit
limiter.check(scope="user", identifier="456", endpoint="/api/sync")
```

**Use case:** Prevent individual users from abusing your API

### Global Rate Limiting
Total limit across ALL users.

```python
# All users combined can make 1000 req/hour to /api/sync
limiter.check(scope="global", identifier="all", endpoint="/api/sync")
```

**Use case:** Protect downstream services (external APIs, databases) from total load

### Per-Endpoint Rate Limiting
Each endpoint has independent limits (all users share).

```python
# All users combined: 500 req/hour to /api/sync
limiter.check(scope="endpoint", identifier="all", endpoint="/api/sync")

# Different limit for different endpoint
limiter.check(scope="endpoint", identifier="all", endpoint="/api/emails")
```

**Use case:** Limit expensive operations regardless of who calls them

### Custom Rate Limiting
Flexible custom scopes for special resources.

```python
# Limit API key usage
limiter.check(scope="custom", identifier="api_key_abc123", endpoint="openai_api")

# Limit by IP address
limiter.check(scope="custom", identifier="192.168.1.1", endpoint="/api/public")
```

**Use case:** Cost limiting (e.g., OpenAI API calls), IP-based rate limiting

## Configuration

### Default Configuration

- **Capacity:** 100 tokens
- **Refill Rate:** 100 tokens per hour (allows 100 requests/hour)
- **Algorithm:** Token Bucket (allows bursts up to capacity)

### Modifying for Different Projects

The rate limiter is designed to be easily customized. To change limits per project:

**Option 1: Modify defaults in code (limiter.go)**
```go
func DefaultConfig() Config {
    return Config{
        DefaultCapacity:   50,  // Change burst capacity
        DefaultRefillRate: 50,  // Change sustained rate (per hour)
    }
}
```

**Option 2: Use different scopes per endpoint**
```python
# Light endpoint: per-user limit
limiter.check(scope="user", identifier=user_id, endpoint="/api/emails")

# Heavy endpoint: global limit
limiter.check(scope="global", identifier="all", endpoint="/api/sync")
```

**Option 3: Implement custom configuration loader**
```go
// Load from environment variables
capacity := os.Getenv("RATE_LIMIT_CAPACITY")
refillRate := os.Getenv("RATE_LIMIT_REFILL_RATE")
```

## Integration Examples

### FastAPI with Decorator

```python
from ratelimiter.client.ratelimiter_client import rate_limited, RateLimiterClient

limiter = RateLimiterClient()

@app.get("/api/sync")
@rate_limited(limiter, scope="user", identifier_key="user_id")
async def sync_emails(user_id: int):
    return {"status": "ok"}
```

### FastAPI with Dependency Injection

```python
from ratelimiter.client.ratelimiter_client import create_rate_limit_dependency, RateLimiterClient
from fastapi import Depends

limiter = RateLimiterClient()
rate_limit = create_rate_limit_dependency(limiter, scope="user")

@app.get("/api/sync", dependencies=[Depends(rate_limit)])
async def sync_emails(user_id: int, request: Request):
    # Request proceeds only if rate limit allows
    return {"status": "ok"}
```

### Manual Check with Custom Logic

```python
@app.get("/api/expensive-operation")
async def expensive_operation(user_id: int):
    # Check rate limit
    result = limiter.check(scope="user", identifier=str(user_id), endpoint="/api/expensive")

    if not result["allowed"]:
        # Custom error handling
        return JSONResponse(
            status_code=429,
            content={
                "error": "Rate limit exceeded",
                "retry_after": result["retry_after_seconds"],
                "remaining": 0
            },
            headers={"Retry-After": str(result["retry_after_seconds"])}
        )

    # Add rate limit info to response headers
    headers = {
        "X-RateLimit-Limit": str(result["limit"]),
        "X-RateLimit-Remaining": str(result["remaining"]),
        "X-RateLimit-Reset": str(result["reset_after_seconds"])
    }

    return JSONResponse({"status": "ok"}, headers=headers)
```

### Combining Multiple Scopes

```python
@app.post("/api/send-email")
async def send_email(user_id: int):
    # Check per-user limit (prevent spam from single user)
    user_result = limiter.check(scope="user", identifier=str(user_id), endpoint="/api/send-email")
    if not user_result["allowed"]:
        raise HTTPException(429, "You're sending too many emails. Please wait.")

    # Check global limit (protect email server)
    global_result = limiter.check(scope="global", identifier="all", endpoint="/api/send-email")
    if not global_result["allowed"]:
        raise HTTPException(503, "Email service is at capacity. Try again later.")

    # Send email
    return {"status": "email sent"}
```

## Architecture

### How Token Bucket Works

1. Each bucket starts with **capacity** tokens (e.g., 100)
2. Tokens refill at **refill rate** (e.g., 100 per hour = 1 every 36 seconds)
3. Each request consumes tokens (default: 1 token)
4. Request allowed only if sufficient tokens available
5. Bucket never exceeds capacity (prevents hoarding)

**Benefits:**
- Allows natural bursts (user can make 100 requests immediately if bucket is full)
- Enforces average rate over time (sustained rate = refill rate)
- Simple and efficient (just 3 numbers: capacity, tokens, last_refill_time)

### Concurrency Model

- **sync.Map** - Concurrent map for bucket storage (Go built-in)
- **Mutex per bucket** - Each bucket has its own lock (fine-grained locking)
- **Thread-safe** - Safe for concurrent access from multiple goroutines
- **No global lock** - High parallelism (multiple requests processed simultaneously)

### Memory Usage

For 10,000 users × 5 endpoints = 50,000 buckets:
- ~64 bytes per bucket (struct + mutex)
- ~48 bytes per sync.Map entry (overhead)
- **Total: ~5.6 MB** (negligible for modern systems)

### Cleanup

- Inactive buckets (no access for 24 hours) are automatically removed every hour
- Prevents memory leaks in long-running services
- Configurable in `main.go`

## Testing

### Manual Testing

```bash
# Test per-user rate limiting (should allow 100, then deny)
for i in {1..105}; do
  curl -X POST http://localhost:8002/check \
    -H "Content-Type: application/json" \
    -d '{"scope":"user","identifier":"test_user","endpoint":"/test","tokens":1}' \
    -s | jq '.allowed, .remaining'
done

# Test global rate limiting
for i in {1..10}; do
  curl -X POST http://localhost:8002/check \
    -H "Content-Type: application/json" \
    -d '{"scope":"global","identifier":"all","endpoint":"/test","tokens":1}' \
    -s | jq
done

# Check health and stats
curl http://localhost:8002/health | jq
```

### Python Test Script

```python
from ratelimiter.client.ratelimiter_client import RateLimiterClient

limiter = RateLimiterClient()

# Test 1: Basic check
print("Test 1: Basic check")
for i in range(5):
    result = limiter.check("user", "test_user", "/api/test")
    print(f"Request {i+1}: allowed={result['allowed']}, remaining={result['remaining']}")

# Test 2: Different users
print("\nTest 2: Different users have independent limits")
result1 = limiter.check("user", "user_1", "/api/test")
result2 = limiter.check("user", "user_2", "/api/test")
print(f"User 1: remaining={result1['remaining']}")
print(f"User 2: remaining={result2['remaining']}")

# Test 3: Global limit
print("\nTest 3: Global limit affects all users")
result = limiter.check("global", "all", "/api/heavy")
print(f"Global: allowed={result['allowed']}, remaining={result['remaining']}")

# Test 4: Health check
print("\nTest 4: Health check")
health = limiter.health()
print(f"Status: {health['status']}")
print(f"Stats: {health['stats']}")
```

## Deployment

### Development

```bash
cd ratelimiter
go run .
```

### Production

```bash
# Build binary
cd ratelimiter
go build -o ratelimiter

# Run in background
./ratelimiter &

# Or with nohup
nohup ./ratelimiter > ratelimiter.log 2>&1 &
```

### Docker (Optional)

```dockerfile
FROM golang:1.25-alpine AS builder
WORKDIR /app
COPY . .
RUN go build -o ratelimiter

FROM alpine:latest
WORKDIR /app
COPY --from=builder /app/ratelimiter .
EXPOSE 8002
CMD ["./ratelimiter"]
```

```bash
docker build -t ratelimiter .
docker run -p 8002:8002 ratelimiter
```

### Systemd Service (Linux)

```ini
# /etc/systemd/system/ratelimiter.service
[Unit]
Description=Rate Limiter Microservice
After=network.target

[Service]
Type=simple
User=your_user
WorkingDirectory=/path/to/ratelimiter
ExecStart=/path/to/ratelimiter/ratelimiter
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable ratelimiter
sudo systemctl start ratelimiter
sudo systemctl status ratelimiter
```

## Copying to Other Projects

This rate limiter is designed to be plug-and-play:

1. **Copy the entire `ratelimiter/` directory** to your project
2. **Start the server** alongside your main application
3. **Import the Python client** (or create client in your language)
4. **Call `check()`** before processing requests

**No modifications needed** - just copy and run!

## Customization for Different Use Cases

### Example 1: Strict Per-Endpoint Limits

Prevent expensive operations from overwhelming your server:

```python
# Heavy operation: 10 requests per hour globally
limiter.check(scope="endpoint", identifier="all", endpoint="/api/expensive")
```

Modify `DefaultConfig()` in `limiter.go`:
```go
return Config{
    DefaultCapacity:   10,
    DefaultRefillRate: 10,  // 10 per hour
}
```

### Example 2: Per-IP Rate Limiting

Protect public endpoints from abuse:

```python
@app.get("/api/public")
async def public_endpoint(request: Request):
    client_ip = request.client.host

    result = limiter.check(scope="custom", identifier=client_ip, endpoint="/api/public")
    if not result["allowed"]:
        raise HTTPException(429, "Too many requests from your IP")

    return {"status": "ok"}
```

### Example 3: Tiered Rate Limiting

Different limits for different user tiers:

```python
async def get_user_tier(user_id: int) -> str:
    # Fetch from database
    return "premium"  # or "free"

@app.get("/api/data")
async def get_data(user_id: int):
    tier = await get_user_tier(user_id)

    # Premium users: 1000 req/hour, Free users: 100 req/hour
    result = limiter.check(scope=tier, identifier=str(user_id), endpoint="/api/data")

    if not result["allowed"]:
        raise HTTPException(429, f"Rate limit exceeded for {tier} tier")

    return {"data": "..."}
```

## Troubleshooting

### Service won't start
- Check if port 8002 is already in use: `lsof -i :8002`
- Try a different port by modifying `DefaultPort` in `main.go`

### Rate limits not working
- Verify server is running: `curl http://localhost:8002/health`
- Check logs for errors
- Verify you're using correct scope/identifier/endpoint combination

### Python client fails
- Check if rate limiter is reachable: `curl http://localhost:8002/health`
- Python client fails open (allows requests) if service is down
- Check timeout setting in RateLimiterClient

### Memory usage growing
- Cleanup runs every hour automatically
- Check active buckets: `curl http://localhost:8002/health | jq '.stats.active_buckets'`
- Adjust cleanup interval in `main.go` if needed

## License

This is a personal project tool - use freely in your projects!

## Questions?

Rate limiting concepts:
- Token Bucket: https://en.wikipedia.org/wiki/Token_bucket
- Leaky Bucket: https://en.wikipedia.org/wiki/Leaky_bucket

Go concurrency patterns:
- sync.Map: https://pkg.go.dev/sync#Map
- Mutexes: https://gobyexample.com/mutexes
