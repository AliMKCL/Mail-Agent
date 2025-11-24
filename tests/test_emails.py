"""
Tests for email functionality.

Tests cover:
- Each user can only see their own emails
- Emails can be opened fully (full body visible)
- Email list retrieval
"""

import pytest
from fastapi.testclient import TestClient
from backend.app import app


class TestEmailRetrieval:
    """Tests for retrieving emails from the API."""

    def test_get_emails_for_user(self, client, user_with_emails):
        """Test that a user can retrieve their emails."""
        response = client.get(f"/api/emails?user_id={user_with_emails.id}")

        assert response.status_code == 200
        emails = response.json()
        assert isinstance(emails, list)
        assert len(emails) == 2

    def test_email_contains_full_body(self, client, user_with_emails):
        """Test that retrieved emails contain full body text."""
        response = client.get(f"/api/emails?user_id={user_with_emails.id}")

        emails = response.json()
        assert len(emails) > 0

        # Check first email has full body
        email = emails[0]
        assert "body_text" in email
        assert email["body_text"] is not None
        assert len(email["body_text"]) > 0

    def test_email_contains_html_body_when_available(self, client, user_with_emails):
        """Test that emails include HTML body when available."""
        response = client.get(f"/api/emails?user_id={user_with_emails.id}")

        emails = response.json()
        # Find the email with HTML body (msg_001)
        email_with_html = next((e for e in emails if e.get("body_html")), None)
        assert email_with_html is not None
        assert "<p>" in email_with_html["body_html"]

    def test_email_fields_present(self, client, user_with_emails):
        """Test that all required email fields are present."""
        response = client.get(f"/api/emails?user_id={user_with_emails.id}")

        emails = response.json()
        required_fields = ["id", "subject", "sender", "recipient", "date_sent", "snippet", "body_text"]

        for email in emails:
            for field in required_fields:
                assert field in email, f"Missing field: {field}"


class TestEmailIsolation:
    """Tests for email isolation between users."""

    def test_user_sees_only_own_emails(self, client, user_with_emails, second_user_with_emails):
        """Test that each user only sees their own emails."""
        # Get first user's emails
        response1 = client.get(f"/api/emails?user_id={user_with_emails.id}")
        emails1 = response1.json()

        # Get second user's emails
        response2 = client.get(f"/api/emails?user_id={second_user_with_emails.id}")
        emails2 = response2.json()

        # First user should have 2 emails
        assert len(emails1) == 2
        # Second user should have 1 email
        assert len(emails2) == 1

        # Verify email content is different
        emails1_ids = {e["id"] for e in emails1}
        emails2_ids = {e["id"] for e in emails2}
        assert emails1_ids.isdisjoint(emails2_ids), "Users should not share any emails"

    def test_user_cannot_see_other_user_emails(self, client, user_with_emails, second_user_with_emails):
        """Test that user1's emails don't appear in user2's list."""
        # Get second user's emails
        response = client.get(f"/api/emails?user_id={second_user_with_emails.id}")
        emails = response.json()

        # Check none of user1's email subjects appear
        subjects = [e["subject"] for e in emails]
        assert "Test Email 1" not in subjects
        assert "Meeting Tomorrow" not in subjects
        # But user2's email should be there
        assert "Second User Email" in subjects

    def test_nonexistent_user_returns_empty(self, client, user_with_emails):
        """Test that querying for non-existent user returns empty list."""
        response = client.get("/api/emails?user_id=9999")

        assert response.status_code == 200
        assert response.json() == []


class TestEmailLimits:
    """Tests for email pagination/limits."""

    def test_email_limit_parameter(self, client, user_with_emails):
        """Test that limit parameter works correctly."""
        response = client.get(f"/api/emails?user_id={user_with_emails.id}&limit=1")

        emails = response.json()
        assert len(emails) == 1

    def test_default_limit(self, client, user_with_emails):
        """Test that default limit returns all emails when under limit."""
        response = client.get(f"/api/emails?user_id={user_with_emails.id}")

        emails = response.json()
        # Default limit is 50, we have 2 emails
        assert len(emails) == 2
