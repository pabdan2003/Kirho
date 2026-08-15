"""Regresiones del switch de palanca ON-OFF-ON."""
import os

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import numpy as np
from PyQt6.QtCore import QEvent, QPointF, Qt
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtWidgets import QApplication

from kirho.engine.components import SPDT3
from kirho.ui.dialogs.component_dialog import ComponentDialog
from kirho.ui.scene import CircuitScene, build_engine_components_for_item
from kirho.ui.style import COLORS


_APP = QApplication.instance() or QApplication([])


def test_spdt3_connects_only_the_selected_output():
    node_map = {'COM': 0, 'ON1': 1, 'ON2': 2}

    for position in (-1, 0, 1):
        matrix = np.zeros((3, 3))
        current = np.zeros(3)
        SPDT3('S1', 'COM', 'ON1', 'ON2', position).stamp(
            matrix, current, node_map)

        on1_closed = position == -1
        on2_closed = position == 1
        assert bool(abs(matrix[0, 1]) > 900) == on1_closed
        assert bool(abs(matrix[0, 2]) > 900) == on2_closed


def test_spdt3_is_placeable_editable_and_cycles_three_positions():
    scene = CircuitScene()
    item = scene.place_component(
        'SPDT3', QPointF(0, 0), name='S1',
        node1='COM', node2='ON1', node3='ON2')

    assert item.value == 0.0
    assert len(item.all_pin_positions_scene()) == 3
    engine_items = build_engine_components_for_item(item, {})
    assert len(engine_items) == 1
    assert isinstance(engine_items[0], SPDT3)

    item.switch_on1_key = '1'
    item.switch_off_key = '0'
    item.switch_on2_key = '2'
    for key, text, expected in (
            (Qt.Key.Key_1, '1', -1.0),
            (Qt.Key.Key_0, '0', 0.0),
            (Qt.Key.Key_2, '2', 1.0)):
        scene.keyPressEvent(QKeyEvent(
            QEvent.Type.KeyPress, key, Qt.KeyboardModifier.NoModifier, text))
        assert item.value == expected
    snapshot = scene._serialize_component(item)
    restored = CircuitScene()._instantiate_component(snapshot)
    assert restored.switch_on1_key == '1'
    assert restored.switch_off_key == '0'
    assert restored.switch_on2_key == '2'

    item.value = 0.0
    scene._cycle_switch(item)
    assert item.value == -1.0
    scene._cycle_switch(item)
    assert item.value == 1.0
    scene._cycle_switch(item)
    assert item.value == 0.0


def test_spdt3_dialog_exposes_three_independent_keys():
    scene = CircuitScene()
    item = scene.place_component('SPDT3', QPointF(0, 0), name='S1')
    dialog = ComponentDialog(item, COLORS)
    dialog._switch_on1_key_edit.setText('1')
    dialog._switch_off_key_edit.setText('0')
    dialog._switch_on2_key_edit.setText('2')
    data = dialog.get_data()

    assert data['switch_on1_key'] == '1'
    assert data['switch_off_key'] == '0'
    assert data['switch_on2_key'] == '2'
