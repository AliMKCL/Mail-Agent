package main

import (
	"fmt"
	"log"
	"time"
)

// LogLevel represents logging severity
type LogLevel int

const (
	LogLevelInfo LogLevel = iota
	LogLevelWarn
	LogLevelError
)

// Logger provides simple structured logging
type Logger struct {
	prefix string
}

// NewLogger creates a new logger with optional prefix
func NewLogger(prefix string) *Logger {
	return &Logger{prefix: prefix}
}

// formatMessage formats a log message with timestamp and level
func (l *Logger) formatMessage(level LogLevel, message string) string {
	timestamp := time.Now().Format("2006-01-02 15:04:05")

	var levelStr string
	switch level {
	case LogLevelInfo:
		levelStr = "INFO"
	case LogLevelWarn:
		levelStr = "WARN"
	case LogLevelError:
		levelStr = "ERROR"
	default:
		levelStr = "UNKNOWN"
	}

	if l.prefix != "" {
		return fmt.Sprintf("%s [%s] [%s] %s", timestamp, levelStr, l.prefix, message)
	}
	return fmt.Sprintf("%s [%s] %s", timestamp, levelStr, message)
}

// Info logs an informational message
func (l *Logger) Info(format string, args ...interface{}) {
	message := fmt.Sprintf(format, args...)
	log.Println(l.formatMessage(LogLevelInfo, message))
}

// Warn logs a warning message
func (l *Logger) Warn(format string, args ...interface{}) {
	message := fmt.Sprintf(format, args...)
	log.Println(l.formatMessage(LogLevelWarn, message))
}

// Error logs an error message
func (l *Logger) Error(format string, args ...interface{}) {
	message := fmt.Sprintf(format, args...)
	log.Println(l.formatMessage(LogLevelError, message))
}

// LogRateLimitViolation logs when a request is denied due to rate limiting
func (l *Logger) LogRateLimitViolation(scope, identifier, endpoint string, retryAfter int64) {
	l.Warn("Rate limit exceeded: scope=%s identifier=%s endpoint=%s retry_after=%ds",
		scope, identifier, endpoint, retryAfter)
}

// LogRateLimitAllowed logs when a request is allowed
func (l *Logger) LogRateLimitAllowed(scope, identifier, endpoint string, remaining int64) {
	l.Info("Request allowed: scope=%s identifier=%s endpoint=%s remaining=%d",
		scope, identifier, endpoint, remaining)
}
