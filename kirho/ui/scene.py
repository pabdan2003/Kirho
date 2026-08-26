"""
CircuitScene — QGraphicsScene del canvas de circuitos. Maneja:
  • colocación, movimiento, rotación y borrado de componentes
  • dibujo de cables (modo wire) con preview
  • snap a grilla y a pines
  • selección, copy/paste y undo
  • dibujado de grid (drawBackground) y puntos de unión (drawForeground)

Y `build_engine_components_for_item`: helper para traducir un
ComponentItem analógico a los objetos del motor MNA.

Extraído de main.py.
"""
import math
import re
from typing import Optional, List, Dict, Tuple

from PyQt6.QtWidgets import QGraphicsScene, QMenu, QDialog
from PyQt6.QtGui import QPainter, QPen, QBrush, QColor, QFont, QFontMetricsF
from PyQt6.QtCore import Qt, QPointF, QRectF, QLineF, pyqtSignal

from kirho.ui.style import COLORS, GRID_SIZE, PIN_RADIUS, _qfont, theme_revision
from kirho.ui.items.component_item import ComponentItem
from kirho.ui.dialogs.component_dialog import ComponentDialog
from kirho.ui.items.wire_item import WireItem


# Tamaños ISO 216 y formatos habituales en milímetros.
PAPER_FORMATS = {
    'LETTER': ('Letter / Carta', 216, 279),
    'LEGAL':  ('Legal / Oficio', 216, 356),
    'A0': ('A0', 841, 1189),
    'A1': ('A1', 594, 841),
    'A2': ('A2', 420, 594),
    'A3': ('A3', 297, 420),
    'A4': ('A4', 210, 297),
    'A5': ('A5', 148, 210),
    'A6': ('A6', 105, 148),
    'A7': ('A7', 74, 105),
    'A8': ('A8', 52, 74),
    'A9': ('A9', 37, 52),
    'A10': ('A10', 26, 37),
}
DEFAULT_PAPER_FORMAT = 'A4'
PAPER_UNITS_PER_MM = 7.0
PAPER_MARGIN_MM = 12.0
PAPER_LINE_WIDTH = 2.5
TITLE_BLOCK_FIELDS = (
    ('title', 'Title'),
    ('project', 'Project'),
    ('author', 'Author'),
    ('date', 'Date'),
    ('revision', 'Revision'),
    ('sheet', 'Sheet'),
)


