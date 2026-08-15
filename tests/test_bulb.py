"""Regresiones del componente bombillo."""
import os
from types import SimpleNamespace

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PyQt6.QtCore import QPointF
from PyQt6.QtWidgets import QApplication

from main import MainWindow
from kirho.engine.components import Resistor
from kirho.ui.scene import CircuitScene, build_engine_components_for_item


_APP = QApplication.instance() or QApplication([])


def test_bulb_uses_voltage_threshold_and_fixed_load():
    scene = CircuitScene()
    item = scene.place_component(
        'LAMP', QPointF(0, 0), name='L1', node1='N1', node2='0')

    assert item.value == 3.0
    assert item.unit == 'V'
    engine_items = build_engine_components_for_item(
        item, {'L1__p1': 'N1', 'L1__p2': '0'})

    assert len(engine_items) == 1
    assert isinstance(engine_items[0], Resistor)
    assert engine_items[0].R == 100.0
    assert engine_items[0].is_lamp
    assert not MainWindow._light_is_on(item, 2.99, 0.0)
    assert MainWindow._light_is_on(item, 3.0, 0.0)
    assert MainWindow._light_is_on(item, 0.0, 3.0)

    item.value = 6.0
    assert not MainWindow._light_is_on(item, 3.0, 0.0)


def test_bulb_threshold_is_independent_of_led_color():
    item = SimpleNamespace(comp_type='LAMP', value=12.0, led_color='red')

    assert MainWindow._light_threshold(item) == 12.0
    assert not MainWindow._light_is_on(item, 5.0)
    assert MainWindow._light_is_on(item, 12.0)


def test_bulb_display_voltage_is_polarity_independent():
    item = SimpleNamespace(comp_type='LAMP')

    assert MainWindow._display_voltage(item, 13.0, 0.0) == 13.0
    assert MainWindow._display_voltage(item, 0.0, 13.0) == 13.0


def test_bulb_live_voltage_drop_allows_ground_on_either_pin():
    samples = {'OUT': [12.0, 13.0]}

    assert list(MainWindow._voltage_drop_array(samples, 'OUT', '0')) == [12.0, 13.0]
    assert list(MainWindow._voltage_drop_array(samples, '0', 'OUT')) == [-12.0, -13.0]
