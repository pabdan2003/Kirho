import os

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QPointF

from pynode.ui.dialogs.component_dialog import ComponentDialog
from pynode.ui.items.component_item import ComponentItem
from pynode.ui.items.wire_item import WireItem
from pynode.ui.scene import CircuitScene
from pynode.ui.style import COLORS, GRID_SIZE


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
