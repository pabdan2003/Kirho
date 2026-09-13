"""Regresiones de persistencia del formato .csin."""
import os
import json

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from main import MainWindow
from kirho.ui.scene import CircuitScene
from kirho.pcb import PcbBoard, PcbSilk, PcbText, PcbTrack, PcbVia


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


def test_load_sheet_restores_paper_settings():
    scene = CircuitScene()

    MainWindow._load_sheet_data(None, scene, {
        "paper_format": "LETTER",
        "paper_visible": True,
        "title_block_visible": True,
        "title_block": {
            "title": "Fuente 10 V",
            "project": "Prueba",
            "author": "Pablo",
        },
        "components": [],
        "wires": [],
    })

    assert scene.paper_format == "LETTER"
    assert scene.paper_visible
    assert scene.title_block_visible
    assert scene.title_block["title"] == "Fuente 10 V"
    assert scene.title_block["author"] == "Pablo"
    assert scene.paper_rect().width() > scene.paper_rect().height()


def test_complete_pcb_survives_csin_only_copy_and_regeneration(tmp_path):
    window = MainWindow()
    try:
        window._open_pcb_editor()
        editor = window._active_pcb_editor()
        editor.board.tracks = [PcbTrack('B', points=[(12, 12), (20, 20)])]
        editor.board.vias = [PcbVia(20, 20, 'B')]
        editor.board.texts = [PcbText('Assembly', 20, 30, 'B.SilkS')]
        editor.board.layers[0].color = '#aa3344'
        editor.set_layer_visible('F.Mask', False)
        editor.board.footprints[0].pads[0].width_mm = 2.3
        custom_silk = PcbSilk('polyline', ((-2, -3), (2, -3)), 0.2)
        editor.board.footprints[0].silkscreen.append(custom_silk)
        editor._regenerate()
        assert editor.board.tracks and editor.board.vias and editor.board.texts
        assert editor.board.footprints[0].pads[0].width_mm == 2.3
        assert editor.board.footprints[0].silkscreen[-1] == custom_silk
        window._current_file = str(tmp_path / 'original.csin')
        window.documents._save_circuit()
        saved = json.loads((tmp_path / 'original.csin').read_text())
        expected = editor.board.to_dict()
        assert saved['pcb'] == expected
        assert PcbBoard.from_dict(saved['pcb']).to_dict() == expected
        assert json.loads((tmp_path / 'original.kpcb').read_text())['board'] == expected
        # A copied .csin remains self contained even without its companion.
        copy_dir = tmp_path / 'copy'
        copy_dir.mkdir()
        (copy_dir / 'project.csin').write_text(json.dumps(saved))
        window._open_circuit(str(copy_dir / 'project.csin'))
        window._open_pcb_editor()
        assert window._active_pcb_editor().board.to_dict() == expected
        # Save from the PCB tab refreshes both representations.
        window._active_pcb_editor().board.texts[0].text = 'Updated'
        window._save_circuit()
        assert json.loads((copy_dir / 'project.csin').read_text())['pcb']['texts'][0]['text'] == 'Updated'
    finally:
        window.close()
