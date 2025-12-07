package main

/*
PLAN:
1) Get mail ids 														DONE
2) Get credentials for the user id 										DONE
2) Request mail bodies from google server (metadata, body, html body)	DONE
3) Do it concurrently
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
	"time"

	"golang.org/x/oauth2"
	"google.golang.org/api/gmail/v1"
	"google.golang.org/api/option"
	_ "modernc.org/sqlite"
)

const Port int = 8001
const MaxWorkers int = 10
const DBPath string = "../../gmail_agent.db"

type Mail struct {
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
	var mails Mail
	// Decode the JSON request body into the mail struct
	err := json.NewDecoder(r.Body).Decode(&mails)

	if err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}
	fmt.Println("User id: ", mails.UserID, " Mails ids: ", mails.MailIDs)

	// ====== Get credentails from the database ======
	creds, err := getCredentials(mails.UserID)
	if err != nil {
		http.Error(w, "Failed to get credentials", http.StatusInternalServerError)
		return
	}
	//fmt.Println("Creds: ", creds)

	// ====== Create the gmail service to fetch mails ======
	service, err := createGmailService(creds)
	if err != nil {
		http.Error(w, "Failed to create gmail service", http.StatusInternalServerError)
		return
	}

	// ====== Fetch emals ======

	var mailID = mails.MailIDs[0]
	fetchSingleEmail(service, mailID)

	var emails []map[string]interface{}

	for _, mailID := range mails.MailIDs {
		email, err := fetchSingleEmail(service, mailID)
		if err != nil {
			fmt.Printf("Error fetching email id %s: %v\n", mailID, err)
			continue // Skip failing emails
		}
		emails = append(emails, email)
	}
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
		w.WriteHeader(http.StatusOK)
	}

	w.Header().Set("Content-Type", "application/json")

	json.NewEncoder(w).Encode(response)

	fmt.Printf("Successfully fetched %d emails\n", len(emails))
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

	// ??
	ctx := context.Background()

	// Create our HTTP client using the Oauth2 token
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
			email["date_sent"] = header.Value
		}
	}

	// Get text body and html body
	email["body_text"] = extractBody(msg.Payload, "text/plain")
	email["body_html"] = extractBody(msg.Payload, "text/html")

	return email, nil
}

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
