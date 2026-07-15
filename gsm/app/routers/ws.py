import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import select

from app.database import async_session
from app.models.server_access import ServerAccess
from app.models.user import User
from app.services.audit_service import audit_service
from app.services.server_manager import server_manager

logger = logging.getLogger(__name__)

router = APIRouter()


async def _authorize_console_user(websocket: WebSocket, server_id: int) -> User | None:
    """Authenticate and authorize the user for the console WebSocket."""
    user_id = websocket.session.get("user_id")
    if not user_id:
        await websocket.close(code=4001, reason="Not authenticated")
        return None

    async with async_session() as db:
        user = await db.get(User, user_id)
        if not user:
            await websocket.close(code=4001, reason="Not authenticated")
            return None
        if user.role != "admin":
            result = await db.execute(
                select(ServerAccess).where(
                    ServerAccess.user_id == user.id,
                    ServerAccess.server_id == server_id,
                )
            )
            access = result.scalars().first()
            if not access or access.permission not in ("operate", "manage"):
                await websocket.close(code=4003, reason="No access to this server")
                return None
    return user


async def _send_log_history(websocket: WebSocket, log_lines: list[str]) -> None:
    for line in log_lines:
        await websocket.send_json({"type": "log", "data": line})


async def _handle_console_command(
    websocket: WebSocket,
    user: User,
    server_id: int,
    sp,
    msg: dict,
) -> None:
    """Handle a command message sent from the console client."""
    if msg.get("type") != "command":
        return
    command = msg.get("data", "").strip()
    if not command:
        await websocket.send_json(
            {"type": "error", "message": "Empty command rejected"}
        )
        return
    audit_service.create_task(
        audit_service.log(
            user_id=user.id,
            username=user.username,
            action="console.command",
            resource_type="server",
            resource_id=str(server_id),
            details=f"command={command}",
        )
    )
    await sp.send_command(command)


@router.websocket("/ws/console/{server_id}")
async def console_ws(websocket: WebSocket, server_id: int):
    user = await _authorize_console_user(websocket, server_id)
    if user is None:
        return

    await websocket.accept()

    sp = server_manager.processes.get(server_id)
    if not sp:
        await websocket.send_json({"type": "error", "message": "Server is not running"})
        await websocket.close()
        return

    await _send_log_history(websocket, sp.log_lines)

    async def on_output(line: str):
        try:
            await websocket.send_json({"type": "log", "data": line})
        except Exception:
            logger.debug("Console WebSocket send failed, unsubscribing", exc_info=True)
            sp.unsubscribe(on_output)

    sp.subscribe(on_output)

    try:
        while True:
            data = await websocket.receive_text()
            msg = json.loads(data)
            await _handle_console_command(websocket, user, server_id, sp, msg)
    except WebSocketDisconnect:
        sp.unsubscribe(on_output)
    except Exception:
        logger.exception("Unexpected console WebSocket error")
        sp.unsubscribe(on_output)


@router.websocket("/ws/steamcmd/{server_id}")
async def steamcmd_ws(websocket: WebSocket, server_id: int):
    """WebSocket endpoint for SteamCMD installation/update progress."""
    if not websocket.session.get("user_id"):
        await websocket.close(code=4001, reason="Not authenticated")
        return

    await websocket.accept()

    import asyncio

    from app.services.steamcmd import steamcmd

    queue: asyncio.Queue = asyncio.Queue()
    steamcmd.subscribe_progress(server_id, queue)

    try:
        await websocket.send_json(steamcmd.get_operation_snapshot(server_id))
        while True:
            try:
                data = await asyncio.wait_for(queue.get(), timeout=30.0)
                await websocket.send_json(data)
            except asyncio.TimeoutError:
                snapshot = steamcmd.get_operation_snapshot(server_id)
                await websocket.send_json(
                    {
                        "type": "heartbeat",
                        "server_id": server_id,
                        "operation_id": snapshot.get("operation_id"),
                        "operation": snapshot.get("operation"),
                        "status": snapshot.get("status"),
                    }
                )
            except WebSocketDisconnect:
                break
    finally:
        steamcmd.unsubscribe_progress(server_id, queue)
