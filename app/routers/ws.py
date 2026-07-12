import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.services.audit_service import audit_service
from app.services.server_manager import server_manager

logger = logging.getLogger(__name__)

router = APIRouter()


@router.websocket("/ws/console/{server_id}")
async def console_ws(websocket: WebSocket, server_id: int):
    if not websocket.session.get("user_id"):
        await websocket.close(code=4001, reason="Not authenticated")
        return

    await websocket.accept()

    from sqlalchemy import select

    from app.database import async_session
    from app.models.server_access import ServerAccess
    from app.models.user import User

    user_id = websocket.session.get("user_id")
    async with async_session() as db:
        user = await db.get(User, user_id)
        if not user:
            await websocket.close(code=4001, reason="Not authenticated")
            return
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
                return

    sp = server_manager.processes.get(server_id)
    if not sp:
        await websocket.send_json({"type": "error", "message": "Server is not running"})
        await websocket.close()
        return

    # Send existing log history
    for line in sp.log_lines:
        await websocket.send_json({"type": "log", "data": line})

    # Subscribe to new output
    async def on_output(line: str):
        try:
            await websocket.send_json({"type": "log", "data": line})
        except Exception:
            sp.unsubscribe(on_output)

    sp.subscribe(on_output)

    try:
        while True:
            data = await websocket.receive_text()
            msg = json.loads(data)
            if msg.get("type") == "command":
                command = msg.get("data", "").strip()
                if not command:
                    await websocket.send_json(
                        {"type": "error", "message": "Empty command rejected"}
                    )
                    continue
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
    except WebSocketDisconnect:
        sp.unsubscribe(on_output)
    except Exception:
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
