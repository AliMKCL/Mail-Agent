package main

import (
	"fmt"
	"time"
)

func main() {
	// With limit = 5
	refillRatePerHour := int64(5)

	// Convert to per second (as in limiter.go line 88)
	refillRatePerSecond := float64(refillRatePerHour) / 3600.0
	fmt.Printf("Refill rate per second: %f\n", refillRatePerSecond)

	// Store as nanosecond precision (as in limiter.go line 93)
	refillRate := int64(refillRatePerSecond * 1000000000)
	fmt.Printf("Refill rate stored: %d (nanoseconds precision)\n", refillRate)

	// Simulate elapsed time
	elapsed := 3 * time.Second

	// Calculate tokens to add (as in bucket.go line 43 - FIXED)
	tokensToAdd := int64(float64(elapsed.Nanoseconds()) * float64(refillRate) / 1e18)
	fmt.Printf("\nAfter 3 seconds:\n")
	fmt.Printf("Elapsed nanoseconds: %d\n", elapsed.Nanoseconds())
	fmt.Printf("Tokens to add: %d\n", tokensToAdd)
	fmt.Printf("Expected: should be 5/3600 * 3 = %f tokens\n", float64(refillRatePerHour)/3600.0*3.0)

	// Test with 1 millisecond
	elapsed = 1 * time.Millisecond
	tokensToAdd = int64(float64(elapsed.Nanoseconds()) * float64(refillRate) / 1e18)
	fmt.Printf("\nAfter 1 millisecond:\n")
	fmt.Printf("Tokens to add: %d\n", tokensToAdd)

	// Test with 720 seconds (should give 1 full token)
	elapsed = 720 * time.Second
	tokensToAdd = int64(float64(elapsed.Nanoseconds()) * float64(refillRate) / 1e18)
	fmt.Printf("\nAfter 720 seconds (12 minutes):\n")
	fmt.Printf("Tokens to add: %d\n", tokensToAdd)
	fmt.Printf("Expected: 5/3600 * 720 = 1 token\n")
}
