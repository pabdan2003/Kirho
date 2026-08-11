"""Every bundled example must remain a loadable OhmPy project."""
import json
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from main import MainWindow
from ohmpy.ui.scene import CircuitScene

_APP = QApplication.instance() or QApplication([])
_EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


def test_examples_are_valid_and_loadable():
    files = sorted(_EXAMPLES.glob("*.csin"))
    assert len(files) >= 10
    for path in files:
        data = json.loads(path.read_text(encoding="utf-8"))
        sheets = data.get("sheets") or [{
            "components": data.get("components", []),
            "wires": data.get("wires", []),
        }]
        assert sheets, path.name
        for sheet_data in sheets:
            scene = CircuitScene()
            MainWindow._load_sheet_data(None, scene, sheet_data)
            assert scene.components, path.name
