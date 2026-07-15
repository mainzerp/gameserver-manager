import json
import re

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.webhook import Webhook
from app.services.auth import get_current_user_dep, require_role
from app.template_utils import templates
from app.utils.security import validate_webhook_url

router = APIRouter(prefix="/webhooks", dependencies=[Depends(get_current_user_dep)])

VALID_EVENTS = ["start", "stop", "crash", "backup", "update"]
URL_PATTERN = re.compile(r"^https?://\S+$")


@router.get("/", response_class=HTMLResponse)
async def webhook_list(request: Request, db: AsyncSession = Depends(get_db)):
    await require_role(request, "admin")
    result = await db.execute(select(Webhook).order_by(Webhook.created_at.desc()))
    webhooks = result.scalars().all()
    return templates.TemplateResponse(request, "webhooks.html", {
            "webhooks": webhooks,
            "valid_events": VALID_EVENTS,
        })


@router.post("/create")
async def create_webhook(
    request: Request,
    name: str = Form(...),
    url: str = Form(...),
    secret: str = Form(""),
    events: str = Form("start,stop,crash"),
    custom_headers: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    await require_role(request, "admin")

    if not name.strip():
        raise HTTPException(status_code=400, detail="Webhook name is required")
    if not URL_PATTERN.match(url):
        raise HTTPException(status_code=400, detail="Invalid webhook URL")
    if len(url) > 500:
        raise HTTPException(status_code=400, detail="URL too long (max 500 chars)")

    event_list = [e.strip() for e in events.split(",") if e.strip()]
    for e in event_list:
        if e not in VALID_EVENTS:
            raise HTTPException(status_code=400, detail=f"Invalid event: {e}")

    headers_json = None
    if custom_headers.strip():
        try:
            parsed = json.loads(custom_headers)
            if not isinstance(parsed, dict):
                raise ValueError()
            headers_json = json.dumps(parsed)
        except (json.JSONDecodeError, ValueError):
            raise HTTPException(
                status_code=400, detail="Custom headers must be valid JSON object"
            )

    webhook = Webhook(
        name=name.strip(),
        url=url.strip(),
        secret=secret.strip() or None,
        events=",".join(event_list),
        headers=headers_json,
    )
    db.add(webhook)
    await db.commit()
    return RedirectResponse(url="/webhooks", status_code=303)


@router.post("/{webhook_id}/update")
async def update_webhook(
    request: Request,
    webhook_id: int,
    name: str = Form(...),
    url: str = Form(...),
    secret: str = Form(""),
    events: str = Form("start,stop,crash"),
    custom_headers: str = Form(""),
    enabled: bool = Form(True),
    db: AsyncSession = Depends(get_db),
):
    await require_role(request, "admin")
    webhook = await db.get(Webhook, webhook_id)
    if not webhook:
        raise HTTPException(status_code=404, detail="Webhook not found")

    if not URL_PATTERN.match(url):
        raise HTTPException(status_code=400, detail="Invalid webhook URL")

    event_list = [e.strip() for e in events.split(",") if e.strip()]
    for e in event_list:
        if e not in VALID_EVENTS:
            raise HTTPException(status_code=400, detail=f"Invalid event: {e}")

    headers_json = None
    if custom_headers.strip():
        try:
            parsed = json.loads(custom_headers)
            if not isinstance(parsed, dict):
                raise ValueError()
            headers_json = json.dumps(parsed)
        except (json.JSONDecodeError, ValueError):
            raise HTTPException(
                status_code=400, detail="Custom headers must be valid JSON object"
            )

    webhook.name = name.strip()
    webhook.url = url.strip()
    webhook.secret = secret.strip() or None
    webhook.events = ",".join(event_list)
    webhook.headers = headers_json
    webhook.enabled = enabled
    await db.commit()
    return RedirectResponse(url="/webhooks", status_code=303)


@router.post("/{webhook_id}/delete")
async def delete_webhook(
    request: Request,
    webhook_id: int,
    db: AsyncSession = Depends(get_db),
):
    await require_role(request, "admin")
    webhook = await db.get(Webhook, webhook_id)
    if not webhook:
        raise HTTPException(status_code=404, detail="Webhook not found")
    await db.delete(webhook)
    await db.commit()
    return RedirectResponse(url="/webhooks", status_code=303)


@router.post("/{webhook_id}/test", response_class=JSONResponse)
async def test_webhook(
    request: Request,
    webhook_id: int,
    db: AsyncSession = Depends(get_db),
):
    await require_role(request, "admin")
    webhook = await db.get(Webhook, webhook_id)
    if not webhook:
        return JSONResponse(
            {"ok": False, "error": "Webhook not found"}, status_code=404
        )

    import hashlib
    import hmac

    import httpx

    ok, error = validate_webhook_url(webhook.url)
    if not ok:
        return JSONResponse({"ok": False, "error": error}, status_code=400)

    payload = json.dumps(
        {
            "event": "test",
            "server_name": "Test Server",
            "message": "This is a test webhook from GameServer Manager.",
        }
    )
    req_headers = {"Content-Type": "application/json"}
    if webhook.headers:
        try:
            req_headers.update(json.loads(webhook.headers))
        except (json.JSONDecodeError, TypeError):
            pass
    if webhook.secret:
        sig = hmac.new(
            webhook.secret.encode(), payload.encode(), hashlib.sha256
        ).hexdigest()
        req_headers["X-Webhook-Signature"] = f"sha256={sig}"

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(webhook.url, content=payload, headers=req_headers)
        return JSONResponse({"ok": True, "status_code": resp.status_code})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})
