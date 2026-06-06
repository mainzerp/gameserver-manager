from typing import Optional

from pydantic import BaseModel


class ScheduleItem(BaseModel):
    id: int
    name: str
    server_id: int
    task_type: str
    cron_expression: str
    command: Optional[str] = None
    enabled: bool
    last_run: Optional[str] = None
    created_at: Optional[str] = None


class ScheduleListData(BaseModel):
    ok: bool
    data: list[ScheduleItem]


class ScheduleCreateResult(BaseModel):
    id: int
    name: str


class ScheduleCreateData(BaseModel):
    ok: bool
    data: ScheduleCreateResult
