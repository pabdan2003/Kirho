import os

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QPointF

from kirho.ui.dialogs.component_dialog import ComponentDialog
from kirho.ui.items.component_item import ComponentItem
from kirho.ui.items.wire_item import WireItem
from kirho.ui.scene import CircuitScene
from kirho.ui.style import COLORS, GRID_SIZE


def test_555_pins_align_to_grid_and_properties_show_all_pins():
    app = QApplication.instance() or QApplication([])
    item = ComponentItem('IC555', 'U1')

    assert len(item.all_pin_positions_scene()) == 8
    assert all(p.x() % GRID_SIZE == 0 and p.y() % GRID_SIZE == 0
               for p in item.all_pin_positions_scene())
    assert len(ComponentDialog(item, COLORS)._timer_node_edits) == 8


def test_pin_on_existing_wire_is_part_of_its_net():
    app = QApplication.instance() or QApplication([])
    scene = CircuitScene()
    timer = scene.place_component('IC555', QPointF(0, 0), name='U1')
    resistor = scene.place_component('R', QPointF(50, -60), name='R1', value=1000)
    wire = WireItem(QPointF(-120, -60), QPointF(0, -60))
    scene.addItem(wire)
    scene.wires.append(wire)

    nets = scene.extract_netlist()

    assert nets['U1__p4'] == nets['R1__p1']


def test_counter_grows_one_output_pin_per_bit():
    app = QApplication.instance() or QApplication([])
    item = ComponentItem('COUNTER', 'CNT1')
    item.prepareGeometryChange()
    item.dig_bits = 4

    pins = item.all_pin_positions_scene()

    # p1=Q0, p2=CLK y p3…p5=Q1…Q3; no hay pines superpuestos.
    assert len(pins) == 5
    assert len({(p.x(), p.y()) for p in pins}) == 5
    assert pins[0].x() > 0 and pins[1].x() < 0
    assert all(p.x() > 0 for p in pins[2:])
