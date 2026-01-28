# Rate Limiter Quickstart Guide

## What is This?

A standalone Go microservice for rate limiting any REST API. Uses the Token Bucket algorithm for flexible, accurate rate limiting.

**Key Features:**
- ✅ **Plug-and-play** - Copy to any project, works out of the box
- ✅ **Zero config** - Sensible defaults (100 req/hour)
- ✅ **Flexible scoping** - Per-user, global, per-endpoint, or custom
- ✅ **Fast** - In-memory, handles thousands of requests/sec
- ✅ **Language agnostic** - HTTP REST API, works with any framework

## 30-Second Start

```bash
# 1. Start the service
cd ratelimiter
go run .

# 2. Test it
curl -X POST http://localhost:8002/check \
  -H "Content-Type: application/json" \
  -d '{"scope":"user","identifier":"123","endpoint":"/api/test","tokens":1}'

# Response:
# {"allowed":true,"remaining":99,"limit":100,"reset_after_seconds":3600,"retry_after_seconds":0}
```

## Integration (Python/FastAPI)

```python
from ratelimiter.client.ratelimiter_client import RateLimiterClient

limiter = RateLimiterClient("http://localhost:8002")

@app.get("/api/endpoint")
async def my_endpoint(user_id: int):
    # Check rate limit
    result = limiter.check("user", str(user_id), "/api/endpoint")

    if not result["allowed"]:
        raise HTTPException(429, "Rate limited")

    # Your code here
    return {"status": "ok"}
```

## Common Use Cases

### Per-User Rate Limiting
Prevent individual users from abusing your API:
```python
limiter.check(scope="user", identifier=str(user_id), endpoint="/api/sync")
```

### Global Rate Limiting
Protect backend services from total load:
```python
limiter.check(scope="global", identifier="all", endpoint="/api/expensive")
```

### Cost Limiting (e.g., OpenAI API)
Track and limit expensive external API calls:
```python
limiter.check(scope="custom", identifier="openai_api", endpoint="gpt4_calls")
```

## File Structure

```
ratelimiter/
├── bucket.go              # Token bucket algorithm
├── limiter.go             # Rate limiter manager
├── handlers.go            # HTTP API handlers
├── models.go              # Request/response structs
├── logger.go              # Logging utility
├── main.go                # Server entry point
├── go.mod                 # Go module file
├── client/
│   └── ratelimiter_client.py  # Python client library
├── README.md              # Full documentation
├── INTEGRATION_EXAMPLE.md # Mail Agent integration examples
└── QUICKSTART.md          # This file
```

## API Endpoints

- `POST /check` - Check if request allowed, consume tokens
- `GET /status` - Get bucket status (doesn't consume tokens)
- `DELETE /reset` - Reset bucket to full capacity
- `GET /health` - Health check and stats
- `GET /` - API documentation

## Configuration

**Default:** 100 requests per hour, burst capacity of 100

**To change:** Edit `DefaultConfig()` in `limiter.go`:
```go
func DefaultConfig() Config {
    return Config{
        DefaultCapacity:   50,  // Burst capacity
        DefaultRefillRate: 50,  // Requests per hour
    }
}
```

## Scope Types

| Scope | Use Case | Example |
|-------|----------|---------|
| `user` | Per-user limits | Prevent spam from single user |
| `global` | Total system limit | Protect downstream services |
| `endpoint` | Per-endpoint limit | Limit expensive operations |
| `custom` | Flexible custom limits | API keys, IP addresses, etc. |

## Testing

```bash
# Run test suite
python3 test_ratelimiter.py

# Manual test
curl http://localhost:8002/health
```

## Production Deployment

```bash
# Build binary
go build -o ratelimiter

# Run in background
./ratelimiter &

# Check logs
tail -f /path/to/logs
```

## Copy to Another Project

1. Copy entire `ratelimiter/` directory
2. Start the service: `go run .`
3. Import Python client in your code
4. Call `limiter.check()` before processing requests

That's it! No configuration, no database, no external dependencies.

## Full Documentation

- **README.md** - Complete documentation with all features
- **INTEGRATION_EXAMPLE.md** - Mail Agent integration examples
- API documentation: Visit `http://localhost:8002` when running

## Questions?

The design is intentionally simple and self-contained. Read through:
1. `bucket.go` - Token bucket algorithm (~120 lines)
2. `limiter.go` - Rate limiter manager (~150 lines)
3. `handlers.go` - HTTP API (~120 lines)

Total: ~400 lines of Go code for full functionality.
