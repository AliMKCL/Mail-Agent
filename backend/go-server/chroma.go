package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
)

type EmailWithEmbedding struct {
	MessageID string    `json:"message_id"`
	BodyText  string    `json:"body_text"`
	Embedding []float32 `json:"embedding"`
	Sender    string    `json:"sender"`
	Subject   string    `json:"subject"`
	DateSent  string    `json:"date_sent"`
}

func embedMails(batch []map[string]interface{}) ([]EmailWithEmbedding, error) {
	// Extract body_text from each email
	documents := make([]string, 0, len(batch))
	for _, email := range batch {
		documents = append(documents, email["body_text"].(string))
	}

	// Get embeddings using Ollama with mxbai-embed-large model
	mailsWithEmbeddings, err := getOllamaEmbeddings(documents, batch)
	if err != nil {
		fmt.Printf("Error getting embeddings: %v\n", err)
		return mailsWithEmbeddings, err
	}

	fmt.Printf("Successfully embedded batch of %d emails (got %d embeddings)\n", len(batch), len(mailsWithEmbeddings))

	return mailsWithEmbeddings, err

}

// getOllamaEmbeddings calls Ollama API to get embeddings using mxbai-embed-large
// Uses batch embedding (single HTTP request for all documents)
func getOllamaEmbeddings(documents []string, batch []map[string]interface{}) ([]EmailWithEmbedding, error) {
	// Prepare batch request for Ollama
	reqBody := map[string]interface{}{
		"model": "mxbai-embed-large",
		"input": documents, // Send all documents at once
	}

	jsonData, err := json.Marshal(reqBody)
	if err != nil {
		return nil, fmt.Errorf("error marshaling request: %w", err)
	}

	// Call Ollama batch embed API (single request)
	resp, err := http.Post("http://localhost:11434/api/embed", "application/json", bytes.NewBuffer(jsonData))
	if err != nil {
		return nil, fmt.Errorf("error calling Ollama API: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != 200 {
		body, _ := io.ReadAll(resp.Body)
		return nil, fmt.Errorf("ollama API error (status %d): %s", resp.StatusCode, string(body))
	}

	// Format embeddings as a 2d array
	var result struct {
		Embeddings [][]float32 `json:"embeddings"` // Array of embeddings
	}

	// Parse batch response
	if err := json.NewDecoder(resp.Body).Decode(&result); err != nil {
		return nil, fmt.Errorf("error decoding response: %w", err)
	}

	// Verify we got the right number of embeddings
	if len(result.Embeddings) != len(documents) {
		return nil, fmt.Errorf("expected %d embeddings but got %d", len(documents), len(result.Embeddings))
	}

	fmt.Println("Embedding example output:", result.Embeddings[0][:5]) // Print first 5 values of first embedding

	// Convert mails to the correct format (with embeddings) for python to handle adding to db.
	var EmailsWithEmbeddings []EmailWithEmbedding
	for i, embedding := range result.Embeddings {
		EmailsWithEmbeddings = append(EmailsWithEmbeddings, EmailWithEmbedding{
			MessageID: batch[i]["message_id"].(string),
			BodyText:  batch[i]["body_text"].(string),
			Embedding: embedding,
			Sender:    batch[i]["sender"].(string),
			Subject:   batch[i]["subject"].(string),
			DateSent:  batch[i]["date_sent"].(string),
		})
	}

	return EmailsWithEmbeddings, nil
}
