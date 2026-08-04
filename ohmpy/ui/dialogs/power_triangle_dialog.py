from __future__ import annotations

import math

from PyQt6.QtCore import QPointF, Qt
from PyQt6.QtGui import QColor, QFont, QPainter, QPen, QPolygonF
from PyQt6.QtWidgets import (
    QComboBox, QDialog, QDoubleSpinBox, QGroupBox, QHBoxLayout, QLabel,
    QPushButton, QSpinBox, QSplitter, QVBoxLayout, QWidget,
)

from ohmpy.engine import MNASolver


class PowerTriangleDialog(QDialog):
    """
    Ventana emergente con:
      - Triángulo de potencia dibujado (P, Q, S, ángulo φ)
      - Corrección de factor de potencia interactiva
    """

    def __init__(self, ac_result: dict, colors=None, parent=None):
        super().__init__(parent)
        self.ac_result = ac_result
        self.colors = colors or {}
        self.setWindowTitle(self.tr("Power Triangle"))
        self.setMinimumSize(620, 580)
        self._apply_style()
        self._build_ui()

    def _color(self, key: str, fallback: str) -> str:
        return self.colors.get(key, fallback)

    def _apply_style(self):
        bg = self._color('panel', '#16213e')
        field_bg = self._color('bg', '#1a1a2e')
        text = self._color('text', '#e0e0e0')
        dim = self._color('text_dim', '#7f8c8d')
        border = self._color('panel_brd', '#0f3460')
        accent = self._color('component', '#e94560')
        hover = self._color('comp_sel', '#f5a623')
        self.setStyleSheet(f"""
            QDialog {{
                background: {bg};
                color: {text};
            }}
            QLabel, QGroupBox {{
                color: {text};
            }}
            QGroupBox {{
                border: 1px solid {border};
                border-radius: 4px;
                margin-top: 10px;
                padding-top: 8px;
            }}
            QGroupBox::title {{
                subcontrol-origin: margin;
                left: 8px;
                padding: 0 4px;
                color: {dim};
            }}
            QComboBox, QDoubleSpinBox, QSpinBox {{
                background: {field_bg};
                color: {text};
                border: 1px solid {border};
                border-radius: 4px;
                padding: 4px;
            }}
            QPushButton {{
                background: {accent};
                color: white;
                border: 0;
                border-radius: 4px;
                padding: 6px 12px;
            }}
            QPushButton:hover {{
                background: {hover};
            }}
            QSplitter::handle {{
                background: {border};
            }}
        """)

    def _build_ui(self):
        main = QVBoxLayout(self)

        splitter = QSplitter(Qt.Orientation.Vertical)

        self.canvas = _PowerTriangleCanvas(self.ac_result['total'], self.colors)
        self.canvas.setMinimumHeight(260)
        splitter.addWidget(self.canvas)

        box = QGroupBox(self.tr("Power Factor Correction"))
        box_layout = QHBoxLayout(box)

        box_layout.addWidget(QLabel(self.tr("Target PF:")))
        self.fp_spin = QDoubleSpinBox()
        self.fp_spin.setRange(0.01, 1.0)
        self.fp_spin.setDecimals(3)
        self.fp_spin.setSingleStep(0.01)
        self.fp_spin.setValue(1.0)
        box_layout.addWidget(self.fp_spin)

        box_layout.addSpacing(10)
        box_layout.addWidget(QLabel(self.tr("Type:")))
        self.target_combo = QComboBox()
        self.target_combo.addItem(self.tr("Auto (same domain)"), 'auto')
        self.target_combo.addItem(self.tr("Inductive (Q > 0)"), 'inductive')
        self.target_combo.addItem(self.tr("Capacitive (Q < 0)"), 'capacitive')
        self.target_combo.setToolTip(
            self.tr("Auto: keeps the current domain (capacitive↔capacitive,\n"
                    "inductive↔inductive).\n"
                    "Inductive: forces a positive resulting Q (it may cross\n"
                    "from capacitive to inductive by adding a large inductor).\n"
                    "Capacitive: forces a negative resulting Q.")
        )
        box_layout.addWidget(self.target_combo)

        self.decimals_label = QLabel(self.tr("Decimals:"))
        self.decimals_spinbox = QSpinBox()
        self.decimals_spinbox.setRange(0, 15)
        self.decimals_spinbox.setValue(4)
        box_layout.addWidget(self.decimals_label)
        box_layout.addWidget(self.decimals_spinbox)

        self.correct_btn = QPushButton(self.tr("Calculate correction"))
        self.correct_btn.clicked.connect(self._on_correct)
        box_layout.addWidget(self.correct_btn)
        box_layout.addStretch()

        self.corr_label = QLabel("")
        self.corr_label.setWordWrap(True)
        self.corr_label.setFont(QFont('Menlo', 9))

        bottom = QWidget()
        bl = QVBoxLayout(bottom)
        bl.addWidget(box)
        bl.addWidget(self.corr_label)
        splitter.addWidget(bottom)

        main.addWidget(splitter)

        btn_row = QHBoxLayout()
        reset_btn = QPushButton(self.tr("Reset view"))
        reset_btn.clicked.connect(self.canvas.reset_view)
        btn_row.addWidget(reset_btn)
        btn_row.addStretch()
        close_btn = QPushButton(self.tr("Close"))
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)
        main.addLayout(btn_row)

    def _on_correct(self):
        solver = MNASolver()
        total = self.ac_result['total']
        freq = self.ac_result['frequency']
        fp_tgt = self.fp_spin.value()
        target_type = self.target_combo.currentData() or 'auto'
        decsel = self.decimals_spinbox.value()
        res = solver.correct_power_factor(
            total, freq, fp_tgt, target_type=target_type)

        if 'error' in res:
            self.corr_label.setText(res['error'])
            return

        tipo = res['type']
        val = res['value']
        Q_corr = res['Q_corr']
        fp_new = res['fp_new']
        fp_type_new = res.get('fp_type_new', '')
        form = res['formula']
        tt_used = res.get('target_type', 'auto')

        if tipo == 'capacitor':
            val_str = self.tr("C = {value:.{decimals}f} µF  (normalized to 1 Vrms)").format(
                value=val * 1e6, decimals=decsel)
            emoji = self.tr("Capacitor")
            note = self.tr(
                "Multiply C by (1/Vrms²) using your actual line voltage. "
                "E.g., for Vrms=120V → C_real = C_norm / 120²")
        else:
            val_str = self.tr("L = {value:.{decimals}f} mH  (normalized to 1 Vrms)").format(
                value=val * 1e3, decimals=decsel)
            emoji = self.tr("Inductor")
            note = self.tr(
                "Multiply L by Vrms² using your actual line voltage. "
                "E.g., for Vrms=120V → L_real = L_norm · 120²")

        modo_map = {'auto': self.tr('Auto'), 'inductive': self.tr('Inductive'),
                    'capacitive': self.tr('Capacitive')}
        fp_type_map = {'inductive': self.tr('inductive'),
                       'capacitive': self.tr('capacitive'),
                       'unity': self.tr('unity')}
        modo_str = modo_map.get(tt_used, tt_used)
        fp_type_str = fp_type_map.get(fp_type_new, fp_type_new)

        text = (
            self.tr("  Target mode:        {mode}\n").format(mode=modo_str)
            + self.tr("  Correction element: {element} in PARALLEL\n").format(element=emoji)
            + f"  {val_str}\n"
            + self.tr("  Q to compensate:    {value:.4f} VAR\n").format(value=Q_corr)
            + self.tr("  Resulting PF:       {value:.4f}  ({type})\n").format(value=fp_new, type=fp_type_str)
            + self.tr("  Formula:            {formula}\n").format(formula=form)
            + f"  {note}"
        )
        self.corr_label.setText(text)
        self.canvas.set_correction(res)
        self.canvas.update()


