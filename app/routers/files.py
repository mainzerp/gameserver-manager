import asyncio
import os
import shutil
import stat
import zipfile
from pathlib import Path, PurePosixPath

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.server import Server
from app.services.audit_service import audit_service, get_audit_context
from app.services.auth import (
    get_current_user_dep,
    require_server_access,
)
from app.template_utils import templates
from app.validation import validate_file_content_size

router = APIRouter(
    prefix="/servers/{server_id}/files", dependencies=[Depends(get_current_user_dep)]
)


def _embed_suffix(request: Request) -> str:
    """Return '?embed=1' if the request is in embed mode, else ''."""
    return "?embed=1" if request.query_params.get("embed", "") == "1" else ""


# File extensions that can safely be edited as text
EDITABLE_EXTENSIONS = {
    ".txt",
    ".properties",
    ".yml",
    ".yaml",
    ".json",
    ".cfg",
    ".conf",
    ".toml",
    ".ini",
    ".log",
    ".md",
    ".sh",
    ".bat",
    ".cmd",
    ".xml",
    ".csv",
    ".env",
    ".mcmeta",
    ".lang",
    ".dat_old",
}

# File extensions/names that should never be served or edited
BLOCKED_NAMES = {"..", "~"}

# Max file size for editing (2 MB)
MAX_EDIT_SIZE = 2 * 1024 * 1024

# Max file size for upload (50 MB)
MAX_UPLOAD_SIZE = 50 * 1024 * 1024


def _safe_resolve(server_path: str, rel_path: str) -> Path:
    """Resolve a relative path within the server directory, preventing path traversal and symlinks."""
    base = Path(server_path).resolve()
    # Normalize without following symlinks so we can inspect each component
    candidate = Path(os.path.normpath(base / rel_path))
    # Reject any symlink in the requested path (including the final component)
    for p in [candidate, *candidate.parents]:
        if p == base or not p.is_relative_to(base):
            break
        try:
            if stat.S_ISLNK(os.lstat(p).st_mode):
                raise HTTPException(
                    status_code=403, detail="Access denied: path is a symlink"
                )
        except FileNotFoundError:
            pass
    # Resolve and block path traversal
    target = candidate.resolve()
    if not target.is_relative_to(base):
        raise HTTPException(
            status_code=403, detail="Access denied: path outside server directory"
        )
    return target


def _is_editable(filename: str) -> bool:
    """Check if a file can be opened in the text editor."""
    ext = Path(filename).suffix.lower()
    return ext in EDITABLE_EXTENSIONS


def _get_icon(name: str, is_dir: bool) -> str:
    """Return a simple icon class/emoji for file types."""
    if is_dir:
        return "folder"
    ext = Path(name).suffix.lower()
    if ext in {".jar"}:
        return "archive"
    if ext in {
        ".properties",
        ".yml",
        ".yaml",
        ".toml",
        ".json",
        ".cfg",
        ".conf",
        ".ini",
        ".xml",
    }:
        return "config"
    if ext in {".log", ".txt"}:
        return "text"
    if ext in {".png", ".jpg", ".jpeg", ".gif", ".ico"}:
        return "image"
    if ext in {".sh", ".bat", ".cmd"}:
        return "script"
    return "file"


