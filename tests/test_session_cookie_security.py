"""SESSION_COOKIE_SECURE: off by default for local HTTP, on for HTTPS deployments, and login still works."""
import re

import pytest

from app import create_app
from app.main_db import create_tenant

PASSWORD = "cookie-test-password"


def _app(monkeypatch, value=None):
    if value is None:
        monkeypatch.delenv("SESSION_COOKIE_SECURE", raising=False)
    else:
        monkeypatch.setenv("SESSION_COOKIE_SECURE", value)
    app = create_app()
    app.config.update(TESTING=True)
    return app


def _tenant(username):
    tid, err = create_tenant("Cookie Shop", username, "cookie@example.com", PASSWORD)
    assert not err
    return tid


def _login(client, username):
    page = client.get("/login").get_data(as_text=True)
    token = re.search(r'name="csrf_token" value="([^"]+)"', page).group(1)
    return client.post("/login", data={"username": username, "password": PASSWORD, "csrf_token": token},
                       follow_redirects=False)


def _session_cookie_header(response):
    headers = response.headers.getlist("Set-Cookie")
    cookies = [h for h in headers if h.startswith("session=")]
    assert cookies, "login should set a session cookie"
    return cookies[0]


class TestSettingParsing:
    def test_default_is_off_for_local_http(self, monkeypatch):
        app = _app(monkeypatch)
        assert app.config["SESSION_COOKIE_SECURE"] is False

    @pytest.mark.parametrize("value", ["1", "true", "YES", " on "])
    def test_true_values_enable_secure_cookies(self, monkeypatch, value):
        assert _app(monkeypatch, value).config["SESSION_COOKIE_SECURE"] is True

    @pytest.mark.parametrize("value", ["0", "false", "no", "off", ""])
    def test_false_values_keep_secure_cookies_off(self, monkeypatch, value):
        assert _app(monkeypatch, value).config["SESSION_COOKIE_SECURE"] is False

    @pytest.mark.parametrize("value", ["ture", "2", "enabled"])
    def test_unknown_values_stop_startup(self, monkeypatch, value):
        with pytest.raises(ValueError, match="SESSION_COOKIE_SECURE"):
            _app(monkeypatch, value)


class TestCookieAttributes:
    def test_login_cookie_is_marked_secure_when_configured(self, monkeypatch):
        app = _app(monkeypatch, "1")
        _tenant("cookie_secure_on")
        response = _login(app.test_client(), "cookie_secure_on")
        assert "Secure" in _session_cookie_header(response)
        assert "HttpOnly" in _session_cookie_header(response)

    def test_login_cookie_is_not_secure_by_default(self, monkeypatch):
        app = _app(monkeypatch)
        _tenant("cookie_secure_off")
        response = _login(app.test_client(), "cookie_secure_off")
        assert "Secure" not in _session_cookie_header(response)
        assert "HttpOnly" in _session_cookie_header(response)


class TestLoginStillWorks:
    def test_local_http_login_keeps_the_session(self, monkeypatch):
        app = _app(monkeypatch)
        _tenant("http_dev_user")
        client = app.test_client()
        assert _login(client, "http_dev_user").status_code == 302
        assert client.get("/").status_code == 200

    def test_https_login_keeps_the_session_when_secure_is_on(self, monkeypatch):
        app = _app(monkeypatch, "1")
        _tenant("https_user")
        client = app.test_client()
        # Browsers send Referer on HTTPS requests, and Flask-WTF checks it there, so the test sends one too.
        https = {"base_url": "https://localhost", "headers": {"Referer": "https://localhost/login"}}
        page = client.get("/login", **https).get_data(as_text=True)
        token = re.search(r'name="csrf_token" value="([^"]+)"', page).group(1)
        response = client.post("/login", data={"username": "https_user", "password": PASSWORD,
                                               "csrf_token": token}, **https)
        assert response.status_code == 302
        assert client.get("/", **https).status_code == 200
