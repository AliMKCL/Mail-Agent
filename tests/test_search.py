"""
Tests for search functionality.

Tests cover:
- Vector database search (AI search via top search bar)
- Keyword search (subject search via bottom search bar)
"""

import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from fastapi.testclient import TestClient


class TestVectorDatabaseSearch:
    """Tests for vector database / AI search (top search bar)."""

    def test_query_returns_answer(self, test_db, user_with_emails, mock_vector_db, mock_llm):
        """Test that query endpoint returns an AI-generated answer."""
        from backend.app import app

        with patch('backend.app.db_manager', test_db), \
             patch('backend.app.query_vector_db', mock_vector_db), \
             patch('backend.app.llm_response', mock_llm):
            client = TestClient(app)
            response = client.get("/api/query?query=when is my meeting")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert "answer" in data
        assert len(data["answer"]) > 0

    def test_query_returns_sources(self, test_db, user_with_emails, mock_vector_db, mock_llm):
        """Test that query endpoint returns source emails."""
        from backend.app import app

        with patch('backend.app.db_manager', test_db), \
             patch('backend.app.query_vector_db', mock_vector_db), \
             patch('backend.app.llm_response', mock_llm):
            client = TestClient(app)
            response = client.get("/api/query?query=meeting tomorrow")

        data = response.json()
        assert "sources" in data
        assert isinstance(data["sources"], list)

    def test_query_sources_have_metadata(self, test_db, user_with_emails, mock_vector_db, mock_llm):
        """Test that sources include required metadata fields."""
        from backend.app import app

        with patch('backend.app.db_manager', test_db), \
             patch('backend.app.query_vector_db', mock_vector_db), \
             patch('backend.app.llm_response', mock_llm):
            client = TestClient(app)
            response = client.get("/api/query?query=meeting")

        data = response.json()
        sources = data["sources"]

        if len(sources) > 0:
            source = sources[0]
            assert "message_id" in source
            assert "sender" in source
            assert "subject" in source

    def test_query_returns_count(self, test_db, user_with_emails, mock_vector_db, mock_llm):
        """Test that query returns count of matched documents."""
        from backend.app import app

        with patch('backend.app.db_manager', test_db), \
             patch('backend.app.query_vector_db', mock_vector_db), \
             patch('backend.app.llm_response', mock_llm):
            client = TestClient(app)
            response = client.get("/api/query?query=deadline")

        data = response.json()
        assert "count" in data
        assert isinstance(data["count"], int)

    def test_query_respects_top_k(self, test_db, user_with_emails, mock_llm):
        """Test that top_k parameter limits results."""
        from backend.app import app

        async def mock_query_with_limit(query, top_k=3):
            # Return number of results based on top_k
            docs = []
            for i in range(min(top_k, 5)):
                doc = MagicMock()
                doc.page_content = f"Content {i}"
                doc.metadata = {
                    "message_id": f"msg_{i}",
                    "sender": f"sender{i}@test.com",
                    "subject": f"Subject {i}",
                    "date_sent": "2025-11-20"
                }
                docs.append(doc)
            return docs

        with patch('backend.app.db_manager', test_db), \
             patch('backend.app.query_vector_db', mock_query_with_limit), \
             patch('backend.app.llm_response', mock_llm):
            client = TestClient(app)

            # Request with top_k=2
            response = client.get("/api/query?query=test&top_k=2")

        data = response.json()
        assert data["count"] <= 2

    def test_empty_query_returns_error(self, test_db):
        """Test that empty query returns an error status."""
        from backend.app import app

        with patch('backend.app.db_manager', test_db):
            client = TestClient(app)
            response = client.get("/api/query?query=")

        # Empty query should return error (400 or 422 for validation)
        assert response.status_code in [400, 422, 500]
        # Should contain error information
        data = response.json()
        assert "detail" in data or "error" in data

    def test_no_results_returns_appropriate_message(self, test_db, mock_llm):
        """Test response when no matching documents found."""
        from backend.app import app

        async def mock_empty_query(query, top_k=3):
            return []

        with patch('backend.app.db_manager', test_db), \
             patch('backend.app.query_vector_db', mock_empty_query), \
             patch('backend.app.llm_response', mock_llm):
            client = TestClient(app)
            response = client.get("/api/query?query=nonexistent topic xyz")

        data = response.json()
        assert data["status"] == "success"
        assert data["count"] == 0