def _format_size(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    elif size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    elif size < 1024 * 1024 * 1024:
        return f"{size / 1024 / 1024:.1f} MB"
    else:
        return f"{size / 1024 / 1024 / 1024:.1f} GB"


# Binary extensions to skip during file search
_BINARY_EXTENSIONS = {
    ".jar",
    ".zip",
    ".gz",
    ".tar",
    ".bz2",
    ".7z",
    ".rar",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".ico",
    ".bmp",
    ".webp",
    ".mp3",
    ".wav",
    ".ogg",
    ".mp4",
    ".avi",
    ".mkv",
    ".class",
    ".so",
    ".dll",
    ".exe",
    ".bin",
    ".dat",
}


@router.get("/search/", response_class=JSONResponse)
async def search_files(
    request: Request,
    server_id: int,
    q: str = Query(..., min_length=1, max_length=200),
    db: AsyncSession = Depends(get_db),
):
    from fastapi.responses import JSONResponse as _JSONResponse

    await require_server_access(request, server_id, "view", db)
    server = await db.get(Server, server_id)
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    base = Path(server.path).resolve()
    query_lower = q.lower()
    results = []
    max_results = 100
    max_file_size = 1 * 1024 * 1024  # 1 MB
    max_depth = 10

    def _search():
        for root, dirs, filenames in os.walk(str(base)):
            depth = str(Path(root).resolve()).replace(str(base), "").count(os.sep)
            if depth > max_depth:
                dirs.clear()
                continue
            for fname in filenames:
                if len(results) >= max_results:
                    return
                ext = Path(fname).suffix.lower()
                if ext in _BINARY_EXTENSIONS:
                    continue
                fpath = Path(root) / fname
                if fpath.stat().st_size > max_file_size:
                    continue
                try:
                    rel = str(fpath.relative_to(base)).replace(os.sep, "/")
                    with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                        for line_num, line in enumerate(f, 1):
                            if query_lower in line.lower():
                                snippet = line.strip()
                                if len(snippet) > 200:
                                    snippet = snippet[:200] + "..."
                                results.append(
                                    {
                                        "path": rel,
                                        "line": line_num,
                                        "content": snippet,
                                    }
                                )
                                if len(results) >= max_results:
                                    return
                except (OSError, UnicodeDecodeError):
                    continue

    await asyncio.to_thread(_search)
    return _JSONResponse({"results": results, "query": q, "total": len(results)})


@router.get("/", response_class=HTMLResponse)
@router.get("/{path:path}", response_class=HTMLResponse)
async def browse_files(
    request: Request,
    server_id: int,
    path: str = "",
    db: AsyncSession = Depends(get_db),
):
    await require_server_access(request, server_id, "view", db)
    server = await db.get(Server, server_id)
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    target = _safe_resolve(server.path, path)

    if not target.exists():
        raise HTTPException(status_code=404, detail="Path not found")

    # If it's a file, show the editor
    if target.is_file():
        return await _show_editor(request, server, path, target)

    # List directory
    entries = []
    try:
        for item in sorted(
            target.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())
        ):
            if item.name in BLOCKED_NAMES:
                continue
            rel = str(PurePosixPath(Path(path) / item.name)) if path else item.name
            stat = item.stat()
            entries.append(
                {
                    "name": item.name,
                    "is_dir": item.is_dir(),
                    "rel_path": rel,
                    "size": _format_size(stat.st_size) if item.is_file() else "",
                    "modified": stat.st_mtime,
                    "icon": _get_icon(item.name, item.is_dir()),
                    "editable": _is_editable(item.name) if item.is_file() else False,
                }
            )
    except PermissionError:
        raise HTTPException(status_code=403, detail="Access denied")

    # Build breadcrumbs
    breadcrumbs = [{"name": "root", "path": ""}]
    if path:
        parts = PurePosixPath(path).parts
        for i, part in enumerate(parts):
            breadcrumbs.append(
                {
                    "name": part,
                    "path": str(PurePosixPath(*parts[: i + 1])),
                }
            )

    return templates.TemplateResponse(request, "file_browser.html", {
            "server": server,
            "entries": entries,
            "current_path": path,
            "breadcrumbs": breadcrumbs,
            "parent_path": str(PurePosixPath(path).parent)
            if path and path != "."
            else None,
            "embed": request.query_params.get("embed", "") == "1",
        })


