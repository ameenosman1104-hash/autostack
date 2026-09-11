import importlib.util
import pathlib
import unittest
from unittest.mock import patch
ROOT = pathlib.Path(__file__).resolve().parents[1]
def load(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
v = load("validation", "app/validation.py")
h = load("safe_http", "app/services/safe_http.py")
class Adjustments(unittest.TestCase):
    def test_payments(self):
        self.assertEqual(v.payment_amount("8000.50"), 8000.5)
        for bad in ["nan", "inf", "-1", "0", "1.001", "abc"]:
            with self.assertRaises(ValueError): v.payment_amount(bad)
    def test_intervals(self):
        self.assertEqual(v.reminder_interval("28"), 28)
        for bad in ["0", "-1", "366", "nan", "1.5", ""]:
            with self.assertRaises(ValueError): v.reminder_interval(bad)
    def test_private_urls(self):
        with patch.object(h.socket, "getaddrinfo", return_value=[(0,0,0,"",("127.0.0.1",80))]):
            with self.assertRaises(ValueError): h.validate_url("http://example.com")
        for url in ["file:///etc/passwd", "http://user:pass@example.com", "https://example.com:22"]:
            with self.assertRaises(ValueError): h.validate_url(url)
    def test_public_url(self):
        with patch.object(h.socket, "getaddrinfo", return_value=[(0,0,0,"",("8.8.8.8",443))]):
            self.assertEqual(h.validate_url("https://example.com"), "https://example.com")
if __name__ == "__main__": unittest.main()
