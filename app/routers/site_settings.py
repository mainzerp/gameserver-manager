import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.site_settings import SiteSettings
from app.services import settings_service
from app.services.auth import get_current_user_dep, require_role
from app.template_utils import templates

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/settings", dependencies=[Depends(get_current_user_dep)])

SMTP_EVENTS = ["crash", "backup_failed", "start", "stop"]
DISCORD_EVENTS = ["start", "stop", "crash", "backup"]
TELEGRAM_EVENTS = ["start", "stop", "crash", "backup", "high_cpu", "high_memory"]


async def _get_row(db: AsyncSession) -> SiteSettings:
    result = await db.execute(select(SiteSettings).where(SiteSettings.id == 1))
    row = result.scalars().first()
    if row is None:
        row = SiteSettings(id=1)
        db.add(row)
        await db.commit()
        await db.refresh(row)
    return row


@router.get("/", response_class=HTMLResponse)
async def settings_page(request: Request, db: AsyncSession = Depends(get_db)):
    await require_role(request, "admin")
    row = await _get_row(db)

    from app.models.steam_account import SteamAccount

    steam_accounts_result = await db.execute(
        select(SteamAccount).order_by(SteamAccount.display_name)
    )
    steam_accounts = steam_accounts_result.scalars().all()

    from app.services.steamcmd import steamcmd

    return templates.TemplateResponse(
        "site_settings.html",
        {
            "request": request,
            "s": row,
            "smtp_events": SMTP_EVENTS,
            "discord_events": DISCORD_EVENTS,
            "telegram_events": TELEGRAM_EVENTS,
            "has_smtp_password": bool(row.smtp_password_enc),
            "saved": request.query_params.get("saved") == "1",
            "steam_accounts": steam_accounts,
            "steamcmd_available": steamcmd.is_available,
        },
    )


@router.post("/", response_class=HTMLResponse)
async def settings_save(request: Request, db: AsyncSession = Depends(get_db)):
    await require_role(request, "admin")

    form = await request.form()

    def _bool(key: str) -> bool:
        return key in form and form[key] in ("true", "on", "1", "yes")

    smtp_notify_list = form.getlist("smtp_notify_events")
    discord_notify_list = form.getlist("discord_notify_events")
    telegram_notify_list = form.getlist("telegram_notify_events")

    try:
        smtp_port = int(form.get("smtp_port", 587))
    except (ValueError, TypeError):
        smtp_port = 587

    form_data = {
        "smtp_enabled": _bool("smtp_enabled"),
        "smtp_host": (form.get("smtp_host") or "").strip(),
        "smtp_port": smtp_port,
        "smtp_user": (form.get("smtp_user") or "").strip(),
        "smtp_password": (form.get("smtp_password") or "").strip(),
        "smtp_use_tls": _bool("smtp_use_tls"),
        "smtp_from_address": (form.get("smtp_from_address") or "").strip(),
        "smtp_to_addresses": (form.get("smtp_to_addresses") or "").strip(),
        "smtp_notify_events": ",".join(e for e in smtp_notify_list if e in SMTP_EVENTS)
        or "crash,backup_failed",
        "totp_global_enabled": _bool("totp_global_enabled"),
        "multi_node_enabled": _bool("multi_node_enabled"),
        "webauthn_enabled": _bool("webauthn_enabled"),
        "webauthn_rp_id": (form.get("webauthn_rp_id") or "").strip() or "localhost",
        "webauthn_origin": (form.get("webauthn_origin") or "").strip()
        or "https://localhost:8443",
        "discord_webhook_url": (form.get("discord_webhook_url") or "").strip(),
        "discord_notify_events": ",".join(
            e for e in discord_notify_list if e in DISCORD_EVENTS
        )
        or "start,stop,crash,backup",
        "telegram_bot_token": (form.get("telegram_bot_token") or "").strip(),
        "telegram_chat_id": (form.get("telegram_chat_id") or "").strip(),
        "telegram_notify_events": ",".join(
            e for e in telegram_notify_list if e in TELEGRAM_EVENTS
        )
        or "crash",
        "backup_external_path": (form.get("backup_external_path") or "").strip(),
        "steam_api_key": (form.get("steam_api_key") or "").strip(),
    }

    await settings_service.save_to_db(db, form_data)
    return RedirectResponse(url="/settings/?saved=1", status_code=303)


