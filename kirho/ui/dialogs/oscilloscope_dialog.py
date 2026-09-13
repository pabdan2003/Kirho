"""
Panel frontal del Osciloscopio (estilo Multisim XSC1, 2 canales).

Recibe muestras del live transient vía `push_samples(tr, pin_node)`.
Cada llamada agrega los puntos al buffer circular de cada canal y
re-pinta la pantalla.

Controles:
  • Time/Div   (presets: 1us, 10us, 100us, 1ms, 10ms, 100ms, 1s)
  • V/Div  A,B (1mV → 10V por div)
  • Offset vertical A,B (en divisiones)
  • Trigger: source (A/B), edge (rising/falling), level (V), mode (auto/normal/single)
"""
from __future__ import annotations

import collections
import math
from typing import Optional, Deque, Tuple, TYPE_CHECKING

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGridLayout, QFormLayout,
    QGroupBox, QLabel, QPushButton, QComboBox, QDoubleSpinBox,
    QCheckBox, QWidget, QSizePolicy,
)
from PyQt6.QtGui import QPainter, QPen, QColor, QBrush, QPainterPath, QFont
from PyQt6.QtCore import Qt, QRectF, QPointF, QTimer, pyqtSignal

from kirho.ui.style import COLORS, _qfont

if TYPE_CHECKING:
    from kirho.ui.items.component_item import ComponentItem


# Presets razonables, en segundos por división
_TIME_DIV_PRESETS = [
    (1e-6,   "1 µs"), (2e-6,   "2 µs"), (5e-6,   "5 µs"),
    (10e-6,  "10 µs"), (20e-6,  "20 µs"), (50e-6,  "50 µs"),
    (100e-6, "100 µs"), (200e-6, "200 µs"), (500e-6, "500 µs"),
    (1e-3,   "1 ms"), (2e-3,   "2 ms"), (5e-3,   "5 ms"),
    (10e-3,  "10 ms"), (20e-3,  "20 ms"), (50e-3,  "50 ms"),
    (100e-3, "100 ms"), (200e-3, "200 ms"), (500e-3, "500 ms"),
    (1.0,    "1 s"),
]
_V_DIV_PRESETS = [
    (1e-3, "1 mV"), (2e-3, "2 mV"), (5e-3, "5 mV"),
    (10e-3, "10 mV"), (20e-3, "20 mV"), (50e-3, "50 mV"),
    (100e-3, "100 mV"), (200e-3, "200 mV"), (500e-3, "500 mV"),
    (1.0, "1 V"), (2.0, "2 V"), (5.0, "5 V"),
    (10.0, "10 V"), (20.0, "20 V"), (50.0, "50 V"),
    (100.0, "100 V"),
]

# El historial cubre siempre una pantalla completa. Al superar el límite se
# conserva la forma (primero, mínimo, máximo y último de cada bloque).
_BUFFER_TARGET = 8000
_BUFFER_LIMIT = 12000
_HISTORY_SCREENS = 1.1
_SCREEN_REFRESH_MS = 34  # 29,4 FPS; los samples siguen acumulándose
_DEFAULT_TRIGGER_POSITION = 2.0  # divisiones desde la izquierda
_ROLL_TIME_DIV = 0.1

# Colores de las trazas — estilo Multisim
_TRACE_A_COLOR = QColor(255, 220, 70)    # amarillo
_TRACE_B_COLOR = QColor(80, 200, 255)    # celeste
_SCREEN_BG     = QColor(15, 25, 18)
_GRID_COLOR    = QColor(60, 80, 70)
_AXIS_COLOR    = QColor(110, 140, 120)


def _find_trigger(samples, level, edge, hysteresis, after_t=float('-inf'),
                  before_t=float('inf'), latest=True) -> Optional[float]:
    """Devuelve un cruce válido, interpolado entre dos muestras."""
    armed = False
    found = None
    previous = None
    low, high = level - hysteresis, level + hysteresis
    for t, value in samples:
        if previous is None:
            previous = (t, value)
            continue
        t0, value0 = previous
        previous = (t, value)
        if t0 > before_t:
            break
        if edge == 'falling':
            if value0 >= high:
                armed = True
            crossed = armed and value0 >= level > value
        else:
            if value0 <= low:
                armed = True
            crossed = armed and value0 <= level < value
        if not crossed or value == value0:
            continue
        crossing = t0 + (level - value0) * (t - t0) / (value - value0)
        armed = False
        if after_t < crossing <= before_t:
            found = crossing
            if not latest:
                return found
    return found


