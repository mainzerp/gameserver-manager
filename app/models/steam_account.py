import base64
import hashlib
import logging
from datetime import datetime, timezone

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import Boolean, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

logger = logging.getLogger(__name__)

_V2_SALT = b"gsm-fernet-salt-v2"
_V2_ITERATIONS = 100_000


def _derive_key_v2(secret: str) -> bytes:
    """Derive a Fernet key using PBKDF2-HMAC-SHA256."""
    key = hashlib.pbkdf2_hmac(
        "sha256", secret.encode("utf-8"), _V2_SALT, _V2_ITERATIONS, dklen=32
    )
    return base64.urlsafe_b64encode(key)


def _derive_key_legacy(secret: str) -> bytes:
    """Derive a Fernet key using the legacy SHA-256 method."""
    key = hashlib.sha256(secret.encode()).digest()
    return base64.urlsafe_b64encode(key)


def _get_fernet_keys() -> list[bytes]:
    from app.config import settings

    secret = settings.encryption_key or settings.secret_key
    return [_derive_key_v2(secret), _derive_key_legacy(secret)]


def encrypt_password(plain: str) -> str:
    from app.config import settings

    secret = settings.encryption_key or settings.secret_key
    key = _derive_key_v2(secret)
    return Fernet(key).encrypt(plain.encode()).decode()


def decrypt_password(encrypted: str) -> str:
    for key in _get_fernet_keys():
        try:
            return Fernet(key).decrypt(encrypted.encode()).decode()
        except InvalidToken:
            continue
    logger.warning("Failed to decrypt Steam account password (all keys exhausted)")
    raise ValueError("Invalid encryption key or corrupted data")


class SteamAccount(Base):
    __tablename__ = "steam_accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    display_name: Mapped[str] = mapped_column(String(100), nullable=False)
    username: Mapped[str] = mapped_column(String(100), nullable=False)
    password_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    steam_guard_type: Mapped[str] = mapped_column(
        String(20), default="none", server_default="none"
    )
    is_anonymous: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="0"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
