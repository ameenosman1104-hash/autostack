"""Run only against a disposable data directory, never production databases."""
import os
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path
import re

class LaunchSafety(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.env = patch.dict(os.environ, {
            "AUTOSTACK_DATA_DIR": cls.temp.name,
            "SECRET_KEY": "test-only-key-not-for-production",
            "TESTING": "1"
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
        self.db.add_debtor(self.tid,"Customer","","customer@example.com",1000,"2026-09-09","email",28,"")
        self.did = self.db.get_all_debtors(self.tid)[0]["id"]
        self.client = self.app.test_client()

    def login(self):
        page = self.client.get("/login").get_data(as_text=True)
        token = re.search(r'name="csrf_token" value="([^"]+)"', page).group(1)
        result = self.client.post("/login",data={"username":self.username,"password":"test-password-123","csrf_token":token})
        self.assertEqual(result.status_code,302)
        return token

    def test_settings_save_with_invalid_debtor_date(self):
        """Ensure saving settings doesn't crash if a debtor has missing/invalid purchase date."""
        # Create a debtor with an invalid date format to test error handling
        import sqlite3
        from app.tenant_db import _db_path
        conn = sqlite3.connect(_db_path(self.tid))
        # Insert debtor with invalid date format
        conn.execute("INSERT INTO debtors (name, phone, email, amount_owed, date_of_purchase, notify_method, reminder_days, notes, reminder_mode) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                     ("NoDateDebtor", "", "test@example.com", 500, "not-a-date", "email", 28, "", "default"))
        conn.commit()
        conn.close()

        # Save settings should not crash despite invalid debtor
        page = self.client.get("/login").get_data(as_text=True)
        token = re.search(r'name="csrf_token" value="([^"]+)"', page).group(1)
        response = self.client.post("/login", data={
            "username": self.username,
            "password": "test-password-123",
            "csrf_token": token
        })

        # Now save settings
        page = self.client.get("/settings/").get_data(as_text=True)
        token = re.search(r'name="csrf_token" value="([^"]+)"', page).group(1)
        response = self.client.post("/settings/", data={
            "csrf_token": token,
            "default_reminder_days": "35"
        })

        self.assertEqual(response.status_code, 302, "Settings save should redirect (not crash)")

        # Verify the setting was actually saved
        self.assertEqual(self.db.get_setting(self.tid, "default_reminder_days"), "35")

    def test_settings_save_email_password_encrypted(self):
        """Ensure saving new email password is encrypted and persists."""
        import sqlite3
        from app.tenant_db import _db_path

        token = self.login()

        # Save a new email password
        page = self.client.get("/settings/").get_data(as_text=True)
        token = re.search(r'name="csrf_token" value="([^"]+)"', page).group(1)
        response = self.client.post("/settings/", data={
            "csrf_token": token,
            "email_sender": "test@gmail.com",
            "email_password": "my-secret-app-password-123"
        })
        self.assertEqual(response.status_code, 302, "Settings save should redirect successfully")

        # Verify password was encrypted in database (read raw value)
        conn = sqlite3.connect(_db_path(self.tid))
        row = conn.execute("SELECT value FROM settings WHERE key='email_password'").fetchone()
        conn.close()
        self.assertIsNotNone(row, "Password setting should exist in database")
        stored_encrypted = row[0]
        self.assertTrue(stored_encrypted.startswith("enc:"), "Password should be encrypted in database")
        self.assertNotIn("my-secret-app-password-123", stored_encrypted, "Plaintext password should not appear in database")

        # Verify decryption works (get_setting automatically decrypts)
        decrypted = self.db.get_setting(self.tid, "email_password")
        self.assertEqual(decrypted, "my-secret-app-password-123", "Decrypted password should match original")

    def test_settings_save_masked_password_preserves_existing(self):
        """Ensure masked password (****) preserves existing value."""
        # Set initial password
        self.db.save_setting(self.tid, "email_password", "original-password-123")

        token = self.login()

        # Submit masked password (user didn't change it)
        page = self.client.get("/settings/").get_data(as_text=True)
        token = re.search(r'name="csrf_token" value="([^"]+)"', page).group(1)
        response = self.client.post("/settings/", data={
            "csrf_token": token,
            "email_sender": "test@gmail.com",
            "email_password": "●●●●●●●●●●●●●●●●●●"  # Masked password
        })
        self.assertEqual(response.status_code, 302, "Settings save should redirect successfully")

        # Verify original password is preserved
        decrypted = self.db.get_setting(self.tid, "email_password")
        self.assertEqual(decrypted, "original-password-123", "Masked password should preserve original value")

    def test_settings_clear_email_password(self):
        """Ensure clear_email_password checkbox deletes saved password."""
        # Set initial password
        self.db.save_setting(self.tid, "email_password", "original-password-123")
        self.assertEqual(self.db.get_setting(self.tid, "email_password"), "original-password-123")

        token = self.login()

        # Submit clear checkbox
        page = self.client.get("/settings/").get_data(as_text=True)
        token = re.search(r'name="csrf_token" value="([^"]+)"', page).group(1)
        response = self.client.post("/settings/", data={
            "csrf_token": token,
            "email_sender": "test@gmail.com",
            "clear_email_password": "1"
        })
        self.assertEqual(response.status_code, 302, "Settings save should redirect successfully")

        # Verify password is deleted
        deleted_value = self.db.get_setting(self.tid, "email_password", default="")
        self.assertEqual(deleted_value, "", "Password should be deleted")

    def test_reminder_display_ignores_stale_interval_cache(self):
        self.db.save_setting(self.tid, "default_reminder_days", "28")
        self.db.update_debtor(self.tid, self.did, reminder_mode="custom_interval",
                              reminder_interval_days=7, next_reminder_date="2026-09-23")
        self.assertEqual(self.db.next_reminder(self.db.get_debtor(self.tid,self.did),self.tid)[0], "16 Sep 2026")
        token=self.login()
        self.assertIn("16 Sep 2026", self.client.get("/debtors/").get_data(as_text=True))
        result=self.client.post(f"/debtors/{self.did}/set-next-reminder",data={"csrf_token":token,"mode":"custom_interval","interval":"21"})
        self.assertEqual(result.json["next_date"], "2026-09-30")
        self.assertIn("30 Sep 2026", self.client.get("/debtors/").get_data(as_text=True))
        self.db.save_setting(self.tid,"default_reminder_days","42")
        self.assertIn("30 Sep 2026", self.client.get("/debtors/").get_data(as_text=True))
        result=self.client.post(f"/debtors/{self.did}/set-next-reminder",data={"csrf_token":token,"mode":"default"})
        self.assertEqual(result.json["next_date"], "2026-10-21")
        self.assertIn("21 Oct 2026",self.client.get("/debtors/").get_data(as_text=True))
        self.db.update_debtor(self.tid,self.did,reminder_mode="manual",next_reminder_date="2026-09-18")
        page=self.client.get("/debtors/").get_data(as_text=True)
        self.assertIn("18 Sep 2026",page)
        self.assertIn("Custom reminder date",page)

    def test_custom_save_advances_past_successful_send(self):
        self.db.update_debtor(self.tid,self.did,last_reminded="2026-09-25")
        self.assertEqual(self.db.set_custom_interval_reminder(self.tid,self.did,7),"2026-09-30")
        self.assertEqual(self.db.calculate_next_reminder(self.tid,self.did),"2026-09-30")

    def test_partial_payment_consistent(self):
        self.db.add_payment(self.tid,self.did,250,recorded_by="test")
        debtor = self.db.get_debtor(self.tid,self.did)
        self.assertEqual(debtor["amount_owed"],1000)  # historical amount preserved
        self.assertEqual(self.db.outstanding_balance(debtor),750)
        self.assertEqual(self.db.get_stats(self.tid)["total_owed"],750)
        self.assertEqual(self.db.get_debtor_payment_summary(self.tid,self.did)["remaining"],750)
        from app.services.debt_notifier import _build_message
        self.assertIn("750.00",_build_message(self.tid,debtor))
        self.login()
        page=self.client.get("/debtors/")
        self.assertEqual(page.status_code,200)
        self.assertIn("750.00",page.get_data(as_text=True))

    def test_overpayment_and_missing_debtor_do_not_write(self):
        for did, amount in [(self.did,1001),(999999,20)]:
            with self.assertRaises(ValueError):self.db.add_payment(self.tid,did,amount)
        self.assertEqual(self.db.get_payment_history(self.tid,self.did),[])

    def test_paid_account_does_not_send(self):
        self.db.add_payment(self.tid,self.did,1000)
        from app.services.debt_notifier import send_reminder
        with patch("app.services.debt_notifier._via_email") as sender:
            ok,_=send_reminder(self.tid,self.db.get_debtor(self.tid,self.did))
        self.assertFalse(ok)
        sender.assert_not_called()

    def test_default_and_custom_reminders(self):
        self.db.save_setting(self.tid,"default_reminder_days","28")
        self.assertEqual(self.db.calculate_next_reminder(self.tid,self.did),"2026-10-07")
        self.db.set_custom_interval_reminder(self.tid,self.did,56)
        self.db.save_setting(self.tid,"default_reminder_days","42")
        self.assertEqual(self.db.calculate_next_reminder(self.tid,self.did),"2026-11-04")
        self.assertEqual(self.db.set_default_reminder_mode(self.tid,self.did),"2026-10-21")
        self.db.update_debtor(self.tid,self.did,reminder_mode="manual",next_reminder_date="2026-09-20")
        self.assertEqual(self.db.calculate_next_reminder(self.tid,self.did),"2026-09-20")

    def test_recurring_schedule_uses_purchase_anchor(self):
        self.db.save_setting(self.tid,"default_reminder_days","28")
        self.db.update_debtor(self.tid,self.did,last_reminded="2026-10-10")
        self.assertEqual(self.db.calculate_next_reminder(self.tid,self.did),"2026-11-04")

    def test_registered_pages_render(self):
        self.login()
        for path in ["/", "/inventory/", "/purchase-orders/", "/debtors/", "/settings/", "/reports/", "/notifications/", "/recycle-bin/", "/inventory/import", "/debtors/import"]:
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code,200)

    def test_authenticated_payment_post_with_csrf(self):
        token=self.login()
        response=self.client.post(f"/debtors/{self.did}/record-payment",data={"csrf_token":token,"amount":"250","payment_date":"2026-09-11"})
        self.assertEqual(response.status_code,302)
        self.assertEqual(self.db.outstanding_balance(self.db.get_debtor(self.tid,self.did)),750)

    def test_settings_keep_saved_password(self):
        self.db.save_setting(self.tid,"email_password","test-only-credential")
        token=self.login()
        response=self.client.post("/settings/",data={"csrf_token":token,"default_reminder_days":"42","email_password":""})
        self.assertEqual(response.status_code,302)
        self.assertEqual(self.db.get_setting(self.tid,"email_password"),"test-only-credential")
        self.assertEqual(self.db.get_debtor(self.tid,self.did)["next_reminder_date"],"2026-10-21")

    def test_csrf_required(self):
        self.assertEqual(self.client.post("/login",data={"username":self.username}).status_code,400)
        self.login()
        self.assertEqual(self.client.post(f"/debtors/{self.did}/delete").status_code,400)
        self.assertIsNotNone(self.db.get_debtor(self.tid,self.did))

    def test_private_page_headers(self):
        self.login()
        r=self.client.get("/debtors/")
        self.assertEqual(r.status_code,200)
        self.assertIn("no-store",r.headers["Cache-Control"])
        self.assertEqual(r.headers["X-Frame-Options"],"SAMEORIGIN")

    def test_tenant_isolation_and_admin_denied(self):
        other,_=self.main.create_tenant("Other Shop",self.username+"other","","test-password-123")
        self.db.init_tenant_db(other)
        self.db.add_debtor(other,"OTHER PRIVATE CUSTOMER","","",100,"2026-09-09","email",28,"")
        self.login()
        page=self.client.get("/debtors/").get_data(as_text=True)
        self.assertNotIn("OTHER PRIVATE CUSTOMER",page)
        self.assertEqual(self.client.get("/admin/api/tenants").status_code,302)

    def test_disabled_session_denied(self):
        self.login()
        self.main.update_tenant(self.tid,is_active=0)
        self.assertEqual(self.client.get("/debtors/").status_code,302)

    def google_callback(self, verified=True):
        from unittest.mock import MagicMock
        provider=MagicMock()
        provider.authorize_access_token.return_value={"id_token":"test-token", "userinfo":{"sub":self.username,"email":self.username+"@example.com","email_verified":verified}}
        with patch.dict(self.app.config, GOOGLE_CLIENT_ID="test", GOOGLE_CLIENT_SECRET="test", GOOGLE_REDIRECT_URI="https://example.com/auth/google/callback"), patch("app.google_auth.provider",return_value=provider):
            return self.client.get("/auth/google/callback")

    def google_form(self):
        self.google_callback()
        page=self.client.get("/auth/google/complete").get_data(as_text=True)
        return re.search(r'name="csrf_token" value="([^"]+)"',page).group(1)

    def test_google_configuration_and_unverified_identity(self):
        with patch.dict(self.app.config, GOOGLE_CLIENT_ID="", GOOGLE_CLIENT_SECRET="", GOOGLE_REDIRECT_URI=""):
            self.assertNotIn("Continue with Google",self.client.get("/login").get_data(as_text=True))
            self.assertEqual(self.client.get("/auth/google").status_code,302)
        self.google_callback(False)
        with self.client.session_transaction() as session:
            self.assertNotIn("google_pending",session)
            self.assertNotIn("_user_id",session)

    def test_google_link_requires_password_and_preserves_blocking(self):
        token=self.google_form()
        data={"csrf_token":token,"action":"link","username":self.username,"password":"incorrect"}
        self.assertEqual(self.client.post("/auth/google/complete",data=data).status_code,200)
        with self.client.session_transaction() as session:self.assertNotIn("_user_id",session)
        data["password"]="test-password-123"
        self.assertEqual(self.client.post("/auth/google/complete",data=data).status_code,302)
        with self.client.session_transaction() as session:
            self.assertEqual(int(session["_user_id"]),self.tid)
            session.clear()
        self.main.update_tenant(self.tid,is_active=0)
        self.google_callback()
        with self.client.session_transaction() as session:self.assertNotIn("_user_id",session)

    def test_google_new_account_and_csrf(self):
        token=self.google_form()
        data={"action":"create","username":self.username+"new","business_name":"New shop"}
        self.assertEqual(self.client.post("/auth/google/complete",data=data).status_code,400)
        data["csrf_token"]=token
        self.assertEqual(self.client.post("/auth/google/complete",data=data).status_code,302)
        user=self.main.get_user_by_username(data["username"])
        self.assertIsNotNone(user)
        with self.client.session_transaction() as session:
            self.assertEqual(int(session["_user_id"]),user["id"])
            session.clear()
        self.google_callback()
        with self.client.session_transaction() as session:self.assertEqual(int(session["_user_id"]),user["id"])

    def test_google_expired_setup_and_invalid_state(self):
        with self.client.session_transaction() as session:
            session["google_pending"]={"sub":"expired","email":"test@example.com","expires":0}
        self.assertEqual(self.client.get("/auth/google/complete").status_code,302)
        with patch.dict(self.app.config, GOOGLE_CLIENT_ID="test", GOOGLE_CLIENT_SECRET="test", GOOGLE_REDIRECT_URI="https://example.com/auth/google/callback"):
            self.assertEqual(self.client.get("/auth/google/callback?code=fake&state=invalid").status_code,302)
        with self.client.session_transaction() as session:self.assertNotIn("_user_id",session)

    def test_login_username_field_always_empty(self):
        """Verify username field is never pre-populated by AutoStack."""
        # Test 1: Fresh login page (GET request)
        page = self.client.get("/login").get_data(as_text=True)
        self.assertNotIn('name="username" value="', page, "Username field should have no value attribute on GET")
        self.assertNotIn('name="username" value=\'"', page, "Username field should have no value attribute on GET")

        # Test 2: After failed login (POST with wrong password)
        page = self.client.get("/login").get_data(as_text=True)
        token = re.search(r'name="csrf_token" value="([^"]+)"', page).group(1)
        response = self.client.post("/login", data={
            "csrf_token": token,
            "username": "test@example.com",
            "password": "wrongpassword"
        })
        page = response.get_data(as_text=True)
        self.assertNotIn('value="test@example.com"', page, "Username field should be empty after failed login")
        self.assertNotIn('value="wrongusername"', page, "Username field should not contain any username after failed login")

        # Test 3: After disabled account attempt
        self.main.update_tenant(self.tid, is_active=0)
        page = self.client.get("/login").get_data(as_text=True)
        token = re.search(r'name="csrf_token" value="([^"]+)"', page).group(1)
        response = self.client.post("/login", data={
            "csrf_token": token,
            "username": self.username,
            "password": "test-password-123"
        })
        page = response.get_data(as_text=True)
        self.assertNotIn(f'value="{self.username}"', page, "Username field should be empty even after disabled account login attempt")

    def test_google_returning_user_skips_linking_form(self):
        from unittest.mock import MagicMock
        google_sub = "returning-user-google-sub"

        # Step 1: Create an account and link Google (first-time flow)
        token = self.google_form()
        data = {"csrf_token": token, "action": "create", "username": "returninguser", "business_name": "Returning Shop"}
        self.assertEqual(self.client.post("/auth/google/complete", data=data).status_code, 302)

        # Verify user is now linked
        user = self.main.get_user_by_username("returninguser")
        self.assertIsNotNone(user)

        # Step 2: Logout
        with self.client.session_transaction() as session:
            session.clear()

        # Step 3: Return user clicks "Continue with Google" with the SAME Google account
        # They should go DIRECTLY to dashboard, NOT to the linking form
        provider = MagicMock()
        provider.authorize_access_token.return_value = {
            "id_token": "test-token",
            "userinfo": {
                "sub": self.username,  # Same Google sub as initial creation
                "email": self.username + "@example.com",
                "email_verified": True
            }
        }

        with patch.dict(self.app.config, GOOGLE_CLIENT_ID="test", GOOGLE_CLIENT_SECRET="test", GOOGLE_REDIRECT_URI="https://example.com/auth/google/callback"), \
             patch("app.google_auth.provider", return_value=provider):
            # Call the callback - should redirect to dashboard, not to google_complete.html
            response = self.client.get("/auth/google/callback", follow_redirects=False)

        # Should be a 302 redirect to dashboard (not to google_complete)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.location, "/")  # Redirects to dashboard

        # Verify user is logged in
        with self.client.session_transaction() as session:
            self.assertIn("_user_id", session)
            self.assertEqual(int(session["_user_id"]), user["id"])

if __name__ == "__main__":unittest.main()