async def _show_editor(request: Request, server: Server, rel_path: str, target: Path):
    """Show the file editor for a text file."""
    from starlette.responses import RedirectResponse

    if not _is_editable(target.name):
        return RedirectResponse(
            url=f"/servers/{server.id}/files/download/{rel_path}",
            status_code=302,
        )

    file_size = target.stat().st_size
    if file_size > MAX_EDIT_SIZE:
        raise HTTPException(
            status_code=400,
            detail=f"File too large to edit ({_format_size(file_size)}). Maximum: {_format_size(MAX_EDIT_SIZE)}",
        )

    try:
        content = target.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error reading file: {e}")

    # Build breadcrumbs for the editor
    breadcrumbs = [{"name": "root", "path": ""}]
    if rel_path:
        parts = PurePosixPath(rel_path).parts
        for i, part in enumerate(parts):
            breadcrumbs.append(
                {
                    "name": part,
                    "path": str(PurePosixPath(*parts[: i + 1])),
                }
            )

    return templates.TemplateResponse(request, "file_editor.html", {
            "server": server,
            "file_path": rel_path,
            "file_name": target.name,
            "content": content,
            "file_size": _format_size(file_size),
            "parent_path": str(PurePosixPath(rel_path).parent)
            if "/" in rel_path or "\\" in rel_path
            else "",
            "breadcrumbs": breadcrumbs,
            "embed": request.query_params.get("embed", "") == "1",
        })


@router.post("/{path:path}")
async def save_file(
    request: Request,
    server_id: int,
    path: str,
    content: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    await require_server_access(request, server_id, "manage", db)
    server = await db.get(Server, server_id)
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    target = _safe_resolve(server.path, path)

    if not target.is_file():
        raise HTTPException(status_code=404, detail="File not found")

    if not _is_editable(target.name):
        raise HTTPException(status_code=400, detail="File type cannot be edited")

    err = validate_file_content_size(content, MAX_EDIT_SIZE)
    if err:
        raise HTTPException(status_code=400, detail=err)

    temp_path = target.with_suffix(target.suffix + ".tmp")
    try:
        temp_path.write_text(content, encoding="utf-8")
        os.replace(str(temp_path), str(target))
    except Exception as e:
        temp_path.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail=f"Error saving file: {e}")

    ctx = get_audit_context(request)
    audit_service.create_task(
        audit_service.log(
            **ctx,
            action="file.edit",
            resource_type="file",
            resource_id=str(server_id),
            details=f"path={path}",
        )
    )

    return RedirectResponse(
        url=f"/servers/{server_id}/files/{path}{_embed_suffix(request)}",
        status_code=303,
    )


_BLOCKED_EXTENSIONS = {".exe", ".sh", ".bat", ".py", ".pl", ".rb"}


def _sanitize_filename(name: str) -> str | None:
    name = name.strip()
    if not name or len(name) > 255:
        return None
    if "/" in name or "\\" in name or ".." in name or "\x00" in name:
        return None
    if name.startswith("."):
        return None
    if Path(name).suffix.lower() in _BLOCKED_EXTENSIONS:
        return None
    return name


@router.post("/upload/")
@router.post("/upload/{path:path}")
async def upload_file(
    request: Request,
    server_id: int,
    file: UploadFile,
    path: str = "",
    db: AsyncSession = Depends(get_db),
):
    await require_server_access(request, server_id, "manage", db)
    server = await db.get(Server, server_id)
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    target_dir = _safe_resolve(server.path, path)
    if not target_dir.is_dir():
        raise HTTPException(status_code=400, detail="Target path is not a directory")

    filename = _sanitize_filename(file.filename or "upload")
    if not filename:
        raise HTTPException(status_code=400, detail="Invalid filename")

    dest = _safe_resolve(
        server.path, str(PurePosixPath(path) / filename) if path else filename
    )

    try:
        total_size = 0
        with open(dest, "wb") as out_f:
            while chunk := await file.read(1024 * 1024):
                total_size += len(chunk)
                if total_size > MAX_UPLOAD_SIZE:
                    raise HTTPException(
                        status_code=400,
                        detail="File exceeds maximum upload size (50 MB)",
                    )
                out_f.write(chunk)
    except HTTPException:
        dest.unlink(missing_ok=True)
        raise
    except Exception as e:
        dest.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail=f"Error saving file: {e}")

    ctx = get_audit_context(request)
    audit_service.create_task(
        audit_service.log(
            **ctx,
            action="file.upload",
            resource_type="file",
            resource_id=str(server_id),
            details=f"filename={filename}, path={path}",
        )
    )

    return RedirectResponse(
        url=f"/servers/{server_id}/files/{path}{_embed_suffix(request)}",
        status_code=303,
    )


