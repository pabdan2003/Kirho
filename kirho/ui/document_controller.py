"""Controlador de documentos e impresión de Kirho.

Mantiene fuera de MainWindow la persistencia .csin, el intercambio SPICE y la
vista previa/impresión de hojas.
"""
from __future__ import annotations

import json
import math
import os
from typing import Optional, List

from PyQt6.QtWidgets import (
    QDialog, QDialogButtonBox, QFileDialog, QMessageBox, QVBoxLayout,
)
from PyQt6.QtGui import QPainter, QPageLayout, QPageSize
from PyQt6.QtCore import (
    Qt, QPointF, QRectF, QSizeF, pyqtSignal,
)
from PyQt6.QtPrintSupport import (
    QPrintDialog, QPrintPreviewWidget, QPrinter, QPrinterInfo,
)

from kirho.spice import export_netlist, parse_netlist
from kirho.pcb import PcbBoard
from kirho.ui.items.component_item import ComponentItem
from kirho.ui.items.wire_item import WireItem
from kirho.ui.scene import (
    CircuitScene, PAPER_FORMATS, PAPER_LINE_WIDTH,
)



class ResponsivePrintPreview(QPrintPreviewWidget):
    """Notifica cambios de tamaño para conservar el ajuste de cada hoja."""

    resized = pyqtSignal()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.resized.emit()


