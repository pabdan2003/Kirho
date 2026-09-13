"""
Kirho — Simulador de circuitos open source
GUI principal con canvas drag-and-drop, PyQt6
"""

import sys
import os
import numpy as np
from pathlib import Path
from typing import Optional, List, Dict, Tuple

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QGraphicsView, QTableWidgetItem, QDialog,
    QMessageBox, QInputDialog, QFileDialog,
)
from PyQt6.QtGui import (
    QPainter, QBrush, QColor, QKeySequence, QIcon
)
from PyQt6.QtCore import (
    QEvent, Qt, QPointF, QTimer,
)
from PyQt6.QtPrintSupport import (
    QPrinter,
)

from kirho.circuit_analyzer import (
    DEFAULT_STANDARD,
)
from kirho.ui.component_metadata import (
    COMPONENT_NODE_LABELS,
    DEFAULT_NODE_LABELS,
    DIGITAL_BRIDGE_TYPES,
    DIGITAL_FLIPFLOP_TYPES,
    DIGITAL_GATE_TYPES,
    FIVE_PIN_NODE_LABELS,
    FOUR_PIN_NODE_LABELS,
    SIX_PIN_NODE_LABELS,
    KEYPAD_BUTTON_LABELS,
)
from kirho.ui.dialogs.component_dialog import ComponentDialog
from kirho.ui.dialogs.component_picker_dialog import ComponentPickerDialog
from kirho.ui.dialogs.power_triangle_dialog import PowerTriangleDialog
from kirho.ui.dialogs.resistor_calc_dialog import ResistorCalcDialog
from kirho.ui.dialogs.settings_dialog import SettingsDialog
from kirho.ui.dialogs.title_block_dialog import TitleBlockDialog
from kirho.i18n import load_translator


# ══════════════════════════════════════════════════════════════
# CONSTANTES DE ESTILO Y RECURSOS COMPARTIDOS
# ══════════════════════════════════════════════════════════════
# Reexportados desde ui.style para mantener compatibilidad con el resto
# del código de main.py (y para que el import de este módulo dispare la
# carga del tema inicial).
from kirho.ui import style as _style
from kirho.ui.style import (
    GRID_SIZE, COMP_W, COMP_H, PIN_RADIUS,
    COLORS, THEME_MANAGER, apply_theme_to_colors,
    _qfont, theme_revision,
    _INITIAL_THEME_ID,
)


# ══════════════════════════════════════════════════════════════
# ÍTEMS GRÁFICOS DEL CANVAS (extraídos)
# ══════════════════════════════════════════════════════════════
from kirho.ui.items.component_item import ComponentItem
from kirho.ui.items.wire_item import WireItem

        
# ══════════════════════════════════════════════════════════════
# ESCENA DEL CIRCUITO (extraída)
# ══════════════════════════════════════════════════════════════
from kirho.ui.scene import (
    CircuitScene, PAPER_FORMATS,
    build_engine_components_for_item,
)
from kirho.ui.simulation_controller import SimulationController
from kirho.ui.document_controller import DocumentController, ResponsivePrintPreview
from kirho.ui.main_window_ui import MainWindowUI
from kirho.pcb import (
    PcbBoard, PcbFootprint, PcbPad, PcbText, PcbTrack, PcbVia,
    footprint_names_for_type,
)
from kirho.ui.pcb_editor import PcbEditorWidget
from kirho.external_libraries import ExternalLibraryManager


class CircuitView(QGraphicsView):
    """Vista del circuito con rueda y trackpad nativos."""

    _MIN_ZOOM = 0.2
    _MAX_ZOOM = 5.0

    def _zoom_at(self, factor, position):
        current = self.transform().m11()
        factor = max(self._MIN_ZOOM / current, min(factor, self._MAX_ZOOM / current))
        if factor == 1:
            return

        scene_pos = self.mapToScene(position.toPoint())
        self.scale(factor, factor)
        moved_pos = self.mapFromScene(scene_pos)
        delta = moved_pos - position.toPoint()
        self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() + delta.x())
        self.verticalScrollBar().setValue(self.verticalScrollBar().value() + delta.y())

    def wheelEvent(self, event):
        modifiers = Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.MetaModifier
        if event.pixelDelta().isNull() or event.modifiers() & modifiers:
            delta = event.angleDelta().y()
            if delta:
                self._zoom_at(1.15 ** (delta / 120), event.position())
                event.accept()
                return
        super().wheelEvent(event)

    def viewportEvent(self, event):
        if (event.type() == QEvent.Type.NativeGesture
                and event.gestureType() == Qt.NativeGestureType.ZoomNativeGesture):
            self._zoom_at(1 + event.value(), event.position())
            event.accept()
            return True
        return super().viewportEvent(event)