class TestKeywordSearch:
    """Tests for keyword search (subject search via emails endpoint filtering).

    Note: The keyword search is done client-side in the frontend by filtering
    the emails list. These tests verify that the backend provides searchable data.
    """

    def test_emails_have_searchable_subject(self, test_db, user_with_emails):
        """Test that emails have subject field for searching."""
        from backend.app import app

        with patch('backend.app.db_manager', test_db):
            client = TestClient(app)
            response = client.get(f"/api/emails?user_id={user_with_emails.id}")

        emails = response.json()
        for email in emails:
            assert "subject" in email
            assert email["subject"] is not None

    def test_can_filter_emails_by_subject_keyword(self, test_db, user_with_emails):
        """Test that emails can be filtered by subject keyword (simulating frontend search)."""
        from backend.app import app

        with patch('backend.app.db_manager', test_db):
            client = TestClient(app)
            response = client.get(f"/api/emails?user_id={user_with_emails.id}")

        emails = response.json()

        # Simulate frontend keyword search for "Meeting"
        keyword = "meeting"
        filtered = [e for e in emails if keyword.lower() in e["subject"].lower()]

        assert len(filtered) == 1
        assert "Meeting" in filtered[0]["subject"]

    def test_keyword_search_case_insensitive(self, test_db, user_with_emails):
        """Test that keyword search works case-insensitively."""
        from backend.app import app

        with patch('backend.app.db_manager', test_db):
            client = TestClient(app)
            response = client.get(f"/api/emails?user_id={user_with_emails.id}")

        emails = response.json()

        # Search with different cases
        keyword_lower = "meeting"
        keyword_upper = "MEETING"
        keyword_mixed = "MeEtInG"

        filtered_lower = [e for e in emails if keyword_lower.lower() in e["subject"].lower()]
        filtered_upper = [e for e in emails if keyword_upper.lower() in e["subject"].lower()]
        filtered_mixed = [e for e in emails if keyword_mixed.lower() in e["subject"].lower()]

        assert len(filtered_lower) == len(filtered_upper) == len(filtered_mixed)

    def test_keyword_search_no_match(self, test_db, user_with_emails):
        """Test keyword search with no matching results."""
        from backend.app import app

        with patch('backend.app.db_manager', test_db):
            client = TestClient(app)
            response = client.get(f"/api/emails?user_id={user_with_emails.id}")

        emails = response.json()

        # Search for non-existent keyword
        keyword = "xyznonexistent123"
        filtered = [e for e in emails if keyword.lower() in e["subject"].lower()]

        assert len(filtered) == 0

    def test_keyword_search_partial_match(self, test_db, user_with_emails):
        """Test that partial keyword matches work."""
        from backend.app import app

        with patch('backend.app.db_manager', test_db):
            client = TestClient(app)
            response = client.get(f"/api/emails?user_id={user_with_emails.id}")

        emails = response.json()

        # Search for partial word "Meet" (should match "Meeting")
        keyword = "meet"
        filtered = [e for e in emails if keyword.lower() in e["subject"].lower()]

        assert len(filtered) >= 1

    def test_emails_sorted_by_date(self, test_db, user_with_emails):
        """Test that emails are returned sorted by date (newest first)."""
        from backend.app import app

        with patch('backend.app.db_manager', test_db):
            client = TestClient(app)
            response = client.get(f"/api/emails?user_id={user_with_emails.id}")

        emails = response.json()

        # Check emails are sorted by date descending
        dates = [e["date_sent"] for e in emails if e["date_sent"]]
        assert dates == sorted(dates, reverse=True)