def _compact_samples(samples, max_points=_BUFFER_TARGET):
    if len(samples) <= max_points:
        return list(samples)
    block_size = max(1, (len(samples) + max_points // 4 - 1) // (max_points // 4))
    compacted = []
    for start in range(0, len(samples), block_size):
        block = samples[start:start + block_size]
        points = sorted({block[0], min(block, key=lambda p: p[1]),
                         max(block, key=lambda p: p[1]), block[-1]})
        compacted.extend(point for point in points
                         if not compacted or point != compacted[-1])
    return compacted


def _condition_samples(samples, coupling='DC', inverted=False):
    if coupling == 'DC' and not inverted:
        return samples
    samples = list(samples)
    center = (sum(value for _, value in samples) / len(samples)
              if coupling == 'AC' and samples else 0.0)
    sign = -1.0 if inverted else 1.0
    return [(t, sign * (value - center)) for t, value in samples]


def _estimate_period(samples) -> Optional[float]:
    samples = list(samples)
    if len(samples) < 3:
        return None
    values = [value for _, value in samples]
    level = (min(values) + max(values)) / 2.0
    if max(values) - min(values) <= 1e-12:
        return None
    crossings = []
    for (t0, v0), (t1, v1) in zip(samples, samples[1:]):
        if v0 <= level < v1 and v1 != v0:
            crossings.append(t0 + (level - v0) * (t1 - t0) / (v1 - v0))
    periods = [b - a for a, b in zip(crossings, crossings[1:]) if b > a]
    if not periods:
        return None
    periods.sort()
    return periods[len(periods) // 2]


def _measure_samples(samples):
    samples = list(samples)
    if not samples:
        return None
    values = [value for _, value in samples]
    period = _estimate_period(samples)
    duration = samples[-1][0] - samples[0][0]
    if duration > 0:
        average = sum((v0 + v1) * (t1 - t0) / 2.0
                      for (t0, v0), (t1, v1) in zip(samples, samples[1:])) / duration
        mean_square = sum((v0 * v0 + v1 * v1) * (t1 - t0) / 2.0
                          for (t0, v0), (t1, v1) in zip(samples, samples[1:])) / duration
    else:
        average = values[-1]
        mean_square = values[-1] ** 2
    return {
        'frequency': 1.0 / period if period else None,
        'period': period,
        'vpp': max(values) - min(values),
        'maximum': max(values),
        'minimum': min(values),
        'average': average,
        'rms': math.sqrt(max(0.0, mean_square)),
    }


def _value_at_time(samples, target):
    previous = None
    for point in samples:
        if point[0] == target:
            return point[1]
        if point[0] > target:
            if previous is None:
                return None
            t0, v0 = previous
            t1, v1 = point
            return v0 + (v1 - v0) * (target - t0) / (t1 - t0)
        previous = point
    return None


class _Screen(QWidget):
    """Pantalla del osciloscopio: grilla 10×8 + dos trazas."""
    display_changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(480, 320)
        self.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.Expanding)
        # Buffers (t, v) por canal. deque para coste O(1) por insert.
        self.buf_a: Deque[Tuple[float, float]] = collections.deque()
        self.buf_b: Deque[Tuple[float, float]] = collections.deque()
        self._capture_a = None
        self._capture_b = None
        self._pause_a = None
        self._pause_b = None
        # Configuración cacheada (refrescada por el diálogo)
        self.time_div = 1e-3
        self.v_div_a  = 1.0
        self.v_div_b  = 1.0
        self.pos_a    = 0.0
        self.pos_b    = 0.0
        self.enabled_a = True
        self.enabled_b = True
        self.coupling_a = 'DC'
        self.coupling_b = 'DC'
        self.invert_a = False
        self.invert_b = False
        # Ventana de tiempo a mostrar (segundos). 10 divisiones.
        self.t_window_end: float = 0.0
        self.trig_source = 'A'
        self.trig_edge = 'rising'
        self.trig_level = 0.0
        self.trig_mode = 'auto'
        self.trig_position = _DEFAULT_TRIGGER_POSITION
        self.triggered = False
        self.single_armed = True
        self.paused = False
        self._resume_pending = False
        self._last_trigger_time: Optional[float] = None
        self._trigger_config = None
        self.cursor_enabled = False
        self.cursor_channel = 'A'
        self.cursor_div_1 = 3.0
        self.cursor_div_2 = 7.0
        self._drag_cursor = None
        self._repaint_timer = QTimer(self)
        self._repaint_timer.setSingleShot(True)
        self._repaint_timer.setInterval(_SCREEN_REFRESH_MS)
        self._repaint_timer.timeout.connect(self.update)

    def push(self, t_arr, v_a, v_b):
        """Acepta arrays paralelos. v_a/v_b pueden ser None si el pin
        correspondiente no está conectado (canal apagado para esa muestra)."""
        if t_arr is None or len(t_arr) == 0:
            return
        if self.paused:
            return
        if self.trig_mode == 'single' and not self.single_armed:
            return
        if self._resume_pending:
            self.buf_a.clear()
            self.buf_b.clear()
            self._pause_a = self._pause_b = None
            self._last_trigger_time = None
            self.triggered = False
            self._resume_pending = False
        if v_a is not None:
            self.buf_a.extend((float(t), float(v)) for t, v in zip(t_arr, v_a))
        if v_b is not None:
            self.buf_b.extend((float(t), float(v)) for t, v in zip(t_arr, v_b))
        last = float(t_arr[-1])
        self._acquire(last)
        self._maintain_buffer(self.buf_a, last)
        self._maintain_buffer(self.buf_b, last)
        if not self._repaint_timer.isActive():
            self._repaint_timer.start()
        self.display_changed.emit()

    def configure_trigger(self, source: str, edge: str, level: float, mode: str,
                          position_divs: float = _DEFAULT_TRIGGER_POSITION):
        scale = self.v_div_a if source == 'A' else self.v_div_b
        coupling = self.coupling_a if source == 'A' else self.coupling_b
        inverted = self.invert_a if source == 'A' else self.invert_b
        enabled = self.enabled_a if source == 'A' else self.enabled_b
        position_divs = min(10.0, max(0.0, position_divs))
        config = (source, edge, level, mode, position_divs,
                  self.time_div, scale, coupling, inverted, enabled)
        if config == self._trigger_config:
            return
        self._trigger_config = config
        self.trig_source, self.trig_edge = source, edge
        self.trig_level, self.trig_mode = level, mode
        self.trig_position = position_divs
        self.triggered = False
        self.single_armed = True
        self._capture_a = self._capture_b = None
        source_buf = self.buf_a if source == 'A' else self.buf_b
        self._last_trigger_time = (source_buf[-1][0]
                                   if mode == 'single' and source_buf else None)
        if source_buf:
            self._acquire(source_buf[-1][0])
        self.display_changed.emit()

    @property
    def is_roll(self):
        return self.trig_mode == 'auto' and self.time_div >= _ROLL_TIME_DIV

    def set_paused(self, paused: bool):
        if paused == self.paused:
            return
        if paused:
            start = self.t_window_end - self.time_div * 10.0
            frozen = self.trig_mode in ('normal', 'single')
            source_a = (self._capture_a if frozen and self._capture_a is not None
                        else self.buf_a)
            source_b = (self._capture_b if frozen and self._capture_b is not None
                        else self.buf_b)
            self._pause_a = [p for p in source_a
                             if start <= p[0] <= self.t_window_end]
            self._pause_b = [p for p in source_b
                             if start <= p[0] <= self.t_window_end]
            self._repaint_timer.stop()
        else:
            self._resume_pending = True
        self.paused = paused
        self.update()
        self.display_changed.emit()

    def arm_single(self):
        self.single_armed = True
        self.triggered = False
        source_buf = self.buf_a if self.trig_source == 'A' else self.buf_b
        self._last_trigger_time = source_buf[-1][0] if source_buf else None
        self.update()
        self.display_changed.emit()

    def _maintain_buffer(self, buf, latest):
        total = self.time_div * 10.0
        cutoff = min(latest - total * _HISTORY_SCREENS,
                     self.t_window_end - total)
        while buf and buf[0][0] < cutoff:
            buf.popleft()
        if len(buf) > _BUFFER_LIMIT:
            compacted = _compact_samples(list(buf))
            buf.clear()
            buf.extend(compacted)

    def _acquire(self, latest: float):
        if self.trig_mode == 'single' and not self.single_armed:
            return
        if self.is_roll:
            self.t_window_end = latest
            self.triggered = False
            return
        enabled = self.enabled_a if self.trig_source == 'A' else self.enabled_b
        source_buf = self.buf_a if self.trig_source == 'A' else self.buf_b
        coupling = self.coupling_a if self.trig_source == 'A' else self.coupling_b
        inverted = self.invert_a if self.trig_source == 'A' else self.invert_b
        source_samples = (_condition_samples(source_buf, coupling, inverted)
                          if enabled else ())
        total = self.time_div * 10.0
        posttrigger = total * (1.0 - self.trig_position / 10.0)
        scale = self.v_div_a if self.trig_source == 'A' else self.v_div_b
        trigger = _find_trigger(
            source_samples, self.trig_level, self.trig_edge,
            max(scale * 0.02, 1e-12),
            self._last_trigger_time if self._last_trigger_time is not None else float('-inf'),
            latest - posttrigger,
            latest=self.trig_mode != 'single',
        )
        if trigger is not None:
            self._last_trigger_time = trigger
            self.t_window_end = trigger + posttrigger
            self.triggered = True
            if self.trig_mode in ('normal', 'single'):
                start = self.t_window_end - total
                self._capture_a = [point for point in self.buf_a
                                   if start <= point[0] <= self.t_window_end]
                self._capture_b = [point for point in self.buf_b
                                   if start <= point[0] <= self.t_window_end]
            if self.trig_mode == 'single':
                self.single_armed = False
        elif self.trig_mode == 'auto' and (
                not self.triggered or
                latest - (self._last_trigger_time
                          if self._last_trigger_time is not None else latest) > total):
            self.t_window_end = latest
            self.triggered = False

    def clear(self):
        self.buf_a.clear()
        self.buf_b.clear()
        self.t_window_end = 0.0
        self.triggered = False
        self.single_armed = True
        self._last_trigger_time = None
        self._capture_a = self._capture_b = None
        self._pause_a = self._pause_b = None
        self._resume_pending = False
        self._repaint_timer.stop()
        self.update()
        self.display_changed.emit()

    def _display_buffers(self):
        frozen = self.trig_mode in ('normal', 'single')
        display_a = (self._pause_a if self.paused and self._pause_a is not None else
                     self._capture_a if frozen and self._capture_a is not None else
                     self.buf_a)
        display_b = (self._pause_b if self.paused and self._pause_b is not None else
                     self._capture_b if frozen and self._capture_b is not None else
                     self.buf_b)
        return display_a, display_b

    def visible_samples(self, channel):
        buf_a, buf_b = self._display_buffers()
        buf = buf_a if channel == 'A' else buf_b
        coupling = self.coupling_a if channel == 'A' else self.coupling_b
        inverted = self.invert_a if channel == 'A' else self.invert_b
        start = self.t_window_end - self.time_div * 10.0
        return _condition_samples(
            [point for point in buf if start <= point[0] <= self.t_window_end],
            coupling, inverted,
        )

    def measurements(self, channel):
        enabled = self.enabled_a if channel == 'A' else self.enabled_b
        return _measure_samples(self.visible_samples(channel)) if enabled else None

    def cursor_readout(self):
        samples = self.visible_samples(self.cursor_channel)
        start = self.t_window_end - self.time_div * 10.0
        t1 = start + self.cursor_div_1 * self.time_div
        t2 = start + self.cursor_div_2 * self.time_div
        v1 = _value_at_time(samples, t1)
        v2 = _value_at_time(samples, t2)
        dt = abs(t2 - t1)
        return dt, (1.0 / dt if dt else None), (v2 - v1 if v1 is not None and v2 is not None else None)

    def _move_cursor(self, x):
        rect = self.rect().adjusted(2, 2, -2, -2)
        position = min(10.0, max(0.0, (x - rect.left()) * 10.0 / rect.width()))
        if self._drag_cursor == 1:
            self.cursor_div_1 = position
        else:
            self.cursor_div_2 = position
        self.update()
        self.display_changed.emit()

    def mousePressEvent(self, event):
        if self.cursor_enabled and event.button() == Qt.MouseButton.LeftButton:
            rect = self.rect().adjusted(2, 2, -2, -2)
            x1 = rect.left() + rect.width() * self.cursor_div_1 / 10.0
            x2 = rect.left() + rect.width() * self.cursor_div_2 / 10.0
            self._drag_cursor = 1 if abs(event.position().x() - x1) <= abs(event.position().x() - x2) else 2
            self._move_cursor(event.position().x())
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_cursor is not None:
            self._move_cursor(event.position().x())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._drag_cursor = None
        super().mouseReleaseEvent(event)

    # ── Pintado ─────────────────────────────────────────────────────────
    def paintEvent(self, event):
        p = QPainter(self)
        # La traza se limita a la resolución de pantalla más abajo; AA aquí
        # solo duplica trabajo para cientos de segmentos casi colineales.
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        rect = self.rect().adjusted(2, 2, -2, -2)
        # Fondo
        p.fillRect(rect, QBrush(_SCREEN_BG))
        # Marco
        p.setPen(QPen(_AXIS_COLOR, 1))
        p.drawRect(rect)

        # Grilla: 10 divisiones horizontales × 8 verticales
        DX = rect.width() / 10.0
        DY = rect.height() / 8.0
        p.setPen(QPen(_GRID_COLOR, 1, Qt.PenStyle.DotLine))
        for i in range(1, 10):
            x = rect.left() + i * DX
            p.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))
        for j in range(1, 8):
            y = rect.top() + j * DY
            p.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))

        # Ejes centrales más marcados
        p.setPen(QPen(_AXIS_COLOR, 1))
        cx = rect.left() + 5 * DX
        cy = rect.top() + 4 * DY
        p.drawLine(QPointF(cx, rect.top()), QPointF(cx, rect.bottom()))
        p.drawLine(QPointF(rect.left(), cy), QPointF(rect.right(), cy))

        # Marcas de tick (small ticks en los ejes centrales)
        p.setPen(QPen(_AXIS_COLOR, 1))
        for i in range(1, 10):
            x = rect.left() + i * DX
            p.drawLine(QPointF(x, cy - 3), QPointF(x, cy + 3))
        for j in range(1, 8):
            y = rect.top() + j * DY
            p.drawLine(QPointF(cx - 3, y), QPointF(cx + 3, y))

        if not self.is_roll:
            trigger_x = rect.left() + rect.width() * self.trig_position / 10.0
            trigger_color = QColor(230, 145, 55)
            p.setPen(QPen(trigger_color, 1))
            p.setBrush(QBrush(trigger_color))
            marker = QPainterPath(QPointF(trigger_x - 5, rect.top()))
            marker.lineTo(QPointF(trigger_x + 5, rect.top()))
            marker.lineTo(QPointF(trigger_x, rect.top() + 8))
            marker.closeSubpath()
            p.drawPath(marker)
            trigger_scale = self.v_div_a if self.trig_source == 'A' else self.v_div_b
            trigger_pos = self.pos_a if self.trig_source == 'A' else self.pos_b
            trigger_y = rect.top() + (0.5 - (
                self.trig_level + trigger_pos * trigger_scale
            ) / (trigger_scale * 8.0)) * rect.height()
            if rect.top() <= trigger_y <= rect.bottom():
                marker = QPainterPath(QPointF(rect.left(), trigger_y - 5))
                marker.lineTo(QPointF(rect.left(), trigger_y + 5))
                marker.lineTo(QPointF(rect.left() + 8, trigger_y))
                marker.closeSubpath()
                p.drawPath(marker)

        # ── Trazas ──────────────────────────────────────────────────────
        p.setBrush(QBrush(Qt.BrushStyle.NoBrush))
        # La ventana de tiempo es [t_end - 10·time_div, t_end].
        t_total = self.time_div * 10.0
        if t_total <= 0:
            return
        t_end = self.t_window_end
        t_start = t_end - t_total

        def draw_trace(samples, v_div, pos_divs, color, enabled):
            if not enabled or v_div <= 0 or len(samples) < 2:
                return
            points = []
            for t, v in samples:
                frac_x = (t - t_start) / t_total
                # Volts: positivo arriba (y decrece hacia arriba en Qt)
                # 4 divisiones positivas y 4 negativas alrededor del centro.
                v_offset = v + pos_divs * v_div  # pos_divs sube la traza
                frac_y = 0.5 - (v_offset / (v_div * 8.0))  # 8 divs totales
                x = rect.left() + frac_x * rect.width()
                y = rect.top() + frac_y * rect.height()
                # Clamp para evitar líneas que salen al infinito si se sale
                # un montón de la escala (sigue siendo informativo).
                if y < rect.top() - 200:   y = rect.top() - 200
                if y > rect.bottom() + 200: y = rect.bottom() + 200
                points.append((x, y))

            if len(points) < 2:
                return
            p.setPen(QPen(color, 1.2))
            path = QPainterPath()
            if len(points) <= rect.width() * 2:
                path.moveTo(*points[0])
                for point in points[1:]:
                    path.lineTo(*point)
            else:
                # Conserva mínimo y máximo de cada columna para no perder
                # picos al reducir miles de muestras a píxeles.
                columns = [None] * max(1, rect.width())
                left = rect.left()
                for x, y in points:
                    column = min(len(columns) - 1, max(0, int(x - left)))
                    bounds = columns[column]
                    if bounds is None:
                        columns[column] = [y, y]
                    else:
                        bounds[0] = min(bounds[0], y)
                        bounds[1] = max(bounds[1], y)
                first = True
                for column, bounds in enumerate(columns):
                    if bounds is None:
                        continue
                    x = left + column
                    if first:
                        path.moveTo(x, bounds[0])
                        first = False
                    else:
                        path.lineTo(x, bounds[0])
                    if bounds[1] != bounds[0]:
                        path.lineTo(x, bounds[1])
            p.drawPath(path)

        draw_trace(self.visible_samples('A'), self.v_div_a, self.pos_a,
                   _TRACE_A_COLOR, self.enabled_a)
        draw_trace(self.visible_samples('B'), self.v_div_b, self.pos_b,
                   _TRACE_B_COLOR, self.enabled_b)

        if self.cursor_enabled:
            for number, div, color in (
                    (1, self.cursor_div_1, QColor(220, 90, 210)),
                    (2, self.cursor_div_2, QColor(80, 220, 150))):
                x = rect.left() + rect.width() * div / 10.0
                p.setPen(QPen(color, 1, Qt.PenStyle.DashLine))
                p.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))
                p.drawText(QPointF(x + 3, rect.bottom() - 4), str(number))

        # ── Leyenda superior (Time/Div, V/Div) ──────────────────────────
        p.setPen(QPen(QColor(180, 200, 190), 1))
        p.setFont(_qfont('Menlo', 8))
        a_info = self._fmt_volts(self.v_div_a) + '/div' if self.enabled_a else 'OFF'
        b_info = self._fmt_volts(self.v_div_b) + '/div' if self.enabled_b else 'OFF'
        info = (f"  T={self._fmt_time(self.time_div)}/div    "
                f"A={a_info}    B={b_info}    "
                f"{self._trigger_status()}")
        p.drawText(rect.adjusted(0, -2, 0, 0), Qt.AlignmentFlag.AlignTop, info)

    def _trigger_status(self) -> str:
        if self.paused:
            return 'PAUSED'
        if self.is_roll:
            return 'ROLL'
        if self.trig_mode == 'single':
            return 'ARMED' if self.single_armed else 'SINGLE'
        return 'TRIG' if self.triggered else self.trig_mode.upper()

    @staticmethod
    def _fmt_time(t: float) -> str:
        if t < 1e-3:  return f"{t*1e6:.0f}µs"
        if t < 1.0:   return f"{t*1e3:.0f}ms"
        return f"{t:g}s"

    @staticmethod
    def _fmt_volts(v: float) -> str:
        if v < 1.0:   return f"{v*1e3:.0f}mV"
        return f"{v:g}V"


