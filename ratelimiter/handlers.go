package main

import (
	"encoding/json"
	"net/http"
	"strconv"
)

// Server holds the rate limiter and logger
type Server struct {
	limiter *RateLimiter
	logger  *Logger
}

// NewServer creates a new HTTP server with rate limiter
func NewServer() *Server {
	return &Server{
		limiter: NewRateLimiter(),
		logger:  NewLogger("RateLimiter"),
	}
}

// respondJSON sends a JSON response
func respondJSON(w http.ResponseWriter, status int, data interface{}) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	json.NewEncoder(w).Encode(data)
}

// respondError sends a JSON error response
func respondError(w http.ResponseWriter, status int, errorType, message string) {
	respondJSON(w, status, ErrorResponse{
		Error:   errorType,
		Message: message,
	})
}

// handleCheck handles POST /check - check if request should be allowed
func (s *Server) handleCheck(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		respondError(w, http.StatusMethodNotAllowed, "method_not_allowed", "Only POST method is allowed")
		return
	}

	// Parse request
	var req CheckRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		respondError(w, http.StatusBadRequest, "invalid_json", "Invalid JSON in request body")
		return
	}

	// Validate required fields
	if req.Scope == "" {
		respondError(w, http.StatusBadRequest, "missing_scope", "Scope is required")
		return
	}
	if req.Identifier == "" {
		respondError(w, http.StatusBadRequest, "missing_identifier", "Identifier is required")
		return
	}
	if req.Endpoint == "" {
		respondError(w, http.StatusBadRequest, "missing_endpoint", "Endpoint is required")
		return
	}

	// Default to 1 token if not specified
	if req.Tokens <= 0 {
		req.Tokens = 1
	}

	// Check rate limit
	allowed, remaining, resetAfter, retryAfter := s.limiter.Check(
		req.Scope,
		req.Identifier,
		req.Endpoint,
		req.Tokens,
	)

	// Log the result
	if allowed {
		s.logger.LogRateLimitAllowed(req.Scope, req.Identifier, req.Endpoint, remaining)
	} else {
		s.logger.LogRateLimitViolation(req.Scope, req.Identifier, req.Endpoint, retryAfter)
	}

	// Get capacity for response
	_, limit, _ := s.limiter.GetStatus(req.Scope, req.Identifier, req.Endpoint)

	// Respond with result
	response := CheckResponse{
		Allowed:           allowed,
		Remaining:         remaining,
		Limit:             limit,
		ResetAfterSeconds: resetAfter,
		RetryAfterSeconds: retryAfter,
	}

	// Use 429 status code if rate limited, 200 if allowed
	status := http.StatusOK
	if !allowed {
		status = http.StatusTooManyRequests
		// Set Retry-After header for RFC compliance
		w.Header().Set("Retry-After", strconv.FormatInt(retryAfter, 10))
	}

	respondJSON(w, status, response)
}

// handleStatus handles GET /status - get current bucket status
func (s *Server) handleStatus(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		respondError(w, http.StatusMethodNotAllowed, "method_not_allowed", "Only GET method is allowed")
		return
	}

	// Parse query parameters
	query := r.URL.Query()
	scope := query.Get("scope")
	identifier := query.Get("identifier")
	endpoint := query.Get("endpoint")

	// Validate required parameters
	if scope == "" {
		respondError(w, http.StatusBadRequest, "missing_scope", "Scope query parameter is required")
		return
	}
	if identifier == "" {
		respondError(w, http.StatusBadRequest, "missing_identifier", "Identifier query parameter is required")
		return
	}
	if endpoint == "" {
		respondError(w, http.StatusBadRequest, "missing_endpoint", "Endpoint query parameter is required")
		return
	}

	// Get status
	remaining, limit, resetAfter := s.limiter.GetStatus(scope, identifier, endpoint)

	response := StatusResponse{
		Scope:             scope,
		Identifier:        identifier,
		Endpoint:          endpoint,
		Remaining:         remaining,
		Limit:             limit,
		ResetAfterSeconds: resetAfter,
	}

	respondJSON(w, http.StatusOK, response)
}

