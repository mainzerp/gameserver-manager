from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Mod(Base):
    __tablename__ = "mods"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    server_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("servers.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(255), nullable=True)
    source: Mapped[str] = mapped_column(String(50), nullable=False)  # modrinth
    project_id: Mapped[str] = mapped_column(String(100), nullable=False)
    version_id: Mapped[str] = mapped_column(String(100), nullable=True)
    installed_version: Mapped[str] = mapped_column(String(100), nullable=True)
    latest_version: Mapped[str] = mapped_column(String(100), nullable=True)
    file_name: Mapped[str] = mapped_column(String(500), nullable=True)
    download_url: Mapped[str] = mapped_column(Text, nullable=True)
    auto_update: Mapped[bool] = mapped_column(Boolean, default=True)
    update_available: Mapped[bool] = mapped_column(Boolean, default=False)
    is_dependency: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="0"
    )
    max_compatible_mc_version: Mapped[str | None] = mapped_column(
        String(20), nullable=True
    )
    last_checked: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
