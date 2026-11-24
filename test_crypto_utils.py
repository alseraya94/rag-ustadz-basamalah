import pytest

from crypto_utils import decrypt_token, encrypt_token, generate_encryption_key


def test_token_round_trip(monkeypatch):
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", generate_encryption_key())

    encrypted = encrypt_token("token-rahasia-✓")

    assert encrypted != "token-rahasia-✓"
    assert decrypt_token(encrypted) == "token-rahasia-✓"


def test_decrypt_rejects_wrong_key(monkeypatch):
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", generate_encryption_key())
    encrypted = encrypt_token("token-rahasia")
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", generate_encryption_key())

    with pytest.raises(ValueError, match="invalid or was encrypted"):
        decrypt_token(encrypted)


def test_missing_key_fails_explicitly(monkeypatch):
    monkeypatch.delenv("TOKEN_ENCRYPTION_KEY", raising=False)

    with pytest.raises(RuntimeError, match="TOKEN_ENCRYPTION_KEY is not set"):
        encrypt_token("token-rahasia")
