"""Encrypt/decrypt sensitive credentials stored in database."""
import os
import base64
from cryptography.fernet import Fernet


# Settings keys that should be encrypted
SENSITIVE_KEYS = {
    "email_password",
    "callmebot_api_key",
}

# Cache test key so it stays consistent during test suite
_test_key_cache = None


def get_encryption_key():
    """Get encryption key from environment or raise error if not configured.

    For production, set AUTOSTACK_ENCRYPTION_KEY environment variable to a 32-byte
    value encoded as base64, or set AUTOSTACK_ENCRYPTION_KEY_FILE to path of file
    containing the key.

    For testing, uses a cached test key so decryption works consistently.
    """
    global _test_key_cache

    # Check environment variable first
    key_str = os.environ.get("AUTOSTACK_ENCRYPTION_KEY")
    if key_str:
        try:
            return base64.urlsafe_b64decode(key_str)
        except Exception as e:
            raise RuntimeError(f"Invalid AUTOSTACK_ENCRYPTION_KEY format: {e}")

    # Check key file
    key_file = os.environ.get("AUTOSTACK_ENCRYPTION_KEY_FILE")
    if key_file and os.path.exists(key_file):
        try:
            with open(key_file, "rb") as f:
                return base64.urlsafe_b64decode(f.read().strip())
        except Exception as e:
            raise RuntimeError(f"Failed to read encryption key from {key_file}: {e}")

    # Test mode: use cached key or generate one
    if os.environ.get("TESTING"):
        if _test_key_cache is None:
            _test_key_cache = Fernet.generate_key()
        return _test_key_cache

    raise RuntimeError(
        "Encryption key not configured. Set AUTOSTACK_ENCRYPTION_KEY or "
        "AUTOSTACK_ENCRYPTION_KEY_FILE environment variable for production use."
    )


def encrypt_value(plaintext):
    """Encrypt a string value. Returns encrypted string (prefixed with 'enc:' marker)."""
    if not plaintext:
        return plaintext

    key = get_encryption_key()
    cipher = Fernet(key)
    encrypted = cipher.encrypt(plaintext.encode("utf-8"))
    return f"enc:{encrypted.decode('utf-8')}"


def decrypt_value(ciphertext):
    """Decrypt a value if it's marked as encrypted, otherwise return as-is."""
    if not ciphertext or not isinstance(ciphertext, str):
        return ciphertext

    if not ciphertext.startswith("enc:"):
        return ciphertext

    try:
        key = get_encryption_key()
        cipher = Fernet(key)
        encrypted = ciphertext[4:].encode("utf-8")
        decrypted = cipher.decrypt(encrypted)
        return decrypted.decode("utf-8")
    except Exception as e:
        # Return encrypted value if decryption fails (key not available, corrupted, etc)
        return None


def is_encrypted(value):
    """Check if a setting value is encrypted."""
    return isinstance(value, str) and value.startswith("enc:")


def should_encrypt_key(key):
    """Check if a setting key should be encrypted."""
    return key in SENSITIVE_KEYS


def generate_key_file(path):
    """Generate a new encryption key and save to file. Returns the key."""
    key = Fernet.generate_key()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    # Write with restricted permissions
    with open(path, "wb") as f:
        f.write(base64.urlsafe_b64encode(key))
    # Restrict file permissions on Unix-like systems
    try:
        os.chmod(path, 0o600)
    except:
        pass
    return key
