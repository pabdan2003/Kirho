"""Regresiones de las acciones básicas del editor esquemático."""
import os
import re

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QEvent, QPointF, Qt
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtPrintSupport import QPrinter
from PyQt6.QtWidgets import QApplication, QMenu, QToolBar

from main import MainWindow
from kirho.pcb import PcbBoard, PcbFootprint, PcbPad
from kirho.ui.items.wire_item import WireItem
from kirho.ui.pcb_editor import PcbEditorWidget
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
    assert editor.board.tracks[0].points == [(0.0, 0.0), (5.0, 4.0), (10.0, 0.0)]
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