class DocumentController:
    def __init__(self, window):
        self._window = window

    @property
    def _current_file(self):
        return getattr(self._window, '_current_file', None)

    @_current_file.setter
    def _current_file(self, value):
        self._window._current_file = value

    @property
    def _pcb_file(self):
        return getattr(self._window, '_pcb_file', None)

    @_pcb_file.setter
    def _pcb_file(self, value):
        self._window._pcb_file = value

    def __getattr__(self, name):
        return getattr(self._window, name)

    # ── Persistencia e impresión ───────────────────────────────────────
    def _serialize_sheet(self, scene: CircuitScene) -> dict:
        sheet_data = {
            'components': [],
            'wires': [],
            'paper_format': scene.paper_format,
            'paper_line_width': scene.paper_line_width,
            'paper_visible': scene.paper_visible,
            'title_block_visible': scene.title_block_visible,
            'title_block': dict(scene.title_block),
        }
        for item in scene.components:
            entry = {
                'type':  item.comp_type,
                'name':  item.name,
                'value': item.value,
                'unit':  item.unit,
                'node1': item.node1,
                'node2': item.node2,
                'node3': item.node3,
                'x':     item.pos().x(),
                'y':     item.pos().y(),
                'angle': item._angle,
                'flip_x': item._flip_x,
                'flip_y': item._flip_y,
            }
            if item.comp_type in ('VAC', 'FGEN'):
                entry['frequency'] = item.frequency
                entry['phase_deg'] = item.phase_deg
                entry['ac_mode']   = item.ac_mode
            if item.comp_type == 'FGEN':
                entry['fgen_waveform'] = item.fgen_waveform
                entry['fgen_offset']   = item.fgen_offset
                entry['fgen_duty']     = item.fgen_duty
            if item.comp_type == 'LED':
                entry['led_color'] = item.led_color
            if item.comp_type == 'Z':
                entry['z_real']  = item.z_real
                entry['z_imag']  = item.z_imag
                entry['z_mag']   = item.z_mag
                entry['z_phase'] = item.z_phase
                entry['z_mode']  = item.z_mode
            if item.comp_type == 'POT':
                entry['pot_wiper'] = item.pot_wiper
            if item.comp_type == 'XFMR':
                entry['xfmr_ratio'] = item.xfmr_ratio
                entry['xfmr_imax']  = item.xfmr_imax
            if item.comp_type == 'BRIDGE':
                entry['bridge_vf'] = item.bridge_vf
            if item.comp_type == 'RELAY':
                entry['relay_activation_voltage'] = item.relay_activation_voltage
            if item.comp_type in ComponentItem.FOUR_PIN_TYPES:
                entry['node4'] = item.node4
            if item.comp_type in ComponentItem.SIX_PIN_TYPES:
                entry['node4'] = item.node4
                entry['node5'] = item.node5
                entry['node6'] = item.node6
            if item.comp_type in ComponentItem.FIVE_PIN_TYPES:
                entry['node4']     = item.node4
                entry['node5']     = item.node5
                entry['tl082_unit'] = item.tl082_unit
            if item.comp_type in ('NET_LABEL_IN', 'NET_LABEL_OUT'):
                entry['sheet_label'] = item.sheet_label
            if item.comp_type == 'PORT':
                entry['port_name'] = item.port_name
                entry['port_dir']  = item.port_dir
            if item.comp_type == 'SUBCKT':
                entry['subckt_name']   = item.subckt_name
                entry['ic_label']      = item.ic_label
                entry['ic_body_color'] = item.ic_body_color
                entry['ic_text_color'] = item.ic_text_color
                entry['ic_pins']       = [dict(p) for p in (item.ic_pins or [])]
            if item.comp_type == 'CLK':
                entry['clk_running'] = item.clk_running
            if item.comp_type == 'MULTIMETER':
                entry['meter_quantity'] = item.meter_quantity
                entry['meter_coupling'] = item.meter_coupling
            if item.comp_type == 'OSC':
                for attr in (
                    'osc_time_div', 'osc_v_div_a', 'osc_v_div_b',
                    'osc_pos_a', 'osc_pos_b', 'osc_trig_level',
                    'osc_trig_source', 'osc_trig_edge', 'osc_trig_mode',
                    'osc_hw_config',
                ):
                    if hasattr(item, attr):
                        value = getattr(item, attr)
                        entry[attr] = dict(value) if isinstance(value, dict) else value
            if item.comp_type in ComponentItem.DIGITAL_TYPES:
                neg = list(getattr(item, 'dig_input_neg', []) or [])
                if any(neg):
                    entry['dig_input_neg'] = neg
                # Configuración digital (necesaria para subcircuitos y para
                # restaurar fielmente puertas/FF/contadores/puentes).
                entry['dig_inputs']      = item.dig_inputs
                entry['dig_bits']        = item.dig_bits
                entry['dig_bits_adc']    = item.dig_bits_adc
                entry['dig_vref']        = item.dig_vref
                entry['dig_clk']         = item.dig_clk
                entry['dig_tpd_ns']      = item.dig_tpd_ns
                entry['dig_analog_node'] = item.dig_analog_node
                entry['dig_input_nodes'] = list(
                    getattr(item, 'dig_input_nodes', []) or [])
            if item.comp_type == 'IC555':
                entry['timer_nodes'] = list(getattr(item, 'timer_nodes', []) or [])
            if item.comp_type in ('SPST', 'SPDT', 'DPDT'):
                entry['switch_key'] = item.switch_key
            if item.comp_type == 'SPDT3':
                entry['switch_on1_key'] = item.switch_on1_key
                entry['switch_off_key'] = item.switch_off_key
                entry['switch_on2_key'] = item.switch_on2_key
            sheet_data['components'].append(entry)

        for wire in scene.wires:
            line = wire.line()
            sheet_data['wires'].append({
                'x1': line.x1(), 'y1': line.y1(),
                'x2': line.x2(), 'y2': line.y2(),
            })
        return sheet_data

    def _load_sheet_data(self, scene: CircuitScene, sheet_data: dict):
        scene.set_paper_format(sheet_data.get('paper_format', 'A4'))
        scene.set_paper_line_width(
            sheet_data.get('paper_line_width', PAPER_LINE_WIDTH))
        scene.set_paper_visible(sheet_data.get('paper_visible', False))
        scene.set_title_block(sheet_data.get('title_block', {}))
        scene.set_title_block_visible(sheet_data.get('title_block_visible', False))
        # Los NE555 guardados antes de la cuadrícula actual tenían pines a
        # ±50/±30 px. Conservamos sus cables al abrirlos y los llevamos a los
        # nuevos pines, todos múltiplos de GRID_SIZE.
        legacy_555_pins = []
        for c in sheet_data.get('components', []):
            item = scene.place_component(
                c['type'], QPointF(c['x'], c['y']),
                name=c['name'], value=c['value'],
                unit=c.get('unit', ''),
                node1=c.get('node1', ''),
                node2=c.get('node2', ''),
                node3=c.get('node3', '')
            )
            angle = c.get('angle', 0)
            flip_x = bool(c.get('flip_x', False))
            flip_y = bool(c.get('flip_y', False))
            if angle or flip_x or flip_y:
                item._angle = angle
                item._flip_x = flip_x
                item._flip_y = flip_y
                item._apply_transform()
            if c['type'] == 'IC555':
                old_local = ((-50, 30), (-50, 10), (-50, -10), (-50, -30),
                             (50, -30), (50, -10), (50, 10), (50, 30))
                old_pins = [item.mapToScene(QPointF(x, y)) for x, y in old_local]
                legacy_555_pins.extend(zip(old_pins, item.all_pin_positions_scene()))
            if c['type'] == 'VAC':
                item.frequency = c.get('frequency', 60.0)
                item.phase_deg = c.get('phase_deg', 0.0)
                item.ac_mode   = c.get('ac_mode', 'rms')
            if c['type'] == 'FGEN':
                item.frequency     = c.get('frequency', item.frequency)
                item.phase_deg     = c.get('phase_deg', item.phase_deg)
                item.ac_mode       = c.get('ac_mode', item.ac_mode)
                item.fgen_waveform = c.get('fgen_waveform', item.fgen_waveform)
                item.fgen_offset   = c.get('fgen_offset', item.fgen_offset)
                item.fgen_duty     = c.get('fgen_duty', item.fgen_duty)
            if c['type'] == 'LED':
                item.led_color = c.get('led_color', 'red')
            if c['type'] == 'Z':
                item.z_real  = c.get('z_real',  100.0)
                item.z_imag  = c.get('z_imag',  0.0)
                item.z_mag   = c.get('z_mag',   100.0)
                item.z_phase = c.get('z_phase', 0.0)
                item.z_mode  = c.get('z_mode',  'rect')
            if c['type'] == 'POT' and 'pot_wiper' in c:
                item.pot_wiper = max(0.0, min(1.0, float(c['pot_wiper'])))
            if c['type'] == 'XFMR':
                item.xfmr_ratio = c.get('xfmr_ratio', 2.0)
                item.xfmr_imax  = c.get('xfmr_imax',  1.0)
            if c['type'] == 'BRIDGE':
                item.bridge_vf = c.get('bridge_vf', 0.7)
            if c['type'] == 'RELAY':
                item.relay_activation_voltage = max(
                    0.0, float(c.get(
                        'relay_activation_voltage', item.relay_activation_voltage)))
            if c['type'] in ComponentItem.FOUR_PIN_TYPES and 'node4' in c:
                item.node4 = c['node4']
            if c['type'] in ComponentItem.SIX_PIN_TYPES:
                item.node4 = c.get('node4', '')
                item.node5 = c.get('node5', '')
                item.node6 = c.get('node6', '')
            if c['type'] in ComponentItem.FIVE_PIN_TYPES:
                if 'node4' in c: item.node4 = c['node4']
                if 'node5' in c: item.node5 = c['node5']
                item.tl082_unit = c.get('tl082_unit', 'A')
            if c['type'] in ('NET_LABEL_IN', 'NET_LABEL_OUT'):
                item.sheet_label = c.get('sheet_label', item.name)
            if c['type'] == 'PORT':
                item.port_name = c.get('port_name', item.name)
                item.port_dir  = c.get('port_dir', 'in')
            if c['type'] == 'SUBCKT':
                item.subckt_name   = c.get('subckt_name', '')
                item.ic_label      = c.get('ic_label', '')
                item.ic_body_color = c.get('ic_body_color', '')
                item.ic_text_color = c.get('ic_text_color', '')
                item.ic_pins       = [dict(p) for p in c.get('ic_pins', [])]
                if not item.ic_pins:
                    scene._init_subckt_appearance(item)
                item.prepareGeometryChange()
                item.update()
            if c['type'] == 'CLK':
                item.clk_running = bool(c.get('clk_running', False))
            if c['type'] == 'MULTIMETER':
                item.meter_quantity = c.get('meter_quantity', 'V')
                item.meter_coupling = c.get('meter_coupling', 'DC')
                item.meter_reading_unit_hint = {
                    'V': 'V', 'A': 'A', 'OHM': 'Ω'
                }.get(item.meter_quantity, 'V')
            if c['type'] == 'OSC':
                for attr in (
                    'osc_time_div', 'osc_v_div_a', 'osc_v_div_b',
                    'osc_pos_a', 'osc_pos_b', 'osc_trig_level',
                    'osc_trig_source', 'osc_trig_edge', 'osc_trig_mode',
                    'osc_hw_config',
                ):
                    if attr in c:
                        value = c[attr]
                        setattr(item, attr, dict(value) if isinstance(value, dict) else value)
            if c['type'] in ComponentItem.DIGITAL_TYPES:
                item.dig_inputs = int(c.get('dig_inputs', item.dig_inputs))
                if c['type'] == 'NOT':
                    item.dig_inputs = 1
                if c['type'] == 'COUNTER':
                    item.prepareGeometryChange()
                item.dig_bits = int(c.get('dig_bits', item.dig_bits))
                item.dig_bits_adc = int(c.get('dig_bits_adc', item.dig_bits_adc))
                item.dig_vref = float(c.get('dig_vref', item.dig_vref))
                item.dig_clk = c.get('dig_clk', item.dig_clk)
                item.dig_tpd_ns = float(c.get('dig_tpd_ns', item.dig_tpd_ns))
                item.dig_analog_node = c.get('dig_analog_node', item.dig_analog_node)
                item.dig_input_nodes = list(c.get('dig_input_nodes', []) or [])
                item.dig_input_neg = list(c.get('dig_input_neg', []) or [])
            if c['type'] == 'IC555':
                item.timer_nodes = (list(c.get('timer_nodes', [])) + [''] * 8)[:8]
            if c['type'] in ('SPST', 'SPDT', 'DPDT'):
                item.switch_key = c.get('switch_key', '')
            if c['type'] == 'SPDT3':
                item.switch_on1_key = c.get('switch_on1_key', '')
                item.switch_off_key = c.get('switch_off_key', '')
                item.switch_on2_key = c.get('switch_on2_key', '')

        for w in sheet_data.get('wires', []):
            def migrate_555_pin(point):
                for old, new in legacy_555_pins:
                    if abs(point.x() - old.x()) < 12 and abs(point.y() - old.y()) < 12:
                        return QPointF(new)
                return point

            wire = WireItem(migrate_555_pin(QPointF(w['x1'], w['y1'])),
                            migrate_555_pin(QPointF(w['x2'], w['y2'])))
            scene.addItem(wire)
            scene.wires.append(wire)

    # ── Guardar (.csin) ──────────────────────────
    def _save_circuit(self):
        path = self._current_file
        if not path:
            path, _ = QFileDialog.getSaveFileName(
                self._window, self.tr("Save Circuit"), "",
                self.tr("Kirho (*.csin);;All Files (*)")
            )
        if not path:
            return
        if not path.endswith('.csin'):
            path += '.csin'

        sheets = []
        for sheet in self._schematic_sheets():
            sd = self._serialize_sheet(sheet['scene'])
            sd['name'] = sheet['name']
            sheets.append(sd)

        data = {'version': '2.1', 'format': 'kirho-schematic', 'sheets': sheets}
        pcb_tab = next((sheet for sheet in self._sheets
                        if sheet.get('kind') == 'pcb'), None)
        pcb_path = self._pcb_file
        if pcb_path is None and pcb_tab is not None:
            pcb_path = os.path.splitext(path)[0] + '.kpcb'
            self._pcb_file = os.path.abspath(pcb_path)
        if pcb_path:
            data['pcb_file'] = os.path.relpath(
                pcb_path, os.path.dirname(os.path.abspath(path)) or '.')
        if pcb_path and pcb_tab is not None:
            self._write_pcb_file(pcb_path, pcb_tab['board'])
        elif pcb_path and self._window._legacy_pcb_data is not None:
            legacy_board = PcbBoard.from_dict(self._window._legacy_pcb_data)
            if legacy_board is not None and not os.path.exists(pcb_path):
                self._write_pcb_file(pcb_path, legacy_board)
                self._window._legacy_pcb_data = None

        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

        self._current_file = path
        self.setWindowTitle(f"Kirho — {os.path.basename(path)}")
        self.statusBar().showMessage(self.tr("Saved: {path}").format(path=path))

    def _default_pcb_path(self, circuit_path=None):
        circuit_path = circuit_path or self._current_file
        if not circuit_path:
            return None
        return os.path.splitext(circuit_path)[0] + '.kpcb'

    def _load_pcb_board(self, path):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except (OSError, ValueError) as exc:
            QMessageBox.critical(
                self._window, self.tr("Error"),
                self.tr("Could not open PCB file:\n{error}").format(error=exc))
            return None
        board = PcbBoard.from_dict(data.get('board', data)
                                   if isinstance(data, dict) else None)
        if board is None:
            QMessageBox.critical(
                self._window, self.tr("Error"),
                self.tr("The PCB file does not contain a valid board."))
        return board

    def _write_pcb_file(self, path, board):
        data = {
            'version': '1.0',
            'format': 'kirho-pcb',
            'source_circuit': os.path.basename(self._current_file)
            if self._current_file else None,
            'board': board.to_dict(),
        }
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def _save_pcb(self, path=None):
        pcb_tab = next((sheet for sheet in self._sheets
                        if sheet.get('kind') == 'pcb'), None)
        if pcb_tab is None:
            return
        path = path or self._pcb_file or self._default_pcb_path()
        if not path:
            path, _ = QFileDialog.getSaveFileName(
                self._window, self.tr("Save PCB"), "",
                self.tr("Kirho PCB (*.kpcb);;All Files (*)"))
        if not path:
            return
        if not path.lower().endswith('.kpcb'):
            path += '.kpcb'

        self._write_pcb_file(path, pcb_tab['board'])
        self._pcb_file = os.path.abspath(path)
        self.statusBar().showMessage(self.tr("PCB saved: {path}").format(path=path))

    def _save_pcb_as(self):
        path, _ = QFileDialog.getSaveFileName(
            self._window, self.tr("Save PCB As"), "",
            self.tr("Kirho PCB (*.kpcb);;All Files (*)"))
        if path:
            self._save_pcb(path)

    # ── Guardar como (.csin) ─────────────────────
    def _save_circuit_as(self):
        path, _ = QFileDialog.getSaveFileName(
            self._window, self.tr("Save Circuit As"), "",
            self.tr("Kirho (*.csin);;All Files (*)")
        )
        if not path:
            return
        if not path.endswith('.csin'):
            path += '.csin'
        self._current_file = path
        self._save_circuit()

    # ── Abrir (.csin) ────────────────────────────
    def _open_circuit(self, path=None):
        if isinstance(path, str) and path.lower().endswith('.kpcb'):
            return self._open_pcb_file(path)
        if not isinstance(path, str) or not path:
            path, _ = QFileDialog.getOpenFileName(
                self._window, self.tr("Open Circuit"), "",
                self.tr("Kirho files (*.csin *.kpcb);;Kirho schematic (*.csin);;Kirho PCB (*.kpcb);;All Files (*)")
            )
        if not path:
            return
        if path.lower().endswith('.kpcb'):
            return self._open_pcb_file(path)

        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except Exception as e:
            QMessageBox.critical(self._window, self.tr("Error"), self.tr("Could not open file:\n{error}").format(error=e))
            return

        # Compatibilidad con formato v1 (una sola hoja)
        if 'sheets' not in data:
            data = {'version': '2.0', 'sheets': [{
                'name': self.tr('Sheet 1'),
                'components': data.get('components', []),
                'wires': data.get('wires', []),
            }]}

        self._clear_all_sheets()
        self._sheets.clear()
        self.tab_widget.clear()

        for sd in data['sheets']:
            name = sd.get('name', self.tr('Sheet {number}').format(number=len(self._sheets) + 1))
            self._add_sheet(name=name)
            scene = self._sheets[-1]['scene']
            self._load_sheet_data(scene, sd)

        if self.scene.paper_visible:
            self._fit_paper_in_view()

        pcb_ref = data.get('pcb_file')
        if not isinstance(pcb_ref, str) or not pcb_ref:
            pcb_ref = os.path.splitext(os.path.basename(path))[0] + '.kpcb'
        self._pcb_file = os.path.abspath(
            pcb_ref if os.path.isabs(pcb_ref)
            else os.path.join(os.path.dirname(os.path.abspath(path)), pcb_ref))
        self._window._legacy_pcb_data = data.get('pcb')

        self._current_file = path
        self.setWindowTitle(f"Kirho — {os.path.basename(path)}")
        self.statusBar().showMessage(self.tr("Opened: {path}").format(path=path))

    def _open_pcb_file(self, path=None):
        if not isinstance(path, str) or not path:
            path, _ = QFileDialog.getOpenFileName(
                self._window, self.tr("Open PCB"), "",
                self.tr("Kirho PCB (*.kpcb);;All Files (*)"))
        if not path:
            return
        board = self._load_pcb_board(path)
        if board is None:
            return
        self._clear_all_sheets(create_sheet=False)
        self._current_file = None
        self._pcb_file = os.path.abspath(path)
        self._window._legacy_pcb_data = None
        self._open_pcb_editor(board, source_scene=None)
        self.setWindowTitle(f"Kirho — {os.path.basename(path)}")
        self.statusBar().showMessage(self.tr("Opened PCB: {path}").format(path=path))

    # ── Importar netlist SPICE ────────────────────────────────────────────
    def _import_spice(self):
        if self.scene is None:
            return
        path, _ = QFileDialog.getOpenFileName(
            self._window, self.tr("Import SPICE Netlist"), "",
            self.tr("SPICE Netlist (*.cir *.net *.sp);;All Files (*)"))
        if not path:
            return
        try:
            with open(path, 'r', encoding='utf-8') as file:
                result = parse_netlist(file.read())
        except OSError as exc:
            QMessageBox.critical(self._window, self.tr("Error"), str(exc))
            return
        if not result.elements:
            QMessageBox.warning(self._window, self.tr("Import SPICE Netlist"),
                                self.tr("No supported SPICE components were found."))
            return
        if self.scene.components and QMessageBox.question(
                self._window, self.tr("Import SPICE Netlist"),
                self.tr("Replace the active sheet with the imported netlist?"),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No) \
                != QMessageBox.StandardButton.Yes:
            return

        self._clear_circuit()
        columns = max(1, math.ceil(math.sqrt(len(result.elements))))
        for index, element in enumerate(result.elements):
            x = (index % columns) * 140
            y = (index // columns) * 100
            item = self.scene.place_component(
                element.kind, QPointF(x, y), name=element.name,
                value=element.value, node1=element.node1, node2=element.node2)
            if item is not None and element.kind == 'VAC':
                item.phase_deg = element.phase_deg

        self._current_file = None
        self.setWindowTitle(f"Kirho — {os.path.basename(path)}")
        detail = self.tr(
            "Imported {count} component(s). SPICE node names are preserved in properties; "
            "wire them visually if you want a conventional schematic.").format(count=len(result.elements))
        if result.warnings:
            detail += "\n\n" + "\n".join(result.warnings[:8])
        QMessageBox.information(self._window, self.tr("Import SPICE Netlist"), detail)

    # ── Exportar netlist SPICE (.net) ────────────
    def _export_spice(self):
        if self.scene is None:
            return
        path, _ = QFileDialog.getSaveFileName(
            self._window, self.tr("Export SPICE Netlist"), "",
            self.tr("SPICE Netlist (*.cir *.net *.sp);;All Files (*)")
        )
        if not path:
            return
        if not os.path.splitext(path)[1]:
            path += '.cir'
        text, warnings = export_netlist(
            self.scene.components, self.scene.extract_netlist(),
            f"Kirho — {os.path.basename(path)}")

        with open(path, 'w', encoding='utf-8') as f:
            f.write(text)

        self.statusBar().showMessage(f"Netlist exportado: {path}")
        detail = f"Netlist SPICE guardado en:\n{path}\n\nCompatible con LTspice y ngspice."
        if warnings:
            detail += "\n\nOmitted components:\n" + "\n".join(warnings)
        QMessageBox.information(
            self, "Exportado",
            detail
        )

    def _print_sheet(self):
        scenes = [sheet['scene'] for sheet in self._schematic_sheets()]
        if not scenes:
            return

        scene = scenes[0]
        label, width_mm, height_mm = PAPER_FORMATS[scene.paper_format]
        orientation = (
            QPageLayout.Orientation.Landscape
            if scene.paper_rect().width() >= scene.paper_rect().height()
            else QPageLayout.Orientation.Portrait
        )

        printer = QPrinter(QPrinter.PrinterMode.HighResolution)
        printer.setPageSize(QPageSize(
            QSizeF(width_mm, height_mm),
            QPageSize.Unit.Millimeter,
            label,
        ))
        printer.setPageOrientation(orientation)
        color_logo = any(
            sheet['scene'].title_block.get('logo_mode') == 'color'
            and sheet['scene'].title_block.get('logo_data')
            for sheet in self._schematic_sheets()
        )
        printer.setColorMode(
            QPrinter.ColorMode.Color if color_logo
            else QPrinter.ColorMode.GrayScale)
        default_printer = QPrinterInfo.defaultPrinterName()
        if default_printer:
            printer.setPrinterName(default_printer)

        preview = QDialog(self._window)
        preview.setWindowTitle(self.tr("Print Preview"))
        preview.resize(900, 700)
        preview.setMinimumSize(720, 560)
        preview_layout = QVBoxLayout(preview)
        preview_widget = ResponsivePrintPreview(printer, preview)
        preview_widget.setZoomMode(QPrintPreviewWidget.ZoomMode.FitInView)
        preview_widget.paintRequested.connect(
            lambda target_printer: self._render_print_pages(
                scenes,
                target_printer,
                monochrome=True))
        preview_widget.resized.connect(
            lambda: preview_widget.setZoomMode(
                QPrintPreviewWidget.ZoomMode.FitInView))
        preview_layout.addWidget(preview_widget, 1)

        buttons = QDialogButtonBox(preview)
        buttons.addButton(
            self.tr("Cancel"), QDialogButtonBox.ButtonRole.RejectRole)
        buttons.addButton(
            self.tr("Print"), QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.accepted.connect(preview.accept)
        buttons.rejected.connect(preview.reject)
        preview_layout.addWidget(buttons)

        if preview.exec() != QDialog.DialogCode.Accepted:
            return

        print_dialog = QPrintDialog(printer, self._window)
        if print_dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self._render_print_pages(
            scenes,
            printer,
            monochrome=True)

    def _render_print_page(self, scene: CircuitScene, printer: QPrinter):
        self._render_print_pages(
            [scene], printer, monochrome=True)

    def _render_print_pages(self, scenes: List[CircuitScene], printer: QPrinter,
                            monochrome: bool = True):
        if not scenes:
            return
        painter = QPainter()
        if not painter.begin(printer):
            self.statusBar().showMessage(self.tr("Could not start printing"))
            return

        try:
            for index, scene in enumerate(scenes):
                old_paper_visible = scene.paper_visible
                old_title_block_visible = scene.title_block_visible
                old_print_mode = scene.print_mode
                old_print_monochrome = scene.print_monochrome
                has_title_block_data = any(
                    str(value).strip() for value in scene.title_block.values())
                try:
                    scene.set_paper_visible(True)
                    scene.set_title_block_visible(
                        old_title_block_visible or has_title_block_data)
                    scene.set_print_mode(True, monochrome=monochrome)
                    page_rect = printer.pageLayout().paintRectPixels(
                        printer.resolution())
                    source_rect = scene.paper_rect().united(
                        scene.itemsBoundingRect())
                    scene.render(
                        painter,
                        QRectF(page_rect),
                        source_rect,
                        Qt.AspectRatioMode.KeepAspectRatio,
                    )
                finally:
                    scene.set_paper_visible(old_paper_visible)
                    scene.set_title_block_visible(old_title_block_visible)
                    scene.set_print_mode(old_print_mode, old_print_monochrome)
                if index < len(scenes) - 1:
                    printer.newPage()
        finally:
            painter.end()
