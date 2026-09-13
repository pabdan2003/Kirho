"""Regresiones de las acciones básicas del editor esquemático."""
import os
import re
import time
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QEvent, QPointF, Qt
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtPrintSupport import QPrinter
from PyQt6.QtWidgets import QApplication, QMenu, QToolBar

from main import MainWindow
from kirho.pcb import (
    PcbBoard, PcbFootprint, PcbPad, PcbText, PcbTrack, PcbVia, pad_shape,
)
from kirho.ui.items.wire_item import WireItem
from kirho.ui.items.component_item import ComponentItem
from kirho.ui.dialogs.component_picker_dialog import ComponentPickerDialog
from kirho.ui.dialogs.component_dialog import ComponentDialog
from kirho.ui.pcb_editor import (
    PcbEditorWidget, PcbTextItem, PcbTrackItem, PcbViaItem,
)
from kirho.ui.scene import CircuitScene
from kirho.ui.style import COLORS

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


def test_external_rp2040_symbol_uses_library_definition_without_footprint():
    scene = _scene()
    scene.external_board_definitions = {
        'RP2040': {
            'name': 'RP2040 Dev Board',
            'reference_prefix': 'PICO',
            'pins': [{'number': number, 'name': f'GP{number - 1}'}
                     for number in range(1, 41)],
        }
    }
    item = scene.place_component('RP2040', QPointF(13, 17))

    assert item.name == 'PICO1'
    assert item.footprint_name == ''
    assert len(item.all_pin_positions_scene()) == 40
    assert item.all_pin_positions_scene()[0].x() < 0
    assert item.all_pin_positions_scene()[-1].x() > 0
    assert all(point.x() % 20 == 0 and point.y() % 20 == 0
               for point in item.all_pin_positions_scene())
    assert len(scene.extract_netlist()) == 40
    assert scene.electrical_rule_warnings() == []


def test_external_rp2040_maps_gpio_to_its_schematic_net():
    scene = _scene()
    scene.external_board_definitions = {
        'RP2040': {
            'pins': [
                {'number': 1, 'name': 'GP0', 'gpio': 0},
                {'number': 2, 'name': 'GP11', 'gpio': 11},
            ],
        }
    }
    scene.place_component('RP2040', QPointF(0, 0), name='PICO1')

    assert scene.external_gpio_net_map() == {
        0: 'net_PICO1__p1',
        11: 'net_PICO1__p2',
    }


def test_external_board_ground_pins_are_global_ground():
    scene = _scene()
    scene.external_board_definitions = {
        'RP2040': {
            'pins': [
                {'number': 1, 'name': 'GP0', 'gpio': 0, 'kind': 'gpio'},
                {'number': 2, 'name': 'GND', 'kind': 'ground'},
                {'number': 3, 'name': 'AGND', 'kind': 'analog_ground'},
                {'number': 4, 'name': 'GP1', 'gpio': 1, 'kind': 'gpio'},
            ],
        }
    }
    scene.place_component('RP2040', QPointF(0, 0), name='PICO1')

    nets = scene.extract_netlist()
    assert nets['PICO1__p2'] == '0'
    assert nets['PICO1__p3'] == '0'
    assert nets['PICO1__p1'] != '0'
    assert nets['PICO1__p4'] != '0'


def test_rp2040_firmware_gpio_drives_a_connected_led():
    window = MainWindow()
    try:
        scene = window.scene
        scene.components.clear()
        scene.wires.clear()
        scene.clear()
        scene.external_board_definitions = {
            'RP2040': {
                'pins': [
                    {'number': 1, 'name': 'GP11', 'gpio': 11},
                    {'number': 2, 'name': 'GP0', 'gpio': 0},
                ],
            }
        }
        scene.place_component('RP2040', QPointF(0, 0), name='PICO1')
        scene.place_component(
            'R', QPointF(150, 0), name='R1', value=1000.0,
            node1='net_PICO1__p1', node2='LED_NET')
        led = scene.place_component(
            'LED', QPointF(300, 0), name='LED1',
            node1='LED_NET', node2='0')

        window._on_component_selected(scene.components[0])
        window._set_external_gpio_test(1)
        assert led.led_on

        window._set_external_gpio_test(0)
        assert not led.led_on
    finally:
        window.close()


def test_rp2040_firmware_stays_with_component_and_simulate_runs_it():
    window = MainWindow()
    try:
        scene = window.scene
        scene.components.clear()
        scene.wires.clear()
        scene.clear()
        pico = scene.place_component('RP2040', QPointF(0, 0), name='PICO1')
        resistor = scene.place_component('R', QPointF(150, 0), name='R1',
                                          value=1000.0,
                                          node1='net_PICO1__p1',
                                          node2='LED_NET')
        led = scene.place_component('LED', QPointF(300, 0), name='LED1',
                                    node1='LED_NET', node2='0')
        image = SimpleNamespace(
            format='py', path='main.py',
            source='from machine import Pin\nPin(0, Pin.OUT).on()\n')
        pico.external_firmware_path = image.path
        pico.external_firmware_image = image

        window._on_component_selected(pico)
        assert window._external_component_controller.firmware is image
        assert not hasattr(window._external_component_controller, 'run_button')

        window._on_component_selected(resistor)
        window._on_component_selected(pico)
        assert window._external_component_controller.firmware is image

        window._toggle_simulation(True)
        runner = pico.external_firmware_runner
        assert runner is not None
        assert runner.wait(1)
        QApplication.processEvents()
        assert runner.error is None
        assert led.led_on
        assert not window.simulation._sim_timer.isActive()
        window._toggle_simulation(False)
    finally:
        window.close()


