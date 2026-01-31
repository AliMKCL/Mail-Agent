package tests

import (
	"bytes"
	"encoding/json"
	"net/http"
	"testing"
)

// These tests verify the rate limiter service functionality
// by making actual HTTP requests to the running service

// Test models matching the main package
type CheckRequest struct {
	Scope      string `json:"scope"`
	Identifier string `json:"identifier"`
	Endpoint   string `json:"endpoint"`
	Tokens     int64  `json:"tokens"`
	Capacity   *int64 `json:"capacity,omitempty"`
	RefillRate *int64 `json:"refill_rate,omitempty"`
}

type CheckResponse struct {
	Allowed           bool  `json:"allowed"`
	Remaining         int64 `json:"remaining"`
	Limit             int64 `json:"limit"`
	ResetAfterSeconds int64 `json:"reset_after_seconds"`
	RetryAfterSeconds int64 `json:"retry_after_seconds"`
}

type StatusResponse struct {
	Scope             string `json:"scope"`
	Identifier        string `json:"identifier"`
	Endpoint          string `json:"endpoint"`
	Remaining         int64  `json:"remaining"`
	Limit             int64  `json:"limit"`
	ResetAfterSeconds int64  `json:"reset_after_seconds"`
}

type ResetResponse struct {
	Success bool   `json:"success"`
	Message string `json:"message"`
}

type HealthResponse struct {
	Status  string        `json:"status"`
	Version string        `json:"version"`
	Stats   StatsResponse `json:"stats"`
}

type StatsResponse struct {
	TotalRequests int64 `json:"total_requests"`
	Allowed       int64 `json:"allowed"`
	Denied        int64 `json:"denied"`
	ActiveBuckets int64 `json:"active_buckets"`
	UptimeSeconds int64 `json:"uptime_seconds"`
}

var baseURL string = "http://localhost:8002"

// IMPORTANT: These are INTEGRATION TESTS, not unit tests
// They test the actual rate limiter service running on http://localhost:8002
// The service MUST be running before executing these tests

// Helper function to make check request
func makeCheckRequest(t *testing.T, baseURL string, req CheckRequest) CheckResponse {
	jsonData, err := json.Marshal(req)
	if err != nil {
		t.Fatalf("Failed to marshal request: %v", err)
	}

	resp, err := http.Post(baseURL+"/check", "application/json", bytes.NewBuffer(jsonData))
	if err != nil {
		t.Fatalf("Failed to make request: %v", err)
	}
	defer resp.Body.Close()

	var checkResp CheckResponse
	if err := json.NewDecoder(resp.Body).Decode(&checkResp); err != nil {
		t.Fatalf("Failed to decode response: %v", err)
	}

	return checkResp
}

// Helper function to reset bucket
func resetBucket(t *testing.T, baseURL, scope, identifier, endpoint string) {
	url := baseURL + "/reset?scope=" + scope + "&identifier=" + identifier + "&endpoint=" + endpoint
	req, _ := http.NewRequest("DELETE", url, nil)
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatalf("Failed to reset bucket: %v", err)
	}
	defer resp.Body.Close()
}

// Test 1: Per-user rate limiting (default parameters)
func TestPerUserRateLimiting_DefaultParams(t *testing.T) {

	// Reset bucket before test
	resetBucket(t, baseURL, "user", "user123", "/api/emails")

	// Make request with default parameters
	req := CheckRequest{
		Scope:      "user",
		Identifier: "user123",
		Endpoint:   "/api/emails",
		Tokens:     1,
	}

	resp := makeCheckRequest(t, baseURL, req)

	// Should be allowed with default capacity (5)
	if !resp.Allowed {
		t.Errorf("Expected request to be allowed")
	}
	if resp.Limit != 5 {
		t.Errorf("Expected limit to be 5 (default), got %d", resp.Limit)
	}
	if resp.Remaining != 4 {
		t.Errorf("Expected remaining to be 4, got %d", resp.Remaining)
	}
}