// handleReset handles DELETE /reset - reset a bucket
func (s *Server) handleReset(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodDelete {
		respondError(w, http.StatusMethodNotAllowed, "method_not_allowed", "Only DELETE method is allowed")
		return
	}

	// Parse query parameters
	query := r.URL.Query()
	scope := query.Get("scope")
	identifier := query.Get("identifier")
	endpoint := query.Get("endpoint")

	// Validate required parameters
	if scope == "" {
		respondError(w, http.StatusBadRequest, "missing_scope", "Scope query parameter is required")
		return
	}
	if identifier == "" {
		respondError(w, http.StatusBadRequest, "missing_identifier", "Identifier query parameter is required")
		return
	}
	if endpoint == "" {
		respondError(w, http.StatusBadRequest, "missing_endpoint", "Endpoint query parameter is required")
		return
	}

	// Reset bucket
	success := s.limiter.Reset(scope, identifier, endpoint)

	var response ResetResponse
	if success {
		s.logger.Info("Bucket reset: scope=%s identifier=%s endpoint=%s", scope, identifier, endpoint)
		response = ResetResponse{
			Success: true,
			Message: "Rate limit bucket reset successfully",
		}
	} else {
		response = ResetResponse{
			Success: false,
			Message: "Bucket not found (was never accessed)",
		}
	}

	respondJSON(w, http.StatusOK, response)
}

// handleHealth handles GET /health - health check and stats
func (s *Server) handleHealth(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		respondError(w, http.StatusMethodNotAllowed, "method_not_allowed", "Only GET method is allowed")
		return
	}

	stats := s.limiter.GetStats()

	response := HealthResponse{
		Status:  "healthy",
		Version: "1.0.0",
		Stats:   stats,
	}

	respondJSON(w, http.StatusOK, response)
}

// handleRoot handles GET / - API documentation
func (s *Server) handleRoot(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		respondError(w, http.StatusMethodNotAllowed, "method_not_allowed", "Only GET method is allowed")
		return
	}

	docs := `Rate Limiter Microservice API

Available Endpoints:

POST /check
  Check if a request should be allowed based on rate limits
  Request Body:
    {
      "scope": "user|global|endpoint|custom",
      "identifier": "user_id or 'all'",
      "endpoint": "/api/endpoint",
      "tokens": 1 (optional, default: 1)
    }
  Response (200 OK or 429 Too Many Requests):
    {
      "allowed": true/false,
      "remaining": 49,
      "limit": 100,
      "reset_after_seconds": 3600,
      "retry_after_seconds": 0
    }

GET /status?scope=user&identifier=123&endpoint=/api/sync
  Get current status of a rate limit bucket
  Response (200 OK):
    {
      "scope": "user",
      "identifier": "123",
      "endpoint": "/api/sync",
      "remaining": 50,
      "limit": 100,
      "reset_after_seconds": 3600
    }

DELETE /reset?scope=user&identifier=123&endpoint=/api/sync
  Reset a rate limit bucket to full capacity (admin operation)
  Response (200 OK):
    {
      "success": true,
      "message": "Rate limit bucket reset successfully"
    }

GET /health
  Health check and service statistics
  Response (200 OK):
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

Scope Types:
  - user: Per-user rate limiting (e.g., 100 req/hour per user)
  - global: Global rate limiting across all users (e.g., 1000 req/hour total)
  - endpoint: Per-endpoint rate limiting (e.g., 500 req/hour for this endpoint)
  - custom: Custom scope for special resources (e.g., API key limits)

Default Configuration:
  - Capacity: 100 tokens
  - Refill Rate: 100 tokens per hour
  - Algorithm: Token Bucket (allows bursts up to capacity)
`

	w.Header().Set("Content-Type", "text/plain")
	w.WriteHeader(http.StatusOK)
	w.Write([]byte(docs))
}