def test_schematic_properties_panel_uses_component_pin_definitions():
    window = MainWindow()

    def fields_for(item):
        window._on_component_selected(item)
        return {
            window.prop_table.item(row, 0).text():
            window.prop_table.item(row, 1).text()
            for row in range(window.prop_table.rowCount())
        }

    try:
        pico = window.scene.place_component('RP2040', QPointF(0, 0), name='PICO1')
        pico_fields = fields_for(pico)
        assert pico_fields['Board'] == 'RP2040 Dev Board'
        assert pico_fields['Pins'] == '40'
        assert 'Value' not in pico_fields
        assert 'Node +' not in pico_fields
        assert 'Node −' not in pico_fields

        timer = window.scene.place_component('IC555', QPointF(0, 0), name='U1')
        timer_fields = fields_for(timer)
        assert all(label in timer_fields for label in (
            '1 GND', '2 TRIG', '3 OUT', '4 RESET',
            '5 CTRL', '6 THRESH', '7 DISCH', '8 VCC'))

        opamp = window.scene.place_component('TL082', QPointF(0, 0), name='U2')
        opamp_fields = fields_for(opamp)
        assert all(label in opamp_fields for label in (
            'Output (OUT)', 'Input − (IN−)', 'Input + (IN+)',
            'Supply V+', 'Supply V−'))

        comparator = window.scene.place_component(
            'COMPARATOR', QPointF(0, 0), name='CMP1')
        comparator_fields = fields_for(comparator)
        assert all(label in comparator_fields for label in (
            'Output (Y)', 'Input 1 (A)', 'Input 2 (B)',
            'Resolution (bits)', 'Vref', 'MNA analog node',
            'Net CLK (optional)'))

        counter = window.scene.place_component(
            'COUNTER', QPointF(1000, 0), name='CNT1')
        counter_fields = fields_for(counter)
        assert counter_fields['Q0'].startswith('net_CNT1__p1')
        assert counter_fields['CLK'].startswith('net_CNT1__p2')
        assert 'Node +' not in counter_fields
        assert 'Node −' not in counter_fields

        source = window.scene.place_component(
            'V', QPointF(0, 0), name='V1', node1='NEG', node2='POS')
        source_fields = fields_for(source)
        assert source_fields['Node +'] == 'POS'
        assert source_fields['Node −'] == 'NEG'

        generator = window.scene.place_component(
            'FGEN', QPointF(1200, 0), name='FGEN1')
        generator_fields = fields_for(generator)
        assert all(label in generator_fields for label in (
            'Waveform', 'Frequency (Hz)', 'Offset (V)',
            'Duty cycle (%)', 'Phase'))

        meter = window.scene.place_component(
            'MULTIMETER', QPointF(1400, 0), name='M1')
        meter_fields = fields_for(meter)
        assert all(label in meter_fields for label in (
            'Measurement', 'Coupling', 'Reading'))

        scope = window.scene.place_component(
            'OSC', QPointF(1600, 0), name='XSC1')
        scope_fields = fields_for(scope)
        assert all(label in scope_fields for label in (
            'Time/div', 'Channel A V/div', 'Channel B V/div',
            'Trigger source', 'Trigger edge', 'Trigger level'))
    finally:
        window.close()


def test_live_tick_updates_multimeter_from_voltage_array():
    window = MainWindow()
    try:
        window.scene.components.clear()
        meter = window.scene.place_component(
            'MULTIMETER', QPointF(0, 0), name='M1', node1='N', node2='0')
        window.simulation._sim_all_comps = [meter]
        window.simulation._live_pin_node = {}
        window.simulation._live_components = []

        window.simulation._update_items_from_live({
            'voltages': {'N': [1.0, 2.0]},
        })

        assert meter.meter_reading == 1.5
    finally:
        window.close()


def test_component_picker_scales_external_board_preview_to_fit():
    definition = {
        'name': 'RP2040 Dev Board',
        'pins': [{'number': number, 'name': f'GP{number - 1}'}
                 for number in range(1, 41)],
    }
    dialog = ComponentPickerDialog(
        'External boards',
        [('RP2040', 'RP2040 Dev Board', '▣40', definition)],
        ComponentItem, COLORS)
    _APP.processEvents()

    item = dialog.preview_scene.items()[0]
    assert item.external_definition == definition
    assert dialog.preview_view.transform().m11() < 1.0
    dialog.close()