// Test 2: Per-user rate limiting with custom capacity and refill_rate
func TestPerUserRateLimiting_CustomParams(t *testing.T) {

	// Reset bucket before test
	resetBucket(t, baseURL, "user", "user456", "/api/sync")

	// Custom parameters
	capacity := int64(10)
	refillRate := int64(10)

	req := CheckRequest{
		Scope:      "user",
		Identifier: "user456",
		Endpoint:   "/api/sync",
		Tokens:     1,
		Capacity:   &capacity,
		RefillRate: &refillRate,
	}

	resp := makeCheckRequest(t, baseURL, req)

	if !resp.Allowed {
		t.Errorf("Expected request to be allowed")
	}
	if resp.Limit != 10 {
		t.Errorf("Expected limit to be 10 (custom), got %d", resp.Limit)
	}
	if resp.Remaining != 9 {
		t.Errorf("Expected remaining to be 9, got %d", resp.Remaining)
	}
}

// Test 3: Global endpoint rate limiting (default parameters)
func TestGlobalEndpointRateLimiting_DefaultParams(t *testing.T) {

	resetBucket(t, baseURL, "global", "all", "/api/public")

	req := CheckRequest{
		Scope:      "global",
		Identifier: "all",
		Endpoint:   "/api/public",
		Tokens:     1,
	}

	resp := makeCheckRequest(t, baseURL, req)

	if !resp.Allowed {
		t.Errorf("Expected request to be allowed")
	}
	if resp.Limit != 5 {
		t.Errorf("Expected limit to be 5 (default), got %d", resp.Limit)
	}
}

// Test 4: Global endpoint with custom parameters
func TestGlobalEndpointRateLimiting_CustomParams(t *testing.T) {

	resetBucket(t, baseURL, "global", "all", "/api/expensive")

	capacity := int64(100)
	refillRate := int64(100)

	req := CheckRequest{
		Scope:      "global",
		Identifier: "all",
		Endpoint:   "/api/expensive",
		Tokens:     1,
		Capacity:   &capacity,
		RefillRate: &refillRate,
	}

	resp := makeCheckRequest(t, baseURL, req)

	if !resp.Allowed {
		t.Errorf("Expected request to be allowed")
	}
	if resp.Limit != 100 {
		t.Errorf("Expected limit to be 100 (custom), got %d", resp.Limit)
	}
	if resp.Remaining != 99 {
		t.Errorf("Expected remaining to be 99, got %d", resp.Remaining)
	}
}

// Test 5: Server-wide limit (shared bucket across endpoints)
func TestServerWideLimiting(t *testing.T) {

	resetBucket(t, baseURL, "global", "all", "server_total")

	capacity := int64(20)
	refillRate := int64(20)

	req := CheckRequest{
		Scope:      "global",
		Identifier: "all",
		Endpoint:   "server_total",
		Tokens:     1,
		Capacity:   &capacity,
		RefillRate: &refillRate,
	}

	// First request
	resp1 := makeCheckRequest(t, baseURL, req)
	if !resp1.Allowed || resp1.Remaining != 19 {
		t.Errorf("First request failed: allowed=%v, remaining=%d", resp1.Allowed, resp1.Remaining)
	}

	// Second request (should share same bucket)
	resp2 := makeCheckRequest(t, baseURL, req)
	if !resp2.Allowed || resp2.Remaining != 18 {
		t.Errorf("Second request failed: allowed=%v, remaining=%d", resp2.Allowed, resp2.Remaining)
	}
}

// Test 6: Custom tokens consumption
func TestCustomTokensConsumption(t *testing.T) {

	resetBucket(t, baseURL, "user", "user789", "/api/heavy")

	capacity := int64(20)
	refillRate := int64(20)

	req := CheckRequest{
		Scope:      "user",
		Identifier: "user789",
		Endpoint:   "/api/heavy",
		Tokens:     5, // Consume 5 tokens per request
		Capacity:   &capacity,
		RefillRate: &refillRate,
	}

	resp := makeCheckRequest(t, baseURL, req)

	if !resp.Allowed {
		t.Errorf("Expected request to be allowed")
	}
	if resp.Remaining != 15 {
		t.Errorf("Expected remaining to be 15 (20 - 5), got %d", resp.Remaining)
	}
}

