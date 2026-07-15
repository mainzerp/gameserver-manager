from fastapi import APIRouter

from app.routers.api_v1.backups import router as backups_router
from app.routers.api_v1.schedules import router as schedules_router
from app.routers.api_v1.servers import router as servers_router
from app.routers.api_v1.system import router as system_router
from app.routers.api_v1.versions import router as versions_router

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(servers_router, tags=["Servers"])
api_router.include_router(backups_router, tags=["Backups"])
api_router.include_router(schedules_router, tags=["Schedules"])
api_router.include_router(system_router, tags=["System"])
api_router.include_router(versions_router, tags=["Versions"])