class _PowerTriangleCanvas(QWidget):
    """Widget que dibuja el triángulo de potencia S, P, Q, ángulo φ.
    Soporta zoom con rueda del ratón y pan con click+arrastre."""

    def __init__(self, total: dict, colors=None, parent=None):
        super().__init__(parent)
        self.total = total
        self.colors = colors or {}
        self._correction = None
        self._zoom = 1.0
        self._pan_x = 0.0
        self._pan_y = 0.0
        self._dragging = False
        self._last_pos = None

    def _color(self, key: str, fallback: str) -> QColor:
        return QColor(self.colors.get(key, fallback))

    def set_correction(self, corr: dict):
        self._correction = corr
        self.update()

    def wheelEvent(self, event):
        """Zoom con rueda del ratón centrado en el cursor."""
        delta = event.pixelDelta().y() or event.angleDelta().y()
        if not delta:
            event.ignore()
            return
        factor = 1.15 ** (delta / (120 if event.pixelDelta().isNull() else 240))
        old_zoom = self._zoom
        self._zoom *= factor
        self._zoom = max(0.2, min(self._zoom, 10.0))
        mx = event.position().x()
        my = event.position().y()
        self._pan_x = mx - (mx - self._pan_x) * (self._zoom / old_zoom)
        self._pan_y = my - (my - self._pan_y) * (self._zoom / old_zoom)
        self.update()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self._last_pos = event.pos()

    def mouseMoveEvent(self, event):
        if self._dragging and self._last_pos is not None:
            dx = event.pos().x() - self._last_pos.x()
            dy = event.pos().y() - self._last_pos.y()
            self._pan_x += dx
            self._pan_y += dy
            self._last_pos = event.pos()
            self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = False
            self._last_pos = None

    def reset_view(self):
        self._zoom = 1.0
        self._pan_x = 0.0
        self._pan_y = 0.0
        self.update()

    def paintEvent(self, event):
        P = self.total.get('P', 0.0)
        Q = self.total.get('Q', 0.0)
        S = self.total.get('S', 1.0)
        fp = self.total.get('fp', 0.0)

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), self._color('bg', '#1a1a2e'))

        width = self.width()
        height = self.height()

        if S < 1e-12:
            painter.setPen(QPen(self._color('text_dim', '#aaaaaa'), 1))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                             "No power data")
            return

        base_scale = (width * 0.55) / S
        scale = base_scale * self._zoom
        ox = int(width * 0.15) + int(self._pan_x)

        q_values = [Q]
        if self._correction is not None:
            q_values.append(self._correction.get('Q_new', Q))
        has_up = any(q > 1e-12 for q in q_values)
        has_down = any(q < -1e-12 for q in q_values)

        if has_up and has_down:
            oy = int(height * 0.5) + int(self._pan_y)
        elif has_down:
            oy = int(height * 0.35) + int(self._pan_y)
        else:
            oy = int(height * 0.65) + int(self._pan_y)

        px = int(P * scale)
        qy = int(-Q * scale)

        pen_p = QPen(self._color('voltage', '#e74c3c'), 3)
        pen_q = QPen(self._color('current', '#3498db'), 3)
        pen_s = QPen(self._color('wire', '#2ecc71'), 3)
        pen_ax = QPen(self._color('panel_brd', '#444466'), 1)
        pen_c = QPen(self._color('comp_sel', '#f39c12'), 2, Qt.PenStyle.DashLine)

        font_lbl = QFont('Menlo', 9, QFont.Weight.Bold)
        font_ax = QFont('Menlo', 8)
        painter.setFont(font_lbl)

        def arrow(painter, pen, x1, y1, x2, y2, label='', lside='end'):
            painter.setPen(pen)
            painter.drawLine(x1, y1, x2, y2)
            dx = x2 - x1
            dy = y2 - y1
            length = math.sqrt(dx * dx + dy * dy)
            if length < 1:
                return
            ux = dx / length
            uy = dy / length
            pxn = -uy
            pyn = ux
            size = 8
            tip = QPolygonF([
                QPointF(x2, y2),
                QPointF(x2 - size * ux + size * 0.4 * pxn,
                        y2 - size * uy + size * 0.4 * pyn),
                QPointF(x2 - size * ux - size * 0.4 * pxn,
                        y2 - size * uy - size * 0.4 * pyn),
            ])
            painter.setBrush(QColor(pen.color()))
            painter.drawPolygon(tip)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            if label:
                painter.setPen(QPen(pen.color(), 1))
                if lside == 'end':
                    painter.drawText(int(x2) + 6, int(y2) + 5, label)
                else:
                    mx = (x1 + x2) // 2
                    my = (y1 + y2) // 2
                    painter.drawText(mx + 6, my - 4, label)

        ax_len = max(abs(px), abs(qy), 60) + 50
        painter.setFont(font_ax)

        arrow(painter, pen_ax, ox - 20, oy, ox + ax_len, oy, '', 'end')
        painter.setPen(QPen(self._color('text_dim', '#666688'), 1))
        painter.drawText(ox + ax_len + 4, oy + 4, self.tr("P (W)"))

        arrow(painter, pen_ax, ox, oy + 20, ox, oy - ax_len, '', 'end')
        painter.drawText(ox + 4, oy - ax_len - 4, self.tr("Q+ inductive"))
        painter.drawText(ox + 4, oy + 28, self.tr("Q− capacitive"))

        painter.setPen(QPen(self._color('grid_line', '#333355'), 1, Qt.PenStyle.DotLine))
        painter.drawLine(ox, oy, ox, oy + ax_len)

        painter.setFont(font_lbl)
        arrow(painter, pen_p, ox, oy, ox + px, oy, f"P = {P:.2f} W", 'end')
        arrow(painter, pen_q, ox + px, oy, ox + px, oy + qy,
              f"Q = {Q:.2f} VAR", 'end')
        arrow(painter, pen_s, ox, oy, ox + px, oy + qy,
              f"S = {S:.4f} VA", 'mid')

        phi_rad = math.atan2(Q, P)
        phi_deg = math.degrees(phi_rad)
        painter.setPen(QPen(self._color('comp_sel', '#f1c40f'), 1))
        r_arc = 40
        painter.drawArc(ox - r_arc, oy - r_arc, 2 * r_arc, 2 * r_arc,
                        0, int(phi_deg * 16))
        label_y = oy - 14 if Q >= 0 else oy + 22
        fp_type = {
            'inductive': self.tr('inductive'),
            'capacitive': self.tr('capacitive'),
            'unity': self.tr('unity'),
        }.get(self.total.get('fp_type', ''), self.total.get('fp_type', ''))
        painter.drawText(ox + r_arc + 4, label_y,
                         f"φ = {phi_deg:+.1f}°  fp={fp:.3f}  "
                         f"({fp_type})")

        if self._correction:
            q_new = self._correction.get('Q_new', Q)
            qy_new = int(-q_new * scale)
            arrow(painter, pen_c, ox, oy, ox + px, oy + qy_new,
                  f"S' (fp={self._correction['fp_new']:.3f})", 'mid')
            painter.setPen(QPen(self._color('comp_sel', '#f39c12'), 1, Qt.PenStyle.DashLine))
            painter.drawLine(ox + px, oy + qy, ox + px, oy + qy_new)
            painter.setPen(QPen(self._color('comp_sel', '#f39c12'), 1))
            painter.drawText(ox + px + 8, (oy + qy + oy + qy_new) // 2,
                             f"ΔQ={abs(Q-q_new):.2f} VAR")
