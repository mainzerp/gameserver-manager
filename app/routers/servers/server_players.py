import logging
import re

from fastapi import (
    APIRouter,
    Depends,
    Form,
    HTTPException,
    Request,
)
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.server import Server, ServerType
from app.routers.servers._shared import (
    get_current_user_dep,
    get_db,
    parse_player_list,
    require_server_access,
)
from app.services.player_manager import player_manager
from app.services.query_protocol import minecraft_query, steam_query
from app.services.rcon_client import RCONClient
from app.services.server_manager import server_manager

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(get_current_user_dep)])

@router.get("/servers/{server_id}/players", response_class=JSONResponse)
async def get_players(
    request: Request, server_id: int, db: AsyncSession = Depends(get_db)
):
    await require_server_access(request, server_id, "view", db)
    server = await db.get(Server, server_id)
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    if not server_manager.is_running(server_id, db=db):
        return JSONResponse({"players": [], "online": 0, "max": 0, "source": "offline"})

    # Try RCON first for Minecraft
    if server.rcon_enabled and server.rcon_port and server.rcon_password:
        client = RCONClient()
        try:
            authed = await client.connect(
                "127.0.0.1", server.rcon_port, server.rcon_password
            )
            if authed:
                response = await client.send_command("list")
                players = parse_player_list(response)
                return JSONResponse(
                    {
                        "players": [{"name": p} for p in players],
                        "online": len(players),
                        "max": 0,
                        "source": "rcon",
                    }
                )
        except Exception:
            pass
        finally:
            await client.close()

    # Fallback: query protocol
    if server.server_type in (ServerType.MINECRAFT_JAVA,):
        result = await minecraft_query.query("127.0.0.1", server.port)
        if result:
            return JSONResponse(
                {
                    "players": result["players"],
                    "online": result["online"],
                    "max": result["max"],
                    "source": "slp",
                }
            )
    elif server.server_type == ServerType.STEAM:
        result = await steam_query.query_players("127.0.0.1", server.port)
        if result is not None:
            return JSONResponse(
                {
                    "players": result,
                    "online": len(result),
                    "max": 0,
                    "source": "a2s",
                }
            )

    return JSONResponse({"players": [], "online": 0, "max": 0, "source": "unavailable"})


# -- Whitelist / Ban Management --------------------------------------------

@router.get("/servers/{server_id}/players/manage", response_class=HTMLResponse)
async def player_management_page(
    request: Request, server_id: int, db: AsyncSession = Depends(get_db)
):
    await require_server_access(request, server_id, "manage", db)
    return RedirectResponse(url=f"/servers/{server_id}?tab=config", status_code=302)

@router.post("/servers/{server_id}/whitelist/add")
async def whitelist_add(
    request: Request,
    server_id: int,
    name: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    await require_server_access(request, server_id, "manage", db)
    server = await db.get(Server, server_id)
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    player_manager.add_to_whitelist(server.path, name.strip())
    if (
        server_manager.is_running(server_id, db=db)
        and server.rcon_enabled
        and server.rcon_port
        and server.rcon_password
    ):
        await player_manager.rcon_whitelist_add(None, server, name.strip())

    return RedirectResponse(url=f"/servers/{server_id}?tab=config", status_code=303)

@router.post("/servers/{server_id}/whitelist/remove")
async def whitelist_remove(
    request: Request,
    server_id: int,
    name: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    await require_server_access(request, server_id, "manage", db)
    server = await db.get(Server, server_id)
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    player_manager.remove_from_whitelist(server.path, name.strip())
    if (
        server_manager.is_running(server_id, db=db)
        and server.rcon_enabled
        and server.rcon_port
        and server.rcon_password
    ):
        await player_manager.rcon_whitelist_remove(None, server, name.strip())

    return RedirectResponse(url=f"/servers/{server_id}?tab=config", status_code=303)

@router.post("/servers/{server_id}/ban")
async def ban_player_route(
    request: Request,
    server_id: int,
    name: str = Form(...),
    reason: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    await require_server_access(request, server_id, "manage", db)
    server = await db.get(Server, server_id)
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")


    if not re.match(r"^[a-zA-Z0-9_]{1,64}$", name.strip()):
        raise HTTPException(status_code=400, detail="Invalid player name")

    player_manager.ban_player(
        server.path, name.strip(), reason.strip() or "Banned by operator"
    )
    if (
        server_manager.is_running(server_id, db=db)
        and server.rcon_enabled
        and server.rcon_port
        and server.rcon_password
    ):
        await player_manager.rcon_ban(None, server, name.strip(), reason.strip())

    return RedirectResponse(url=f"/servers/{server_id}?tab=config", status_code=303)

@router.post("/servers/{server_id}/pardon")
async def pardon_player_route(
    request: Request,
    server_id: int,
    name: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    await require_server_access(request, server_id, "manage", db)
    server = await db.get(Server, server_id)
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    player_manager.pardon_player(server.path, name.strip())
    if (
        server_manager.is_running(server_id, db=db)
        and server.rcon_enabled
        and server.rcon_port
        and server.rcon_password
    ):
        await player_manager.rcon_pardon(None, server, name.strip())

    return RedirectResponse(url=f"/servers/{server_id}?tab=config", status_code=303)


# -- Server Configuration Editor -------------------------------------------