# ══════════════════════════════════════════════════════════════
# ESCENA DEL CIRCUITO
# ══════════════════════════════════════════════════════════════
class CircuitScene(QGraphicsScene):
    component_selected   = pyqtSignal(object)
    status_message       = pyqtSignal(str)
    logic_state_toggled  = pyqtSignal(object)   # emitido cuando LOGIC_STATE cambia
    instrument_changed   = pyqtSignal(object)   # cambió un parámetro de instrumento
    title_block_edit_requested = pyqtSignal()

    # Portapapeles compartido entre escenas (todas las hojas) — guarda un
    # snapshot de la selección.
    _clipboard: Optional[dict] = None

    _NAME_PREFIXES = {
        'R': 'R', 'V': 'V', 'I': 'I', 'C': 'C', 'L': 'L',
        'GND': 'GND', 'NODE': 'N', 'LOGIC_STATE': 'LS',
        'AND': 'AND', 'OR': 'OR', 'NOT': 'NOT', 'NAND': 'NAND',
        'NOR': 'NOR', 'XOR': 'XOR',
        'DFF': 'DFF', 'JKFF': 'JKFF',
        'TFF': 'TFF', 'SRFF': 'SRFF',
        'COUNTER': 'CNT', 'MUX2': 'MUX',
        'IC555': 'U555',
        'CLK': 'CLK',
        'NET_LABEL_IN': 'NL', 'NET_LABEL_OUT': 'NL',
        'FGEN': 'FGEN', 'OSC': 'XSC',
        'TL082': 'U',
        'MULTIMETER': 'XMM',
        'PORT': 'P', 'SUBCKT': 'X',
    }

    def __init__(self):
        super().__init__()
        self.setSceneRect(-1000, -1000, 2000, 2000)
        self.setBackgroundBrush(QBrush(QColor(COLORS['bg'])))

        self.components: List[ComponentItem] = []
        self.wires: List[WireItem] = []

        self._wire_start: Optional[QPointF] = None
        self._wire_preview: Optional[WireItem] = None
        self._mode = 'select'   # 'select' | 'wire' | 'place_{tipo}'

        self._comp_counter: Dict[str, int] = {}

        # Estado para arrastre grupal (mover circuito + cables como una unidad)
        self._group_drag_active: bool = False
        self._group_drag_start_pos: Optional[QPointF] = None
        self._group_drag_wires: List[dict] = []
        self._hover_pos: Optional[QPointF] = None

        # Stack de Ctrl+Z (undo). Cada entrada es un snapshot serializado.
        self._undo_stack: List[dict] = []
        self._redo_stack: List[dict] = []
        self._undo_max: int = 50
        self.snap_enabled: bool = True
        self.paper_format = DEFAULT_PAPER_FORMAT
        self.paper_line_width = PAPER_LINE_WIDTH
        self.paper_visible = False
        self.title_block_visible = False
        self.print_mode = False
        self.print_monochrome = True
        self.title_block = {key: '' for key, _ in TITLE_BLOCK_FIELDS}

    @staticmethod
    def _counter_key_for_type(comp_type: str) -> str:
        return 'NET_LABEL' if comp_type in ('NET_LABEL_IN', 'NET_LABEL_OUT') else comp_type

    def _bump_component_counter_from_name(self, comp_type: str, name: str):
        prefix = self._NAME_PREFIXES.get(comp_type, comp_type)
        m = re.match(rf'^{re.escape(prefix)}(\d+)', name or '')
        if not m:
            return
        key = self._counter_key_for_type(comp_type)
        self._comp_counter[key] = max(self._comp_counter.get(key, 0), int(m.group(1)))

    def _component_name_exists(self, name: str) -> bool:
        return any(c.name == name for c in self.components)

    def _next_component_name(self, comp_type: str, suffix: str = '') -> str:
        key = self._counter_key_for_type(comp_type)
        prefix = self._NAME_PREFIXES.get(comp_type, comp_type)
        count = self._comp_counter.get(key, 0)
        while True:
            count += 1
            candidate = f"{prefix}{count}{suffix}"
            if not self._component_name_exists(candidate):
                self._comp_counter[key] = count
                return candidate

    # ── Grid (dibujado en drawBackground para que sea independiente del zoom) ──
    def _grid_pens(self) -> Tuple[QPen, QPen]:
        """Devuelve (pen_minor, pen_major) cacheados; se reconstruyen sólo
        cuando cambia el tema (detectado vía theme_revision())."""
        rev = theme_revision()
        if getattr(self, '_grid_pens_rev', None) != rev:
            color = QColor(COLORS['grid_line'])
            pen_minor = QPen(color, 0)
            pen_minor.setCosmetic(True)
            pen_minor.setStyle(Qt.PenStyle.DotLine)
            pen_major = QPen(color, 0)
            pen_major.setCosmetic(True)
            pen_major.setStyle(Qt.PenStyle.SolidLine)
            self._grid_pen_minor = pen_minor
            self._grid_pen_major = pen_major
            self._grid_pens_rev = rev
        return self._grid_pen_minor, self._grid_pen_major

    def paper_rect(self) -> QRectF:
        """Rectángulo de la hoja en unidades del canvas."""
        _, width_mm, height_mm = PAPER_FORMATS[self.paper_format]
        width = max(width_mm, height_mm) * PAPER_UNITS_PER_MM
        height = min(width_mm, height_mm) * PAPER_UNITS_PER_MM
        return QRectF(-width / 2, -height / 2, width, height)

    def _paper_inner_rect(self) -> QRectF:
        paper = self.paper_rect()
        margin = min(
            PAPER_MARGIN_MM * PAPER_UNITS_PER_MM,
            paper.width() * 0.08,
            paper.height() * 0.15,
        )
        return paper.adjusted(margin, margin, -margin, -margin)

    def _title_block_layout(self, inner_rect: QRectF):
        block_height = min(150.0, inner_rect.height() * 0.35)
        row_height = block_height / len(TITLE_BLOCK_FIELDS)
        font_size = max(6, min(24, int(row_height * 0.75)))
        lines = [
            f'{self.tr(label)}: {self.title_block.get(key, "")}'
            for key, label in TITLE_BLOCK_FIELDS
        ]

        # Ajusta la fuente solo si el texto no cabe en el ancho útil de la
        # hoja; normalmente el cajetín crece horizontalmente.
        while font_size > 6:
            font = _qfont('Menlo', font_size)
            if self.print_mode:
                font = QFont(font)
                font.setPixelSize(font_size)
            text_width = max(
                QFontMetricsF(font).horizontalAdvance(line) for line in lines
            )
            if text_width + 16 <= inner_rect.width():
                break
            font_size -= 1

        font = _qfont('Menlo', font_size)
        if self.print_mode:
            font = QFont(font)
            font.setPixelSize(font_size)
        text_width = max(
            QFontMetricsF(font).horizontalAdvance(line) for line in lines
        )
        base_width = min(420.0, inner_rect.width() * 0.45)
        block_width = min(inner_rect.width(), max(base_width, text_width + 16))
        block = QRectF(
            inner_rect.right() - block_width,
            inner_rect.bottom() - block_height,
            block_width,
            block_height,
        )
        return block, font, lines

    def title_block_rect(self) -> QRectF:
        return self._title_block_layout(self._paper_inner_rect())[0]

    def set_paper_format(self, paper_format: str) -> bool:
        if paper_format not in PAPER_FORMATS:
            return False
        self.paper_format = paper_format
        self.setSceneRect(self.sceneRect().united(
            self.paper_rect().adjusted(-100, -100, 100, 100)))
        self.update()
        return True

    def set_paper_line_width(self, width: float):
        self.paper_line_width = max(0.5, min(10.0, float(width)))
        self.update()

    def set_paper_visible(self, visible: bool):
        self.paper_visible = bool(visible)
        self.update()

    def set_title_block_visible(self, visible: bool):
        self.title_block_visible = bool(visible)
        self.update()

    def set_print_mode(self, enabled: bool, monochrome: bool = True):
        self.print_mode = bool(enabled)
        self.print_monochrome = bool(monochrome)
        self.update()

    def set_title_block(self, values: dict):
        values = values if isinstance(values, dict) else {}
        self.title_block = {
            key: str(values.get(key, '') or '')
            for key, _ in TITLE_BLOCK_FIELDS
        }
        self.update()

    def _draw_title_block(self, painter: QPainter, inner_rect: QRectF):
        if not self.title_block_visible:
            return
        if inner_rect.width() <= 0 or inner_rect.height() <= 0:
            return

        block, font, lines = self._title_block_layout(inner_rect)
        border_color = QColor('#000000' if self.print_mode else
                              COLORS.get('comp_sel', COLORS.get('panel_brd', COLORS['grid_line'])))
        panel_color = QColor('#ffffff' if self.print_mode else
                             COLORS.get('panel', COLORS.get('bg', '#000000')))
        panel_color.setAlpha(235)

        pen = QPen(border_color, self.paper_line_width)
        painter.setPen(pen)
        painter.setBrush(QBrush(panel_color))
        painter.drawRect(block)
        # El cajetín se dibuja en coordenadas de escena, por lo que una fuente
        # pequeña desaparece al ajustar la hoja completa al viewport.
        row_height = block.height() / len(TITLE_BLOCK_FIELDS)
        painter.setFont(font)
        for index, line in enumerate(lines):
            row = QRectF(block.left(), block.top() + index * row_height,
                         block.width(), row_height)
            if index:
                painter.setPen(QPen(border_color, self.paper_line_width))
                painter.drawLine(row.topLeft(), row.topRight())
            painter.setPen(QPen(QColor('#000000' if self.print_mode else
                                       COLORS.get('text', '#FFFFFF')), 0))
            painter.drawText(
                row.adjusted(8, 0, -8, 0),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                line,
            )

    def _draw_paper(self, painter: QPainter, rect: QRectF):
        if not self.paper_visible:
            return
        paper = self.paper_rect()
        if not rect.intersects(paper):
            return

        painter.save()
        if self.print_mode:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(QColor('#ffffff')))
            painter.drawRect(paper)

        if not self.print_mode:
            border = QPen(QColor(COLORS.get('panel_brd', COLORS['grid_line'])),
                          self.paper_line_width)
            painter.setPen(border)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(paper)

        margin_rect = self._paper_inner_rect()
        margin_pen = QPen(
            QColor('#000000' if self.print_mode else
                   COLORS.get('comp_sel', COLORS['panel_brd'])),
            self.paper_line_width)
        margin_pen.setStyle(Qt.PenStyle.SolidLine if self.print_mode
                            else Qt.PenStyle.DashLine)
        painter.setPen(margin_pen)
        painter.drawRect(margin_rect)
        self._draw_title_block(painter, margin_rect)
        painter.restore()

    def drawBackground(self, painter: QPainter, rect: QRectF):
        if self.print_mode:
            self._draw_paper(painter, rect)
            return
        super().drawBackground(painter, rect)

        left   = int(math.floor(rect.left()  / GRID_SIZE)) * GRID_SIZE
        right  = int(math.ceil(rect.right()  / GRID_SIZE)) * GRID_SIZE
        top    = int(math.floor(rect.top()   / GRID_SIZE)) * GRID_SIZE
        bottom = int(math.ceil(rect.bottom() / GRID_SIZE)) * GRID_SIZE

        pen_minor, pen_major = self._grid_pens()

        lines_minor = []
        lines_major = []
        x = left
        while x <= right:
            idx = round(x / GRID_SIZE)
            line = QLineF(x, rect.top(), x, rect.bottom())
            if idx % 5 == 0:
                lines_major.append(line)
            else:
                lines_minor.append(line)
            x += GRID_SIZE

        y = top
        while y <= bottom:
            idx = round(y / GRID_SIZE)
            line = QLineF(rect.left(), y, rect.right(), y)
            if idx % 5 == 0:
                lines_major.append(line)
            else:
                lines_minor.append(line)
            y += GRID_SIZE

        painter.setPen(pen_minor)
        painter.drawLines(lines_minor)
        painter.setPen(pen_major)
        painter.drawLines(lines_major)
        self._draw_paper(painter, rect)

    # ── Punto de unión (junction dot) ───────────
    def drawForeground(self, painter: QPainter, rect: QRectF):
        super().drawForeground(painter, rect)

        if self.print_mode:
            return

        # Cuenta extremos LIBRES de cables (sin componente conectado) por
        # posición snapeada. Cuando concurren más de 3 en un mismo punto
        # se dibuja un dot tipo pin para indicar la unión cable-cable.
        SNAP = 5
        counts: Dict[Tuple[int, int], int] = {}
        positions: Dict[Tuple[int, int], QPointF] = {}
        for w in self.wires:
            line = w.line()
            if w.start_comp is None:
                p = line.p1()
                key = (round(p.x() / SNAP) * SNAP, round(p.y() / SNAP) * SNAP)
                counts[key] = counts.get(key, 0) + 1
                positions.setdefault(key, p)
            if w.end_comp is None:
                p = line.p2()
                key = (round(p.x() / SNAP) * SNAP, round(p.y() / SNAP) * SNAP)
                counts[key] = counts.get(key, 0) + 1
                positions.setdefault(key, p)

        color = QColor(COLORS['pin'])
        painter.setPen(QPen(color, 2))
        painter.setBrush(QBrush(color))
        for key, n in counts.items():
            if n > 3:
                painter.drawEllipse(positions[key], PIN_RADIUS, PIN_RADIUS)

        # Un extremo libre que no toca otro cable ni un pin suele indicar un
        # cable incompleto. Se marca discretamente para detectarlo al editar.
        warning = QColor('#e59a3a')
        painter.setPen(QPen(warning, 2))
        for wire in self.wires:
            for point, connected in ((wire.line().p1(), wire.start_comp),
                                     (wire.line().p2(), wire.end_comp)):
                if connected is None and not self._free_endpoint_connected(point, wire):
                    painter.drawLine(point + QPointF(-4, -4), point + QPointF(4, 4))
                    painter.drawLine(point + QPointF(-4, 4), point + QPointF(4, -4))

        # Bajo el cursor se iluminan todos los pines de la misma red. Es un
        # feedback ligero y no altera el esquema ni el netlist.
        if self._hover_pos is not None:
            net = self._net_at(self._hover_pos)
            if net:
                nets = self.extract_netlist()
                painter.setPen(QPen(QColor(COLORS['comp_sel']), 2))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                for comp in self.components:
                    for index, point in enumerate(comp.all_pin_positions_scene(), 1):
                        if nets.get(f'{comp.name}__p{index}') == net:
                            painter.drawEllipse(point, PIN_RADIUS + 4, PIN_RADIUS + 4)

    @staticmethod
    def _point_on_segment(point: QPointF, a: QPointF, b: QPointF, tolerance: float = 10.0) -> bool:
        dx, dy = b.x() - a.x(), b.y() - a.y()
        length_sq = dx * dx + dy * dy
        if length_sq == 0:
            return QLineF(point, a).length() < tolerance
        t = max(0.0, min(1.0, ((point.x() - a.x()) * dx + (point.y() - a.y()) * dy) / length_sq))
        projected = QPointF(a.x() + t * dx, a.y() + t * dy)
        return QLineF(point, projected).length() < tolerance

    def _free_endpoint_connected(self, point: QPointF, owner: WireItem) -> bool:
        for comp in self.components:
            if any(QLineF(point, pin).length() < 12 for pin in comp.all_pin_positions_scene()):
                return True
        for wire in self.wires:
            if wire is owner:
                continue
            line = wire.line()
            if self._point_on_segment(point, line.p1(), line.p2()):
                return True
        return False

    def _net_at(self, pos: QPointF) -> Optional[str]:
        closest = (16.0, None)
        for comp in self.components:
            for index, point in enumerate(comp.all_pin_positions_scene(), 1):
                distance = QLineF(pos, point).length()
                if distance < closest[0]:
                    closest = (distance, f'{comp.name}__p{index}')
        if closest[1] is None:
            return None
        return self.extract_netlist().get(closest[1])

    # ── Modo ────────────────────────────────────
    def set_mode(self, mode: str):
        self._mode = mode
        if not mode.startswith('place') and mode != 'wire':
            if self._wire_preview:
                self.removeItem(self._wire_preview)
                self._wire_preview = None
            self._wire_start = None

    def _snap_to_pin_or_grid(self, pos: QPointF, threshold: float = 16.0) -> QPointF:
        """
        Si el cursor está a menos de `threshold` px de cualquier pin,
        retorna la posición exacta del pin. Si no, snapea a grilla.
        """
        best_dist = threshold
        best_pt   = None
        for comp in self.components:
            for pt in comp.all_pin_positions_scene():
                dx = pos.x() - pt.x()
                dy  = pos.y() - pt.y()
                d  = (dx*dx + dy*dy) ** 0.5
                if d  < best_dist:
                    best_dist = d
                    best_pt   = pt
        if best_pt is not None:
            return best_pt
        if not self.snap_enabled:
            return QPointF(pos)
        return QPointF(round(pos.x()/GRID_SIZE)*GRID_SIZE,
                        round(pos.y()/GRID_SIZE)*GRID_SIZE)

    def _find_component_at_pin(self, pos: QPointF, threshold: float = 16.0):
        """Encuentra el componente y índice de pin más cercano a pos"""
        best_dist = threshold
        best_comp = None
        best_pin_idx = 0
        
        for comp in self.components:
            for idx, pt in enumerate(comp.all_pin_positions_scene()):
                dx = pos.x() - pt.x()
                dy = pos.y() - pt.y()
                d = (dx*dx + dy*dy) ** 0.5
                if d < best_dist:
                    best_dist = d
                    best_comp = comp
                    best_pin_idx = idx
        
        return (best_comp, best_pin_idx) if best_comp else (None, 0)

    # ── Colocar componente ───────────────────────
    def place_component(self, comp_type: str, pos: QPointF,
                        name: str = '', value: float = 0.0, unit: str = '',
                        node1: str = '', node2: str = '', node3: str = '',
                        tl082_unit: str = '') -> 'ComponentItem | None':
        # ── Instancia de subcircuito: comp_type viene como "SUBCKT:Nombre" ──
        _subckt_name = ''
        if comp_type.startswith('SUBCKT:'):
            _subckt_name = comp_type.split(':', 1)[1]
            comp_type = 'SUBCKT'

        # ── Selector de unidad para CIs duales (TL082) ──────────────────
        _tl082_unit = 'A'
        if comp_type == 'TL082' and not name:
            from kirho.ui.dialogs.tl082_unit_dialog import TL082UnitDialog
            _parent = self.views()[0].parent() if self.views() else None
            dlg = TL082UnitDialog(_parent)
            if dlg.exec() != QDialog.DialogCode.Accepted:
                return None
            _tl082_unit = dlg.selected_unit
        elif tl082_unit:
            _tl082_unit = tl082_unit

        if name:
            if self._component_name_exists(name):
                suffix = ''
                if comp_type == 'TL082':
                    prefix = self._NAME_PREFIXES.get(comp_type, comp_type)
                    m = re.match(rf'^{re.escape(prefix)}\d+(.+)$', name)
                    suffix = m.group(1) if m else _tl082_unit
                name = self._next_component_name(comp_type, suffix=suffix)
            else:
                self._bump_component_counter_from_name(comp_type, name)

        if not name:
            # TL082: el nombre incluye la letra de unidad (U1A / U1B).
            suffix = _tl082_unit if comp_type == 'TL082' else ''
            name = self._next_component_name(comp_type, suffix=suffix)

        units = {'R': 'Ω', 'V': 'V', 'VAC': 'V', 'I': 'A', 'C': 'F', 'L': 'H',
                 'D': 'A', 'LED': 'A', 'LAMP': 'V', 'BJT_NPN': 'hFE', 'BJT_PNP': 'hFE',
                 'NMOS': 'A/V²', 'PMOS': 'A/V²', 'OPAMP': 'V/V', 'TL082': 'V/V',
                 'FGEN': 'V', 'MULTIMETER': ''}
        if not unit:
            unit = units.get(comp_type, '')

        # NOTA: el default de LED es 0.0 para que el `Is` lo determine el COLOR
        # (ver build_engine_components_for_item).  Si el usuario escribe un Is
        # positivo en la propiedad, se usa ese valor en lugar del preset.
        defaults = {'R': 1000.0, 'POT': 10_000.0, 'V': 5.0, 'VAC': 120.0,
                    'I': 0.001, 'C': 1e-6, 'L': 1e-3,
                    'D': 1e-14, 'LED': 0.0, 'LAMP': 3.0, 'BJT_NPN': 100.0, 'BJT_PNP': 100.0,
                    'NMOS': 1e-3, 'PMOS': 1e-3, 'OPAMP': 1e5, 'TL082': 1e5,
                    'XFMR': 1.0, 'BRIDGE': 0.7,
                    'SPDT3': 0.0, 'DPDT': 0.0,
                    'LOGIC_STATE': 0.0, 'CLK': 0.0,
                    'NET_LABEL_IN': 0.0, 'NET_LABEL_OUT': 0.0,
                    'FGEN': 5.0, 'MULTIMETER': 0.0}
        _stateful = ('LOGIC_STATE', 'CLK', 'NET_LABEL_IN', 'NET_LABEL_OUT',
                     'OSC', 'MULTIMETER', 'PORT', 'SUBCKT')
        if value == 0.0 and comp_type not in _stateful:
            value = defaults.get(comp_type, 1.0)
        elif comp_type in _stateful:
            value = defaults.get(comp_type, 0.0)

        item = ComponentItem(comp_type, name, value, unit, node1, node2, node3)
        if comp_type == 'NOT':
            item.dig_inputs = 1
        if comp_type == 'MUX2':
            item.dig_inputs = 2   # 2 datos (I0,I1); SEL es p4 aparte
        if comp_type == 'FGEN':
            # Por convención del panel frontal estilo Multisim, el FGEN
            # interpreta su amplitud como tensión de pico.
            item.ac_mode = 'peak'
            item.frequency = 1000.0  # 1 kHz default — más útil que 60 Hz
            item.fgen_waveform = 'sin'
            item.fgen_offset = 0.0
            item.fgen_duty = 0.5
        if comp_type in ('NET_LABEL_IN', 'NET_LABEL_OUT'):
            # El sheet_label es el nombre de red inalámbrico.
            # Por defecto 'NET' para que el usuario lo renombre a algo significativo.
            # Dos net labels con el mismo sheet_label quedan eléctricamente unidos.
            item.sheet_label = 'NET'
        if comp_type == 'TL082':
            item.tl082_unit = _tl082_unit
        if comp_type == 'PORT':
            item.port_name = 'IN'
            item.port_dir = 'in'
        if comp_type == 'SUBCKT':
            item.subckt_name = _subckt_name
            self._init_subckt_appearance(item)
        snap_x = round(pos.x() / GRID_SIZE) * GRID_SIZE if self.snap_enabled else pos.x()
        snap_y = round(pos.y() / GRID_SIZE) * GRID_SIZE if self.snap_enabled else pos.y()
        self.addItem(item)
        item.setPos(snap_x, snap_y)
        self.components.append(item)
        return item

    def _init_subckt_appearance(self, item):
        """Rellena ic_pins / ic_label de una instancia SUBCKT desde su
        definición en la biblioteca (si los overrides aún no existen)."""
        from kirho.subcircuit_manager import SUBCIRCUIT_MANAGER
        defn = SUBCIRCUIT_MANAGER.get(item.subckt_name)
        if not defn:
            return
        if not item.ic_label:
            app = defn.get('appearance', {})
            item.ic_label = app.get('label', item.subckt_name)
            if not item.ic_body_color:
                item.ic_body_color = app.get('body_color', '')
            if not item.ic_text_color:
                item.ic_text_color = app.get('text_color', '')
        if not item.ic_pins:
            app = defn.get('appearance', {})
            ap_pins = app.get('pins')
            ports = defn.get('ports', [])
            if ap_pins and len(ap_pins) == len(ports):
                item.ic_pins = [dict(p) for p in ap_pins]
            else:
                # Reparto por defecto: in→izq, out→der, resto alterna.
                item.ic_pins = []
                for p in ports:
                    d = p.get('dir', 'in')
                    side = 'left' if d == 'in' else ('right' if d == 'out'
                                                     else 'left')
                    item.ic_pins.append({'name': p.get('name', '?'),
                                         'side': side})

    # ── Eventos de mouse ────────────────────────

    def _snap_to_pin_or_grid_with_comp(self, pos: QPointF, threshold: float = 16.0):
        """Igual que _snap_to_pin_or_grid, pero devuelve también (comp, pin_idx)."""
        best_dist = threshold
        best_pt = None
        best_comp = None
        best_pin_idx = 0
        
        for comp in self.components:
            for idx, pt in enumerate(comp.all_pin_positions_scene()):
                dx = pos.x() - pt.x()
                dy = pos.y() - pt.y()
                d = (dx*dx + dy*dy) ** 0.5
                if d < best_dist:
                    best_dist = d
                    best_pt = pt
                    best_comp = comp
                    best_pin_idx = idx
                    
        if best_pt is not None:
            return best_pt, best_comp, best_pin_idx
        if not self.snap_enabled:
            return QPointF(pos), None, 0
        return QPointF(round(pos.x()/GRID_SIZE)*GRID_SIZE,
                       round(pos.y()/GRID_SIZE)*GRID_SIZE), None, 0

    def mousePressEvent(self, event):
        pos = event.scenePos()

        if self._mode.startswith('place_'):
            comp_type = self._mode.split('_', 1)[1]
            self.push_undo()
            placed = self.place_component(comp_type, pos)
            if placed is not None:
                self.status_message.emit(
                    self.tr("Component {type} placed at ({x:.0f}, {y:.0f})").format(
                        type=comp_type, x=pos.x(), y=pos.y()))
            return

        if self._mode == 'wire':
            snap, comp, pin_idx = self._snap_to_pin_or_grid_with_comp(pos)
            
            if self._wire_start is None:
                # Buscar componente/pin en posición inicial
                start_comp, start_pin = self._find_component_at_pin(snap)
                self._wire_start = snap
                self._wire_start_comp = start_comp
                self._wire_start_pin = start_pin or 0
                self._wire_preview = WireItem(snap, snap)
                self.addItem(self._wire_preview)
            else:
                # Finalizar cable
                end_comp, end_pin = self._find_component_at_pin(snap)
                self.push_undo()
                wire = WireItem(
                    self._wire_start, snap,
                    start_comp=self._wire_start_comp,
                    start_pin_idx=self._wire_start_pin,
                    end_comp=end_comp,
                    end_pin_idx=end_pin or 0
                )
                self.addItem(wire)
                self.wires.append(wire)
                # Preparar para siguiente cable
                self._wire_start = snap
                self._wire_start_comp = end_comp
                self._wire_start_pin = end_pin or 0
                if self._wire_preview:
                    self.removeItem(self._wire_preview)
                self._wire_preview = WireItem(snap, snap)
                self.addItem(self._wire_preview)
                self._wire_start = snap
                self._wire_start_comp = comp
                self._wire_start_pin = pin_idx
                self.status_message.emit("Cable colocado")
            return

        super().mousePressEvent(event)

        # Preparar arrastre grupal: si el usuario hace click sobre un ítem
        # ya seleccionado en modo 'select', registramos los cables que deben
        # trasladarse manualmente junto con los componentes seleccionados.
        self._group_drag_active = False
        self._group_drag_start_pos = None
        self._group_drag_wires = []
        if (self._mode == 'select'
                and event.button() == Qt.MouseButton.LeftButton):
            selected_items = self.selectedItems()
            clicked_items  = self.items(pos)
            clicked_selected = next(
                (it for it in clicked_items if it in selected_items), None)
            if clicked_selected is not None:
                selected_comps = {
                    it for it in selected_items if isinstance(it, ComponentItem)}
                wires_in_sel = {
                    it for it in selected_items if isinstance(it, WireItem)}
                wires_to_track = set(wires_in_sel)
                for w in self.wires:
                    if w.start_comp in selected_comps or w.end_comp in selected_comps:
                        wires_to_track.add(w)

                # Mapa de uniones libres entre cables: posición (snap) →
                # lista de (cable, extremo). Solo extremos sin componente
                # asociado pueden formar junction "cable-cable".
                SNAP = 5
                def _jkey(p: QPointF):
                    return (round(p.x() / SNAP) * SNAP,
                            round(p.y() / SNAP) * SNAP)
                junction_map: Dict[Tuple[int, int], List[Tuple[WireItem, str]]] = {}
                for w in self.wires:
                    if w.start_comp is None:
                        junction_map.setdefault(_jkey(w.line().p1()), []) \
                            .append((w, 'p1'))
                    if w.end_comp is None:
                        junction_map.setdefault(_jkey(w.line().p2()), []) \
                            .append((w, 'p2'))

                tracked_by_wire: Dict[WireItem, dict] = {}
                for w in wires_to_track:
                    line = w.line()
                    p1_free = (w.start_comp is None)
                    p2_free = (w.end_comp is None)
                    p1_in_sel = (w.start_comp in selected_comps)
                    p2_in_sel = (w.end_comp in selected_comps)
                    wire_selected = w in wires_in_sel
                    # Trasladamos a mano cualquier extremo libre cuyo cable
                    # forme parte del grupo (ya sea por estar seleccionado
                    # o por tener su otro extremo unido a un comp del grupo).
                    translate_p1 = p1_free and (wire_selected or p2_in_sel)
                    translate_p2 = p2_free and (wire_selected or p1_in_sel)
                    if translate_p1 or translate_p2:
                        tracked_by_wire[w] = {
                            'p1': QPointF(line.p1()),
                            'p2': QPointF(line.p2()),
                            'translate_p1': translate_p1,
                            'translate_p2': translate_p2,
                        }

                # Propagación por uniones: si un extremo libre se traslada,
                # todos los demás extremos libres en esa misma posición
                # deben moverse con él para que la unión no se rompa.
                changed = True
                while changed:
                    changed = False
                    moving_keys = set()
                    for w, info in tracked_by_wire.items():
                        if info['translate_p1']:
                            moving_keys.add(_jkey(info['p1']))
                        if info['translate_p2']:
                            moving_keys.add(_jkey(info['p2']))
                    for key in moving_keys:
                        for (w, end) in junction_map.get(key, []):
                            if w not in tracked_by_wire:
                                line = w.line()
                                tracked_by_wire[w] = {
                                    'p1': QPointF(line.p1()),
                                    'p2': QPointF(line.p2()),
                                    'translate_p1': False,
                                    'translate_p2': False,
                                }
                            info = tracked_by_wire[w]
                            if end == 'p1' and not info['translate_p1']:
                                info['translate_p1'] = True
                                changed = True
                            if end == 'p2' and not info['translate_p2']:
                                info['translate_p2'] = True
                                changed = True

                tracked = [{'wire': w, **info}
                           for w, info in tracked_by_wire.items()]
                if tracked or selected_comps:
                    # Snapshot previo al drag → Ctrl+Z revierte posiciones.
                    self.push_undo()
                    self._group_drag_active = True
                    self._group_drag_start_pos = QPointF(pos)
                    self._group_drag_wires = tracked

        # Emitir componente seleccionado
        items = self.selectedItems()
        if items and isinstance(items[0], ComponentItem):
            self.component_selected.emit(items[0])
        else:
            self.component_selected.emit(None)

    def mouseMoveEvent(self, event):
        self._hover_pos = QPointF(event.scenePos())
        self.update()
        if self._mode == 'wire' and self._wire_start and self._wire_preview:
            pos  = event.scenePos()
            snap = self._snap_to_pin_or_grid(pos)
            self._wire_preview.setLine(QLineF(self._wire_start, snap))
        super().mouseMoveEvent(event)

        # Trasladar extremos libres de cables durante un arrastre grupal,
        # snapeando el delta a la grilla para mantener alineación con pines.
        if self._group_drag_active and self._group_drag_wires \
                and self._group_drag_start_pos is not None:
            delta = event.scenePos() - self._group_drag_start_pos
            dx = round(delta.x() / GRID_SIZE) * GRID_SIZE
            dy = round(delta.y() / GRID_SIZE) * GRID_SIZE
            for info in self._group_drag_wires:
                wire = info['wire']
                p1 = info['p1']
                p2 = info['p2']
                new_p1 = QPointF(p1.x() + dx, p1.y() + dy) if info['translate_p1'] else QPointF(p1)
                new_p2 = QPointF(p2.x() + dx, p2.y() + dy) if info['translate_p2'] else QPointF(p2)
                wire.setLine(QLineF(new_p1, new_p2))
                # Si algún extremo está unido a un componente, dejar que el
                # pin actual gobierne esa coordenada.
                if not info['translate_p1'] or not info['translate_p2']:
                    wire.update_from_pins()

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        if self._group_drag_active:
            self._group_drag_active = False
            self._group_drag_start_pos = None
            self._group_drag_wires = []

    @staticmethod
    def _cycle_switch(item):
        """Avanza un switch binario o las tres posiciones del ON-OFF-ON."""
        if item.comp_type == 'SPDT3':
            item.value = {0: -1.0, -1: 1.0, 1: 0.0}.get(
                int(round(item.value)), 0.0)
        else:
            item.value = 0.0 if item.value else 1.0
        item.update()

    @staticmethod
    def _switch_key_target(item, pressed):
        if item.comp_type != 'SPDT3' or not pressed:
            return None
        for attr, target in (
                ('switch_on1_key', -1.0),
                ('switch_off_key', 0.0),
                ('switch_on2_key', 1.0)):
            if getattr(item, attr, '').strip().upper() == pressed:
                return target
        return None

    def mouseDoubleClickEvent(self, event):
        if (self._mode == 'select' and self.paper_visible
                and self.title_block_visible
                and self.title_block_rect().contains(event.scenePos())):
            self.title_block_edit_requested.emit()
            return

        items = self.items(event.scenePos())
        if self._mode == 'select':
            wire = next((item for item in items if isinstance(item, WireItem)), None)
            if wire is not None:
                if self._toggle_wire_vertex(wire, event.scenePos()):
                    return
        for item in items:
            if isinstance(item, ComponentItem):
                if item.comp_type == 'LOGIC_STATE':
                    # Toggle 0↔1 con doble-click
                    self.push_undo()
                    item.value = 0.0 if item.value else 1.0
                    item.update()
                    self.logic_state_toggled.emit(item)
                    return
                if item.comp_type == 'CLK':
                    # Doble-click conmuta manualmente y detiene la oscilación
                    # automática (entra en modo manual como un LOGIC_STATE).
                    self.push_undo()
                    item.clk_running = False
                    item.value = 0.0 if item.value else 1.0
                    item.update()
                    self.logic_state_toggled.emit(item)
                    return
                if item.comp_type in ('SPST', 'SPDT', 'SPDT3', 'DPDT'):
                    # Interruptores mecánicos: doble clic cambia de posición.
                    self.push_undo()
                    self._cycle_switch(item)
                    self.logic_state_toggled.emit(item)
                    return
                if item.comp_type in ComponentItem.INSTRUMENT_TYPES:
                    self._open_instrument_panel(item)
                    return
                if item.comp_type == 'PORT':
                    from kirho.ui.dialogs.subcircuit_edit_dialog import PortEditDialog
                    dlg = PortEditDialog(item, self.views()[0] if self.views() else None)
                    if dlg.exec() == QDialog.DialogCode.Accepted:
                        self.push_undo()
                        dlg.apply()
                    return
                if item.comp_type == 'SUBCKT':
                    from kirho.ui.dialogs.subcircuit_edit_dialog import SubcircuitAppearanceDialog
                    dlg = SubcircuitAppearanceDialog(item, self.views()[0] if self.views() else None)
                    if dlg.exec() == QDialog.DialogCode.Accepted:
                        self.push_undo()
                        dlg.apply()
                    return
                self._edit_component(item)
                return
        super().mouseDoubleClickEvent(event)

    def _toggle_wire_vertex(self, wire: WireItem, pos: QPointF) -> bool:
        """Convierte un cable diagonal en dos tramos ortogonales.

        Un doble clic sobre el vértice compartido de dos tramos lo vuelve a
        unir. Para recorridos más largos, el modo Wire ya permite colocar
        tantos tramos manuales como se necesiten.
        """
        line = wire.line()
        p1, p2 = line.p1(), line.p2()
        if abs(p1.x() - p2.x()) < 1 or abs(p1.y() - p2.y()) < 1:
            return self._remove_wire_vertex(pos)

        elbow_a = QPointF(p1.x(), p2.y())
        elbow_b = QPointF(p2.x(), p1.y())
        elbow = elbow_a if QLineF(pos, elbow_a).length() <= QLineF(pos, elbow_b).length() else elbow_b
        self.push_undo()
        first = WireItem(p1, elbow, wire.start_comp, wire.start_pin_idx)
        second = WireItem(elbow, p2, end_comp=wire.end_comp, end_pin_idx=wire.end_pin_idx)
        self.removeItem(wire)
        self.wires.remove(wire)
        for item in (first, second):
            self.addItem(item)
            self.wires.append(item)
            item.setSelected(True)
        self.status_message.emit(self.tr("Orthogonal wire vertex added"))
        return True

    def _remove_wire_vertex(self, pos: QPointF) -> bool:
        ends = []
        for wire in self.wires:
            if wire.start_comp is None and QLineF(pos, wire.line().p1()).length() < 12:
                ends.append((wire, 'start'))
            if wire.end_comp is None and QLineF(pos, wire.line().p2()).length() < 12:
                ends.append((wire, 'end'))
        if len(ends) != 2 or ends[0][0] is ends[1][0]:
            return False
        (first, first_end), (second, second_end) = ends
        a = first.line().p2() if first_end == 'start' else first.line().p1()
        b = second.line().p2() if second_end == 'start' else second.line().p1()
        a_comp = first.end_comp if first_end == 'start' else first.start_comp
        b_comp = second.end_comp if second_end == 'start' else second.start_comp
        a_pin = first.end_pin_idx if first_end == 'start' else first.start_pin_idx
        b_pin = second.end_pin_idx if second_end == 'start' else second.start_pin_idx
        self.push_undo()
        merged = WireItem(a, b, a_comp, a_pin, b_comp, b_pin)
        for item in (first, second):
            self.removeItem(item)
            self.wires.remove(item)
        self.addItem(merged)
        self.wires.append(merged)
        merged.setSelected(True)
        self.status_message.emit(self.tr("Wire vertex removed"))
        return True

    def handle_switch_key(self, event) -> bool:
        """Aplica una tecla de switch y devuelve si fue consumida."""
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            return False
        key = event.key()
        pressed = 'SPACE' if key == Qt.Key.Key_Space else event.text().upper()
        direct_switches = []
        switches = [item for item in self.components
                    if item.comp_type in ('SPST', 'SPDT', 'DPDT')
                    and getattr(item, 'switch_key', '').upper() == pressed]
        for item in self.components:
            target = self._switch_key_target(item, pressed)
            if target is not None:
                direct_switches.append((item, target))
        if not (switches or direct_switches):
            return False
        self.push_undo()
        for item in switches:
            self._cycle_switch(item)
        for item, target in direct_switches:
            item.value = target
            item.update()
        self.logic_state_toggled.emit(
            switches[0] if switches else direct_switches[0][0])
        event.accept()
        return True

    def keyPressEvent(self, event):
        if self.handle_switch_key(event):
            return

        mod = event.modifiers()
        has_ctrl = bool(mod & Qt.KeyboardModifier.ControlModifier)
        key = event.key()

        if key in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            sel = list(self.selectedItems())
            if sel:
                self.push_undo()
                for item in sel:
                    if isinstance(item, ComponentItem) and item in self.components:
                        self.components.remove(item)
                    elif isinstance(item, WireItem) and item in self.wires:
                        self.wires.remove(item)
                    self.removeItem(item)
                self.status_message.emit(self.tr("Selection deleted"))
        elif has_ctrl and key == Qt.Key.Key_Z:
            if self.undo():
                self.status_message.emit(self.tr("Action undone (Ctrl+Z)"))
            else:
                self.status_message.emit(self.tr("Nothing to undo"))
        elif has_ctrl and key == Qt.Key.Key_C:
            if self.copy_selected():
                self.status_message.emit(self.tr("Selection copied (Ctrl+C)"))
        elif has_ctrl and key == Qt.Key.Key_X:
            if self.cut_selected():
                self.status_message.emit(self.tr("Selection cut (Ctrl+X)"))
        elif has_ctrl and key == Qt.Key.Key_V:
            if self.paste():
                self.status_message.emit(self.tr("Pasted (Ctrl+V)"))
            else:
                self.status_message.emit(self.tr("Clipboard is empty"))
        elif has_ctrl and key in (Qt.Key.Key_Plus, Qt.Key.Key_Equal):
            # Ctrl++ → rotar 90° a la derecha (horario).
            # Aceptamos también Ctrl+= para teclados donde + requiere Shift.
            if self.rotate_selected(delta=90):
                self.status_message.emit("Rotado 90° a la derecha (Ctrl++)")
            event.accept()
        elif has_ctrl and key in (Qt.Key.Key_Minus, Qt.Key.Key_Underscore):
            # Ctrl+- → rotar 90° a la izquierda (antihorario).
            if self.rotate_selected(delta=-90):
                self.status_message.emit("Rotado 90° a la izquierda (Ctrl+-)")
            event.accept()
        elif key == Qt.Key.Key_Escape:
            if self._wire_preview:
                self.removeItem(self._wire_preview)
                self._wire_preview = None
            self._wire_start = None
            self.set_mode('select')
        else:
            super().keyPressEvent(event)

    def update_wires_for_component(self, comp: 'ComponentItem'):
        """Actualiza todos los cables conectados al componente dado."""
        for wire in self.wires:
            if wire.start_comp is comp or wire.end_comp is comp:
                wire.update_from_pins()

    # ── Serialización para undo / clipboard ─────────────────────────────

    # Atributos opcionales que persistimos por componente. Para mantener el
    # snapshot pequeño, sólo se guardan si el componente los tiene.
    _SNAP_ATTRS = (
        'sheet_label', 'pot_wiper', 'led_color',
        'frequency', 'phase_deg', 'ac_mode',
        'z_real', 'z_imag', 'z_mag', 'z_phase', 'z_mode',
        'xfmr_ratio', 'xfmr_imax', 'bridge_vf', 'relay_activation_voltage',
        'node4', 'node5', 'node6', 'tl082_unit', 'clk_running', 'switch_key',
        'switch_on1_key', 'switch_off_key', 'switch_on2_key',
        'timer_nodes',
        'dig_inputs', 'dig_tpd_ns', 'dig_clk', 'dig_analog_node',
        'dig_bits', 'dig_bits_adc', 'dig_vref',
        # Instrumentos
        'fgen_waveform', 'fgen_offset', 'fgen_duty',
        'osc_time_div', 'osc_v_div_a', 'osc_v_div_b',
        'osc_pos_a', 'osc_pos_b',
        'osc_trig_level', 'osc_trig_source', 'osc_trig_edge', 'osc_trig_mode',
        'osc_hw_config',
        'meter_quantity', 'meter_coupling',
    )

    def _serialize_component(self, c: 'ComponentItem') -> dict:
        e = {
            'type':  c.comp_type, 'name': c.name, 'value': c.value,
            'unit':  c.unit, 'node1': c.node1, 'node2': c.node2,
            'node3': c.node3,
            'x':     c.pos().x(), 'y': c.pos().y(),
            'angle': c._angle,
            'flip_x': c._flip_x,
            'flip_y': c._flip_y,
        }
        for attr in self._SNAP_ATTRS:
            if hasattr(c, attr):
                e[attr] = getattr(c, attr)
        if hasattr(c, 'dig_input_nodes'):
            e['dig_input_nodes'] = list(c.dig_input_nodes or [])
        if hasattr(c, 'dig_input_neg'):
            e['dig_input_neg'] = list(c.dig_input_neg or [])
        return e

    def _serialize_wire(self, w: WireItem,
                        comp_filter: Optional[set] = None) -> dict:
        line = w.line()
        sn = w.start_comp.name if (
            w.start_comp and (comp_filter is None
                              or w.start_comp.name in comp_filter)) else None
        en = w.end_comp.name if (
            w.end_comp and (comp_filter is None
                            or w.end_comp.name in comp_filter)) else None
        return {
            'x1': line.x1(), 'y1': line.y1(),
            'x2': line.x2(), 'y2': line.y2(),
            'start': sn, 'spi': w.start_pin_idx,
            'end':   en, 'epi': w.end_pin_idx,
        }

    def _snapshot(self) -> dict:
        """Estado serializado completo de la hoja, listo para undo."""
        return {
            'components': [self._serialize_component(c) for c in self.components],
            'wires':      [self._serialize_wire(w) for w in self.wires],
            'counter':    dict(self._comp_counter),
        }

    def _instantiate_component(self, c: dict,
                               offset_x: float = 0.0, offset_y: float = 0.0,
                               keep_name: bool = True) -> 'ComponentItem':
        item = self.place_component(
            c['type'],
            QPointF(c['x'] + offset_x, c['y'] + offset_y),
            name=(c['name'] if keep_name else ''),
            value=c.get('value', 0.0),
            unit=c.get('unit', ''),
            node1=c.get('node1', ''),
            node2=c.get('node2', ''),
            node3=c.get('node3', ''),
            tl082_unit=c.get('tl082_unit', 'A'))
        if item is None:
            # No debería ocurrir en restore/paste (tl082_unit ya se pasa),
            # pero por seguridad devolvemos un item vacío.
            return self.place_component('NODE', QPointF(c['x'], c['y']))
        angle = c.get('angle', 0)
        flip_x = bool(c.get('flip_x', False))
        flip_y = bool(c.get('flip_y', False))
        if angle or flip_x or flip_y:
            item._angle = angle
            item._flip_x = flip_x
            item._flip_y = flip_y
            item._apply_transform()
        for attr in self._SNAP_ATTRS:
            if attr in c:
                setattr(item, attr, c[attr])
        if 'dig_input_nodes' in c:
            item.dig_input_nodes = list(c['dig_input_nodes'])
        if 'dig_input_neg' in c:
            item.dig_input_neg = list(c['dig_input_neg'])
        item.update()
        return item

    def _clear_all(self):
        """Vacía la escena (componentes + cables) preparando un restore."""
        for it in list(self.components):
            if it.scene() is self:
                self.removeItem(it)
        self.components.clear()
        for w in list(self.wires):
            if w.scene() is self:
                self.removeItem(w)
        self.wires.clear()
        self._comp_counter.clear()

    def _restore(self, snap: dict):
        """Reemplaza el contenido actual con el del snapshot."""
        self._clear_all()
        name_to_comp: Dict[str, ComponentItem] = {}
        for c in snap.get('components', []):
            item = self._instantiate_component(c, keep_name=True)
            name_to_comp[c['name']] = item
        for w in snap.get('wires', []):
            sc = name_to_comp.get(w['start']) if w.get('start') else None
            ec = name_to_comp.get(w['end'])   if w.get('end')   else None
            wire = WireItem(
                QPointF(w['x1'], w['y1']), QPointF(w['x2'], w['y2']),
                start_comp=sc, start_pin_idx=w.get('spi', 0),
                end_comp=ec,   end_pin_idx=w.get('epi', 0))
            self.addItem(wire)
            self.wires.append(wire)
        # Restaurar contador de nombres para no chocar con autogenerados
        self._comp_counter = dict(snap.get('counter', {}))
        self.update()

    # ── Undo (Ctrl+Z) ───────────────────────────────────────────────────
    def push_undo(self):
        """Captura el estado actual y lo apila para Ctrl+Z. Llamar ANTES
        de cualquier mutación del canvas."""
        self._undo_stack.append(self._snapshot())
        self._redo_stack.clear()
        if len(self._undo_stack) > self._undo_max:
            self._undo_stack.pop(0)

    def undo(self) -> bool:
        if not self._undo_stack:
            return False
        self._redo_stack.append(self._snapshot())
        snap = self._undo_stack.pop()
        self._restore(snap)
        # Cualquier estado de drag en curso queda invalidado tras un restore.
        self._group_drag_active = False
        self._group_drag_wires = []
        self.component_selected.emit(None)
        return True

    def redo(self) -> bool:
        if not self._redo_stack:
            return False
        self._undo_stack.append(self._snapshot())
        self._restore(self._redo_stack.pop())
        self.component_selected.emit(None)
        return True

    # ── Copy / Cut / Paste (Ctrl+C / Ctrl+X / Ctrl+V) ───────────────────
    def copy_selected(self) -> bool:
        sel = self.selectedItems()
        sel_comps = [it for it in sel if isinstance(it, ComponentItem)]
        sel_wires = [it for it in sel if isinstance(it, WireItem)]
        if not sel_comps and not sel_wires:
            return False
        sel_names = {c.name for c in sel_comps}
        comps = [self._serialize_component(c) for c in sel_comps]
        wires = [self._serialize_wire(w, comp_filter=sel_names)
                 for w in sel_wires]
        CircuitScene._clipboard = {'components': comps, 'wires': wires}
        return True

    def cut_selected(self) -> bool:
        if not self.copy_selected():
            return False
        self.push_undo()
        for it in list(self.selectedItems()):
            if isinstance(it, ComponentItem) and it in self.components:
                self.components.remove(it)
            elif isinstance(it, WireItem) and it in self.wires:
                self.wires.remove(it)
            self.removeItem(it)
        return True

    def paste(self, offset_x: float = GRID_SIZE * 2,
              offset_y: float = GRID_SIZE * 2) -> bool:
        cb = CircuitScene._clipboard
        if not cb or (not cb.get('components') and not cb.get('wires')):
            return False
        self.push_undo()
        self.clearSelection()
        name_map: Dict[str, ComponentItem] = {}
        for c in cb['components']:
            item = self._instantiate_component(
                c, offset_x=offset_x, offset_y=offset_y, keep_name=False)
            name_map[c['name']] = item
            item.setSelected(True)
        for w in cb['wires']:
            sc = name_map.get(w['start']) if w.get('start') else None
            ec = name_map.get(w['end'])   if w.get('end')   else None
            wire = WireItem(
                QPointF(w['x1'] + offset_x, w['y1'] + offset_y),
                QPointF(w['x2'] + offset_x, w['y2'] + offset_y),
                start_comp=sc, start_pin_idx=w.get('spi', 0),
                end_comp=ec,   end_pin_idx=w.get('epi', 0))
            self.addItem(wire)
            self.wires.append(wire)
            wire.setSelected(True)
        return True

    def rotate_selected(self, delta: int = 90) -> bool:
        items = [it for it in self.selectedItems()
                 if isinstance(it, ComponentItem)]
        if not items:
            return False
        self.push_undo()
        for it in items:
            it.rotate_90(delta=delta)
        return True

    def flip_selected_x(self) -> bool:
        items = [it for it in self.selectedItems()
                 if isinstance(it, ComponentItem)]
        if not items:
            return False
        self.push_undo()
        for it in items:
            it.flip_x()
        return True

    def flip_selected_y(self) -> bool:
        items = [it for it in self.selectedItems()
                 if isinstance(it, ComponentItem)]
        if not items:
            return False
        self.push_undo()
        for it in items:
            it.flip_y()
        return True

    def align_selected(self, edge: str) -> bool:
        """Alinea los componentes seleccionados sobre uno de sus bordes."""
        items = [it for it in self.selectedItems() if isinstance(it, ComponentItem)]
        if len(items) < 2:
            return False
        self.push_undo()
        horizontal = edge in ('left', 'right')
        values = [it.pos().x() if horizontal else it.pos().y() for it in items]
        value = min(values) if edge in ('left', 'top') else max(values)
        for item in items:
            item.setPos(value, item.pos().y()) if horizontal else item.setPos(item.pos().x(), value)
        return True

    def distribute_selected(self, axis: str) -> bool:
        """Distribuye la selección entre sus dos extremos actuales."""
        items = sorted(
            (it for it in self.selectedItems() if isinstance(it, ComponentItem)),
            key=lambda it: it.pos().x() if axis == 'x' else it.pos().y())
        if len(items) < 3:
            return False
        first = items[0].pos().x() if axis == 'x' else items[0].pos().y()
        last = items[-1].pos().x() if axis == 'x' else items[-1].pos().y()
        self.push_undo()
        for index, item in enumerate(items[1:-1], 1):
            value = first + (last - first) * index / (len(items) - 1)
            if self.snap_enabled:
                value = round(value / GRID_SIZE) * GRID_SIZE
            item.setPos(value, item.pos().y()) if axis == 'x' else item.setPos(item.pos().x(), value)
        return True

    def electrical_rule_warnings(self) -> List[str]:
        """Comprobaciones ligeras antes de simular, sin modificar el circuito."""
        warnings: List[str] = []
        nets = self.extract_netlist()
        counts: Dict[str, int] = {}
        for net in nets.values():
            counts[net] = counts.get(net, 0) + 1

        analog = [c for c in self.components if c.comp_type not in (
            ComponentItem.DIGITAL_TYPES | {'GND', 'NODE', 'NET_LABEL_IN',
                                            'NET_LABEL_OUT', 'PORT'})]
        if analog and not any(c.comp_type == 'GND' for c in self.components):
            warnings.append(self.tr("No ground node (GND) was found."))

        floating: List[str] = []
        for comp in analog:
            for index, _ in enumerate(comp.all_pin_positions_scene(), 1):
                net = nets.get(f'{comp.name}__p{index}')
                if net and net != '0' and counts.get(net, 0) == 1:
                    floating.append(f'{comp.name}: pin {index}')
        if floating:
            preview = ', '.join(floating[:6])
            suffix = '…' if len(floating) > 6 else ''
            warnings.append(self.tr("Floating pins: ") + preview + suffix)

        dangling = sum(
            1 for wire in self.wires
            for point, comp in ((wire.line().p1(), wire.start_comp),
                                (wire.line().p2(), wire.end_comp))
            if comp is None and not self._free_endpoint_connected(point, wire))
        if dangling:
            warnings.append(self.tr("Dangling wire endpoints: {count}").format(count=dangling))
        return warnings

    # ── Menú contextual (click derecho sobre un componente) ──
    def contextMenuEvent(self, event):
        items = self.items(event.scenePos())
        comp = next((it for it in items if isinstance(it, ComponentItem)), None)
        if comp is None:
            super().contextMenuEvent(event)
            return

        # Si el componente clickeado no estaba seleccionado, seleccionarlo
        # (y limpiar la selección anterior) para que las acciones del menú
        # operen sobre él.
        if not comp.isSelected():
            for it in self.selectedItems():
                it.setSelected(False)
            comp.setSelected(True)

        menu = QMenu()
        act_props    = menu.addAction(self.tr("Properties…"))
        act_rename_net = None
        if comp.comp_type in ('NET_LABEL_IN', 'NET_LABEL_OUT'):
            act_rename_net = menu.addAction(self.tr("Rename Net Label…"))
        menu.addSeparator()
        act_rot_left  = menu.addAction(self.tr("Rotate 90° Left"))
        act_rot_right = menu.addAction(self.tr("Rotate 90° Right"))
        menu.addSeparator()
        act_flip_x = menu.addAction(self.tr("Flip on X Axis"))
        act_flip_y = menu.addAction(self.tr("Flip on Y Axis"))

        chosen = menu.exec(event.screenPos())
        if chosen is None:
            return
        if chosen is act_props:
            self._edit_component(comp)
        elif chosen is act_rename_net:
            from PyQt6.QtWidgets import QInputDialog
            label, ok = QInputDialog.getText(
                self.views()[0] if self.views() else None,
                self.tr("Rename Net Label"), self.tr("Net name:"),
                text=comp.sheet_label)
            if ok and label.strip() and label.strip() != comp.sheet_label:
                self.push_undo()
                comp.sheet_label = label.strip()
                comp.update()
                self.update()
        elif chosen is act_rot_left:
            self.rotate_selected(delta=-90)
        elif chosen is act_rot_right:
            self.rotate_selected(delta=90)
        elif chosen is act_flip_x:
            self.flip_selected_x()
        elif chosen is act_flip_y:
            self.flip_selected_y()

    # ── Extraccion de netlist por Union-Find ─────
    def extract_netlist(self) -> Dict[str, str]:
        """
        Analiza los cables del canvas y asigna nodos automaticamente.
        Union-Find: une pines conectados por cables en el mismo nodo.
        GND se mapea al nodo 0. Retorna {CompNombre__p1: net_X, ...}
        """
        SNAP = 12

        # ── 1. Registrar pines de componentes ───────────────────────────
        pins = {}
        for comp in self.components:
            p1, p2 = comp.pin_positions_scene()
            pins[f"{comp.name}__p1"] = p1
            pins[f"{comp.name}__p2"] = p2
            # SUBCKT: registrar TODOS los pines dinámicos p1..pN
            if comp.comp_type == 'SUBCKT':
                for i, pt in enumerate(comp.subckt_pin_positions_scene()):
                    pins[f"{comp.name}__p{i + 1}"] = pt
                continue
            if comp.comp_type in ComponentItem.TIMER_TYPES:
                for i, pt in enumerate(comp.all_pin_positions_scene(), 1):
                    pins[f"{comp.name}__p{i}"] = pt
                continue
            if comp.comp_type == 'COUNTER':
                for i, pt in enumerate(comp.all_pin_positions_scene(), 1):
                    pins[f"{comp.name}__p{i}"] = pt
                continue
            # Registrar pines adicionales según el número de terminales
            if comp.comp_type in ComponentItem.SIX_PIN_TYPES:
                for i, pt in enumerate(comp.all_pin_positions_scene()[2:], 3):
                    pins[f"{comp.name}__p{i}"] = pt
            elif comp.comp_type in ('BJT_NPN', 'BJT_PNP', 'NMOS', 'PMOS', 'OPAMP'):
                pins[f"{comp.name}__p3"] = comp.pin3_position_scene()
            elif comp.comp_type in ('SPDT', 'SPDT3'):
                pins[f"{comp.name}__p3"] = comp.pin3_position_scene()
            elif comp.comp_type in ComponentItem.FLIPFLOP_TYPES:
                pins[f"{comp.name}__p3"] = comp.pin3_position_scene()
                pins[f"{comp.name}__p4"] = comp.pin4_position_scene()  # SET
                pins[f"{comp.name}__p5"] = comp.pin5_position_scene()  # RESET
                pins[f"{comp.name}__p6"] = comp.pin6_position_scene()  # Q̄
            elif comp.comp_type == 'MUX2':
                pins[f"{comp.name}__p3"] = comp.pin3_position_scene()  # I1
                pins[f"{comp.name}__p4"] = comp.pin4_position_scene()  # SEL
            elif comp.comp_type in ComponentItem.FIVE_PIN_TYPES:
                pins[f"{comp.name}__p3"] = comp.pin3_position_scene()
                pins[f"{comp.name}__p4"] = comp.pin4_position_scene()
                pins[f"{comp.name}__p5"] = comp.pin5_position_scene()
            elif comp.comp_type in ComponentItem.FOUR_PIN_TYPES:
                pins[f"{comp.name}__p3"] = comp.pin3_position_scene()
                pins[f"{comp.name}__p4"] = comp.pin4_position_scene()
            elif comp.comp_type in ('AND', 'OR', 'NAND', 'NOR', 'XOR', 'COMPARATOR'):
                # Registrar TODOS los pines de entrada de la puerta:
                # p1 = salida (ya registrado), p2 = entrada 1 (ya registrado),
                # p3 = entrada 2, p4 = entrada 3, ...
                gw, gh, step, n_in = comp._gate_geometry()
                ys = comp._gate_pin_ys()
                for i, y in enumerate(ys):
                    pin_key = f"{comp.name}__p{i + 2}"  # p2, p3, p4, ...
                    pins[pin_key] = comp.mapToScene(QPointF(-gw - 10, y))

        # ── 2. Union-Find sobre pines + extremos de cables ───────────────
        # Incluimos los extremos de cables como nodos propios del grafo
        # para propagar correctamente cadenas de cables sin pines en el medio
        all_nodes: Dict[str, QPointF] = dict(pins)
        for idx, wire in enumerate(self.wires):
            line = wire.line()
            wp1 = wire.mapToScene(line.p1())
            wp2 = wire.mapToScene(line.p2())
            all_nodes[f"__wire{idx}__p1"] = wp1
            all_nodes[f"__wire{idx}__p2"] = wp2

        parent = {nid: nid for nid in all_nodes}

        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(a, b):
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[ra] = rb

        def pts_near(pa: QPointF, pb: QPointF) -> bool:
            return abs(pa.x() - pb.x()) < SNAP and abs(pa.y() - pb.y()) < SNAP

        # Unir nodos (pines y extremos de cable) que se tocan espacialmente
        node_ids = list(all_nodes.keys())
        for i in range(len(node_ids)):
            for j in range(i + 1, len(node_ids)):
                if pts_near(all_nodes[node_ids[i]], all_nodes[node_ids[j]]):
                    union(node_ids[i], node_ids[j])

        # Unir los dos extremos de cada cable entre sí (esto propaga
        # la conectividad a través de cables que no tocan ningún pin)
        for idx in range(len(self.wires)):
            union(f"__wire{idx}__p1", f"__wire{idx}__p2")

        # Un pin puede quedar sobre un tramo ya dibujado (uniones en T), no
        # solamente en uno de sus extremos. Sin esta unión el esquema se ve
        # conectado, pero el netlist deja el pin flotante.
        def pin_on_wire(pin: QPointF, a: QPointF, b: QPointF) -> bool:
            dx, dy = b.x() - a.x(), b.y() - a.y()
            length_sq = dx * dx + dy * dy
            if length_sq == 0:
                return pts_near(pin, a)
            t = max(0.0, min(1.0, ((pin.x() - a.x()) * dx + (pin.y() - a.y()) * dy)
                                 / length_sq))
            x, y = a.x() + t * dx, a.y() + t * dy
            return (pin.x() - x) ** 2 + (pin.y() - y) ** 2 < SNAP ** 2

        for pin_id, pin in pins.items():
            for idx in range(len(self.wires)):
                a = all_nodes[f"__wire{idx}__p1"]
                b = all_nodes[f"__wire{idx}__p2"]
                if pin_on_wire(pin, a, b):
                    union(pin_id, f"__wire{idx}__p1")

        # Un extremo de cable sobre otro tramo es una unión en T igual que
        # un pin sobre el tramo. Sin esto, el dibujo se ve conectado pero la
        # red queda partida eléctricamente.
        for idx in range(len(self.wires)):
            for end in (f"__wire{idx}__p1", f"__wire{idx}__p2"):
                point = all_nodes[end]
                for other in range(len(self.wires)):
                    if other == idx:
                        continue
                    a = all_nodes[f"__wire{other}__p1"]
                    b = all_nodes[f"__wire{other}__p2"]
                    if pin_on_wire(point, a, b):
                        union(end, f"__wire{other}__p1")

        # ── 3a. Unir pines de net labels con el mismo sheet_label ───────
        # Esta es la lógica central de los nodos inalámbricos:
        # todos los net labels (IN u OUT) que comparten sheet_label
        # se unen en el mismo grupo del Union-Find, igual que si hubiera
        # un cable físico entre ellos. Funciona en la misma hoja Y entre hojas.
        label_first_pin: Dict[str, str] = {}  # label/alias → pin_key del primer label visto
        for comp in self.components:
            if comp.comp_type in ('NET_LABEL_IN', 'NET_LABEL_OUT') and comp.sheet_label:
                pin_key = f"{comp.name}__p1"
                if pin_key not in pins:
                    continue
                aliases = []
                for lbl in (comp.sheet_label.strip(), comp.name.strip()):
                    if lbl and lbl not in aliases:
                        aliases.append(lbl)
                for lbl in aliases:
                    if lbl not in label_first_pin:
                        label_first_pin[lbl] = pin_key
                    else:
                        # Unir este pin con el primer pin que tiene el mismo label
                        union(label_first_pin[lbl], pin_key)

        # ── 3b. Detectar grupos GND ──────────────────────────────────────
        # Registrar AMBOS pines del componente GND como tierra
        gnd_roots: set = set()
        for comp in self.components:
            if comp.comp_type == 'GND':
                gnd_roots.add(find(f"{comp.name}__p1"))
                gnd_roots.add(find(f"{comp.name}__p2"))

        # ── 4. Asignar nombres de nodo ───────────────────────────────────
        pin_ids = list(pins.keys())
        groups: Dict[str, list] = {}
        for pid in pin_ids:
            groups.setdefault(find(pid), []).append(pid)

        root_labels: Dict[str, List[str]] = {}
        for comp in self.components:
            if comp.comp_type in ('NET_LABEL_IN', 'NET_LABEL_OUT') and comp.sheet_label:
                pin_key = f"{comp.name}__p1"
                if pin_key in pins:
                    lbl = comp.sheet_label.strip()
                    if lbl:
                        root = find(pin_key)
                        root_labels.setdefault(root, [])
                        if lbl not in root_labels[root]:
                            root_labels[root].append(lbl)

        root_to_name: Dict[str, str] = {}
        for root in groups:
            if root in gnd_roots:
                root_to_name[root] = '0'
            elif root in root_labels:
                # En grupos con net labels, la etiqueta visible es el nombre
                # electrico real. Asi una salida manual "Y" conecta con una
                # netlabel "Y" y tambien con el alias interno "OUT_Y".
                root_to_name[root] = sorted(root_labels[root], key=lambda s: (len(s), s))[0]
            else:
                # Usar el primer pin del grupo como nombre canónico del net.
                # Esto hace que el nombre sea estable y único por componente,
                # evitando que circuitos independientes compartan nombres de red
                # entre llamadas sucesivas a extract_netlist().
                canonical = min(groups[root])  # orden lexicográfico → determinista
                root_to_name[root] = f'net_{canonical}'

        return {pid: root_to_name[find(pid)] for pid in pin_ids}

    # ── Instrumentos ─────────────────────────────
    def _open_instrument_panel(self, item: ComponentItem):
        """Abre el panel frontal del instrumento. Import perezoso para evitar
        cargar Qt dialogs si nunca se abren."""
        # Si ya hay un panel abierto para este item, lo levantamos al frente
        existing = getattr(item, '_panel_dialog', None)
        if existing is not None and existing.isVisible():
            existing.raise_()
            existing.activateWindow()
            return

        if item.comp_type == 'FGEN':
            from kirho.ui.dialogs.function_generator_dialog import FunctionGeneratorDialog
            self.push_undo()
            dlg = FunctionGeneratorDialog(item, parent=None)
            item._panel_dialog = dlg
            dlg.changed.connect(lambda i=item: self.instrument_changed.emit(i))
            dlg.show()
            return

        if item.comp_type == 'OSC':
            from kirho.ui.dialogs.oscilloscope_dialog import OscilloscopeDialog
            self.push_undo()
            dlg = OscilloscopeDialog(item, parent=None)
            item._panel_dialog = dlg
            # Aunque sólo cambien parámetros visuales (Time/Div, etc.),
            # propagamos `changed` para mantener consistencia con FGEN.
            dlg.changed.connect(lambda i=item: self.instrument_changed.emit(i))
            dlg.show()
            return

        if item.comp_type == 'MULTIMETER':
            from kirho.ui.dialogs.multimeter_dialog import MultimeterDialog
            self.push_undo()
            dlg = MultimeterDialog(item, parent=None)
            item._panel_dialog = dlg
            # Cambiar V↔A modifica la R interna → la topología del solver
            # cambia y hay que reconstruir el netlist live.
            dlg.changed.connect(lambda i=item: self.instrument_changed.emit(i))
            dlg.show()
            return

    # ── Editar propiedades ───────────────────────
    def _edit_component(self, item: ComponentItem):
        dialog = ComponentDialog(item, COLORS)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            # Snapshot pre-edición para que Ctrl+Z revierta los cambios.
            self.push_undo()
            data = dialog.get_data()
            item.name      = data['name']
            item.value     = data['value']
            item.node1     = data['node1']
            item.node2     = data['node2']
            item.node3     = data['node3']
            if 'switch_key' in data:
                item.switch_key = data['switch_key']
            if item.comp_type == 'SPDT3' and 'switch_position' in data:
                item.value = float(data['switch_position'])
                item.switch_on1_key = data.get('switch_on1_key', '')
                item.switch_off_key = data.get('switch_off_key', '')
                item.switch_on2_key = data.get('switch_on2_key', '')
            if item.comp_type == 'VAC':
                item.frequency = data['frequency']
                item.phase_deg = data['phase_deg']
                item.ac_mode   = data['ac_mode']
            if item.comp_type == 'Z':
                item.z_mode   = data.get('z_mode', 'rect')
                item.z_real   = data.get('z_real', 100.0)
                item.z_imag   = data.get('z_imag', 0.0)
                item.z_mag    = data.get('z_mag', 100.0)
                item.z_phase  = data.get('z_phase', 0.0)
            if item.comp_type == 'LED':
                item.led_color = data.get('led_color', 'red')
            # POT
            if item.comp_type == 'POT' and 'pot_wiper' in data:
                item.pot_wiper = max(0.0, min(1.0, float(data['pot_wiper'])))
            # XFMR
            if item.comp_type == 'XFMR':
                if 'xfmr_ratio' in data: item.xfmr_ratio = float(data['xfmr_ratio'])
                if 'xfmr_imax'  in data: item.xfmr_imax  = float(data['xfmr_imax'])
            if item.comp_type == 'RELAY':
                item.relay_activation_voltage = max(0.0, float(data.get(
                    'relay_activation_voltage', item.relay_activation_voltage)))
            # 4º nodo
            if item.comp_type in ComponentItem.FOUR_PIN_TYPES and 'node4' in data:
                item.node4 = data['node4']
            if item.comp_type in ComponentItem.SIX_PIN_TYPES:
                item.node4 = data.get('node4', '')
                item.node5 = data.get('node5', '')
                item.node6 = data.get('node6', '')
            # 5º nodo (TL082)
            if item.comp_type in ComponentItem.FIVE_PIN_TYPES:
                if 'node4' in data: item.node4 = data['node4']
                if 'node5' in data: item.node5 = data['node5']
            if item.comp_type == 'IC555' and 'timer_nodes' in data:
                item.timer_nodes = (list(data['timer_nodes']) + [''] * 8)[:8]
            # Etiqueta inter-hoja
            if item.comp_type in ('NET_LABEL_IN', 'NET_LABEL_OUT') and 'sheet_label' in data:
                item.sheet_label = data['sheet_label']
            # Campos digitales
            if item.comp_type in ComponentItem.DIGITAL_TYPES:
                if 'dig_inputs'  in data: item.dig_inputs      = data['dig_inputs']
                if item.comp_type == 'NOT': item.dig_inputs = 1
                if 'dig_bits'    in data:
                    if item.comp_type == 'COUNTER': item.prepareGeometryChange()
                    item.dig_bits = data['dig_bits']
                if 'dig_bits_adc'in data: item.dig_bits_adc    = data['dig_bits_adc']
                if 'dig_vref'    in data: item.dig_vref         = data['dig_vref']
                if 'dig_tpd_ns'  in data: item.dig_tpd_ns      = data['dig_tpd_ns']
                if 'dig_clk'     in data: item.dig_clk          = data['dig_clk']
                if 'dig_analog_node' in data: item.dig_analog_node = data['dig_analog_node']
                if 'dig_input_nodes' in data: item.dig_input_nodes  = data['dig_input_nodes']
                if 'dig_input_neg'   in data: item.dig_input_neg   = data['dig_input_neg']
                # Normalizar la máscara de negación al nº actual de entradas
                if item.comp_type in ComponentItem.DIGITAL_TYPES:
                    n_in_now = 1 if item.comp_type == 'NOT' else max(1, item.dig_inputs)
                    neg = list(getattr(item, 'dig_input_neg', []) or [])
                    if len(neg) < n_in_now:
                        neg.extend([False] * (n_in_now - len(neg)))
                    item.dig_input_neg = neg[:n_in_now]
            item.update()
            if item.comp_type == 'COUNTER':
                self.update_wires_for_component(item)

