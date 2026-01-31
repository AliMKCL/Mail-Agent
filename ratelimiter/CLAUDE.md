# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

A standalone Go microservice for flexible, high-performance rate limiting using the Token Bucket algorithm. Designed to be plug-and-play with any REST API framework (FastAPI, Flask, Express, etc.). The service operates as an independent HTTP server that other applications call to check rate limits before processing requests.

**Tech Stack:**
- Go 1.25+ (HTTP server, port 8002)
- Token Bucket algorithm (in-memory state)
- Python client library (optional, for FastAPI/Python integration)
- Zero external dependencies

## Running the Service

### Start the Rate Limiter Server

```bash
# From the ratelimiter directory
go run .
```

Server runs on port 8002 by default.

### Build Production Binary

```bash
go build -o ratelimiter
./ratelimiter &
```

### Verify Service is Running

```bash
curl http://localhost:8002/health
```

## Testing

### Manual Testing with curl

```bash
# Test rate limit check (per-user)
curl -X POST http://localhost:8002/check \
  -H "Content-Type: application/json" \
  -d '{"scope":"user","identifier":"123","endpoint":"/api/test","tokens":1}'

# Check bucket status without consuming tokens
curl "http://localhost:8002/status?scope=user&identifier=123&endpoint=/api/test"

# Reset a bucket (admin operation)
curl -X DELETE "http://localhost:8002/reset?scope=user&identifier=123&endpoint=/api/test"

# Health check and statistics
curl http://localhost:8002/health
```

### Test Script

```bash
# Test from parent directory
python test_ratelimiter.py

# Or test from project root
python -m test_ratelimiter
```

### Load Testing

```bash
# Test rate limiting behavior (should allow 5, then deny)
for i in {1..10}; do
  curl -X POST http://localhost:8002/check \
    -H "Content-Type: application/json" \
    -d '{"scope":"user","identifier":"test_user","endpoint":"/test","tokens":1}' \
    -s | jq '.allowed, .remaining'
done
```

## Architecture Overview

### Core Components

**main.go** - HTTP server entry point
- Registers HTTP handlers for all endpoints
- Starts cleanup goroutine (removes inactive buckets every hour)
- Handles graceful shutdown on SIGINT/SIGTERM
- Default port: 8002

**limiter.go** - Rate limiter manager
- `RateLimiter` struct manages multiple token buckets using `sync.Map`
- `GenerateKey()` creates unique bucket keys from (scope, identifier, endpoint)
- `getOrCreateBucket()` lazy-creates buckets with default config
- `Check()` verifies if request allowed, consumes tokens if yes
- `GetStatus()` returns bucket state without consuming tokens
- `CleanupInactiveBuckets()` removes buckets inactive for 24+ hours

**bucket.go** - Token Bucket algorithm implementation
- `TokenBucket` struct with capacity, tokens, refillRate, lastRefill
- `refillTokens()` adds tokens based on elapsed time (called before each check)
- `Allow()` checks availability and consumes tokens atomically
- Thread-safe with per-bucket mutexes (fine-grained locking)

**handlers.go** - HTTP API handlers
- `POST /check` - Check rate limit and consume tokens
- `GET /status` - Get bucket status (no token consumption)
- `DELETE /reset` - Reset bucket to full capacity
- `GET /health` - Service health and statistics
- `GET /` - API documentation

**models.go** - Request/response structs
- `CheckRequest`, `CheckResponse`, `StatusResponse`, `ResetResponse`, `HealthResponse`, `ErrorResponse`

**logger.go** - Logging utility
- Structured logging for rate limit events
- `LogRateLimitAllowed()`, `LogRateLimitViolation()`

**client/ratelimiter_client.py** - Python client library
- `RateLimiterClient` class for easy integration with Python applications
- Fail-open design: if service is unavailable, allows requests
- Helper functions for FastAPI decorator and dependency injection

### Token Bucket Algorithm

1. Each bucket starts with **capacity** tokens (default: 5)
2. Tokens refill at **refill rate** (default: 5 per hour = 0.00139 per second)
3. Each request consumes tokens (default: 1 token)
4. Request allowed only if sufficient tokens available
5. Bucket never exceeds capacity

**Benefits:**
- Allows natural bursts (user can use full capacity if bucket is full)
- Enforces average rate over time (sustained rate = refill rate)
- Simple and efficient (just 4 values: capacity, tokens, refillRate, lastRefill)

### Scope Types

The flexible scoping system allows different rate limiting strategies:

