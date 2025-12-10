package main

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"net/http"

	chroma "github.com/amikos-tech/chroma-go"
	defaultef "github.com/amikos-tech/chroma-go/pkg/embeddings/default_ef"
)

func embedMails(batch []map[string]interface{}, ef *defaultef.DefaultEmbeddingFunction, client *chroma.Client) {
	// SEPARATE DUPLICATES FIRST BEFORE EMBEDDING, ONLY EMBED NEW MAILS

	/*
		PLAN:
		- Instead of concurrent embedding, via a buffered channel send finished mails into
		another goroutine which handles embedding.
		- This avoids the TCP handshake overhead for each mail, and is faster than linearly embedding with a loop.


	*/

	// REWRITE TO USE BATCH INSTEAD OF ONE BY ONE

	ctx := context.Background

	var mailsToEmbed []interface{}

	for _, email := range batch {
		mailsToEmbed = append(mailsToEmbed, email["body_text"])
	}

	jsonData, _ := json.Marshal(map[string]interface{}{
		"mailsToEmbed": mailsToEmbed,
	})

	// bytes.NewBuffer converts jsonData into an io.Reader (what http.Post expects)
	// application/json is content type (which is json)
	http.Post("http://localhost:8002/embed-email", "application/json", bytes.NewBuffer(jsonData))

	client.GetCollection(ctx, "mails", ef) // OR maybe just embedDocuments?

	fmt.Println("Embedding batch of size: ", len(batch))
}