# ══════════════════════════════════════════════════════════════
# Helper: convertir un ComponentItem analógico a objetos del engine
# ══════════════════════════════════════════════════════════════
def _rebuild_item_from_data(c: dict):
    """Reconstruye un ComponentItem (sin escena) desde una entrada
    serializada. Sólo restaura los atributos que afectan a la simulación."""
    item = ComponentItem(c['type'], c.get('name', '?'),
                          float(c.get('value', 0.0) or 0.0),
                          c.get('unit', ''),
                          c.get('node1', ''), c.get('node2', ''),
                          c.get('node3', ''))
    t = c['type']
    if t in ('VAC', 'FGEN'):
        item.frequency = c.get('frequency', 60.0)
        item.phase_deg = c.get('phase_deg', 0.0)
        item.ac_mode = c.get('ac_mode', 'rms')
    if t == 'FGEN':
        item.fgen_waveform = c.get('fgen_waveform', 'sin')
        item.fgen_offset = c.get('fgen_offset', 0.0)
        item.fgen_duty = c.get('fgen_duty', 0.5)
    if t == 'LED':
        item.led_color = c.get('led_color', 'red')
    if t == 'Z':
        item.z_real = c.get('z_real', 100.0)
        item.z_imag = c.get('z_imag', 0.0)
        item.z_mag = c.get('z_mag', 100.0)
        item.z_phase = c.get('z_phase', 0.0)
        item.z_mode = c.get('z_mode', 'rect')
    if t == 'POT':
        item.pot_wiper = max(0.0, min(1.0, float(c.get('pot_wiper', 0.5))))
    if t == 'XFMR':
        item.xfmr_ratio = c.get('xfmr_ratio', 2.0)
        item.xfmr_imax = c.get('xfmr_imax', 1.0)
    if t == 'BRIDGE':
        item.bridge_vf = c.get('bridge_vf', 0.7)
    if t == 'RELAY':
        item.relay_activation_voltage = max(0.0, float(c.get(
            'relay_activation_voltage', item.relay_activation_voltage)))
    if t in ('NET_LABEL_IN', 'NET_LABEL_OUT'):
        item.sheet_label = c.get('sheet_label', item.name)
    if t == 'SUBCKT':
        item.subckt_name = c.get('subckt_name', '')
        item.ic_pins = [dict(p) for p in c.get('ic_pins', [])]
    if t in ComponentItem.DIGITAL_TYPES:
        item.dig_inputs      = int(c.get('dig_inputs', item.dig_inputs))
        item.dig_bits        = int(c.get('dig_bits', item.dig_bits))
        item.dig_bits_adc    = int(c.get('dig_bits_adc', item.dig_bits_adc))
        item.dig_vref        = float(c.get('dig_vref', item.dig_vref))
        item.dig_clk         = c.get('dig_clk', item.dig_clk)
        item.dig_tpd_ns      = float(c.get('dig_tpd_ns', item.dig_tpd_ns))
        item.dig_analog_node = c.get('dig_analog_node', item.dig_analog_node)
        item.dig_input_nodes = list(c.get('dig_input_nodes', []) or [])
        item.dig_input_neg   = list(c.get('dig_input_neg', []) or [])
    item.node4 = c.get('node4', '')
    item.node5 = c.get('node5', '')
    item.node6 = c.get('node6', '')
    return item


