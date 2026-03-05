"""
Vogelnamen vertaling via BirdNET-Pi JSON label bestanden
Format: {"Scientific Name": "Translated Name", ...}
"""

import os
import json
import logging

log = logging.getLogger("birdwatch.translations")

_cache = {}
_labels_dir = os.environ.get("LABELS_DIR", "/app/labels")


def _load_locale(locale: str) -> dict:
    if locale in _cache:
        return _cache[locale]

    locale_file = os.path.join(_labels_dir, f"labels_{locale}.json")
    if not os.path.exists(locale_file):
        log.warning(f"Labels file not found: {locale_file}")
        _cache[locale] = {}
        return {}

    try:
        with open(locale_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        _cache[locale] = data
        log.info(f"Loaded {len(data)} translations for locale '{locale}'")
        return data
    except Exception as e:
        log.error(f"Failed to load labels_{locale}.json: {e}")
        _cache[locale] = {}
        return {}


def translate_scientific(scientific_name: str, locale: str) -> str:
    """Translate via scientific name (most reliable)."""
    if not locale or locale == "en":
        return None
    translations = _load_locale(locale)
    return translations.get(scientific_name)


def translate(common_name: str, locale: str, scientific_name: str = "") -> str:
    """Translate bird name to target locale."""
    if not locale or locale == "en":
        return common_name
    # Try scientific name first (most reliable)
    if scientific_name:
        result = translate_scientific(scientific_name, locale)
        if result:
            return result
    return common_name


def available_locales() -> list:
    locales = ["en"]
    if os.path.exists(_labels_dir):
        for f in sorted(os.listdir(_labels_dir)):
            if f.startswith("labels_") and f.endswith(".json"):
                code = f[7:-5]
                locales.append(code)
    return locales
