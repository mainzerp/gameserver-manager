import base64
import io
import json
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.user import User
from app.services.audit_service import audit_service, get_audit_context
from app.services.auth import (
    generate_recovery_codes,
    generate_totp_secret,
    get_current_user,
    get_totp_uri,
    hash_password,
    hash_recovery_codes,
    verify_password,
    verify_recovery_code,
    verify_totp,
    verify_totp_with_replay_protection,
)
from app.template_utils import templates

router = APIRouter()

_login_attempts: dict[str, list[float]] = defaultdict(list)
LOGIN_MAX_ATTEMPTS = 10
LOGIN_WINDOW_SECONDS = 300

_totp_attempts: dict[int, list[float]] = defaultdict(list)
TOTP_MAX_ATTEMPTS = 5
TOTP_WINDOW_SECONDS = 300


def _check_login_rate_limit(ip: str) -> bool:
    now = time.time()
    pruned = [t for t in _login_attempts.get(ip, []) if now - t < LOGIN_WINDOW_SECONDS]
    if pruned:
        _login_attempts[ip] = pruned
    elif ip in _login_attempts:
        del _login_attempts[ip]
    if len(pruned) >= LOGIN_MAX_ATTEMPTS:
        return False
    return True


def _check_totp_rate_limit(user_id: int) -> bool:
    now = time.time()
    pruned = [
        t for t in _totp_attempts.get(user_id, []) if now - t < TOTP_WINDOW_SECONDS
    ]
    pruned.append(now)
    _totp_attempts[user_id] = pruned
    if len(pruned) > TOTP_MAX_ATTEMPTS:
        # Clean up immediately when over limit to avoid accumulation
        _totp_attempts[user_id] = pruned[-TOTP_MAX_ATTEMPTS:]
        return False
    return True


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, error: str = ""):
    from app.services.webauthn_service import webauthn_service

    if request.session.get("user_id"):
        return RedirectResponse(url="/", status_code=303)
    return templates.TemplateResponse(request, "login.html", {
            "error": error,
            "webauthn_available": webauthn_service.is_available(),
        })


