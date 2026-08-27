"""Construcción de la interfaz principal de Kirho."""

import sys
from typing import Dict, List

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QTableWidget, QTextEdit,
    QPushButton, QSplitter, QTabWidget, QToolBar,
)
from PyQt6.QtGui import QAction, QFont, QKeySequence
from PyQt6.QtCore import Qt

from kirho.ui.scene import PAPER_FORMATS
from kirho.ui.style import (
    COLORS, THEME_MANAGER, apply_theme_to_colors, _qfont,
)


class MainWindowUI:
    """Métodos de construcción visual usados por MainWindow."""

    def _build_tools_button(self):
        """Construye el QToolButton 'Herramientas' con menú desplegable.
        Aparece justo después del botón '+ Hoja' en la toolbar principal.
        """
        from PyQt6.QtWidgets import QToolButton, QMenu
        btn = QToolButton(self)
        btn.setText(self.tr("Tools"))
        btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        menu = QMenu(btn)
        for key in ('clk_frequency', 'analyze', 'bode', 'resistor_calc'):
            menu.addAction(self._shared_actions[key])
        menu.addSeparator()
        menu.addAction(self._shared_actions['erc'])
        btn.setMenu(menu)
        # Mostrar el menú también al pasar el cursor (hover)
        btn.installEventFilter(self)
        self._tools_button = btn
        return btn

    # ── Construcción UI ──────────────────────────

    def _build_ui(self):
        # Sistema de hojas (tabs) — cada hoja tiene su propia escena y vista
        self._sheets: List[Dict] = []  # [{scene, view, name}]
        self.tab_widget = QTabWidget()
        self.tab_widget.setTabsClosable(True)
        self.tab_widget.setMovable(True)
        self.tab_widget.tabCloseRequested.connect(self._close_sheet)
        self.tab_widget.currentChanged.connect(self._on_sheet_changed)

        # El botón para agregar hojas vive en la toolbar principal (+ Hoja)
        # Doble-click en la pestaña para renombrar
        self.tab_widget.tabBarDoubleClicked.connect(self._rename_sheet)

        # Crear la primera hoja
        self._add_sheet(name=self.tr("Sheet 1"))

        # Panel derecho (propiedades + resultados)
        self._build_right_panel()

        # Layout central: tabs + panel derecho
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self.tab_widget)
        splitter.addWidget(self.right_panel)
        splitter.setSizes([1000, 280])

        self.setCentralWidget(splitter)

        # En macOS Qt la muestra en la barra global; en Windows y Linux
        # permanece integrada en la ventana.
        self._build_shared_actions()
        self._build_menu_bar()

        # ── Toolbar PRINCIPAL (fila 1: archivo, zoom, etc.) ──────────────
        self._build_main_toolbar()

        # ── Toolbar SECUNDARIA (fila 2: categorías, herramientas, simulación)
        self._build_component_toolbar()

        # Status bar
        self.statusBar().showMessage(self.tr("Ready — Choose a category to place components"))

    def _build_right_panel(self):
        self.right_panel = QWidget()
        self.right_panel.setFixedWidth(260)
        layout = QVBoxLayout(self.right_panel)
        layout.setContentsMargins(8, 8, 8, 8)

        # Propiedades del componente seleccionado
        prop_label = QLabel(self.tr("PROPERTIES"))
        prop_label.setFont(_qfont('Menlo', 9, QFont.Weight.Bold))
        layout.addWidget(prop_label)

        self.prop_table = QTableWidget(0, 2)
        self.prop_table.setHorizontalHeaderLabels([self.tr("Field"), self.tr("Value")])
        self.prop_table.horizontalHeader().setStretchLastSection(True)
        self.prop_table.setMaximumHeight(200)
        layout.addWidget(self.prop_table)

        # ── Slider de potenciómetro (visible sólo cuando hay un POT seleccionado) ──
        from PyQt6.QtWidgets import QSlider
        self.pot_panel = QWidget()
        pot_layout = QVBoxLayout(self.pot_panel)
        pot_layout.setContentsMargins(0, 4, 0, 4)
        self.pot_label = QLabel(self.tr("POTENTIOMETER WIPER"))
        self.pot_label.setFont(_qfont('Menlo', 8, QFont.Weight.Bold))
        pot_layout.addWidget(self.pot_label)

        self.pot_slider = QSlider(Qt.Orientation.Horizontal)
        self.pot_slider.setRange(0, 1000)        # resolución 0.1%
        self.pot_slider.setValue(500)
        self.pot_slider.setToolTip(
            self.tr("Move the potentiometer wiper in real time.\n"
                    "When simulation is active, the effect is immediate."))
        self.pot_slider.valueChanged.connect(self._on_pot_slider)
        pot_layout.addWidget(self.pot_slider)

        self.pot_value_label = QLabel("50.0% — R = ----")
        self.pot_value_label.setFont(_qfont('Menlo', 8))
        pot_layout.addWidget(self.pot_value_label)

        self.pot_panel.setVisible(False)
        layout.addWidget(self.pot_panel)
        self._selected_pot = None   # ComponentItem actualmente seleccionado (POT)

        # Resultados de simulación
        res_label = QLabel(self.tr("RESULTS"))
        res_label.setFont(_qfont('Menlo', 9, QFont.Weight.Bold))
        layout.addWidget(res_label)

        self.results_text = QTextEdit()
        self.results_text.setReadOnly(True)
        self.results_text.setFont(_qfont('Menlo', 9))
        layout.addWidget(self.results_text)

        # Botón triángulo de potencia (visible solo tras análisis AC)
        self.btn_power_triangle = QPushButton(self.tr("📐  View Power Triangle"))
        self.btn_power_triangle.setFont(_qfont('Menlo', 9))
        self.btn_power_triangle.setVisible(False)
        self.btn_power_triangle.clicked.connect(self._show_power_triangle)
        layout.addWidget(self.btn_power_triangle)
        self._last_ac_result = None   # guardamos el resultado AC para el popup


    def _build_shared_actions(self):
        self._shared_actions = {}
        specs = [
            ('new', 'New', self._new_circuit, 'Ctrl+N'),
            ('open', 'Open', self._open_circuit, 'Ctrl+O'),
            ('import_spice', 'Import SPICE', self._import_spice, None),
            ('save', 'Save', self._save_circuit, 'Ctrl+S'),
            ('save_as', 'Save As…', self._save_circuit_as, 'Ctrl+Shift+S'),
            ('print', 'Print…', self._print_sheet, None),
            ('export_spice', 'Export SPICE', self._export_spice, 'Ctrl+E'),
            ('add_sheet', '+ Sheet', lambda: self._add_sheet(), 'Ctrl+T'),
            ('clear', 'Clear', self._clear_circuit, 'Ctrl+L'),
            ('zoom_in', 'Zoom +', lambda: self.view.scale(1.2, 1.2), None),
            ('zoom_out', 'Zoom −', lambda: self.view.scale(1 / 1.2, 1 / 1.2), None),
            ('reset', 'Reset', self._reset_zoom, 'Ctrl+0'),
            ('settings', '⚙ Settings', self._open_settings_dialog, 'Ctrl+,'),
            ('undo', 'Undo', self._undo_active_sheet, None),
            ('redo', 'Redo', self._redo_active_sheet, None),
            ('copy', 'Copy', self._copy_active_selection, None),
            ('cut', 'Cut', self._cut_active_selection, None),
            ('paste', 'Paste', self._paste_active_selection, None),
            ('duplicate', 'Duplicate', self._duplicate_active_selection, None),
            ('select', 'Select', self._set_select_mode, None),
            ('wire', 'Wire', self._set_wire_mode, None),
            ('align_left', 'Left', lambda: self._align_selection('left'), None),
            ('align_right', 'Right', lambda: self._align_selection('right'), None),
            ('align_top', 'Top', lambda: self._align_selection('top'), None),
            ('align_bottom', 'Bottom', lambda: self._align_selection('bottom'), None),
            ('distribute_horizontal', 'Horizontally', lambda: self._distribute_selection('x'), None),
            ('distribute_vertical', 'Vertically', lambda: self._distribute_selection('y'), None),
            ('clk_frequency', 'CLK Frequency…', self._set_clk_frequency, None),
            ('analyze', 'Analyze Circuit…', self._open_circuit_analyzer, None),
            ('bode', 'Bode / Transfer Analysis…', self._open_bode_analyzer, None),
            ('resistor_calc', 'Resistor Color Code…', self._open_resistor_calculator, None),
            ('erc', 'Check Circuit (ERC)', self._run_erc, None),
            ('about', 'About Kirho', self._show_about, None),
            ('quit', 'Quit Kirho', self.close, None),
        ]
        for key, text, slot, shortcut in specs:
            action = QAction(self.tr(text), self)
            if shortcut:
                action.setShortcut(shortcut)
            action.triggered.connect(slot)
            self._shared_actions[key] = action

        self._shared_actions['print'].setShortcut(
            QKeySequence(QKeySequence.StandardKey.Print))

        self._snap_action = QAction(self.tr('Snap to Grid'), self)
        self._snap_action.setCheckable(True)
        self._snap_action.setChecked(self.scene.snap_enabled)
        self._snap_action.toggled.connect(self._toggle_snap)
        self._shared_actions['snap'] = self._snap_action

        clk_action = QAction(self.tr('Toggle CLK'), self)
        clk_action.setShortcut('Ctrl+K')
        clk_action.triggered.connect(self._toggle_clk_running)
        self.addAction(clk_action)


    def _build_main_toolbar(self):
        """Barra superior (fila 1): archivo, zoom, etc."""
        tb = self.addToolBar("Principal")
        tb.setMovable(False)
        tb.setObjectName("main_toolbar")
        tb.setVisible(sys.platform != 'darwin')

        actions = [
            'new', 'open', 'import_spice', 'save', 'save_as', 'export_spice',
            '|', 'add_sheet', '__TOOLS__', 'clear',
            'zoom_in', 'zoom_out', 'reset',
        ]
        for key in actions:
            if key == '|':
                tb.addSeparator()
                continue
            if key == '__TOOLS__':
                tb.addWidget(self._build_tools_button())
                continue
            tb.addAction(self._shared_actions[key])

        # ── Botón Configuración (alineado a la derecha) ──────────────────
        tb.addSeparator()
        from PyQt6.QtWidgets import QSizePolicy
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding,
                             QSizePolicy.Policy.Preferred)
        tb.addWidget(spacer)

        act_settings = self._shared_actions['settings']
        act_settings.setToolTip(
            self.tr("Open settings window (themes, preferences…)") )
        tb.addAction(act_settings)

        self._current_file: Optional[str] = None


    def _build_menu_bar(self):
        """Menús nativos que exponen las mismas acciones de la barra de herramientas."""
        menu_bar = self.menuBar()
        menu_bar.setNativeMenuBar(sys.platform == 'darwin')

        app_menu = menu_bar.addMenu("Kirho")
        app_menu.addAction(self._shared_actions['settings'])
        app_menu.addAction(self._shared_actions['about'])
        app_menu.addSeparator()
        app_menu.addAction(self._shared_actions['quit'])

        file_menu = menu_bar.addMenu(self.tr("File"))
        for key in ('new', 'open', 'import_spice', 'save', 'save_as', 'print'):
            file_menu.addAction(self._shared_actions[key])
        file_menu.addSeparator()
        for key in ('export_spice', 'add_sheet'):
            file_menu.addAction(self._shared_actions[key])

        edit_menu = menu_bar.addMenu(self.tr("Edit"))
        for key in ('undo', 'redo'):
            edit_menu.addAction(self._shared_actions[key])
        edit_menu.addSeparator()
        for key in ('copy', 'cut', 'paste', 'duplicate'):
            edit_menu.addAction(self._shared_actions[key])
        edit_menu.addSeparator()
        for key in ('select', 'wire'):
            edit_menu.addAction(self._shared_actions[key])
        align_menu = edit_menu.addMenu(self.tr("Align"))
        for key in ('align_left', 'align_right', 'align_top', 'align_bottom'):
            align_menu.addAction(self._shared_actions[key])
        distribute_menu = edit_menu.addMenu(self.tr("Distribute"))
        for key in ('distribute_horizontal', 'distribute_vertical'):
            distribute_menu.addAction(self._shared_actions[key])
        edit_menu.addSeparator()
        edit_menu.addAction(self._shared_actions['clear'])

        view_menu = menu_bar.addMenu(self.tr("View"))
        for key in ('zoom_in', 'zoom_out', 'reset', 'snap'):
            view_menu.addAction(self._shared_actions[key])

        self._paper_visibility_action = view_menu.addAction(
            self.tr("Show Paper Frame"))
        self._paper_visibility_action.setCheckable(True)
        self._paper_visibility_action.setChecked(self.scene.paper_visible)
        self._paper_visibility_action.toggled.connect(self._toggle_paper_frame)

        self._title_block_visibility_action = view_menu.addAction(
            self.tr("Show Title Block"))
        self._title_block_visibility_action.setCheckable(True)
        self._title_block_visibility_action.setChecked(
            self.scene.title_block_visible)
        self._title_block_visibility_action.toggled.connect(
            self._toggle_title_block)
        view_menu.addAction(self.tr("Edit Title Block…"), self._edit_title_block)
        view_menu.addAction(
            self.tr("Paper line width…"), self._set_paper_line_width)

        paper_menu = view_menu.addMenu(self.tr("Paper Size"))
        self._paper_actions = {}
        for paper_id, (label, width_mm, height_mm) in PAPER_FORMATS.items():
            width_mm, height_mm = max(width_mm, height_mm), min(width_mm, height_mm)
            action = paper_menu.addAction(
                self.tr(f"{label} ({width_mm} × {height_mm} mm)"))
            action.setCheckable(True)
            action.triggered.connect(
                lambda checked, paper_id=paper_id: self._set_paper_format(paper_id))
            self._paper_actions[paper_id] = action
        self._update_paper_actions()

        tools_menu = menu_bar.addMenu(self.tr("Tools"))
        for key in ('clk_frequency', 'analyze', 'bode', 'resistor_calc'):
            tools_menu.addAction(self._shared_actions[key])
        tools_menu.addSeparator()
        tools_menu.addAction(self._shared_actions['erc'])


    def _build_component_toolbar(self):
        """Barra secundaria (fila 2): categorías de componentes, herramientas y simulación."""
        # ── FORZAR SALTO DE LÍNEA antes de esta toolbar ──────────────────
        self.addToolBarBreak()

        tb = QToolBar("Components", self)
        tb.setMovable(False)
        tb.setObjectName("component_toolbar")
        self.addToolBar(tb)

        # ── Categorías de componentes ────────────────────────────────────
        categories = [
            (self.tr("Passive"), [
                ('R',    'Resistor',      '━┤ZZZ├━'),
                ('POT',  'Potentiometer', '━┤Z↗├━'),
                ('C',    'Capacitor',     '━┤  ├━'),
                ('L',    'Inductor',      '━⌒⌒⌒━'),
                ('Z',    'Impedance',     '━┤▭├━'),
                ('XFMR', 'Transformer',   '⌇⌇'),
            ]),
            (self.tr("Sources"), [
                ('V',   'DC Voltage Source',  '━(+)━'),
                ('VAC', 'AC Voltage Source',  '━(~)━'),
                ('I',   'Current Source',     '━(→)━'),
            ]),
            (self.tr("Semiconductors"), [
                ('D',       'Diode',                 '━|▷|━'),
                ('LED',     'LED',                   '━|▷|★'),
                ('BRIDGE',  'Bridge rectifier',      '◇'),
                ('BJT_NPN', 'BJT NPN',               '━(NPN)'),
                ('BJT_PNP', 'BJT PNP',               '━(PNP)'),
                ('NMOS',    'MOSFET N',              '━[N]━'),
                ('PMOS',    'MOSFET P',              '━[P]━'),
                ('OPAMP',   'Op-Amp (ideal)',         '━[▷]━'),
                ('TL082',   'TL082 (op-amp dual)',   '━[▷²]━'),
            ]),
            (self.tr("Miscellaneous"), [
                ('SPST', 'SPST switch',   '━o/ o━'),
                ('SPDT', 'SPDT switch',   '━o/ o━'),
                ('SPDT3', 'ON-OFF-ON switch', '━o/ o/ o━'),
                ('DPDT', 'DPDT switch',   '━o/ o━\n━o/ o━'),
                ('RELAY','Relay',         '⌁'),
                ('LAMP', 'Bulb', '💡'),
            ]),
            (self.tr("Reference"), [
                ('GND',          'Ground',          '⏚'),
                ('NODE',         'Node',            '•'),
                ('NET_LABEL_IN',  'Input Net Label', '→▷'),
                ('NET_LABEL_OUT', 'Output Net Label', '◁→'),
            ]),
            (self.tr("Instruments"), [
                ('FGEN', 'Function generator', '⎍'),
                ('OSC',  'Oscilloscope (2 channels)', '∿▥'),
                ('MULTIMETER', 'Multimeter', '[V/A]'),
            ]),
            (self.tr("Digital"), [
                ('AND',       'AND Gate',       '&'),
                ('OR',        'OR Gate',        '≥1'),
                ('NOT',       'NOT Gate',       '○'),
                ('NAND',      'NAND Gate',      '&̄'),
                ('NOR',       'NOR Gate',       '≥1̄'),
                ('XOR',       'XOR Gate',       '=1'),
                ('DFF',       'Flip-flop D',    '▣D'),
                ('JKFF',      'Flip-flop JK',   '▣JK'),
                ('TFF',       'Flip-flop T',    '▣T'),
                ('SRFF',      'Flip-flop SR',   '▣SR'),
                ('COUNTER',   'Binary counter', '#'),
                ('MUX2',      'Multiplexer 2:1','⊞'),
                ('IC555',     'NE555 Timer',    '▣555'),
                ('LOGIC_STATE','Logic State',   '0/1'),
                ('CLK',       'Clock (CLK)',    '⏲'),
            ]),
        ]

        for cat_name, items in categories:
            btn = QPushButton(self.tr(cat_name))
            btn.setFont(_qfont('Menlo', 9))
            btn.setFixedHeight(28)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda checked, c=cat_name, it=items: self._show_picker(c, it))
            tb.addWidget(btn)

        tb.addSeparator()

        # ── Subcircuitos ─────────────────────────────────────────────────
        btn_sub = QPushButton(self.tr("⊞ Subcircuits"))
        btn_sub.setFont(_qfont('Menlo', 9))
        btn_sub.setFixedHeight(28)
        btn_sub.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_sub.clicked.connect(self._show_subcircuit_picker)
        tb.addWidget(btn_sub)

        btn_mksub = QPushButton(self.tr("＋ Create Subckt"))
        btn_mksub.setFont(_qfont('Menlo', 9))
        btn_mksub.setFixedHeight(28)
        btn_mksub.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_mksub.setToolTip(self.tr("Packages the current sheet as a reusable "
                                     "subcircuit (its Net Labels become the pins)"))
        btn_mksub.clicked.connect(self._create_subcircuit_from_sheet)
        tb.addWidget(btn_mksub)

        tb.addSeparator()

        # ── Herramientas ─────────────────────────────────────────────────
        self.btn_select = QPushButton(self.tr("↖ Select"))
        self.btn_select.setFont(_qfont('Menlo', 9))
        self.btn_select.setFixedHeight(28)
        self.btn_select.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_select.setCheckable(True)
        self.btn_select.setChecked(True)
        self.btn_select.clicked.connect(self._set_select_mode)
        tb.addWidget(self.btn_select)

        self.btn_wire = QPushButton(self.tr("✏ Wire"))
        self.btn_wire.setFont(_qfont('Menlo', 9))
        self.btn_wire.setFixedHeight(28)
        self.btn_wire.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_wire.setCheckable(True)
        self.btn_wire.clicked.connect(self._set_wire_mode)
        tb.addWidget(self.btn_wire)

        tb.addSeparator()

        # ── Simulación ─────────────────────────────────────────────────────
        # Estándar lógico fijo: CMOS 5 V (no expuesto en la UI)
        self.run_btn = QPushButton(self.tr("▶  SIMULATE"))
        self.run_btn.setFont(_qfont('Menlo', 10, QFont.Weight.Bold))
        self.run_btn.setFixedHeight(28)
        self.run_btn.setCheckable(True)
        self.run_btn.setToolTip(self.tr("Automatically detects: DC · AC · Digital · Mixed"))
        self.run_btn.clicked.connect(self._toggle_simulation)
        tb.addWidget(self.run_btn)

    # ── Estilo ───────────────────────────────────

    def _apply_style(self):
        self.setStyleSheet(f"""
            QMainWindow, QWidget {{
                background-color: {COLORS['bg']};
                color: {COLORS['text']};
                font-family: 'Menlo';
            }}
            QToolBar {{
                background: {COLORS['toolbar']};
                border-bottom: 1px solid {COLORS['panel_brd']};
                padding: 4px;
                spacing: 6px;
            }}
            QToolBar#component_toolbar {{
                background: {COLORS['panel']};
                border-bottom: 2px solid {COLORS['panel_brd']};
            }}
            QToolBar QToolButton {{
                color: {COLORS['text']};
                padding: 4px 10px;
                font-family: 'Menlo';
            }}
            QPushButton {{
                background: {COLORS['comp_body']};
                color: {COLORS['text']};
                border: 1px solid {COLORS['panel_brd']};
                border-radius: 4px;
                padding: 5px 12px;
                text-align: left;
            }}
            QPushButton:hover  {{ background: {COLORS['toolbar']}; }}
            QPushButton:checked {{ background: {COLORS['component']}; color: white; }}
            QPushButton#run    {{ background: {COLORS['component']}; color: white; font-weight: bold; }}
            QTableWidget {{
                background: {COLORS['comp_body']};
                color: {COLORS['text']};
                gridline-color: {COLORS['panel_brd']};
                border: 1px solid {COLORS['panel_brd']};
            }}
            QHeaderView::section {{
                background: {COLORS['toolbar']};
                color: {COLORS['text']};
                border: none; padding: 4px;
            }}
            QTextEdit {{
                background: {COLORS['comp_body']};
                color: {COLORS['current']};
                border: 1px solid {COLORS['panel_brd']};
                border-radius: 4px;
            }}
            QScrollBar:vertical, QScrollBar:horizontal {{
                background: {COLORS['comp_body']}; width: 8px; height: 8px;
            }}
            QScrollBar::handle {{ background: {COLORS['panel_brd']}; border-radius: 4px; }}
            QStatusBar {{ background: {COLORS['toolbar']}; color: {COLORS['text_dim']}; }}
            QSplitter::handle {{ background: {COLORS['panel_brd']}; width: 1px; }}
            QGroupBox {{
                font-family: 'Menlo';
                margin-top: 6px;
            }}
            QGroupBox::title {{
                subcontrol-origin: margin;
                left: 6px;
                padding: 0 4px;
            }}
        """)

    # ── Modos ────────────────────────────────────