@router.post("/upload-multi/")
@router.post("/upload-multi/{path:path}")
async def upload_files_multi(
    request: Request,
    server_id: int,
    files: list[UploadFile],
    path: str = "",
    db: AsyncSession = Depends(get_db),
):
    await require_server_access(request, server_id, "manage", db)
    server = await db.get(Server, server_id)
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    target_dir = _safe_resolve(server.path, path)
    if not target_dir.is_dir():
        raise HTTPException(status_code=400, detail="Target path is not a directory")

    uploaded = []
    errors = []
    for file in files:
        filename = _sanitize_filename(file.filename or "upload")
        if not filename:
            errors.append(f"Invalid filename: {file.filename}")
            continue

        dest = _safe_resolve(
            server.path, str(PurePosixPath(path) / filename) if path else filename
        )
        try:
            total = 0
            size_exceeded = False
            with open(dest, "wb") as out_f:
                while True:
                    chunk = await file.read(65536)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > MAX_UPLOAD_SIZE:
                        size_exceeded = True
                        break
                    out_f.write(chunk)
            if size_exceeded:
                dest.unlink(missing_ok=True)
                errors.append(f"{filename}: exceeds 50 MB limit")
            else:
                uploaded.append(filename)
        except Exception as e:
            dest.unlink(missing_ok=True)
            errors.append(f"{filename}: {e}")

    if uploaded:
        ctx = get_audit_context(request)
        audit_service.create_task(
            audit_service.log(
                **ctx,
                action="file.upload",
                resource_type="file",
                resource_id=str(server_id),
                details=f"files={','.join(uploaded)}, path={path}",
            )
        )

    return JSONResponse({"uploaded": uploaded, "errors": errors})


@router.get("/download/{path:path}")
async def download_file(
    request: Request,
    server_id: int,
    path: str,
    db: AsyncSession = Depends(get_db),
):
    await require_server_access(request, server_id, "view", db)
    server = await db.get(Server, server_id)
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    target = _safe_resolve(server.path, path)

    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="File not found")

    return FileResponse(
        path=str(target),
        filename=target.name,
        media_type="application/octet-stream",
    )


@router.post("/delete/{path:path}")
async def delete_file(
    request: Request,
    server_id: int,
    path: str,
    db: AsyncSession = Depends(get_db),
):
    await require_server_access(request, server_id, "manage", db)
    server = await db.get(Server, server_id)
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    target = _safe_resolve(server.path, path)

    if target == Path(server.path).resolve():
        raise HTTPException(
            status_code=403, detail="Cannot delete server root directory"
        )

    if not target.exists():
        raise HTTPException(status_code=404, detail="Path not found")

    try:
        if target.is_file():
            target.unlink()
        elif target.is_dir():
            shutil.rmtree(target)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error deleting: {e}")

    ctx = get_audit_context(request)
    audit_service.create_task(
        audit_service.log(
            **ctx,
            action="file.delete",
            resource_type="file",
            resource_id=str(server_id),
            details=f"path={path}",
        )
    )

    parent = str(PurePosixPath(path).parent)
    if parent == ".":
        parent = ""
    return RedirectResponse(
        url=f"/servers/{server_id}/files/{parent}{_embed_suffix(request)}",
        status_code=303,
    )