def _flatten_subckt(item, pin_node, _depth: int = 0) -> list:
    """Aplana recursivamente una instancia SUBCKT en componentes del motor.

    Usa la netlist resuelta al crear el subcircuito (internal_nets/port_nets),
    renombrando cada nodo interno con un prefijo por instancia y mapeando los
    nodos de puerto a la red externa conectada a cada pin del IC.
    """
    from kirho.subcircuit_manager import SUBCIRCUIT_MANAGER
    if _depth > 16:
        return []  # protección anti-recursión infinita
    defn = SUBCIRCUIT_MANAGER.get(getattr(item, 'subckt_name', ''))
    if not defn:
        return []  # definición ausente → instancia no resuelta (placeholder)

    ports = defn.get('ports', [])
    port_nets = defn.get('port_nets', {})       # port_name -> net interno
    internal = defn.get('internal_nets', {})    # comp__pK -> net interno
    prefix = f"{item.name}/"

    ext_nodes = getattr(item, '_ext_nodes', None)
    ext = {}
    for i, p in enumerate(ports):
        pname = p.get('name', f'P{i + 1}')
        if ext_nodes and i < len(ext_nodes) and ext_nodes[i]:
            ext[pname] = ext_nodes[i]
        else:
            ext[pname] = pin_node.get(f"{item.name}__p{i + 1}",
                                      f"iso_{item.name}_p{i + 1}")

    net_to_port = {}
    for pname, pnet in port_nets.items():
        if pnet not in net_to_port:
            net_to_port[pnet] = pname

    def rename(net: str) -> str:
        if not net:
            return ''
        if net == '0':
            return '0'
        if net in net_to_port:
            return ext.get(net_to_port[net], prefix + net)
        return prefix + net

    out = []
    for c in defn.get('components', []):
        if c.get('type') == 'PORT':
            continue
        cn = c.get('name', '?')
        child = _rebuild_item_from_data(c)
        child.name = prefix + cn
        child.node1 = rename(internal.get(f"{cn}__p1", ''))
        child.node2 = rename(internal.get(f"{cn}__p2", ''))
        child.node3 = rename(internal.get(f"{cn}__p3", ''))
        child.node4 = rename(internal.get(f"{cn}__p4", ''))
        child.node5 = rename(internal.get(f"{cn}__p5", ''))
        child.node6 = rename(internal.get(f"{cn}__p6", ''))
        orig_extra = list(getattr(child, 'dig_input_nodes', []) or [])
        if orig_extra:
            child.dig_input_nodes = [
                rename(internal.get(f"{cn}__p{4 + j}", '')) or rename(ex)
                for j, ex in enumerate(orig_extra)
            ]
        if child.comp_type == 'SUBCKT':
            ndef = SUBCIRCUIT_MANAGER.get(child.subckt_name)
            n_ports = len(ndef.get('ports', [])) if ndef else 0
            child._ext_nodes = [rename(internal.get(f"{cn}__p{k + 1}", ''))
                                for k in range(n_ports)]
            out.extend(_flatten_subckt(child, {}, _depth + 1))
        else:
            out.extend(build_engine_components_for_item(child, {}))
    return out


