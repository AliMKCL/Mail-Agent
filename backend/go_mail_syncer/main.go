package main

// SAVE BEDORE TIME PARSE FIX
/*
PLAN:
1) Get mail ids 														DONE
2) Get credentials for the user id 										DONE
2) Request mail bodies from google server (metadata, body, html body)	DONE
3) Do it concurrently													DONE
4) Add them to the db
5) Clean the mails
6) Embed the cleaned mails
*/

import (
	"context"
	"database/sql"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"net/http"
	"sync"
	"time"

	"golang.org/x/oauth2"
	"google.golang.org/api/gmail/v1"
	"google.golang.org/api/option"
	_ "modernc.org/sqlite"
)

const Port int = 8001
const MaxWorkers int = 10
const DBPath string = "../../gmail_agent.db"

type messageIDs struct {
	UserID  int      `json:"user_id"`
	MailIDs []string `json:"mail_ids"`
}

type Credentials struct {
	AccessToken  string   `json:"access_token"`
	RefreshToken string   `json:"refresh_token"`
	TokenURI     string   `json:"token_uri"`
	ClientID     string   `json:"client_id"`
	ClientSecret string   `json:"client_secret"`
	Scopes       []string `json:"scopes"`
	Expiry       string   `json:"expiry"`
	CreatedAt    string   `json:"created_at"`
	UpdatedAt    string   `json:"updated_at"`
}

func main() {
	// ====== CREATE THE GO HTTP SERVER ======
	mux := http.NewServeMux()

	mux.HandleFunc("/fetch-emails", operateEmails)

	fmt.Println("Starting server on port 8001...")
	http.ListenAndServe(":8001", mux)
}

func operateEmails(
	w http.ResponseWriter, // Used to build and send the HTTP request back to the cilent.
	r *http.Request, // Contains all the information of the request
) {
	var ids messageIDs
	// Decode the JSON request body into the mail struct
	err := json.NewDecoder(r.Body).Decode(&ids)

	if err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}

	// ================ Get credentails from the database ================
	creds, err := getCredentials(ids.UserID)
	if err != nil {
		http.Error(w, "Failed to get credentials", http.StatusInternalServerError)
		return
	}

	// ================ Create the gmail service to fetch mails ================
	service, err := createGmailService(creds)
	if err != nil {
		http.Error(w, "Failed to create gmail service", http.StatusInternalServerError)
		return
	}

	// ================ Fetch emals ================
	startTime := time.Now()
	emails := fetchWorker(service, ids.MailIDs)
	elapsedTime := time.Since(startTime)
	fmt.Printf("Time taken to fetch emails: %s\n", elapsedTime)

	// ================ Add emails to db ================ (Convert to concurrent via mutex)
	addMailsToDB(emails, ids.UserID)

	var response map[string]interface{}

	if len(emails) == 0 {
		response = map[string]interface{}{
			"status": "fail",
			"emails": emails,
			"count":  0,
		}
		w.WriteHeader(http.StatusInternalServerError)
	} else {
		response = map[string]interface{}{
			"status": "success",
			"emails": emails,
			"count":  len(emails),
		}
		w.WriteHeader(http.StatusOK) // Status success (200)
	}

	w.Header().Set("Content-Type", "application/json") // Tells the client the response type is JSON
	json.NewEncoder(w).Encode(response)                // Converts the resposnse map into JSON

	fmt.Printf("Successfully fetched %d emails\n", len(emails))
}

func addMailsToDB(emails []map[string]interface{}, userID int) {
	db, err := sql.Open("sqlite", DBPath)
	if err != nil {
		fmt.Println("Error opening database:", err)
		return
	}
	defer db.Close()

	for _, email := range emails {
		// Check if email already exists
		var count int
		err := db.QueryRow("SELECT COUNT(*) FROM emails WHERE message_id = ? AND user_id = ?", email["message_id"], userID).Scan(&count)
		if err != nil {
			fmt.Printf("Error checking duplicate for message_id %s: %v\n", email["message_id"], err)
			continue
		}

		if count > 0 {
			fmt.Printf("Email with message_id %s already exists for user_id %d, skipping insertion.\n", email["message_id"], userID)
			continue
		}

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
	}
}

// Aproximate speeds:
// 0.7s for 50 mails with 10 workers
// 5s for 50 mails with 1 worker
// 13.12s for 50 mails with python single thread
func fetchWorker(service *gmail.Service, ids []string) []map[string]interface{} {
	var emails []map[string]interface{}
	var emailsChan = make(chan map[string]interface{}, len(ids))
	var jobsChan = make(chan string, len(ids))

	var wg sync.WaitGroup

	for i := 0; i < len(ids); i++ {
		jobsChan <- ids[i]
	}
	close(jobsChan)

	for w := 0; w < MaxWorkers; w++ {
		wg.Add(1)
		go func(workerID int) {
			defer wg.Done()
			for mailID := range jobsChan {
				fmt.Println("Fetching mail ID: ", mailID, " by worker ", workerID)
				email, err := fetchSingleEmail(service, mailID)

				if err != nil {
					fmt.Printf("Error fetching email id %s: %v\n", mailID, err)
					continue
				}
				emailsChan <- email
			}
		}(w + 1)
	}

	go func() {
		wg.Wait()
		close(emailsChan)
	}()

	for email := range emailsChan {
		emails = append(emails, email)
	}

	return emails
}

