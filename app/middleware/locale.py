from starlette.types import ASGIApp, Receive, Scope, Send
from starlette.requests import Request

from app.i18n import get_locale_from_request
from app.template_utils import _current_locale


class LocaleMiddleware:
    """Sets the active locale ContextVar from the session before each request."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            request = Request(scope, receive)
            locale = get_locale_from_request(request)
            token = _current_locale.set(locale)
            try:
                await self.app(scope, receive, send)
            finally:
                _current_locale.reset(token)
        else:
            await self.app(scope, receive, send)
