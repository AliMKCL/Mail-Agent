package main

import (
	"context"
	"database/sql"
	"encoding/json"
	"fmt"
	"time"

	"golang.org/x/oauth2"
	"google.golang.org/api/gmail/v1"
	"google.golang.org/api/option"
)

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