| Scope | Identifier | Endpoint | Use Case |
|-------|------------|----------|----------|
| `user` | user_id (e.g., "123") | endpoint path | Per-user limits (prevent individual abuse) |
| `global` | "all" | endpoint path | Total system limit (protect downstream services) |
| `endpoint` | "all" | endpoint path | Per-endpoint limits (same as global for single endpoint) |
| `custom` | any string (e.g., IP, API key) | resource name | Flexible custom limits (cost limiting, IP blocking) |

**Bucket Key Format:** `{scope}:{identifier}:{endpoint}`

Examples:
- Per-user: `user:123:/api/sync`
- Global: `global:all:/api/sync`
- Custom: `custom:192.168.1.1:/api/public`

### Concurrency Model

- **sync.Map** - Thread-safe map for bucket storage (Go built-in)
- **Mutex per bucket** - Each bucket has its own lock (fine-grained locking)
- **No global lock** - High parallelism for concurrent requests
- **Atomic operations** - Token refill and consumption are atomic

### Memory Management

- Buckets are created lazily (only when first accessed)
- Cleanup goroutine runs every hour, removes buckets inactive for 24+ hours
- Typical memory: ~112 bytes per bucket (struct + sync.Map overhead)
- Example: 10,000 buckets ≈ 1.1 MB

## Configuration

### Default Configuration

Edit `DefaultConfig()` in `limiter.go` to change the default for all buckets:

```go
func DefaultConfig() Config {
    return Config{
        DefaultCapacity:   100,  // Burst capacity
        DefaultRefillRate: 100,  // Requests per hour (converted to per-second internally)
    }
}
```

After changing, rebuild:
```bash
go build -o ratelimiter
```

**Current default:** 5 requests per hour, burst capacity of 5

### Per-Endpoint Configuration (Recommended)

You can override the default configuration **per bucket** by passing `capacity` and `refill_rate` parameters:

```python
# Light endpoint: 1000 requests per hour
limiter.check(
    scope="user",
    identifier=str(user_id),
    endpoint="/api/list",
    capacity=1000,
    refill_rate=1000
)

# Heavy endpoint: 10 requests per hour
limiter.check(
    scope="user",
    identifier=str(user_id),
    endpoint="/api/sync",
    capacity=10,
    refill_rate=10
)

# Use defaults (5 req/hour)
limiter.check(
    scope="user",
    identifier=str(user_id),
    endpoint="/api/other"
)
```

**Important:** Config is set **only when the bucket is first created**. Subsequent requests to the same (scope, identifier, endpoint) use the existing bucket's config.

### Changing Server Port

Edit `DefaultPort` in `main.go`:

```go
const (
    DefaultPort = 8002
    Version     = "1.0.0"
)
```

## Integration Patterns

### Python/FastAPI Integration

**Initialize client:**
```python
from ratelimiter.client.ratelimiter_client import RateLimiterClient

limiter = RateLimiterClient("http://localhost:8002")
```

**Per-user rate limiting with custom limits:**
```python
@app.get("/api/emails")
async def get_emails(user_id: int):
    # Light endpoint: 1000 requests per hour
    result = limiter.check(
        scope="user",
        identifier=str(user_id),
        endpoint="/api/emails",
        capacity=1000,
        refill_rate=1000
    )

    if not result["allowed"]:
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded. Try again in {result['retry_after_seconds']}s.",
            headers={"Retry-After": str(result["retry_after_seconds"])}
        )

    # Process request
    return {"emails": [...]}
```

**Heavy endpoint with strict limits:**
```python
@app.post("/api/sync")
async def sync_emails(user_id: int):
    # Heavy operation: 10 requests per hour
    result = limiter.check(
        scope="user",
        identifier=str(user_id),
        endpoint="/api/sync",
        capacity=10,
        refill_rate=10
    )

    if not result["allowed"]:
        raise HTTPException(
            status_code=429,
            detail=f"Sync limited to 10 per hour. Wait {result['retry_after_seconds']}s.",
            headers={"Retry-After": str(result["retry_after_seconds"])}
        )

    # Process sync
    return {"status": "syncing"}
```

**Global endpoint limiting:**
```python
@app.post("/api/expensive")
async def expensive_operation(user_id: int):
    # Global limit: 100 requests per hour across all users
    result = limiter.check(
        scope="global",
        identifier="all",
        endpoint="/api/expensive",
        capacity=100,
        refill_rate=100
    )

    if not result["allowed"]:
        raise HTTPException(503, "Service at capacity. Try again later.")

    # Process request
    return {"status": "ok"}
```

**Server-wide limiting (shared bucket across endpoints):**
```python
# Add to multiple endpoints with same endpoint key
result = limiter.check(
    scope="global",
    identifier="all",
    endpoint="server_total",  # Same key for all endpoints
    capacity=500,
    refill_rate=500
)
```

