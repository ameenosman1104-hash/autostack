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
        cls.env = patch.dict(os.environ, {"AUTOSTACK_DATA_DIR": cls.temp.name, "SECRET_KEY": "test-only-key-not-for-production"})
        cls.env.start()
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

if __name__ == "__main__":unittest.main()
