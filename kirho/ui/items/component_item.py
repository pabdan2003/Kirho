"""
ComponentItem — representación visual de un componente en el canvas
de circuitos. Soporta drag, selección, doble-click para editar y
rotación/espejado. Extraído de main.py.
"""
from typing import Optional, List, Tuple

from PyQt6.QtWidgets import QGraphicsItem
from PyQt6.QtGui import QPainter, QTransform
from PyQt6.QtCore import QPointF, QRectF

from kirho.ui.style import GRID_SIZE, COMP_W, COMP_H, PIN_RADIUS, format_si_value
from kirho.ui.items.component_painter import ComponentPainter, _MonochromePainter

# ══════════════════════════════════════════════════════════════
# ÍTEM DE COMPONENTE EN EL CANVAS
# ══════════════════════════════════════════════════════════════
class ComponentItem(QGraphicsItem):
    """
    Representación visual de un componente en el canvas.
    Soporta drag, selección y doble-click para editar propiedades.
    """

    COMP_TYPES = ['R', 'POT', 'V', 'VAC', 'I', 'C', 'L', 'Z', 'GND', 'NODE',
                  'D', 'LED', 'LAMP', 'BJT_NPN', 'BJT_PNP', 'NMOS', 'PMOS', 'OPAMP',
                  'TL082',
                  'XFMR', 'BRIDGE', 'SPST', 'SPDT', 'SPDT3', 'DPDT', 'RELAY',
                  # ── Instrumentos ──
                  'FGEN', 'OSC', 'MULTIMETER',
                  # ── Digital ──
                  'AND', 'OR', 'NOT', 'NAND', 'NOR', 'XOR',
                  'DFF', 'JKFF', 'TFF', 'SRFF',
                  'MUX2', 'COUNTER', 'IC555',
                  'ADC_BRIDGE', 'DAC_BRIDGE', 'COMPARATOR', 'PWM',
                  'CLK',
                  # ── Inter-hoja ──
                  'NET_LABEL_IN', 'NET_LABEL_OUT',
                  # ── Subcircuitos ──
                  'PORT', 'SUBCKT']

    # Instrumentos virtuales (panel frontal independiente).
    INSTRUMENT_TYPES = {'FGEN', 'OSC', 'MULTIMETER'}
    LIGHT_TYPES = {'LED', 'LAMP'}

    # Tipos analógicos con 4 terminales (necesitan p3 y p4)
    FOUR_PIN_TYPES = {'XFMR', 'BRIDGE', 'OSC', 'RELAY'}

    # Tipos analógicos con 5 terminales (necesitan p3, p4 y p5)
    FIVE_PIN_TYPES = {'TL082'}

    # Tipos analógicos con 6 terminales (necesitan p3…p6)
    SIX_PIN_TYPES = {'DPDT'}

    # Tipos de flip-flop con SET/RESET (4 inputs lógicos + Q,Qn)
    FLIPFLOP_TYPES = {'DFF', 'JKFF', 'TFF', 'SRFF'}
    TIMER_TYPES = {'IC555'}

    # Tipos que pertenecen al dominio digital (no se pasan al MNA)
    DIGITAL_TYPES = {
        'AND', 'OR', 'NOT', 'NAND', 'NOR', 'XOR',
        'DFF', 'JKFF', 'TFF', 'SRFF',
        'MUX2', 'COUNTER', 'IC555',
        'ADC_BRIDGE', 'DAC_BRIDGE', 'COMPARATOR', 'PWM',
        'LOGIC_STATE', 'CLK',
    }

    def __init__(self, comp_type: str, name: str, value: float = 0.0,
                 unit: str = '', node1: str = '', node2: str = '', node3: str = ''):
        super().__init__()
        self.comp_type = comp_type
        self.name = name
        self.value = value
        self.unit = unit
        self.node1 = node1
        self.node2 = node2
        self.node3 = node3
        # Nombres opcionales de red para los ocho pines del NE555. Los cables
        # siguen teniendo prioridad; estos campos sirven para editar el CI.
        self.timer_nodes: list = ['', '', '', '', '', '', '', '']
        # Atributos extra para fuente AC
        self.frequency: float = 60.0    # Hz
        self.phase_deg: float = 0.0     # grados
        self.ac_mode:   str   = 'rms'   # 'rms' o 'peak'
        self.result_voltage: Optional[float] = None
        self._angle = 0  # rotación en grados (0, 90, 180, 270)
        self._flip_x: bool = False  # invertir en eje X (horizontal)
        self._flip_y: bool = False  # invertir en eje Y (vertical)
        # Estado de iluminación (LED y bombillo)
        self.led_color: str  = 'red'   # color del LED
        self.led_on:    bool = False    # encendido si supera su umbral
        # Atributos para impedancia genérica
        self.z_mode:   str   = 'rect'   # 'rect' o 'phasor'
        self.z_real:   float = 100.0    # Ω (parte real)
        self.z_imag:   float = 0.0      # Ω (parte imag)
        self.z_mag:    float = 100.0    # Ω (magnitud fasorial)
        self.z_phase:  float = 0.0      # ° (fase fasorial)

        # ── Atributos para componentes digitales ────────────────────────────
        # Puerta: número de entradas (AND/OR/etc.)
        self.dig_inputs:   int   = 2
        # Flip-flop / contador: bits de salida
        self.dig_bits:     int   = 1
        # ADC/DAC: resolución y Vref
        self.dig_bits_adc: int   = 8
        self.dig_vref:     float = 3.3
        # Señal de reloj (nombre de net digital)
        self.dig_clk:      str   = 'CLK'
        # Retardo de propagación (ns)
        self.dig_tpd_ns:   float = 1.0
        # Nodo analógico que conecta al MNA (ADC/DAC/Comparador)
        self.dig_analog_node: str = ''
        # Nodos de entradas extra (entrada 3, 4, ... N) para puertas multi-entrada
        self.dig_input_nodes: list = []   # ['net_A', 'net_B', ...]
        # Máscara de negación por entrada (alineada con la lista total de
        # entradas: [entrada1, entrada2, ...]). Si una posición es True el
        # valor de esa entrada se invierte antes de evaluar la compuerta y
        # se dibuja un bubble (círculo) sobre el pin de entrada.
        self.dig_input_neg: list = []

        # ── Atributos analógicos extendidos ─────────────────────────────────
        # Potenciómetro: posición del cursor (0.0 a 1.0). El valor base se
        # guarda en self.value (R_total).  R_efectiva = value * pot_wiper.
        self.pot_wiper: float = 0.5
        # Transformador: relación de transformación (n=N1/N2) y corriente máx
        self.xfmr_ratio: float = 2.0          # primario:secundario (n)
        self.xfmr_imax:  float = 1.0          # corriente nominal del primario (A)
        # Puente rectificador: tensión directa de cada diodo (informativa)
        self.bridge_vf: float = 0.7
        # Relé: tensión mínima de bobina que cierra el contacto NO.
        self.relay_activation_voltage: float = 3.0
        self.relay_active: bool = False
        # Cuarto nodo para componentes de 4 terminales
        self.node4: str = ''
        self.node6: str = ''
        self.switch_key: str = ''
        self.switch_on1_key: str = ''
        self.switch_off_key: str = ''
        self.switch_on2_key: str = ''

        # Etiqueta de net label inalámbrico
        self.sheet_label: str = ''

        # ── Generador de funciones (FGEN) ───────────────────────────────────
        # Reutiliza self.value (amplitud), self.frequency, self.phase_deg,
        # self.ac_mode ('rms'/'peak'). Estos atributos extra controlan la
        # forma de onda — equivalen a los del VoltageSourceAC del motor.
        self.fgen_waveform: str   = 'sin'   # 'sin' | 'square' | 'triangle'
        self.fgen_offset:   float = 0.0     # V DC sumados a la onda
        self.fgen_duty:     float = 0.5     # ciclo de trabajo (solo square), 0..1

        # ── Osciloscopio (OSC) ──────────────────────────────────────────────
        # Configuración del panel. Buffers de muestras viven en el diálogo
        # (no en el item) para mantener el item liviano.
        self.osc_time_div:    float = 1e-3   # segundos por división (10 div totales)
        self.osc_v_div_a:     float = 1.0    # V por división, canal A
        self.osc_v_div_b:     float = 1.0    # V por división, canal B
        self.osc_pos_a:       float = 0.0    # desplazamiento vertical canal A (divs)
        self.osc_pos_b:       float = 0.0    # desplazamiento vertical canal B (divs)
        self.osc_trig_level:  float = 0.0    # nivel de trigger (V)
        self.osc_trig_source: str   = 'A'    # 'A' o 'B'
        self.osc_trig_edge:   str   = 'rising'   # 'rising' | 'falling'
        self.osc_trig_mode:   str   = 'auto'     # 'auto' | 'normal' | 'single'
        # Última config de hardware (puerto, baud, ganancia, etc.) — vacía
        # mientras no se haya conectado nunca. Se guarda en el .csin para
        # que al reabrir el archivo el panel recuerde el puerto y la
        # calibración del usuario.
        self.osc_hw_config:   dict  = {}

        # ── TL082 (op-amp dual) ─────────────────────────────────────────────
        # Cada instancia representa UNA de las dos unidades del CI.
        # tl082_unit indica cuál ('A' o 'B') — solo informativo/visual.
        # node5 almacena el nodo del pin V− (quinto terminal).
        self.tl082_unit: str = 'A'   # 'A' | 'B'
        self.node5:      str = ''    # V− (sólo TL082)

        # ── Multímetro (instrumento de medición) ─────────────────────────────
        # meter_quantity:  'V' (voltaje), 'A' (corriente), 'OHM' (resistencia)
        # meter_coupling:  'DC' o 'AC' (modo de acoplamiento)
        # meter_reading:   último valor leído (None si aún no se midió)
        # meter_reading_unit_hint: 'V' | 'A' | 'Ω' — para formatear el display
        self.meter_quantity:         str             = 'V'
        self.meter_coupling:         str             = 'DC'
        self.meter_reading:          Optional[float] = None
        self.meter_reading_unit_hint: str            = 'V'

        # ── CLK (reloj digital) ─────────────────────────────────────────────
        # Si está corriendo (oscilando), el timer global lo conmuta a la frecuencia
        # configurada; en caso contrario se comporta como un LOGIC_STATE manual.
        self.clk_running: bool = False

        # ── Estado de memoria de flip-flops ─────────────────────────────────
        # Refleja la salida Q actual del FF (0 ó 1) para visualizarla
        # con un círculo en el centro durante la simulación.
        self.dig_q_state: int = 0

        # ── Puerto de subcircuito (PORT) ────────────────────────────────────
        # Marca un nodo interno que se expone como pin del IC.
        self.port_name: str = 'IN'
        self.port_dir:  str = 'in'    # 'in' | 'out' | 'bidir' (cosmético)

        # ── Instancia de subcircuito (SUBCKT) ───────────────────────────────
        # subckt_name → nombre de la definición en la biblioteca.
        # ic_pins     → lista [{'name': str, 'side': 'left|right|top|bottom'}]
        #               alineada con el orden de los puertos de la definición;
        #               el pin i corresponde al pin de netlist p{i+1}.
        self.subckt_name:    str  = ''
        self.ic_label:       str  = ''     # texto en el cuerpo (vacío → subckt_name)
        self.ic_body_color:  str  = ''     # override (vacío → tema)
        self.ic_text_color:  str  = ''
        self.ic_pins:        list = []     # [{'name','side'}, ...]

        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges)
        self.setCacheMode(QGraphicsItem.CacheMode.NoCache)

    def _apply_transform(self):
        """Aplica rotación y flips combinados como una única QTransform.
        El orden es: primero los flips (escala -1), luego la rotación.
        Esto mantiene la posición del componente intacta."""
        t = QTransform()
        t.rotate(self._angle)
        sx = -1.0 if self._flip_x else 1.0
        sy = -1.0 if self._flip_y else 1.0
        if sx != 1.0 or sy != 1.0:
            t.scale(sx, sy)
        self.setTransform(t)
        self.update()
        # Actualizar cables conectados a este componente tras la transformación
        if self.scene() and hasattr(self.scene(), 'update_wires_for_component'):
            self.scene().update_wires_for_component(self)

    def rotate_90(self, delta: int = 90):
        """Rota el componente `delta` grados (positivo = horario,
        negativo = antihorario)."""
        self._angle = (self._angle + delta) % 360
        self._apply_transform()

    def flip_x(self):
        """Invierte el componente en el eje X (espejo horizontal)."""
        self._flip_x = not self._flip_x
        self._apply_transform()

    def flip_y(self):
        """Invierte el componente en el eje Y (espejo vertical)."""
        self._flip_y = not self._flip_y
        self._apply_transform()

    def pin_positions_scene(self) -> Tuple[QPointF, QPointF]:
        """Retorna posición de los pines en coordenadas de ESCENA (considera rotación)."""
        p1_local, p2_local = self.pin_positions()
        return self.mapToScene(p1_local), self.mapToScene(p2_local)

    # ── Geometría ──────────────────────────────
    def _counter_height(self) -> float:
        """Altura necesaria para separar claramente las salidas Q0…Qn."""
        return max(COMP_H // 2 + 6, 12 + 9 * max(1, self.dig_bits))

    def _counter_output_positions(self) -> list[QPointF]:
        """Posiciones de Q0…Qn, centradas verticalmente en el lateral derecho."""
        n = max(1, self.dig_bits)
        step = 18
        return [QPointF(COMP_W // 2 + 10, (i - (n - 1) / 2) * step)
                for i in range(n)]

    def _gate_geometry(self):
        """Retorna (hw, hh, step, n) para una puerta digital."""
        n = max(self.dig_inputs, 1)
        hw = COMP_W // 2
        step = GRID_SIZE
        hh = (n - 1) * step // 2 + step
        return hw, hh, step, n

    def _gate_pin_ys(self):
        """Posiciones y de los pines de entrada, centradas en 0."""
        _, _, step, n = self._gate_geometry()
        return [-(n - 1) * step // 2 + i * step for i in range(n)]

    def boundingRect(self) -> QRectF:
        if self.comp_type == 'GND':
            return QRectF(-20, -5, 40, 30)
        if self.comp_type == 'NODE':
            return QRectF(-8, -8, 16, 16)
        if self.comp_type in ('NET_LABEL_IN', 'NET_LABEL_OUT'):
            # Tamaño similar a GND. Flecha de 30 px y etiqueta encima.
            return QRectF(-18, -16, 36, 28)
        if self.comp_type == 'PORT':
            # Banderín pequeño + etiqueta encima.
            return QRectF(-26, -20, 52, 34)
        if self.comp_type == 'SUBCKT':
            w, h, _ = self._subckt_geometry()
            m = 22
            return QRectF(-w / 2 - 12 - m, -h / 2 - m,
                          w + 24 + 2 * m, h + 2 * m)
        if self.comp_type in self.TIMER_TYPES:
            return QRectF(-95, -85, 190, 170)
        if self.comp_type == 'COUNTER':
            hh = self._counter_height()
            return QRectF(-COMP_W // 2 - 20, -hh - 20,
                         COMP_W + 40, hh * 2 + 40)
        # Flip-flops: cuerpo + cables horizontales + pines SET/RESET arriba/abajo
        if self.comp_type in self.FLIPFLOP_TYPES:
            hw_f = COMP_W // 2
            hh_f = COMP_H // 2 + 8
            return QRectF(-hw_f - 14, -hh_f - 18,
                          (hw_f + 14) * 2, (hh_f + 18) * 2)
        # Puertas: bounding rect dinámico según altura real
        if self.comp_type in ('AND', 'OR', 'NOT', 'NAND', 'NOR', 'XOR',
                               'COMPARATOR', 'PWM', 'MUX2'):
            gw, gh, step, n = self._gate_geometry()
            margin = 20
            return QRectF(-gw - 10 - margin, -gh - margin,
                          (gw + 10) * 2 + margin * 2, gh * 2 + margin * 2)
        if self.comp_type == 'XFMR':
            return QRectF(-70, -35, 140, 80)
        if self.comp_type == 'BRIDGE':
            return QRectF(-70, -70, 140, 140)
        if self.comp_type == 'OSC':
            return QRectF(-50, -36, 100, 72)
        if self.comp_type == 'MULTIMETER':
            # Cuerpo cuadrado con display + puntas de prueba en la parte inferior
            return QRectF(-44, -50, 88, 100)
        if self.comp_type == 'LAMP':
            return QRectF(-52, -64, 104, 140)
        if self.comp_type == 'DPDT':
            return QRectF(-62, -62, 124, 124)
        if self.comp_type == 'TL082':
            # Triángulo (−35..+35, −28..+28) + cables V+/V− (±44) + margen etiquetas
            return QRectF(-64, -58, 128, 116)
        margin = 10 + PIN_RADIUS
        return QRectF(-COMP_W//2 - margin, -COMP_H//2 - 20,
                      COMP_W + 2 * margin, COMP_H + 40)

    def pin_positions(self) -> Tuple[QPointF, QPointF]:
        """Retorna posición de los pines principales en coordenadas locales."""
        hw = COMP_W // 2
        hh = COMP_H // 2
        if self.comp_type == 'GND':
            return QPointF(0, 0), QPointF(0, 0)
        if self.comp_type in ('BJT_NPN', 'BJT_PNP'):
            return QPointF(hw + 10, -hh - 6), QPointF(hw + 10, hh + 6)
        if self.comp_type in ('NMOS', 'PMOS'):
            return QPointF(hw + 10, -hh - 6), QPointF(hw + 10, hh + 6)
        if self.comp_type == 'OPAMP':
            hh_op = hh + 6
            return QPointF(hw + 10, 0), QPointF(-hw - 10, hh_op // 2)
        if self.comp_type == 'TL082':
            # p1 = OUT (derecha-centro), p2 = IN− (izquierda-abajo)
            return QPointF(50, 0), QPointF(-50, 18)
        # ── Transformador: p1=PRI+ (sup-izq), p2=PRI- (inf-izq) ─────────
        if self.comp_type == 'XFMR':
            return QPointF(-60, -20), QPointF(-60, 20)
        if self.comp_type in ('SPDT', 'SPDT3'):
            return QPointF(-40, 0), QPointF(40, -20)
        if self.comp_type == 'DPDT':
            return QPointF(-50, -25), QPointF(40, -40)
        if self.comp_type == 'RELAY':
            return QPointF(-45, -20), QPointF(-45, 20)
        # ── Puente rectificador (diamante):
        #     p1 = AC1 (izq),  p2 = AC2 (der)
        #     p3 = DC+ (sup),  p4 = DC− (inf)
        if self.comp_type == 'BRIDGE':
            return QPointF(-60, 0), QPointF(60, 0)
        # ── Osciloscopio:
        #     p1 = A+ (izq-arriba), p2 = A− (izq-abajo)
        #     p3 = B+ (der-arriba), p4 = B− (der-abajo)
        if self.comp_type == 'OSC':
            return QPointF(-40, -20), QPointF(-40, 20)
        # ── Multímetro: puntas de prueba en la parte inferior ────────────
        #     p1 = V+ (rojo, izq-abajo), p2 = V− (negro, der-abajo)
        if self.comp_type == 'MULTIMETER':
            return QPointF(-30, 50), QPointF(30, 50)
        # ── Puertas lógicas: usar _gate_geometry para coincidir exactamente ──
        if self.comp_type in ('AND', 'OR', 'NOT', 'NAND', 'NOR', 'XOR',
                               'COMPARATOR', 'PWM'):
            gw, gh, step, n = self._gate_geometry()
            y0 = self._gate_pin_ys()[0]  # posición exacta del primer pin
            return QPointF(gw + 10, 0), QPointF(-gw - 10, y0)
        # ── Flip-flops ───────────────────────────────────────────────────
        if self.comp_type in ('DFF', 'JKFF', 'TFF', 'SRFF'):
            hw_f = COMP_W // 2
            hh_f = COMP_H // 2 + 8
            return QPointF(hw_f + 10, -(hh_f // 2)), QPointF(-hw_f - 10, -(hh_f // 2))
        # ── Contador: p1=Q0 (der-arriba), p2=CLK (izq-centro) ───────────
        if self.comp_type == 'COUNTER':
            return self._counter_output_positions()[0], QPointF(-COMP_W // 2 - 10, 0)
        # ── MUX2: p1=salida (der), p2=I0 (izq-arriba) ───────────────────
        if self.comp_type == 'MUX2':
            gw, gh, step, _ = self._gate_geometry()
            ys = self._gate_pin_ys()
            return QPointF(gw + 10, 0), QPointF(-gw - 10, ys[0])
        if self.comp_type in self.TIMER_TYPES:
            return QPointF(-80, 60), QPointF(-80, 20)  # p1 GND, p2 TRIG
        if self.comp_type == 'LOGIC_STATE':
            hw2 = COMP_W // 2
            return QPointF(hw2 + 10, 0), QPointF(hw2 + 10, 0)  # p1=salida, p2=dummy
        if self.comp_type == 'CLK':
            hw2 = COMP_W // 2
            return QPointF(hw2 + 10, 0), QPointF(hw2 + 10, 0)  # p1=salida, p2=dummy
        if self.comp_type == 'NET_LABEL_IN':
            # Pin en la CABEZA de la flecha (lado derecho): ─►●
            return QPointF(15, 0), QPointF(15, 0)
        if self.comp_type == 'NET_LABEL_OUT':
            # Pin en la COLA de la flecha (lado izquierdo): ●─►
            return QPointF(-15, 0), QPointF(-15, 0)
        if self.comp_type == 'PORT':
            # Único pin a la derecha del banderín.
            return QPointF(16, 0), QPointF(16, 0)
        if self.comp_type == 'SUBCKT':
            pts = self._subckt_pin_points()
            p1 = pts[0] if pts else QPointF(0, 0)
            p2 = pts[1] if len(pts) > 1 else p1
            return p1, p2
        return QPointF(-hw - 10, 0), QPointF(hw + 10, 0)

    # ── Geometría de subcircuitos (SUBCKT) ──────────────────────────────────
    def _subckt_geometry(self):
        """Devuelve (ancho, alto, dist_por_lado) del cuerpo del IC en función
        del número de pines colocados en cada lado."""
        sides = {'left': 0, 'right': 0, 'top': 0, 'bottom': 0}
        for p in (self.ic_pins or []):
            sides[p.get('side', 'left')] = sides.get(p.get('side', 'left'), 0) + 1
        v_max = max(sides['left'], sides['right'], 1)
        h_max = max(sides['top'], sides['bottom'], 1)
        h = max(50, v_max * 24 + 20)
        w = max(70, h_max * 24 + 20, 60)
        return float(w), float(h), 24.0

    def _subckt_pin_points(self):
        """Lista de QPointF (coordenadas locales) alineada con self.ic_pins."""
        w, h, step = self._subckt_geometry()
        pins = self.ic_pins or []
        # contador por lado para repartir uniformemente
        per_side = {'left': [], 'right': [], 'top': [], 'bottom': []}
        for i, p in enumerate(pins):
            per_side.setdefault(p.get('side', 'left'), []).append(i)
        result = [QPointF(0, 0)] * len(pins)
        ext = 12  # longitud del cable que sale del cuerpo

        def spread(n, length):
            if n <= 0:
                return []
            gap = length / (n + 1)
            return [-length / 2 + gap * (k + 1) for k in range(n)]

        for side, idxs in per_side.items():
            if side in ('left', 'right'):
                ys = spread(len(idxs), h)
                x = (-w / 2 - ext) if side == 'left' else (w / 2 + ext)
                for k, gi in enumerate(idxs):
                    result[gi] = QPointF(x, ys[k])
            else:
                xs = spread(len(idxs), w)
                y = (-h / 2 - ext) if side == 'top' else (h / 2 + ext)
                for k, gi in enumerate(idxs):
                    result[gi] = QPointF(xs[k], y)
        return result

    def subckt_pin_positions_scene(self) -> list:
        return [self.mapToScene(p) for p in self._subckt_pin_points()]

    def pin3_position(self) -> QPointF:
        """
        Tercer pin:
          BJT/MOSFET  → Base/Gate   (izq-centro)
          OpAmp       → Entrada+    (izq-arriba)
          Puertas 2+  → segunda entrada (izq, segundo cable)
          Flip-flops  → CLK         (izq-abajo)
          MUX2        → I1          (izq-centro)
        """
        hw = COMP_W // 2
        hh = COMP_H // 2
        if self.comp_type in ('BJT_NPN', 'BJT_PNP', 'NMOS', 'PMOS'):
            return QPointF(-hw - 10, 0)
        if self.comp_type == 'OPAMP':
            hh_op = hh + 6
            return QPointF(-hw - 10, -(hh_op // 2))
        if self.comp_type == 'TL082':
            # p3 = IN+ (izquierda-arriba)
            return QPointF(-50, -18)
        # Puertas con 2+ entradas: segundo cable de entrada
        if self.comp_type in ('AND', 'OR', 'NAND', 'NOR', 'XOR', 'COMPARATOR'):
            gw, gh, step, n = self._gate_geometry()
            ys = self._gate_pin_ys()
            if n >= 2:
                return QPointF(-gw - 10, ys[1])  # posición exacta del segundo pin
            return QPointF(0, 0)
        # Flip-flops: CLK (izq-abajo)
        if self.comp_type in ('DFF', 'JKFF', 'TFF', 'SRFF'):
            hw_f = COMP_W // 2
            hh_f = COMP_H // 2 + 8
            return QPointF(-hw_f - 10, hh_f // 2)
        # MUX2: I1 (izq, segundo cable)
        if self.comp_type == 'MUX2':
            gw, gh, step, _ = self._gate_geometry()
            ys = self._gate_pin_ys()
            return QPointF(-gw - 10, ys[1] if len(ys) > 1 else 0)
        if self.comp_type == 'COUNTER':
            outputs = self._counter_output_positions()
            return outputs[1] if len(outputs) > 1 else QPointF(0, 0)
        # Transformador: p3 = SEC+ (sup-der)
        if self.comp_type == 'XFMR':
            return QPointF(60, -20)
        if self.comp_type in ('SPDT', 'SPDT3'): return QPointF(40, 20)
        if self.comp_type == 'DPDT': return QPointF(40, -10)
        if self.comp_type == 'RELAY': return QPointF(45, -20)
        # Puente: p3 = DC+ (sup)
        if self.comp_type == 'BRIDGE':
            return QPointF(0, -60)
        # Osciloscopio: p3 = B+ (der-arriba)
        if self.comp_type == 'OSC':
            return QPointF(40, -20)
        return QPointF(0, 0)

    def pin3_position_scene(self) -> QPointF:
        return self.mapToScene(self.pin3_position())

    def pin4_position(self) -> QPointF:
        """Cuarto pin.

          TL082       → V+   (superior-centro)
          XFMR        → SEC− (inferior derecho)
          BRIDGE      → DC−  (inferior)
          DFF/JKFF/TFF/SRFF → SET (parte superior, arriba del centro)
        """
        if self.comp_type == 'TL082':
            # p4 = V+ : sale por la mitad del lado superior del triángulo
            return QPointF(0, -44)
        if self.comp_type == 'XFMR':
            return QPointF(60, 20)
        if self.comp_type == 'RELAY': return QPointF(45, 20)
        if self.comp_type == 'DPDT': return QPointF(-50, 25)
        if self.comp_type == 'BRIDGE':
            return QPointF(0, 60)
        if self.comp_type == 'OSC':
            return QPointF(40, 20)
        if self.comp_type == 'MUX2':
            # p4 = línea de selección (abajo-centro)
            _, hh, _, _ = self._gate_geometry()
            return QPointF(0, hh + 10)
        if self.comp_type == 'COUNTER':
            outputs = self._counter_output_positions()
            return outputs[2] if len(outputs) > 2 else QPointF(0, 0)
        if self.comp_type in self.FLIPFLOP_TYPES:
            hh_f = COMP_H // 2 + 8
            return QPointF(0, -hh_f - 10)
        return QPointF(0, 0)

    def pin4_position_scene(self) -> QPointF:
        return self.mapToScene(self.pin4_position())

    def pin5_position(self) -> QPointF:
        """Quinto pin.

          TL082               → V−   (inferior-centro)
          DFF/JKFF/TFF/SRFF   → RESET (parte inferior)
        """
        if self.comp_type == 'TL082':
            # p5 = V− : sale por la mitad del lado inferior del triángulo
            return QPointF(0, 44)
        if self.comp_type in self.FLIPFLOP_TYPES:
            hh_f = COMP_H // 2 + 8
            return QPointF(0, hh_f + 10)
        if self.comp_type == 'COUNTER':
            outputs = self._counter_output_positions()
            return outputs[3] if len(outputs) > 3 else QPointF(0, 0)
        if self.comp_type == 'DPDT':
            return QPointF(40, 10)
        return QPointF(0, 0)

    def pin5_position_scene(self) -> QPointF:
        return self.mapToScene(self.pin5_position())

    def pin6_position(self) -> QPointF:
        """Sexto pin (sólo flip-flops): salida complementada Q̄ (derecha-abajo)."""
        if self.comp_type in self.FLIPFLOP_TYPES:
            hw_f = COMP_W // 2
            hh_f = COMP_H // 2 + 8
            return QPointF(hw_f + 10, hh_f // 2)
        if self.comp_type == 'DPDT':
            return QPointF(40, 40)
        return QPointF(0, 0)

    def _timer_pin_positions(self) -> list:
        """DIP-8 con todos los pines alineados a la cuadrícula de 20 px."""
        return [QPointF(-80, 60), QPointF(-80, 20), QPointF(-80, -20),
                QPointF(-80, -60), QPointF(80, -60), QPointF(80, -20),
                QPointF(80, 20), QPointF(80, 60)]

    def pin6_position_scene(self) -> QPointF:
        return self.mapToScene(self.pin6_position())

    def all_pin_positions_scene(self) -> list:
        """Retorna todos los pines activos del componente en coordenadas de escena."""
        if self.comp_type == 'SUBCKT':
            pts = self.subckt_pin_positions_scene()
            return pts if pts else [self.mapToScene(QPointF(0, 0))]
        if self.comp_type == 'PORT':
            p1, _ = self.pin_positions_scene()
            return [p1]
        if self.comp_type in self.TIMER_TYPES:
            return [self.mapToScene(p) for p in self._timer_pin_positions()]
        if self.comp_type == 'COUNTER':
            outputs = [self.mapToScene(p) for p in self._counter_output_positions()]
            clock = self.mapToScene(QPointF(-COMP_W // 2 - 10, 0))
            return [outputs[0], clock, *outputs[1:]]
        p1, p2 = self.pin_positions_scene()
        pins = [p1, p2]
        # Pines adicionales según tipo
        if self.comp_type in self.SIX_PIN_TYPES:
            return [self.mapToScene(p) for p in (
                self.pin_positions()[0], self.pin_positions()[1],
                self.pin3_position(), self.pin4_position(),
                self.pin5_position(), self.pin6_position())]
        if self.comp_type in self.FIVE_PIN_TYPES:
            pins.append(self.pin3_position_scene())  # IN+
            pins.append(self.pin4_position_scene())  # V+
            pins.append(self.pin5_position_scene())  # V−
        elif self.comp_type in ('BJT_NPN', 'BJT_PNP', 'NMOS', 'PMOS', 'OPAMP'):
            pins.append(self.pin3_position_scene())
        elif self.comp_type in ('SPDT', 'SPDT3'):
            pins.append(self.pin3_position_scene())
        elif self.comp_type in ('AND', 'OR', 'NAND', 'NOR', 'XOR', 'COMPARATOR'):
            gw, gh, step, n = self._gate_geometry()
            ys = self._gate_pin_ys()
            for y in ys[1:]:   # primer pin ya incluido como p2
                pins.append(self.mapToScene(QPointF(-gw - 10, y)))
        elif self.comp_type in self.FLIPFLOP_TYPES:
            pins.append(self.pin3_position_scene())  # CLK / 2da entrada
            pins.append(self.pin4_position_scene())  # SET
            pins.append(self.pin5_position_scene())  # RESET
            pins.append(self.pin6_position_scene())  # Q̄
        elif self.comp_type == 'MUX2':
            pins.append(self.pin3_position_scene())  # I1
            pins.append(self.pin4_position_scene())  # SEL
        elif self.comp_type in self.FOUR_PIN_TYPES:
            pins.append(self.pin3_position_scene())
            pins.append(self.pin4_position_scene())
        return pins

    # ── Dibujo delegado al pintor de componentes ─────────────────────────
    def paint(self, painter: QPainter, option, widget):
        ComponentPainter(self).paint(painter, option, widget)

    def _draw_resistor(self, painter, pen_body, pen_wire, body_color):
        return ComponentPainter(self)._draw_resistor(painter, pen_body, pen_wire, body_color)

    def _draw_switch(self, painter, pen_body, pen_wire, body_color):
        return ComponentPainter(self)._draw_switch(painter, pen_body, pen_wire, body_color)

    def _draw_potentiometer(self, painter, pen_body, pen_wire, body_color):
        return ComponentPainter(self)._draw_potentiometer(painter, pen_body, pen_wire, body_color)

    def _draw_transformer(self, painter, pen_body, pen_wire, body_color):
        return ComponentPainter(self)._draw_transformer(painter, pen_body, pen_wire, body_color)

    def _draw_bridge_rectifier(self, painter, pen_body, pen_wire, body_color):
        return ComponentPainter(self)._draw_bridge_rectifier(painter, pen_body, pen_wire, body_color)

    def _draw_capacitor(self, painter, pen_body, pen_wire):
        return ComponentPainter(self)._draw_capacitor(painter, pen_body, pen_wire)

    def _draw_inductor(self, painter, pen_body, pen_wire):
        return ComponentPainter(self)._draw_inductor(painter, pen_body, pen_wire)

    def _draw_source(self, painter, pen_body, pen_wire, body_color):
        return ComponentPainter(self)._draw_source(painter, pen_body, pen_wire, body_color)

    def _draw_fgen(self, painter, pen_body, pen_wire, body_color):
        return ComponentPainter(self)._draw_fgen(painter, pen_body, pen_wire, body_color)

    def _draw_osc(self, painter, pen_body, pen_wire, body_color):
        return ComponentPainter(self)._draw_osc(painter, pen_body, pen_wire, body_color)

    def _draw_multimeter(self, painter, pen_body, pen_wire, body_color):
        return ComponentPainter(self)._draw_multimeter(painter, pen_body, pen_wire, body_color)

    def _draw_impedance(self, painter, pen_body, pen_wire, body_color):
        return ComponentPainter(self)._draw_impedance(painter, pen_body, pen_wire, body_color)

    def _draw_gnd(self, painter, pen_body):
        return ComponentPainter(self)._draw_gnd(painter, pen_body)

    def _draw_node(self, painter, color):
        return ComponentPainter(self)._draw_node(painter, color)

    def _draw_diode(self, painter, pen_body, pen_wire):
        return ComponentPainter(self)._draw_diode(painter, pen_body, pen_wire)

    def _draw_led(self, painter, pen_body, pen_wire):
        return ComponentPainter(self)._draw_led(painter, pen_body, pen_wire)

    def _draw_bulb(self, painter, pen_body, pen_wire):
        return ComponentPainter(self)._draw_bulb(painter, pen_body, pen_wire)

    def _draw_bjt(self, painter, pen_body, pen_wire):
        return ComponentPainter(self)._draw_bjt(painter, pen_body, pen_wire)

    def _draw_mosfet(self, painter, pen_body, pen_wire):
        return ComponentPainter(self)._draw_mosfet(painter, pen_body, pen_wire)

    def _draw_opamp(self, painter, pen_body, pen_wire, body_color):
        return ComponentPainter(self)._draw_opamp(painter, pen_body, pen_wire, body_color)

    def _draw_tl082(self, painter, pen_body, pen_wire, body_color):
        return ComponentPainter(self)._draw_tl082(painter, pen_body, pen_wire, body_color)

    def _draw_digital_gate(self, painter, pen_body, pen_wire, body_color, label: str):
        return ComponentPainter(self)._draw_digital_gate(painter, pen_body, pen_wire, body_color, label)

    def _draw_ansi_gate(self, painter, pen_body, pen_wire, body_color):
        return ComponentPainter(self)._draw_ansi_gate(painter, pen_body, pen_wire, body_color)

    def _draw_flipflop(self, painter, pen_body, pen_wire, body_color, ff_type: str):
        return ComponentPainter(self)._draw_flipflop(painter, pen_body, pen_wire, body_color, ff_type)

    def _draw_timer555(self, painter, pen_body, pen_wire, body_color):
        return ComponentPainter(self)._draw_timer555(painter, pen_body, pen_wire, body_color)

    def _draw_clk(self, painter, pen_body, pen_wire, body_color):
        return ComponentPainter(self)._draw_clk(painter, pen_body, pen_wire, body_color)

    def _draw_adc_dac(self, painter, pen_body, pen_wire, body_color, is_adc: bool):
        return ComponentPainter(self)._draw_adc_dac(painter, pen_body, pen_wire, body_color, is_adc)

    def _draw_logic_state(self, painter, pen_body, pen_wire, body_color):
        return ComponentPainter(self)._draw_logic_state(painter, pen_body, pen_wire, body_color)

    def _draw_counter(self, painter, pen_body, pen_wire, body_color):
        return ComponentPainter(self)._draw_counter(painter, pen_body, pen_wire, body_color)

    def _draw_mux(self, painter, pen_body, pen_wire, body_color):
        return ComponentPainter(self)._draw_mux(painter, pen_body, pen_wire, body_color)

    def _draw_sheet_connector(self, painter, pen_body, pen_wire, body_color):
        return ComponentPainter(self)._draw_sheet_connector(painter, pen_body, pen_wire, body_color)

    def _draw_port(self, painter, pen_body, pen_wire, body_color):
        return ComponentPainter(self)._draw_port(painter, pen_body, pen_wire, body_color)

    def _draw_subcircuit(self, painter, pen_body, pen_wire, body_color):
        return ComponentPainter(self)._draw_subcircuit(painter, pen_body, pen_wire, body_color)

    def _draw_labels(self, painter, text_color):
        return ComponentPainter(self)._draw_labels(painter, text_color)

    def _draw_labels_content(self, painter, text_color):
        return ComponentPainter(self)._draw_labels_content(painter, text_color)
    def _format_value(self) -> str:
        if self.comp_type == 'Z':
            if self.z_mode == 'rect':
                if abs(self.z_imag) < 1e-12:
                    return format_si_value(self.z_real, 'Ω')
                r = format_si_value(self.z_real, '')
                x = format_si_value(abs(self.z_imag), 'Ω')
                sign = '+' if self.z_imag >= 0 else '−'
                return f"{r}{sign}{x}j"
            else:
                return f"{format_si_value(self.z_mag, 'Ω')}∠{self.z_phase:.1f}°"
        return format_si_value(self.value, self.unit)

    # ── Snap a grid ──────────────────────────────
    def itemChange(self, change, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionChange:
            if self.scene() is not None and not getattr(self.scene(), 'snap_enabled', True):
                return value
            x = round(value.x() / GRID_SIZE) * GRID_SIZE
            y = round(value.y() / GRID_SIZE) * GRID_SIZE
            return QPointF(x, y)
        elif change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged:
            # NUEVO: Notificar a la escena para actualizar cables
            if self.scene() is not None and hasattr(self.scene(), 'update_wires_for_component'):
                self.scene().update_wires_for_component(self)
        return super().itemChange(change, value)