@router.post("/login")
async def login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    client_ip = request.client.host if request.client else "unknown"
    if not _check_login_rate_limit(client_ip):
        return RedirectResponse(url="/login?error=rate_limited", status_code=303)

    result = await db.execute(select(User).where(User.username == username))
    user = result.scalars().first()

    # Check account lockout
    if user and user.locked_until and user.locked_until > datetime.now(timezone.utc):
        _login_attempts[client_ip].append(time.time())
        ctx = get_audit_context(request)
        audit_service.create_task(
            audit_service.log(
                **ctx,
                action="auth.login_failed",
                details=f"username={username} (locked)",
            )
        )
        return RedirectResponse(url="/login?error=invalid", status_code=303)

    if not user or not verify_password(password, user.password_hash):
        _login_attempts[client_ip].append(time.time())
        if user:
            user.failed_login_count += 1
            if user.failed_login_count >= 5:
                backoff_minutes = 5 * (2 ** (user.failed_login_count // 5))
                user.locked_until = datetime.now(timezone.utc) + timedelta(
                    minutes=backoff_minutes
                )
            await db.commit()
        ctx = get_audit_context(request)
        audit_service.create_task(
            audit_service.log(
                **ctx,
                action="auth.login_failed",
                details=f"username={username}",
            )
        )
        return RedirectResponse(url="/login?error=invalid", status_code=303)

    # Successful login: clear lockout
    if user.failed_login_count > 0 or user.locked_until:
        user.failed_login_count = 0
        user.locked_until = None
        await db.commit()

    if user.totp_enabled:
        request.session["pending_2fa_user"] = user.id
        return RedirectResponse(url="/login/2fa", status_code=303)

    locale = request.session.get("locale")
    request.session.clear()
    if locale:
        request.session["locale"] = locale
    request.session["user_id"] = user.id
    request.session["username"] = user.username
    request.session["user_role"] = user.role
    ctx = get_audit_context(request)
    ctx["user_id"] = user.id
    ctx["username"] = user.username
    audit_service.create_task(
        audit_service.log(
            **ctx,
            action="auth.login",
        )
    )
    return RedirectResponse(url="/", status_code=303)


@router.post("/logout")
async def logout(request: Request):
    ctx = get_audit_context(request)
    audit_service.create_task(
        audit_service.log(
            **ctx,
            action="auth.logout",
        )
    )
    request.session.clear()
    return RedirectResponse(url="/login", status_code=303)


@router.get("/login/2fa", response_class=HTMLResponse)
async def totp_verify_page(request: Request):
    if not request.session.get("pending_2fa_user"):
        return RedirectResponse(url="/login", status_code=303)
    return templates.TemplateResponse(request, "totp_verify.html", {
            "error": "",
        })


@router.post("/login/2fa")
async def totp_verify(
    request: Request,
    code: str = Form(""),
    recovery_code: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    user_id = request.session.get("pending_2fa_user")
    if not user_id:
        return RedirectResponse(url="/login", status_code=303)

    user = await db.get(User, user_id)
    if not user:
        request.session.clear()
        return RedirectResponse(url="/login", status_code=303)

    if not _check_totp_rate_limit(user_id):
        return templates.TemplateResponse(request, "totp_verify.html", {
                "error": "Too many attempts. Please wait before trying again.",
            })

    if (
        code
        and user.totp_secret
        and await verify_totp_with_replay_protection(user, code, db)
    ):
        locale = request.session.get("locale")
        request.session.clear()
        if locale:
            request.session["locale"] = locale
        request.session["user_id"] = user.id
        request.session["username"] = user.username
        request.session["user_role"] = user.role
        ctx = get_audit_context(request)
        ctx["user_id"] = user.id
        ctx["username"] = user.username
        audit_service.create_task(audit_service.log(**ctx, action="auth.login"))
        return RedirectResponse(url="/", status_code=303)

    if recovery_code and user.recovery_codes:
        valid, updated_codes = verify_recovery_code(user.recovery_codes, recovery_code)
        if valid:
            user.recovery_codes = updated_codes
            await db.commit()
            locale = request.session.get("locale")
            request.session.clear()
            if locale:
                request.session["locale"] = locale
            request.session["user_id"] = user.id
            request.session["username"] = user.username
            request.session["user_role"] = user.role
            ctx = get_audit_context(request)
            ctx["user_id"] = user.id
            ctx["username"] = user.username
            audit_service.create_task(
                audit_service.log(**ctx, action="auth.login_recovery")
            )
            return RedirectResponse(url="/", status_code=303)

    return templates.TemplateResponse(request, "totp_verify.html", {
            "error": "Invalid verification code.",
        })


@router.get("/settings/2fa/setup", response_class=HTMLResponse)
async def totp_setup_page(request: Request):
    import qrcode

    user = await get_current_user(request)
    secret = generate_totp_secret()
    uri = get_totp_uri(secret, user.username)
    img = qrcode.make(uri)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    qr_b64 = base64.b64encode(buf.getvalue()).decode()
    request.session["pending_totp_secret"] = secret
    return templates.TemplateResponse(request, "totp_setup.html", {
            "qr_b64": qr_b64,
            "secret": secret,
            "error": "",
            "recovery_codes": None,
        })


@router.post("/settings/2fa/setup")
async def totp_setup_confirm(
    request: Request,
    code: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    import qrcode

    user = await get_current_user(request)
    secret = request.session.get("pending_totp_secret")
    if not secret:
        return RedirectResponse(url="/settings/2fa/setup", status_code=303)

    if not verify_totp(secret, code):
        uri = get_totp_uri(secret, user.username)
        img = qrcode.make(uri)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        qr_b64 = base64.b64encode(buf.getvalue()).decode()
        return templates.TemplateResponse(request, "totp_setup.html", {
                "qr_b64": qr_b64,
                "secret": secret,
                "error": "Invalid code. Please try again.",
                "recovery_codes": None,
            })

    recovery_codes = generate_recovery_codes()

    db_user = await db.get(User, user.id)
    db_user.totp_secret = secret
    db_user.totp_enabled = True
    db_user.recovery_codes = hash_recovery_codes(recovery_codes)
    await db.commit()

    request.session.pop("pending_totp_secret", None)
    ctx = get_audit_context(request)
    audit_service.create_task(audit_service.log(**ctx, action="auth.2fa_enabled"))

    return templates.TemplateResponse(request, "totp_setup.html", {
            "qr_b64": "",
            "secret": "",
            "error": "",
            "recovery_codes": recovery_codes,
        })


@router.post("/settings/2fa/disable")
async def totp_disable(
    request: Request,
    password: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    user = await get_current_user(request)
    db_user = await db.get(User, user.id)
    if not verify_password(password, db_user.password_hash):
        return RedirectResponse(url="/", status_code=303)

    db_user.totp_enabled = False
    db_user.totp_secret = None
    db_user.recovery_codes = None
    await db.commit()

    ctx = get_audit_context(request)
    audit_service.create_task(audit_service.log(**ctx, action="auth.2fa_disabled"))
    return RedirectResponse(url="/", status_code=303)


@router.get("/setup", response_class=HTMLResponse)
async def setup_page(request: Request, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(func.count()).select_from(User))
    count = result.scalar()
    if count > 0:
        return RedirectResponse(url="/", status_code=303)
    return templates.TemplateResponse(request, "setup.html", {
            "error": "",
        })


@router.post("/setup")
async def setup(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    password_confirm: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(func.count()).select_from(User))
    count = result.scalar()
    if count > 0:
        return RedirectResponse(url="/", status_code=303)

    error = ""
    import re

    if len(username) < 3 or len(username) > 50:
        error = "Username must be between 3 and 50 characters."
    elif not re.match(r"^[a-zA-Z0-9_.-]+$", username):
        error = (
            "Username may only contain letters, digits, underscores, hyphens, and dots."
        )
    elif len(password) < 8:
        error = "Password must be at least 8 characters."
    elif password != password_confirm:
        error = "Passwords do not match."

    if error:
        return templates.TemplateResponse(request, "setup.html", {
                "error": error,
            })

    user = User(
        username=username,
        password_hash=hash_password(password),
        is_admin=True,
        role="admin",
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)

    locale = request.session.get("locale")
    request.session.clear()
    if locale:
        request.session["locale"] = locale
    request.session["user_id"] = user.id
    request.session["username"] = user.username
    request.session["user_role"] = user.role
    return RedirectResponse(url="/", status_code=303)


@router.post("/set-locale")
async def set_locale(request: Request, locale: str = Form(...)):
    from app.i18n import SUPPORTED_LOCALES

    if locale in SUPPORTED_LOCALES:
        request.session["locale"] = locale
    referer = request.headers.get("referer", "/")
    from urllib.parse import urlparse

    parsed = urlparse(referer)
    if parsed.netloc or not referer.startswith("/"):
        referer = "/"
    return RedirectResponse(url=referer, status_code=303)


# ---------------------------------------------------------------------------
# WebAuthn / Passkey endpoints
# ---------------------------------------------------------------------------


@router.get("/auth/webauthn/register", response_class=HTMLResponse)
async def webauthn_register_page(request: Request, db: AsyncSession = Depends(get_db)):
    from app.models.webauthn_credential import WebAuthnCredential
    from app.services.webauthn_service import webauthn_service

    user = await get_current_user(request)
    result = await db.execute(
        select(WebAuthnCredential).where(WebAuthnCredential.user_id == user.id)
    )
    credentials = result.scalars().all()
    return templates.TemplateResponse(request, "webauthn_register.html", {
            "credentials": credentials,
            "webauthn_available": webauthn_service.is_available(),
            "flash": request.session.pop("flash", None),
            "error": request.session.pop("flash_error", None),
        })


@router.post("/auth/webauthn/register/options")
async def webauthn_register_options(
    request: Request, db: AsyncSession = Depends(get_db)
):
    from app.models.webauthn_credential import WebAuthnCredential
    from app.services.webauthn_service import webauthn_service

    if not webauthn_service.is_available():
        return JSONResponse(
            {"ok": False, "error": "WebAuthn is not enabled"}, status_code=400
        )
    user = await get_current_user(request)
    result = await db.execute(
        select(WebAuthnCredential).where(WebAuthnCredential.user_id == user.id)
    )
    existing = result.scalars().all()
    try:
        from webauthn.helpers import options_to_json

        options = webauthn_service.get_registration_options(user, existing)
        challenge = options.challenge
        request.session["webauthn_reg_challenge"] = challenge.hex()
        return JSONResponse(json.loads(options_to_json(options)))
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)


@router.post("/auth/webauthn/register/complete")
async def webauthn_register_complete(
    request: Request, db: AsyncSession = Depends(get_db)
):
    from app.models.webauthn_credential import WebAuthnCredential
    from app.services.webauthn_service import webauthn_service

    if not webauthn_service.is_available():
        return JSONResponse(
            {"ok": False, "error": "WebAuthn is not enabled"}, status_code=400
        )
    user = await get_current_user(request)
    challenge_hex = request.session.pop("webauthn_reg_challenge", None)
    if not challenge_hex:
        return JSONResponse(
            {"ok": False, "error": "No pending registration challenge"}, status_code=400
        )
    try:
        body = await request.json()
        name = body.pop("name", "Passkey")
        verification = webauthn_service.verify_registration(
            body, bytes.fromhex(challenge_hex)
        )
        cred = WebAuthnCredential(
            user_id=user.id,
            credential_id=verification["credential_id"],
            public_key=verification["public_key"],
            sign_count=verification["sign_count"],
            name=name,
        )
        db.add(cred)
        await db.commit()
        return JSONResponse({"ok": True})
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)


@router.post("/auth/webauthn/credentials/{credential_id}/delete")
async def webauthn_delete_credential(
    request: Request,
    credential_id: int,
    db: AsyncSession = Depends(get_db),
):
    from app.models.webauthn_credential import WebAuthnCredential

    user = await get_current_user(request)
    cred = await db.get(WebAuthnCredential, credential_id)
    if cred and cred.user_id == user.id:
        await db.delete(cred)
        await db.commit()
        request.session["flash"] = "Passkey removed."
    return RedirectResponse(url="/auth/webauthn/register", status_code=303)


@router.get("/auth/webauthn/login/options")
async def webauthn_login_options(request: Request, db: AsyncSession = Depends(get_db)):
    from app.models.webauthn_credential import WebAuthnCredential
    from app.services.webauthn_service import webauthn_service

    if not webauthn_service.is_available():
        return JSONResponse(
            {"ok": False, "error": "WebAuthn is not enabled"}, status_code=400
        )
    result = await db.execute(select(WebAuthnCredential))
    all_creds = result.scalars().all()
    try:
        from webauthn.helpers import options_to_json

        options = webauthn_service.get_authentication_options(all_creds)
        request.session["webauthn_auth_challenge"] = options.challenge.hex()
        return JSONResponse(json.loads(options_to_json(options)))
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)


@router.post("/auth/webauthn/login/complete")
async def webauthn_login_complete(request: Request, db: AsyncSession = Depends(get_db)):
    from app.models.webauthn_credential import WebAuthnCredential
    from app.services.webauthn_service import webauthn_service

    if not webauthn_service.is_available():
        return JSONResponse(
            {"ok": False, "error": "WebAuthn is not enabled"}, status_code=400
        )
    challenge_hex = request.session.pop("webauthn_auth_challenge", None)
    if not challenge_hex:
        return JSONResponse(
            {"ok": False, "error": "No pending authentication challenge"},
            status_code=400,
        )
    try:
        body = await request.json()
        import base64

        raw_id = base64.urlsafe_b64decode(body["rawId"] + "==")
        result = await db.execute(
            select(WebAuthnCredential).where(WebAuthnCredential.credential_id == raw_id)
        )
        cred_record = result.scalars().first()
        if not cred_record:
            return JSONResponse(
                {"ok": False, "error": "Credential not found"}, status_code=400
            )
        new_sign_count = webauthn_service.verify_authentication(
            body, bytes.fromhex(challenge_hex), cred_record
        )
        cred_record.sign_count = new_sign_count
        await db.commit()
        user = await db.get(User, cred_record.user_id)
        request.session["user_id"] = user.id
        request.session["username"] = user.username
        request.session["user_role"] = user.role
        return JSONResponse({"ok": True, "redirect": "/"})
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
