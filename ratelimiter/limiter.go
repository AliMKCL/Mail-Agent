package main

import (
	"fmt"
	"sync"
	"time"
)

// Scope types for flexible rate limiting
const (
	ScopeUser     = "user"     // Per-user limits (e.g., "user:123:/api/sync")
	ScopeGlobal   = "global"   // Global limits across all users (e.g., "global:all:/api/sync")
	ScopeEndpoint = "endpoint" // Per-endpoint limits (e.g., "endpoint:all:/api/sync")
	ScopeCustom   = "custom"   // Custom scope for special cases (e.g., "custom:resource:api_key")
)

// Config holds the rate limiter configuration
type Config struct {
	DefaultCapacity   int64 // Default maximum tokens (allows initial burst)
	DefaultRefillRate int64 // Default tokens added per second
}

// DefaultConfig returns sensible defaults: 100 requests per hour
func DefaultConfig() Config {
	return Config{
		DefaultCapacity:   5, // _ token burst capacity
		DefaultRefillRate: 5, // _ tokens per hour = _/3600 tokens per second
		// Simplified: since we want 100 per hour, we'll use 100 and calculate per-second in bucket
	}
}

// RateLimiter manages multiple token buckets for different scopes
type RateLimiter struct {
	buckets sync.Map // Map of bucket keys to *TokenBucket
	config  Config
	stats   Stats // Global statistics
}

// Stats holds global statistics for monitoring
type Stats struct {
	TotalRequests int64
	AllowedCount  int64
	DeniedCount   int64
	ActiveBuckets int64
	StartTime     time.Time
	mu            sync.Mutex
}

// NewRateLimiter creates a new rate limiter with default configuration
func NewRateLimiter() *RateLimiter {
	return &RateLimiter{
		config: DefaultConfig(),
		stats: Stats{
			StartTime: time.Now(),
		},
	}
}

// NewRateLimiterWithConfig creates a rate limiter with custom configuration
func NewRateLimiterWithConfig(config Config) *RateLimiter {
	return &RateLimiter{
		config: config,
		stats: Stats{
			StartTime: time.Now(),
		},
	}
}

// GenerateKey creates a unique key for a bucket based on scope, identifier, and endpoint
// This is the core of the flexible design - easy to modify for different projects:
//   - Per-user: scope="user", identifier="123", endpoint="/api/sync"
//   - Global: scope="global", identifier="all", endpoint="/api/sync"
//   - Per-endpoint: scope="endpoint", identifier="all", endpoint="/api/sync"
//   - Custom: scope="custom", identifier="any_string", endpoint="resource_name"
func GenerateKey(scope, identifier, endpoint string) string {
	return fmt.Sprintf("%s:%s:%s", scope, identifier, endpoint)
}

// getOrCreateBucket retrieves an existing bucket (by its key in buckets / sync.Map) or creates a new one
// customCapacity and customRefillRate are optional (use nil for defaults)
func (rl *RateLimiter) getOrCreateBucket(key string, customCapacity, customRefillRate *int64) *TokenBucket {
	// Try to load existing bucket
	if bucket, ok := rl.buckets.Load(key); ok {
		return bucket.(*TokenBucket)
	}

	// Determine capacity and refill rate to use
	capacity := rl.config.DefaultCapacity
	refillRate := rl.config.DefaultRefillRate

	if customCapacity != nil && *customCapacity > 0 {
		capacity = *customCapacity
	}
	if customRefillRate != nil && *customRefillRate > 0 {
		refillRate = *customRefillRate
	}

	// Convert refillRate from "per hour" to "per second"
	refillRatePerSecond := float64(refillRate) / 3600.0

	// Create bucket with capacity and refill rate per second
	newBucket := NewTokenBucket(
		capacity,
		refillRatePerSecond, // Tokens per second as float64
	)

	// Try to store the new bucket (another goroutine might have created it)
	actual, loaded := rl.buckets.LoadOrStore(key, newBucket)

	if !loaded {
		// We created a new bucket, increment counter
		rl.stats.mu.Lock()
		rl.stats.ActiveBuckets++
		rl.stats.mu.Unlock()
	}

	return actual.(*TokenBucket)
}