// Test 7: Partial parameters - only capacity
func TestPartialParams_OnlyCapacity(t *testing.T) {

	resetBucket(t, baseURL, "user", "user_cap", "/api/test1")

	capacity := int64(50)

	req := CheckRequest{
		Scope:      "user",
		Identifier: "user_cap",
		Endpoint:   "/api/test1",
		Tokens:     1,
		Capacity:   &capacity,
		// RefillRate not provided - should use default (5)
	}

	resp := makeCheckRequest(t, baseURL, req)

	if !resp.Allowed {
		t.Errorf("Expected request to be allowed")
	}
	if resp.Limit != 50 {
		t.Errorf("Expected limit to be 50 (custom capacity), got %d", resp.Limit)
	}
}

// Test 8: Partial parameters - only refill_rate
func TestPartialParams_OnlyRefillRate(t *testing.T) {

	resetBucket(t, baseURL, "user", "user_refill", "/api/test2")

	refillRate := int64(100)

	req := CheckRequest{
		Scope:      "user",
		Identifier: "user_refill",
		Endpoint:   "/api/test2",
		Tokens:     1,
		// Capacity not provided - should use default (5)
		RefillRate: &refillRate,
	}

	resp := makeCheckRequest(t, baseURL, req)

	if !resp.Allowed {
		t.Errorf("Expected request to be allowed")
	}
	if resp.Limit != 5 {
		t.Errorf("Expected limit to be 5 (default capacity), got %d", resp.Limit)
	}
}

// Test 9: Partial parameters - tokens and capacity
func TestPartialParams_TokensAndCapacity(t *testing.T) {

	resetBucket(t, baseURL, "user", "user_tc", "/api/test3")

	capacity := int64(30)

	req := CheckRequest{
		Scope:      "user",
		Identifier: "user_tc",
		Endpoint:   "/api/test3",
		Tokens:     3,
		Capacity:   &capacity,
		// RefillRate not provided - should use default (5)
	}

	resp := makeCheckRequest(t, baseURL, req)

	if !resp.Allowed {
		t.Errorf("Expected request to be allowed")
	}
	if resp.Limit != 30 {
		t.Errorf("Expected limit to be 30, got %d", resp.Limit)
	}
	if resp.Remaining != 27 {
		t.Errorf("Expected remaining to be 27 (30 - 3), got %d", resp.Remaining)
	}
}

// Test 10: Rate limit exhaustion
func TestRateLimitExhaustion(t *testing.T) {

	resetBucket(t, baseURL, "user", "user_exhaust", "/api/limited")

	capacity := int64(3)
	refillRate := int64(3)

	req := CheckRequest{
		Scope:      "user",
		Identifier: "user_exhaust",
		Endpoint:   "/api/limited",
		Tokens:     1,
		Capacity:   &capacity,
		RefillRate: &refillRate,
	}

	// Make 3 requests (should all succeed)
	for i := 0; i < 3; i++ {
		resp := makeCheckRequest(t, baseURL, req)
		if !resp.Allowed {
			t.Errorf("Request %d should be allowed", i+1)
		}
	}

	// 4th request should be denied
	resp := makeCheckRequest(t, baseURL, req)
	if resp.Allowed {
		t.Errorf("Request should be denied after exhausting limit")
	}
	if resp.Remaining != 0 {
		t.Errorf("Expected remaining to be 0, got %d", resp.Remaining)
	}
	if resp.RetryAfterSeconds <= 0 {
		t.Errorf("Expected retry_after_seconds to be positive, got %d", resp.RetryAfterSeconds)
	}
}

// Test 11: Different users don't affect each other (per-user scope)
func TestUserIsolation(t *testing.T) {

	resetBucket(t, baseURL, "user", "alice", "/api/shared")
	resetBucket(t, baseURL, "user", "bob", "/api/shared")

	capacity := int64(2)
	refillRate := int64(2)

	// Alice makes 2 requests (exhausts her limit)
	reqAlice := CheckRequest{
		Scope:      "user",
		Identifier: "alice",
		Endpoint:   "/api/shared",
		Tokens:     1,
		Capacity:   &capacity,
		RefillRate: &refillRate,
	}

	makeCheckRequest(t, baseURL, reqAlice)
	makeCheckRequest(t, baseURL, reqAlice)

	// Alice's 3rd request should be denied
	respAlice := makeCheckRequest(t, baseURL, reqAlice)
	if respAlice.Allowed {
		t.Errorf("Alice's request should be denied")
	}

	// Bob should still be able to make requests
	reqBob := CheckRequest{
		Scope:      "user",
		Identifier: "bob",
		Endpoint:   "/api/shared",
		Tokens:     1,
		Capacity:   &capacity,
		RefillRate: &refillRate,
	}

	respBob := makeCheckRequest(t, baseURL, reqBob)
	if !respBob.Allowed {
		t.Errorf("Bob's request should be allowed")
	}
}

