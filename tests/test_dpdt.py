"""Regresiones del switch de dos polos y dos posiciones."""
import os

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import numpy as np
from PyQt6.QtCore import QEvent, QPointF, Qt
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtWidgets import QApplication

from kirho.engine.components import DPDT
from kirho.ui.dialogs.component_dialog import ComponentDialog
from kirho.ui.scene import CircuitScene, build_engine_components_for_item
from kirho.ui.style import COLORS


_APP = QApplication.instance() or QApplication([])


def test_dpdt_switches_both_poles_together():
    node_map = {'CA': 0, 'A1': 1, 'A2': 2,
                'CB': 3, 'B1': 4, 'B2': 5}

    for position in (False, True):
        matrix = np.zeros((6, 6))
        current = np.zeros(6)
        DPDT('S1', 'CA', 'A1', 'A2', 'CB', 'B1', 'B2', position).stamp(
            matrix, current, node_map)

        assert bool(abs(matrix[0, 1]) > 900) is (not position)
        assert bool(abs(matrix[0, 2]) > 900) is position
        assert bool(abs(matrix[3, 4]) > 900) is (not position)
        assert bool(abs(matrix[3, 5]) > 900) is position


def test_dpdt_has_six_pins_and_persists_its_nodes():
    scene = CircuitScene()
    item = scene.place_component(
        'DPDT', QPointF(0, 0), name='S1',
        node1='CA', node2='A1', node3='A2')
    item.node4, item.node5, item.node6 = 'CB', 'B1', 'B2'

    assert item.value == 0.0
    assert len(item.all_pin_positions_scene()) == 6
    engine_items = build_engine_components_for_item(item, {})
    assert len(engine_items) == 1
    assert isinstance(engine_items[0], DPDT)

    item.switch_key = 'D'
    scene.keyPressEvent(QKeyEvent(
        QEvent.Type.KeyPress, Qt.Key.Key_D,
        Qt.KeyboardModifier.NoModifier, 'd'))
    assert item.value == 1.0

    restored = CircuitScene()._instantiate_component(
        scene._serialize_component(item))
    assert restored.node4 == 'CB'
    assert restored.node5 == 'B1'
    assert restored.node6 == 'B2'


def test_dpdt_sixth_pin_gets_its_net_from_a_visual_wire():
    scene = CircuitScene()
    switch = scene.place_component('DPDT', QPointF(0, 0), name='S1')
    ground = scene.place_component('GND', QPointF(100, 40), name='GND1')
    from kirho.ui.items.wire_item import WireItem
    wire = WireItem(switch.all_pin_positions_scene()[5],
                    ground.all_pin_positions_scene()[0])
    scene.addItem(wire)
    scene.wires.append(wire)

    assert scene.extract_netlist()['S1__p6'] == '0'


def test_dpdt_dialog_exposes_all_six_nodes():
    scene = CircuitScene()
    item = scene.place_component(
        'DPDT', QPointF(0, 0), name='S1',
        node1='CA', node2='A1', node3='A2')
    item.node4, item.node5, item.node6 = 'CB', 'B1', 'B2'
    data = ComponentDialog(item, COLORS).get_data()

    assert data['node1'] == 'CA'
    assert data['node4'] == 'CB'
    assert data['node6'] == 'B2'
