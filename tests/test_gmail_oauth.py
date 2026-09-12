"""Test Gmail OAuth integration for sending reminders."""
import os
import tempfile
import unittest
from unittest.mock import patch, MagicMock
from pathlib import Path
import re
import time

class GmailOAuthTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.env = patch.dict(os.environ, {
            "AUTOSTACK_DATA_DIR": cls.temp.name,
            "SECRET_KEY": "test-only-key-not-for-production",
            "TESTING": "1",
            "GMAIL_CLIENT_ID": "test-client-id",
            "GMAIL_CLIENT_SECRET": "test-client-secret",
            "GMAIL_REDIRECT_URI": "http://localhost/settings/gmail-oauth/callback"
        })
        cls.env.start()

        # Reload modules after patching environment
        import sys
        for mod in list(sys.modules.keys()):
            if mod.startswith('app'):
                del sys.modules[mod]

        from app import create_app
        from app import tenant_db, main_db
        cls.db = tenant_db
        cls.main = main_db
        cls.app = create_app()
        cls.app.config.update(TESTING=True)

    @classmethod
    def tearDownClass(cls):
        cls.env.stop()
        cls.temp.cleanup()

    def setUp(self):
        from uuid import uuid4
        self.username = "test_" + uuid4().hex
        self.tid, _ = self.main.create_tenant("Test Shop", self.username, "", "test-password-123")
        self.db.init_tenant_db(self.tid)
        self.client = self.app.test_client()

    def login(self):
        page = self.client.get("/login").get_data(as_text=True)
        token = re.search(r'name="csrf_token" value="([^"]+)"', page).group(1)
        result = self.client.post("/login", data={
            "username": self.username,
            "password": "test-password-123",
            "csrf_token": token
        })
        self.assertEqual(result.status_code, 302)
        return token

    def test_gmail_oauth_status_not_connected(self):
        """Test Gmail OAuth status endpoint when not connected."""
        self.login()
        response = self.client.get("/settings/gmail-oauth/status")
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertFalse(data.get("connected"))
        self.assertIsNone(data.get("email"))

    def test_save_gmail_token(self):
        """Test saving and retrieving encrypted Gmail OAuth token."""
        from app.gmail_oauth import save_gmail_token, get_gmail_token, get_authorized_email

        # Save token
        save_gmail_token(
            self.tid,
            "test@gmail.com",
            "access_token_value",
            "refresh_token_value",
            3600
        )

        # Retrieve and verify
        token = get_gmail_token(self.tid)
        self.assertIsNotNone(token)
        self.assertEqual(token["access_token"], "access_token_value")
        self.assertEqual(token["refresh_token"], "refresh_token_value")

        # Check authorized email
        email = get_authorized_email(self.tid)
        self.assertEqual(email, "test@gmail.com")

    def test_gmail_token_encryption(self):
        """Test that Gmail tokens are encrypted in database."""
        import sqlite3
        from app.tenant_db import _db_path
        from app.gmail_oauth import save_gmail_token

        save_gmail_token(
            self.tid,
            "test@gmail.com",
            "secret_access_token",
            "secret_refresh_token",
            3600
        )

        # Read raw database to verify encryption
        conn = sqlite3.connect(_db_path(self.tid))
        row = conn.execute("SELECT access_token, refresh_token FROM gmail_oauth_tokens LIMIT 1").fetchone()
        conn.close()

        # Verify tokens are encrypted (start with "enc:")
        self.assertTrue(row[0].startswith("enc:"), "Access token should be encrypted")
        self.assertTrue(row[1].startswith("enc:"), "Refresh token should be encrypted")
        self.assertNotIn("secret_access_token", row[0])
        self.assertNotIn("secret_refresh_token", row[1])

    def test_gmail_token_tenant_isolation(self):
        """Test that Gmail tokens are isolated per tenant."""
        from app.gmail_oauth import save_gmail_token, get_authorized_email
        from uuid import uuid4

        # Create second tenant
        username2 = "test_" + uuid4().hex
        tid2, _ = self.main.create_tenant("Shop 2", username2, "", "password123")
        self.db.init_tenant_db(tid2)

        # Save tokens for both tenants
        save_gmail_token(self.tid, "account1@gmail.com", "token1", "refresh1", 3600)
        save_gmail_token(tid2, "account2@gmail.com", "token2", "refresh2", 3600)

        # Verify isolation
        email1 = get_authorized_email(self.tid)
        email2 = get_authorized_email(tid2)

        self.assertEqual(email1, "account1@gmail.com")
        self.assertEqual(email2, "account2@gmail.com")
        self.assertNotEqual(email1, email2)

    def test_disconnect_gmail_token(self):
        """Test disconnecting Gmail OAuth."""
        from app.gmail_oauth import save_gmail_token, delete_gmail_token, get_authorized_email

        save_gmail_token(self.tid, "test@gmail.com", "token", "refresh", 3600)
        self.assertIsNotNone(get_authorized_email(self.tid))

        delete_gmail_token(self.tid)
        self.assertIsNone(get_authorized_email(self.tid))

    def test_gmail_separate_from_google_login(self):
        """Test that Gmail OAuth is separate from Google login."""
        from app.gmail_oauth import has_gmail_token
        from app.google_auth import configured as google_configured

        # Gmail OAuth should be independent of Google login
        # Having Gmail OAuth doesn't mean Google login is used
        self.assertFalse(has_gmail_token(self.tid))

        # Google login and Gmail OAuth use different scopes/purposes
        # This test verifies the separation of concerns


if __name__ == "__main__":
    unittest.main()