def test_properties_panel_does_not_assign_voltage_labels_to_other_components():
    window = MainWindow()
    voltage_node_types = {'V', 'VAC', 'I', 'LAMP'}

    try:
        for index, comp_type in enumerate(ComponentItem.COMP_TYPES):
            item = window.scene.place_component(
                comp_type, QPointF(index * 300, 1000), name=f'X{index + 1}')
            window._on_component_selected(item)
            fields = {
                window.prop_table.item(row, 0).text()
                for row in range(window.prop_table.rowCount())
            }
            if comp_type not in voltage_node_types:
                assert 'Node +' not in fields
                assert 'Node −' not in fields
    finally:
        window.close()


def test_paper_format_changes_the_canvas_page():
    scene = _scene()

    assert scene.paper_format == "A4"
    assert not scene.paper_visible
    assert scene.set_paper_format("LEGAL")
    assert scene.paper_rect().width() == 2492
    assert scene.paper_rect().height() == 1512
    scene.set_paper_visible(True)
    assert scene.paper_visible
    assert not scene.set_paper_format("unknown")


def test_title_block_rect_is_inside_the_paper():
    scene = _scene()

    assert scene.paper_rect().contains(scene.title_block_rect())


def test_title_block_grows_for_long_text():
    scene = _scene()
    default_width = scene.title_block_rect().width()
    scene.set_title_block({'title': 'A' * 80})

    assert scene.title_block_rect().width() > default_width
    assert scene.paper_rect().contains(scene.title_block_rect())


def test_double_click_wire_helper_makes_an_orthogonal_corner():
    scene = _scene()
    wire = WireItem(QPointF(0, 0), QPointF(40, 40))
    scene.addItem(wire)
    scene.wires.append(wire)

    assert scene._toggle_wire_vertex(wire, QPointF(0, 40))
    assert len(scene.wires) == 2
    assert all(abs(w.line().dx()) < 1 or abs(w.line().dy()) < 1 for w in scene.wires)


def test_wire_endpoint_on_another_wire_forms_a_t_junction_net():
    scene = _scene()
    first = scene.place_component("R", QPointF(0, 0), name="R1")
    second = scene.place_component("R", QPointF(200, 60), name="R2")
    for start, end in ((first.pin_positions_scene()[1], QPointF(200, 0)),
                       (second.pin_positions_scene()[0], QPointF(160, 0))):
        wire = WireItem(start, end)
        scene.addItem(wire)
        scene.wires.append(wire)

    nets = scene.extract_netlist()

    assert nets["R1__p2"] == nets["R2__p1"]


def test_wire_mode_stays_active_and_starts_next_wire_from_scratch():
    class Click:
        def __init__(self, point):
            self.point = point

        def scenePos(self):
            return self.point

    scene = _scene()
    scene.set_mode('wire')
    scene.mousePressEvent(Click(QPointF(0, 0)))
    scene.mousePressEvent(Click(QPointF(20, 0)))

    assert len(scene.wires) == 1
    assert scene._mode == 'wire'
    assert scene._wire_start is None
    scene.mousePressEvent(Click(QPointF(40, 0)))
    assert scene._wire_start == QPointF(40, 0)


def test_wire_action_uses_w_shortcut():
    window = MainWindow()
    action = window._shared_actions['wire']

    assert action.shortcut().toString() == 'W'
    action.trigger()
    assert window.scene._mode == 'wire'
    assert window.btn_wire.isChecked()
    window.close()


def test_escape_syncs_wire_button_back_to_select():
    window = MainWindow()
    window._shared_actions['wire'].trigger()
    assert window.btn_wire.isChecked()

    window.scene.keyPressEvent(QKeyEvent(
        QEvent.Type.KeyPress, Qt.Key.Key_Escape,
        Qt.KeyboardModifier.NoModifier))

    assert window.scene._mode == 'select'
    assert not window.btn_wire.isChecked()
    assert window.btn_select.isChecked()
    window.close()


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


def test_pcb_placement_snaps_and_nudges_on_grid():
    board = PcbBoard(footprints=[PcbFootprint(
        reference='R1', component_type='R', value='1 kΩ',
        footprint_name='R_0805', x_mm=10.0, y_mm=10.0, angle=0.0,
        width_mm=2.0, height_mm=2.0)])
    editor = PcbEditorWidget(board=board)
    item = editor._footprint_items['R1']
    item.setSelected(True)

    item.setPos(10.4, 11.6)
    assert (item.footprint.x_mm, item.footprint.y_mm) == (10.0, 12.0)
    assert editor.nudge_selected(1, -1)
    assert (item.footprint.x_mm, item.footprint.y_mm) == (11.0, 11.0)
    editor._start_area(QPointF(0.4, 0.4))
    editor._update_area_preview(QPointF(3.6, 4.4))
    preview = editor._area_preview.rect()
    assert (preview.x(), preview.y(), preview.width(), preview.height()) == (
        0.0, 0.0, 4.0, 4.0)
    editor._finish_area(QPointF(3.6, 4.4))
    assert editor.board.outline == (0.0, 0.0, 4.0, 4.0)
    area = editor._area_item
    area.setSelected(True)
    area.setPos(2.4, 1.6)
    assert editor.board.outline == (2.0, 2.0, 4.0, 4.0)
    editor.close()


