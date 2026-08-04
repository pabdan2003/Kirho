"""Regresiones de persistencia del formato .csin."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from main import MainWindow
from ohmpy.ui.scene import CircuitScene


def test_load_sheet_restores_digital_configuration():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    scene = CircuitScene()
    MainWindow._load_sheet_data(None, scene, {"components": [{
        "type": "AND", "name": "U1", "value": 0, "x": 0, "y": 0,
        "dig_inputs": 4, "dig_bits": 7, "dig_bits_adc": 10,
        "dig_vref": 4.2, "dig_clk": "CLOCKX", "dig_tpd_ns": 17,
        "dig_analog_node": "sense", "dig_input_nodes": ["A", "B"],
        "dig_input_neg": [True, False, True, False],
    }], "wires": []})

    item = scene.components[0]
    assert item.dig_inputs == 4
    assert item.dig_bits == 7
    assert item.dig_bits_adc == 10
    assert item.dig_vref == 4.2
    assert item.dig_clk == "CLOCKX"
    assert item.dig_tpd_ns == 17
    assert item.dig_analog_node == "sense"
    assert item.dig_input_nodes == ["A", "B"]
    assert item.dig_input_neg == [True, False, True, False]
