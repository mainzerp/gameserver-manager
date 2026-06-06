from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class ServerSummary(BaseModel):
    id: int
    name: str
    type: str
    status: str
    port: int
    running: bool


class ServerDetailItem(BaseModel):
    id: int
    name: str
    type: str
    status: str
    port: int
    path: str
    mc_version: Optional[str] = None
    loader: Optional[str] = None
    min_memory: int
    max_memory: int
    auto_start: bool
    running: bool
    created_at: Optional[str] = None


class ServerListData(BaseModel):
    ok: bool
    data: list[ServerSummary]


class ServerDetailData(BaseModel):
    ok: bool
    data: ServerDetailItem


class ServerActionData(BaseModel):
    ok: bool
    data: dict


class ServerStatsData(BaseModel):
    ok: bool
    data: dict
