"""
Internationalization (i18n) module for GameServer Manager.

Uses Babel for translation management. Provides locale detection
from session/request and translation loading/caching.
"""

import os

from babel.support import Translations

SUPPORTED_LOCALES = ["en", "de"]
DEFAULT_LOCALE = "en"
TRANSLATIONS_DIR = os.path.join(os.path.dirname(__file__), "..", "translations")


_translations_cache: dict[str, Translations] = {}


def get_translations(locale: str = DEFAULT_LOCALE) -> Translations:
    if locale not in SUPPORTED_LOCALES:
        locale = DEFAULT_LOCALE
    if locale not in _translations_cache:
        try:
            _translations_cache[locale] = Translations.load(TRANSLATIONS_DIR, [locale])
        except Exception:
            _translations_cache[locale] = Translations.load(
                TRANSLATIONS_DIR, [DEFAULT_LOCALE]
            )
    return _translations_cache[locale]


def get_locale_from_request(request) -> str:
    locale = getattr(request, "session", {}).get("locale", "")
    if locale in SUPPORTED_LOCALES:
        return locale
    accept = getattr(request, "headers", {}).get("accept-language", "")
    for part in accept.split(","):
        lang = part.split(";")[0].strip().split("-")[0].lower()
        if lang in SUPPORTED_LOCALES:
            return lang
    return DEFAULT_LOCALE
