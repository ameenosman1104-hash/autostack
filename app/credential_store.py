"""Encrypt/decrypt sensitive credentials stored in database."""
import os
from cryptography.fernet import Fernet


# Settings keys that should be encrypted
SENSITIVE_KEYS = {
    "email_password",
    "callmebot_api_key",
}

# Cache test key so it stays consistent during test suite
_test_key_cache = None


def get_encryption_key():
    """Get encryption key from environment or file.

    Fernet.generate_key() returns base64-encoded bytes ready for use.
    Store and retrieve as-is without re-encoding.

    For production, set AUTOSTACK_ENCRYPTION_KEY environment variable to the
    base64 string from Fernet.generate_key(), or AUTOSTACK_ENCRYPTION_KEY_FILE
    to path of file containing the key (one line, no encoding).

    For testing, uses a cached test key so decryption works consistently.
    """
    global _test_key_cache

    # Check environment variable first
    key_str = os.environ.get("AUTOSTACK_ENCRYPTION_KEY")
    if key_str:
        try:
            # Key should be base64-encoded bytes as a string
            # Fernet expects bytes, so encode if we got a string
            if isinstance(key_str, str):
                key_bytes = key_str.encode("utf-8")
            else:
                key_bytes = key_str

            # Validate that it's a valid Fernet key
            Fernet(key_bytes)  # This will raise if invalid
            return key_bytes
        except Exception as e:
            raise RuntimeError(
                f"Invalid AUTOSTACK_ENCRYPTION_KEY: {e}\n"
                "Expected output from Fernet.generate_key(), stored as a string in environment."
            )

    # Check key file
    key_file = os.environ.get("AUTOSTACK_ENCRYPTION_KEY_FILE")
    if key_file:
        if not os.path.exists(key_file):
            raise RuntimeError(f"Encryption key file not found: {key_file}")
        try:
            with open(key_file, "rb") as f:
                key_bytes = f.read().strip()
            # Validate that it's a valid Fernet key
            Fernet(key_bytes)
            return key_bytes
        except Exception as e:
            raise RuntimeError(
                f"Failed to read or validate encryption key from {key_file}: {e}\n"
                "File should contain output from Fernet.generate_key() (one line, no wrapping)."
            )

    # Test mode: use cached key or generate one
    if os.environ.get("TESTING"):
        if _test_key_cache is None:
            _test_key_cache = Fernet.generate_key()
        return _test_key_cache

    raise RuntimeError(
        "Encryption key not configured. Set AUTOSTACK_ENCRYPTION_KEY (as base64 string) or "
        "AUTOSTACK_ENCRYPTION_KEY_FILE (as path to key file). "
        "Generate key with: python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'"
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
    """Decrypt a value if it's marked as encrypted, otherwise return as-is.

    Raises RuntimeError if decryption fails - never silently returns None.
    """
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
    except RuntimeError:
        # Re-raise key configuration errors
        raise
    except Exception as e:
        # Raise on decrypt failure - don't silently return None
        raise RuntimeError(
            f"Failed to decrypt credential: {e}\n"
            "This usually means the encryption key has changed or is unavailable.\n"
            "Encrypted value: {ciphertext[:50]}..."
        )


def is_encrypted(value):
    """Check if a setting value is encrypted."""
    return isinstance(value, str) and value.startswith("enc:")


def should_encrypt_key(key):
    """Check if a setting key should be encrypted."""
    return key in SENSITIVE_KEYS


def generate_key_file(path):
    """Generate a new encryption key and save to file. Returns the key bytes.

    The key is stored as-is (base64 bytes from Fernet.generate_key()),
    never re-encoded.
    """
    key = Fernet.generate_key()
    os.makedirs(os.path.dirname(path), exist_ok=True)

    # Write key as-is (it's already base64-encoded by Fernet.generate_key())
    with open(path, "wb") as f:
        f.write(key)

    # Restrict file permissions on Unix-like systems
    try:
        os.chmod(path, 0o600)
    except:
        pass

    return key
