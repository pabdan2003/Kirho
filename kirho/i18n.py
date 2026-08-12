"""Runtime support for compiled Qt translation catalogs."""
from pathlib import Path

from PyQt6.QtCore import QTranslator


def load_translator(app, language: str) -> QTranslator | None:
    """Load a non-default catalog, returning a retained translator."""
    if language == "en":
        return None
    translator = QTranslator(app)
    catalog = Path(__file__).parent.parent / "i18n" / f"kirho_{language}.qm"
    if translator.load(str(catalog)):
        app.installTranslator(translator)
        return translator
    return None