def _expand_subckt_instance(item, pin_node: dict, _depth: int = 0):
    """Expande UNA instancia SUBCKT en ítems reales (no analógicos solamente).

    Devuelve (lista_de_ComponentItem, pin_node_parcial) con nombres y nets
    espaciados por instancia. Sirve para AMBOS motores (analógico y digital)
    porque produce ComponentItem normales con node1..5 y entradas en pin_node.
    """
    from kirho.subcircuit_manager import SUBCIRCUIT_MANAGER
    if _depth > 16:
        return [], {}
    defn = SUBCIRCUIT_MANAGER.get(getattr(item, 'subckt_name', ''))
    if not defn:
        return [], {}   # definición ausente → placeholder, no se simula

    ports = defn.get('ports', [])
    port_nets = defn.get('port_nets', {})
    internal = defn.get('internal_nets', {})
    prefix = f"{item.name}/"

    ext_nodes = getattr(item, '_ext_nodes', None)
    ext = {}
    for i, p in enumerate(ports):
        pname = p.get('name', f'P{i + 1}')
        if ext_nodes and i < len(ext_nodes) and ext_nodes[i]:
            ext[pname] = ext_nodes[i]
        else:
            ext[pname] = pin_node.get(f"{item.name}__p{i + 1}",
                                      f"iso_{item.name}_p{i + 1}")

    net_to_port = {}
    for pname, pnet in port_nets.items():
        if pnet not in net_to_port:
            net_to_port[pnet] = pname

    def rename(net: str) -> str:
        if not net:
            return ''
        if net == '0':
            return '0'
        if net in net_to_port:
            return ext.get(net_to_port[net], prefix + net)
        return prefix + net

    out_items, out_pn = [], {}
    for c in defn.get('components', []):
        if c.get('type') == 'PORT':
            continue
        cn = c.get('name', '?')
        child = _rebuild_item_from_data(c)
        child.name = prefix + cn
        child.node1 = rename(internal.get(f"{cn}__p1", ''))
        child.node2 = rename(internal.get(f"{cn}__p2", ''))
        child.node3 = rename(internal.get(f"{cn}__p3", ''))
        child.node4 = rename(internal.get(f"{cn}__p4", ''))
        child.node5 = rename(internal.get(f"{cn}__p5", ''))
        child.node6 = rename(internal.get(f"{cn}__p6", ''))
        # Entradas extra de puertas multi-entrada (≥3 entradas): el net de
        # cada una vive en dig_input_nodes y DEBE re-espaciarse igual que
        # node1..5. La entrada lógica j (j≥0 sobre las extra) corresponde al
        # pin p(4+j) (input0=p2, input1=p3, input2=p4, ...). Sin esto el
        # OR/AND final de una salida con ≥3 términos pierde esas entradas.
        orig_extra = list(getattr(child, 'dig_input_nodes', []) or [])
        if orig_extra:
            child.dig_input_nodes = [
                rename(internal.get(f"{cn}__p{4 + j}", '')) or rename(ex)
                for j, ex in enumerate(orig_extra)
            ]
        # Reexponer TODOS los pines en el pin_node aplanado (las puertas
        # multi-entrada y los FF usan p2..p8 vía pin_node).
        for k in range(1, 9):
            key = f"{cn}__p{k}"
            if key in internal:
                out_pn[f"{child.name}__p{k}"] = rename(internal[key])
        if child.comp_type == 'SUBCKT':
            ndef = SUBCIRCUIT_MANAGER.get(child.subckt_name)
            n_ports = len(ndef.get('ports', [])) if ndef else 0
            child._ext_nodes = [rename(internal.get(f"{cn}__p{j + 1}", ''))
                                for j in range(n_ports)]
            gc, gpn = _expand_subckt_instance(child, {}, _depth + 1)
            out_items.extend(gc)
            out_pn.update(gpn)
        else:
            out_items.append(child)
    return out_items, out_pn


