"""Stable local session signing key; production should supply SECRET_KEY."""
import os
import secrets
from pathlib import Path

def load_session_key():
    if os.environ.get("SECRET_KEY"):
        return os.environ["SECRET_KEY"]
    path = Path(__file__).resolve().parent.parent / "data" / ".session_key"
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        pass
    else:
        with os.fdopen(fd, "w") as handle:
            handle.write(secrets.token_hex(32))
    key = path.read_text().strip()
    if len(key) < 32:
        raise RuntimeError("Invalid session key; configure SECRET_KEY")
    return key
