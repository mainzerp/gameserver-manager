from contextvars import ContextVar
from pathlib import Path

from fastapi.templating import Jinja2Templates
from markupsafe import Markup
from starlette.requests import Request

from app.i18n import get_translations, get_locale_from_request, SUPPORTED_LOCALES

templates = Jinja2Templates(
    directory=str(Path(__file__).resolve().parent / "templates")
)

# Enable i18n extension
templates.env.add_extension("jinja2.ext.i18n")

# ContextVar that holds the active locale for the current async task / request.
# Set by LocaleMiddleware before each request is handled.
_current_locale: ContextVar[str] = ContextVar("current_locale", default="en")


def _gettext(string: str) -> str:
    return get_translations(_current_locale.get()).ugettext(string)


def _ngettext(singular: str, plural: str, n: int) -> str:
    return get_translations(_current_locale.get()).ungettext(singular, plural, n)


# Install per-request-aware gettext callables so _() in templates
# uses the locale that is active for the current request.
templates.env.install_gettext_callables(_gettext, _ngettext, newstyle=True)


def csrf_field(request: Request) -> Markup:
    token = request.session.get("csrf_token", "")
    return Markup(f'<input type="hidden" name="csrf_token" value="{token}">')


def filesizeformat(value) -> str:
    try:
        size = float(value)
    except (TypeError, ValueError):
        return "--"
    if size <= 0:
        return "0 B"

    units = ["B", "KB", "MB", "GB", "TB"]
    unit_index = 0
    while size >= 1024 and unit_index < len(units) - 1:
        size /= 1024
        unit_index += 1
    decimals = 0 if unit_index == 0 else 1
    return f"{size:.{decimals}f} {units[unit_index]}"


templates.env.globals["csrf_field"] = csrf_field
templates.env.globals["SUPPORTED_LOCALES"] = SUPPORTED_LOCALES
templates.env.filters["filesizeformat"] = filesizeformat
