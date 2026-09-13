"""Source-only PCB acceptance case: real mouse routing, save/reopen and capture.

QT_QPA_PLATFORM=offscreen .venv/bin/python scripts/verify_pcb_editor.py --output /tmp/kirho-pcb
Omit QT_QPA_PLATFORM and add --interactive to inspect the same editable project.
"""
import argparse
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PyQt6.QtCore import QPointF, Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

import main
from kirho.pcb import PcbText, footprint_names_for_type


def verify(output, interactive=False):
    app = QApplication.instance() or QApplication([])
    app.setApplicationName('Kirho — PCB verification from source')
    app.setStyle('Fusion')
    window = main.MainWindow()
    window.resize(1440, 900)
    window._clear_circuit()
    scene = window.scene
    # A small real netlist, independent of any image/reference artwork.
    scene.place_component('R', QPointF(0, 0), 'R1', 1000, 'Ω', 'VIN', 'LED')
    scene.place_component('LED', QPointF(200, 0), 'LED1', 1e-14, 'A', 'LED', '0')
    scene.place_component('C', QPointF(0, 200), 'C1', 1e-6, 'F', 'VIN', '0')
    smd = scene.place_component('R', QPointF(200, 200), 'R2', 10000, 'Ω', 'VIN', '0')
    smd.footprint_name = 'R_0805'
    window._open_pcb_editor()
    editor = window._active_pcb_editor()
    editor._set_outline((0, 0, 56, 38))
    for reference, position in {'R1': (15, 10), 'LED1': (40, 10),
                                'C1': (15, 27), 'R2': (40, 27)}.items():
        editor._footprint_items[reference].setPos(*position)
    editor.board.texts.append(PcbText('LED / FILTER', 22, 35, size_mm=1.2))
    editor._draw_board()
    window.show()
    assert QTest.qWaitForWindowExposed(window)
    QTest.qWait(100)  # Let native window-manager resize events settle before hit testing.
    app.processEvents()
    editor.fit_board()
    editor.set_track_width(0.5)
    editor.set_route_mode(True)
    messages = []
    editor.route_status.connect(messages.append)

    def click(point):
        position = editor.view.mapFromScene(QPointF(*point))
        QTest.mouseMove(editor.view.viewport(), position)
        QTest.qWait(20)
        QTest.mouseClick(editor.view.viewport(), Qt.MouseButton.LeftButton,
                         pos=position)
        app.processEvents()

    def pad(reference, number):
        footprint = editor._footprint_items[reference].footprint
        return footprint.pad_position(next(p for p in footprint.pads if p.number == number))

    click(pad('R1', 2))
    click(pad('LED1', 1))
    assert len(editor.board.tracks) == 1, messages
    assert editor.board.routing_status()['LED']['complete']
    click(pad('R1', 1))
    click((6, 15))
    click((6, 20))
    QTest.keyClick(editor.view, Qt.Key.Key_V)
    assert editor.active_layer == 'B.Cu' and len(editor.board.vias) == 1, (
        editor.active_layer, len(editor.board.vias), messages)
    assert (editor.board.vias[0].x_mm, editor.board.vias[0].y_mm) == (6, 20)
    click(pad('C1', 1))
    editor.set_route_mode(False)
    assert len(editor.board.tracks) == 3
    assert len(editor.board.unrouted_connections()) == 3
    assert not [v for v in editor.run_drc() if v.severity == 'error']

    output.mkdir(parents=True, exist_ok=True)
    project = output / 'pcb-acceptance.csin'
    window._current_file = str(project)
    window.documents._save_circuit()
    expected = editor.board.to_dict()
    window._open_circuit(str(project))
    window._open_pcb_editor()
    editor = window._active_pcb_editor()
    assert editor.board.to_dict() == expected
    app.processEvents()
    editor.fit_board()
    editor.run_drc()

    def capture(widget, filename):
        QTest.qWait(150)
        widget.repaint()
        app.processEvents()
        assert widget.grab().save(str(output / filename))

    editor._footprint_items['R1'].setSelected(True)
    capture(window, 'pcb.png')
    editor.scene.clearSelection()
    editor.set_layer_visible('F.Cu', False)
    editor.set_active_layer('B.Cu')
    capture(editor.view.viewport(), 'pcb-back-copper.png')
    editor.set_layer_visible('F.Cu', True)
    editor.set_active_layer('F.Cu')
    editor.run_drc()
    editor.set_display_option('outline_copper', True)
    capture(editor.view.viewport(), 'pcb-copper-outlines.png')
    editor.set_display_option('outline_copper', False)
    # Check a repository example too: no decorative packages or invented nets.
    window._open_circuit(str(ROOT / 'examples' / '555-astable.csin'))
    window._open_pcb_editor()
    example = window._active_pcb_editor()
    app.processEvents()
    package = example._footprint_items['U5551']
    assert abs(package.footprint.pads[1].y_mm - package.footprint.pads[0].y_mm - 2.54) < 1e-9
    bounds = package.sceneBoundingRect()
    for item in example._footprint_items.values():
        bounds = bounds.united(item.sceneBoundingRect())
    example.view.fitInView(bounds.adjusted(-3, -3, 3, 3), Qt.AspectRatioMode.KeepAspectRatio)
    capture(example.view.viewport(), '555-footprints.png')
    example.view.fitInView(package.sceneBoundingRect().adjusted(-2, -2, 2, 2),
                           Qt.AspectRatioMode.KeepAspectRatio)
    capture(example.view.viewport(), '555-dip-detail.png')
    window._clear_all_sheets(create_sheet=True)
    for index, kind in enumerate(('BJT_NPN', 'POT', 'SPST', 'SPDT',
                                   'DPDT', 'RELAY', 'XFMR', 'BRIDGE')):
        part = window.scene.place_component(kind, QPointF((index % 4) * 280,
                                                           (index // 4) * 300))
        part.footprint_name = footprint_names_for_type(kind)[-1]
    window._open_pcb_editor()
    packages = window._active_pcb_editor()
    assert len(packages.board.footprints) == 8
    packages._set_outline((0, 0, 108, 60))
    packages._draw_board()
    app.processEvents()
    packages.fit_board()
    window._current_file = str(output / 'own-footprints.csin')
    window.documents._save_circuit()
    capture(packages.view.viewport(), 'own-footprints.png')
    window._open_circuit(str(project))
    window._open_pcb_editor()
    editor = window._active_pcb_editor()
    assert editor.board.to_dict() == expected
    app.processEvents()
    editor.fit_board()
    editor.run_drc()
    print(f'Source: {main.__file__}', flush=True)
    print(f'Project and captures: {output}', flush=True)
    print('4 footprints, 8 pads, 3 tracks, 1 via; 3 real unrouted connections.', flush=True)
    print('Manual mouse routing, via transition, DRC and save/reopen: PASS', flush=True)
    if interactive:
        return app.exec()
    window.close()
    return 0


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--interactive', action='store_true')
    args = parser.parse_args()
    output = args.output or Path(tempfile.mkdtemp(prefix='kirho-pcb-'))
    raise SystemExit(verify(output.resolve(), args.interactive))