@router.post("/rename/{path:path}")
async def rename_file(
    request: Request,
    server_id: int,
    path: str,
    new_name: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    await require_server_access(request, server_id, "manage", db)
    server = await db.get(Server, server_id)
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    target = _safe_resolve(server.path, path)

    if not target.exists():
        raise HTTPException(status_code=404, detail="Path not found")

    clean_name = _sanitize_filename(new_name)
    if not clean_name:
        raise HTTPException(status_code=400, detail="Invalid new name")

    new_path = target.parent / clean_name
    base = Path(server.path).resolve()
    if not str(new_path.resolve()).startswith(str(base)):
        raise HTTPException(
            status_code=403, detail="Access denied: path outside server directory"
        )

    if new_path.exists():
        raise HTTPException(
            status_code=400, detail="A file or folder with that name already exists"
        )

    try:
        target.rename(new_path)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error renaming: {e}")

    parent = str(PurePosixPath(path).parent)
    if parent == ".":
        parent = ""
    return RedirectResponse(
        url=f"/servers/{server_id}/files/{parent}{_embed_suffix(request)}",
        status_code=303,
    )


@router.post("/mkdir/")
@router.post("/mkdir/{path:path}")
async def make_directory(
    request: Request,
    server_id: int,
    dir_name: str = Form(...),
    path: str = "",
    db: AsyncSession = Depends(get_db),
):
    server = await db.get(Server, server_id)
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    await require_server_access(request, server_id, "manage", db)

    clean_name = _sanitize_filename(dir_name)
    if not clean_name:
        raise HTTPException(status_code=400, detail="Invalid directory name")

    new_dir = _safe_resolve(
        server.path, str(PurePosixPath(path) / clean_name) if path else clean_name
    )

    if new_dir.exists():
        raise HTTPException(status_code=400, detail="Directory already exists")

    try:
        os.makedirs(new_dir)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error creating directory: {e}")

    return RedirectResponse(
        url=f"/servers/{server_id}/files/{path}{_embed_suffix(request)}",
        status_code=303,
    )


MAX_EXTRACT_SIZE = 20 * 1024 * 1024 * 1024  # 20 GB
MAX_EXTRACT_FILES = 10000


def _compress_path(source: str, dest_zip: str):
    source_path = Path(source)
    with zipfile.ZipFile(dest_zip, "w", zipfile.ZIP_DEFLATED) as zf:
        if source_path.is_file():
            zf.write(source_path, source_path.name)
        else:
            for root, dirs, files in os.walk(source_path):
                for f in files:
                    fp = Path(root) / f
                    arcname = str(fp.relative_to(source_path.parent))
                    zf.write(fp, arcname)


def _extract_archive(zip_path: str, dest_dir: str):
    safe_dest = Path(dest_dir).resolve()
    os.makedirs(dest_dir, exist_ok=True)

    with zipfile.ZipFile(zip_path, "r") as zf:
        if len(zf.infolist()) > MAX_EXTRACT_FILES:
            raise ValueError(f"Archive contains too many files (>{MAX_EXTRACT_FILES})")

        # Validate all member paths BEFORE extracting anything
        for member in zf.infolist():
            member_path = (safe_dest / member.filename).resolve()
            if not str(member_path).startswith(str(safe_dest)):
                raise ValueError(f"Unsafe path in zip: {member.filename}")

        # Extract member-by-member, tracking real decompressed bytes written
        total_written = 0
        for member in zf.infolist():
            if member.is_dir():
                (safe_dest / member.filename).mkdir(parents=True, exist_ok=True)
                continue
            target = safe_dest / member.filename
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(member) as src, open(target, "wb") as dst:
                while True:
                    chunk = src.read(65536)
                    if not chunk:
                        break
                    total_written += len(chunk)
                    if total_written > MAX_EXTRACT_SIZE:
                        raise ValueError(
                            f"Archive too large when decompressed (>{MAX_EXTRACT_SIZE} bytes)"
                        )
                    dst.write(chunk)


@router.post("/compress/{path:path}")
async def compress_path(
    request: Request,
    server_id: int,
    path: str,
    db: AsyncSession = Depends(get_db),
):
    server = await db.get(Server, server_id)
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    await require_server_access(request, server_id, "manage", db)

    target = _safe_resolve(server.path, path)
    if not target.exists():
        raise HTTPException(status_code=404, detail="Path not found")

    output_zip = target.parent / (target.name + ".zip")
    if output_zip.exists():
        raise HTTPException(status_code=400, detail="Zip file already exists")

    try:
        await asyncio.to_thread(_compress_path, str(target), str(output_zip))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Compression failed: {e}")

    parent = str(PurePosixPath(path).parent)
    if parent == ".":
        parent = ""
    return RedirectResponse(
        url=f"/servers/{server_id}/files/{parent}{_embed_suffix(request)}",
        status_code=303,
    )


@router.post("/extract/{path:path}")
async def extract_archive(
    request: Request,
    server_id: int,
    path: str,
    db: AsyncSession = Depends(get_db),
):
    server = await db.get(Server, server_id)
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    await require_server_access(request, server_id, "manage", db)

    target = _safe_resolve(server.path, path)
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="File not found")

    if target.suffix.lower() != ".zip":
        raise HTTPException(status_code=400, detail="Only .zip files can be extracted")

    extract_dir = target.parent / target.stem
    try:
        await asyncio.to_thread(_extract_archive, str(target), str(extract_dir))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Extraction failed: {e}")

    parent = str(PurePosixPath(path).parent)
    if parent == ".":
        parent = ""
    return RedirectResponse(
        url=f"/servers/{server_id}/files/{parent}{_embed_suffix(request)}",
        status_code=303,
    )


