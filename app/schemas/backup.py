from typing import Optional

from pydantic import BaseModel


class BackupItem(BaseModel):
    id: int
    file_name: str
    size_bytes: int
    note: Optional[str] = None
    created_at: Optional[str] = None


class BackupListData(BaseModel):
    ok: bool
    data: list[BackupItem]


class BackupCreateData(BaseModel):
    ok: bool
    data: BackupItem
