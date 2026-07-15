import os

import pytest

os.environ.setdefault("GSM_DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("GSM_SECRET_KEY", "test-secret-key-for-encryption")

from app.models.steam_account import (
    SteamAccount,
    decrypt_password,
    decrypt_totp_secret,
    encrypt_password,
    encrypt_totp_secret,
    generate_steam_totp_code,
)


def test_password_encryption_roundtrip():
    plain = "my-steam-password"
    encrypted = encrypt_password(plain)
    assert encrypted != plain
    assert decrypt_password(encrypted) == plain


def test_totp_secret_encryption_roundtrip():
    secret = "JBSWY3DPEHPK3PXP"
    encrypted = encrypt_totp_secret(secret)
    assert encrypted != secret
    assert decrypt_totp_secret(encrypted) == secret


def test_generate_steam_totp_code_returns_digits():
    # Use a known base32 secret; pyotp will produce a 5-digit code.
    secret = "JBSWY3DPEHPK3PXP"
    code = generate_steam_totp_code(secret)
    assert code is not None
    assert len(code) == 5
    assert code.isdigit()


def test_generate_steam_totp_code_with_spaces():
    secret = "JBSW Y3DP EHPK 3PXP"
    code = generate_steam_totp_code(secret)
    assert code is not None
    assert len(code) == 5


def test_generate_steam_totp_code_empty():
    assert generate_steam_totp_code("") is None
    assert generate_steam_totp_code(None) is None


def test_steam_account_totp_field_exists():
    account = SteamAccount(
        display_name="Test",
        username="test",
        password_encrypted=encrypt_password("pass"),
        steam_guard_type="totp",
        steam_guard_secret_encrypted=encrypt_totp_secret("JBSWY3DPEHPK3PXP"),
    )
    assert account.steam_guard_type == "totp"
    assert account.steam_guard_secret_encrypted is not None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