# ══════════════════════════════════════════════════════════════
# VENTANA PRINCIPAL
# ══════════════════════════════════════════════════════════════
class MainWindow(MainWindowUI, QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(self.tr("Kirho — Circuit Simulator"))
        self.resize(1280, 800)
        self._active_theme_id = _INITIAL_THEME_ID

        # La coordinación y el estado de simulación viven fuera de la ventana.
        self.simulation = SimulationController(self)
        self.documents = DocumentController(self)
        self.external_libraries = ExternalLibraryManager()
        self.external_libraries.activate()
        self._external_schematic_components = (
            self.external_libraries.list_schematic_components())
        self._external_components = {
            component["type"]: component
            for component in self._external_schematic_components
        }
        self._external_runtimes = {}
        for component in self._external_schematic_components:
            backend_name = component.get("backend")
            if not backend_name or backend_name in self._external_runtimes:
                continue
            runtime = self.external_libraries.create_runtime(backend_name)
            if runtime is not None:
                self._external_runtimes[backend_name] = runtime
                self.simulation.bind_external_runtime(runtime)

        # ── Reloj global para componentes CLK ──────────────────────────────
        # Cada CLK con clk_running=True conmuta su valor cada medio período
        # de _clk_freq_hz. La frecuencia es ajustable desde Herramientas.
        self._clk_freq_hz: float = 1.0
        self._clk_timer = QTimer(self)
        self._clk_timer.timeout.connect(self._tick_clk)
        self._update_clk_timer_interval()

        self._build_ui()
        self._apply_style()
        self._load_demo_circuit()
        self._configure_external_runtimes()
        self._install_global_shortcuts()
    # ── Atajos globales (funcionan sin importar qué widget tenga foco) ──
    def _install_global_shortcuts(self):
        """Atajos a nivel de aplicación.

        Ctrl+C/X/V/Z se registran como `QShortcut` (funcionan bien con el
        parser de strings de Qt). La ROTACIÓN (Ctrl++ / Ctrl+-) se maneja
        con un `eventFilter` instalado sobre la `QApplication` porque las
        combinaciones con `+` y `-` confunden al parser de QKeySequence
        en algunos backends y dependiendo del layout del teclado pueden
        no disparar el QShortcut. El event filter ve los eventos al
        nivel más bajo y los enruta a la escena activa.
        """
        from PyQt6.QtGui import QShortcut, QKeySequence

        def _msg(text):
            self.statusBar().showMessage(text)

        def _bind_string(seq, fn):
            sh = QShortcut(QKeySequence(seq), self)
            sh.setContext(Qt.ShortcutContext.ApplicationShortcut)
            sh.activated.connect(fn)

        def do_copy():
            pcb = self._active_pcb_editor()
            if pcb is not None:
                if pcb.copy_selected():
                    _msg(self.tr("Selection copied (Ctrl+C)"))
                return
            sc = self.scene
            if sc is not None and sc.copy_selected():
                _msg(self.tr("Selection copied (Ctrl+C)"))

        def do_cut():
            pcb = self._active_pcb_editor()
            if pcb is not None:
                if pcb.cut_selected():
                    _msg(self.tr("Selection cut (Ctrl+X)"))
                return
            sc = self.scene
            if sc is not None and sc.cut_selected():
                _msg(self.tr("Selection cut (Ctrl+X)"))

        def do_paste():
            pcb = self._active_pcb_editor()
            if pcb is not None:
                _msg(self.tr("Pasted (Ctrl+V)")) if pcb.paste() else _msg(
                    self.tr("Clipboard is empty"))
                return
            sc = self.scene
            if sc is None:
                return
            if sc.paste():
                _msg(self.tr("Pasted (Ctrl+V)"))
            else:
                _msg(self.tr("Clipboard is empty"))

        def do_undo():
            pcb = self._active_pcb_editor()
            if pcb is not None:
                _msg(self.tr("Action undone (Ctrl+Z)")) if pcb.undo() else _msg(
                    self.tr("Nothing to undo"))
                return
            sc = self.scene
            if sc is None:
                return
            if sc.undo():
                _msg(self.tr("Action undone (Ctrl+Z)"))
            else:
                _msg(self.tr("Nothing to undo"))

        def do_redo():
            pcb = self._active_pcb_editor()
            if pcb is not None:
                _msg(self.tr("Action redone")) if pcb.redo() else _msg(
                    self.tr("Nothing to redo"))
                return
            sc = self.scene
            if sc is not None and sc.redo():
                _msg(self.tr("Action redone"))
            else:
                _msg(self.tr("Nothing to redo"))

        def do_duplicate():
            pcb = self._active_pcb_editor()
            if pcb is not None:
                if pcb.duplicate_selected():
                    _msg(self.tr("Selection duplicated"))
                return
            sc = self.scene
            if sc is not None and sc.copy_selected() and sc.paste():
                _msg(self.tr("Selection duplicated"))

        _bind_string("Ctrl+C", do_copy)
        _bind_string("Ctrl+X", do_cut)
        _bind_string("Ctrl+V", do_paste)
        _bind_string("Ctrl+Z", do_undo)
        _bind_string("Ctrl+Y", do_redo)
        _bind_string("Ctrl+Shift+Z", do_redo)
        _bind_string("Meta+Shift+Z", do_redo)
        _bind_string("Ctrl+D", do_duplicate)

        # Rotación: event filter a nivel de QApplication.
        QApplication.instance().installEventFilter(self)

    def eventFilter(self, obj, event):
        """Filtro unificado:
          • Captura Ctrl+- y Ctrl++ ANTES de que cualquier widget procese el
            KeyPress (instalado a nivel de QApplication).
          • Muestra el menú de herramientas al pasar el cursor sobre
            ``_tools_button``.
        """
        from PyQt6.QtCore import QEvent
        et = event.type()
        if et == QEvent.Type.FileOpen:
            path = event.file()
            # Cocoa also emits FileOpen for source scripts / command arguments.
            if path and Path(path).suffix.lower() in ('.csin', '.kpcb'):
                self._open_circuit(path)
                return True
        elif et == QEvent.Type.KeyPress:
            mods = event.modifiers()
            sc = self.scene
            if (self._sim_running and sc is not None
                    and not mods & Qt.KeyboardModifier.ControlModifier):
                if (sc.handle_switch_key(event) or
                        sc.handle_keypad_key(event, True)):
                    return True
            if mods & Qt.KeyboardModifier.ControlModifier:
                k = event.key()
                if k in (Qt.Key.Key_Minus, Qt.Key.Key_Underscore):
                    pcb = self._active_pcb_editor()
                    rotated = (pcb is not None and pcb.rotate_selected(delta=-90))
                    if ((sc is not None and sc.rotate_selected(delta=-90))
                            or rotated):
                        self.statusBar().showMessage(
                            "Rotado 90° a la izquierda (Ctrl+-)")
                    return True
                if k in (Qt.Key.Key_Plus, Qt.Key.Key_Equal):
                    sc = self.scene
                    pcb = self._active_pcb_editor()
                    rotated = (pcb is not None and pcb.rotate_selected(delta=90))
                    if ((sc is not None and sc.rotate_selected(delta=90))
                            or rotated):
                        self.statusBar().showMessage(
                            "Rotado 90° a la derecha (Ctrl++)")
                    return True
        elif et == QEvent.Type.KeyRelease:
            mods = event.modifiers()
            sc = self.scene
            if (self._sim_running and sc is not None
                    and not mods & Qt.KeyboardModifier.ControlModifier
                    and sc.handle_keypad_key(event, False)):
                return True
        elif et == QEvent.Type.Enter:
            if getattr(self, '_tools_button', None) is obj:
                obj.showMenu()
        return super().eventFilter(obj, event)

    def keyPressEvent(self, event):
        """Segunda red de seguridad: si por algún motivo el event filter
        no recibe el evento (envío sintético desde QTest, etc.), aún
        capturamos las rotaciones aquí."""
        sc = self.scene
        if (sc is not None and event.key() == Qt.Key.Key_Space
                and not event.modifiers() & Qt.KeyboardModifier.ControlModifier):
            # La vista consume Space para navegación; reenviarlo al canvas
            # permite usarlo como tecla de un interruptor.
            sc.keyPressEvent(event)
            if event.isAccepted():
                return
        mod = event.modifiers()
        if mod & Qt.KeyboardModifier.ControlModifier:
            sc = self.scene
            pcb = self._active_pcb_editor()
            k = event.key()
            if k in (Qt.Key.Key_Minus, Qt.Key.Key_Underscore):
                rotated = (sc is not None and sc.rotate_selected(delta=-90))
                rotated = pcb.rotate_selected(delta=-90) if pcb is not None else rotated
                if rotated:
                    self.statusBar().showMessage(
                        "Rotado 90° a la izquierda (Ctrl+-)")
                event.accept()
                return
            if k in (Qt.Key.Key_Plus, Qt.Key.Key_Equal):
                rotated = (sc is not None and sc.rotate_selected(delta=90))
                rotated = pcb.rotate_selected(delta=90) if pcb is not None else rotated
                if rotated:
                    self.statusBar().showMessage(
                        "Rotado 90° a la derecha (Ctrl++)")
                event.accept()
                return
        super().keyPressEvent(event)

    # ── CLK (oscilador global) ────────────────────────────────────────────
    def _update_clk_timer_interval(self):
        """Configura el intervalo del timer según _clk_freq_hz.
        Half-period en ms: 1/(2f) * 1000.  Mínimo 10 ms para no saturar la GUI.
        """
        f = max(0.001, float(self._clk_freq_hz))
        half_period_ms = max(10, int(1000.0 / (2.0 * f)))
        self._clk_timer.setInterval(half_period_ms)

    def _tick_clk(self):
        """Conmuta los CLK que están en modo automático."""
        any_active = False
        for sheet in self._schematic_sheets():
            for it in sheet['scene'].components:
                if it.comp_type == 'CLK' and it.clk_running:
                    it.value = 0.0 if it.value else 1.0
                    it.update()
                    any_active = True
        if not any_active:
            self._clk_timer.stop()
            return
        # Re-disparar simulación en vivo si está activa
        if self._sim_running:
            self._run_simulation_dc(silent=True)

    def _toggle_clk_running(self):
        """Atajo Ctrl+K: invierte el estado oscilando de los CLK seleccionados.
        Si no hay ninguno seleccionado, conmuta todos los CLK del canvas.
        """
        if self.scene is None:
            return
        sel = [it for it in self.scene.selectedItems()
               if isinstance(it, ComponentItem) and it.comp_type == 'CLK']
        targets = sel if sel else [
            it for it in self.scene.components if it.comp_type == 'CLK']
        if not targets:
            self.statusBar().showMessage(
                self.tr("There are no CLK components on the active sheet."))
            return
        # Si al menos uno está corriendo, los detenemos todos. Si ninguno
        # corre, los iniciamos todos.
        any_running = any(it.clk_running for it in targets)
        for it in targets:
            it.clk_running = not any_running
            it.update()
        # Iniciar/parar el timer global
        any_running_now = any(
            it.clk_running for sheet in self._schematic_sheets()
            for it in sheet['scene'].components if it.comp_type == 'CLK')
        if any_running_now:
            self._update_clk_timer_interval()
            self._clk_timer.start()
            self.statusBar().showMessage(
                self.tr("CLK enabled at {frequency:g} Hz ({count} component{plural})").format(
                    frequency=self._clk_freq_hz, count=len(targets),
                    plural="s" if len(targets) != 1 else ""))
        else:
            self._clk_timer.stop()
            self.statusBar().showMessage(self.tr("CLK stopped."))

    def _set_clk_frequency(self):
        """Diálogo Herramientas → Frecuencia CLK."""
        from PyQt6.QtWidgets import QInputDialog
        f, ok = QInputDialog.getDouble(
            self, self.tr("CLK Frequency"),
            self.tr("CLK component oscillation frequency (Hz):"),
            self._clk_freq_hz, 0.01, 100000.0, 3)
        if not ok:
            return
        self._clk_freq_hz = f
        self._update_clk_timer_interval()
        self.statusBar().showMessage(self.tr("CLK Frequency = {frequency:g} Hz").format(frequency=f))

    def _open_circuit_analyzer(self):
        """Abre el analizador de circuitos digitales preservando el estado previo."""
        # Import perezoso: el diálogo importa símbolos de main.py (ComponentItem,
        # WireItem, COLORS, etc.), por eso se carga en el momento de uso para
        # evitar una importación circular en tiempo de carga del módulo.
        from kirho.ui.dialogs.circuit_analyzer_dialog import CircuitAnalyzerDialog
        state = getattr(self, '_analyzer_state', None)
        dlg = CircuitAnalyzerDialog(parent=self, initial_state=state)
        dlg.exec()

    def _open_resistor_calculator(self):
        """Abre la calculadora de código de colores de resistencias."""
        dlg = ResistorCalcDialog(colors=COLORS, parent=self)
        dlg.exec()

    def _open_bode_analyzer(self):
        """Abre el analizador de Bode (barrido AC + plots de magnitud y fase).
        No-modal: se puede dejar abierto mientras editas el circuito y
        recalcular al gusto."""
        if self.scene is None:
            return
        from kirho.ui.dialogs.bode_dialog import BodeDialog
        dlg = BodeDialog(self.scene, COLORS, parent=self)
        dlg.show()

    def _active_tab(self) -> Optional[Dict]:
        idx = self.tab_widget.currentIndex()
        if 0 <= idx < len(self._sheets):
            return self._sheets[idx]
        return None

    def _schematic_sheets(self) -> List[Dict]:
        return [sheet for sheet in self._sheets
                if sheet.get('kind', 'schematic') == 'schematic']

    def _is_pcb_tab(self) -> bool:
        tab = self._active_tab()
        return bool(tab and tab.get('kind') == 'pcb')

    def _active_pcb_editor(self):
        tab = self._active_tab()
        return tab['widget'] if tab and tab.get('kind') == 'pcb' else None

    @property
    def scene(self) -> Optional['CircuitScene']:
        tab = self._active_tab()
        if tab is None or tab.get('kind', 'schematic') != 'schematic':
            return None
        return tab['scene']

    @property
    def view(self) -> QGraphicsView:
        tab = self._active_tab()
        if tab is not None:
            return tab['view']
        idx = self.tab_widget.currentIndex()
        return self._sheets[0]['view'] if idx < 0 and self._sheets else None

    def _create_scene_view(self) -> Tuple[CircuitScene, QGraphicsView]:
        scene = CircuitScene()
        scene.external_components = dict(
            getattr(self, '_external_components', {}))
        scene.component_selected.connect(self._on_component_selected)
        scene.status_message.connect(self.statusBar().showMessage)
        scene.mode_changed.connect(
            lambda mode, source=scene: self._on_scene_mode_changed(source, mode))
        scene.logic_state_toggled.connect(self._on_logic_state_toggled)
        scene.instrument_changed.connect(self._on_instrument_changed)
        scene.title_block_edit_requested.connect(self._edit_title_block)

        view = CircuitView(scene)
        view.setRenderHint(QPainter.RenderHint.Antialiasing)
        view.setDragMode(QGraphicsView.DragMode.RubberBandDrag)
        view.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        view.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        return scene, view

    def _configure_external_runtimes(self):
        """Entrega a cada runtime externo el mapa genérico GPIO→net."""
        gpio_nets = {}
        for sheet in self._schematic_sheets():
            gpio_nets.update(sheet['scene'].external_gpio_net_map())
        for runtime in getattr(self, '_external_runtimes', {}).values():
            configure = getattr(runtime, 'configure', None)
            if callable(configure):
                try:
                    configure(gpio_nets)
                except Exception:
                    continue

    def _on_scene_mode_changed(self, source, mode: str):
        if self.scene is not source:
            return
        self.btn_select.blockSignals(True)
        self.btn_wire.blockSignals(True)
        self.btn_select.setChecked(mode == 'select')
        self.btn_wire.setChecked(mode == 'wire')
        self.btn_select.blockSignals(False)
        self.btn_wire.blockSignals(False)
        self.view.setDragMode(
            QGraphicsView.DragMode.RubberBandDrag
            if mode == 'select' else QGraphicsView.DragMode.NoDrag)

    def _add_sheet(self, name: str = ''):
        if not name:
            idx = len(self._sheets) + 1
            name = self.tr("Sheet {number}").format(number=idx)
        scene, view = self._create_scene_view()
        sheet = {'kind': 'schematic', 'scene': scene, 'view': view,
                 'widget': view, 'name': name}
        self._sheets.append(sheet)
        tab_idx = self.tab_widget.addTab(view, name)
        self.tab_widget.setCurrentIndex(tab_idx)

    def _open_pcb_editor(self, board: PcbBoard | None = None,
                         source_scene=None):
        """Abre la vista PCB como una pestaña del proyecto actual."""
        for index, sheet in enumerate(self._sheets):
            if sheet.get('kind') == 'pcb':
                self.tab_widget.setCurrentIndex(index)
                return
        if source_scene is None:
            source_scene = self.scene
        if board is None and source_scene is not None:
            pcb_path = self._pcb_file or self.documents._default_pcb_path()
            if pcb_path and os.path.isfile(pcb_path):
                board = self.documents._load_pcb_board(pcb_path)
                if board is None:
                    return
            elif self._legacy_pcb_data is not None:
                board = PcbBoard.from_dict(self._legacy_pcb_data)
            if board is not None and pcb_path:
                self._pcb_file = os.path.abspath(pcb_path)
        if source_scene is None and board is None:
            return

        pcb_widget = PcbEditorWidget(source_scene, board, self.tab_widget)
        sheet = {
            'kind': 'pcb',
            'scene': None,
            'view': pcb_widget.view,
            'widget': pcb_widget,
            'name': self.tr('PCB'),
            'board': pcb_widget.board,
            'source_scene': source_scene,
        }
        pcb_widget.board_changed.connect(
            lambda updated, entry=sheet: entry.__setitem__('board', updated))
        pcb_widget.board_changed.connect(lambda updated: self._update_tab_mode())
        pcb_widget.object_selected.connect(self._on_pcb_object_selected)
        pcb_widget.route_mode_changed.connect(
            lambda enabled: self._shared_actions['pcb_route'].setChecked(enabled))
        pcb_widget.layer_changed.connect(self._on_pcb_layer_changed)
        pcb_widget.route_status.connect(self.statusBar().showMessage)
        self._sheets.append(sheet)
        tab_idx = self.tab_widget.addTab(pcb_widget, sheet['name'])
        self.tab_widget.setCurrentIndex(tab_idx)

    def _regenerate_pcb(self):
        tab = self._active_tab()
        if not tab or tab.get('kind') != 'pcb':
            return
        tab['widget']._regenerate()

    def _set_pcb_unit(self, unit: str):
        tab = self._active_tab()
        if tab and tab.get('kind') == 'pcb':
            tab['widget'].set_unit(unit)

    def _set_pcb_layer(self, layer: str):
        pcb = self._active_pcb_editor()
        if pcb is not None:
            pcb.set_active_layer(layer)

    def _set_pcb_layer_visibility(self, layer: str, visible: bool):
        pcb = self._active_pcb_editor()
        if pcb is not None:
            pcb.set_layer_visible(layer, visible)

    def _on_pcb_layer_changed(self, layer: str):
        combo = getattr(self, '_pcb_layer_combo', None)
        if combo is None:
            return
        combo.blockSignals(True)
        combo.setCurrentText(layer)
        combo.blockSignals(False)

    def _set_pcb_track_width(self, width_mm):
        pcb = self._active_pcb_editor()
        if pcb is not None and width_mm is not None:
            pcb.set_track_width(float(width_mm))

    def _set_pcb_ratsnest(self, visible: bool):
        pcb = self._active_pcb_editor()
        if pcb is not None:
            pcb.set_ratsnest_visible(visible)

    def _toggle_pcb_route(self, enabled: bool):
        pcb = self._active_pcb_editor()
        if pcb is not None:
            pcb.set_route_mode(enabled)
            return
        action = self._shared_actions.get('pcb_route')
        if action is not None:
            action.blockSignals(True)
            action.setChecked(False)
            action.blockSignals(False)

    def _set_pcb_area_mode(self):
        tab = self._active_tab()
        if tab and tab.get('kind') == 'pcb':
            tab['widget'].set_area_mode(not tab['widget'].area_mode)

    def _close_sheet(self, index: int):
        if not 0 <= index < len(self._sheets):
            return
        entry = self._sheets[index]
        if entry.get('kind', 'schematic') == 'schematic' and len(self._schematic_sheets()) <= 1:
            self.statusBar().showMessage(self.tr("The last sheet cannot be closed"))
            return
        reply = QMessageBox.question(
            self, self.tr("Close Sheet"),
            self.tr("Close \"{name}\"? Its components will be lost.").format(name=entry['name']),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._sheets.pop(index)
            self.tab_widget.removeTab(index)
            self._on_sheet_changed(self.tab_widget.currentIndex())

    def _on_sheet_changed(self, index: int):
        if 0 <= index < len(self._sheets):
            self._configure_external_runtimes()
            self._update_tab_mode()
            if self._sheets[index].get('kind') == 'pcb':
                self._on_component_selected(None)
                self.statusBar().showMessage(self.tr("Active tab: PCB"))
                return
            if hasattr(self, '_snap_action'):
                self._snap_action.setChecked(self.scene.snap_enabled)
            self._update_paper_visibility_action()
            self._update_title_block_visibility_action()
            self._update_paper_actions()
            self.statusBar().showMessage(self.tr("Active sheet: {name}").format(name=self._sheets[index]['name']))

    def _on_tab_moved(self, from_index: int, to_index: int):
        entry = self._sheets.pop(from_index)
        self._sheets.insert(to_index, entry)

    def _set_paper_format(self, paper_format: str):
        if self.scene is None:
            return
        if self.scene.set_paper_format(paper_format):
            self._update_paper_actions()
            if self.scene.paper_visible:
                self._fit_paper_in_view()
            label = PAPER_FORMATS[paper_format][0]
            self.statusBar().showMessage(
                self.tr("Paper size: {format}").format(format=label))

    def _set_paper_line_width(self):
        if self.scene is None:
            return
        width, ok = QInputDialog.getDouble(
            self,
            self.tr("Paper line width"),
            self.tr("Width:"),
            self.scene.paper_line_width,
            0.5,
            10.0,
            1,
        )
        if ok:
            self.scene.set_paper_line_width(width)

    def _update_paper_actions(self):
        if not hasattr(self, '_paper_actions') or self.scene is None:
            return
        current = self.scene.paper_format
        for paper_id, action in self._paper_actions.items():
            action.setChecked(paper_id == current)

    def _toggle_paper_frame(self, visible: bool):
        if self.scene is None:
            return
        self.scene.set_paper_visible(visible)
        if visible:
            self._fit_paper_in_view()
        self._update_paper_visibility_action()

    def _toggle_title_block(self, visible: bool):
        if self.scene is None:
            return
        if visible and not self.scene.paper_visible:
            self.scene.set_paper_visible(True)
            self._update_paper_visibility_action()
        self.scene.set_title_block_visible(visible)
        if visible:
            self._fit_paper_in_view()
        self._update_title_block_visibility_action()

    def _edit_title_block(self):
        if self.scene is None:
            return
        dialog = TitleBlockDialog(self.scene.title_block, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.scene.set_title_block(dialog.values())
            self._toggle_title_block(True)

    def _update_title_block_visibility_action(self):
        if hasattr(self, '_title_block_visibility_action') and self.scene is not None:
            self._title_block_visibility_action.setChecked(
                self.scene.title_block_visible)

    def _fit_paper_in_view(self):
        if self.scene is None:
            return
        self.view.fitInView(
            self.scene.paper_rect(), Qt.AspectRatioMode.KeepAspectRatio)

    def _update_paper_visibility_action(self):
        if hasattr(self, '_paper_visibility_action') and self.scene is not None:
            self._paper_visibility_action.setChecked(self.scene.paper_visible)

    def _rename_sheet(self, index: int):
        if 0 <= index < len(self._sheets):
            old_name = self._sheets[index]['name']
            new_name, ok = QInputDialog.getText(
                self, self.tr("Rename Sheet"), self.tr("New name:"), text=old_name)
            if ok and new_name.strip():
                self._sheets[index]['name'] = new_name.strip()
                self.tab_widget.setTabText(index, new_name.strip())

    def _show_about(self):
        QMessageBox.about(
            self,
            self.tr("About Kirho"),
            self.tr("Kirho — Circuit Simulator\nVersion 0.1.0"),
        )

    def _undo_active_sheet(self):
        pcb = self._active_pcb_editor()
        if pcb is not None:
            if not pcb.undo():
                self.statusBar().showMessage(self.tr("Nothing to undo"))
            return
        if self.scene is None or not self.scene.undo():
            self.statusBar().showMessage(self.tr("Nothing to undo"))

    def _redo_active_sheet(self):
        pcb = self._active_pcb_editor()
        if pcb is not None:
            if not pcb.redo():
                self.statusBar().showMessage(self.tr("Nothing to redo"))
            return
        if self.scene is None or not self.scene.redo():
            self.statusBar().showMessage(self.tr("Nothing to redo"))

    def _copy_active_selection(self):
        pcb = self._active_pcb_editor()
        if pcb is not None:
            return pcb.copy_selected()
        return self.scene is not None and self.scene.copy_selected()

    def _cut_active_selection(self):
        pcb = self._active_pcb_editor()
        if pcb is not None:
            return pcb.cut_selected()
        return self.scene is not None and self.scene.cut_selected()

    def _paste_active_selection(self):
        pcb = self._active_pcb_editor()
        if pcb is not None:
            return pcb.paste()
        return self.scene is not None and self.scene.paste()

    def _duplicate_active_selection(self):
        pcb = self._active_pcb_editor()
        if pcb is not None:
            return pcb.duplicate_selected()
        if self.scene is not None and self.scene.copy_selected():
            return self.scene.paste()
        return False

    def _align_selection(self, edge: str):
        if not self.scene.align_selected(edge):
            self.statusBar().showMessage(self.tr("Select at least two components to align"))

    def _distribute_selection(self, axis: str):
        if self.scene.distribute_selected(axis):
            self.statusBar().showMessage(
                self.tr("Distributed horizontally") if axis == 'x'
                else self.tr("Distributed vertically"))
        else:
            self.statusBar().showMessage(self.tr("Select at least three components to distribute"))

    def _toggle_snap(self, enabled: bool):
        pcb = self._active_pcb_editor()
        if pcb is not None:
            pcb.set_snap_enabled(enabled)
        elif self.scene is not None:
            self.scene.snap_enabled = enabled
        self.statusBar().showMessage(
            self.tr("Snap to grid enabled") if enabled
            else self.tr("Snap to grid disabled"))

    def _run_erc(self):
        warnings = self.scene.electrical_rule_warnings()
        if warnings:
            QMessageBox.warning(self, self.tr("Circuit Check (ERC)"), "\n\n".join(warnings))
        else:
            QMessageBox.information(self, self.tr("Circuit Check (ERC)"),
                                    self.tr("No basic electrical issues were found."))

    # ── Configuración / Tema ──────────────────────────────────────────────
    def _open_settings_dialog(self):
        """Abre el diálogo de configuración."""
        dlg = SettingsDialog(THEME_MANAGER, COLORS,
                             parent=self,
                             current_theme_id=self._active_theme_id,
                             on_theme_change=self._apply_theme_change,
                             current_language=THEME_MANAGER.load_language(),
                             on_language_change=self._apply_language_change,
                             library_manager=self.external_libraries)
        dlg.exec()

    def _apply_theme_change(self, theme_id: str):
        """Callback que el SettingsDialog invoca al elegir un tema."""
        applied = apply_theme_to_colors(theme_id)
        self._active_theme_id = applied
        THEME_MANAGER.save_selection(applied)
        self._refresh_theme_in_ui()
        meta = THEME_MANAGER.get_theme_meta(applied)
        if meta:
            self.statusBar().showMessage(self.tr("Theme applied: {name}").format(name=meta['name']), 3000)

    def _apply_language_change(self, language: str):
        """Save the language selection; Qt reloads translated widgets at startup."""
        if THEME_MANAGER.save_language(language):
            QMessageBox.information(
                self, self.tr("Language"),
                self.tr("Language saved. Restart Kirho to apply it."))

    def _refresh_theme_in_ui(self):
        """Re-aplica stylesheet y fuerza redibujo del canvas tras cambiar tema."""
        self._apply_style()
        for sheet in self._schematic_sheets():
            sheet['scene'].setBackgroundBrush(QBrush(QColor(COLORS['bg'])))
            sheet['scene'].update()
            sheet['view'].viewport().update()

    def _show_picker(self, category_name: str, items: List[tuple]):
        """Abre el diálogo de selección y, si se acepta, activa el modo colocación."""
        dialog = ComponentPickerDialog(category_name, items, ComponentItem, COLORS, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            ctype = dialog.get_selected_type()
            if ctype:
                self._set_place_mode(ctype)

    def _show_subcircuit_picker(self):
        """Lista los subcircuitos de la biblioteca y activa colocación."""
        from kirho.subcircuit_manager import SUBCIRCUIT_MANAGER
        SUBCIRCUIT_MANAGER.refresh()
        subs = SUBCIRCUIT_MANAGER.list_subcircuits()
        if not subs:
            QMessageBox.information(
                self, self.tr("Subcircuits"),
                self.tr("There are no subcircuits in the library.\n\n"
                        "Create one: add Net Labels (input/output) to a sheet and "
                        "click «＋ Create Subckt»."))
            return
        items = [(f"SUBCKT:{s['name']}", s['name'],
                  self.tr("⊞ {count} pins").format(count=len(s.get('ports', [])))) for s in subs]
        dialog = ComponentPickerDialog(self.tr("Subcircuits"), items,
                                       ComponentItem, COLORS, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            ctype = dialog.get_selected_type()
            if ctype:
                self._set_place_mode(ctype)

    def _create_subcircuit_from_sheet(self):
        """Empaqueta la hoja activa como un subcircuito .sub.json.

        Los pines del IC se derivan de los NET LABELS de la hoja: cada
        `sheet_label` único se convierte en un pin (NET_LABEL_IN → entrada,
        NET_LABEL_OUT → salida, ambos → bidireccional).
        """
        from kirho.subcircuit_manager import SUBCIRCUIT_MANAGER
        scene = self.scene
        _LBL = ('NET_LABEL_IN', 'NET_LABEL_OUT')
        label_items = [c for c in scene.components if c.comp_type in _LBL
                       and (c.sheet_label or '').strip()]
        if not label_items:
            QMessageBox.warning(
                self, self.tr("Create Subcircuit"),
                self.tr("This sheet has no Net Labels.\n\n"
                        "Add one «Net Label Input/Output» for each pin you want to "
                        "expose; its name will become the IC pin name."))
            return
        nested = [c for c in scene.components if c.comp_type == 'SUBCKT']
        body = [c for c in scene.components if c.comp_type not in _LBL
                and c.comp_type not in ('PORT',)]
        if not body:
            QMessageBox.warning(self, self.tr("Create Subcircuit"),
                                self.tr("This sheet has no components to package."))
            return

        name, ok = QInputDialog.getText(self, self.tr("Create Subcircuit"),
                                        self.tr("Subcircuit name:"))
        if not ok or not name.strip():
            return
        name = name.strip()
        nested_used = {c.subckt_name for c in nested}
        if name in nested_used:
            QMessageBox.warning(self, self.tr("Create Subcircuit"),
                                self.tr("A subcircuit cannot contain itself "
                                        "(there is an instance of «{name}» on this sheet).").format(name=name))
            return
        if SUBCIRCUIT_MANAGER.exists(name):
            r = QMessageBox.question(
                self, self.tr("Create Subcircuit"),
                self.tr("«{name}» already exists. Overwrite it?").format(name=name))
            if r != QMessageBox.StandardButton.Yes:
                return

        pin_node = scene.extract_netlist()
        full = self._serialize_sheet(scene)
        # Los net labels y PORT no se empaquetan como componentes: su
        # conectividad ya quedó resuelta en internal_nets (extract_netlist).
        comp_entries = [e for e in full['components']
                        if e['type'] not in _LBL and e['type'] != 'PORT']

        # ── Agrupar net labels por sheet_label → un pin único por etiqueta ──
        # Dirección: solo IN → 'in', solo OUT → 'out', ambos → 'bidir'.
        # Orden estable: por posición del primer label de cada etiqueta
        # (arriba→abajo, izq→der).
        groups: dict = {}
        for it in label_items:
            lbl = it.sheet_label.strip()
            g = groups.setdefault(lbl, {'in': False, 'out': False,
                                        'y': it.pos().y(), 'x': it.pos().x(),
                                        'item': it})
            if it.comp_type == 'NET_LABEL_IN':
                g['in'] = True
            else:
                g['out'] = True
            if (round(it.pos().y()), round(it.pos().x())) < (round(g['y']),
                                                             round(g['x'])):
                g['y'], g['x'], g['item'] = it.pos().y(), it.pos().x(), it

        ordered = sorted(groups.items(),
                         key=lambda kv: (round(kv[1]['y']), round(kv[1]['x'])))
        ports_def, port_nets, appearance_pins = [], {}, []
        for lbl, g in ordered:
            if g['in'] and g['out']:
                d = 'bidir'
            elif g['out']:
                d = 'out'
            else:
                d = 'in'
            ports_def.append({'name': lbl, 'dir': d})
            # El net canónico de un grupo etiquetado en extract_netlist es la
            # propia etiqueta; usamos el pin del label como respaldo robusto.
            ref = g['item']
            port_nets[lbl] = pin_node.get(f"{ref.name}__p1", lbl)
            side = 'left' if d == 'in' else ('right' if d == 'out' else 'left')
            appearance_pins.append({'name': lbl, 'side': side})

        definition = {
            'name': name,
            'ports': ports_def,
            'components': comp_entries,
            'wires': full['wires'],
            'port_nets': port_nets,
            'internal_nets': pin_node,
            'appearance': {
                'label': name, 'body_color': '', 'text_color': '',
                'pins': appearance_pins,
            },
        }
        path = SUBCIRCUIT_MANAGER.save(definition, overwrite=True)
        if path:
            QMessageBox.information(
                self, self.tr("Create Subcircuit"),
                self.tr("Subcircuit «{name}» saved to:\n{path}\n\n"
                        "Available in «⊞ Subcircuits».").format(name=name, path=path))
        else:
            QMessageBox.critical(self, self.tr("Create Subcircuit"),
                                 self.tr("The subcircuit could not be saved."))

    def _set_place_mode(self, comp_type: str):
        self.scene.set_mode(f'place_{comp_type}')
        self.btn_select.setChecked(False)
        self.btn_wire.setChecked(False)
        self.view.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.statusBar().showMessage(self.tr("Click the canvas to place: {component}").format(component=comp_type))

    def _set_wire_mode(self):
        self.scene.set_mode('wire')
        self.btn_select.setChecked(False)
        self.btn_wire.setChecked(True)
        self.view.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.statusBar().showMessage(self.tr("Wire: click to start, click to finish, ESC to cancel"))

    def _set_select_mode(self):
        self.scene.set_mode('select')
        self.btn_select.setChecked(True)
        self.btn_wire.setChecked(False)
        self.view.setDragMode(QGraphicsView.DragMode.RubberBandDrag)
        self.statusBar().showMessage(self.tr("Selection mode"))

    def _on_sim_mode_changed(self, mode: str):
        pass  # modo automático — no se usa

    # ── Fachada de compatibilidad para la simulación ─────────────────────
    @property
    def solver(self):
        return self.simulation.solver

    @solver.setter
    def solver(self, value):
        self.simulation.solver = value

    @property
    def _sim_running(self):
        return self.simulation._sim_running

    @_sim_running.setter
    def _sim_running(self, value):
        self.simulation._sim_running = value

    @property
    def _sim_mode(self):
        return self.simulation._sim_mode

    @_sim_mode.setter
    def _sim_mode(self, value):
        self.simulation._sim_mode = value

    @property
    def _sim_all_comps(self):
        return self.simulation._sim_all_comps

    @_sim_all_comps.setter
    def _sim_all_comps(self, value):
        self.simulation._sim_all_comps = value

    @property
    def _sim_pin_node(self):
        return self.simulation._sim_pin_node

    @_sim_pin_node.setter
    def _sim_pin_node(self, value):
        self.simulation._sim_pin_node = value

    @property
    def _live_state(self):
        return self.simulation._live_state

    @_live_state.setter
    def _live_state(self, value):
        self.simulation._live_state = value

    @property
    def _live_components(self):
        return self.simulation._live_components

    @_live_components.setter
    def _live_components(self, value):
        self.simulation._live_components = value

    @property
    def _live_pin_node(self):
        return self.simulation._live_pin_node

    @_live_pin_node.setter
    def _live_pin_node(self, value):
        self.simulation._live_pin_node = value

    @property
    def _live_freq(self):
        return self.simulation._live_freq

    @_live_freq.setter
    def _live_freq(self, value):
        self.simulation._live_freq = value

    @property
    def _live_tick_count(self):
        return self.simulation._live_tick_count

    @_live_tick_count.setter
    def _live_tick_count(self, value):
        self.simulation._live_tick_count = value

    @property
    def _live_phasor_summary(self):
        return self.simulation._live_phasor_summary

    @_live_phasor_summary.setter
    def _live_phasor_summary(self, value):
        self.simulation._live_phasor_summary = value

    @property
    def _last_ac_result(self):
        return getattr(self.simulation, '_last_ac_result', None)

    @_last_ac_result.setter
    def _last_ac_result(self, value):
        self.simulation._last_ac_result = value

    @staticmethod
    def _is_digital_indicator_circuit(items) -> bool:
        return SimulationController._is_digital_indicator_circuit(items)

    @staticmethod
    def _estimate_led_current(vd, color: str = 'red'):
        return SimulationController._estimate_led_current(vd, color)

    @staticmethod
    def _light_threshold(item) -> float:
        return SimulationController._light_threshold(item)

    @classmethod
    def _light_is_on(cls, item, voltage: float, reference: float = 0.0) -> bool:
        return SimulationController._light_is_on(item, voltage, reference)

    @staticmethod
    def _display_voltage(item, voltage, reference: float = 0.0):
        return SimulationController._display_voltage(item, voltage, reference)

    @staticmethod
    def _voltage_drop_array(voltages, n1: str, n2: str):
        return SimulationController._voltage_drop_array(voltages, n1, n2)

    def _toggle_simulation(self, checked: bool):
        return self.simulation._toggle_simulation(checked)

    def _stop_simulation(self):
        return self.simulation._stop_simulation()

    def _tick_simulation(self):
        return self.simulation._tick_simulation()

    def _run_simulation(self):
        return self.simulation._run_simulation()

    def _run_simulation_auto(self, flags=None, pin_node=None):
        return self.simulation._run_simulation_auto(flags, pin_node)

    def _run_simulation_dc(self, silent: bool = False):
        return self.simulation._run_simulation_dc(silent)

    def _run_simulation_ac(self):
        return self.simulation._run_simulation_ac()

    def _start_live_transient(self, flags, pin_node):
        return self.simulation._start_live_transient(flags, pin_node)

    def _tick_live_transient(self):
        return self.simulation._tick_live_transient()

    def _get_sim_context(self):
        return self.simulation._get_sim_context()

    def _merge_all_sheets(self):
        return self.simulation._merge_all_sheets()

    def _build_analog_components(self, items, pin_node):
        return self.simulation._build_analog_components(items, pin_node)

    def _evaluate_digital_gates(self, pin_node, dc_voltages, silent=False,
                                out=None, sim_comps=None):
        return SimulationController._evaluate_digital_gates(
            self, pin_node, dc_voltages, silent, out, sim_comps)

    def _show_power_triangle(self):
        if not self._last_ac_result:
            return
        dlg = PowerTriangleDialog(self._last_ac_result, COLORS, parent=self)
        dlg.exec()

    def _on_instrument_changed(self, item):
        """Un parámetro de instrumento (FGEN, …) cambió. Si el live
        transient está corriendo, reconstruyo la lista de componentes del
        motor con los nuevos valores. Manteniendo el estado anterior se
        evita un “salto” brusco en la simulación."""
        if not self._sim_running:
            return
        if self._live_components is None:
            return
        # Reconstruir componentes del motor para el item modificado.
        # Buscamos por nombre y reemplazamos en la lista.
        pin_node = self._live_pin_node or {}
        new_comps = build_engine_components_for_item(item, pin_node)
        if not new_comps:
            return
        new_by_name = {c.name: c for c in new_comps}
        self._live_components = [
            new_by_name.get(c.name, c) for c in self._live_components
        ]
        # CRÍTICO: si cambió la frecuencia de una fuente AC, recalcular
        # `_live_freq`. Sin esto el `dt_internal` del tick sigue calculado
        # para la frecuencia ANTERIOR, lo que produce aliasing severo y
        # hace que el circuito “no responda” al cambio de frecuencia.
        if item.comp_type in ('VAC', 'FGEN'):
            all_items = [it for sh in self._schematic_sheets()
                         for it in sh['scene'].components]
            new_freq = self._max_ac_source_frequency(all_items)
            if new_freq:
                prev_freq = self._live_freq
                self._live_freq = new_freq
                # Si la frecuencia cambió >2x (subió o bajó) reseteamos el
                # estado: los capacitores del filtro tienen carga residual
                # de la frecuencia anterior y mantenerla causa transitorios
                # MUY largos (o falsos steady-state) en el solver trapezoidal.
                if prev_freq > 0 and (new_freq / prev_freq > 2.0 or
                                       prev_freq / new_freq > 2.0):
                    self._live_state = None   # el próximo tick recalcula DC OP
                    # Limpiar buffers de osciloscopios abiertos: las muestras
                    # de la frecuencia anterior tienen otra escala temporal.
                    for it in self.scene.components:
                        if it.comp_type == 'OSC':
                            dlg = getattr(it, '_panel_dialog', None)
                            if dlg is not None and dlg.isVisible():
                                try:
                                    dlg.screen.clear()
                                except AttributeError:
                                    pass

    @staticmethod
    def _max_ac_source_frequency(items) -> float:
        """Frecuencia máxima entre todas las fuentes AC del iterable.
        Devuelve 0.0 si no hay fuentes AC."""
        return max((it.frequency for it in items
                    if it.comp_type in ('VAC', 'FGEN')), default=0.0)

    def _on_logic_state_toggled(self, item):
        """Re-ejecuta la simulación cuando un LOGIC_STATE cambia de estado."""
        if self._sim_running:
            if self._sim_mode == 'live_transient':
                self._on_instrument_changed(item)
            else:
                self._run_simulation_dc(silent=True)
        else:
            # Aunque no esté en modo continuo, actualizar igual (one-shot silencioso)
            pin_node = self.scene.extract_netlist()
            std = DEFAULT_STANDARD
            # Calcular voltaje del estado y actualizar display del prop_table
            v = std.Voh if item.value else std.Vol
            self._on_component_selected(item)

    # ──────────────────────────────────────────────────────────────────
    # Potenciómetro: control en tiempo real desde el panel derecho
    # ──────────────────────────────────────────────────────────────────
    def _on_pot_slider(self, value: int):
        """El usuario movió el slider → actualiza el wiper en vivo."""
        if self._selected_pot is None:
            return
        self._selected_pot.pot_wiper = value / 1000.0
        self._selected_pot.update()           # repinta el componente con la flecha movida
        self._update_pot_label()
        # Si la simulación continua está corriendo, el siguiente tick
        # recalcula con el nuevo valor automáticamente.

    def _update_pot_label(self):
        if self._selected_pot is None:
            return
        w   = self._selected_pot.pot_wiper
        Rt  = max(self._selected_pot.value, 1.0)
        Ref = Rt * w
        # Formato bonito de R efectiva
        if Ref >= 1e6:   r_str = f"{Ref/1e6:.2f} MΩ"
        elif Ref >= 1e3: r_str = f"{Ref/1e3:.2f} kΩ"
        else:            r_str = f"{Ref:.2f} Ω"
        self.pot_value_label.setText(f"{w*100:.1f}% — R = {r_str}")

    def _clear_external_component_panel(self):
        layout = getattr(self, 'external_component_panel_layout', None)
        if layout is None:
            return
        while layout.count():
            child = layout.takeAt(0)
            widget = child.widget()
            if widget is not None:
                widget.deleteLater()
        self._external_component_controller = None
        self.external_component_panel.setVisible(False)

    def _create_external_component_controller(self, item):
        backend_name = getattr(item, 'external_backend', '')
        if not backend_name:
            return None
        runner = getattr(item, 'external_firmware_runner', None)
        if runner is None:
            runner = self.external_libraries.create_micropython_runner(
                backend_name, self._external_runtimes.get(backend_name))
            item.external_firmware_runner = runner
        controller = self.external_libraries.create_component_controller(
            item, {
                'parent': self,
                'runtime': self._external_runtimes.get(backend_name),
                'micropython_runner': runner,
                'tr': self.tr,
                'open_file': lambda title, filters: QFileDialog.getOpenFileName(
                    self, title, '', filters)[0],
                'load_firmware': lambda path: self.external_libraries.load_firmware(
                    backend_name, path),
                'warning': lambda title, message: QMessageBox.warning(
                    self, title, message),
                'status_message': self.statusBar().showMessage,
                'emit_runtime_event': self.simulation._on_external_runtime_event,
            })
        if controller is not None and getattr(controller, 'widget', None) is not None:
            self.external_component_panel_layout.addWidget(controller.widget)
            self.external_component_panel.setVisible(True)
            self._external_component_controller = controller
        return controller

    def _on_component_selected(self, item):
        self.prop_table.setRowCount(0)
        self._selected_component = item
        self._clear_external_component_panel()
        controller = (self._create_external_component_controller(item)
                      if item is not None else None)
        options = footprint_names_for_type(item.comp_type) if item else ()
        self.footprint_combo.blockSignals(True)
        self.footprint_combo.clear()
        if options:
            selected = getattr(item, 'footprint_name', '')
            if selected not in options:
                selected = options[0]
                item.footprint_name = selected
            self.footprint_combo.addItems(options)
            self.footprint_combo.setCurrentText(selected)
        self.footprint_combo.blockSignals(False)
        self.footprint_label.setVisible(bool(options))
        self.footprint_combo.setVisible(bool(options))
        # ── Slider del potenciómetro ──────────────────────────────────────
        if item is not None and item.comp_type == 'POT':
            self._selected_pot = item
            self.pot_slider.blockSignals(True)
            self.pot_slider.setValue(int(item.pot_wiper * 1000))
            self.pot_slider.blockSignals(False)
            self.pot_panel.setVisible(True)
            self._update_pot_label()
        else:
            self._selected_pot = None
            self.pot_panel.setVisible(False)

        if item is None:
            return

        # Nodos automáticos desde cables
        pin_node = self.scene.extract_netlist()

        def _node_display(manual, auto_key):
            v = manual.strip() if manual.strip() else pin_node.get(auto_key, '—')
            return v if manual.strip() else f"{v} ({self.tr('auto')})"

        no_value_types = {
            'GND', 'NODE', 'NET_LABEL_IN', 'NET_LABEL_OUT',
            'PORT', 'SUBCKT', 'KEYPAD4X4',
        }
        rows = [
            (self.tr("Type"), item.comp_type),
            (self.tr("Name"), item.name),
        ]
        if item.comp_type not in no_value_types and not item.is_external_component():
            rows.append((self.tr("Value"), f"{item.value} {item.unit}"))
        rows.append((self.tr("Rotation"), f"{item._angle}°"))
        if options:
            rows.insert(1, (self.tr("Footprint"), item.footprint_name))

        if item.comp_type == 'KEYPAD4X4':
            keys = list(getattr(item, 'keypad_keys', []) or [])
            keys.extend([''] * (len(KEYPAD_BUTTON_LABELS) - len(keys)))
            for label, key in zip(KEYPAD_BUTTON_LABELS, keys):
                rows.append((self.tr('Button {label} key').format(label=label),
                             key or '—'))
            pressed = [label for label, active in zip(
                KEYPAD_BUTTON_LABELS,
                getattr(item, 'keypad_pressed', [])) if active]
            rows.append((self.tr('Pressed'), ', '.join(pressed) or '—'))
            for pin, label in enumerate(('R1', 'R2', 'R3', 'R4',
                                         'C1', 'C2', 'C3', 'C4'), 1):
                rows.append((label, _node_display(
                    '', f"{item.name}__p{pin}")))
        elif item.is_external_component():
            if controller is not None:
                rows.extend(controller.property_rows(pin_node) or [])
            else:
                definition = getattr(item, 'external_definition', None) or {}
                rows.extend((
                    (self.tr('Board'), definition.get(
                        'name', self.tr('External board'))),
                    (self.tr('Pins'), str(len(definition.get(
                        'pins', [])) or len(item.all_pin_positions_scene()))),
                    (self.tr('Status'), self.tr(
                        'Schematic symbol only. PCB footprint is not available yet.')),
                ))
        elif item.comp_type in DIGITAL_GATE_TYPES:
            n_in = item.dig_inputs if item.comp_type != 'NOT' else 1
            rows.append((self.tr("Output (Y)"), _node_display(item.node1, f"{item.name}__p1")))
            rows.append((self.tr("Input 1 (A)"), _node_display(item.node2, f"{item.name}__p2")))
            if n_in >= 2:
                rows.append((self.tr("Input 2 (B)"), _node_display(
                    item.node3 if hasattr(item,'node3') else '', f"{item.name}__p3")))
            for i in range(2, n_in):
                _extra = getattr(item, 'dig_input_nodes', [])
                _manual = _extra[i-2] if len(_extra) > i-2 else ''
                rows.append((self.tr("Input {number}").format(number=i + 1), _node_display(_manual, f"{item.name}__p{i+2}")))
            rows.append((self.tr("Input count"), str(n_in)))
            rows.append((self.tr("Propagation delay"), f"{item.dig_tpd_ns} ns"))
        elif item.comp_type in DIGITAL_FLIPFLOP_TYPES:
            input_labels = {
                'DFF': 'Data (D)', 'JKFF': 'Input J',
                'TFF': 'Input T', 'SRFF': 'Set (S)',
            }
            clock_labels = {
                'DFF': 'CLK', 'JKFF': 'Input K',
                'TFF': 'CLK', 'SRFF': 'Reset (R)',
            }
            rows.append((self.tr("Output Q"),
                         _node_display(item.node1, f"{item.name}__p1")))
            rows.append((self.tr(input_labels[item.comp_type]),
                         _node_display(item.node2, f"{item.name}__p2")))
            rows.append((self.tr(clock_labels[item.comp_type]), _node_display(
                item.node3 if hasattr(item, 'node3') else '',
                f"{item.name}__p3")))
        elif item.comp_type == 'IC555':
            labels = ('1 GND', '2 TRIG', '3 OUT', '4 RESET',
                      '5 CTRL', '6 THRESH', '7 DISCH', '8 VCC')
            values = list(getattr(item, 'timer_nodes', []) or [])
            values.extend([''] * (8 - len(values)))
            for pin, label in enumerate(labels, 1):
                rows.append((label, _node_display(
                    values[pin - 1], f"{item.name}__p{pin}")))
        elif item.comp_type == 'COUNTER':
            for pin in range(1, len(item.all_pin_positions_scene()) + 1):
                label = 'CLK' if pin == 2 else f"Q{0 if pin == 1 else pin - 2}"
                manual = {1: item.node1, 2: item.node2}.get(pin, '')
                rows.append((label, _node_display(
                    manual, f"{item.name}__p{pin}")))
        elif item.comp_type == 'LOGIC_STATE':
            rows.append((self.tr("Output"),  _node_display(item.node1, f"{item.name}__p1")))
            rows.append((self.tr("State"),  "1 (HIGH)" if item.value else "0 (LOW)"))
        elif item.comp_type == 'MUX2':
            rows.append((self.tr("Output (Y)"), _node_display(item.node1, f"{item.name}__p1")))
            rows.append((self.tr("Input 0 (I0)"), _node_display(item.node2, f"{item.name}__p2")))
            rows.append((self.tr("Input 1 (I1)"), _node_display(item.node3, f"{item.name}__p3")))
            rows.append((self.tr("Select (SEL)"), _node_display(item.node4, f"{item.name}__p4")))
        elif item.comp_type == 'SPDT3':
            position = {-1: self.tr('ON 1'), 0: self.tr('OFF'), 1: self.tr('ON 2')}
            rows[2] = (self.tr("Value"), position.get(
                int(round(item.value)), self.tr('OFF')))
            rows.append((self.tr('Position'), position.get(
                int(round(item.value)), self.tr('OFF'))))
            rows.append((self.tr('Common (COM)'), _node_display(
                item.node1, f"{item.name}__p1")))
            rows.append((self.tr('ON 1'), _node_display(
                item.node2, f"{item.name}__p2")))
            rows.append((self.tr('ON 2'), _node_display(
                item.node3, f"{item.name}__p3")))
            for label, attr in (
                    ('ON 1 key:', 'switch_on1_key'),
                    ('OFF key:', 'switch_off_key'),
                    ('ON 2 key:', 'switch_on2_key')):
                key = getattr(item, attr, '')
                if key:
                    rows.append((self.tr(label), key))
        elif item.comp_type in SIX_PIN_NODE_LABELS:
            lbls = SIX_PIN_NODE_LABELS[item.comp_type]
            node_labels = {
                'Common A': self.tr('Common A'),
                'A 1': self.tr('A 1'),
                'A 2': self.tr('A 2'),
                'Common B': self.tr('Common B'),
                'B 1': self.tr('B 1'),
                'B 2': self.tr('B 2'),
            }
            for label, node, pin in zip(
                    lbls,
                    (item.node1, item.node2, item.node3,
                     item.node4, item.node5, item.node6),
                    range(1, 7)):
                rows.append((node_labels[label], _node_display(
                    node, f"{item.name}__p{pin}")))
        elif item.comp_type in FIVE_PIN_NODE_LABELS:
            lbls = FIVE_PIN_NODE_LABELS[item.comp_type]
            for label, node, pin in zip(
                    lbls,
                    (item.node1, item.node2,
                     getattr(item, 'node3', ''),
                     getattr(item, 'node4', ''),
                     getattr(item, 'node5', '')),
                    range(1, 6)):
                rows.append((label, _node_display(
                    node, f"{item.name}__p{pin}")))
        elif item.comp_type in FOUR_PIN_NODE_LABELS:
            lbls = FOUR_PIN_NODE_LABELS[item.comp_type]
            rows.append((lbls[0], _node_display(item.node1, f"{item.name}__p1")))
            rows.append((lbls[1], _node_display(item.node2, f"{item.name}__p2")))
            rows.append((lbls[2], _node_display(
                item.node3 if hasattr(item, 'node3') else '', f"{item.name}__p3")))
            rows.append((lbls[3], _node_display(
                item.node4 if hasattr(item, 'node4') else '', f"{item.name}__p4")))
        elif item.comp_type in ('NET_LABEL_IN', 'NET_LABEL_OUT'):
            kind = 'Input' if item.comp_type == 'NET_LABEL_IN' else 'Output'
            rows.append((self.tr('Net name'), getattr(item, 'sheet_label', '')))
            rows.append((self.tr('Direction'), self.tr(kind)))
        elif item.comp_type == 'PORT':
            rows.append((self.tr('Port'), getattr(item, 'port_name', 'IN')))
            rows.append((self.tr('Direction'), getattr(item, 'port_dir', 'in')))
            rows.append((self.tr('Pin'), _node_display(
                item.node1, f"{item.name}__p1")))
        elif item.comp_type == 'SUBCKT':
            rows.append((self.tr('Subcircuit'), getattr(item, 'subckt_name', '')))
            for pin, definition in enumerate(getattr(item, 'ic_pins', []) or [], 1):
                label = definition.get('name', f'Pin {pin}') \
                    if isinstance(definition, dict) else f'Pin {pin}'
                rows.append((label, _node_display(
                    '', f"{item.name}__p{pin}")))
        elif item.comp_type == 'GND':
            rows.append((self.tr('Reference'), '0'))
        elif item.comp_type == 'NODE':
            rows.append((self.tr('Net'), _node_display(
                item.node1, f"{item.name}__p1")))
        elif item.comp_type == 'CLK':
            rows.append((self.tr('Output'), _node_display(
                item.node1, f"{item.name}__p1")))
            rows.append((self.tr('Running'), self.tr('Yes') if getattr(
                item, 'clk_running', False) else self.tr('No')))
        else:
            lbl1, lbl2, lbl3 = COMPONENT_NODE_LABELS.get(
                item.comp_type, DEFAULT_NODE_LABELS)
    
            # Para fuentes, invertir el orden de visualización
            # porque node1=pin izquierdo=negativo, node2=pin derecho=positivo
            if item.comp_type in ('V', 'VAC', 'I'):
                rows.append((self.tr("Node +"), _node_display(item.node2, f"{item.name}__p2")))
                rows.append((self.tr("Node −"), _node_display(item.node1, f"{item.name}__p1")))
            elif item.comp_type == 'LAMP':
                rows.append((self.tr("Node +"), _node_display(item.node1, f"{item.name}__p1")))
                rows.append((self.tr("Node −"), _node_display(item.node2, f"{item.name}__p2")))
            else:
                rows.append((lbl1, _node_display(item.node1, f"{item.name}__p1")))
                rows.append((lbl2, _node_display(item.node2, f"{item.name}__p2")))
            if lbl3 is not None:
                rows.append((lbl3, _node_display(
                    item.node3 if hasattr(item,'node3') else '', f"{item.name}__p3")))
            if item.comp_type == 'Z':
                rows.append((self.tr("Z mode"), item.z_mode))
                if item.z_mode == 'rect':
                    rows.append(("Z", f"{item.z_real:.4g} {item.z_imag:+.4g}j Ω"))
                else:
                    rows.append(("Z", f"{item.z_mag:.4g} ∠{item.z_phase:.2f}° Ω"))

        # Propiedades configurables que también aparecen en el diálogo de
        # doble clic. Mantenerlas aquí evita que el panel quede desfasado.
        if item.comp_type in DIGITAL_BRIDGE_TYPES:
            rows.extend((
                (self.tr('Resolution (bits)'), str(item.dig_bits_adc)),
                (self.tr('Vref'), f"{item.dig_vref} V"),
                (self.tr('MNA analog node'), item.dig_analog_node or '—'),
                (self.tr('Net CLK (optional)'), item.dig_clk or '—'),
            ))
        elif item.comp_type == 'COUNTER':
            rows.extend((
                (self.tr('Bits'), str(item.dig_bits)),
                (self.tr('Net CLK'), item.dig_clk),
            ))

        if item.comp_type in DIGITAL_FLIPFLOP_TYPES:
            rows.extend((
                (self.tr('Net CLK'), item.dig_clk),
                (self.tr('Propagation delay'), f"{item.dig_tpd_ns} ns"),
            ))
        elif item.comp_type == 'LED':
            rows.append((self.tr('LED color'), item.led_color))
        elif item.comp_type == 'VAC':
            rows.extend((
                (self.tr('Amplitude mode'), item.ac_mode),
                (self.tr('Frequency (Hz)'), str(item.frequency)),
                (self.tr('Phase'), f"{item.phase_deg}°"),
            ))
        elif item.comp_type == 'POT':
            rows.append((self.tr('Wiper (0–1)'), str(item.pot_wiper)))
        elif item.comp_type == 'XFMR':
            rows.extend((
                (self.tr('Ratio n = N1/N2'), str(item.xfmr_ratio)),
                (self.tr('Maximum primary current (A)'),
                 f"{item.xfmr_imax} A"),
            ))
        elif item.comp_type == 'RELAY':
            rows.extend((
                (self.tr('Activation voltage (V)'),
                 str(item.relay_activation_voltage)),
                (self.tr('Active'), self.tr('Yes') if item.relay_active
                 else self.tr('No')),
            ))
        elif item.comp_type == 'FGEN':
            rows.extend((
                (self.tr('Waveform'), item.fgen_waveform),
                (self.tr('Frequency (Hz)'), str(item.frequency)),
                (self.tr('Offset (V)'), str(item.fgen_offset)),
                (self.tr('Duty cycle (%)'), str(item.fgen_duty * 100)),
                (self.tr('Phase'), f"{item.phase_deg}°"),
            ))
        elif item.comp_type == 'OSC':
            rows.extend((
                (self.tr('Time/div'), f"{item.osc_time_div} s"),
                (self.tr('Channel A V/div'), f"{item.osc_v_div_a} V"),
                (self.tr('Channel B V/div'), f"{item.osc_v_div_b} V"),
                (self.tr('Trigger source'), item.osc_trig_source),
                (self.tr('Trigger edge'), item.osc_trig_edge),
                (self.tr('Trigger level'), f"{item.osc_trig_level} V"),
            ))
        elif item.comp_type == 'MULTIMETER':
            reading = getattr(item, 'meter_reading', None)
            rows.extend((
                (self.tr('Measurement'), item.meter_quantity),
                (self.tr('Coupling'), item.meter_coupling),
                (self.tr('Reading'), '—' if reading is None else str(reading)),
            ))
        elif item.comp_type in ('SPST', 'SPDT', 'DPDT'):
            key = getattr(item, 'switch_key', '')
            if key:
                rows.append((self.tr('Toggle key'), key))

        for label, val in rows:
            r = self.prop_table.rowCount()
            self.prop_table.insertRow(r)
            self.prop_table.setItem(r, 0, QTableWidgetItem(label))
            self.prop_table.setItem(r, 1, QTableWidgetItem(str(val)))

    def _set_external_gpio_test(self, value: int):
        """Prueba el primer GPIO de un componente externo sin conocer su tipo."""
        controller = getattr(self, '_external_component_controller', None)
        setter = getattr(controller, 'set_gpio', None)
        if callable(setter):
            setter(value)
            return
        item = getattr(self, '_selected_component', None)
        if item is None or not item.is_external_component():
            return
        pin = next((pin for pin in item._external_board_pins()
                    if isinstance(pin.get('gpio'), int)), None)
        if pin is None:
            return
        runtime = next(iter(getattr(self, '_external_runtimes', {}).values()), None)
        if runtime is not None:
            runtime.pin_mode(pin['gpio'], 'OUTPUT')
            runtime.set_gpio(pin['gpio'], value)
        else:
            self.simulation._on_external_runtime_event({
                'type': 'gpio', 'gpio': pin['gpio'], 'value': value,
                'source': 'firmware',
            })

    def _on_footprint_changed(self, footprint_name: str):
        item = getattr(self, '_selected_component', None)
        if item is None or footprint_name not in footprint_names_for_type(
                item.comp_type):
            return
        item.footprint_name = footprint_name
        for row in range(self.prop_table.rowCount()):
            field = self.prop_table.item(row, 0)
            value = self.prop_table.item(row, 1)
            if field is not None and value is not None \
                    and field.text() == self.tr("Footprint"):
                value.setText(footprint_name)
                break
        self.statusBar().showMessage(
            self.tr("{component}: footprint set to {footprint}; regenerate PCB to apply").format(
                component=item.name, footprint=footprint_name))

    def _on_pcb_footprint_selected(self, footprint):
        self._on_component_selected(None)
        if footprint is None:
            return
        pcb = self._active_pcb_editor()
        format_length = pcb._format_length if pcb is not None else str
        rows = [
            (self.tr("Reference"), footprint.reference),
            (self.tr("Footprint"), footprint.footprint_name),
            (self.tr("Value"), footprint.value),
            (self.tr("Position X"), format_length(footprint.x_mm)),
            (self.tr("Position Y"), format_length(footprint.y_mm)),
            (self.tr("Rotation"), f"{footprint.angle:g}°"),
            (self.tr("Side"), footprint.side),
            (self.tr("Pads"), str(len(footprint.pads))),
        ]
        for label, value in rows:
            row = self.prop_table.rowCount()
            self.prop_table.insertRow(row)
            self.prop_table.setItem(row, 0, QTableWidgetItem(label))
            self.prop_table.setItem(row, 1, QTableWidgetItem(str(value)))
        self._install_pcb_fields(footprint)

    def _install_pcb_fields(self, obj):
        from PyQt6.QtWidgets import QComboBox, QDoubleSpinBox, QPlainTextEdit, QPushButton

        pcb = self._active_pcb_editor()
        labels = {'x_mm': 'Position X', 'y_mm': 'Position Y', 'angle': 'Rotation',
                  'value': 'Value', 'side': 'Side', 'width_mm': 'Width',
                  'height_mm': 'Height', 'drill_mm': 'Drill', 'shape': 'Shape',
                  'diameter_mm': 'Diameter', 'size_mm': 'Size', 'layer': 'Layer',
                  'text': 'Text', 'points': 'Vertices (mm)', 'footprint_name': 'Footprint'}
        for row in range(self.prop_table.rowCount()):
            for column in (0, 1):
                cell = self.prop_table.item(row, column)
                cell.setFlags(cell.flags() & ~Qt.ItemFlag.ItemIsEditable)
        for name in pcb.property_names(obj):
            label = self.tr(labels[name])
            if isinstance(obj, PcbPad) and name in ('x_mm', 'y_mm'):
                label += self.tr(' (local mm)')
            row = next((r for r in range(self.prop_table.rowCount())
                        if self.prop_table.item(r, 0).text() == label), self.prop_table.rowCount())
            if row == self.prop_table.rowCount():
                self.prop_table.insertRow(row)
                cell = QTableWidgetItem(label)
                cell.setFlags(cell.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.prop_table.setItem(row, 0, cell)
            field = pcb.property_field(obj, name)
            self.prop_table.setCellWidget(row, 1, field)

            def commit(name=name, field=field):
                try:
                    value = (field.value() if isinstance(field, QDoubleSpinBox)
                             else field.currentText() if isinstance(field, QComboBox)
                             else field.text())
                    if value != getattr(obj, name):
                        pcb.apply_properties(obj, {name: value})
                except (ValueError, TypeError) as exc:
                    self.statusBar().showMessage(str(exc))
                    field.setToolTip(str(exc))
                    field.setStyleSheet('border: 1px solid #ed6963;')

            if isinstance(field, QComboBox):
                field.currentTextChanged.connect(lambda text, fn=commit: fn())
            elif isinstance(field, QPlainTextEdit):
                # Multi-vertex edits remain atomic in the shared properties dialog.
                button = QPushButton(self.tr('Edit vertices…'))
                button.clicked.connect(lambda: pcb.edit_properties())
                self.prop_table.setCellWidget(row, 1, button)
            else:
                field.editingFinished.connect(commit)

    def _on_pcb_object_selected(self, selected):
        if isinstance(selected, PcbFootprint):
            self._on_pcb_footprint_selected(selected)
            return
        self._on_component_selected(None)
        if not isinstance(selected, (PcbPad, PcbTrack, PcbVia, PcbText)):
            return
        pcb = self._active_pcb_editor()
        format_length = pcb._format_length if pcb is not None else str
        if isinstance(selected, PcbPad):
            footprint = next(fp for fp in pcb.board.footprints
                             if any(pad is selected for pad in fp.pads))
            x, y = footprint.pad_position(selected)
            rows = [
                (self.tr('Object'), f'{footprint.reference}.{selected.number}'),
                (self.tr('Net'), selected.net),
                (self.tr('Position X'), format_length(x)),
                (self.tr('Position Y'), format_length(y)),
                (self.tr('Shape'), selected.shape),
                (self.tr('Drill'), format_length(selected.drill_mm)),
                (self.tr('Layers'), ', '.join(footprint.pad_layers(selected))),
            ]
        elif isinstance(selected, PcbTrack):
            length = sum(
                ((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5
                for (x1, y1), (x2, y2) in zip(
                    selected.points, selected.points[1:]))
            rows = [
                (self.tr('Object'), self.tr('Track')),
                (self.tr('Net'), selected.net),
                (self.tr('Layer'), selected.layer),
                (self.tr('Width'), format_length(selected.width_mm)),
                (self.tr('Length'), format_length(length)),
                (self.tr('Vertices'), str(len(selected.points))),
            ]
        elif isinstance(selected, PcbVia):
            rows = [
                (self.tr('Object'), self.tr('Via')),
                (self.tr('Net'), selected.net),
                (self.tr('Position X'), format_length(selected.x_mm)),
                (self.tr('Position Y'), format_length(selected.y_mm)),
                (self.tr('Drill'), format_length(selected.drill_mm)),
                (self.tr('Diameter'), format_length(selected.diameter_mm)),
                (self.tr('Layers'), ', '.join(selected.layers)),
            ]
        else:
            rows = [
                (self.tr('Object'), self.tr('Silkscreen text')),
                (self.tr('Text'), selected.text),
                (self.tr('Layer'), selected.layer),
                (self.tr('Position X'), format_length(selected.x_mm)),
                (self.tr('Position Y'), format_length(selected.y_mm)),
                (self.tr('Size'), format_length(selected.size_mm)),
                (self.tr('Rotation'), f'{selected.angle:g}°'),
            ]
        for label, value in rows:
            row = self.prop_table.rowCount()
            self.prop_table.insertRow(row)
            self.prop_table.setItem(row, 0, QTableWidgetItem(label))
            self.prop_table.setItem(row, 1, QTableWidgetItem(str(value)))

        self._install_pcb_fields(selected)

    # ── Circuito de demo ─────────────────────────
    def _load_demo_circuit(self):
        """Carga un divisor de voltaje de ejemplo."""
        s = self.scene
        s.place_component('V',   QPointF(-120,   0), 'V1', 10.0, 'V',  '0', 'A')
        s.place_component('R',   QPointF(   0, -80), 'R1', 1000.0, 'Ω', 'A', 'B')
        s.place_component('R',   QPointF(   0,  80), 'R2', 1000.0, 'Ω', 'B', '0')
        s.place_component('GND', QPointF(-120,  80), 'GND1')
        self.results_text.setPlainText(
            self.tr("Demo circuit: voltage divider\n"
            "V1=10V, R1=R2=1kΩ\n\n"
            "Expected: V(B) = 5.0 V\n\n"
            "Press ▶ SIMULATE to verify.\n\n"
            "Tip: double-click a component\nto edit its nodes and values.")
        )

    # ── Acciones ─────────────────────────────────
    def _new_circuit(self):
        reply = QMessageBox.question(
            self, self.tr("New Circuit"),
            self.tr("Discard the current circuit (all sheets)?"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._clear_all_sheets()
            self._current_file = None
            self.setWindowTitle("Kirho — Simulador de Circuitos")
            self._load_demo_circuit()

    def _clear_circuit(self):
        """Limpia solo la hoja activa."""
        if self.scene is None:
            return
        for item in self.scene.components + self.scene.wires:
            self.scene.removeItem(item)
        self.scene.components.clear()
        self.scene.wires.clear()
        self.scene._comp_counter.clear()
        self.results_text.clear()

    def _clear_all_sheets(self, create_sheet=True):
        """Elimina todas las hojas y opcionalmente crea una nueva vacía."""
        for sheet in self._schematic_sheets():
            sc = sheet['scene']
            for item in sc.components + sc.wires:
                sc.removeItem(item)
            sc.components.clear()
            sc.wires.clear()
            sc._comp_counter.clear()
        self._sheets.clear()
        self.tab_widget.clear()
        self._pcb_file = None
        self._legacy_pcb_data = None
        if create_sheet:
            self._add_sheet(name=self.tr("Sheet 1"))
        self.results_text.clear()
        self._current_file = None
        self.setWindowTitle(self.tr("Kirho — Circuit Simulator"))

    # ── Fachada de documentos e impresión ───────────────────────────────
    def _serialize_sheet(self, scene: CircuitScene) -> dict:
        controller = self.documents if self is not None else DocumentController(None)
        return controller._serialize_sheet(scene)

    def _load_sheet_data(self, scene: CircuitScene, sheet_data: dict):
        controller = self.documents if self is not None else DocumentController(None)
        return controller._load_sheet_data(scene, sheet_data)

    def _save_circuit(self):
        if self._is_pcb_tab():
            return self.documents._save_pcb()
        return self.documents._save_circuit()

    def _save_circuit_as(self):
        if self._is_pcb_tab():
            return self.documents._save_pcb_as()
        return self.documents._save_circuit_as()

    def _open_circuit(self, path=None):
        return self.documents._open_circuit(path)

    def _open_pcb_file(self, path=None):
        return self.documents._open_pcb_file(path)

    def _import_spice(self):
        return self.documents._import_spice()

    def _export_spice(self):
        return self.documents._export_spice()

    def _print_sheet(self):
        return self.documents._print_sheet()

    def _render_print_page(self, scene: CircuitScene, printer: QPrinter):
        return self.documents._render_print_page(scene, printer)

    def _render_print_pages(self, scenes: List[CircuitScene], printer: QPrinter,
                            monochrome: bool = True):
        return self.documents._render_print_pages(scenes, printer, monochrome)

    def _reset_zoom(self):
        if self.scene is None:
            self.view.fitInView(self.view.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)
            return
        if self.scene.paper_visible:
            self._fit_paper_in_view()
            return
        self.view.resetTransform()
        self.view.centerOn(0, 0)

# ══════════════════════════════════════════════════════════════
# ENTRY POINT
# ══════════════════════════════════════════════════════════════
def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Kirho")
    app.setWindowIcon(QIcon(str(Path(__file__).resolve().parent / "assets" / "kirho.png")))
    app.setStyle("Fusion")
    load_translator(app, THEME_MANAGER.load_language())
    window = MainWindow()
    window.show()
    if len(sys.argv) > 1 and os.path.isfile(sys.argv[1]):
        window._open_circuit(sys.argv[1])
    sys.exit(app.exec())


if __name__ == '__main__':
    main()
