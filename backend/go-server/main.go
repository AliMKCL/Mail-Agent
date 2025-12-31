package main

// SAVE BEDORE TIME PARSE FIX
/*
PLAN:
1) Get mail ids 														DONE
2) Get credentials for the user id 										DONE
2) Request mail bodies from google server (metadata, body, html body)	DONE
3) Do it concurrently													DONE
4) Add them to the db													DONE
5) Do it concurrently													DONE
6) Clean the mails														NOT NEEDED
7) Separate duplicates for embedding (Duplicates shouldn't be)
7) Embed the mails
8) Do it concurrently
*/

import (
	"database/sql"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"net/http"
	"sync"
	"time"

	"google.golang.org/api/gmail/v1"
	_ "modernc.org/sqlite"
)

const Port int = 8001
const MaxWorkers int = 10
const DBPath string = "../../gmail_agent.db" // Relative to go-server directory

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

	// ================ Fetch emails & Add to DB ================
	startTime := time.Now()
	emails := fetchWorker(service, ids.MailIDs, ids.UserID) // Returns emails with a field for their embeddings.
	elapsedTime := time.Since(startTime)
	fmt.Printf("Time taken to fetch emails and add to db: %s\n", elapsedTime)

	var response map[string]interface{}

	if len(emails) == 0 {
		response = map[string]interface{}{
			"status": "success",
			"emails": emails,
			"count":  0,
		}
		w.WriteHeader(http.StatusOK)
	} else {
		response = map[string]interface{}{
			"status": "success",
			"emails": emails,
			"count":  len(emails),
		}
		w.WriteHeader(http.StatusOK) // Status success (200)
	}

	w.Header().Set("Content-Type", "application/json") // Tells the client the response type is JSON
	json.NewEncoder(w).Encode(response)                // Converts the response map into JSON

	fmt.Printf("Successfully fetched %d emails\n", len(emails))
}

// Aproximate speeds:
// Fetch mails:
// 0.6s for 50 mails with 10 workers
// 5s for 50 mails with 1 worker
// 13.12s for 50 mails with python single thread
// Fetch mails AND add to db:
// 0.542s for 22 mails with 10 workers (2 duplicates)
// Fetches mails, adds to db and embeds them. Returns the mails.
func fetchWorker(service *gmail.Service, ids []string, userID int) []EmailWithEmbedding {
	var emails []map[string]interface{}
	var jobsChan = make(chan string, len(ids))

	var wg sync.WaitGroup
	var dbWg sync.WaitGroup
	var embedWg sync.WaitGroup

	var newMails = make(chan map[string]interface{}, 50)

	var emailsToWrite = make(chan map[string]interface{}, len(ids))
	var numNewMails int

	// Add jobs to the channel then close it
	for i := 0; i < len(ids); i++ {
		jobsChan <- ids[i]
	}
	close(jobsChan)

	// Open the db
	db, err := sql.Open("sqlite", DBPath)
	if err != nil {
		fmt.Println("Error opening database:", err)
		return []EmailWithEmbedding{}
	}
	defer db.Close()

	// Start DB writer goroutine
	dbWg.Add(1)
	go func() {
		defer dbWg.Done()
		for email := range emailsToWrite {
			newMail := addMailToDB(email, userID, db)
			newMails <- newMail // Add the newMails to buffered channel
			emails = append(emails, email)
		}
	}()

	var MailsWithEmbeddings []EmailWithEmbedding
	var mweLock sync.Mutex

	embedWg.Add(1)
	go func() {
		defer embedWg.Done()
		batch := make([]map[string]interface{}, 0, 50) // Batch size of 50

		var embeddingWg sync.WaitGroup

		for mail := range newMails {
			if mail != nil { // Nil is for duplicates (where addToDB returns nil)
				batch = append(batch, mail)
				if len(batch) == 50 {
					fmt.Println("Embedding batch of size: 50")

					// Make a copy of the batch to avoid race condition
					batchCopy := make([]map[string]interface{}, len(batch))
					copy(batchCopy, batch)

					embeddingWg.Add(1)
					go func(batchCopy []map[string]interface{}) { // After batch fills, it is operated on in a separate Goroutine.
						defer embeddingWg.Done()

						embeds, err := embedMails(batchCopy)
						if err != nil {
							fmt.Printf("Error embedding mails: %v\n", err)
						}
						mweLock.Lock()
						MailsWithEmbeddings = append(MailsWithEmbeddings, embeds...)
						numNewMails += len(batchCopy)
						mweLock.Unlock()
					}(batchCopy)

					batch = batch[:0] // Reset batch in parent goroutine
				}
			}

		}
		// Embed the remaining mails in the final batch
		if len(batch) > 0 {
			fmt.Println("Embedding final batch of size: ", len(batch))

			embeds, err := embedMails(batch)
			if err != nil {
				fmt.Printf("Error embedding mails: %v\n", err)
			} else {
				mweLock.Lock()
				numNewMails += len(batch)
				MailsWithEmbeddings = append(MailsWithEmbeddings, embeds...)
				mweLock.Unlock()
			}
		}

		embeddingWg.Wait()
	}()

	// Start worker goroutines
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
				emailsToWrite <- email
			}
		}(w + 1)
	}

	// Wait for all workers to finish fetching
	wg.Wait()
	close(emailsToWrite) // Signal DB writer that no more emails coming
	fmt.Println("All workers done fetching.")

	// Wait for DB writer to finish
	dbWg.Wait()
	close(newMails) // Signal embedding goroutine no more mails coming
	fmt.Println("DB writer done.")

	// Wait for embedding to finish
	embedWg.Wait()
	fmt.Println("Embedding done.")
	fmt.Println("Number of new mails = ", numNewMails)

	// Send embeddings to python, to add to db

	return MailsWithEmbeddings
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
