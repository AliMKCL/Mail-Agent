package main

import (
	"fmt"
	"time"
)

// Removes timezone name in parentheses (ex: " (UTC)") from date strings)
func cleanDateString(dateStr string) string {
	// Find and remove anything after " (" like " (UTC)" or " (PST)"
	if idx := len(dateStr); idx > 0 {
		for i := 0; i < len(dateStr); i++ {
			if i+2 < len(dateStr) && dateStr[i] == ' ' && dateStr[i+1] == '(' {
				dateStr = dateStr[:i]
				break
			}
		}
	}
	return dateStr
}

// Parses RFC 2822 date strings from Gmail headers
// Handles multiple format variations including single-digit days and timezone suffixes
func parseEmailDate(dateStr string) (time.Time, error) {
	if dateStr == "" {
		return time.Time{}, fmt.Errorf("empty date string")
	}

	// Clean the string (remove " (UTC)" or similar timezone suffixes)
	cleaned := cleanDateString(dateStr)

	// Try multiple RFC 2822 layouts (FOR DEBUGGING)
	layouts := []string{
		time.RFC1123Z,                    // "Mon, 02 Jan 2006 15:04:05 -0700"
		time.RFC1123,                     // "Mon, 02 Jan 2006 15:04:05 MST"
		"Mon, 2 Jan 2006 15:04:05 -0700", // Single-digit day with numeric timezone
		"Mon, 2 Jan 2006 15:04:05 MST",   // Single-digit day with named timezone
	}

	for _, layout := range layouts {
		if parsed, err := time.Parse(layout, cleaned); err == nil {
			return parsed, nil
		}
	}

	return time.Time{}, fmt.Errorf("unable to parse date: %s", dateStr)
}
