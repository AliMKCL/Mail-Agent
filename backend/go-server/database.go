package main

import (
	"database/sql"
	"fmt"
	"time"
)

// Adds the mails into the database.
func addMailToDB(email map[string]interface{}, userID int, db *sql.DB) map[string]interface{} {

	var count int
	err := db.QueryRow("SELECT COUNT(*) FROM emails WHERE message_id = ? AND user_id = ?", email["message_id"], userID).Scan(&count)
	if err != nil {
		fmt.Printf("Error checking duplicate for message_id %s: %v\n", email["message_id"], err)
		panic(err)
	}
	if count > 0 { // Duplicate found, skip insertion
		fmt.Printf("Email with message_id %s already exists for user_id %d, skipping insertion.\n", email["message_id"], userID)
		return nil
	}

	// Otherwise insert into the db:
	// Insert new email (10 values, 10 placeholders)
	// Use SQLite datetime format to match Python's SQLAlchemy: YYYY-MM-DD HH:MM:SS.ffffff
	_, err = db.Exec("INSERT INTO emails (user_id, message_id, subject, sender, recipient, date_sent, snippet, body_text, body_html, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
		userID,
		email["message_id"],
		email["subject"],
		email["sender"],
		email["recipient"],
		email["date_sent"],
		email["snippet"],
		email["body_text"],
		email["body_html"],
		time.Now().Format("2006-01-02 15:04:05.000000"),
	)
	if err != nil {
		fmt.Printf("Error inserting email %s: %v\n", email["message_id"], err)
	}
	return email
}
