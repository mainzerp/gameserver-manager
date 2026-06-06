from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class WorkshopItemCreate(BaseModel):
    workshop_id: str
    name: str
    app_id: str


class WorkshopItemResponse(BaseModel):
    id: int
    server_id: int
    workshop_id: str
    app_id: str
    name: str
    installed: bool
    last_updated: Optional[datetime] = None
    created_at: Optional[str] = None


class WorkshopItemList(BaseModel):
    ok: bool
    data: list[WorkshopItemResponse]


class SteamAccountCreate(BaseModel):
    display_name: str
    username: str
    password: str
    steam_guard_type: str = "none"


class SteamAccountResponse(BaseModel):
    id: int
    display_name: str
    username: str
    steam_guard_type: str
    is_anonymous: bool
    created_at: Optional[str] = None
