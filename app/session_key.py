"""Stable session signing key with secure production support via Google Cloud Secret Manager."""
import os
import secrets
from pathlib import Path

def _is_production():
    """Detect if running in production (App Engine)."""
    return bool(os.environ.get("GAE_RUNTIME"))

def _load_from_secret_manager():
    """Load SECRET_KEY from Google Cloud Secret Manager (production only)."""
    try:
        from google.cloud import secretmanager
        client = secretmanager.SecretManagerServiceClient()
        project_id = os.environ.get("GOOGLE_CLOUD_PROJECT") or "project-8adff5c8-05b0-4160-b71"
        secret_name = f"projects/{project_id}/secrets/autostack-secret-key/versions/latest"
        response = client.access_secret_version(request={"name": secret_name})
        key = response.payload.data.decode("UTF-8").strip()
        if len(key) < 32:
            raise RuntimeError("Invalid secret in Secret Manager; key too short")
        return key
    except Exception as e:
        if _is_production():
            raise RuntimeError(f"Failed to load SECRET_KEY from Secret Manager in production: {e}")
        return None

def load_session_key():
    if os.environ.get("SECRET_KEY"):
        return os.environ["SECRET_KEY"]

    secret_manager_key = _load_from_secret_manager()
    if secret_manager_key:
        return secret_manager_key

    if _is_production():
        raise RuntimeError("Production environment detected but SECRET_KEY not available; configure Secret Manager")

    path = Path(os.environ.get("AUTOSTACK_DATA_DIR", Path(__file__).resolve().parent.parent / "data")) / ".session_key"
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
