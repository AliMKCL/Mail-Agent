package main

import (
	"fmt"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"
)

const (
	DefaultPort = 8002
	Version     = "1.0.0"
)

func main() {
	// Create server
	server := NewServer()

	// Log startup
	server.logger.Info("Starting Rate Limiter Microservice v%s", Version)
	server.logger.Info("Default configuration: %d tokens capacity, %d tokens per hour",
		server.limiter.config.DefaultCapacity, server.limiter.config.DefaultRefillRate)

	// Setup HTTP routes
	http.HandleFunc("/", server.handleRoot)
	http.HandleFunc("/check", server.handleCheck)
	http.HandleFunc("/status", server.handleStatus)
	http.HandleFunc("/reset", server.handleReset)
	http.HandleFunc("/health", server.handleHealth)

	// Start cleanup goroutine (runs every hour, removes buckets inactive for 24h)
	go func() {
		ticker := time.NewTicker(1 * time.Hour)
		defer ticker.Stop()

		for range ticker.C {
			removed := server.limiter.CleanupInactiveBuckets(24 * time.Hour)
			if removed > 0 {
				server.logger.Info("Cleaned up %d inactive buckets", removed)
			}
		}
	}()

	// Setup graceful shutdown
	done := make(chan os.Signal, 1)
	signal.Notify(done, os.Interrupt, syscall.SIGINT, syscall.SIGTERM)

	// Start server in goroutine
	addr := fmt.Sprintf(":%d", DefaultPort)
	server.logger.Info("Server listening on %s", addr)
	server.logger.Info("Visit http://localhost:%d for API documentation", DefaultPort)

	go func() {
		if err := http.ListenAndServe(addr, nil); err != nil && err != http.ErrServerClosed {
			server.logger.Error("Server failed to start: %v", err)
			os.Exit(1)
		}
	}()

	// Wait for interrupt signal
	<-done
	server.logger.Info("Shutting down server...")

	// Log final stats
	stats := server.limiter.GetStats()
	server.logger.Info("Final stats: total_requests=%d allowed=%d denied=%d active_buckets=%d uptime=%ds",
		stats.TotalRequests, stats.Allowed, stats.Denied, stats.ActiveBuckets, stats.UptimeSeconds)

	server.logger.Info("Server stopped")
}
