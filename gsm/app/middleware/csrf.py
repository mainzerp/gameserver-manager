import re
import secrets
from urllib.parse import parse_qs

from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp, Receive, Scope, Send


class CSRFMiddleware:
    """Pure ASGI CSRF middleware that replays the request body so downstream
    handlers can still read it after the CSRF token has been extracted."""

    def __init__(self, app: ASGIApp, exempt_paths: list[str] | None = None):
        self.app = app
        self.exempt_paths = set(exempt_paths or [])

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope, receive)

        # Ensure a CSRF token exists in the session
        if "csrf_token" not in request.session:
            request.session["csrf_token"] = secrets.token_hex(32)

        path: str = scope.get("path", "")
        method: str = scope.get("method", "")

        # API routes and WebSocket upgrades are exempt
        if path.startswith("/api/"):
            await self.app(scope, receive, send)
            return

        if (
            method in ("POST", "PUT", "DELETE", "PATCH")
            and path not in self.exempt_paths
        ):
            headers = {k.lower(): v for k, v in scope.get("headers", [])}
            upgrade = headers.get(b"upgrade", b"").decode(errors="replace").lower()
            if upgrade != "websocket":
                submitted_token, receive = await self._extract_token(
                    scope, request, receive, headers
                )
                session_token = request.session.get("csrf_token", "")
                if not submitted_token or not secrets.compare_digest(
                    submitted_token, session_token
                ):
                    response = Response("CSRF validation failed", status_code=403)
                    await response(scope, receive, send)
                    return

        await self.app(scope, receive, send)

    async def _extract_token(
        self,
        scope: Scope,
        request: Request,
        receive: Receive,
        headers: dict,
    ) -> tuple[str, Receive]:
        """Read the CSRF token from headers, multipart body, or urlencoded body.

        Returns the token and a replacement ``receive`` that replays the body so
        downstream handlers can still read it.
        """
        content_type = headers.get(b"content-type", b"").decode(errors="replace")
        submitted_token = headers.get(b"x-csrf-token", b"").decode(errors="replace")

        if submitted_token or "application/json" in content_type:
            return submitted_token, receive

        if "multipart/form-data" in content_type:
            token, buffered, last_more_body = await self._stream_multipart_csrf(receive)
            return (
                token,
                self._build_multipart_replay_receive(
                    buffered, last_more_body, receive
                ),
            )

        body = await self._read_body(receive)
        if "application/x-www-form-urlencoded" in content_type:
            parsed = parse_qs(
                body.decode("utf-8", errors="replace"),
                keep_blank_values=True,
            )
            submitted_token = parsed.get("csrf_token", [""])[0]

        return submitted_token, self._build_body_replay_receive(body)

    @staticmethod
    def _build_multipart_replay_receive(
        buffered: list[tuple[bytes, bool]],
        last_more_body: bool,
        original_receive: Receive,
    ) -> Receive:
        """Return a replay ``receive`` for buffered multipart chunks."""
        chunk_idx = 0

        async def replay_receive() -> dict:
            nonlocal chunk_idx
            if chunk_idx < len(buffered):
                body_chunk, orig_more = buffered[chunk_idx]
                chunk_idx += 1
                # If more buffered chunks remain, always signal more_body=True
                has_more = chunk_idx < len(buffered) or orig_more
                return {
                    "type": "http.request",
                    "body": body_chunk,
                    "more_body": has_more,
                }
            # All buffered chunks sent — delegate to original receive
            return await original_receive()

        return replay_receive

    @staticmethod
    def _build_body_replay_receive(body: bytes) -> Receive:
        """Return a replay ``receive`` for a fully-read body."""
        body_sent = False

        async def replay_receive() -> dict:
            nonlocal body_sent
            if not body_sent:
                body_sent = True
                return {
                    "type": "http.request",
                    "body": body,
                    "more_body": False,
                }
            return {"type": "http.disconnect"}

        return replay_receive

    MAX_CSRF_BODY_READ = 10 * 1024 * 1024  # 10 MB (for urlencoded forms)
    MAX_MULTIPART_CSRF_SEARCH = 64 * 1024  # 64 KB — csrf_token is always near the top

    @staticmethod
    async def _read_body(receive: Receive) -> bytes:
        body = b""
        more_body = True
        while more_body:
            message = await receive()
            body += message.get("body", b"")
            if len(body) > CSRFMiddleware.MAX_CSRF_BODY_READ:
                break
            more_body = message.get("more_body", False)
        return body

    @staticmethod
    async def _stream_multipart_csrf(receive: Receive):
        """Read multipart chunks only until csrf_token is found (max 64 KB).
        Returns (token, buffered_chunks, last_more_body).
        The caller must build a streaming replay from buffered_chunks + original receive."""
        chunks: list[tuple[bytes, bool]] = []
        total = 0
        token = ""
        last_more_body = True

        while total < CSRFMiddleware.MAX_MULTIPART_CSRF_SEARCH:
            message = await receive()
            chunk = message.get("body", b"")
            last_more_body = message.get("more_body", False)
            chunks.append((chunk, last_more_body))
            total += len(chunk)

            accumulated = b"".join(c for c, _ in chunks)
            match = re.search(rb'name="csrf_token"\r\n\r\n([^\r\n]+)', accumulated)
            if match:
                token = match.group(1).decode(errors="replace")
                break

            if not last_more_body:
                break

        return token, chunks, last_more_body