def test_pcb_regeneration_preserves_manual_placement():
    scene = CircuitScene()
    scene.place_component('R', QPointF(0, 0), name='R1')
    editor = PcbEditorWidget(source_scene=scene)
    item = editor._footprint_items['R1']
    item.setPos(42.0, 18.0)
    item.footprint.angle = 90.0
    item.footprint.side = 'B.Cu'
    editor._regenerate()

    restored = editor.board.footprints[0]
    assert (restored.x_mm, restored.y_mm, restored.angle, restored.side) == (
        42.0, 18.0, 90.0, 'B.Cu')
    editor.close()


def test_pcb_sidebar_replaces_results_and_restores_without_losing_state():
    window = MainWindow()
    window.show()
    window.results_text.setPlainText('Saved simulation result')
    window._open_pcb_editor()
    editor = window._active_pcb_editor()
    _APP.processEvents()
    assert window.inspector_pages.currentWidget() is editor.sidebar
    assert editor.canvas_layout.indexOf(editor.sidebar) == -1
    assert not window.results_text.isVisible()
    editor.layer_checks['F.Cu'].setChecked(False)
    editor.layer_combo.setCurrentText('B.Cu')
    editor.sidebar.setCurrentIndex(1)
    editor.display_checks['show_pad_numbers'].setChecked(False)
    window.tab_widget.setCurrentIndex(0)
    _APP.processEvents()
    assert window.results_text.isVisible()
    assert window.results_text.toPlainText() == 'Saved simulation result'
    assert editor.canvas_layout.indexOf(editor.sidebar) >= 0
    window.tab_widget.setCurrentWidget(editor)
    _APP.processEvents()
    assert window.inspector_pages.currentWidget() is editor.sidebar
    assert not editor.is_layer_visible('F.Cu')
    assert editor.active_layer == window._pcb_layer_combo.currentText() == 'B.Cu'
    assert not editor.show_pad_numbers and editor.sidebar.currentIndex() == 1
    # Reloading a document must detach the old controls, not leave a stale inspector.
    window._clear_all_sheets(create_sheet=True)
    window._open_pcb_editor()
    replacement = window._active_pcb_editor()
    assert window.inspector_pages.currentWidget() is replacement.sidebar
    assert replacement is not editor
    assert window.inspector_pages.count() == 2
    window.close()


def test_pcb_direct_inspector_edits_validate_and_undo():
    from PyQt6.QtWidgets import QDoubleSpinBox, QComboBox

    window = MainWindow()
    window._open_pcb_editor()
    editor = window._active_pcb_editor()
    def control(name):
        return next(window.prop_table.cellWidget(row, 1)
                    for row in range(window.prop_table.rowCount())
                    if window.prop_table.cellWidget(row, 1) is not None
                    and window.prop_table.cellWidget(row, 1).objectName() == name)
    item = editor._footprint_items['R1']
    item.setSelected(True)
    field = control('x_mm')
    before = item.footprint.x_mm
    field.setValue(before + 3)
    field.editingFinished.emit()
    assert editor.board.footprints[0].x_mm == before + 3
    assert editor.scene.selectedItems()
    assert editor.undo()
    assert editor.board.footprints[0].x_mm == before
    editor._footprint_items['R1'].setSelected(True)
    choice = control('footprint_name')
    choice.setCurrentText('R_0805')
    assert editor.board.footprints[0].footprint_name == 'R_0805'
    assert editor.board.footprints[0].pads[0].drill_mm == 0
    pad = editor._footprint_items['R1'].pad_items[0]
    editor.scene.clearSelection()
    pad.setSelected(True)
    drill = control('drill_mm')
    drill.setValue(1)
    drill.editingFinished.emit()
    assert pad.pad.drill_mm == 0
    assert 'Invalid pad drill' in window.statusBar().currentMessage()
    assert all(action in window._pcb_toolbar.actions() for action in editor.edit_toolbar.actions())
    window.close()