// Test 12: Bucket persistence across requests
func TestBucketPersistence(t *testing.T) {

	resetBucket(t, baseURL, "user", "user_persist", "/api/persist")

	capacity := int64(10)
	refillRate := int64(10)

	// First request with custom params
	req1 := CheckRequest{
		Scope:      "user",
		Identifier: "user_persist",
		Endpoint:   "/api/persist",
		Tokens:     1,
		Capacity:   &capacity,
		RefillRate: &refillRate,
	}

	resp1 := makeCheckRequest(t, baseURL, req1)
	if resp1.Limit != 10 {
		t.Errorf("First request: expected limit 10, got %d", resp1.Limit)
	}

	// Second request without custom params (should use existing bucket)
	req2 := CheckRequest{
		Scope:      "user",
		Identifier: "user_persist",
		Endpoint:   "/api/persist",
		Tokens:     1,
		// No custom params - should still use capacity=10 from first request
	}

	resp2 := makeCheckRequest(t, baseURL, req2)
	if resp2.Limit != 10 {
		t.Errorf("Second request: expected limit 10 (from existing bucket), got %d", resp2.Limit)
	}
	if resp2.Remaining != 8 {
		t.Errorf("Expected remaining to be 8, got %d", resp2.Remaining)
	}
}

// Test 13: Health endpoint
func TestHealthEndpoint(t *testing.T) {

	resp, err := http.Get(baseURL + "/health")
	if err != nil {
		t.Fatalf("Failed to get health: %v", err)
	}
	defer resp.Body.Close()

	var health HealthResponse
	if err := json.NewDecoder(resp.Body).Decode(&health); err != nil {
		t.Fatalf("Failed to decode health response: %v", err)
	}

	if health.Status != "healthy" {
		t.Errorf("Expected status 'healthy', got '%s'", health.Status)
	}
	if health.Version == "" {
		t.Errorf("Expected version to be set")
	}
}

// Test 14: Status endpoint
func TestStatusEndpoint(t *testing.T) {

	// First create a bucket
	resetBucket(t, baseURL, "user", "user_status", "/api/status_test")

	capacity := int64(15)
	refillRate := int64(15)

	req := CheckRequest{
		Scope:      "user",
		Identifier: "user_status",
		Endpoint:   "/api/status_test",
		Tokens:     2,
		Capacity:   &capacity,
		RefillRate: &refillRate,
	}

	makeCheckRequest(t, baseURL, req)

	// Now check status
	statusURL := baseURL + "/status?scope=user&identifier=user_status&endpoint=/api/status_test"
	resp, err := http.Get(statusURL)
	if err != nil {
		t.Fatalf("Failed to get status: %v", err)
	}
	defer resp.Body.Close()

	var status StatusResponse
	if err := json.NewDecoder(resp.Body).Decode(&status); err != nil {
		t.Fatalf("Failed to decode status response: %v", err)
	}

	if status.Limit != 15 {
		t.Errorf("Expected limit 15, got %d", status.Limit)
	}
	if status.Remaining != 13 {
		t.Errorf("Expected remaining 13, got %d", status.Remaining)
	}
}

// Test 15: Zero/negative custom values should use defaults
func TestInvalidCustomParams(t *testing.T) {

	resetBucket(t, baseURL, "user", "user_invalid", "/api/invalid")

	// Try to set capacity to 0 (should use default)
	capacity := int64(0)
	refillRate := int64(-5) // Negative should also use default

	req := CheckRequest{
		Scope:      "user",
		Identifier: "user_invalid",
		Endpoint:   "/api/invalid",
		Tokens:     1,
		Capacity:   &capacity,
		RefillRate: &refillRate,
	}

	resp := makeCheckRequest(t, baseURL, req)

	// Should use default capacity (5)
	if resp.Limit != 5 {
		t.Errorf("Expected limit to be 5 (default, since custom was invalid), got %d", resp.Limit)
	}
}