### Using Python Decorators

```python
from ratelimiter.client.ratelimiter_client import rate_limited

limiter = RateLimiterClient()

@app.get("/api/sync")
@rate_limited(limiter, scope="user", identifier_key="user_id")
async def sync_emails(user_id: int):
    return {"status": "ok"}
```

### Using FastAPI Dependencies

```python
from ratelimiter.client.ratelimiter_client import create_rate_limit_dependency
from fastapi import Depends

limiter = RateLimiterClient()
rate_limit = create_rate_limit_dependency(limiter, scope="user")

@app.get("/api/sync", dependencies=[Depends(rate_limit)])
async def sync_emails(user_id: int):
    return {"status": "ok"}
```

## API Response Format

### Successful Check (200 OK)
```json
{
  "allowed": true,
  "remaining": 4,
  "limit": 5,
  "reset_after_seconds": 3600,
  "retry_after_seconds": 0
}
```

### Rate Limited (429 Too Many Requests)
```json
{
  "allowed": false,
  "remaining": 0,
  "limit": 5,
  "reset_after_seconds": 2834,
  "retry_after_seconds": 721
}
```

**Headers:** `Retry-After` header is set with retry_after_seconds value

### Health Check (200 OK)
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

## Common Development Tasks

### Monitoring Active Buckets

```bash
# View health and active bucket count
curl http://localhost:8002/health | jq '.stats.active_buckets'
```

### Debugging Rate Limit Issues

```bash
# Check specific bucket status without consuming tokens
curl "http://localhost:8002/status?scope=user&identifier=123&endpoint=/api/sync" | jq
```

### Resetting a User's Limit (Admin)

```bash
# Reset via curl
curl -X DELETE "http://localhost:8002/reset?scope=user&identifier=123&endpoint=/api/sync"
```

```python
# Reset via Python client
limiter.reset(scope="user", identifier="123", endpoint="/api/sync")
```

### Viewing Logs

The service logs to stdout:
- Rate limit violations (denied requests)
- Allowed requests with remaining tokens
- Bucket cleanup operations
- Service startup/shutdown

```bash
# Run with output redirection
./ratelimiter > ratelimiter.log 2>&1 &

# View logs
tail -f ratelimiter.log
```

## Deployment

### Development
```bash
cd ratelimiter
go run .
```

### Production (Background Process)
```bash
cd ratelimiter
go build -o ratelimiter
nohup ./ratelimiter > ratelimiter.log 2>&1 &
```

### Systemd Service (Linux)

Create `/etc/systemd/system/ratelimiter.service`:
```ini
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

## Troubleshooting

### Service won't start
- Check if port 8002 is in use: `lsof -i :8002`
- Try different port (edit `DefaultPort` in `main.go`)

### Rate limits not working
- Verify service is running: `curl http://localhost:8002/health`
- Check scope/identifier/endpoint combination matches between requests
- Verify Python client is pointing to correct URL (default: `http://localhost:8002`)

### Python client always allows requests
- Python client fails open (allows requests) if rate limiter is unavailable
- Check if rate limiter service is running
- Check firewall/network connectivity

### Memory usage growing
- Cleanup runs automatically every hour for buckets inactive >24h
- Check active buckets: `curl http://localhost:8002/health | jq '.stats.active_buckets'`
- Adjust cleanup interval in `main.go` if needed

## File Structure

```
ratelimiter/
├── main.go                       # HTTP server entry point
├── limiter.go                    # Rate limiter manager (sync.Map, bucket lifecycle)
├── bucket.go                     # Token bucket algorithm
├── handlers.go                   # HTTP API handlers
├── models.go                     # Request/response structs
├── logger.go                     # Logging utility
├── go.mod                        # Go module definition
├── client/
│   └── ratelimiter_client.py    # Python client library
├── README.md                     # Complete documentation
├── USAGE_GUIDE.md               # Quick usage patterns
├── zQUICKSTART.md               # 30-second quickstart
└── zINTEGRATION_EXAMPLE.md      # Mail Agent integration examples
```

Total codebase: ~600 lines of Go + ~280 lines of Python client

## Design Philosophy

- **Plug-and-play:** Copy directory to any project, start server, integrate
- **Zero configuration:** Works with sensible defaults (5 req/hour currently)
- **Language agnostic:** Simple HTTP REST API, works with any language
- **Fail-open:** Python client allows requests if service is down
- **High performance:** In-memory state, fine-grained locking, minimal overhead
- **Self-contained:** No external dependencies, single binary deployment