def test_pcb_package_geometry_and_display_options_preserve_model():
    import math

    scene = CircuitScene()
    scene.place_component('R', QPointF(0, 0), 'R1', 1000, 'Ω')
    scene.place_component('C', QPointF(180, 0), 'C1', 1e-6, 'F')
    editor = PcbEditorWidget(source_scene=scene)
    resistor, capacitor = (editor._footprint_items[name] for name in ('R1', 'C1'))
    assert resistor._display_value() == '1kΩ'
    assert capacitor._display_value() == '1μF'
    assert resistor.silkscreen_path.contains(QPointF(0, 1.25))
    assert not resistor.silkscreen_path.contains(QPointF(0, 2))
    angle = math.radians(22.5)
    # A true circular stroke must remain circular after pad-clearance clipping.
    assert capacitor.silkscreen_path.contains(QPointF(3.5 * math.cos(angle),
                                                     3.5 * math.sin(angle)))
    for pad in capacitor.footprint.pads:
        assert not capacitor.silkscreen_path.intersects(pad_shape(pad, False))
    editor.show()
    _APP.processEvents()
    editor.view.fitInView(resistor.sceneBoundingRect(), Qt.AspectRatioMode.KeepAspectRatio)
    snapshot = editor.board.to_dict()
    solid = editor.view.grab().toImage()
    editor.set_display_option('show_pad_numbers', False)
    assert editor.view.grab().toImage() != solid
    editor.set_display_option('outline_copper', True)
    assert editor.display_checks['outline_copper'].isChecked()
    assert editor.view.grab().toImage() != solid
    editor.set_display_option('show_values', False)
    assert editor.board.to_dict() == snapshot
    assert editor.board.routing_status() == PcbBoard.from_dict(snapshot).routing_status()
    resistor.setSelected(True)
    editor.rotate_selected()
    editor.flip_selected()
    assert resistor.silk_layer() == 'B.SilkS'
    assert resistor.footprint.silkscreen == PcbBoard.from_dict(snapshot).footprints[0].silkscreen
    editor.close()


def test_pcb_view_can_pan_after_zoom():
    editor = PcbEditorWidget(board=PcbBoard(outline=(0.0, 0.0, 100.0, 80.0)))
    editor.show()
    _APP.processEvents()
    view = editor.view
    view._zoom_at(3.0, QPointF(view.viewport().rect().center()))
    view.horizontalScrollBar().setValue(view.horizontalScrollBar().maximum())
    before = view.horizontalScrollBar().value()

    view._pan_by(QPointF(20.0, 0.0))

    assert view.horizontalScrollBar().value() < before
    editor.close()


def test_pcb_scene_expands_when_footprint_moves_outside_initial_bounds():
    board = PcbBoard(outline=(0.0, 0.0, 20.0, 20.0), footprints=[PcbFootprint(
        reference='R1', component_type='R', value='1 kΩ',
        footprint_name='R_0805', x_mm=10.0, y_mm=10.0, angle=0.0,
        width_mm=2.0, height_mm=2.0)])
    editor = PcbEditorWidget(board=board)
    editor._footprint_items['R1'].setPos(200.0, 150.0)

    rect = editor.scene.sceneRect()
    assert rect.right() > 200.0 and rect.bottom() > 150.0
    editor.close()


def test_pcb_route_snaps_corners_and_connects_same_net_pads():
    board = PcbBoard(footprints=[
        PcbFootprint(
            reference='R1', component_type='R', value='1 kΩ',
            footprint_name='R_0805', x_mm=0.0, y_mm=0.0, angle=0.0,
            width_mm=2.0, height_mm=2.0,
            pads=[PcbPad(1, 0.0, 0.0, 'N1'),
                  PcbPad(2, 0.0, 3.0, 'GND')]),
        PcbFootprint(
            reference='R2', component_type='R', value='1 kΩ',
            footprint_name='R_0805', x_mm=10.0, y_mm=0.0, angle=0.0,
            width_mm=2.0, height_mm=2.0,
            pads=[PcbPad(1, 0.0, 0.0, 'N1'),
                  PcbPad(2, 0.0, 3.0, 'GND')]),
    ])
    editor = PcbEditorWidget(board=board)
    editor.set_route_mode(True)

    editor._route_click(QPointF(0.0, 0.0))
    editor._route_click(QPointF(5.4, 3.6))
    editor._route_click(QPointF(10.0, 0.0))

    assert len(editor.board.tracks) == 1
    assert editor.board.tracks[0].net == 'N1'
    points = editor.board.tracks[0].points
    assert points[0] == (0.0, 0.0) and points[-1] == (10.0, 0.0)
    assert (5.0, 4.0) in points
    assert all(x1 == x2 or y1 == y2 or abs(x2 - x1) == abs(y2 - y1)
               for (x1, y1), (x2, y2) in zip(points, points[1:]))
    assert editor.board.routing_status()['N1']['complete']
    assert editor.route_mode
    assert editor.undo()
    assert not editor.board.tracks
    editor.close()


