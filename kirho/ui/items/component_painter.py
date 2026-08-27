"""Pintado de componentes del canvas de Kirho.

La clase ComponentItem conserva estado, geometría e interacción; este módulo
contiene únicamente la representación visual de sus símbolos.
"""
import math

from PyQt6.QtGui import (
    QPainter, QPen, QBrush, QColor, QFont, QPainterPath, QPolygonF,
    QRadialGradient,
)
from PyQt6.QtCore import Qt, QPointF, QRectF

from kirho.ui.style import (
    COLORS, COMP_W, COMP_H, PIN_RADIUS, _qfont,
)



class _MonochromePainter:
    """Adapta los colores de los componentes al modo de impresión vectorial."""

    def __init__(self, painter, item):
        self._painter = painter
        self._item = item
        self.upright_text = True

    def __getattr__(self, name):
        return getattr(self._painter, name)

    def setPen(self, pen):
        if isinstance(pen, QPen):
            pen = QPen(QColor('#000000'), pen.widthF(), pen.style())
        elif isinstance(pen, QColor):
            pen = QColor('#000000')
        return self._painter.setPen(pen)

    def setBrush(self, brush):
        if isinstance(brush, QBrush):
            if brush.style() != Qt.BrushStyle.NoBrush:
                brush = QBrush(QColor('#ffffff'), brush.style())
        elif isinstance(brush, QColor):
            brush = QColor('#ffffff')
        return self._painter.setBrush(brush)

    def setFont(self, font):
        if isinstance(font, QFont) and font.pointSizeF() > 0:
            font = QFont(font)
            font.setPixelSize(max(6, round(font.pointSizeF())))
        return self._painter.setFont(font)

    def drawText(self, *args):
        # Los textos internos (+/−, V+/V−, pinout, etc.) deben permanecer
        # legibles aunque el símbolo esté girado.
        if not self.upright_text:
            return self._painter.drawText(*args)
        self._painter.save()
        item_transform, invertible = self._item.transform().inverted()
        if invertible:
            self._painter.setWorldTransform(
                item_transform * self._painter.worldTransform(), False)
        result = self._painter.drawText(*args)
        self._painter.restore()
        return result




