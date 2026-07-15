from pydantic import BaseModel


class SystemStatsResponse(BaseModel):
    ok: bool
    data: dict


class VersionItem(BaseModel):
    app_name: str
    version: str


class VersionResponse(BaseModel):
    ok: bool
    data: VersionItem


class UpdateStatusResponse(BaseModel):
    ok: bool
    data: dict
