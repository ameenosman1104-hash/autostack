import os


def _awaiting_verified_restore():
    if os.environ.get("AUTOSTACK_REQUIRE_RESTORED_DATA") != "1":
        return False
    data_dir = os.environ.get("AUTOSTACK_DATA_DIR", "")
    return not (data_dir and os.path.isfile(os.path.join(data_dir, ".restore_verified")))


if _awaiting_verified_restore():
    def app(environ, start_response):
        start_response("503 Service Unavailable", [
            ("Content-Type", "text/plain; charset=utf-8"),
            ("Retry-After", "600"),
            ("Cache-Control", "no-store"),
        ])
        return [b"AutoStack is paused until verified database restoration is complete."]
else:
    from app import create_app
    app = create_app()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(debug=False, host="0.0.0.0", port=port)