class ComponentPainter:
    """Dibuja un ComponentItem usando su estado como fuente de datos.

    El proxy de atributos permite mover los dibujantes sin reescribir sus
    fórmulas de geometría: cualquier atributo o helper no visual se resuelve
    en el item asociado.
    """

    def __init__(self, item):
        self._item = item

    def __getattr__(self, name):
        return getattr(self._item, name)

    # ── Dibujo ──────────────────────────────────
    def paint(self, painter: QPainter, option, widget):
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        selected = self.isSelected()
        printing = self.scene() is not None and getattr(self.scene(), 'print_mode', False)
        monochrome = printing and getattr(self.scene(), 'print_monochrome', True)
        if monochrome:
            painter = _MonochromePainter(painter, self)
        if printing:
            print_font = QFont(_qfont('Menlo', 8))
            print_font.setPixelSize(8)
            painter.setFont(print_font)
        body_color  = QColor('#ffffff' if monochrome else
                             COLORS['comp_body'] if printing else
                             COLORS['comp_sel'] if selected else COLORS['comp_body'])
        line_color  = QColor('#000000' if monochrome else
                             COLORS['component'] if printing else
                             COLORS['comp_sel'] if selected else COLORS['component'])
        text_color  = QColor('#000000' if printing else COLORS['text'])

        pen_body = QPen(line_color, 2)
        pen_wire = QPen(QColor('#000000' if monochrome else COLORS['wire']), 2)
        pen_pin  = QPen(QColor('#000000' if monochrome else COLORS['pin']),  2)

        if self.comp_type == 'GND':
            self._draw_gnd(painter, pen_body)
        elif self.comp_type == 'NODE':
            self._draw_node(painter, line_color)
        elif self.comp_type == 'R':
            self._draw_resistor(painter, pen_body, pen_wire, body_color)
        elif self.comp_type == 'POT':
            self._draw_potentiometer(painter, pen_body, pen_wire, body_color)
        elif self.comp_type == 'C':
            self._draw_capacitor(painter, pen_body, pen_wire)
        elif self.comp_type == 'L':
            self._draw_inductor(painter, pen_body, pen_wire)
        elif self.comp_type in ('V', 'I', 'VAC'):
            self._draw_source(painter, pen_body, pen_wire, body_color)
        elif self.comp_type == 'D':
            self._draw_diode(painter, pen_body, pen_wire)
        elif self.comp_type == 'LED':
            self._draw_led(painter, pen_body, pen_wire)
        elif self.comp_type == 'LAMP':
            self._draw_bulb(painter, pen_body, pen_wire)
        elif self.comp_type in ('BJT_NPN', 'BJT_PNP'):
            self._draw_bjt(painter, pen_body, pen_wire)
        elif self.comp_type in ('NMOS', 'PMOS'):
            self._draw_mosfet(painter, pen_body, pen_wire)
        elif self.comp_type == 'OPAMP':
            self._draw_opamp(painter, pen_body, pen_wire, body_color)
        elif self.comp_type == 'TL082':
            self._draw_tl082(painter, pen_body, pen_wire, body_color)
        elif self.comp_type == 'Z':
            self._draw_impedance(painter, pen_body, pen_wire, body_color)
        elif self.comp_type == 'XFMR':
            self._draw_transformer(painter, pen_body, pen_wire, body_color)
        elif self.comp_type == 'BRIDGE':
            self._draw_bridge_rectifier(painter, pen_body, pen_wire, body_color)
        elif self.comp_type in ('SPST', 'SPDT', 'SPDT3', 'DPDT', 'RELAY'):
            self._draw_switch(painter, pen_body, pen_wire, body_color)
        # ── Instrumentos ─────────────────────────────────────────────────
        elif self.comp_type == 'FGEN':
            self._draw_fgen(painter, pen_body, pen_wire, body_color)
        elif self.comp_type == 'OSC':
            self._draw_osc(painter, pen_body, pen_wire, body_color)
        elif self.comp_type == 'MULTIMETER':
            self._draw_multimeter(painter, pen_body, pen_wire, body_color)
        # ── Digital ──────────────────────────────────────────────────────
        elif self.comp_type in ('AND', 'NAND', 'OR', 'NOR', 'XOR', 'NOT'):
            self._draw_ansi_gate(painter, pen_body, pen_wire, body_color)
        elif self.comp_type == 'DFF':
            self._draw_flipflop(painter, pen_body, pen_wire, body_color, 'DFF')
        elif self.comp_type == 'JKFF':
            self._draw_flipflop(painter, pen_body, pen_wire, body_color, 'JKFF')
        elif self.comp_type == 'TFF':
            self._draw_flipflop(painter, pen_body, pen_wire, body_color, 'TFF')
        elif self.comp_type == 'SRFF':
            self._draw_flipflop(painter, pen_body, pen_wire, body_color, 'SRFF')
        elif self.comp_type == 'CLK':
            self._draw_clk(painter, pen_body, pen_wire, body_color)
        elif self.comp_type == 'ADC_BRIDGE':
            self._draw_adc_dac(painter, pen_body, pen_wire, body_color, is_adc=True)
        elif self.comp_type == 'DAC_BRIDGE':
            self._draw_adc_dac(painter, pen_body, pen_wire, body_color, is_adc=False)
        elif self.comp_type == 'COMPARATOR':
            self._draw_digital_gate(painter, pen_body, pen_wire, body_color, 'CMP')
        elif self.comp_type == 'PWM':
            self._draw_digital_gate(painter, pen_body, pen_wire, body_color, 'PWM')
        elif self.comp_type == 'COUNTER':
            self._draw_counter(painter, pen_body, pen_wire, body_color)
        elif self.comp_type == 'MUX2':
            self._draw_mux(painter, pen_body, pen_wire, body_color)
        elif self.comp_type in self.TIMER_TYPES:
            self._draw_timer555(painter, pen_body, pen_wire, body_color)
        elif self.comp_type == 'LOGIC_STATE':
            self._draw_logic_state(painter, pen_body, pen_wire, body_color)
        elif self.comp_type in ('NET_LABEL_IN', 'NET_LABEL_OUT'):
            self._draw_sheet_connector(painter, pen_body, pen_wire, body_color)
        elif self.comp_type == 'PORT':
            self._draw_port(painter, pen_body, pen_wire, body_color)
        elif self.comp_type == 'SUBCKT':
            self._draw_subcircuit(painter, pen_body, pen_wire, body_color)

        # Nombre y valor
        self._draw_labels(painter, text_color)

        # Pines — los dispositivos de 3 terminales dibujan sus propios pines
        # internamente con etiquetas; solo dibujar pines genéricos para el resto
        three_terminal = ('BJT_NPN', 'BJT_PNP', 'NMOS', 'PMOS', 'OPAMP', 'TL082',
                          'NET_LABEL_IN', 'NET_LABEL_OUT',
                          'DFF', 'JKFF', 'TFF', 'SRFF', 'COUNTER',
                          'SPST', 'SPDT', 'SPDT3', 'DPDT', 'RELAY',
                          'IC555',
                          'PORT', 'SUBCKT',   # dibujan sus propios pines
                          'MULTIMETER')   # _draw_multimeter pinta sus pines
        if self.comp_type not in three_terminal:
            for pin in self.pin_positions():
                painter.setPen(pen_pin)
                painter.setBrush(QBrush(QColor(COLORS['pin'])))
                painter.drawEllipse(pin, PIN_RADIUS, PIN_RADIUS)

    def _draw_resistor(self, painter, pen_body, pen_wire, body_color):
        hw = COMP_W // 2
        hh = COMP_H // 2
        # Cables de conexión
        painter.setPen(pen_wire)
        painter.drawLine(QPointF(-hw - 10, 0), QPointF(-hw, 0))
        painter.drawLine(QPointF(hw, 0), QPointF(hw + 10, 0))
        # Cuerpo (rectángulo)
        painter.setPen(pen_body)
        painter.setBrush(QBrush(body_color))
        painter.drawRect(QRectF(-hw, -hh, COMP_W, COMP_H))
        # Símbolo zigzag interno
        painter.setPen(QPen(QColor(COLORS['component']), 1.5))
        pts = []
        steps = 6
        for i in range(steps + 1):
            x = -hw + i * (COMP_W / steps)
            y = (hh * 0.6) if i % 2 == 0 else -(hh * 0.6)
            pts.append(QPointF(x, y))
        for i in range(len(pts) - 1):
            painter.drawLine(pts[i], pts[i+1])

    def _draw_switch(self, painter, pen_body, pen_wire, body_color):
        """Símbolos para SPST, SPDT, SPDT3, DPDT y relé."""
        pin = QColor(COLORS['pin'])
        painter.setPen(pen_wire)
        painter.setBrush(QBrush(body_color))
        if self.comp_type == 'SPST':
            painter.drawLine(QPointF(-40, 0), QPointF(-8, 0))
            painter.drawLine(QPointF(8, 0), QPointF(40, 0))
            painter.setPen(pen_body)
            painter.drawLine(QPointF(-8, 0), QPointF(8, 0) if self.value else QPointF(8, -18))
            points = [QPointF(-40, 0), QPointF(40, 0)]
        elif self.comp_type in ('SPDT', 'SPDT3'):
            painter.drawLine(QPointF(-40, 0), QPointF(-8, 0))
            painter.drawLine(QPointF(8, -20), QPointF(40, -20))
            painter.drawLine(QPointF(8, 20), QPointF(40, 20))
            painter.setPen(pen_body)
            if self.comp_type == 'SPDT':
                target = QPointF(8, 20 if self.value else -20)
            elif self.value < 0:
                target = QPointF(8, -20)
            elif self.value > 0:
                target = QPointF(8, 20)
            else:
                target = QPointF(8, 0)
            painter.drawLine(QPointF(-8, 0), target)
            points = [QPointF(-40, 0), QPointF(40, -20), QPointF(40, 20)]
        elif self.comp_type == 'DPDT':
            for y in (-25, 25):
                painter.drawLine(QPointF(-50, y), QPointF(-8, y))
            for y in (-40, -10, 10, 40):
                painter.drawLine(QPointF(8, y), QPointF(40, y))
            painter.setPen(pen_body)
            upper_target = -40 if not self.value else -10
            lower_target = 10 if not self.value else 40
            painter.drawLine(QPointF(-8, -25), QPointF(8, upper_target))
            painter.drawLine(QPointF(-8, 25), QPointF(8, lower_target))
            points = [QPointF(-50, -25), QPointF(40, -40),
                      QPointF(40, -10), QPointF(-50, 25),
                      QPointF(40, 10), QPointF(40, 40)]
        else:
            # Bobina del relé: cuatro espiras verticales, como un inductor.
            painter.setPen(pen_body)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            coil = QPainterPath()
            coil.moveTo(-32, -20)
            for i in range(4):
                cy = -15 + i * 10
                coil.arcTo(QRectF(-44, cy - 5, 24, 10), 90, -180)
            painter.drawPath(coil)
            painter.setPen(pen_wire)
            painter.drawLine(QPointF(-45, -20), QPointF(-32, -20))
            painter.drawLine(QPointF(-45, 20), QPointF(-32, 20))
            painter.drawLine(QPointF(8, -20), QPointF(45, -20))
            painter.drawLine(QPointF(8, 20), QPointF(45, 20))
            painter.setPen(pen_body)
            active = bool(getattr(self, 'relay_active', False))
            painter.drawLine(QPointF(8, 20), QPointF(8, -20) if active else QPointF(25, -8))
            points = [QPointF(-45, -20), QPointF(-45, 20), QPointF(45, -20), QPointF(45, 20)]
        painter.setPen(QPen(pin, 2)); painter.setBrush(QBrush(pin))
        for point in points:
            painter.drawEllipse(point, PIN_RADIUS, PIN_RADIUS)

    def _draw_potentiometer(self, painter, pen_body, pen_wire, body_color):
        """Resistor + flecha diagonal que lo atraviesa (cursor variable)."""
        # Primero el resistor base
        self._draw_resistor(painter, pen_body, pen_wire, body_color)
        hw = COMP_W // 2
        hh = COMP_H // 2

        # Flecha diagonal con la inclinación según la posición del cursor.
        # wiper=0  → flecha apuntando a la izq;  wiper=1 → a la derecha.
        w        = max(0.0, min(1.0, float(self.pot_wiper)))
        # Punto inicial: izq-inferior, punta: cruza el cuerpo en diagonal
        arrow_pen = QPen(QColor(COLORS['comp_sel']), 2.2)
        painter.setPen(arrow_pen)
        x_start = -hw + 4
        y_start = hh + 8
        # X de la punta varía con el wiper para visualizar la posición
        x_tip   = -hw + 6 + (COMP_W - 12) * w
        y_tip   = -hh - 8
        # Línea principal
        painter.drawLine(QPointF(x_start, y_start), QPointF(x_tip, y_tip))
        # Cabeza de la flecha (triángulo)
        import math as _m
        dx = x_tip - x_start; dy = y_tip - y_start
        L  = _m.hypot(dx, dy) or 1.0
        ux, uy = dx/L, dy/L
        px, py = -uy, ux
        sz = 7
        head = QPolygonF([
            QPointF(x_tip, y_tip),
            QPointF(x_tip - sz*ux + sz*0.45*px, y_tip - sz*uy + sz*0.45*py),
            QPointF(x_tip - sz*ux - sz*0.45*px, y_tip - sz*uy - sz*0.45*py),
        ])
        painter.setBrush(QColor(COLORS['comp_sel']))
        painter.drawPolygon(head)
        painter.setBrush(Qt.BrushStyle.NoBrush)

        # Indicador del % (pequeño, debajo)
        painter.setPen(QPen(QColor(COLORS['text_dim']), 1))
        painter.setFont(_qfont('Menlo', 6))
        painter.drawText(QRectF(-hw, hh + 14, COMP_W, 10),
                         Qt.AlignmentFlag.AlignCenter, f"{w*100:.0f}%")

    def _draw_transformer(self, painter, pen_body, pen_wire, body_color):
        """
        Transformador con dos bobinas verticales (primario izq, secundario der)
        y dos líneas verticales centrales que representan el núcleo de hierro.
        """
        import math as _m
        # ── Cables a los 4 pines ──────────────────────────────────────────
        painter.setPen(pen_wire)
        # Primario (izq): pines a (±60, ±20), bobina a x=-40
        painter.drawLine(QPointF(-60, -20), QPointF(-40, -20))   # p1 → top de bobina
        painter.drawLine(QPointF(-60,  20), QPointF(-40,  20))   # p2 → bot de bobina
        # Secundario (der): bobina a x=40
        painter.drawLine(QPointF(40, -20), QPointF(60, -20))     # p3 → top
        painter.drawLine(QPointF(40,  20), QPointF(60,  20))     # p4 → bot

        # ── Bobinas (semicírculos apilados) ───────────────────────────────
        painter.setPen(QPen(QColor(COLORS['component']), 1.8))
        # Primario: 4 lazos a la izquierda (abren hacia la derecha)
        path_p = QPainterPath()
        path_p.moveTo(-40, -20)
        for i in range(4):
            cy = -20 + i*10 + 5
            path_p.arcTo(QRectF(-45, cy - 5, 10, 10), 90, -180)
        painter.drawPath(path_p)
        # Secundario: 4 lazos a la derecha (abren hacia la izquierda)
        path_s = QPainterPath()
        path_s.moveTo(40, -20)
        for i in range(4):
            cy = -20 + i*10 + 5
            path_s.arcTo(QRectF(35, cy - 5, 10, 10), 90, 180)
        painter.drawPath(path_s)

        # ── Núcleo de hierro: dos líneas verticales paralelas ─────────────
        painter.setPen(QPen(QColor(COLORS['text']), 1.4))
        painter.drawLine(QPointF(-3, -22), QPointF(-3, 22))
        painter.drawLine(QPointF( 3, -22), QPointF( 3, 22))

        # ── Etiqueta de relación ──────────────────────────────────────────
        painter.setFont(_qfont('Menlo', 7))
        painter.setPen(QPen(QColor(COLORS['text_dim']), 1))
        n = self.xfmr_ratio
        if n >= 1:
            label = f"{n:.1f}:1"
        else:
            label = f"1:{1/n:.1f}"
        painter.drawText(QRectF(-30, 24, 60, 10),
                         Qt.AlignmentFlag.AlignCenter, label)
        # Indicar polaridad con un punto en la parte superior de cada bobina
        painter.setPen(QPen(QColor(COLORS['component']), 1))
        painter.setBrush(QColor(COLORS['component']))
        painter.drawEllipse(QPointF(-32, -24), 1.8, 1.8)
        painter.drawEllipse(QPointF( 32, -24), 1.8, 1.8)
        painter.setBrush(Qt.BrushStyle.NoBrush)

    def _draw_bridge_rectifier(self, painter, pen_body, pen_wire, body_color):
        """
        Puente rectificador en disposición de diamante con 4 diodos:

                    DC+ (top)
                     /\\
                    /  \\
              D3 ↗      ↘ D1
                /        \\
        AC1 ──┤          ├── AC2
                \\        /
              D4 ↘      ↗ D2
                    \\/
                    DC− (bottom)
        """
        # ── Pines ─────────────────────────────────────────────────────────
        # AC1 (izq), AC2 (der), DC+ (sup), DC- (inf)
        # Conectores cortos a los vértices del diamante
        painter.setPen(pen_wire)
        painter.drawLine(QPointF(-60, 0),  QPointF(-40, 0))   # AC1
        painter.drawLine(QPointF(40, 0),   QPointF(60, 0))    # AC2
        painter.drawLine(QPointF(0, -60),  QPointF(0, -40))   # DC+
        painter.drawLine(QPointF(0,  40),  QPointF(0,  60))   # DC−

        # ── Diamante ──────────────────────────────────────────────────────
        painter.setPen(pen_body)
        painter.setBrush(QBrush(body_color))
        diamond = QPolygonF([
            QPointF(0, -40),   # top  (DC+)
            QPointF(40,  0),   # right (AC2)
            QPointF(0,  40),   # bot  (DC-)
            QPointF(-40, 0),   # left (AC1)
        ])
        painter.drawPolygon(diamond)

        # ── 4 diodos dentro del diamante ──────────────────────────────────
        painter.setPen(QPen(QColor(COLORS['component']), 1.5))
        painter.setBrush(QBrush(QColor(COLORS['component'])))

        def draw_diode_arrow(painter, p_from, p_to):
            """Dibuja un diodo orientado de p_from → p_to dentro del puente."""
            dx = p_to.x() - p_from.x()
            dy = p_to.y() - p_from.y()
            import math as _m
            L  = _m.hypot(dx, dy) or 1.0
            ux, uy = dx/L, dy/L
            px, py = -uy, ux
            cx = (p_from.x() + p_to.x()) / 2
            cy = (p_from.y() + p_to.y()) / 2
            sz = 6
            tri = QPolygonF([
                QPointF(cx + sz*ux,             cy + sz*uy),
                QPointF(cx - sz*ux + sz*0.7*px, cy - sz*uy + sz*0.7*py),
                QPointF(cx - sz*ux - sz*0.7*px, cy - sz*uy - sz*0.7*py),
            ])
            painter.drawPolygon(tri)
            tip_x = cx + sz*ux
            tip_y = cy + sz*uy
            painter.drawLine(
                QPointF(tip_x + 0.7*sz*px, tip_y + 0.7*sz*py),
                QPointF(tip_x - 0.7*sz*px, tip_y - 0.7*sz*py))

        draw_diode_arrow(painter, QPointF(-40, 0), QPointF(0, -40))   # D1: AC1→DC+
        draw_diode_arrow(painter, QPointF(40, 0),  QPointF(0, -40))   # D2: AC2→DC+
        draw_diode_arrow(painter, QPointF(0, 40),  QPointF(-40, 0))   # D3: DC-→AC1
        draw_diode_arrow(painter, QPointF(0, 40),  QPointF(40, 0))    # D4: DC-→AC2
        painter.setBrush(Qt.BrushStyle.NoBrush)

        # Etiquetas de pines
        painter.setFont(_qfont('Menlo', 6))
        painter.setPen(QPen(QColor(COLORS['text_dim']), 1))
        painter.drawText(QRectF(-60, -8, 18, 10),  Qt.AlignmentFlag.AlignCenter, '~')
        painter.drawText(QRectF(42,  -8, 18, 10),  Qt.AlignmentFlag.AlignCenter, '~')
        painter.drawText(QRectF(-12, -60, 24, 10), Qt.AlignmentFlag.AlignCenter, '+')
        painter.drawText(QRectF(-12,  50, 24, 10), Qt.AlignmentFlag.AlignCenter, '−')

    def _draw_capacitor(self, painter, pen_body, pen_wire):
        painter.setPen(pen_wire)
        painter.drawLine(QPointF(-COMP_W//2 - 10, 0), QPointF(-8, 0))
        painter.drawLine(QPointF(8, 0), QPointF(COMP_W//2 + 10, 0))
        painter.setPen(pen_body)
        painter.drawLine(QPointF(-8, -COMP_H//2), QPointF(-8, COMP_H//2))
        painter.drawLine(QPointF(8, -COMP_H//2), QPointF(8, COMP_H//2))

    def _draw_inductor(self, painter, pen_body, pen_wire):
        painter.setPen(pen_wire)
        painter.drawLine(QPointF(-COMP_W//2 - 10, 0), QPointF(-COMP_W//2, 0))
        painter.drawLine(QPointF(COMP_W//2, 0), QPointF(COMP_W//2 + 10, 0))
        painter.setPen(pen_body)
        path = QPainterPath()
        path.moveTo(-COMP_W//2, 0)
        for i in range(4):
            cx = -COMP_W//2 + i * 15
            path.arcTo(QRectF(cx, -10, 15, 20), 180, -180)
        painter.drawPath(path)

    def _draw_source(self, painter, pen_body, pen_wire, body_color):
        r = COMP_H // 2 + 2
        painter.setPen(pen_wire)
        painter.drawLine(QPointF(-COMP_W//2 - 10, 0), QPointF(-r, 0))
        painter.drawLine(QPointF(r, 0), QPointF(COMP_W//2 + 10, 0))
        painter.setPen(pen_body)
        painter.setBrush(QBrush(body_color))
        painter.drawEllipse(QPointF(0, 0), r, r)
        # Símbolo + / − / ~  o flecha
        painter.setPen(QPen(QColor(COLORS['component']), 2))
        if self.comp_type == 'V':
            painter.drawText(QRectF(4, -r+4, r-4, r*2-8), Qt.AlignmentFlag.AlignCenter, '+')
        elif self.comp_type == 'VAC':
            # Onda sinusoidal dentro del círculo
            path = QPainterPath()
            path.moveTo(-r*0.5, 0)
            for i in range(1, 21):
                t = i / 20.0
                x = -r*0.5 + t * r
                y = -r*0.35 * math.sin(t * 2 * math.pi)
                path.lineTo(x, y)
            painter.drawPath(path)
        else:
            # Flecha de corriente
            painter.drawLine(QPointF(-8, 0), QPointF(8, 0))
            painter.drawLine(QPointF(4, -5), QPointF(8, 0))
            painter.drawLine(QPointF(4, 5), QPointF(8, 0))

    def _draw_fgen(self, painter, pen_body, pen_wire, body_color):
        """Generador de funciones: caja rectangular tipo instrumento con la
        forma de onda actual dibujada dentro. Pines a izquierda (V+) y
        derecha (V−)."""
        hw = COMP_W // 2 + 4
        hh = COMP_H // 2 + 4
        # Cables a los pines
        painter.setPen(pen_wire)
        painter.drawLine(QPointF(-hw - 10, 0), QPointF(-hw, 0))
        painter.drawLine(QPointF(hw, 0), QPointF(hw + 10, 0))
        # Cuerpo
        painter.setPen(pen_body)
        painter.setBrush(QBrush(body_color))
        painter.drawRect(QRectF(-hw, -hh, hw * 2, hh * 2))
        # Forma de onda dentro (eje horizontal = un período)
        painter.setPen(QPen(QColor(COLORS['component']), 1.6))
        path = QPainterPath()
        wf = getattr(self, 'fgen_waveform', 'sin')
        x0, x1 = -hw + 6, hw - 6
        y_amp = hh - 6
        N = 48
        duty = max(0.02, min(0.98, getattr(self, 'fgen_duty', 0.5)))
        for i in range(N + 1):
            frac = i / N
            x = x0 + frac * (x1 - x0)
            if wf == 'square':
                y = -y_amp if frac < duty else y_amp
            elif wf == 'triangle':
                # +1 en frac=0.5, -1 en bordes
                y = -y_amp * (1.0 - 4.0 * abs(frac - 0.5))
            else:
                y = -y_amp * math.sin(2.0 * math.pi * frac)
            if i == 0:
                path.moveTo(x, y)
            else:
                path.lineTo(x, y)
        painter.drawPath(path)
        # Etiqueta "FGEN" arriba a la izquierda
        painter.setPen(QPen(QColor(COLORS['text_dim']), 1))
        painter.setFont(_qfont('Menlo', 6, QFont.Weight.Bold))
        painter.drawText(QRectF(-hw + 2, -hh + 1, hw * 2 - 4, 8),
                         Qt.AlignmentFlag.AlignLeft, 'FGEN')

    def _draw_osc(self, painter, pen_body, pen_wire, body_color):
        """Osciloscopio: rectángulo con mini-pantalla que muestra una onda
        decorativa y 4 pines etiquetados A+ A− B+ B−."""
        hw, hh = 30, 22
        # Cables a los 4 pines (a ±40, ±20 — múltiplos de GRID_SIZE)
        painter.setPen(pen_wire)
        painter.drawLine(QPointF(-40, -20), QPointF(-hw, -10))
        painter.drawLine(QPointF(-40,  20), QPointF(-hw,  10))
        painter.drawLine(QPointF( 40, -20), QPointF( hw, -10))
        painter.drawLine(QPointF( 40,  20), QPointF( hw,  10))
        # Cuerpo del instrumento
        painter.setPen(pen_body)
        painter.setBrush(QBrush(body_color))
        painter.drawRect(QRectF(-hw, -hh, hw * 2, hh * 2))
        # Pantalla mini (verde fosforescente clásico)
        screen = QRectF(-hw + 4, -hh + 8, hw * 2 - 8, hh * 2 - 14)
        painter.setBrush(QBrush(QColor(20, 30, 22)))
        painter.setPen(QPen(QColor(COLORS['panel_brd']), 1))
        painter.drawRect(screen)
        # Traza decorativa: un período senoidal
        painter.setPen(QPen(QColor(80, 220, 120), 1.2))
        path = QPainterPath()
        N = 36
        for i in range(N + 1):
            frac = i / N
            x = screen.left() + frac * screen.width()
            y = screen.center().y() - (screen.height() * 0.35) * math.sin(2 * math.pi * frac)
            (path.moveTo if i == 0 else path.lineTo)(x, y)
        painter.drawPath(path)
        # Etiqueta "XSC" arriba
        painter.setPen(QPen(QColor(COLORS['text_dim']), 1))
        painter.setFont(_qfont('Menlo', 6, QFont.Weight.Bold))
        painter.drawText(QRectF(-hw, -hh + 1, hw * 2, 8),
                         Qt.AlignmentFlag.AlignCenter, 'XSC')
        # Etiquetas de pines junto al cuerpo
        painter.setFont(_qfont('Menlo', 6))
        painter.setPen(QPen(QColor(COLORS['text']), 1))
        painter.drawText(QRectF(-hw, -16,  8, 10), Qt.AlignmentFlag.AlignLeft,   'A+')
        painter.drawText(QRectF(-hw,   6,  8, 10), Qt.AlignmentFlag.AlignLeft,   'A−')
        painter.drawText(QRectF( hw-9, -16, 9, 10), Qt.AlignmentFlag.AlignRight, 'B+')
        painter.drawText(QRectF( hw-9,   6, 9, 10), Qt.AlignmentFlag.AlignRight, 'B−')
        # Los pines A se dibujan en paint() como pines genéricos; B son los
        # pines tercero y cuarto y se dibujan aquí con la misma apariencia.
        painter.setPen(QPen(QColor(COLORS['pin']), 2))
        painter.setBrush(QBrush(QColor(COLORS['pin'])))
        painter.drawEllipse(QPointF(40, -20), PIN_RADIUS, PIN_RADIUS)
        painter.drawEllipse(QPointF(40, 20), PIN_RADIUS, PIN_RADIUS)

    def _draw_multimeter(self, painter, pen_body, pen_wire, body_color):
        """Multímetro estilo Multisim: cuerpo cuadrado con display, modo
        seleccionado y dos puntas de prueba (V+ rojo, V− negro)."""
        body_w, body_h = 80, 60
        x0, y0 = -body_w / 2, -body_h / 2 - 8

        # ── Cables hacia las puntas de prueba ────────────────────────────
        painter.setPen(pen_wire)
        painter.drawLine(QPointF(-30, body_h / 2 - 8), QPointF(-30, 50))
        painter.drawLine(QPointF( 30, body_h / 2 - 8), QPointF( 30, 50))

        # ── Cuerpo del instrumento ───────────────────────────────────────
        painter.setPen(pen_body)
        painter.setBrush(QBrush(body_color))
        painter.drawRoundedRect(QRectF(x0, y0, body_w, body_h), 4, 4)

        # ── Display ──────────────────────────────────────────────────────
        disp = QRectF(x0 + 6, y0 + 6, body_w - 12, 24)
        painter.setBrush(QBrush(QColor(COLORS.get('bg', '#1a1a2e'))))
        painter.setPen(QPen(QColor(COLORS.get('panel_brd', '#0f3460')), 1))
        painter.drawRect(disp)

        reading_text = self._format_meter_reading()
        painter.setPen(QPen(QColor(COLORS.get('current', '#0fff50'))))
        painter.setFont(_qfont('Menlo', 10, QFont.Weight.Bold))
        painter.drawText(disp, Qt.AlignmentFlag.AlignCenter, reading_text)

        # ── Etiqueta de modo (V/A/Ω + DC/AC) ─────────────────────────────
        mode_lbl = self._meter_mode_label()
        painter.setPen(QPen(QColor(COLORS.get('text_dim', '#a0a0a0'))))
        painter.setFont(_qfont('Menlo', 8))
        mode_rect = QRectF(x0, y0 + body_h - 22, body_w, 14)
        painter.drawText(mode_rect, Qt.AlignmentFlag.AlignCenter, mode_lbl)

        # ── Pines: V+ rojo, V− blanco/oscuro ─────────────────────────────
        painter.setPen(QPen(QColor('#e94560'), 2))
        painter.setBrush(QBrush(QColor('#e94560')))
        painter.drawEllipse(QPointF(-30, 50), PIN_RADIUS + 1, PIN_RADIUS + 1)
        painter.setPen(QPen(QColor(COLORS.get('text', '#e0e0e0')), 2))
        painter.setBrush(QBrush(QColor(COLORS.get('text', '#e0e0e0'))))
        painter.drawEllipse(QPointF( 30, 50), PIN_RADIUS + 1, PIN_RADIUS + 1)

        # Etiquetas + y − junto a los pines
        painter.setPen(QPen(QColor('#e94560')))
        painter.setFont(_qfont('Menlo', 8, QFont.Weight.Bold))
        painter.drawText(QRectF(-44, 32, 16, 14),
                         Qt.AlignmentFlag.AlignCenter, '+')
        painter.setPen(QPen(QColor(COLORS.get('text', '#e0e0e0'))))
        painter.drawText(QRectF( 28, 32, 16, 14),
                         Qt.AlignmentFlag.AlignCenter, '−')

    def _meter_mode_label(self) -> str:
        qty = getattr(self, 'meter_quantity', 'V')
        cpl = getattr(self, 'meter_coupling', 'DC')
        sym = {'V': 'V', 'A': 'A', 'OHM': 'Ω'}.get(qty, 'V')
        if qty == 'OHM':
            return sym
        return f"{sym} {cpl}"

    def _format_meter_reading(self) -> str:
        v = getattr(self, 'meter_reading', None)
        if v is None:
            return '— — —'
        unit = getattr(self, 'meter_reading_unit_hint', '')
        av = abs(v)
        if av >= 1e6:
            return f"{v/1e6:.3f} M{unit}"
        if av >= 1e3:
            return f"{v/1e3:.3f} k{unit}"
        if av >= 1 or av == 0:
            return f"{v:.3f} {unit}"
        if av >= 1e-3:
            return f"{v*1e3:.3f} m{unit}"
        if av >= 1e-6:
            return f"{v*1e6:.3f} μ{unit}"
        return f"{v:.3e} {unit}"

    def _draw_impedance(self, painter, pen_body, pen_wire, body_color):
        hw = COMP_W // 2
        hh = COMP_H // 2
        # Cables
        painter.setPen(pen_wire)
        painter.drawLine(QPointF(-hw - 10, 0), QPointF(-hw, 0))
        painter.drawLine(QPointF(hw, 0), QPointF(hw + 10, 0))
        # Cuerpo: rectángulo vacío (solo borde)
        painter.setPen(pen_body)
        painter.setBrush(QBrush(body_color))
        painter.drawRect(QRectF(-hw, -hh, COMP_W, COMP_H))

    def _draw_gnd(self, painter, pen_body):
        painter.setPen(pen_body)
        painter.drawLine(QPointF(0, 0), QPointF(0, 10))
        for i, w in enumerate([20, 14, 8]):
            y = 10 + i * 5
            painter.drawLine(QPointF(-w//2, y), QPointF(w//2, y))

    def _draw_node(self, painter, color):
        painter.setPen(QPen(color, 1))
        painter.setBrush(QBrush(color))
        painter.drawEllipse(QPointF(0, 0), 5, 5)

    def _draw_diode(self, painter, pen_body, pen_wire):
        hw = COMP_W // 2
        # Cables
        painter.setPen(pen_wire)
        painter.drawLine(QPointF(-hw - 10, 0), QPointF(-hw + 8, 0))
        painter.drawLine(QPointF(hw - 8, 0), QPointF(hw + 10, 0))
        # Triángulo (ánodo → cátodo)
        painter.setPen(pen_body)
        painter.setBrush(QBrush(QColor(COLORS['comp_body'])))
        triangle = [QPointF(-hw + 8, -12), QPointF(-hw + 8, 12), QPointF(hw - 8, 0)]
        from PyQt6.QtGui import QPolygonF
        painter.drawPolygon(QPolygonF(triangle))
        # Línea del cátodo
        painter.drawLine(QPointF(hw - 8, -12), QPointF(hw - 8, 12))

    def _draw_led(self, painter, pen_body, pen_wire):
        """Dibuja LED: apagado=gris oscuro con tinte, encendido=color sólido brillante + glow + rayos."""
        from PyQt6.QtGui import QPolygonF, QRadialGradient
        hw = COMP_W // 2

        selected       = self.isSelected()
        led_on         = getattr(self, 'led_on', False)
        led_color_name = getattr(self, 'led_color', 'red')

        # Color sólido encendido / color apagado (gris con tinte)
        color_on = {
            'red':    QColor(255,  60,  60),
            'green':  QColor( 80, 255,  80),
            'blue':   QColor( 80, 160, 255),
            'yellow': QColor(255, 240,  60),
            'white':  QColor(255, 255, 255),
            'orange': QColor(255, 170,  30),
        }
        color_off = {
            'red':    QColor( 80,  30,  30),
            'green':  QColor( 25,  70,  25),
            'blue':   QColor( 25,  35,  90),
            'yellow': QColor( 80,  75,  20),
            'white':  QColor( 70,  70,  80),
            'orange': QColor( 80,  50,  20),
        }
        on_col  = color_on.get(led_color_name, QColor(255, 40, 40))
        off_col = color_off.get(led_color_name, QColor(60, 30, 30))
        body_col = on_col if led_on else off_col

        # ── Cables ───────────────────────────────────────────────────────
        painter.setPen(pen_wire)
        painter.drawLine(QPointF(-hw - 10, 0), QPointF(-hw + 8, 0))
        painter.drawLine(QPointF(hw - 8,   0), QPointF(hw + 10, 0))

        # ── Glow halo cuando encendido ───────────────────────────────────
        if led_on:
            painter.setPen(Qt.PenStyle.NoPen)
            for radius, alpha in [(30, 30), (24, 55), (18, 90), (13, 130)]:
                gc = QColor(on_col)
                gc.setAlpha(alpha)
                painter.setBrush(QBrush(gc))
                painter.drawEllipse(QPointF(0, 0), radius, radius)

        # ── Cuerpo (triángulo relleno) ───────────────────────────────────
        # Borde: naranja si seleccionado, claro si encendido, normal si apagado
        if selected:
            outline_col = QColor(COLORS['comp_sel'])
        elif led_on:
            outline_col = on_col.lighter(160)
        else:
            outline_col = off_col.lighter(170)
        outline_pen = QPen(outline_col, 2)
        painter.setPen(outline_pen)

        triangle = [QPointF(-hw + 8, -12), QPointF(-hw + 8, 12), QPointF(hw - 8, 0)]
        if led_on:
            # Relleno con gradiente radial centrado en la punta (ánodo) para efecto brillante
            grad = QRadialGradient(QPointF(0, 0), hw)
            bright = QColor(on_col)
            bright.setAlpha(255)
            center_col = bright.lighter(180)   # núcleo casi blanco
            center_col.setAlpha(255)
            grad.setColorAt(0.0, center_col)
            grad.setColorAt(0.6, bright)
            edge_col = QColor(on_col)
            edge_col.setAlpha(200)
            grad.setColorAt(1.0, edge_col)
            painter.setBrush(QBrush(grad))
        else:
            painter.setBrush(QBrush(body_col))
        painter.drawPolygon(QPolygonF(triangle))

        # Línea del cátodo
        cathode_col = outline_col.lighter(120) if led_on else outline_col
        painter.setPen(QPen(cathode_col, 2))
        painter.drawLine(QPointF(hw - 8, -12), QPointF(hw - 8, 12))

        # ── Flechas de emisión de luz (siempre visibles) ──────────────────
        tip_x = hw - 8
        if led_on:
            arrow_col = on_col.lighter(150)
            arrow_alpha = 255
            arrow_width = 2.0
        elif selected:
            arrow_col = QColor(COLORS['comp_sel'])
            arrow_alpha = 200
            arrow_width = 1.5
        else:
            # Apagado: flechas tenues para indicar que ES un LED
            arrow_col = off_col.lighter(200)
            arrow_alpha = 120
            arrow_width = 1.2
        arrow_col.setAlpha(arrow_alpha)
        ray_pen = QPen(arrow_col, arrow_width, Qt.PenStyle.SolidLine,
                       Qt.PenCapStyle.RoundCap)
        painter.setPen(ray_pen)

        # Rayo 1 — diagonal hacia arriba-derecha
        painter.drawLine(QPointF(tip_x + 2,  -8), QPointF(tip_x + 14, -20))
        # Punta de flecha rayo 1
        painter.drawLine(QPointF(tip_x + 14, -20), QPointF(tip_x + 9, -18))
        painter.drawLine(QPointF(tip_x + 14, -20), QPointF(tip_x + 12, -14))

        # Rayo 2 — más vertical
        painter.drawLine(QPointF(tip_x + 6,  -6), QPointF(tip_x + 10, -20))
        # Punta de flecha rayo 2
        painter.drawLine(QPointF(tip_x + 10, -20), QPointF(tip_x + 6,  -17))
        painter.drawLine(QPointF(tip_x + 10, -20), QPointF(tip_x + 13, -16))

    def _draw_bulb(self, painter, pen_body, pen_wire):
        """Dibuja un bombillo redondo; encendido = brillo amarillo."""
        hw = COMP_W // 2
        on = bool(getattr(self, 'led_on', False))
        selected = self.isSelected()
        yellow = QColor(255, 224, 55)
        off = QColor(92, 78, 24)
        outline = QColor(COLORS['comp_sel']) if selected else (
            yellow.lighter(150) if on else off.lighter(160))

        painter.setPen(pen_wire)
        painter.drawLine(QPointF(-hw - 10, 0), QPointF(-28, 0))
        painter.drawLine(QPointF(28, 0), QPointF(hw + 10, 0))

        if on:
            painter.setPen(Qt.PenStyle.NoPen)
            for radius, alpha in ((34, 24), (28, 42), (22, 72), (16, 110)):
                glow = QColor(yellow)
                glow.setAlpha(alpha)
                painter.setBrush(QBrush(glow))
                painter.drawEllipse(QPointF(0, -6), radius, radius)

        painter.setPen(QPen(outline, 2))
        if on:
            gradient = QRadialGradient(QPointF(-4, -12), 30)
            gradient.setColorAt(0.0, QColor(255, 255, 220))
            gradient.setColorAt(0.35, yellow)
            gradient.setColorAt(1.0, QColor(230, 160, 18))
            painter.setBrush(QBrush(gradient))
        else:
            painter.setBrush(QBrush(off))

        bulb = QPainterPath()
        bulb.moveTo(-23, 4)
        bulb.cubicTo(-30, -20, -16, -34, 0, -34)
        bulb.cubicTo(16, -34, 30, -20, 23, 4)
        bulb.cubicTo(20, 13, 13, 15, 11, 20)
        bulb.lineTo(-11, 20)
        bulb.cubicTo(-13, 15, -20, 13, -23, 4)
        painter.drawPath(bulb)

        painter.setPen(QPen(outline, 2))
        painter.setBrush(QBrush(QColor('#a9a9a9') if not on else QColor('#d0d0d0')))
        painter.drawRoundedRect(QRectF(-12, 18, 24, 12), 3, 3)
        painter.setPen(QPen(QColor('#666666'), 1.5))
        for y in (21, 25, 29):
            painter.drawLine(QPointF(-13, y), QPointF(13, y))

        painter.setPen(QPen(yellow if on else off.lighter(190), 1.5))
        painter.drawLine(QPointF(-8, 8), QPointF(-4, -5))
        painter.drawLine(QPointF(8, 8), QPointF(4, -5))
        painter.drawArc(QRectF(-4, -8, 8, 12), 0, 180 * 16)

    def _draw_bjt(self, painter, pen_body, pen_wire):
        hw = COMP_W // 2
        hh = COMP_H // 2
        is_npn = (self.comp_type == 'BJT_NPN')

        # Círculo del cuerpo
        painter.setPen(pen_body)
        painter.setBrush(QBrush(QColor(COLORS['comp_body'])))
        painter.drawEllipse(QPointF(0, 0), hh + 4, hh + 4)

        # Base (izquierda): pin3 = (-hw-10, 0)
        painter.setPen(pen_wire)
        painter.drawLine(QPointF(-hw - 10, 0), QPointF(-8, 0))
        # Barra vertical de base
        painter.drawLine(QPointF(-8, -hh + 2), QPointF(-8, hh - 2))

        # Colector (der-arriba): p1 = (hw+10, -hh-6)
        painter.drawLine(QPointF(-8, -(hh - 2) // 2), QPointF(hw + 10, -hh - 6))
        # Emisor (der-abajo): p2 = (hw+10, hh+6)
        painter.drawLine(QPointF(-8,  (hh - 2) // 2), QPointF(hw + 10,  hh + 6))

        # Flecha en el emisor
        painter.setPen(pen_body)
        ex1 = hw + 10
        ey1 = hh + 6
        # Punto medio del cable del emisor para colocar la flecha
        mx = (-8 + ex1) // 2
        my = ((hh - 2) // 2 + ey1) // 2
        dx = ex1 - (-8)
        dy = ey1 - (hh - 2) // 2
        length = (dx**2 + dy**2) ** 0.5
        if length > 0:
            ux, uy = dx / length, dy / length   # vector unitario
            perp_x, perp_y = -uy, ux            # perpendicular
            tip_x = mx + ux * 6
            tip_y = my + uy * 6
            if is_npn:
                # Flecha apuntando hacia afuera (salida del emisor)
                painter.drawLine(QPointF(tip_x - ux*8 + perp_x*4,
                                         tip_y - uy*8 + perp_y*4),
                                  QPointF(tip_x, tip_y))
                painter.drawLine(QPointF(tip_x - ux*8 - perp_x*4,
                                         tip_y - uy*8 - perp_y*4),
                                  QPointF(tip_x, tip_y))
            else:
                # Flecha apuntando hacia adentro (PNP)
                base_x = mx - ux * 2
                base_y = my - uy * 2
                painter.drawLine(QPointF(base_x + perp_x*4, base_y + perp_y*4),
                                  QPointF(base_x, base_y))
                painter.drawLine(QPointF(base_x - perp_x*4, base_y - perp_y*4),
                                  QPointF(base_x, base_y))

        # Pines con etiquetas B / C / E
        font = _qfont('Menlo', 7, QFont.Weight.Bold)
        painter.setFont(font)
        pin_color = QColor(COLORS['pin'])

        pin_data = [
            (QPointF(hw + 10, -hh - 6), 'C'),   # Colector
            (QPointF(hw + 10,  hh + 6), 'E'),   # Emisor
            (QPointF(-hw - 10, 0),      'B'),   # Base
        ]
        for pos, label in pin_data:
            painter.setPen(QPen(pin_color, 2))
            painter.setBrush(QBrush(pin_color))
            painter.drawEllipse(pos, PIN_RADIUS, PIN_RADIUS)
            # Etiqueta al lado del pin
            painter.setPen(QPen(QColor(COLORS['text']), 1))
            offset_x = 6 if pos.x() > 0 else -14
            offset_y = -8 if pos.y() < 0 else 2
            if abs(pos.x()) < 5:  # pin central
                offset_x = 6
                offset_y = -8
            painter.drawText(QRectF(pos.x() + offset_x, pos.y() + offset_y, 14, 10),
                             Qt.AlignmentFlag.AlignLeft, label)

    def _draw_mosfet(self, painter, pen_body, pen_wire):
        hw = COMP_W // 2
        hh = COMP_H // 2
        is_nmos = (self.comp_type == 'NMOS')

        # Círculo del cuerpo
        painter.setPen(pen_body)
        painter.setBrush(QBrush(QColor(COLORS['comp_body'])))
        painter.drawEllipse(QPointF(0, 0), hh + 4, hh + 4)

        # Gate cable (izquierda → placa)
        painter.setPen(pen_wire)
        painter.drawLine(QPointF(-hw - 10, 0), QPointF(-10, 0))

        # Placa del gate
        painter.setPen(pen_body)
        painter.drawLine(QPointF(-8, -hh + 4), QPointF(-8, hh - 4))

        # Canal con gap de óxido
        gap = 4
        painter.drawLine(QPointF(-8 + gap, -hh + 4), QPointF(-8 + gap, -3))
        painter.drawLine(QPointF(-8 + gap,  3),       QPointF(-8 + gap,  hh - 4))

        # Drain (der-arriba) y Source (der-abajo)
        painter.setPen(pen_wire)
        painter.drawLine(QPointF(-8 + gap, -(hh - 4) // 2), QPointF(hw + 10, -hh - 6))
        painter.drawLine(QPointF(-8 + gap,  (hh - 4) // 2), QPointF(hw + 10,  hh + 6))

        # Flecha de canal (N: hacia canal, P: alejándose)
        painter.setPen(pen_body)
        ax = -8 + gap + 8
        if is_nmos:
            painter.drawLine(QPointF(ax - 6, 0), QPointF(ax, 0))
            painter.drawLine(QPointF(ax - 4, -3), QPointF(ax, 0))
            painter.drawLine(QPointF(ax - 4,  3), QPointF(ax, 0))
        else:
            painter.drawLine(QPointF(ax, 0), QPointF(ax - 6, 0))
            painter.drawLine(QPointF(ax - 2, -3), QPointF(ax - 6, 0))
            painter.drawLine(QPointF(ax - 2,  3), QPointF(ax - 6, 0))

        # Pines con etiquetas G / D / S
        font = _qfont('Menlo', 7, QFont.Weight.Bold)
        painter.setFont(font)
        pin_color = QColor(COLORS['pin'])

        pin_data = [
            (QPointF(hw + 10, -hh - 6), 'D',  6, -8),
            (QPointF(hw + 10, -hh - 6), 'D',  6, -8),
            (QPointF(hw + 10,  hh + 6), 'S',  6,  2),
            (QPointF(-hw - 10, 0),      'G', -14, -8),
        ]
        for pos, label, ox, oy in pin_data:
            painter.setPen(QPen(pin_color, 2))
            painter.setBrush(QBrush(pin_color))
            painter.drawEllipse(pos, PIN_RADIUS, PIN_RADIUS)
            painter.setPen(QPen(QColor(COLORS['text']), 1))
            painter.drawText(QRectF(pos.x() + ox, pos.y() + oy, 14, 10),
                             Qt.AlignmentFlag.AlignLeft, label)

    def _draw_opamp(self, painter, pen_body, pen_wire, body_color):
        hw = COMP_W // 2
        hh = COMP_H // 2 + 6
        from PyQt6.QtGui import QPolygonF

        # Triángulo del op-amp
        painter.setPen(pen_body)
        painter.setBrush(QBrush(body_color))
        triangle = [QPointF(-hw, -hh), QPointF(-hw, hh), QPointF(hw, 0)]
        painter.drawPolygon(QPolygonF(triangle))

        # Cables: salida (der), entrada+ (izq-arriba), entrada- (izq-abajo)
        painter.setPen(pen_wire)
        painter.drawLine(QPointF(hw, 0),          QPointF(hw + 10, 0))
        painter.drawLine(QPointF(-hw - 10, -hh // 2), QPointF(-hw, -hh // 2))
        painter.drawLine(QPointF(-hw - 10,  hh // 2), QPointF(-hw,  hh // 2))

        # Símbolos + y − dentro del triángulo
        painter.setPen(QPen(QColor(COLORS['component']), 2))
        painter.drawText(QRectF(-hw + 4, -hh + 4,  12, 12), Qt.AlignmentFlag.AlignCenter, '+')
        painter.drawText(QRectF(-hw + 4,  hh - 16, 12, 12), Qt.AlignmentFlag.AlignCenter, '−')

        # Pines con etiquetas
        font = _qfont('Menlo', 7, QFont.Weight.Bold)
        painter.setFont(font)
        pin_color = QColor(COLORS['pin'])

        pin_data = [
            (QPointF(hw + 10, 0),          'OUT',  6,  -4),
            (QPointF(-hw - 10, -hh // 2),  'V+',  -20,  -8),
            (QPointF(-hw - 10,  hh // 2),  'V−',  -20,   2),
        ]
        for pos, label, ox, oy in pin_data:
            painter.setPen(QPen(pin_color, 2))
            painter.setBrush(QBrush(pin_color))
            painter.drawEllipse(pos, PIN_RADIUS, PIN_RADIUS)
            painter.setPen(QPen(QColor(COLORS['text']), 1))
            painter.drawText(QRectF(pos.x() + ox, pos.y() + oy, 28, 10),
                             Qt.AlignmentFlag.AlignLeft, label)


    def _draw_tl082(self, painter, pen_body, pen_wire, body_color):
        """
        Símbolo estándar IEC/IEEE de op-amp con 5 terminales:
          • Triángulo apuntando a la derecha
          • IN+ (no-inversora) — izquierda-arriba
          • IN− (inversora)   — izquierda-abajo
          • OUT                — derecha (ápice)
          • V+                 — sale del punto medio del lado superior
          • V−                 — sale del punto medio del lado inferior

        Geometría del triángulo:
            vértice izq-arriba : (−35, −28)
            vértice izq-abajo  : (−35, +28)
            ápice derecho      : (+35,   0)
        """
        # ── Cuerpo: triángulo ─────────────────────────────────────────────
        painter.setPen(pen_body)
        painter.setBrush(QBrush(body_color))
        tri = QPolygonF([QPointF(-35, -28), QPointF(-35, 28), QPointF(35, 0)])
        painter.drawPolygon(tri)

        # ── Cables de señal ───────────────────────────────────────────────
        painter.setPen(pen_wire)
        # OUT: ápice → pin externo
        painter.drawLine(QPointF(35, 0),   QPointF(50, 0))
        # IN+: borde izquierdo (-35, -18) → pin externo
        painter.drawLine(QPointF(-50, -18), QPointF(-35, -18))
        # IN−: borde izquierdo (-35, +18) → pin externo
        painter.drawLine(QPointF(-50,  18), QPointF(-35,  18))

        # ── Cables de alimentación ────────────────────────────────────────
        # El punto de salida sobre el triángulo es la mitad geométrica de
        # cada lado inclinado, es decir x=0 → y=±14.
        # V+: (0, −14) → (0, −44)
        painter.drawLine(QPointF(0, -14), QPointF(0, -44))
        # V−: (0, +14) → (0, +44)
        painter.drawLine(QPointF(0,  14), QPointF(0,  44))

        # ── Símbolos + / − dentro del triángulo ──────────────────────────
        painter.setPen(QPen(QColor(COLORS['component']), 2))
        font_sym = _qfont('Menlo', 9, QFont.Weight.Bold)
        painter.setFont(font_sym)
        # "+" cerca de IN+ (arriba-izq)
        painter.drawText(QRectF(-30, -26, 16, 14),
                         Qt.AlignmentFlag.AlignCenter, '+')
        # "−" cerca de IN− (abajo-izq)
        painter.drawText(QRectF(-30,  12, 16, 14),
                         Qt.AlignmentFlag.AlignCenter, '−')

        # ── Letra de unidad (A / B) centrada en el triángulo ─────────────
        unit = getattr(self, 'tl082_unit', 'A')
        font_unit = _qfont('Menlo', 8, QFont.Weight.Bold)
        painter.setFont(font_unit)
        painter.setPen(QPen(QColor(COLORS['text_dim']), 1))
        painter.drawText(QRectF(-8, -8, 16, 16),
                         Qt.AlignmentFlag.AlignCenter, unit)

        # ── Pines con puntos y etiquetas ──────────────────────────────────
        font_lbl = _qfont('Menlo', 7, QFont.Weight.Bold)
        painter.setFont(font_lbl)
        pin_color = QColor(COLORS['pin'])

        # (posición_pin, etiqueta, offset_x, offset_y)
        pin_data = [
            (QPointF( 50,   0), 'OUT',  5,  -5),
            (QPointF(-50, -18), 'IN+', -26, -12),
            (QPointF(-50,  18), 'IN−', -26,   3),
            (QPointF(  0, -44), 'V+',   4,  -12),
            (QPointF(  0,  44), 'V−',   4,    3),
        ]
        for pos, label, ox, oy in pin_data:
            painter.setPen(QPen(pin_color, 2))
            painter.setBrush(QBrush(pin_color))
            painter.drawEllipse(pos, PIN_RADIUS, PIN_RADIUS)
            painter.setPen(QPen(QColor(COLORS['text']), 1))
            painter.drawText(QRectF(pos.x() + ox, pos.y() + oy, 26, 11),
                             Qt.AlignmentFlag.AlignLeft, label)

    # ──────────────────────────────────────────────────────────────────────
    # Dibujo de componentes digitales
    # ──────────────────────────────────────────────────────────────────────

    def _draw_digital_gate(self, painter, pen_body, pen_wire, body_color, label: str):
        """Cuerpo rectangular de puerta lógica con etiqueta central.

        Se usa para COMPARATOR y PWM (no tienen símbolo ANSI clásico).
        Las puertas booleanas (AND/OR/NOT/NAND/NOR/XOR) usan _draw_ansi_gate.
        """
        hw, hh, step, n = self._gate_geometry()
        # Cuerpo
        painter.setPen(pen_body)
        painter.setBrush(QBrush(body_color))
        painter.drawRoundedRect(QRectF(-hw, -hh, hw * 2, hh * 2), 4, 4)
        # Etiqueta
        painter.setPen(QPen(QColor(COLORS['component']), 2))
        font = _qfont('Menlo', 8, QFont.Weight.Bold)
        painter.setFont(font)
        painter.drawText(QRectF(-hw, -hh, hw * 2, hh * 2),
                         Qt.AlignmentFlag.AlignCenter, label)
        # Cables: entradas izquierda, salida derecha
        painter.setPen(pen_wire)
        pin_ys = self._gate_pin_ys()
        for y in pin_ys:
            painter.drawLine(QPointF(-hw - 10, y), QPointF(-hw, y))
        painter.drawLine(QPointF(hw, 0), QPointF(hw + 10, 0))
        # Pines
        pin_color = QColor(COLORS['pin'])
        for y in pin_ys:
            painter.setPen(QPen(pin_color, 2))
            painter.setBrush(QBrush(pin_color))
            painter.drawEllipse(QPointF(-hw - 10, y), PIN_RADIUS, PIN_RADIUS)
        painter.drawEllipse(QPointF(hw + 10, 0), PIN_RADIUS, PIN_RADIUS)

    # ──────────────────────────────────────────────────────────────────────
    # Puertas con simbología ANSI/IEEE estándar
    # ──────────────────────────────────────────────────────────────────────
    def _draw_ansi_gate(self, painter, pen_body, pen_wire, body_color):
        """
        Dibuja la puerta lógica usando la simbología distintiva ANSI/IEEE
        (la "tradicional" americana):

            AND  / NAND  → forma de D (rect + semielipse a la derecha)
            OR   / NOR   → forma de escudo (back cóncavo + curvas frontales
                           que confluyen en una punta)
            XOR          → como OR + curva paralela cóncava extra a la entrada
            NOT          → triángulo apuntando a la derecha

        Las versiones invertidas (NAND, NOR, NOT) llevan un círculo de
        inversión (bubble) en la salida.

        El bounding [-hw,hw] × [-hh,hh] coincide con _gate_geometry() para
        que las posiciones de los pines (y por tanto de los cables y la
        bounding-rect del componente) sigan siendo válidas.
        """
        hw, hh, step, n = self._gate_geometry()
        ct          = self.comp_type
        bubble_d    = 7                       # diámetro del bubble de inversión
        has_bubble  = ct in ('NAND', 'NOR', 'NOT')
        # Si lleva bubble, el cuerpo termina antes para dejarle hueco;
        # el bubble queda entre body_right y x=hw (donde nace el cable de salida).
        body_right  = hw - bubble_d if has_bubble else hw
        body_w      = body_right - (-hw)      # ancho del cuerpo

        painter.setPen(pen_body)
        painter.setBrush(QBrush(body_color))

        # ── Cuerpo según el tipo ──────────────────────────────────────────
        if ct in ('AND', 'NAND'):
            # Forma de D: mitad izquierda rectangular + semielipse derecha.
            path = QPainterPath()
            flat_w     = body_w * 0.5
            flat_x_end = -hw + flat_w
            arc_a      = body_w - flat_w     # = body_w * 0.5
            path.moveTo(-hw, -hh)
            path.lineTo(flat_x_end, -hh)
            # Semi-elipse: 90° (12 o'clock) sweeping -180° (clockwise) → derecha
            path.arcTo(flat_x_end - arc_a, -hh,
                       2 * arc_a, 2 * hh,
                       90, -180)
            path.lineTo(-hw, hh)
            path.closeSubpath()
            painter.drawPath(path)

        elif ct in ('OR', 'NOR', 'XOR'):
            # Forma de escudo OR: back cóncavo + dos curvas que confluyen
            # en una punta a la derecha.
            back_bulge  = body_w * 0.25      # cuán adentro entra la curva trasera
            front_pull  = body_w * 0.55      # control de las curvas frontales
            path = QPainterPath()
            path.moveTo(-hw, -hh)            # esquina superior trasera
            # Curva superior hasta la punta
            path.quadTo(-hw + front_pull, -hh,
                        body_right, 0)
            # Curva inferior desde la punta
            path.quadTo(-hw + front_pull,  hh,
                        -hw, hh)
            # Curva trasera cóncava (bulge a la derecha)
            path.quadTo(-hw + back_bulge, 0,
                        -hw, -hh)
            path.closeSubpath()
            painter.drawPath(path)

            if ct == 'XOR':
                # Curva extra paralela al back, desplazada hacia la izquierda.
                xor_offset = 5
                xor_path   = QPainterPath()
                xor_path.moveTo(-hw - xor_offset, -hh)
                xor_path.quadTo(-hw - xor_offset + back_bulge, 0,
                                -hw - xor_offset,  hh)
                # Sólo trazo, sin relleno
                painter.strokePath(xor_path, pen_body)

        elif ct == 'NOT':
            # Triángulo equilátero apuntando a la derecha
            path = QPainterPath()
            path.moveTo(-hw, -hh)
            path.lineTo(-hw,  hh)
            path.lineTo(body_right, 0)
            path.closeSubpath()
            painter.drawPath(path)

        # ── Bubble de inversión (NAND / NOR / NOT) ────────────────────────
        if has_bubble:
            # El borde derecho del bubble toca x=hw (donde sale el cable).
            bubble_cx = body_right + bubble_d / 2
            painter.drawEllipse(QPointF(bubble_cx, 0),
                                bubble_d / 2, bubble_d / 2)

        # ── Cables de conexión ────────────────────────────────────────────
        painter.setPen(pen_wire)
        pin_ys = self._gate_pin_ys()

        # Diámetro del bubble en entradas negadas; igual al de salida.
        bubble_d_in = bubble_d
        neg_mask = list(getattr(self, 'dig_input_neg', []) or [])

        def _is_neg(i: int) -> bool:
            return i < len(neg_mask) and bool(neg_mask[i])

        # Para AND/NAND/NOT el lateral es vertical → cable termina en x=-hw.
        # Para OR/NOR/XOR el back es cóncavo (curva Bezier cuadrática), por
        # lo que x varía según y. Cada cable debe terminar EXACTAMENTE sobre
        # la curva — si se queda corto deja un hueco; si se pasa, "atraviesa"
        # el cuerpo y se ve mal.
        #
        # Bezier cuadrático con extremos (-hw, ±hh) y control (-hw+back_bulge, 0):
        #   y(t) = hh·(2t − 1)        →  t = (y + hh)/(2hh)
        #   x(t) = -hw + 2t(1−t)·back_bulge
        if ct in ('OR', 'NOR', 'XOR'):
            # Para XOR los cables conectan a la curva EXTERIOR (más a la izq).
            outer_offset = 5 if ct == 'XOR' else 0
            back_bulge_eff = body_w * 0.25     # mismo back_bulge que el path
            input_back_xs = []
            for i, y in enumerate(pin_ys):
                t      = (y + hh) / (2 * hh) if hh > 0 else 0.5
                back_x = -hw - outer_offset + 2 * t * (1 - t) * back_bulge_eff
                input_back_xs.append(back_x)
                end_x = back_x - bubble_d_in if _is_neg(i) else back_x
                painter.drawLine(QPointF(-hw - 10, y), QPointF(end_x, y))
        else:
            input_back_xs = [-hw] * len(pin_ys)
            for i, y in enumerate(pin_ys):
                end_x = -hw - bubble_d_in if _is_neg(i) else -hw
                painter.drawLine(QPointF(-hw - 10, y), QPointF(end_x, y))

        painter.drawLine(QPointF(hw, 0), QPointF(hw + 10, 0))

        # ── Bubbles de inversión en entradas negadas ──────────────────────
        if any(_is_neg(i) for i in range(len(pin_ys))):
            painter.setPen(pen_body)
            painter.setBrush(QBrush(body_color))
            for i, y in enumerate(pin_ys):
                if _is_neg(i):
                    cx = input_back_xs[i] - bubble_d_in / 2
                    painter.drawEllipse(QPointF(cx, y),
                                        bubble_d_in / 2, bubble_d_in / 2)

        # ── Pines (puntos de conexión) ────────────────────────────────────
        pin_color = QColor(COLORS['pin'])
        painter.setPen(QPen(pin_color, 2))
        painter.setBrush(QBrush(pin_color))
        for y in pin_ys:
            painter.drawEllipse(QPointF(-hw - 10, y), PIN_RADIUS, PIN_RADIUS)
        painter.drawEllipse(QPointF(hw + 10, 0), PIN_RADIUS, PIN_RADIUS)

    def _draw_flipflop(self, painter, pen_body, pen_wire, body_color, ff_type: str):
        """Flip-flop con etiquetas específicas por tipo, SET/RESET y círculo de memoria.

        Layout común:
            - Cuerpo rectangular con título (DFF / JKFF / TFF / SRFF)
            - p1 = Q       (derecha-arriba)
            - p2 = Entrada principal (izquierda-arriba): D / J / T / S
            - p3 = Entrada secundaria (izquierda-abajo): CLK / K / R
            - p4 = SET     (arriba)
            - p5 = RESET   (abajo)
            - Qn dibujada como salida derecha-abajo (sin pin externo)
            - Círculo central muestra el estado actual de Q (dig_q_state)
        """
        hw, hh = COMP_W // 2, COMP_H // 2 + 8       # 30, 23
        # ── Cuerpo ────────────────────────────────────────────────────────
        painter.setPen(pen_body)
        painter.setBrush(QBrush(body_color))
        painter.drawRect(QRectF(-hw, -hh, hw * 2, hh * 2))

        # ── Etiqueta del tipo (parte superior) ────────────────────────────
        title = {'DFF': 'D-FF', 'JKFF': 'JK-FF',
                 'TFF': 'T-FF', 'SRFF': 'SR-FF'}[ff_type]
        painter.setPen(QPen(QColor(COLORS['component']), 2))
        font = _qfont('Menlo', 7, QFont.Weight.Bold)
        painter.setFont(font)
        painter.drawText(QRectF(-hw, -hh + 1, hw * 2, 11),
                         Qt.AlignmentFlag.AlignCenter, title)

        # ── Pines izquierda (entrada principal arriba, secundaria abajo) ──
        painter.setPen(pen_wire)
        painter.drawLine(QPointF(-hw - 10, -hh // 2), QPointF(-hw, -hh // 2))
        painter.drawLine(QPointF(-hw - 10,  hh // 2), QPointF(-hw,  hh // 2))
        # Pines derecha (Q arriba, Qn abajo)
        painter.drawLine(QPointF(hw, -hh // 2), QPointF(hw + 10, -hh // 2))
        painter.drawLine(QPointF(hw,  hh // 2), QPointF(hw + 10,  hh // 2))
        # Pines verticales: SET arriba, RESET abajo
        painter.drawLine(QPointF(0, -hh), QPointF(0, -hh - 10))
        painter.drawLine(QPointF(0,  hh), QPointF(0,  hh + 10))

        # ── Símbolo de reloj (triángulo) en el pin de CLK ─────────────────
        # SRFF y JKFF no tienen CLK como pin físico (SRFF asíncrono;
        # JKFF usa el net global dig_clk).
        if ff_type in ('DFF', 'TFF'):
            painter.setPen(QPen(QColor(COLORS['component']), 1.5))
            cy = hh // 2
            painter.drawLine(QPointF(-hw, cy - 5), QPointF(-hw + 6, cy))
            painter.drawLine(QPointF(-hw + 6, cy), QPointF(-hw, cy + 5))

        # ── Etiquetas de pin internas ─────────────────────────────────────
        labels = {
            'DFF':  ('D',  'CLK'),
            'JKFF': ('J',  'K'),     # K en lugar de CLK como secundaria
            'TFF':  ('T',  'CLK'),
            'SRFF': ('S',  'R'),
        }[ff_type]
        # Para JKFF, la entrada CLK aparece etiquetada también: el flip-flop JK
        # usa j/k/clk pero solo tenemos 2 pines de entrada laterales. Convención:
        # p2 = J (arriba), p3 = K (abajo); CLK se asume en el net p3 también
        # (la simulación lo enruta vía dig_clk como nombre de net global).
        font2 = _qfont('Menlo', 6)
        painter.setFont(font2)
        painter.setPen(QPen(QColor(COLORS['text_dim']), 1))
        painter.drawText(QRectF(-hw + 2, -hh // 2 - 8, 18, 10),
                         Qt.AlignmentFlag.AlignLeft, labels[0])
        painter.drawText(QRectF(-hw + 2,  hh // 2 - 8, 22, 10),
                         Qt.AlignmentFlag.AlignLeft, labels[1])
        painter.drawText(QRectF(hw - 14, -hh // 2 - 8, 14, 10),
                         Qt.AlignmentFlag.AlignRight, 'Q')
        painter.drawText(QRectF(hw - 14,  hh // 2 - 8, 18, 10),
                         Qt.AlignmentFlag.AlignRight, 'Q̄')
        # Etiquetas SET / RESET (encima/debajo del cuerpo, junto a sus pines)
        painter.drawText(QRectF(2, -hh - 12, 28, 10),
                         Qt.AlignmentFlag.AlignLeft, 'S')
        painter.drawText(QRectF(2,  hh + 2, 28, 10),
                         Qt.AlignmentFlag.AlignLeft, 'R')

        # ── Círculo central de memoria ────────────────────────────────────
        # Verde brillante = Q vale 1, gris oscuro = Q vale 0.
        q = 1 if int(getattr(self, 'dig_q_state', 0)) else 0
        mem_r = 7
        mem_color = QColor('#27ae60') if q else QColor(COLORS['comp_body']).darker(125)
        mem_border = QColor(COLORS['component'])
        painter.setPen(QPen(mem_border, 1.5))
        painter.setBrush(QBrush(mem_color))
        painter.drawEllipse(QPointF(0, 1), mem_r, mem_r)
        # Dígito interno (1 ó 0) en blanco
        painter.setPen(QPen(QColor('white' if q else '#7f8c8d'), 1))
        font_q = _qfont('Menlo', 8, QFont.Weight.Bold)
        painter.setFont(font_q)
        painter.drawText(QRectF(-mem_r, 1 - mem_r, mem_r * 2, mem_r * 2),
                         Qt.AlignmentFlag.AlignCenter, str(q))

        # ── Puntos de pin ─────────────────────────────────────────────────
        pin_color = QColor(COLORS['pin'])
        painter.setPen(QPen(pin_color, 2))
        painter.setBrush(QBrush(pin_color))
        for px, py in [(-hw - 10, -hh // 2), (-hw - 10, hh // 2),
                       (hw + 10, -hh // 2), (hw + 10, hh // 2),
                       (0, -hh - 10), (0, hh + 10)]:
            painter.drawEllipse(QPointF(px, py), PIN_RADIUS, PIN_RADIUS)

    def _draw_timer555(self, painter, pen_body, pen_wire, body_color):
        """Encapsulado DIP-8 del NE555 con el pinout físico estándar."""
        painter.setPen(pen_body)
        painter.setBrush(QBrush(body_color))
        painter.drawRoundedRect(QRectF(-60, -72, 120, 144), 4, 4)
        painter.setPen(QPen(QColor(COLORS['component']), 2))
        painter.setFont(_qfont('Menlo', 11, QFont.Weight.Bold))
        painter.drawText(QRectF(-56, -13, 112, 20), Qt.AlignmentFlag.AlignCenter, 'NE555')
        painter.setFont(_qfont('Menlo', 6))
        labels = ('1 GND', '2 TRIG', '3 OUT', '4 RESET',
                  '5 CTRL', '6 THRESH', '7 DISCH', '8 VCC')
        for pin, label in zip(self._timer_pin_positions(), labels):
            inner = QPointF(-60 if pin.x() < 0 else 60, pin.y())
            painter.setPen(pen_wire)
            painter.drawLine(pin, inner)
            painter.setPen(QPen(QColor(COLORS['text_dim']), 1))
            rect = QRectF(-58, pin.y() - 6, 46, 12) if pin.x() < 0 else QRectF(12, pin.y() - 6, 46, 12)
            painter.drawText(rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, label)
            painter.setPen(QPen(QColor(COLORS['pin']), 2))
            painter.setBrush(QBrush(QColor(COLORS['pin'])))
            painter.drawEllipse(pin, PIN_RADIUS, PIN_RADIUS)

    def _draw_clk(self, painter, pen_body, pen_wire, body_color):
        """Reloj digital: cuadrado con onda cuadrada y dígito 0/1 grande.

        Doble-click: conmuta manualmente (como LOGIC_STATE).
        Ctrl+K (con el componente seleccionado): activa/desactiva oscilación
        automática a la frecuencia configurada en Herramientas → Frecuencia CLK.
        """
        hw = COMP_W // 2
        hh = COMP_H // 2
        state = int(self.value) & 1

        # Color de fondo según estado y modo
        if self.clk_running:
            col_off = QColor('#2980b9')   # azul (oscilando, parte LOW)
            col_on  = QColor('#3498db')
        else:
            col_off = QColor('#7f8c8d')   # gris (manual, parte LOW)
            col_on  = QColor('#bdc3c7')
        fill = col_on if state else col_off

        painter.setPen(pen_body)
        painter.setBrush(QBrush(fill))
        painter.drawRoundedRect(QRectF(-hw, -hh, hw * 2, hh * 2), 4, 4)

        # Onda cuadrada como icono central (esquina superior izquierda)
        painter.setPen(QPen(QColor('white'), 1.5))
        wx0 = -hw + 4
        wy0 = -hh + 4
        wave_h = 8
        path = QPainterPath()
        path.moveTo(wx0,        wy0 + wave_h)
        path.lineTo(wx0 + 4,    wy0 + wave_h)
        path.lineTo(wx0 + 4,    wy0)
        path.lineTo(wx0 + 10,   wy0)
        path.lineTo(wx0 + 10,   wy0 + wave_h)
        path.lineTo(wx0 + 16,   wy0 + wave_h)
        path.lineTo(wx0 + 16,   wy0)
        path.lineTo(wx0 + 22,   wy0)
        painter.drawPath(path)

        # Dígito grande del estado
        font_big = _qfont('Menlo', 18, QFont.Weight.Bold)
        painter.setFont(font_big)
        painter.setPen(QPen(QColor('white'), 2))
        painter.drawText(QRectF(-hw, -hh + 4, hw * 2, hh * 2),
                         Qt.AlignmentFlag.AlignCenter, str(state))

        # Pin de salida (derecha)
        painter.setPen(pen_wire)
        painter.drawLine(QPointF(hw, 0), QPointF(hw + 10, 0))
        pin_color = QColor(COLORS['pin'])
        painter.setPen(QPen(pin_color, 2))
        painter.setBrush(QBrush(pin_color))
        painter.drawEllipse(QPointF(hw + 10, 0), PIN_RADIUS, PIN_RADIUS)

    def _draw_adc_dac(self, painter, pen_body, pen_wire, body_color, is_adc: bool):
        """Bloque ADC o DAC con flecha de conversión y datos de configuración."""
        hw, hh = COMP_W // 2, COMP_H // 2 + 6
        # Cuerpo
        painter.setPen(pen_body)
        painter.setBrush(QBrush(body_color))
        painter.drawRect(QRectF(-hw, -hh, hw * 2, hh * 2))
        # Etiqueta principal
        lbl = 'ADC' if is_adc else 'DAC'
        font = _qfont('Menlo', 9, QFont.Weight.Bold)
        painter.setFont(font)
        painter.setPen(QPen(QColor(COLORS['component']), 2))
        painter.drawText(QRectF(-hw, -hh, hw * 2, hh * 2),
                         Qt.AlignmentFlag.AlignCenter, lbl)
        # Flecha de conversión
        painter.setPen(QPen(QColor(COLORS['text_dim']), 1))
        font2 = _qfont('Menlo', 6)
        painter.setFont(font2)
        if is_adc:
            painter.drawText(QRectF(-hw + 2, 4, hw * 2 - 4, 12),
                             Qt.AlignmentFlag.AlignCenter,
                             f'{self.dig_bits_adc}b {self.dig_vref:.1f}V')
        else:
            painter.drawText(QRectF(-hw + 2, 4, hw * 2 - 4, 12),
                             Qt.AlignmentFlag.AlignCenter,
                             f'{self.dig_bits_adc}b {self.dig_vref:.1f}V')
        # Pins: izquierda=analógico, derecha=digital
        painter.setPen(pen_wire)
        painter.drawLine(QPointF(-hw - 10, 0), QPointF(-hw, 0))  # analógico
        painter.drawLine(QPointF(hw, 0), QPointF(hw + 10, 0))    # digital
        pin_color = QColor(COLORS['pin'])
        for px in [-hw - 10, hw + 10]:
            painter.setPen(QPen(pin_color, 2))
            painter.setBrush(QBrush(pin_color))
            painter.drawEllipse(QPointF(px, 0), PIN_RADIUS, PIN_RADIUS)
        # Etiquetas de pin
        painter.setPen(QPen(QColor(COLORS['text_dim']), 1))
        painter.setFont(font2)
        painter.drawText(QRectF(-hw - 24, -6, 20, 10), Qt.AlignmentFlag.AlignRight, 'A')
        painter.drawText(QRectF(hw + 4, -6, 20, 10), Qt.AlignmentFlag.AlignLeft, 'D')

    def _draw_logic_state(self, painter, pen_body, pen_wire, body_color):
        """Botón de estado lógico: cuadrado con 1/0 grande, un pin de salida."""
        hw = COMP_W // 2
        hh = COMP_H // 2
        state = int(self.value)   # 0 o 1
        # Cuerpo — color según estado
        col_on  = QColor('#27ae60')   # verde = HIGH
        col_off = QColor('#c0392b')   # rojo  = LOW
        fill = col_on if state else col_off
        painter.setPen(pen_body)
        painter.setBrush(QBrush(fill))
        painter.drawRoundedRect(QRectF(-hw, -hh, hw * 2, hh * 2), 6, 6)
        # Dígito grande
        font_big = _qfont('Menlo', 22, QFont.Weight.Bold)
        painter.setFont(font_big)
        painter.setPen(QPen(QColor('white'), 2))
        painter.drawText(QRectF(-hw, -hh, hw * 2, hh * 2),
                         Qt.AlignmentFlag.AlignCenter, str(state))
        # Pin de salida (derecha)
        painter.setPen(pen_wire)
        painter.drawLine(QPointF(hw, 0), QPointF(hw + 10, 0))
        pin_color = QColor(COLORS['pin'])
        painter.setPen(QPen(pin_color, 2))
        painter.setBrush(QBrush(pin_color))
        painter.drawEllipse(QPointF(hw + 10, 0), PIN_RADIUS, PIN_RADIUS)

    def _draw_counter(self, painter, pen_body, pen_wire, body_color):
        """Contador binario N-bit."""
        hw, hh = COMP_W // 2, self._counter_height()
        painter.setPen(pen_body)
        painter.setBrush(QBrush(body_color))
        painter.drawRect(QRectF(-hw, -hh, hw * 2, hh * 2))
        font = _qfont('Menlo', 7, QFont.Weight.Bold)
        painter.setFont(font)
        painter.setPen(QPen(QColor(COLORS['component']), 2))
        count = int(getattr(self, 'dig_count_state', 0))
        bits_lbl = f'CNT {count:0{max(1, self.dig_bits)}b}'
        painter.drawText(QRectF(-hw, -hh, hw * 2, hh * 2),
                         Qt.AlignmentFlag.AlignCenter, bits_lbl)
        # CLK a la izquierda, una salida Q por cada bit a la derecha.
        painter.setPen(pen_wire)
        painter.drawLine(QPointF(-hw - 10, 0), QPointF(-hw, 0))
        outputs = self._counter_output_positions()
        for i, p in enumerate(outputs):
            painter.drawLine(QPointF(hw, p.y()), p)
            painter.setFont(_qfont('Menlo', 6))
            painter.setPen(QPen(QColor(COLORS['text_dim']), 1))
            painter.drawText(QRectF(hw - 19, p.y() - 6, 16, 12),
                             Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                             f'Q{i}')
            painter.setPen(pen_wire)
        pin_color = QColor(COLORS['pin'])
        painter.setPen(QPen(pin_color, 2))
        painter.setBrush(QBrush(pin_color))
        painter.drawEllipse(QPointF(-hw - 10, 0), PIN_RADIUS, PIN_RADIUS)
        for p in outputs:
            painter.drawEllipse(p, PIN_RADIUS, PIN_RADIUS)

    def _draw_mux(self, painter, pen_body, pen_wire, body_color):
        """MUX 2:1 — geometría consistente con pin_positions/pin3/pin4."""
        hw = COMP_W // 2
        _, hh, _, _ = self._gate_geometry()
        ys = self._gate_pin_ys()           # [y_I0, y_I1]
        painter.setPen(pen_body)
        painter.setBrush(QBrush(body_color))
        painter.drawRect(QRectF(-hw, -hh, hw * 2, hh * 2))
        font = _qfont('Menlo', 7, QFont.Weight.Bold)
        painter.setFont(font)
        painter.setPen(QPen(QColor(COLORS['component']), 2))
        painter.drawText(QRectF(-hw, -hh, hw * 2, hh * 2),
                         Qt.AlignmentFlag.AlignCenter, 'MUX 2:1')
        # Cables: I0/I1 a la izquierda, salida a la derecha, SEL abajo-centro
        painter.setPen(pen_wire)
        for y in ys:
            painter.drawLine(QPointF(-hw - 10, y), QPointF(-hw, y))
        painter.drawLine(QPointF(0, hh), QPointF(0, hh + 10))        # sel
        painter.drawLine(QPointF(hw, 0), QPointF(hw + 10, 0))
        # Etiquetas de pin
        painter.setFont(_qfont('Menlo', 6))
        painter.setPen(QPen(QColor(COLORS['text_dim']), 1))
        painter.drawText(QRectF(-hw + 2, ys[0] - 6, 16, 12),
                         Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, '0')
        painter.drawText(QRectF(-hw + 2, ys[1] - 6, 16, 12),
                         Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, '1')
        painter.drawText(QRectF(-12, hh - 14, 24, 12),
                         Qt.AlignmentFlag.AlignCenter, 'S')
        pin_color = QColor(COLORS['pin'])
        painter.setPen(QPen(pin_color, 2))
        painter.setBrush(QBrush(pin_color))
        for px, py in [(-hw - 10, ys[0]), (-hw - 10, ys[1]),
                       (0, hh + 10), (hw + 10, 0)]:
            painter.drawEllipse(QPointF(px, py), PIN_RADIUS, PIN_RADIUS)

    def _draw_sheet_connector(self, painter, pen_body, pen_wire, body_color):
        """Net label como flecha pequeña del mismo color que GND, apuntando a la derecha.

        INPUT  (entrada):  ─►●     pin en la CABEZA (extremo derecho)
        OUTPUT (salida):   ●─►     pin en la COLA   (extremo izquierdo)
        """
        is_input = self.comp_type == 'NET_LABEL_IN'
        label = self.sheet_label or self.name

        # Mismo color que GND: pen_body (line_color = COLORS['component'])
        arrow_pen = pen_body
        arrow_color = arrow_pen.color()
        tip_sz = 6

        # Flecha de ~30 px de largo (similar al ancho de GND)
        tail_x = -15
        head_x =  15
        pin_x  = head_x if is_input else tail_x

        # ── Línea + cabeza de la flecha ───────────────────────────────────
        painter.setPen(arrow_pen)
        painter.setBrush(QBrush(arrow_color))
        head_base_x = head_x - tip_sz
        painter.drawLine(QPointF(tail_x, 0), QPointF(head_base_x, 0))
        arrow = QPolygonF([
            QPointF(head_x, 0),                          # punta
            QPointF(head_base_x, -tip_sz * 0.55),
            QPointF(head_base_x,  tip_sz * 0.55),
        ])
        painter.drawPolygon(arrow)

        # ── Etiqueta encima de la flecha ──────────────────────────────────
        font = _qfont('Menlo', 7, QFont.Weight.Bold)
        painter.setFont(font)
        painter.setPen(QPen(QColor(COLORS['text']), 1))
        text_rect = QRectF(tail_x - 4, -15, (head_x - tail_x) + 8, 11)
        painter.drawText(
            text_rect,
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignHCenter,
            label,
        )

        # ── Pin de conexión ───────────────────────────────────────────────
        pin_color = QColor(COLORS['pin'])
        painter.setPen(QPen(pin_color, 2))
        painter.setBrush(QBrush(pin_color))
        painter.drawEllipse(QPointF(pin_x, 0), PIN_RADIUS, PIN_RADIUS)

    def _draw_port(self, painter, pen_body, pen_wire, body_color):
        """Puerto de subcircuito: banderín con un único pin a la derecha."""
        col = QColor(COLORS['comp_sel'] if self.isSelected() else COLORS['component'])
        painter.setPen(QPen(col, 2))
        painter.setBrush(QBrush(QColor(COLORS['comp_body'])))
        # Banderín hexagonal apuntando a la derecha hacia el pin
        flag = QPolygonF([
            QPointF(-22, -8), QPointF(4, -8), QPointF(12, 0),
            QPointF(4, 8), QPointF(-22, 8),
        ])
        painter.drawPolygon(flag)
        # Cable al pin
        painter.setPen(pen_wire)
        painter.drawLine(QPointF(12, 0), QPointF(16, 0))
        # Etiqueta (port_name) dentro / encima
        painter.setFont(_qfont('Menlo', 7, QFont.Weight.Bold))
        painter.setPen(QPen(QColor(COLORS['text']), 1))
        painter.drawText(QRectF(-22, -8, 30, 16),
                         Qt.AlignmentFlag.AlignCenter,
                         self.port_name or self.name)
        # Dirección encima
        painter.setFont(_qfont('Menlo', 6))
        painter.setPen(QPen(QColor(COLORS['text_dim']), 1))
        painter.drawText(QRectF(-26, -20, 52, 10),
                         Qt.AlignmentFlag.AlignCenter,
                         (self.port_dir or 'in').upper())
        # Pin
        painter.setPen(QPen(QColor(COLORS['pin']), 2))
        painter.setBrush(QBrush(QColor(COLORS['pin'])))
        painter.drawEllipse(QPointF(16, 0), PIN_RADIUS, PIN_RADIUS)

    def _draw_subcircuit(self, painter, pen_body, pen_wire, body_color):
        """Bloque tipo circuito integrado con pines configurables."""
        w, h, _ = self._subckt_geometry()
        sel = self.isSelected()
        body = QColor(self.ic_body_color) if self.ic_body_color else None
        if body is None or not body.isValid():
            body = QColor(COLORS['comp_sel'] if sel else COLORS['comp_body'])
        edge = QColor(COLORS['comp_sel'] if sel else COLORS['component'])
        txt = QColor(self.ic_text_color) if self.ic_text_color else None
        if txt is None or not txt.isValid():
            txt = QColor(COLORS['text'])

        rect = QRectF(-w / 2, -h / 2, w, h)
        painter.setPen(QPen(edge, 2))
        painter.setBrush(QBrush(body))
        painter.drawRoundedRect(rect, 4, 4)
        # Muesca superior (orientación del IC)
        painter.setPen(QPen(edge, 1.5))
        painter.drawArc(QRectF(-6, -h / 2 - 6, 12, 12), 180 * 16, 180 * 16)

        pts = self._subckt_pin_points()
        painter.setFont(_qfont('Menlo', 6))
        for i, p in enumerate(pts):
            side = (self.ic_pins[i].get('side', 'left')
                    if i < len(self.ic_pins) else 'left')
            # Cable cuerpo→pin
            painter.setPen(pen_wire)
            if side == 'left':
                painter.drawLine(QPointF(-w / 2, p.y()), p)
                tr = QRectF(-w / 2 + 3, p.y() - 7, w / 2 - 6, 14)
                al = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
            elif side == 'right':
                painter.drawLine(QPointF(w / 2, p.y()), p)
                tr = QRectF(3, p.y() - 7, w / 2 - 6, 14)
                al = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            elif side == 'top':
                painter.drawLine(QPointF(p.x(), -h / 2), p)
                tr = QRectF(p.x() - 16, -h / 2 + 2, 32, 12)
                al = Qt.AlignmentFlag.AlignCenter
            else:
                painter.drawLine(QPointF(p.x(), h / 2), p)
                tr = QRectF(p.x() - 16, h / 2 - 14, 32, 12)
                al = Qt.AlignmentFlag.AlignCenter
            painter.setPen(QPen(QColor(COLORS['pin']), 2))
            painter.setBrush(QBrush(QColor(COLORS['pin'])))
            painter.drawEllipse(p, PIN_RADIUS, PIN_RADIUS)
            name = (self.ic_pins[i].get('name', '')
                    if i < len(self.ic_pins) else '')
            painter.setPen(QPen(txt, 1))
            painter.drawText(tr, al, name)

        # Label central
        label = self.ic_label or self.subckt_name or 'SUB'
        painter.setPen(QPen(txt, 1))
        painter.setFont(_qfont('Menlo', 9, QFont.Weight.Bold))
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, label)
        # Nombre de instancia encima del cuerpo
        painter.setFont(_qfont('Menlo', 8))
        painter.setPen(QPen(QColor(COLORS['text']), 1))
        painter.drawText(QRectF(-w / 2, -h / 2 - 18, w, 16),
                         Qt.AlignmentFlag.AlignCenter, self.name)

    def _draw_labels(self, painter, text_color):
        if isinstance(painter, _MonochromePainter):
            painter.upright_text = False
        self._draw_labels_content(painter, text_color)
        if isinstance(painter, _MonochromePainter):
            painter.upright_text = True

    def _draw_labels_content(self, painter, text_color):
        if self.comp_type in ('GND', 'NODE', 'NET_LABEL_IN', 'NET_LABEL_OUT',
                               'PORT', 'SUBCKT'):
            return
        font = _qfont('Menlo', 8)
        if self.scene() is not None and getattr(self.scene(), 'print_mode', False):
            font = QFont(font)
            font.setPixelSize(8)
        painter.setFont(font)
        painter.setPen(QPen(text_color))

        # TL082: etiquetas desplazadas para no solaparse con los pines de alimentación
        if self.comp_type == 'TL082':
            painter.drawText(QRectF(-35, -56, 70, 13),
                             Qt.AlignmentFlag.AlignCenter, self.name)
            return

        # Multímetro: nombre arriba del cuerpo, sin "valor" abajo (el display
        # del propio cuerpo ya muestra la lectura)
        if self.comp_type == 'MULTIMETER':
            name_rect = QRectF(-50, -50 - 16, 100, 14)
            painter.drawText(name_rect, Qt.AlignmentFlag.AlignCenter, self.name)
            return

        # Nombre arriba
        if self.comp_type == 'LAMP':
            name_y = -52
        elif self.comp_type == 'DPDT':
            name_y = -60
        else:
            name_y = -COMP_H//2 - 18
        name_rect = QRectF(-COMP_W//2, name_y, COMP_W, 16)
        painter.drawText(name_rect, Qt.AlignmentFlag.AlignCenter, self.name)

        # Valor abajo
        if self.value != 0 and self.comp_type not in ('COUNTER', 'SPST', 'SPDT', 'SPDT3', 'DPDT', 'RELAY'):
            val_str = self._format_value()
            value_y = 38 if self.comp_type == 'LAMP' else COMP_H//2 + 2
            val_rect = QRectF(-COMP_W//2, value_y, COMP_W, 16)
            painter.setPen(QPen(QColor(COLORS['text_dim'])))
            painter.drawText(val_rect, Qt.AlignmentFlag.AlignCenter, val_str)

        # Resultado de simulación
        if self.result_voltage is not None:
            res_str = f"{self.result_voltage:.3f}V"
            result_y = 54 if self.comp_type == 'LAMP' else COMP_H//2 + 16
            res_rect = QRectF(-COMP_W//2, result_y, COMP_W, 16)
            painter.setPen(QPen(QColor(COLORS['voltage'])))
            painter.drawText(res_rect, Qt.AlignmentFlag.AlignCenter, res_str)