def test_pcb_route_places_via_and_changes_layer():
    board = PcbBoard(footprints=[
        PcbFootprint(
            reference='R1', component_type='R', value='1 kΩ',
            footprint_name='R_0805', x_mm=0.0, y_mm=0.0, angle=0.0,
            width_mm=2.0, height_mm=2.0,
            pads=[PcbPad(1, 0.0, 0.0, 'N1')]),
        PcbFootprint(
            reference='R2', component_type='R', value='1 kΩ',
            footprint_name='R_0805', x_mm=10.0, y_mm=0.0, angle=0.0,
            width_mm=2.0, height_mm=2.0,
            pads=[PcbPad(1, 0.0, 0.0, 'N1')]),
    ])
    editor = PcbEditorWidget(board=board)
    editor.set_route_mode(True)
    editor._route_click(QPointF(0.0, 0.0))
    editor._route_click(QPointF(5.0, 5.0))

    assert editor.place_via()
    assert editor.active_layer == 'B.Cu'
    assert len(editor.board.tracks) == 1
    assert len(editor.board.vias) == 1

    editor._route_click(QPointF(10.0, 0.0))
    assert len(editor.board.tracks) == 2
    assert [track.layer for track in editor.board.tracks] == ['F.Cu', 'B.Cu']
    assert editor.board.vias[0].net == 'N1'
    assert editor.route_mode
    assert editor.undo()
    assert len(editor.board.tracks) == 1
    assert editor.undo()
    assert not editor.board.tracks and not editor.board.vias
    editor.close()


def test_pcb_tracks_and_vias_are_selectable_and_deletable():
    board = PcbBoard(
        tracks=[PcbTrack('N1', points=[(0.0, 0.0), (5.0, 0.0)])],
        vias=[PcbVia(5.0, 0.0, net='N1')],
    )
    editor = PcbEditorWidget(board=board)

    track_item = next(item for item in editor.scene.items()
                      if isinstance(item, PcbTrackItem))
    track_item.setSelected(True)
    assert editor.highlight_net == 'N1'
    editor.set_track_width(0.5)
    assert board.tracks[0].width_mm == 0.5
    assert editor.delete_selected()
    assert board.tracks == []

    via_item = next(item for item in editor.scene.items()
                    if isinstance(item, PcbViaItem))
    via_item.setSelected(True)
    assert editor.highlight_net == 'N1'
    assert editor.delete_selected()
    assert board.vias == []
    editor.close()


def test_pcb_silkscreen_text_round_trips_and_can_be_deleted():
    board = PcbBoard(texts=[PcbText('KIRHO', 20.0, 10.0, size_mm=3.0)])
    restored = PcbBoard.from_dict(board.to_dict())
    assert restored.texts[0].text == 'KIRHO'
    assert restored.texts[0].size_mm == 3.0

    editor = PcbEditorWidget(board=restored)
    text_item = next(item for item in editor.scene.items()
                     if isinstance(item, PcbTextItem))
    text_item.setSelected(True)
    assert editor.delete_selected()
    assert not restored.texts
    editor.close()


def test_pcb_ratsnest_can_be_hidden_for_board_review():
    board = PcbBoard(footprints=[
        PcbFootprint(
            reference='R1', component_type='R', value='1 kΩ',
            footprint_name='R_0805', x_mm=0.0, y_mm=0.0, angle=0.0,
            width_mm=2.0, height_mm=2.0,
            pads=[PcbPad(1, 0.0, 0.0, 'N1'),
                  PcbPad(2, 0.0, 3.0, 'GND')]),
        PcbFootprint(
            reference='R2', component_type='R', value='1 kΩ',
            footprint_name='R_0805', x_mm=10.0, y_mm=0.0, angle=0.0,
            width_mm=2.0, height_mm=2.0,
            pads=[PcbPad(1, 0.0, 0.0, 'N1'),
                  PcbPad(2, 0.0, 3.0, 'GND')]),
    ])
    editor = PcbEditorWidget(board=board)
    assert editor._ratsnest
    editor.set_ratsnest_visible(False)
    assert not editor._ratsnest
    editor.set_ratsnest_visible(True)
    assert editor._ratsnest
    editor.close()


def test_pcb_layer_visibility_hides_copper_and_supports_flipping():
    board = PcbBoard(
        outline=(0.0, 0.0, 20.0, 20.0),
        footprints=[PcbFootprint(
            reference='R1', component_type='R', value='1 kΩ',
            footprint_name='R_0805', x_mm=5.0, y_mm=5.0, angle=0.0,
            width_mm=2.0, height_mm=2.0, side='F.Cu',
            pads=[PcbPad(1, 0.0, 0.0, 'N1', drill_mm=0.0,
                         pad_type='smd', layers=('F.Cu',))])],
        tracks=[PcbTrack('N1', 'F.Cu', points=[(5.0, 5.0), (8.0, 5.0)]),
                PcbTrack('N2', 'B.Cu', points=[(5.0, 6.0), (8.0, 6.0)])],
    )
    editor = PcbEditorWidget(board=board)
    front_track = next(item for item in editor.scene.items()
                       if isinstance(item, PcbTrackItem)
                       and item.track.layer == 'F.Cu')
    back_track = next(item for item in editor.scene.items()
                      if isinstance(item, PcbTrackItem)
                      and item.track.layer == 'B.Cu')
    footprint = editor._footprint_items['R1']

    editor.set_layer_visible('F.Cu', False)
    assert not front_track.isVisible()
    assert back_track.isVisible()
    assert footprint.isVisible()  # Silkscreen remains independently visible.
    editor.set_layer_visible('F.Mask', False)
    assert not footprint.pad_items[0].isVisible()
    editor.set_layer_visible('F.SilkS', False)
    assert not footprint.isVisible()

    editor.set_layer_visible('F.Cu', True)
    footprint.setSelected(True)
    assert editor.flip_selected()
    assert board.footprints[0].side == 'B.Cu'
    assert board.footprints[0].pad_layers(board.footprints[0].pads[0]) == ('B.Cu',)
    editor.close()


