from datetime import datetime, timezone

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ModProfile(Base):
    __tablename__ = "mod_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    server_type: Mapped[str] = mapped_column(String(50), nullable=False)
    loader: Mapped[str | None] = mapped_column(String(50), nullable=True)
    mc_version: Mapped[str | None] = mapped_column(String(20), nullable=True)
    mods_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
