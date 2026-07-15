import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ.setdefault("GSM_DATABASE_URL", "sqlite+aiosqlite:///:memory:")

import app.models  # noqa: F401
import httpx
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from starlette.middleware.sessions import SessionMiddleware

from app.database import Base
from app.models.server import Server, ServerStatus, ServerType
from app.routers import files as files_router


class FileOperationsUnitTests(unittest.TestCase):
    def test_safe_resolve_blocks_path_traversal(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp).resolve()
            with self.assertRaises(Exception) as ctx:
                files_router._safe_resolve(str(base), "../../../etc/passwd")
            self.assertIn("Access denied", str(ctx.exception.detail))

    def test_safe_resolve_allows_valid_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp).resolve()
            (base / "mods").mkdir()
            (base / "mods" / "test.jar").write_text("jar")
            result = files_router._safe_resolve(str(base), "mods/test.jar")
            self.assertEqual(result, base / "mods" / "test.jar")

    def test_safe_resolve_blocks_symlinks(self):
        if os.name == "nt":
            self.skipTest("Symlink creation requires admin privileges on Windows")
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp).resolve()
            real_file = Path(tmp).parent / "real.txt"
            real_file.write_text("secret")
            (base / "link").symlink_to(real_file)
            with self.assertRaises(Exception) as ctx:
                files_router._safe_resolve(str(base), "link")
            self.assertIn("symlink", str(ctx.exception.detail))

    def test_sanitize_filename_rejects_hidden_files(self):
        self.assertIsNone(files_router._sanitize_filename(".htaccess"))
        self.assertIsNone(files_router._sanitize_filename(".."))

    def test_sanitize_filename_rejects_blocked_extensions(self):
        self.assertIsNone(files_router._sanitize_filename("script.exe"))
        self.assertIsNone(files_router._sanitize_filename("run.sh"))
        self.assertIsNone(files_router._sanitize_filename("auto.bat"))

    def test_sanitize_filename_allows_safe_names(self):
        self.assertEqual(
            files_router._sanitize_filename("server.properties"), "server.properties"
        )
        self.assertEqual(files_router._sanitize_filename("mods.zip"), "mods.zip")

    def test_sanitize_filename_rejects_path_traversal_in_name(self):
        self.assertIsNone(files_router._sanitize_filename("foo/../bar.txt"))
        self.assertIsNone(files_router._sanitize_filename("foo\\bar.txt"))

    def test_is_editable_recognizes_text_files(self):
        self.assertTrue(files_router._is_editable("server.properties"))
        self.assertTrue(files_router._is_editable("config.yml"))
        self.assertFalse(files_router._is_editable("server.jar"))

    def test_extract_archive_respects_max_files_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            zip_path = Path(tmp) / "big.zip"
            with zipfile.ZipFile(zip_path, "w") as zf:
                for i in range(files_router.MAX_EXTRACT_FILES + 5):
                    zf.writestr(f"file{i}.txt", "x")
            with self.assertRaises(ValueError) as ctx:
                files_router._extract_archive(str(zip_path), str(Path(tmp) / "out"))
            self.assertIn("too many files", str(ctx.exception))

    def test_extract_archive_blocks_directory_traversal(self):
        with tempfile.TemporaryDirectory() as tmp:
            zip_path = Path(tmp) / "bad.zip"
            with zipfile.ZipFile(zip_path, "w") as zf:
                zf.writestr("../../etc/passwd", "root")
            with self.assertRaises(ValueError) as ctx:
                files_router._extract_archive(str(zip_path), str(Path(tmp) / "out"))
            self.assertIn("Unsafe path", str(ctx.exception))

    def test_extract_archive_extracts_valid_zip(self):
        with tempfile.TemporaryDirectory() as tmp:
            zip_path = Path(tmp) / "archive.zip"
            with zipfile.ZipFile(zip_path, "w") as zf:
                zf.writestr("inside.txt", "extracted content")
            files_router._extract_archive(str(zip_path), str(Path(tmp) / "out"))
            extracted = Path(tmp) / "out" / "inside.txt"
            self.assertTrue(extracted.exists())
            self.assertEqual(extracted.read_text(), "extracted content")

    def test_extract_archive_respects_size_limit(self):
        original_max = files_router.MAX_EXTRACT_SIZE
        files_router.MAX_EXTRACT_SIZE = 10
        try:
            with tempfile.TemporaryDirectory() as tmp:
                zip_path = Path(tmp) / "big.zip"
                with zipfile.ZipFile(zip_path, "w") as zf:
                    zf.writestr("huge.txt", "x" * 100)
                with self.assertRaises(ValueError) as ctx:
                    files_router._extract_archive(str(zip_path), str(Path(tmp) / "out"))
                self.assertIn("too large", str(ctx.exception))
        finally:
            files_router.MAX_EXTRACT_SIZE = original_max


class FileOperationsIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test.db"
        self.engine = create_async_engine(f"sqlite+aiosqlite:///{self.db_path}")
        self.session_maker = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        self.app = FastAPI()
        self.app.add_middleware(SessionMiddleware, secret_key="test-secret")

        self.app.include_router(files_router.router)

        async def override_get_db():
            async with self.session_maker() as session:
                yield session

        async def override_current_user():
            return SimpleNamespace(id=1, role="admin")

        self.app.dependency_overrides[files_router.get_db] = override_get_db
        self.app.dependency_overrides[files_router.get_current_user_dep] = (
            override_current_user
        )

        self.transport = httpx.ASGITransport(app=self.app)
        self.client = httpx.AsyncClient(
            transport=self.transport,
            base_url="http://testserver",
            follow_redirects=False,
        )

        self.access_patch = patch.object(
            files_router,
            "require_server_access",
            AsyncMock(return_value=SimpleNamespace(id=1, role="admin")),
        )
        self.access_patch.start()

    async def asyncTearDown(self):
        self.access_patch.stop()
        await self.client.aclose()
        await self.engine.dispose()
        self.temp_dir.cleanup()

    async def _create_server(self) -> Server:
        server_path = Path(self.temp_dir.name) / "test-server"
        server_path.mkdir(parents=True, exist_ok=True)
        async with self.session_maker() as session:
            server = Server(
                name="File Test Server",
                server_type=ServerType.MINECRAFT_JAVA,
                status=ServerStatus.STOPPED,
                path=str(server_path),
                executable="server.jar",
                start_command="java -jar server.jar nogui",
                port=25565,
            )
            session.add(server)
            await session.commit()
            await session.refresh(server)
            return server

    async def test_browse_files_lists_directory(self):
        server = await self._create_server()
        (Path(server.path) / "mods").mkdir()
        (Path(server.path) / "mods" / "mod.jar").write_text("jar")
        (Path(server.path) / "server.properties").write_text("props")

        response = await self.client.get(f"/servers/{server.id}/files/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("mods", response.text)
        self.assertIn("server.properties", response.text)

    async def test_browse_files_returns_editor_for_text_file(self):
        server = await self._create_server()
        (Path(server.path) / "config.yml").write_text("key: value")

        response = await self.client.get(f"/servers/{server.id}/files/config.yml")
        self.assertEqual(response.status_code, 200)
        self.assertIn("key: value", response.text)

    async def test_browse_files_returns_404_for_missing_path(self):
        server = await self._create_server()
        response = await self.client.get(f"/servers/{server.id}/files/nonexistent.txt")
        self.assertEqual(response.status_code, 404)

    async def test_browse_files_returns_403_when_access_denied(self):
        server = await self._create_server()

        async def rejecting_access(request, server_id, perm, db):
            from fastapi import HTTPException

            raise HTTPException(status_code=403, detail="No access")

        self.access_patch.stop()
        with patch.object(
            files_router, "require_server_access", side_effect=rejecting_access
        ):
            response = await self.client.get(f"/servers/{server.id}/files/")
        self.access_patch.start()
        self.assertEqual(response.status_code, 403)

    async def test_save_file_updates_content_atomically(self):
        server = await self._create_server()
        test_file = Path(server.path) / "test.txt"
        test_file.write_text("original")

        response = await self.client.post(
            f"/servers/{server.id}/files/test.txt",
            data={"content": "updated content"},
        )
        self.assertEqual(response.status_code, 303)
        self.assertEqual(test_file.read_text(), "updated content")

    async def test_save_file_rejects_non_editable_file(self):
        server = await self._create_server()
        test_file = Path(server.path) / "server.jar"
        test_file.write_text("jarbytes")

        response = await self.client.post(
            f"/servers/{server.id}/files/server.jar",
            data={"content": "new bytes"},
        )
        self.assertEqual(response.status_code, 400)


if __name__ == "__main__":
    unittest.main()