func getCredentials(userID int) (Credentials, error) {
	var Creds Credentials

	db, err := sql.Open("sqlite", DBPath) // Create the db connection
	if err != nil {
		fmt.Println("Error opening database:", err)
		return Credentials{}, err
	}

	defer db.Close()
	fmt.Println("Database opened successfully")

	row := db.QueryRow("SELECT access_token, refresh_token, token_uri, client_id, client_secret, scopes, expiry, created_at, updated_at FROM user_tokens WHERE user_id = ?", userID)

	var scopesJSON string     // Scopes are stored in DB as a JSON string not array, so it needs to be unmarshaled.
	var expiry sql.NullString // Expiry can be Null in the database, so I use a temporary string to handle that.
	if err := row.Scan(&Creds.AccessToken,
		&Creds.RefreshToken,
		&Creds.TokenURI,
		&Creds.ClientID,
		&Creds.ClientSecret,
		&scopesJSON,
		&expiry,
		&Creds.CreatedAt,
		&Creds.UpdatedAt); err != nil {
		if err == sql.ErrNoRows {
			fmt.Println("No credentials found for user ID:", userID)
			return Credentials{}, err
		}
		fmt.Println("Some error occured while allocating credentials:", err)
		return Credentials{}, err
	}

	// Convert null expiry to empty string
	if expiry.Valid {
		Creds.Expiry = expiry.String
	} else {
		Creds.Expiry = ""
	}

	// Convert the scopes from a comma-separated string to a slice
	err = json.Unmarshal([]byte(scopesJSON), &Creds.Scopes)
	if err != nil {
		fmt.Println("Error unmarshaling scopes:", err)
		return Credentials{}, err
	}

	return Creds, nil
}

func createGmailService(creds Credentials) (*gmail.Service, error) {

	expiryToTime, err := time.Parse(time.RFC3339, creds.Expiry)
	if err != nil {
		expiryToTime = time.Time{}
	}
	token := &oauth2.Token{
		AccessToken:  creds.AccessToken,
		RefreshToken: creds.RefreshToken,
		TokenType:    "Bearer",
		Expiry:       expiryToTime,
	}

	config := &oauth2.Config{
		ClientID:     creds.ClientID,
		ClientSecret: creds.ClientSecret,
		Scopes:       creds.Scopes,
		Endpoint: oauth2.Endpoint{
			AuthURL:  "https://accounts.google.com/o/oauth2/auth",
			TokenURL: creds.TokenURI,
		},
	}

	ctx := context.Background()

	// Create our HTTP client using the Oauth2 token. Carries the token to the server for authentication.
	httpClient := config.Client(ctx, token)

	// Authenticate gmail with the HTTPClient authentication option.
	// Automatically refreshes expired tokens using the refresh token.
	gmailService, err := gmail.NewService(ctx, option.WithHTTPClient(httpClient))

	if err != nil {
		return nil, fmt.Errorf("failed to create gmail service: %w", err)
	}

	fmt.Println("Gmail service created successfully")
	return gmailService, nil
}

func fetchSingleEmail(service *gmail.Service, mailID string) (map[string]interface{}, error) {
	// Call Gmail API to get the full message
	// format="full" gets everything: headers + body parts
	msg, err := service.Users.Messages.Get("me", mailID).Format("full").Do() // "me" refers to the authenticated user
	if err != nil {
		return nil, fmt.Errorf("failed to fetch message %s: %w", mailID, err)
	}

	// Initialize email data map
	email := map[string]interface{}{
		"message_id": mailID,
		//"thread_id": msg.ThreadId,
		"snippet": msg.Snippet,
	}

	// Get headers (replaces get_message_metadata python function))
	for _, header := range msg.Payload.Headers {
		switch header.Name {
		case "Subject":
			email["subject"] = header.Value
		case "From":
			email["sender"] = header.Value
		case "To":
			email["recipient"] = header.Value
		case "Date":
			// Parse Gmail's RFC 2822 date format and convert to SQLite format
			// Format: "2025-12-08 12:13:08.123456" (matches Python's SQLAlchemy DateTime storage)
			parsedDate, err := parseEmailDate(header.Value)
			if err != nil {
				fmt.Printf("Warning: Failed to parse date '%s': %v\n", header.Value, err)
				email["date_sent"] = nil // Store as NULL if parsing fails
			} else {
				// Format as SQLite datetime: YYYY-MM-DD HH:MM:SS.ffffff
				email["date_sent"] = parsedDate.Format("2006-01-02 15:04:05.000000")
			}
		}
	}

	// Get text body and html body
	email["body_text"] = extractBody(msg.Payload, "text/plain")
	email["body_html"] = extractBody(msg.Payload, "text/html")

	return email, nil
}

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

// Extracts body of specified MIME type from Gmail message parts
func extractBody(part *gmail.MessagePart, mimeType string) string {
	// Check if this part matches the MIME type we want
	if part.MimeType == mimeType && part.Body.Data != "" {
		// Gmail stores body as base64 URL-encoded string
		decoded, err := base64.URLEncoding.DecodeString(part.Body.Data)
		if err != nil {
			fmt.Println("Warning: Failed to decode body:", err)
			return ""
		}
		return string(decoded)
	}

	// If not found, search in nested parts (multipart emails)
	for _, subPart := range part.Parts {
		if body := extractBody(subPart, mimeType); body != "" {
			return body
		}
	}

	return ""
}