def test_pcb_autoroute_connects_a_clear_pair():
    board = PcbBoard(
        width_mm=20.0, height_mm=20.0, outline=(0.0, 0.0, 20.0, 20.0),
        footprints=[
            PcbFootprint(
                reference='R1', component_type='R', value='1 kΩ',
                footprint_name='R_0805', x_mm=2.0, y_mm=10.0, angle=0.0,
                width_mm=2.0, height_mm=2.0,
                pads=[PcbPad(1, 0.0, 0.0, 'N1', layers=('F.Cu', 'B.Cu'))]),
            PcbFootprint(
                reference='R2', component_type='R', value='1 kΩ',
                footprint_name='R_0805', x_mm=18.0, y_mm=10.0, angle=0.0,
                width_mm=2.0, height_mm=2.0,
                pads=[PcbPad(1, 0.0, 0.0, 'N1', layers=('F.Cu',))]),
        ],
        tracks=[PcbTrack('BLOCK', 'F.Cu', 0.5, [(10.0, 0.0), (10.0, 20.0)])],
    )
    editor = PcbEditorWidget(board=board)

    assert editor.autoroute()
    assert len(board.tracks) == 3
    assert len(board.vias) == 1
    assert not board.unrouted_connections()
    editor.close()


def test_pcb_manual_mouse_route_rejects_shorts_and_requires_via():
    from PyQt6.QtTest import QTest

    board = PcbBoard(outline=(0, 0, 20, 20), footprints=[
        PcbFootprint('R1', 'R', '', '', 3, 5, 0, 2, 2,
                     pads=[PcbPad(1, 0, 0, 'N')]),
        PcbFootprint('R2', 'R', '', '', 17, 5, 0, 2, 2,
                     pads=[PcbPad(1, 0, 0, 'N')]),
    ], tracks=[PcbTrack('BLOCK', points=[(10, 1), (10, 19)])])
    editor = PcbEditorWidget(board=board)
    editor.show()
    _APP.processEvents()
    editor.set_route_mode(True)

    def click(x, y):
        QTest.mouseClick(editor.view.viewport(), Qt.MouseButton.LeftButton,
                         pos=editor.view.mapFromScene(QPointF(x, y)))

    click(3, 5)
    assert not editor.set_active_layer('B.Cu')
    click(17, 5)
    assert len(board.tracks) == 1 and len(editor._ratsnest) == 1
    click(5, 7)
    QTest.keyClick(editor.view, Qt.Key.Key_V)
    assert editor.active_layer == 'B.Cu'
    click(17, 5)
    assert len(board.tracks) == 3 and len(board.vias) == 1
    assert not editor._ratsnest
    editor.set_route_mode(False)
    via = next(item for item in editor.scene.items() if isinstance(item, PcbViaItem))
    via.setSelected(True)
    assert editor.delete_selected()
    assert len(editor._ratsnest) == 1
    assert editor.undo() and not editor._ratsnest
    editor.close()


def test_pcb_pad_properties_flip_rotation_and_drc_are_model_edits():
    from PyQt6.QtCore import QTimer
    from PyQt6.QtTest import QTest
    from PyQt6.QtWidgets import QDialogButtonBox, QDoubleSpinBox

    footprint = PcbFootprint('R1', 'R', '1k', 'R_0805', 5, 5, 0, 2, 2,
                            pads=[PcbPad(1, 1, 0, 'N', drill_mm=0,
                                         pad_type='smd', layers=('F.Cu',))])
    board = PcbBoard(outline=(0, 0, 20, 20), footprints=[footprint])
    editor = PcbEditorWidget(board=board)
    editor.show()
    _APP.processEvents()
    selected = []
    editor.object_selected.connect(selected.append)
    pad_item = editor._footprint_items['R1'].pad_items[0]
    QTest.mouseClick(editor.view.viewport(), Qt.MouseButton.LeftButton,
                     pos=editor.view.mapFromScene(QPointF(6, 5)))
    assert selected[-1] is footprint.pads[0]
    assert editor.highlight_net == 'N'

    def accept_pad_properties():
        dialog = QApplication.activeModalWidget()
        dialog.findChild(QDoubleSpinBox, 'width_mm').setValue(1.5)
        dialog.findChild(QDialogButtonBox).button(
            QDialogButtonBox.StandardButton.Ok).click()

    QTimer.singleShot(0, accept_pad_properties)
    QTest.mouseDClick(editor.view.viewport(), Qt.MouseButton.LeftButton,
                      pos=editor.view.mapFromScene(QPointF(6, 5)))
    assert footprint.pads[0].width_mm == 1.5
    item = editor._footprint_items['R1']
    item.setSelected(True)
    assert editor.flip_selected() and editor.rotate_selected()
    assert item.mapToScene(QPointF(1, 0)) == QPointF(5, 4)
    assert footprint.pad_position(footprint.pads[0]) == (5, 4)
    assert footprint.pad_layers(footprint.pads[0]) == ('B.Cu',)
    editor.apply_properties(footprint, {'x_mm': 25})
    assert any(v.code == 'edge' for v in editor.run_drc())
    editor.select_violation(0)
    assert editor._drc_markers
    assert editor.scene.selectedItems()
    assert editor.undo()
    assert editor.board.footprints[0].x_mm == 5
    editor.close()


