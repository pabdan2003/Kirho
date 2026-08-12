"""Regresiones de las acciones básicas del editor esquemático."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QPointF
from PyQt6.QtWidgets import QApplication

from main import MainWindow
from kirho.ui.items.wire_item import WireItem
from kirho.ui.scene import CircuitScene

_APP = QApplication.instance() or QApplication([])


def _scene():
    return CircuitScene()


def test_redo_restores_an_undone_edit():
    scene = _scene()
    scene.push_undo()
    scene.place_component("R", QPointF(0, 0), name="R1")

    assert scene.undo()
    assert not scene.components
    assert scene.redo()
    assert [item.name for item in scene.components] == ["R1"]


def test_align_and_distribute_selected_components():
    scene = _scene()
    first = scene.place_component("R", QPointF(0, 0), name="R1")
    middle = scene.place_component("R", QPointF(40, 20), name="R2")
    last = scene.place_component("R", QPointF(120, 60), name="R3")
    for item in (first, middle, last):
        item.setSelected(True)

    assert scene.align_selected("top")
    assert {item.pos().y() for item in (first, middle, last)} == {0}
    assert scene.distribute_selected("x")
    assert middle.pos().x() == 60


def test_snap_can_be_disabled_for_precise_placement():
    scene = _scene()
    scene.snap_enabled = False
    item = scene.place_component("R", QPointF(13, 17), name="R1")

    assert item.pos() == QPointF(13, 17)


def test_double_click_wire_helper_makes_an_orthogonal_corner():
    scene = _scene()
    wire = WireItem(QPointF(0, 0), QPointF(40, 40))
    scene.addItem(wire)
    scene.wires.append(wire)

    assert scene._toggle_wire_vertex(wire, QPointF(0, 40))
    assert len(scene.wires) == 2
    assert all(abs(w.line().dx()) < 1 or abs(w.line().dy()) < 1 for w in scene.wires)


def test_erc_flags_missing_ground_and_floating_pins():
    scene = _scene()
    scene.place_component("R", QPointF(0, 0), name="R1")

    warnings = scene.electrical_rule_warnings()
    assert any("ground" in warning.lower() for warning in warnings)
    assert any("Floating" in warning for warning in warnings)


def test_snap_action_toggles_without_calling_a_parameterless_slot():
    window = MainWindow()
    window._snap_action.trigger()

    assert not window.scene.snap_enabled
    assert any(action.text() == "Check Circuit (ERC)"
               for action in window._tools_button.menu().actions())
    window.close()