@router.post("/bulk-delete")
async def bulk_delete(
    request: Request,
    server_id: int,
    db: AsyncSession = Depends(get_db),
):
    await require_server_access(request, server_id, "manage", db)
    server = await db.get(Server, server_id)
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    form = await request.form()
    path = form.get("path", "")
    files = form.getlist("files")
    if not files:
        raise HTTPException(status_code=400, detail="No files selected")

    deleted = []
    for fname in files:
        clean = _sanitize_filename(fname)
        if not clean:
            continue
        target = _safe_resolve(server.path, os.path.join(path, clean))
        if target == Path(server.path).resolve():
            continue
        try:
            if target.is_dir():
                shutil.rmtree(target)
                deleted.append(clean)
            elif target.is_file():
                target.unlink()
                deleted.append(clean)
        except Exception:
            pass

    if deleted:
        ctx = get_audit_context(request)
        audit_service.create_task(
            audit_service.log(
                **ctx,
                action="file.bulk_delete",
                resource_type="file",
                resource_id=str(server_id),
                details=f"files={','.join(deleted)}, path={path}",
            )
        )

    return RedirectResponse(
        url=f"/servers/{server_id}/files/{path}{_embed_suffix(request)}",
        status_code=303,
    )


@router.post("/bulk-download")
async def bulk_download(
    request: Request,
    server_id: int,
    db: AsyncSession = Depends(get_db),
):
    await require_server_access(request, server_id, "view", db)
    server = await db.get(Server, server_id)
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    form = await request.form()
    path = form.get("path", "")
    files = form.getlist("files")
    if not files:
        raise HTTPException(status_code=400, detail="No files selected")

    import tempfile

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".zip")
    tmp_name = tmp.name
    tmp.close()

    try:
        with zipfile.ZipFile(tmp_name, "w", zipfile.ZIP_DEFLATED) as zf:
            for fname in files:
                clean = _sanitize_filename(fname)
                if not clean:
                    continue
                target = _safe_resolve(server.path, os.path.join(path, clean))
                if target.is_file():
                    zf.write(target, clean)
                elif target.is_dir():
                    for root, dirs, dirfiles in os.walk(target):
                        for f in dirfiles:
                            fp = Path(root) / f
                            arcname = os.path.join(clean, str(fp.relative_to(target)))
                            zf.write(fp, arcname)
    except Exception as e:
        os.unlink(tmp_name)
        raise HTTPException(status_code=500, detail=f"Error creating zip: {e}")

    return FileResponse(tmp_name, filename="files.zip", media_type="application/zip")
