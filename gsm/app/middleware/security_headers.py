import secrets

from starlette.types import ASGIApp, Receive, Scope, Send


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # Generate a fresh nonce for each request. Store it in scope so
        # Jinja2 templates can access it via request.state.csp_nonce.
        nonce = secrets.token_urlsafe(16)
        if "state" not in scope:
            scope["state"] = {}
        scope["state"]["csp_nonce"] = nonce

        nonce_directive = f"'nonce-{nonce}'".encode()
        csp_value = (
            b"default-src 'self'; "
            b"script-src 'self' " + nonce_directive + b"; "
            b"script-src-elem 'self' " + nonce_directive + b"; "
            b"style-src 'self' 'unsafe-inline'; "
            b"img-src 'self' data:; "
            b"font-src 'self'; "
            b"connect-src 'self' wss: ws:; "
            b"frame-src 'self'; "
            b"frame-ancestors 'self'"
        )

        async def send_with_headers(message: dict) -> None:
            if message["type"] == "http.response.start":
                extra = [
                    (b"x-content-type-options", b"nosniff"),
                    (b"x-frame-options", b"SAMEORIGIN"),
                    (
                        b"strict-transport-security",
                        b"max-age=31536000; includeSubDomains",
                    ),
                    (b"referrer-policy", b"strict-origin-when-cross-origin"),
                    (
                        b"permissions-policy",
                        b"camera=(), microphone=(), geolocation=()",
                    ),
                    (b"content-security-policy", csp_value),
                ]
                existing = list(message.get("headers", []))
                existing.extend(extra)
                message["headers"] = existing
            await send(message)

        await self.app(scope, receive, send_with_headers)