@router.post("/test-smtp")
async def test_smtp(request: Request, db: AsyncSession = Depends(get_db)):
    await require_role(request, "admin")
    from app.config import settings as cfg

    if not cfg.smtp_enabled or not cfg.smtp_host:
        return JSONResponse(
            {"ok": False, "error": "SMTP is not configured or disabled."}
        )
    try:
        import aiosmtplib
    except ImportError:
        return JSONResponse(
            {
                "ok": False,
                "error": "aiosmtplib is not installed. Add it to requirements.txt.",
            }
        )
    try:
        async with aiosmtplib.SMTP(
            hostname=cfg.smtp_host,
            port=cfg.smtp_port,
            use_tls=cfg.smtp_use_tls,
        ) as smtp:
            if cfg.smtp_user and cfg.smtp_password:
                await smtp.login(cfg.smtp_user, cfg.smtp_password)
        return JSONResponse({"ok": True})
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)})


@router.post("/test-discord")
async def test_discord(request: Request, db: AsyncSession = Depends(get_db)):
    await require_role(request, "admin")
    import httpx

    from app.config import settings as cfg

    if not cfg.discord_webhook_url:
        return JSONResponse(
            {"ok": False, "error": "Discord webhook URL is not configured."}
        )
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                cfg.discord_webhook_url,
                json={"content": "GameServer Manager: test notification"},
                timeout=10,
            )
        if resp.status_code in (200, 204):
            return JSONResponse({"ok": True})
        return JSONResponse(
            {"ok": False, "error": f"Discord returned HTTP {resp.status_code}"}
        )
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)})


@router.post("/test-telegram")
async def test_telegram(request: Request, db: AsyncSession = Depends(get_db)):
    await require_role(request, "admin")
    import httpx

    from app.config import settings as cfg

    if not cfg.telegram_bot_token or not cfg.telegram_chat_id:
        return JSONResponse(
            {"ok": False, "error": "Telegram bot token or chat ID is not configured."}
        )
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"https://api.telegram.org/bot{cfg.telegram_bot_token}/sendMessage",
                json={
                    "chat_id": cfg.telegram_chat_id,
                    "text": "GameServer Manager: test notification",
                },
            )
        data = resp.json()
        if data.get("ok"):
            return JSONResponse({"ok": True})
        return JSONResponse(
            {"ok": False, "error": data.get("description", "Unknown error")}
        )
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)})


@router.post("/steamcmd/install")
async def install_steamcmd(request: Request, db: AsyncSession = Depends(get_db)):
    await require_role(request, "admin")
    from app.services.steamcmd import steamcmd

    await steamcmd.ensure_available()
    return RedirectResponse(url="/settings/?saved=1", status_code=303)


@router.post("/steam-accounts/add")
async def add_steam_account(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    await require_role(request, "admin")
    form = await request.form()
    display_name = (form.get("display_name") or "").strip()
    username = (form.get("username") or "").strip()
    password = (form.get("password") or "").strip()
    steam_guard_type = (form.get("steam_guard_type") or "none").strip()
    steam_guard_secret = (form.get("steam_guard_secret") or "").strip()

    if not display_name or not username or not password:
        return RedirectResponse(url="/settings/", status_code=303)

    from app.models.steam_account import (
        SteamAccount,
        encrypt_password,
        encrypt_totp_secret,
    )

    secret_encrypted = None
    if steam_guard_type == "totp" and steam_guard_secret:
        secret_encrypted = encrypt_totp_secret(steam_guard_secret)

    account = SteamAccount(
        display_name=display_name,
        username=username,
        password_encrypted=encrypt_password(password),
        steam_guard_type=steam_guard_type,
        steam_guard_secret_encrypted=secret_encrypted,
    )
    db.add(account)
    await db.commit()
    return RedirectResponse(url="/settings/?saved=1", status_code=303)


@router.post("/steam-accounts/{account_id}/delete")
async def delete_steam_account(
    request: Request,
    account_id: int,
    db: AsyncSession = Depends(get_db),
):
    await require_role(request, "admin")
    from app.models.steam_account import SteamAccount

    account = await db.get(SteamAccount, account_id)
    if account:
        await db.delete(account)
        await db.commit()
    return RedirectResponse(url="/settings/?saved=1", status_code=303)
