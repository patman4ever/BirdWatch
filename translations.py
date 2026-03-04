"""
Vogelnamen vertaling via BirdNET-Pi label bestanden
Labels worden geladen uit /app/labels/labels_XX.txt
Elke regel bevat: Scientific_Name_Common Name
"""

import os
import logging

log = logging.getLogger("birdwatch.translations")

_cache = {}
_en_index = {}  # English name -> index
_labels_dir = os.environ.get("LABELS_DIR", "/app/labels")


def _load_english_index():
    """Load English labels to build index mapping name -> line number"""
    global _en_index
    if _en_index:
        return
    en_file = os.path.join(_labels_dir, "labels_en.txt")
    if not os.path.exists(en_file):
        # Try birdnetlib's own labels
        try:
            import birdnetlib
            base = os.path.dirname(birdnetlib.__file__)
            for root, dirs, files in os.walk(base):
                for f in files:
                    if f == "labels_en.txt" or f == "labels.txt":
                        en_file = os.path.join(root, f)
                        break
        except Exception:
            pass

    if not os.path.exists(en_file):
        log.warning(f"English labels not found at {en_file}")
        return

    with open(en_file, "r", encoding="utf-8") as f:
        for i, line in enumerate(f):
            line = line.strip()
            if "_" in line:
                # Format: "Scientific_Name_Common Name" -> extract common name
                parts = line.split("_", 1)
                if len(parts) == 2:
                    common = parts[1].strip()
                    _en_index[common] = i
            else:
                _en_index[line] = i

    log.info(f"Loaded {len(_en_index)} English bird names")


def _load_locale(locale: str) -> dict:
    """Load translation dict for a locale"""
    if locale in _cache:
        return _cache[locale]

    _load_english_index()
    if not _en_index:
        _cache[locale] = {}
        return {}

    locale_file = os.path.join(_labels_dir, f"labels_{locale}.txt")
    if not os.path.exists(locale_file):
        log.warning(f"Labels file not found: {locale_file}")
        _cache[locale] = {}
        return {}

    translations = {}
    with open(locale_file, "r", encoding="utf-8") as f:
        locale_lines = [line.strip() for line in f]

    # Build reverse index: line number -> locale name
    en_lines = sorted(_en_index.items(), key=lambda x: x[1])
    for english_name, idx in en_lines:
        if idx < len(locale_lines):
            locale_name = locale_lines[idx]
            if "_" in locale_name:
                parts = locale_name.split("_", 1)
                locale_name = parts[1].strip() if len(parts) == 2 else locale_name
            if locale_name and locale_name != english_name:
                translations[english_name] = locale_name

    log.info(f"Loaded {len(translations)} translations for locale '{locale}'")
    _cache[locale] = translations
    return translations


def translate(common_name: str, locale: str) -> str:
    """Translate English bird name to target locale."""
    if not locale or locale == "en":
        return common_name
    translations = _load_locale(locale)
    return translations.get(common_name, common_name)


def available_locales() -> list:
    """Return list of available locale codes based on label files present"""
    locales = ["en"]
    if os.path.exists(_labels_dir):
        for f in os.listdir(_labels_dir):
            if f.startswith("labels_") and f.endswith(".txt") and f != "labels_en.txt":
                code = f[7:-4]  # strip "labels_" and ".txt"
                locales.append(code)
    return sorted(locales)
