from fastapi import APIRouter, Depends

from app.config import settings as settings
from app.database import async_session as async_session
from app.database import get_db as get_db
from app.routers.servers import (
    server_config,
    server_control,
    server_core,
    server_crud,
    server_logs,
    server_misc,
    server_players,
    server_steam,
    server_worlds,
)
from app.routers.servers._shared import (
    parse_player_list as parse_player_list,
)
from app.routers.servers._shared import (
    refresh_workshop_item_metadata as refresh_workshop_item_metadata,
)
from app.routers.servers._shared import (
    run_background_steam_update as run_background_steam_update,
)
from app.routers.servers._shared import (
    run_background_steam_update_then_start as run_background_steam_update_then_start,
)
from app.routers.servers._shared import (
    run_create_steam_install as run_create_steam_install,
)
from app.routers.servers._shared import (
    run_manual_steam_validate as run_manual_steam_validate,
)
from app.routers.servers._shared import (
    run_workshop_install as run_workshop_install,
)
from app.routers.servers._shared import (
    spawn_background_task as spawn_background_task,
)
from app.services.audit_service import audit_service as audit_service
from app.services.auth import (
    get_current_user_dep as get_current_user_dep,
)
from app.services.auth import (
    require_role as require_role,
)
from app.services.auth import (
    require_server_access as require_server_access,
)
from app.services.mod_updater import mod_updater as mod_updater
from app.services.player_manager import player_manager as player_manager
from app.services.port_manager import port_manager as port_manager
from app.services.query_protocol import (
    minecraft_query as minecraft_query,
)
from app.services.query_protocol import (
    steam_query as steam_query,
)
from app.services.resource_monitor import resource_monitor as resource_monitor
from app.services.server_manager import server_manager as server_manager
from app.services.server_templates import get_templates as get_templates
from app.services.server_updater import server_updater as server_updater
from app.services.steamcmd import generate_start_command as generate_start_command
from app.services.steamcmd import steamcmd as steamcmd
from app.services.task_registry import task_registry as task_registry
from app.services.world_manager import world_manager as world_manager

router = APIRouter(dependencies=[Depends(get_current_user_dep)])

router.include_router(server_core.router)
router.include_router(server_crud.router)
router.include_router(server_control.router)
router.include_router(server_config.router)
router.include_router(server_steam.router)
router.include_router(server_players.router)
router.include_router(server_worlds.router)
router.include_router(server_logs.router)
router.include_router(server_misc.router)

# Backward-compatible aliases for the old helper names used by tests and other code
_spawn_background_task = spawn_background_task
_refresh_workshop_item_metadata = refresh_workshop_item_metadata
_run_create_steam_install = run_create_steam_install
_run_manual_steam_validate = run_manual_steam_validate
_run_background_steam_update = run_background_steam_update
_run_background_steam_update_then_start = run_background_steam_update_then_start
_run_workshop_install = run_workshop_install
_parse_player_list = parse_player_list

__all__ = [
    "router",
    "settings",
    "async_session",
    "get_db",
    "get_current_user_dep",
    "require_role",
    "require_server_access",
    "audit_service",
    "mod_updater",
    "player_manager",
    "port_manager",
    "minecraft_query",
    "steam_query",
    "resource_monitor",
    "server_manager",
    "get_templates",
    "server_updater",
    "generate_start_command",
    "steamcmd",
    "task_registry",
    "world_manager",
    "spawn_background_task",
    "refresh_workshop_item_metadata",
    "run_background_steam_update",
    "run_background_steam_update_then_start",
    "run_create_steam_install",
    "run_manual_steam_validate",
    "run_workshop_install",
    "parse_player_list",
]
