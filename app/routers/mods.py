from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import os
import tempfile

from app.database import get_db
from app.models.mod import Mod
from app.models.server import Server
from app.services.auth import (
    get_current_user,
    get_current_user_dep,
    require_server_access,
)
from app.services.mod_updater import mod_updater
from app.services.audit_service import audit_service, get_audit_context
from app.template_utils import templates
from app.validation import validate_mod_install

router = APIRouter(
    prefix="/servers/{server_id}/mods", dependencies=[Depends(get_current_user_dep)]
)


@router.get("/", response_class=HTMLResponse)
async def mods_page(
    request: Request, server_id: int, db: AsyncSession = Depends(get_db)
):
    return RedirectResponse(url=f"/servers/{server_id}?tab=mods", status_code=302)


@router.get("/search/api")
async def search_mods_api(
    request: Request,
    server_id: int,
    q: str = "",
    source: str = Query("modrinth"),
    db: AsyncSession = Depends(get_db),
):
    await require_server_access(request, server_id, "view", db)
    server = await db.get(Server, server_id)
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")
    results = []
    if q:
        results = await mod_updater.search_modrinth(
            query=q,
            game_version=server.mc_version,
            loader=server.loader,
        )
    return JSONResponse({"results": results})


@router.get("/search", response_class=HTMLResponse)
async def search_mods(
    request: Request, server_id: int, db: AsyncSession = Depends(get_db)
):
    return RedirectResponse(url=f"/servers/{server_id}?tab=mods", status_code=302)


@router.post("/install")
async def install_mod(
    request: Request,
    server_id: int,
    project_id: str = Form(...),
    source: str = Form("modrinth"),
    db: AsyncSession = Depends(get_db),
):
    await require_server_access(request, server_id, "manage", db)
    err = validate_mod_install(source, project_id)
    if err:
        raise HTTPException(status_code=400, detail=err)
    mod = await mod_updater.install_mod(server_id, project_id, source)
    if not mod:
        raise HTTPException(status_code=400, detail="Failed to install mod")
    ctx = get_audit_context(request)
    audit_service.create_task(
        audit_service.log(
            **ctx,
            action="mod.install",
            resource_type="mod",
            resource_id=str(project_id),
            details=f"server_id={server_id}, source={source}",
        )
    )
    return RedirectResponse(url=f"/servers/{server_id}?tab=mods", status_code=303)


@router.post("/{mod_id}/update")
async def update_mod(
    request: Request, server_id: int, mod_id: int, db: AsyncSession = Depends(get_db)
):
    await require_server_access(request, server_id, "manage", db)
    success = await mod_updater.update_mod(mod_id)
    if not success:
        raise HTTPException(status_code=400, detail="Failed to update mod")
    ctx = get_audit_context(request)
    audit_service.create_task(
        audit_service.log(
            **ctx,
            action="mod.update",
            resource_type="mod",
            resource_id=str(mod_id),
        )
    )
    return RedirectResponse(url=f"/servers/{server_id}?tab=mods", status_code=303)


@router.post("/update-all")
async def update_all_mods(
    request: Request, server_id: int, db: AsyncSession = Depends(get_db)
):
    await require_server_access(request, server_id, "manage", db)
    results = await mod_updater.update_all_mods(server_id)
    return RedirectResponse(url=f"/servers/{server_id}?tab=mods", status_code=303)


@router.post("/check-updates")
async def check_updates(
    request: Request, server_id: int, db: AsyncSession = Depends(get_db)
):
    await require_server_access(request, server_id, "manage", db)
    await mod_updater.check_updates(server_id)
    return RedirectResponse(url=f"/servers/{server_id}?tab=mods", status_code=303)


@router.post("/resolve-imported")
async def resolve_imported(
    request: Request, server_id: int, db: AsyncSession = Depends(get_db)
):
    await require_server_access(request, server_id, "manage", db)
    await mod_updater.resolve_imported_mods(server_id)
    return RedirectResponse(url=f"/servers/{server_id}?tab=mods", status_code=303)


@router.post("/refresh-max-version")
async def refresh_max_version(
    request: Request, server_id: int, db: AsyncSession = Depends(get_db)
):
    await require_server_access(request, server_id, "manage", db)
    await mod_updater.update_max_compatible_version(server_id)
    return RedirectResponse(url=f"/servers/{server_id}?tab=overview", status_code=303)


@router.post("/{mod_id}/delete")
async def delete_mod(
    request: Request, server_id: int, mod_id: int, db: AsyncSession = Depends(get_db)
):
    await require_server_access(request, server_id, "manage", db)
    success = await mod_updater.remove_mod(mod_id)
    if not success:
        raise HTTPException(status_code=400, detail="Failed to remove mod")
    ctx = get_audit_context(request)
    audit_service.create_task(
        audit_service.log(
            **ctx,
            action="mod.delete",
            resource_type="mod",
            resource_id=str(mod_id),
        )
    )
    return RedirectResponse(url=f"/servers/{server_id}?tab=mods", status_code=303)


@router.post("/import-modpack")
async def import_modpack(
    request: Request,
    server_id: int,
    file: UploadFile = None,
    modrinth_url: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    await require_server_access(request, server_id, "manage", db)

    if modrinth_url and modrinth_url.strip():
        from app.services.modpack_importer import import_modrinth_modpack

        result = await import_modrinth_modpack(server_id, modrinth_url.strip())
    elif file and file.filename:
        if not file.filename.endswith(".mrpack"):
            raise HTTPException(status_code=400, detail="File must be a .mrpack file")

        from app.services.modpack_importer import import_mrpack

        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".mrpack")
        content = await file.read()
        tmp.write(content)
        tmp.close()

        try:
            result = await import_mrpack(server_id, tmp.name)
        finally:
            os.unlink(tmp.name)
    else:
        raise HTTPException(
            status_code=400, detail="Provide a .mrpack file or a Modrinth URL"
        )

    ctx = get_audit_context(request)
    audit_service.create_task(
        audit_service.log(
            **ctx,
            action="modpack.import",
            resource_type="server",
            resource_id=str(server_id),
            details=f"mods={result['mods_installed']}, overrides={result['overrides_applied']}",
        )
    )
    return RedirectResponse(url=f"/servers/{server_id}?tab=mods", status_code=303)


@router.post("/save-profile")
async def save_mod_profile(
    request: Request,
    server_id: int,
    name: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    await require_server_access(request, server_id, "manage", db)
    await mod_updater.save_profile(server_id, name)
    return RedirectResponse(url=f"/servers/{server_id}?tab=mods", status_code=303)


@router.post("/apply-profile/{profile_id}")
async def apply_mod_profile(
    request: Request,
    server_id: int,
    profile_id: int,
    db: AsyncSession = Depends(get_db),
):
    await require_server_access(request, server_id, "manage", db)
    await mod_updater.apply_profile(server_id, profile_id)
    return RedirectResponse(url=f"/servers/{server_id}?tab=mods", status_code=303)