// Check verifies if a request should be allowed based on rate limits
// Returns allowed status, remaining tokens, reset time, and retry time
// customCapacity and customRefillRate are optional (use nil for defaults)
func (rl *RateLimiter) Check(scope, identifier, endpoint string, tokens int64, customCapacity, customRefillRate *int64) (allowed bool, remaining, resetAfter, retryAfter int64) {
	// Generate bucket key
	key := GenerateKey(scope, identifier, endpoint)

	// Get or create bucket with optional custom config
	bucket := rl.getOrCreateBucket(key, customCapacity, customRefillRate)

	// Update stats
	rl.stats.mu.Lock()
	rl.stats.TotalRequests++
	rl.stats.mu.Unlock()

	// Check if request is allowed
	allowed = bucket.Allow(tokens)

	// Get current state
	remaining = bucket.GetRemaining()
	resetAfter = bucket.GetResetAfter()
	retryAfter = bucket.GetRetryAfter()

	// Update stats based on result
	rl.stats.mu.Lock()
	if allowed {
		rl.stats.AllowedCount++
	} else {
		rl.stats.DeniedCount++
	}
	rl.stats.mu.Unlock()

	return allowed, remaining, resetAfter, retryAfter
}

// GetStatus returns the current status of a bucket without consuming tokens
func (rl *RateLimiter) GetStatus(scope, identifier, endpoint string) (remaining, limit, resetAfter int64) {
	key := GenerateKey(scope, identifier, endpoint)

	// Try to load existing bucket
	if bucket, ok := rl.buckets.Load(key); ok {
		b := bucket.(*TokenBucket)
		return b.GetRemaining(), b.GetCapacity(), b.GetResetAfter()
	}

	// Bucket doesn't exist yet, return default capacity
	return rl.config.DefaultCapacity, rl.config.DefaultCapacity, 0
}

// Reset resets a specific bucket to full capacity
func (rl *RateLimiter) Reset(scope, identifier, endpoint string) bool {
	key := GenerateKey(scope, identifier, endpoint)

	if bucket, ok := rl.buckets.Load(key); ok {
		bucket.(*TokenBucket).Reset()
		return true
	}

	return false // Bucket doesn't exist
}

// GetStats returns global statistics
func (rl *RateLimiter) GetStats() StatsResponse {
	rl.stats.mu.Lock()
	defer rl.stats.mu.Unlock()

	uptime := time.Since(rl.stats.StartTime)

	return StatsResponse{
		TotalRequests: rl.stats.TotalRequests,
		Allowed:       rl.stats.AllowedCount,
		Denied:        rl.stats.DeniedCount,
		ActiveBuckets: rl.stats.ActiveBuckets,
		UptimeSeconds: int64(uptime.Seconds()),
	}
}

// StatsResponse holds statistics for API response
type StatsResponse struct {
	TotalRequests int64 `json:"total_requests"`
	Allowed       int64 `json:"allowed"`
	Denied        int64 `json:"denied"`
	ActiveBuckets int64 `json:"active_buckets"`
	UptimeSeconds int64 `json:"uptime_seconds"`
}

// CleanupInactiveBuckets removes buckets that haven't been used recently
// Call this periodically to prevent memory leaks in long-running services
func (rl *RateLimiter) CleanupInactiveBuckets(maxAge time.Duration) int {
	removed := 0
	now := time.Now()

	rl.buckets.Range(func(key, value interface{}) bool {
		bucket := value.(*TokenBucket)
		bucket.mu.Lock()

		// Check if bucket hasn't been accessed recently
		if now.Sub(bucket.lastRefill) > maxAge {
			bucket.mu.Unlock()
			rl.buckets.Delete(key)
			removed++

			// Update stats
			rl.stats.mu.Lock()
			rl.stats.ActiveBuckets--
			rl.stats.mu.Unlock()
		} else {
			bucket.mu.Unlock()
		}

		return true // Continue iteration
	})

	return removed
}
