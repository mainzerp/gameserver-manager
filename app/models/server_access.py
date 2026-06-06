from sqlalchemy import ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ServerAccess(Base):
    __tablename__ = "server_access"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=False, index=True
    )
    server_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("servers.id"), nullable=False, index=True
    )
    permission: Mapped[str] = mapped_column(String(20), nullable=False, default="view")

    __table_args__ = (UniqueConstraint("user_id", "server_id"),)
