package main

import (
	"sync"
	"time"
)

// TokenBucket implements the token bucket rate limiting algorithm
// Tokens are added at a constant rate (refillRate per second)
// Each request consumes tokens from the bucket
// Requests are allowed only if sufficient tokens are available
type TokenBucket struct {
	capacity   int64      // Maximum number of tokens the bucket can hold
	tokens     float64    // Current number of tokens in the bucket (float for fractional refill)
	refillRate float64    // Tokens added per second
	lastRefill time.Time  // Last time tokens were refilled
	mu         sync.Mutex // Mutex for thread-safe operations
}

// NewTokenBucket creates a new token bucket with the given capacity and refill rate
// capacity: maximum tokens the bucket can hold (allows bursts up to this amount)
// refillRate: tokens added per second (e.g., 100 tokens/hour = 100/3600 ≈ 0.028 tokens/sec)
func NewTokenBucket(capacity int64, refillRate float64) *TokenBucket {
	return &TokenBucket{
		capacity:   capacity,
		tokens:     float64(capacity), // Start with full bucket
		refillRate: refillRate,
		lastRefill: time.Now(),
	}
}

// refillTokens adds tokens based on elapsed time since last refill
// This is called internally before checking if tokens are available
// Must be called with lock held
func (b *TokenBucket) refillTokens() {
	now := time.Now()
	elapsed := now.Sub(b.lastRefill)

	// Calculate tokens to add based on elapsed time
	// tokensToAdd = (elapsed seconds) × (tokens per second)
	tokensToAdd := elapsed.Seconds() * b.refillRate

	if tokensToAdd > 0 {
		b.tokens = minFloat(float64(b.capacity), b.tokens+tokensToAdd)
		b.lastRefill = now
	}
}

// Allow checks if the request can be allowed and consumes tokens if available
// tokens: number of tokens to consume (typically 1 per request)
// Returns true if tokens were available and consumed, false otherwise
func (b *TokenBucket) Allow(tokens int64) bool {
	b.mu.Lock()
	defer b.mu.Unlock()

	// Refill tokens based on elapsed time
	b.refillTokens()

	// Check if enough tokens are available
	if b.tokens >= float64(tokens) {
		b.tokens -= float64(tokens)
		return true
	}

	return false
}

// GetRemaining returns the current number of available tokens
func (b *TokenBucket) GetRemaining() int64 {
	b.mu.Lock()
	defer b.mu.Unlock()

	// Refill before returning count
	b.refillTokens()

	return int64(b.tokens)
}

// GetResetAfter returns the number of seconds until the bucket is full again
func (b *TokenBucket) GetResetAfter() int64 {
	b.mu.Lock()
	defer b.mu.Unlock()

	// Refill first to get accurate count
	b.refillTokens()

	if b.tokens >= float64(b.capacity) {
		return 0 // Already full
	}

	// Calculate time needed to fill remaining tokens
	tokensNeeded := float64(b.capacity) - b.tokens
	if b.refillRate == 0 {
		return 0
	}

	secondsNeeded := tokensNeeded / b.refillRate
	return int64(secondsNeeded) + 1 // Add 1 second buffer
}

// GetRetryAfter returns seconds until at least one token is available
func (b *TokenBucket) GetRetryAfter() int64 {
	b.mu.Lock()
	defer b.mu.Unlock()

	// Refill first
	b.refillTokens()

	if b.tokens >= 1.0 {
		return 0 // Tokens available now
	}

	// Calculate time for next token
	if b.refillRate == 0 {
		return 0
	}

	tokensNeeded := 1.0 - b.tokens
	secondsNeeded := tokensNeeded / b.refillRate
	return int64(secondsNeeded) + 1 // Add 1 for safety margin
}

// Reset resets the bucket to full capacity
func (b *TokenBucket) Reset() {
	b.mu.Lock()
	defer b.mu.Unlock()

	b.tokens = float64(b.capacity)
	b.lastRefill = time.Now()
}

// GetCapacity returns the maximum capacity of the bucket
func (b *TokenBucket) GetCapacity() int64 {
	return b.capacity
}

// Helper function for min of two int64 values
func min(a, b int64) int64 {
	if a < b {
		return a
	}
	return b
}

// Helper function for min of two float64 values
func minFloat(a, b float64) float64 {
	if a < b {
		return a
	}
	return b
}
