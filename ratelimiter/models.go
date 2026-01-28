package main

// CheckRequest represents a rate limit check request
type CheckRequest struct {
	Scope      string `json:"scope"`      // "user", "global", "endpoint", "custom"
	Identifier string `json:"identifier"` // User ID, "all", or custom identifier
	Endpoint   string `json:"endpoint"`   // API endpoint or resource name
	Tokens     int64  `json:"tokens"`     // Number of tokens to consume (default: 1)
}

// CheckResponse represents the response to a rate limit check
type CheckResponse struct {
	Allowed           bool  `json:"allowed"`             // Whether the request is allowed
	Remaining         int64 `json:"remaining"`           // Tokens remaining in bucket
	Limit             int64 `json:"limit"`               // Maximum capacity of bucket
	ResetAfterSeconds int64 `json:"reset_after_seconds"` // Seconds until bucket is full
	RetryAfterSeconds int64 `json:"retry_after_seconds"` // Seconds to wait before retrying
}

// StatusRequest represents a status query request
type StatusRequest struct {
	Scope      string `json:"scope"`      // Scope type
	Identifier string `json:"identifier"` // Identifier
	Endpoint   string `json:"endpoint"`   // Endpoint
}

// StatusResponse represents bucket status information
type StatusResponse struct {
	Scope             string `json:"scope"`               // Scope of this bucket
	Identifier        string `json:"identifier"`          // Identifier
	Endpoint          string `json:"endpoint"`            // Endpoint
	Remaining         int64  `json:"remaining"`           // Current tokens available
	Limit             int64  `json:"limit"`               // Maximum capacity
	ResetAfterSeconds int64  `json:"reset_after_seconds"` // Seconds until full
}

// ResetRequest represents a reset request
type ResetRequest struct {
	Scope      string `json:"scope"`      // Scope type
	Identifier string `json:"identifier"` // Identifier
	Endpoint   string `json:"endpoint"`   // Endpoint
}

// ResetResponse represents the response to a reset operation
type ResetResponse struct {
	Success bool   `json:"success"` // Whether reset was successful
	Message string `json:"message"` // Status message
}

// HealthResponse represents health check response
type HealthResponse struct {
	Status  string        `json:"status"`  // "healthy" or "degraded"
	Version string        `json:"version"` // Service version
	Stats   StatsResponse `json:"stats"`   // Current statistics
}

// ErrorResponse represents an error response
type ErrorResponse struct {
	Error   string `json:"error"`   // Error type
	Message string `json:"message"` // Human-readable error message
}