class OscilloscopeDialog(QDialog):
    """Panel del osciloscopio. No-modal, recibe muestras del live transient."""
    changed = pyqtSignal()

    def __init__(self, item: 'ComponentItem', parent=None):
        super().__init__(parent)
        self.item = item
        self.setWindowTitle(self.tr("Oscilloscope — {name}").format(name=item.name))
        self.setModal(False)
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.Tool)
        # Tamaño cómodo por defecto, redimensionable
        self.resize(1000, 650)

        # Hardware: hilo + configuración persistida en el item.
        self._hw_thread = None
        self._hw_cfg = dict(getattr(item, 'osc_hw_config', {}) or {})

        self._build_ui()
        self._load_from_item()
        self._sync_screen()   # llevar config al widget pantalla

    # ── UI ──────────────────────────────────────────────────────────────
    def _build_ui(self):
        root = QHBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)

        # Pantalla a la izquierda
        self.screen = _Screen(self)
        left = QVBoxLayout()
        left.addWidget(self.screen, stretch=1)

        gb_measure = QGroupBox(self.tr("Measurements"))
        measure_layout = QVBoxLayout(gb_measure)
        self.lbl_measure_a = QLabel()
        self.lbl_measure_b = QLabel()
        self.lbl_measure_a.setFont(_qfont('Menlo', 8))
        self.lbl_measure_b.setFont(_qfont('Menlo', 8))
        measure_layout.addWidget(self.lbl_measure_a)
        measure_layout.addWidget(self.lbl_measure_b)
        cursor_row = QHBoxLayout()
        self.ck_cursors = QCheckBox(self.tr("Cursors"))
        self.ck_cursors.setToolTip(self.tr("Drag the two vertical cursors on the screen."))
        self.cb_cursor_channel = QComboBox(); self.cb_cursor_channel.addItems(['A', 'B'])
        self.lbl_cursor = QLabel()
        self.lbl_cursor.setFont(_qfont('Menlo', 8))
        cursor_row.addWidget(self.ck_cursors)
        cursor_row.addWidget(self.cb_cursor_channel)
        cursor_row.addWidget(self.lbl_cursor, stretch=1)
        measure_layout.addLayout(cursor_row)
        left.addWidget(gb_measure)
        root.addLayout(left, stretch=1)

        # Panel de controles a la derecha
        right = QVBoxLayout()
        right.setSpacing(6)

        # Time base
        gb_time = QGroupBox(self.tr("Time base"))
        f_time = QFormLayout(gb_time)
        self.cb_time = QComboBox()
        for v, lbl in _TIME_DIV_PRESETS:
            self.cb_time.addItem(lbl, v)
        f_time.addRow(self.tr("Time/Div:"), self.cb_time)
        self.btn_autoscale = QPushButton(self.tr("Auto scale"))
        self.btn_autoscale.clicked.connect(self._on_autoscale)
        f_time.addRow(self.btn_autoscale)
        right.addWidget(gb_time)

        # Canal A
        gb_a = QGroupBox(self.tr("Channel A (yellow)"))
        f_a = QFormLayout(gb_a)
        self.cb_va = QComboBox()
        for v, lbl in _V_DIV_PRESETS:
            self.cb_va.addItem(lbl, v)
        f_a.addRow(self.tr("V/Div:"), self.cb_va)
        self.sb_pos_a = QDoubleSpinBox()
        self.sb_pos_a.setRange(-4.0, 4.0)
        self.sb_pos_a.setDecimals(2)
        self.sb_pos_a.setSingleStep(0.1)
        self.sb_pos_a.setSuffix(" div")
        f_a.addRow(self.tr("Position:"), self.sb_pos_a)
        opts_a = QWidget()
        opts_a_layout = QHBoxLayout(opts_a)
        opts_a_layout.setContentsMargins(0, 0, 0, 0)
        self.ck_enabled_a = QCheckBox(self.tr("On")); self.ck_enabled_a.setChecked(True)
        self.ck_invert_a = QCheckBox(self.tr("Invert"))
        self.cb_coupling_a = QComboBox(); self.cb_coupling_a.addItems(['DC', 'AC'])
        opts_a_layout.addWidget(self.ck_enabled_a)
        opts_a_layout.addWidget(self.ck_invert_a)
        opts_a_layout.addWidget(self.cb_coupling_a)
        f_a.addRow(opts_a)
        right.addWidget(gb_a)

        # Canal B
        gb_b = QGroupBox(self.tr("Channel B (cyan)"))
        f_b = QFormLayout(gb_b)
        self.cb_vb = QComboBox()
        for v, lbl in _V_DIV_PRESETS:
            self.cb_vb.addItem(lbl, v)
        f_b.addRow(self.tr("V/Div:"), self.cb_vb)
        self.sb_pos_b = QDoubleSpinBox()
        self.sb_pos_b.setRange(-4.0, 4.0)
        self.sb_pos_b.setDecimals(2)
        self.sb_pos_b.setSingleStep(0.1)
        self.sb_pos_b.setSuffix(" div")
        f_b.addRow(self.tr("Position:"), self.sb_pos_b)
        opts_b = QWidget()
        opts_b_layout = QHBoxLayout(opts_b)
        opts_b_layout.setContentsMargins(0, 0, 0, 0)
        self.ck_enabled_b = QCheckBox(self.tr("On")); self.ck_enabled_b.setChecked(True)
        self.ck_invert_b = QCheckBox(self.tr("Invert"))
        self.cb_coupling_b = QComboBox(); self.cb_coupling_b.addItems(['DC', 'AC'])
        opts_b_layout.addWidget(self.ck_enabled_b)
        opts_b_layout.addWidget(self.ck_invert_b)
        opts_b_layout.addWidget(self.cb_coupling_b)
        f_b.addRow(opts_b)
        right.addWidget(gb_b)

        # Trigger
        gb_trig = QGroupBox(self.tr("Trigger"))
        f_trig = QFormLayout(gb_trig)
        self.cb_trig_mode = QComboBox()
        self.cb_trig_mode.addItem(self.tr('Auto'), 'auto')
        self.cb_trig_mode.addItem(self.tr('Normal'), 'normal')
        self.cb_trig_mode.addItem(self.tr('Single'), 'single')
        self.cb_trig_src = QComboBox(); self.cb_trig_src.addItems(['A', 'B'])
        self.cb_trig_edge = QComboBox()
        self.cb_trig_edge.addItem(self.tr('Rising'), 'rising')
        self.cb_trig_edge.addItem(self.tr('Falling'), 'falling')
        self.sb_trig_lvl = QDoubleSpinBox()
        self.sb_trig_lvl.setRange(-1e6, 1e6); self.sb_trig_lvl.setSuffix(" V")
        self.sb_trig_lvl.setDecimals(3)
        self.sb_trig_pos = QDoubleSpinBox()
        self.sb_trig_pos.setRange(0.0, 10.0)
        self.sb_trig_pos.setDecimals(1)
        self.sb_trig_pos.setSingleStep(0.5)
        self.sb_trig_pos.setSuffix(" div")
        self.btn_arm = QPushButton(self.tr('Arm'))
        self.btn_arm.clicked.connect(self.screen.arm_single)
        f_trig.addRow(self.tr("Mode:"), self.cb_trig_mode)
        f_trig.addRow(self.tr("Source:"), self.cb_trig_src)
        f_trig.addRow(self.tr("Edge:"),   self.cb_trig_edge)
        f_trig.addRow(self.tr("Level:"),  self.sb_trig_lvl)
        f_trig.addRow(self.tr("Horizontal:"), self.sb_trig_pos)
        f_trig.addRow(self.btn_arm)
        right.addWidget(gb_trig)

        # Botones
        btn_row = QHBoxLayout()
        self.btn_pause = QPushButton(self.tr("Pause"))
        self.btn_pause.setCheckable(True)
        self.btn_pause.toggled.connect(self._on_pause_toggled)
        btn_row.addWidget(self.btn_pause)
        self.btn_clear = QPushButton(self.tr("Clear"))
        self.btn_clear.clicked.connect(self._on_clear)
        btn_row.addWidget(self.btn_clear)
        self.btn_hw = QPushButton(self.tr("Hardware…"))
        self.btn_hw.setToolTip(
            self.tr("Connect the oscilloscope to a microcontroller (RP2040 / STM32 / …) "
                    "via USB-CDC, or use Mock device to test without hardware."))
        self.btn_hw.clicked.connect(self._on_hardware_button)
        btn_row.addWidget(self.btn_hw)
        btn_row.addStretch(1)
        right.addLayout(btn_row)

        # Etiqueta de estado del HW (cambia de color según conexión)
        self.lbl_hw_status = QLabel(self.tr('HW disconnected'))
        self.lbl_hw_status.setStyleSheet('color: #888888;')
        right.addWidget(self.lbl_hw_status)
        right.addStretch(1)

        root.addLayout(right)

        # Conexiones — sólo después de armar todo
        self.cb_time.currentIndexChanged.connect(self._on_param_changed)
        self.cb_va.currentIndexChanged.connect(self._on_param_changed)
        self.cb_vb.currentIndexChanged.connect(self._on_param_changed)
        self.sb_pos_a.valueChanged.connect(self._on_param_changed)
        self.sb_pos_b.valueChanged.connect(self._on_param_changed)
        self.ck_enabled_a.toggled.connect(self._on_param_changed)
        self.ck_enabled_b.toggled.connect(self._on_param_changed)
        self.ck_invert_a.toggled.connect(self._on_param_changed)
        self.ck_invert_b.toggled.connect(self._on_param_changed)
        self.cb_coupling_a.currentTextChanged.connect(self._on_param_changed)
        self.cb_coupling_b.currentTextChanged.connect(self._on_param_changed)
        self.cb_trig_mode.currentIndexChanged.connect(self._on_param_changed)
        self.cb_trig_src.currentTextChanged.connect(self._on_param_changed)
        self.cb_trig_edge.currentTextChanged.connect(self._on_param_changed)
        self.sb_trig_lvl.valueChanged.connect(self._on_param_changed)
        self.sb_trig_pos.valueChanged.connect(self._on_param_changed)
        self.ck_cursors.toggled.connect(self._on_cursors_toggled)
        self.cb_cursor_channel.currentTextChanged.connect(self._on_cursor_channel_changed)
        self.screen.display_changed.connect(self._refresh_measurements)

    # ── Carga/escritura del item ────────────────────────────────────────
    def _set_combo_to_value(self, combo: QComboBox, target: float):
        """Selecciona el item del combo cuyo dato (float) es más cercano a `target`."""
        best_i = 0
        best_d = float('inf')
        for i in range(combo.count()):
            v = combo.itemData(i)
            d = abs(v - target)
            if d < best_d:
                best_d = d; best_i = i
        combo.setCurrentIndex(best_i)

    def _load_from_item(self):
        it = self.item
        widgets = (self.cb_time, self.cb_va, self.cb_vb, self.sb_pos_a,
                   self.sb_pos_b, self.cb_trig_mode, self.cb_trig_src,
                   self.cb_trig_edge, self.sb_trig_lvl, self.sb_trig_pos,
                   self.ck_enabled_a, self.ck_enabled_b,
                   self.ck_invert_a, self.ck_invert_b,
                   self.cb_coupling_a, self.cb_coupling_b)
        for w in widgets: w.blockSignals(True)
        try:
            self._set_combo_to_value(self.cb_time, float(it.osc_time_div))
            self._set_combo_to_value(self.cb_va, float(it.osc_v_div_a))
            self._set_combo_to_value(self.cb_vb, float(it.osc_v_div_b))
            self.sb_pos_a.setValue(float(it.osc_pos_a))
            self.sb_pos_b.setValue(float(it.osc_pos_b))
            self.ck_enabled_a.setChecked(bool(it.osc_enabled_a))
            self.ck_enabled_b.setChecked(bool(it.osc_enabled_b))
            self.ck_invert_a.setChecked(bool(it.osc_invert_a))
            self.ck_invert_b.setChecked(bool(it.osc_invert_b))
            self.cb_coupling_a.setCurrentText(it.osc_coupling_a)
            self.cb_coupling_b.setCurrentText(it.osc_coupling_b)
            index = self.cb_trig_mode.findData(it.osc_trig_mode)
            self.cb_trig_mode.setCurrentIndex(index if index >= 0 else 0)
            self.cb_trig_src.setCurrentText(it.osc_trig_source)
            index = self.cb_trig_edge.findData(it.osc_trig_edge)
            self.cb_trig_edge.setCurrentIndex(index if index >= 0 else 0)
            self.sb_trig_lvl.setValue(float(it.osc_trig_level))
            self.sb_trig_pos.setValue(float(it.osc_trig_position))
        finally:
            for w in widgets: w.blockSignals(False)

    def _on_param_changed(self):
        it = self.item
        it.osc_time_div = float(self.cb_time.currentData())
        it.osc_v_div_a  = float(self.cb_va.currentData())
        it.osc_v_div_b  = float(self.cb_vb.currentData())
        it.osc_pos_a    = float(self.sb_pos_a.value())
        it.osc_pos_b    = float(self.sb_pos_b.value())
        it.osc_enabled_a = self.ck_enabled_a.isChecked()
        it.osc_enabled_b = self.ck_enabled_b.isChecked()
        it.osc_invert_a = self.ck_invert_a.isChecked()
        it.osc_invert_b = self.ck_invert_b.isChecked()
        it.osc_coupling_a = self.cb_coupling_a.currentText()
        it.osc_coupling_b = self.cb_coupling_b.currentText()
        it.osc_trig_mode   = self.cb_trig_mode.currentData()
        it.osc_trig_source = self.cb_trig_src.currentText()
        it.osc_trig_edge   = self.cb_trig_edge.currentData()
        it.osc_trig_level  = float(self.sb_trig_lvl.value())
        it.osc_trig_position = float(self.sb_trig_pos.value())
        self._sync_screen()
        self.changed.emit()

    def _sync_screen(self):
        s = self.screen
        s.time_div = float(self.item.osc_time_div)
        s.v_div_a  = float(self.item.osc_v_div_a)
        s.v_div_b  = float(self.item.osc_v_div_b)
        s.pos_a    = float(self.item.osc_pos_a)
        s.pos_b    = float(self.item.osc_pos_b)
        s.enabled_a = bool(self.item.osc_enabled_a)
        s.enabled_b = bool(self.item.osc_enabled_b)
        s.invert_a = bool(self.item.osc_invert_a)
        s.invert_b = bool(self.item.osc_invert_b)
        s.coupling_a = self.item.osc_coupling_a
        s.coupling_b = self.item.osc_coupling_b
        s.configure_trigger(
            self.item.osc_trig_source,
            self.item.osc_trig_edge,
            float(self.item.osc_trig_level),
            self.item.osc_trig_mode,
            float(self.item.osc_trig_position),
        )
        for control in (self.cb_trig_src, self.cb_trig_edge,
                        self.sb_trig_lvl, self.sb_trig_pos):
            control.setEnabled(not s.is_roll)
        self.btn_arm.setEnabled(self.item.osc_trig_mode == 'single')
        for enabled, controls in (
                (s.enabled_a, (self.cb_va, self.sb_pos_a,
                               self.ck_invert_a, self.cb_coupling_a)),
                (s.enabled_b, (self.cb_vb, self.sb_pos_b,
                               self.ck_invert_b, self.cb_coupling_b))):
            for control in controls:
                control.setEnabled(enabled)
        s.update()

    def _on_clear(self):
        self.screen.clear()

    def _on_pause_toggled(self, paused: bool):
        self.screen.set_paused(paused)
        self.btn_pause.setText(self.tr("Resume") if paused else self.tr("Pause"))

    def _on_autoscale(self):
        s = self.screen
        changed = (self.cb_time, self.cb_va, self.cb_vb,
                   self.sb_pos_a, self.sb_pos_b)
        for widget in changed:
            widget.blockSignals(True)
        try:
            for channel, enabled, combo, position, coupling in (
                    ('A', s.enabled_a, self.cb_va, self.sb_pos_a, s.coupling_a),
                    ('B', s.enabled_b, self.cb_vb, self.sb_pos_b, s.coupling_b)):
                samples = s.visible_samples(channel) if enabled else []
                if len(samples) < 2:
                    continue
                values = [value for _, value in samples]
                center = (min(values) + max(values)) / 2.0
                required = max((max(values) - min(values)) / 6.4,
                               abs(center) / 3.5 if coupling == 'DC' else 0.0)
                index = combo.count() - 1
                for i in range(combo.count()):
                    if float(combo.itemData(i)) >= required:
                        index = i
                        break
                combo.setCurrentIndex(index)
                v_div = float(combo.currentData())
                position.setValue(max(-4.0, min(4.0, -center / v_div)))

            for channel in (s.trig_source, 'B' if s.trig_source == 'A' else 'A'):
                enabled = s.enabled_a if channel == 'A' else s.enabled_b
                period = _estimate_period(s.visible_samples(channel)) if enabled else None
                if period:
                    self._set_combo_to_value(self.cb_time, period / 5.0)
                    break
        finally:
            for widget in changed:
                widget.blockSignals(False)
        self._on_param_changed()

    @staticmethod
    def _fmt_eng(value, unit):
        if value is None:
            return '—'
        magnitude = abs(value)
        if magnitude < 1e-12:
            return f"0 {unit}"
        for factor, prefix in ((1e9, 'G'), (1e6, 'M'), (1e3, 'k'),
                               (1.0, ''), (1e-3, 'm'), (1e-6, 'µ'),
                               (1e-9, 'n')):
            if magnitude >= factor or factor == 1e-9:
                return f"{value / factor:.4g} {prefix}{unit}"
        return f"{value:.4g} {unit}"

    def _refresh_measurements(self):
        def line(channel):
            data = self.screen.measurements(channel)
            if data is None:
                enabled = (self.screen.enabled_a if channel == 'A'
                           else self.screen.enabled_b)
                return f"{channel}: {'NO DATA' if enabled else 'OFF'}"
            return (f"{channel}:  f={self._fmt_eng(data['frequency'], 'Hz')}  "
                    f"T={self._fmt_eng(data['period'], 's')}  "
                    f"Vpp={self._fmt_eng(data['vpp'], 'V')}  "
                    f"max={self._fmt_eng(data['maximum'], 'V')}  "
                    f"min={self._fmt_eng(data['minimum'], 'V')}  "
                    f"avg={self._fmt_eng(data['average'], 'V')}  "
                    f"RMS={self._fmt_eng(data['rms'], 'V')}")

        self.lbl_measure_a.setText(line('A'))
        self.lbl_measure_b.setText(line('B'))
        if self.screen.cursor_enabled:
            dt, inverse, dv = self.screen.cursor_readout()
            self.lbl_cursor.setText(
                f"Δt={self._fmt_eng(dt, 's')}   "
                f"1/Δt={self._fmt_eng(inverse, 'Hz')}   "
                f"ΔV={self._fmt_eng(dv, 'V')}")
        else:
            self.lbl_cursor.clear()

    def _on_cursors_toggled(self, enabled):
        self.screen.cursor_enabled = enabled
        self.screen.update()
        self._refresh_measurements()

    def _on_cursor_channel_changed(self, channel):
        self.screen.cursor_channel = channel
        self._refresh_measurements()

    # ── API consumida por MainWindow._push_to_open_instruments ──────────
    def push_samples(self, tr: dict, pin_node: dict):
        """Recibe el último resultado de `solve_transient`. Calcula
        V(A+)−V(A−) y V(B+)−V(B−) y los appendea al buffer."""
        t_arr  = tr.get('time')
        v_dict = tr.get('voltages', {})
        if t_arr is None or not v_dict:
            return

        n_ap = self.item.node1.strip() or pin_node.get(f"{self.item.name}__p1", "")
        n_am = self.item.node2.strip() or pin_node.get(f"{self.item.name}__p2", "")
        n_bp = pin_node.get(f"{self.item.name}__p3", "")
        n_bm = pin_node.get(f"{self.item.name}__p4", "")

        def diff(node_p, node_m):
            # Devuelve list de v(node_p) − v(node_m) por muestra.
            ap = v_dict.get(node_p)
            am = v_dict.get(node_m)
            if ap is None and am is None:
                return None
            n = len(t_arr)
            ap = ap if ap is not None else [0.0] * n
            am = am if am is not None else [0.0] * n
            # Si node_m es '0'/GND, v(0) = 0
            if node_m in ('0', '', 'gnd', 'GND'):
                am = [0.0] * n
            if node_p in ('0', '', 'gnd', 'GND'):
                ap = [0.0] * n
            m = min(len(ap), len(am), n)
            return [ap[i] - am[i] for i in range(m)]

        v_a = diff(n_ap, n_am)
        v_b = diff(n_bp, n_bm)
        self.screen.push(t_arr, v_a, v_b)

    # ── Hardware ────────────────────────────────────────────────────────
    def _on_hardware_button(self):
        """Abre el sub-diálogo de hardware. Si el usuario acepta y hay
        un hilo previo corriendo, lo desconecta primero."""
        from kirho.ui.dialogs.hardware_source_dialog import HardwareSourceDialog
        is_connected = self._hw_thread is not None and self._hw_thread.isRunning()
        if is_connected:
            # Botón funciona como "Desconectar" cuando ya hay stream
            self._stop_hw_thread()
            self._set_hw_status(False, msg=self.tr('HW disconnected'))
            self.btn_hw.setText(self.tr('Hardware…'))
            return
        dlg = HardwareSourceDialog(self._hw_cfg, COLORS, parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        cfg = dlg.get_result()
        if not cfg:
            return
        self._hw_cfg = cfg
        # Persistir en el item para que sobreviva save/load y reopen del panel.
        try:
            self.item.osc_hw_config = dict(cfg)
        except Exception:
            pass
        self._start_hw_thread(cfg)

    def _start_hw_thread(self, cfg: dict):
        from kirho.engine.hw_stream import HardwareStreamThread
        self.screen.clear()
        self._hw_thread = HardwareStreamThread(cfg, parent=self)
        self._hw_thread.samples_received.connect(self._on_hw_samples)
        self._hw_thread.error_occurred.connect(self._on_hw_error)
        self._hw_thread.connection_state.connect(self._on_hw_state)
        self._hw_thread.start()
        self.btn_hw.setText(self.tr('Disconnect HW'))

    def _stop_hw_thread(self):
        if self._hw_thread is None:
            return
        try:
            self._hw_thread.stop()
        except Exception:
            pass
        self._hw_thread = None

    def _on_hw_samples(self, ts: list, va: list, vb: list):
        """Empuja a la pantalla las muestras del HW como si vinieran del sim."""
        self.screen.push(ts, va, vb)

    def _on_hw_state(self, connected: bool):
        if connected:
            self._set_hw_status(True, msg=self.tr('HW connected'))
        else:
            self._set_hw_status(False, msg=self.tr('HW disconnected'))
            self.btn_hw.setText(self.tr('Hardware…'))

    def _on_hw_error(self, msg: str):
        self._set_hw_status(False, msg=self.tr('HW error: {message}').format(message=msg))
        self.btn_hw.setText(self.tr('Hardware…'))

    def _set_hw_status(self, connected: bool, msg: str):
        color = '#27ae60' if connected else '#888888'
        if msg.startswith(self.tr('HW error:').rstrip(':')):
            color = '#e94560'
        self.lbl_hw_status.setStyleSheet(f'color: {color};')
        self.lbl_hw_status.setText(msg)

    # ── Cierre ──────────────────────────────────────────────────────────
    def closeEvent(self, event):
        # Importante: parar el hilo HW antes de soltar el diálogo para no
        # dejar el puerto serie abierto ni que un sample llegue a un Qt
        # widget ya destruido (crash).
        self._stop_hw_thread()
        if getattr(self.item, '_panel_dialog', None) is self:
            self.item._panel_dialog = None
        super().closeEvent(event)
