"""Encrypt and decrypt values stored in environment variables.

The Fernet key must be kept outside the encrypted value. Store it in a
secret manager or inject it as ``TOKEN_ENCRYPTION_KEY`` at runtime.
"""

import os

from cryptography.fernet import Fernet, InvalidToken


KEY_ENV_NAME = "TOKEN_ENCRYPTION_KEY"


def generate_encryption_key() -> str:
    """Generate a new key suitable for ``TOKEN_ENCRYPTION_KEY``."""
    return Fernet.generate_key().decode("ascii")


def _fernet_from_env() -> Fernet:
    key = os.getenv(KEY_ENV_NAME)
    if not key:
        raise RuntimeError(
            f"{KEY_ENV_NAME} is not set; keep the encryption key separate "
            "from the encrypted token"
        )

    try:
        return Fernet(key.encode("ascii"))
    except (UnicodeEncodeError, ValueError) as error:
        raise ValueError(f"{KEY_ENV_NAME} is not a valid Fernet key") from error


def encrypt_token(token: str) -> str:
    """Encrypt a token and return an environment-variable-safe string."""
    if not token:
        raise ValueError("token must not be empty")

    return _fernet_from_env().encrypt(token.encode("utf-8")).decode("ascii")


def decrypt_token(encrypted_token: str) -> str:
    """Decrypt an encrypted token, failing explicitly for invalid ciphertext."""
    if not encrypted_token:
        raise ValueError("encrypted_token must not be empty")

    try:
        plaintext = _fernet_from_env().decrypt(encrypted_token.encode("ascii"))
    except (UnicodeEncodeError, InvalidToken) as error:
        raise ValueError("encrypted_token is invalid or was encrypted with another key") from error

    return plaintext.decode("utf-8")
