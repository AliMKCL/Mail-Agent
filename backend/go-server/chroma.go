package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
)

func embedMails(batch []map[string]interface{}) [][]float32 {
	// Extract body_text from each email
	documents := make([]string, 0, len(batch))
	for _, email := range batch {
		documents = append(documents, email["body_text"].(string))
	}

	// Get embeddings using Ollama with mxbai-embed-large model
	embeddings, err := getOllamaEmbeddings(documents)
	if err != nil {
		fmt.Printf("Error getting embeddings: %v\n", err)
		return embeddings
	}

	fmt.Printf("Successfully embedded batch of %d emails (got %d embeddings)\n", len(batch), len(embeddings))

	return embeddings

}

// getOllamaEmbeddings calls Ollama API to get embeddings using mxbai-embed-large
// Uses batch embedding (single HTTP request for all documents)
func getOllamaEmbeddings(documents []string) ([][]float32, error) {
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

	// Parse batch response
	var result struct {
		Embeddings [][]float32 `json:"embeddings"` // Array of embeddings
	}

	if err := json.NewDecoder(resp.Body).Decode(&result); err != nil {
		return nil, fmt.Errorf("error decoding response: %w", err)
	}

	// Verify we got the right number of embeddings
	if len(result.Embeddings) != len(documents) {
		return nil, fmt.Errorf("expected %d embeddings but got %d", len(documents), len(result.Embeddings))
	}

	fmt.Println("Embedding example output:", result.Embeddings[0][:10]) // Print first 5 values of first embedding

	return result.Embeddings, nil
}
