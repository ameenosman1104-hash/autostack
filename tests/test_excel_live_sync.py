"""Test live Excel sync via Microsoft Graph."""
import os, tempfile, unittest, re
from unittest.mock import patch

class ExcelLiveSyncTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.env = patch.dict(os.environ, {
            "AUTOSTACK_DATA_DIR": cls.temp.name,
            "SECRET_KEY": "test-only-key",
            "TESTING": "1",
            "EXCEL_CLIENT_ID": "test-client-id",
            "EXCEL_CLIENT_SECRET": "test-client-secret",
            "EXCEL_REDIRECT_URI": "http://localhost/settings/excel/callback"
        })
        cls.env.start()

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

    def test_excel_oauth_status_not_connected(self):
        """Test Excel status when not connected."""
        self.login()
        response = self.client.get("/settings/excel/status")
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertFalse(data.get("connected"))

    def test_excel_connection_storage(self):
        """Test storing and retrieving Excel connection."""
        from app.excel_oauth import save_excel_connection, get_excel_connection

        save_excel_connection(
            self.tid,
            "My Excel",
            "workbook123",
            "Sheet1",
            "Table1",
            "access_token_value",
            "refresh_token_value",
            3600,
            "graph_user_123",
            '{"name": "Name", "email": "Email", "amount": "Amount"}',
            "Invoice No."
        )

        conn = get_excel_connection(self.tid)
        self.assertIsNotNone(conn)
        self.assertEqual(conn["workbook_id"], "workbook123")
        self.assertEqual(conn["access_token"], "access_token_value")

    def test_live_sync_check_interval(self):
        """Test that sync respects interval."""
        from app.services.live_excel_sync import check_and_sync_excel
        from app.excel_oauth import save_excel_connection

        save_excel_connection(
            self.tid,
            "Test",
            "wb1",
            "Sheet1",
            "Table1",
            "token",
            "refresh",
            3600,
            "user1",
            "{}",
            "ID"
        )

        # First check should indicate sync is needed
        self.assertTrue(check_and_sync_excel(self.tid))

if __name__ == "__main__":
    unittest.main()