def expand_subcircuits(components, pin_node: dict):
    """Aplana TODAS las instancias SUBCKT de una lista de componentes.

    Devuelve (componentes_planos, pin_node_plano) listos para cualquier
    motor (DC, AC, digital, mixto). Los SUBCKT y PORT se sustituyen por los
    componentes internos reales con nombres/nets espaciados por instancia.
    """
    flat = []
    pn = dict(pin_node)
    has_subckt = any(getattr(c, 'comp_type', '') == 'SUBCKT'
                     for c in components)
    if not has_subckt:
        return list(components), pn
    for item in components:
        ct = getattr(item, 'comp_type', '')
        if ct == 'SUBCKT':
            children, child_pn = _expand_subckt_instance(item, pn)
            flat.extend(children)
            pn.update(child_pn)
        elif ct == 'PORT':
            continue  # los PORT sólo definen interfaz, no se simulan
        else:
            flat.append(item)
    return flat, pn


def build_engine_components_for_item(item, pin_node):
    """
    Devuelve la lista de componentes del motor MNA que representan a `item`.
    Lista vacía si:
      • el item es de tipo digital (lo gestiona el engine digital aparte),
      • no se puede construir (valor inválido, etc.).

    Casos especiales:
      • POT     → 1 Potentiometer.
      • XFMR    → 1 Transformer  (4 nodos).
      • BRIDGE  → 4 Diodes interconectados como puente.
    """
    from kirho.engine import (
        Resistor, VoltageSource, VoltageSourceAC, CurrentSource,
        Capacitor, Inductor, Diode, BJT, MOSFET, OpAmp, Impedance,
        Potentiometer, Transformer, Switch, SPDT, SPDT3, DPDT, Relay,
    )

    if item.comp_type == 'SUBCKT':
        return _flatten_subckt(item, pin_node)
    if item.comp_type == 'PORT':
        return []  # marcador de puerto, sin aporte eléctrico propio
    if item.comp_type in ComponentItem.DIGITAL_TYPES:
        return []
    # El osciloscopio es un instrumento ideal: lee voltajes pero NO
    # aporta stamps al MNA. FGEN sí aporta (lo trata el case 'FGEN' abajo).
    if item.comp_type == 'OSC':
        return []
    # Multímetro: modelado como una única resistencia entre las dos puntas.
    #   V mode  → R_in = 10 MΩ   (idealmente ∞ → no perturba)
    #   A mode  → R_in = 1 mΩ    (idealmente 0 → mide corriente)
    #   Ω mode  → 10 MΩ          (Ω requiere análisis offline)
    if item.comp_type == 'MULTIMETER':
        n1m = item.node1.strip() or pin_node.get(f"{item.name}__p1", f"iso_{item.name}_p1")
        n2m = item.node2.strip() or pin_node.get(f"{item.name}__p2", "0")
        qty = getattr(item, 'meter_quantity', 'V')
        R_in = 1e-3 if qty == 'A' else 1e7
        return [Resistor(item.name, n1m, n2m, R_in)]

    n1 = item.node1.strip() or pin_node.get(f"{item.name}__p1", f"iso_{item.name}_p1")
    n2 = item.node2.strip() or pin_node.get(f"{item.name}__p2", "0")
    n3 = ((item.node3.strip() if hasattr(item, "node3") and item.node3.strip()
           else pin_node.get(f"{item.name}__p3", "")))
    n4 = ((item.node4.strip() if hasattr(item, "node4") and item.node4.strip()
           else pin_node.get(f"{item.name}__p4", "")))
    n5 = ((item.node5.strip() if hasattr(item, "node5") and item.node5.strip()
           else pin_node.get(f"{item.name}__p5", "")))
    n6 = ((item.node6.strip() if hasattr(item, "node6") and item.node6.strip()
           else pin_node.get(f"{item.name}__p6", "")))

    ct = item.comp_type
    try:
        if ct == 'R' and item.value > 0:
            return [Resistor(item.name, n1, n2, item.value)]
        if ct == 'POT':
            return [Potentiometer(item.name, n1, n2,
                                  R_total=max(item.value, 1.0),
                                  wiper=item.pot_wiper)]
        if ct == 'SPST':
            return [Switch(item.name, n1, n2, closed=bool(item.value))]
        if ct == 'SPDT':
            return [SPDT(item.name, n1, n2, n3, position=bool(item.value))]
        if ct == 'SPDT3':
            return [SPDT3(item.name, n1, n2, n3,
                          position=int(round(item.value)))]
        if ct == 'DPDT':
            return [DPDT(item.name, n1, n2, n3, n4, n5, n6,
                         position=bool(item.value))]
        if ct == 'RELAY':
            return [Relay(
                item.name, n1, n2, n3, n4,
                coil_r=max(item.value, 1e-3),
                threshold=max(0.0, float(item.relay_activation_voltage)),
            )]
        if ct == 'V':
            return [VoltageSource(item.name, n2, n1, item.value)]
        if ct == 'VAC':
            return [VoltageSourceAC(item.name, n2, n1,
                                    amplitude=item.value, frequency=item.frequency,
                                    phase_deg=item.phase_deg, mode=item.ac_mode)]
        if ct == 'FGEN':
            # Generador de funciones: misma fuente que VAC pero con waveform,
            # offset y duty configurables. La amplitud (item.value) se toma
            # como "peak" por convención del FGEN (panel frontal del Multisim).
            return [VoltageSourceAC(
                item.name, n2, n1,
                amplitude=item.value, frequency=item.frequency,
                phase_deg=item.phase_deg, mode=item.ac_mode,
                waveform=getattr(item, 'fgen_waveform', 'sin'),
                offset=getattr(item, 'fgen_offset', 0.0),
                duty=getattr(item, 'fgen_duty', 0.5),
            )]
        if ct == 'I':
            return [CurrentSource(item.name, n1, n2, item.value)]
        if ct == 'C' and item.value > 0:
            return [Capacitor(item.name, n1, n2, item.value)]
        if ct == 'L' and item.value > 0:
            return [Inductor(item.name, n1, n2, item.value)]
        if ct == 'D':
            Is_v = item.value if item.value > 0 else 1e-14
            return [Diode(item.name, n1, n2, Is=Is_v,
                          n=1.0, Vd_init=0.6, Vd_max=2.0)]
        if ct == 'LED':
            # Parámetros LED por color (Vf nominal a ~10 mA).
            # El COLOR es la única fuente de verdad para los parámetros físicos
            # del LED.  El campo `value` del item NO se utiliza aquí —
            # mantenerlo como override sería peligroso porque su default
            # heredado del diodo Si (1e-14) hace que el LED conduzca a 0.6V.
            #          (Is,         n,    Vf_typ,  Vd_init)
            led_params = {
                'red':    (1.0e-18, 2.0,  1.8,     1.7),
                'orange': (1.0e-19, 2.1,  2.0,     1.9),
                'yellow': (1.0e-20, 2.2,  2.1,     2.0),
                'green':  (1.0e-23, 2.5,  2.2,     2.1),
                'blue':   (1.0e-27, 3.0,  3.0,     2.9),
                'white':  (1.0e-27, 3.0,  3.1,     3.0),
            }
            color = getattr(item, 'led_color', 'red')
            Is_v, n_v, _, Vd0 = led_params.get(color, led_params['red'])
            return [Diode(item.name, n1, n2, Is=Is_v, n=n_v,
                          Vd_init=Vd0, Vd_max=5.0)]
        if ct == 'LAMP':
            # El valor del item es el umbral visual de encendido; la carga
            # eléctrica usa una resistencia fija de bombillo de laboratorio.
            lamp = Resistor(item.name, n1, n2, 100.0)
            lamp.is_lamp = True
            return [lamp]
        if ct in ('BJT_NPN', 'BJT_PNP'):
            t = 'NPN' if ct == 'BJT_NPN' else 'PNP'
            return [BJT(item.name, n1, n3 or f'b_{item.name}', n2,
                        type_=t, Bf=item.value if item.value > 0 else 100)]
        if ct in ('NMOS', 'PMOS'):
            t = 'NMOS' if ct == 'NMOS' else 'PMOS'
            return [MOSFET(item.name, n1, n3 or f'g_{item.name}', n2,
                           type_=t, Kn=item.value if item.value > 0 else 1e-3)]
        if ct == 'OPAMP':
            return [OpAmp(item.name, n1, n3 or f'vp_{item.name}', n2,
                          A=item.value if item.value > 0 else 1e5)]
        if ct == 'TL082':
            # p1=OUT, p2=IN−, p3=IN+, p4=V+, p5=V−
            # El modelo usa tierra (0) como referencia de la VCVS; V+ y V−
            # son nodos de circuito que el usuario conecta a sus rieles de
            # alimentación — su presencia en el netlist es suficiente para
            # que el solver los tenga en cuenta.
            return [OpAmp(item.name,
                          n1,                              # n_out = OUT
                          n3 or f'inp_{item.name}',        # n_p   = IN+
                          n2,                              # n_n   = IN−
                          A=item.value if item.value > 0 else 1e5)]
        if ct == 'Z':
            import math as _m
            Z_val = (complex(item.z_real, item.z_imag) if item.z_mode == 'rect'
                     else complex(item.z_mag*_m.cos(_m.radians(item.z_phase)),
                                  item.z_mag*_m.sin(_m.radians(item.z_phase))))
            if abs(Z_val) > 1e-12:
                return [Impedance(item.name, n1, n2, Z_val)]
        if ct == 'XFMR':
            n3_x = n3 or f'sec1_{item.name}'
            n4_x = n4 or f'sec2_{item.name}'
            return [Transformer(item.name, n1, n2, n3_x, n4_x,
                                ratio=item.xfmr_ratio,
                                I_max=item.xfmr_imax)]
        if ct == 'BRIDGE':
            n3_b = n3 or f'dcp_{item.name}'   # DC+
            n4_b = n4 or f'dcn_{item.name}'   # DC−
            Is = 1e-14
            return [
                Diode(f'{item.name}_D1', n1,   n3_b, Is=Is),  # AC1 → DC+
                Diode(f'{item.name}_D2', n2,   n3_b, Is=Is),  # AC2 → DC+
                Diode(f'{item.name}_D3', n4_b, n1,   Is=Is),  # DC− → AC1
                Diode(f'{item.name}_D4', n4_b, n2,   Is=Is),  # DC− → AC2
            ]
    except Exception:
        pass
    return []