def test_pcb_selection_populates_properties_panel():
    window = MainWindow()
    window.show()
    _APP.processEvents()
    window._open_pcb_editor()
    _APP.processEvents()
    item = window._active_pcb_editor()._footprint_items['R1']
    item.setSelected(True)
    _APP.processEvents()

    fields = [window.prop_table.item(row, 0).text()
              for row in range(window.prop_table.rowCount())]
    values = [window.prop_table.item(row, 1).text()
              for row in range(window.prop_table.rowCount())]
    assert 'Reference' in fields
    assert 'R1' in values
    assert 'R_Axial_P10.16mm' in values
    window.close()


def test_toolbar_and_native_menu_share_the_same_action_objects():
    window = MainWindow()
    save = window._shared_actions['save']

    assert any(save in toolbar.actions()
               for toolbar in window.findChildren(QToolBar))
    assert any(save in menu.actions()
               for menu in window.findChildren(QMenu))
    window.close()


def test_print_renderer_outputs_one_page_per_open_sheet(tmp_path):
    window = MainWindow()
    window._add_sheet("Sheet 2")
    printer = QPrinter(QPrinter.PrinterMode.HighResolution)
    printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
    output = tmp_path / "sheets.pdf"
    printer.setOutputFileName(str(output))

    window._render_print_pages(
        [sheet['scene'] for sheet in window._sheets], printer)

    assert len(re.findall(rb'/Type\s*/Page\b', output.read_bytes())) == 2
    window.close()


def test_switch_key_is_captured_while_simulation_has_focus():
    window = MainWindow()
    switch = window.scene.place_component("SPDT3", QPointF(0, 0), name="S1")
    switch.switch_on1_key = "A"
    window._sim_running = True

    consumed = window.eventFilter(
        window, QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_A,
                          Qt.KeyboardModifier.NoModifier, "a"))

    assert consumed is True
    assert switch.value == -1.0
    window.close()


def test_keypad_has_matrix_pins_and_keyboard_press_connects_them():
    scene = CircuitScene()
    keypad = scene.place_component('KEYPAD4X4', QPointF(0, 0), name='KP1')
    keypad.keypad_keys[0] = 'A'

    assert len(keypad.all_pin_positions_scene()) == 8
    before = scene.extract_netlist()
    assert before['KP1__p1'] != before['KP1__p5']

    scene.keyPressEvent(QKeyEvent(
        QEvent.Type.KeyPress, Qt.Key.Key_A,
        Qt.KeyboardModifier.NoModifier, 'a'))
    assert keypad.keypad_pressed[0]
    pressed = scene.extract_netlist()
    assert pressed['KP1__p1'] == pressed['KP1__p5']

    scene.keyReleaseEvent(QKeyEvent(
        QEvent.Type.KeyRelease, Qt.Key.Key_A,
        Qt.KeyboardModifier.NoModifier, 'a'))
    assert not keypad.keypad_pressed[0]
    assert scene.extract_netlist()['KP1__p1'] != scene.extract_netlist()['KP1__p5']


def test_keypad_assignments_are_editable_and_serialized():
    scene = CircuitScene()
    keypad = scene.place_component('KEYPAD4X4', QPointF(0, 0), name='KP1')
    dialog = ComponentDialog(keypad, COLORS)
    dialog._keypad_key_edits[0].setText('Z')
    data = dialog.get_data()
    dialog.close()

    assert data['keypad_keys'][0] == 'Z'
    keypad.keypad_keys = data['keypad_keys']
    restored = CircuitScene()._instantiate_component(
        scene._serialize_component(keypad))
    assert restored.keypad_keys[0] == 'Z'


def test_keypad_double_click_pulses_only_the_button_under_the_cursor():
    class DoubleClick:
        def __init__(self, point):
            self._point = point

        def scenePos(self):
            return self._point

    scene = CircuitScene()
    keypad = scene.place_component('KEYPAD4X4', QPointF(0, 0), name='KP1')
    button_center = keypad.mapToScene(
        keypad._keypad_button_rect(0).center())

    scene.mouseDoubleClickEvent(DoubleClick(button_center))
    assert keypad.keypad_pressed[0]
    assert not any(keypad.keypad_pressed[1:])
    time.sleep(0.16)
    _APP.processEvents()
    assert not keypad.keypad_pressed[0]
