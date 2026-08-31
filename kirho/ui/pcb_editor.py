"""Editor PCB interno, deliberadamente pequeño: footprints y ratsnest."""
from __future__ import annotations

import math
from copy import deepcopy

from PyQt6.QtCore import QEvent, QPointF, QRectF, QLineF, Qt, pyqtSignal
from PyQt6.QtGui import (
    QBrush, QColor, QFont, QFontMetricsF, QPainter, QPainterPath,
    QPainterPathStroker, QPen,
)
from PyQt6.QtWidgets import (
    QGraphicsEllipseItem, QGraphicsItem, QGraphicsLineItem, QGraphicsObject,
    QGraphicsRectItem, QGraphicsPathItem, QGraphicsScene, QGraphicsView,
    QHBoxLayout, QLabel, QPushButton,
    QStyle, QVBoxLayout, QWidget,
)

from kirho.pcb import PcbBoard, PcbFootprint, PcbTrack, PcbVia, build_pcb_board
from kirho.ui.style import COLORS


def _color(name: str, fallback: str) -> QColor:
    return QColor(COLORS.get(name, fallback))


def _layer_color(layer: str) -> QColor:
    return QColor('#ef4444' if layer == 'F.Cu' else '#4aa3ff')


class PcbScene(QGraphicsScene):
    """Escena PCB con cuadrícula y captura del modo de área."""

    def __init__(self, editor):
        super().__init__(editor)
        self.editor = editor
        self.unit = 'mm'
        self.grid_step_mm = 1.0

    def set_unit(self, unit: str):
        if unit in ('mm', 'in'):
            self.unit = unit
            self.grid_step_mm = 1.0 if unit == 'mm' else 2.54
            self.update()

    def drawBackground(self, painter: QPainter, rect: QRectF):
        painter.fillRect(rect, QBrush(_color('bg', '#15202b')))
        step = self.grid_step_mm
        first_x = math.floor(rect.left() / step)
        last_x = math.ceil(rect.right() / step)
        first_y = math.floor(rect.top() / step)
        last_y = math.ceil(rect.bottom() / step)
        minor_color = _color('grid_line', '#334155')
        minor_color.setAlpha(90)
        major_color = _color('grid_line', '#64748b')
        major_color.setAlpha(150)
        minor_pen = QPen(minor_color, 0)
        major_pen = QPen(major_color, 0)
        minor_pen.setCosmetic(True)
        major_pen.setCosmetic(True)

        for index in range(first_x, last_x + 1):
            painter.setPen(major_pen if index % 10 == 0 else minor_pen)
            x = index * step
            painter.drawLine(QLineF(x, rect.top(), x, rect.bottom()))
        for index in range(first_y, last_y + 1):
            painter.setPen(major_pen if index % 10 == 0 else minor_pen)
            y = index * step
            painter.drawLine(QLineF(rect.left(), y, rect.right(), y))

    def mousePressEvent(self, event):
        if self.editor.area_mode:
            self.editor._start_area(event.scenePos())
            event.accept()
            return
        if self.editor.route_mode:
            if event.button() == Qt.MouseButton.LeftButton:
                self.editor._route_click(event.scenePos())
            elif event.button() == Qt.MouseButton.RightButton:
                self.editor.cancel_route()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.editor.area_mode:
            self.editor._update_area_preview(event.scenePos())
            event.accept()
            return
        if self.editor.route_mode:
            self.editor._update_route_preview(event.scenePos())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self.editor.area_mode:
            self.editor._finish_area(event.scenePos())
            event.accept()
            return
        if self.editor.route_mode:
            event.accept()
            return
        super().mouseReleaseEvent(event)


class PcbView(QGraphicsView):
    """Vista PCB con zoom de rueda, Ctrl/⌘+rueda y pinch de trackpad."""

    _MIN_ZOOM = 0.2
    _MAX_ZOOM = 100.0

    def __init__(self, scene):
        super().__init__(scene)
        self._pan_last = None

    def _pan_by(self, delta):
        self.horizontalScrollBar().setValue(
            self.horizontalScrollBar().value() - round(delta.x()))
        self.verticalScrollBar().setValue(
            self.verticalScrollBar().value() - round(delta.y()))

    def _zoom_at(self, factor, position):
        current = self.transform().m11()
        factor = max(self._MIN_ZOOM / current,
                     min(factor, self._MAX_ZOOM / current))
        if factor == 1:
            return
        scene_pos = self.mapToScene(position.toPoint())
        self.scale(factor, factor)
        moved_pos = self.mapFromScene(scene_pos)
        delta = moved_pos - position.toPoint()
        self.horizontalScrollBar().setValue(
            self.horizontalScrollBar().value() + delta.x())
        self.verticalScrollBar().setValue(
            self.verticalScrollBar().value() + delta.y())

    def wheelEvent(self, event):
        modifiers = (Qt.KeyboardModifier.ControlModifier
                     | Qt.KeyboardModifier.MetaModifier)
        if event.modifiers() & modifiers:
            delta = event.angleDelta().y() or event.pixelDelta().y()
            if delta:
                self._zoom_at(1.15 ** (delta / 120), event.position())
                event.accept()
                return
        elif event.pixelDelta().isNull():
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
        if (event.type() == QEvent.Type.NativeGesture
                and event.gestureType() == Qt.NativeGestureType.PanNativeGesture):
            self._pan_by(event.delta())
            event.accept()
            return True
        return super().viewportEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.MiddleButton:
            self._pan_last = event.position()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._pan_last is not None:
            self._pan_by(event.position() - self._pan_last)
            self._pan_last = event.position()
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.MiddleButton:
            self._pan_last = None
            self.unsetCursor()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event):
        editor = self.scene().editor
        if editor.route_mode and event.key() == Qt.Key.Key_V:
            editor.place_via()
            event.accept()
            return
        if event.key() == Qt.Key.Key_Escape and editor.route_mode:
            editor.cancel_route()
            event.accept()
            return
        direction = {
            Qt.Key.Key_Left: (-1, 0),
            Qt.Key.Key_Right: (1, 0),
            Qt.Key.Key_Up: (0, -1),
            Qt.Key.Key_Down: (0, 1),
        }.get(event.key())
        if direction is not None and self.scene().editor.nudge_selected(*direction):
            event.accept()
            return
        super().keyPressEvent(event)


class PcbBoardAreaItem(QGraphicsRectItem):
    """Contorno definido por el usuario, sin relleno ni tamaño automático."""

    _HANDLE_SIZE = 2.4
    _MIN_SIZE = 2.0

    def __init__(self, outline, editor):
        x, y, width, height = outline
        super().__init__(0.0, 0.0, width, height)
        self.editor = editor
        self._resize_handle = None
        self._resize_start_pos = QPointF()
        self._resize_start_rect = QRectF()
        self._resize_start_scene_pos = QPointF()
        self._snap_ready = False
        self.setPos(x, y)
        self._snap_ready = True
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges)
        self.setPen(QPen(_color('component', '#65d6a0'), 0.6))
        self.setBrush(QBrush(Qt.BrushStyle.NoBrush))
        self.setZValue(-2)

    def _handle_centers(self):
        rect = self.rect()
        center = rect.center()
        return {
            'nw': rect.topLeft(),
            'n': QPointF(center.x(), rect.top()),
            'ne': rect.topRight(),
            'e': QPointF(rect.right(), center.y()),
            'se': rect.bottomRight(),
            's': QPointF(center.x(), rect.bottom()),
            'sw': rect.bottomLeft(),
            'w': QPointF(rect.left(), center.y()),
        }

    def _handle_rects(self):
        half = self._HANDLE_SIZE / 2
        return [QRectF(center.x() - half, center.y() - half,
                       self._HANDLE_SIZE, self._HANDLE_SIZE)
                for center in self._handle_centers().values()]

    def _handle_at(self, point):
        if not self.isSelected():
            return None
        for name, center in self._handle_centers().items():
            if QRectF(center.x() - self._HANDLE_SIZE / 2,
                      center.y() - self._HANDLE_SIZE / 2,
                      self._HANDLE_SIZE, self._HANDLE_SIZE).contains(point):
                return name
        return None

    def boundingRect(self):
        half = self._HANDLE_SIZE / 2 + 1.0
        return self.rect().adjusted(-half, -half, half, half)

    def shape(self):
        border = QPainterPath()
        border.addRect(self.rect())
        stroker = QPainterPathStroker()
        stroker.setWidth(max(1.4, self.pen().widthF() + 1.0))
        result = stroker.createStroke(border)
        if self.isSelected():
            for handle_rect in self._handle_rects():
                result.addRect(handle_rect)
        return result

    def paint(self, painter, option, widget=None):
        painter.setPen(self.pen())
        painter.setBrush(QBrush(Qt.BrushStyle.NoBrush))
        painter.drawRect(self.rect())
        if self.isSelected():
            painter.setPen(QPen(_color('component', '#65d6a0'), 0.4))
            painter.setBrush(QBrush(_color('bg', '#15202b')))
            for handle_rect in self._handle_rects():
                painter.drawRect(handle_rect)

    def mousePressEvent(self, event):
        handle = self._handle_at(event.pos())
        if handle is not None:
            self._resize_handle = handle
            self._resize_start_pos = self.pos()
            self._resize_start_rect = self.rect()
            self._resize_start_scene_pos = self.editor.snap_position(
                event.scenePos())
            self.editor._push_undo()
            event.accept()
            return
        self.editor._push_undo()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._resize_handle is None:
            super().mouseMoveEvent(event)
            return

        delta = (self.editor.snap_position(event.scenePos())
                 - self._resize_start_scene_pos)
        start = self._resize_start_rect
        width = start.width()
        height = start.height()
        position = QPointF(self._resize_start_pos)
        handle = self._resize_handle
        if 'w' in handle:
            dx = min(delta.x(), start.width() - self._MIN_SIZE)
            position.setX(self._resize_start_pos.x() + dx)
            width = start.width() - dx
        elif 'e' in handle:
            width = max(self._MIN_SIZE, start.width() + delta.x())
        if 'n' in handle:
            dy = min(delta.y(), start.height() - self._MIN_SIZE)
            position.setY(self._resize_start_pos.y() + dy)
            height = start.height() - dy
        elif 's' in handle:
            height = max(self._MIN_SIZE, start.height() + delta.y())

        self.setPos(position)
        self.setRect(0.0, 0.0, width, height)
        self.editor._set_outline(
            (position.x(), position.y(), width, height), emit=False)
        self.update()
        event.accept()

    def mouseReleaseEvent(self, event):
        if self._resize_handle is not None:
            self._resize_handle = None
            self.editor._set_outline(
                (self.pos().x(), self.pos().y(), self.rect().width(),
                 self.rect().height()), emit=True)
            self.update()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def itemChange(self, change, value):
        if (change == QGraphicsItem.GraphicsItemChange.ItemPositionChange
                and getattr(self, '_snap_ready', False)):
            return self.editor.snap_position(value)
        result = super().itemChange(change, value)
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged:
            rect = self.rect()
            self.editor._set_outline(
                (value.x(), value.y(), rect.width(), rect.height()),
                emit=self._resize_handle is None)
        elif change == QGraphicsItem.GraphicsItemChange.ItemSelectedHasChanged:
            self.update()
        return result


class PcbTrackItem(QGraphicsPathItem):
    def __init__(self, track: PcbTrack):
        path = QPainterPath()
        if track.points:
            path.moveTo(QPointF(*track.points[0]))
            for point in track.points[1:]:
                path.lineTo(QPointF(*point))
        super().__init__(path)
        pen = QPen(_layer_color(track.layer), max(0.1, track.width_mm))
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        self.setPen(pen)
        self.setZValue(-0.5)


class PcbViaItem(QGraphicsEllipseItem):
    def __init__(self, via: PcbVia):
        diameter = max(0.1, via.diameter_mm)
        super().__init__(via.x_mm - diameter / 2,
                         via.y_mm - diameter / 2,
                         diameter, diameter)
        self.via = via
        self.setZValue(-0.4)

    def paint(self, painter, option, widget=None):
        rect = self.rect()
        color = QColor('#c084fc')
        painter.setPen(QPen(color, 0.25))
        painter.setBrush(QBrush(color))
        painter.drawEllipse(rect)
        drill = min(rect.width(), max(0.1, self.via.drill_mm))
        hole = rect.adjusted((rect.width() - drill) / 2,
                             (rect.height() - drill) / 2,
                             -(rect.width() - drill) / 2,
                             -(rect.height() - drill) / 2)
        painter.setPen(QPen(_color('bg', '#15202b'), 0.15))
        painter.setBrush(QBrush(_color('bg', '#15202b')))
        painter.drawEllipse(hole)


class PcbFootprintItem(QGraphicsObject):
    def __init__(self, footprint: PcbFootprint, editor):
        super().__init__()
        self.footprint = footprint
        self.editor = editor
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges)
        self.setPos(footprint.x_mm, footprint.y_mm)
        self.setRotation(footprint.angle)
        self._snap_ready = True

    def mousePressEvent(self, event):
        self.editor._push_undo()
        super().mousePressEvent(event)

    def _label_rects(self):
        body = QRectF(
            -self.footprint.width_mm / 2,
            -self.footprint.height_mm / 2,
            self.footprint.width_mm,
            self.footprint.height_mm,
        )
        labels = (
            (self.footprint.reference, QFont('Menlo', 3),
             body.top() - 4.0),
            (self.footprint.value, QFont('Menlo', 2),
             body.bottom() + 0.5),
        )
        rects = []
        for text, font, top in labels:
            metrics = QFontMetricsF(font)
            width = max(body.width(), metrics.horizontalAdvance(text) + 1.0)
            height = max(1.0, metrics.height())
            rects.append(QRectF(-width / 2, top, width, height))
        return rects

    def boundingRect(self) -> QRectF:
        rect = QRectF(
            -self.footprint.width_mm / 2 - 5.0,
            -self.footprint.height_mm / 2 - 5.0,
            self.footprint.width_mm + 10.0,
            self.footprint.height_mm + 10.0,
        )
        for label_rect in self._label_rects():
            rect = rect.united(label_rect)
        return rect

    def paint(self, painter: QPainter, option, widget=None):
        body = QRectF(
            -self.footprint.width_mm / 2,
            -self.footprint.height_mm / 2,
            self.footprint.width_mm,
            self.footprint.height_mm,
        )
        selected = bool(option.state & QStyleState.State_Selected)
        courtyard_margin = max(0.0, self.footprint.courtyard_margin_mm)
        if courtyard_margin:
            courtyard = body.adjusted(-courtyard_margin, -courtyard_margin,
                                      courtyard_margin, courtyard_margin)
            courtyard_pen = QPen(_color('wire', '#70a5ff'), 0.2,
                                 Qt.PenStyle.DashLine)
            painter.setPen(courtyard_pen)
            painter.setBrush(QBrush(Qt.BrushStyle.NoBrush))
            painter.drawRect(courtyard)

        painter.setPen(QPen(_color('component', '#e6a23c') if selected
                            else _color('panel_brd', '#6b7280'), 0.35))
        painter.setBrush(QBrush(_color('comp_body', '#263238')))
        painter.drawRoundedRect(body, 1.2, 1.2)

        for pad in self.footprint.pads:
            width = max(0.1, pad.width_mm)
            height = max(0.1, pad.height_mm)
            rect = QRectF(pad.x_mm - width / 2, pad.y_mm - height / 2,
                          width, height)
            painter.setBrush(QBrush(_color('current', '#f1c40f')))
            painter.setPen(QPen(_color('text', '#ffffff'), 0.25))
            shape = pad.shape.lower()
            if shape == 'circle':
                painter.drawEllipse(rect)
            elif shape == 'oval':
                radius = min(width, height) / 2
                painter.drawRoundedRect(rect, radius, radius)
            elif shape in ('roundrect', 'rounded_rect'):
                radius = min(width, height) * 0.25
                painter.drawRoundedRect(rect, radius, radius)
            else:
                painter.drawRect(rect)

            drill = max(0.0, pad.drill_mm)
            if drill:
                hole = QRectF(pad.x_mm - drill / 2, pad.y_mm - drill / 2,
                              drill, drill)
                painter.setBrush(QBrush(_color('comp_body', '#263238')))
                painter.setPen(QPen(_color('bg', '#15202b'), 0.2))
                painter.drawEllipse(hole)

        painter.setPen(QPen(_color('text', '#ffffff'), 0.25))
        for label_rect, text, font in zip(
                self._label_rects(),
                (self.footprint.reference, self.footprint.value),
                (QFont('Menlo', 3), QFont('Menlo', 2))):
            painter.setFont(font)
            painter.drawText(label_rect, Qt.AlignmentFlag.AlignCenter, text)

    def itemChange(self, change, value):
        if (change == QGraphicsItem.GraphicsItemChange.ItemPositionChange
                and getattr(self, '_snap_ready', False)):
            return self.editor.snap_position(value)
        result = super().itemChange(change, value)
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged:
            self.footprint.x_mm = value.x()
            self.footprint.y_mm = value.y()
            if self.scene() is not None:
                self.editor.refresh_ratsnest()
                self.editor.update_scene_rect()
        return result


# Alias to keep the paint method readable.
QStyleState = QStyle.StateFlag


class PcbEditorWidget(QWidget):
    board_changed = pyqtSignal(object)
    footprint_selected = pyqtSignal(object)
    route_mode_changed = pyqtSignal(bool)
    layer_changed = pyqtSignal(str)
    route_status = pyqtSignal(str)
    _clipboard: list[dict] | None = None

    def __init__(self, source_scene=None, board: PcbBoard | None = None, parent=None):
        super().__init__(parent)
        self.source_scene = source_scene
        self.board = board if board is not None else build_pcb_board(source_scene)
        self._ratsnest: list[QGraphicsLineItem] = []
        self._footprint_items: dict[str, PcbFootprintItem] = {}
        self._area_item: PcbBoardAreaItem | None = None
        self.area_mode = False
        self._area_start: QPointF | None = None
        self._area_preview: QGraphicsRectItem | None = None
        self.unit = 'mm'
        self._undo_stack: list[dict] = []
        self._redo_stack: list[dict] = []
        self.snap_enabled = True
        self.route_mode = False
        self.active_layer = 'F.Cu'
        self.track_width_mm = 0.25
        self._route_net = None
        self._route_points = []
        self._route_cursor = None
        self._route_preview = None

        self.setWindowTitle(self.tr('PCB Editor — Kirho'))
        self.resize(1000, 700)
        root = QVBoxLayout(self)

        self.info = QLabel()
        self.info.setWordWrap(True)
        root.addWidget(self.info)

        self.scene = PcbScene(self)
        self.scene.selectionChanged.connect(self._emit_footprint_selection)
        self.view = PcbView(self.scene)
        self.view.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.view.setDragMode(QGraphicsView.DragMode.RubberBandDrag)
        self.view.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        root.addWidget(self.view, 1)

        buttons = QHBoxLayout()
        regenerate = QPushButton(self.tr('Regenerate from schematic'))
        regenerate.clicked.connect(self._regenerate)
        regenerate.setEnabled(source_scene is not None)
        buttons.addWidget(regenerate)
        buttons.addStretch()
        root.addLayout(buttons)
        self._draw_board()

    def _push_undo(self):
        self._undo_stack.append(self.board.to_dict())
        self._undo_stack = self._undo_stack[-50:]
        self._redo_stack.clear()

    def _restore(self, snapshot):
        board = PcbBoard.from_dict(snapshot)
        if board is None:
            return False
        self.board = board
        self.board_changed.emit(self.board)
        self._draw_board()
        return True

    def undo(self):
        if not self._undo_stack:
            return False
        self._redo_stack.append(self.board.to_dict())
        return self._restore(self._undo_stack.pop())

    def redo(self):
        if not self._redo_stack:
            return False
        self._undo_stack.append(self.board.to_dict())
        return self._restore(self._redo_stack.pop())

    def _selected_footprints(self):
        return [item.footprint for item in self.scene.selectedItems()
                if isinstance(item, PcbFootprintItem)]

    def _emit_footprint_selection(self):
        selected = self._selected_footprints()
        self.footprint_selected.emit(selected[0] if len(selected) == 1 else None)

    def copy_selected(self):
        selected = self._selected_footprints()
        if not selected:
            return False
        payload = PcbBoard(footprints=selected).to_dict()['footprints']
        type(self)._clipboard = deepcopy(payload)
        return True

    def cut_selected(self):
        if not self.copy_selected():
            return False
        self._push_undo()
        selected = {id(footprint) for footprint in self._selected_footprints()}
        self.board.footprints = [fp for fp in self.board.footprints
                                 if id(fp) not in selected]
        self._draw_board()
        self.board_changed.emit(self.board)
        return True

    def paste(self):
        if not type(self)._clipboard:
            return False
        pasted = PcbBoard.from_dict({'footprints': type(self)._clipboard})
        if pasted is None:
            return False
        self._push_undo()
        existing = {fp.reference for fp in self.board.footprints}
        for footprint in pasted.footprints:
            base = f'{footprint.reference}_copy'
            reference = base
            suffix = 2
            while reference in existing:
                reference = f'{base}{suffix}'
                suffix += 1
            footprint.reference = reference
            footprint.x_mm += 5.0
            footprint.y_mm += 5.0
            existing.add(reference)
            self.board.footprints.append(footprint)
        self._draw_board()
        self.board_changed.emit(self.board)
        return True

    def duplicate_selected(self):
        return self.copy_selected() and self.paste()

    def rotate_selected(self, delta=90):
        items = [item for item in self.scene.selectedItems()
                 if isinstance(item, PcbFootprintItem)]
        if not items:
            return False
        self._push_undo()
        for item in items:
            item.footprint.angle = (item.footprint.angle + delta) % 360
            item.setRotation(item.footprint.angle)
        self.update_scene_rect()
        self.board_changed.emit(self.board)
        return True

    def snap_position(self, position):
        if not self.snap_enabled:
            return position
        step = self.scene.grid_step_mm
        return QPointF(round(position.x() / step) * step,
                       round(position.y() / step) * step)

    def set_snap_enabled(self, enabled: bool):
        self.snap_enabled = bool(enabled)

    def nudge_selected(self, dx: int, dy: int):
        items = [item for item in self.scene.selectedItems()
                 if isinstance(item, PcbFootprintItem)]
        if not items:
            return False
        step = self.scene.grid_step_mm
        self._push_undo()
        for item in items:
            item.setPos(item.pos() + QPointF(dx * step, dy * step))
        self.board_changed.emit(self.board)
        return True

    def set_active_layer(self, layer: str):
        if layer in ('F.Cu', 'B.Cu') and layer != self.active_layer:
            self.active_layer = layer
            self.layer_changed.emit(layer)

    def set_track_width(self, width_mm: float):
        self.track_width_mm = max(0.1, float(width_mm))

    def toggle_route_layer(self):
        self.set_active_layer('B.Cu' if self.active_layer == 'F.Cu'
                              else 'F.Cu')
        self.route_status.emit(
            self.tr('Switched to layer {layer}').format(
                layer=self.active_layer))

    def set_route_mode(self, enabled: bool):
        enabled = bool(enabled)
        if not enabled:
            self.cancel_route()
        if self.route_mode != enabled:
            self.route_mode = enabled
            self.route_mode_changed.emit(enabled)
        self.route_status.emit(
            self.tr('Route track: click a pad to start') if enabled
            else self.tr('Track routing cancelled'))

    def _pad_supports_active_layer(self, pad):
        return self.active_layer in pad.layers

    def _pad_at(self, position):
        tolerance = max(0.75, self.scene.grid_step_mm * 0.75)
        closest = None
        closest_distance = float('inf')
        for item in self._footprint_items.values():
            for pad in item.footprint.pads:
                pad_position = item.mapToScene(QPointF(pad.x_mm, pad.y_mm))
                distance = math.hypot(
                    position.x() - pad_position.x(),
                    position.y() - pad_position.y())
                pad_tolerance = max(
                    tolerance, max(pad.width_mm, pad.height_mm) / 2 + 0.5)
                if distance <= pad_tolerance and distance < closest_distance:
                    closest = (item, pad, pad_position)
                    closest_distance = distance
        return closest

    def _clear_route_preview(self):
        if self._route_preview is not None:
            self.scene.removeItem(self._route_preview)
            self._route_preview = None

    def _update_route_preview(self, position):
        if self._route_net is None or not self._route_points:
            return
        pad_hit = self._pad_at(position)
        if pad_hit is not None and pad_hit[1].net == self._route_net:
            cursor = pad_hit[2]
        else:
            cursor = self.snap_position(position)
        self._route_cursor = cursor
        self._clear_route_preview()
        path = QPainterPath(self._route_points[0])
        for point in self._route_points[1:]:
            path.lineTo(point)
        path.lineTo(cursor)
        preview = QGraphicsPathItem(path)
        pen = QPen(_layer_color(self.active_layer), self.track_width_mm,
                   Qt.PenStyle.DashLine)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        preview.setPen(pen)
        preview.setZValue(1)
        self.scene.addItem(preview)
        self._route_preview = preview

    def _route_click(self, position):
        pad_hit = self._pad_at(position)
        if self._route_net is None:
            if pad_hit is None:
                self.route_status.emit(self.tr('Start a track on a pad'))
                return
            _, pad, pad_position = pad_hit
            if not pad.net or pad.net == '?':
                self.route_status.emit(
                    self.tr('This pad has no known net to route'))
                return
            if not self._pad_supports_active_layer(pad):
                self.route_status.emit(
                    self.tr('This pad is not available on {layer}').format(
                        layer=self.active_layer))
                return
            self._route_net = pad.net
            self._route_points = [pad_position]
            self._route_cursor = pad_position
            self._update_route_preview(position)
            self.route_status.emit(
                self.tr('Routing net {net}; click corners or the target pad').format(
                    net=pad.net))
            return

        if pad_hit is not None:
            _, pad, pad_position = pad_hit
            if pad.net != self._route_net:
                self.route_status.emit(
                    self.tr('Target pad belongs to another net'))
                return
            if not self._pad_supports_active_layer(pad):
                self.route_status.emit(
                    self.tr('This pad is not available on {layer}').format(
                        layer=self.active_layer))
                return
            if pad_position == self._route_points[0]:
                return
            if pad_position != self._route_points[-1]:
                self._route_points.append(pad_position)
            if len(self._route_points) >= 2:
                self._clear_route_preview()
                self._push_undo()
                self.board.tracks.append(PcbTrack(
                    net=self._route_net,
                    layer=self.active_layer,
                    width_mm=self.track_width_mm,
                    points=[(point.x(), point.y())
                            for point in self._route_points],
                ))
                self._draw_board(fit=False)
                self.board_changed.emit(self.board)
                self.cancel_route(announce=False)
                self.route_status.emit(self.tr('Track completed'))
            return

        point = self.snap_position(position)
        if point != self._route_points[-1]:
            self._route_points.append(point)
        self._update_route_preview(point)

    def place_via(self):
        if not self.route_mode or self._route_net is None:
            return False
        if len(self._route_points) < 2:
            self.route_status.emit(
                self.tr('Add a track segment before placing a via'))
            return False
        point = self._route_points[-1]
        self._push_undo()
        self.board.tracks.append(PcbTrack(
            net=self._route_net,
            layer=self.active_layer,
            width_mm=self.track_width_mm,
            points=[(route_point.x(), route_point.y())
                    for route_point in self._route_points],
        ))
        self.board.vias.append(PcbVia(
            x_mm=point.x(), y_mm=point.y(), net=self._route_net))
        self._route_points = [point]
        self._clear_route_preview()
        self._draw_board(fit=False)
        self.board_changed.emit(self.board)
        self.toggle_route_layer()
        self._update_route_preview(point)
        return True

    def cancel_route(self, announce=True):
        had_route = bool(self._route_points)
        self._clear_route_preview()
        self._route_net = None
        self._route_points = []
        self._route_cursor = None
        if had_route and announce:
            self.route_status.emit(self.tr('Track routing cancelled'))

    def _draw_board(self, fit=True):
        self.scene.clear()
        self._ratsnest.clear()
        self._footprint_items.clear()
        self._area_item = None

        if self.board.outline is not None:
            self._area_item = PcbBoardAreaItem(self.board.outline, self)
            self.scene.addItem(self._area_item)

        for footprint in self.board.footprints:
            item = PcbFootprintItem(footprint, self)
            self._footprint_items[footprint.reference] = item
            self.scene.addItem(item)

        for via in self.board.vias:
            self.scene.addItem(PcbViaItem(via))

        for track in self.board.tracks:
            if len(track.points) >= 2:
                self.scene.addItem(PcbTrackItem(track))

        self.update_scene_rect()
        self.refresh_ratsnest()
        if fit:
            self.view.fitInView(
                self.scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)
        self._update_info()

    def update_scene_rect(self):
        rects = []
        if self.board.outline is not None:
            x, y, width, height = self.board.outline
            rects.append(QRectF(x - 10.0, y - 10.0,
                                width + 20.0, height + 20.0))

        for footprint in self.board.footprints:
            angle = math.radians(footprint.angle % 180)
            half_width = (abs(math.cos(angle)) * footprint.width_mm
                          + abs(math.sin(angle)) * footprint.height_mm) / 2
            half_height = (abs(math.sin(angle)) * footprint.width_mm
                           + abs(math.cos(angle)) * footprint.height_mm) / 2
            rects.append(QRectF(
                footprint.x_mm - half_width - 20.0,
                footprint.y_mm - half_height - 20.0,
                2 * half_width + 40.0,
                2 * half_height + 40.0,
            ))

        for track in self.board.tracks:
            for x, y in track.points:
                rects.append(QRectF(x - 20.0, y - 20.0, 40.0, 40.0))

        for via in self.board.vias:
            rects.append(QRectF(via.x_mm - 20.0, via.y_mm - 20.0,
                                40.0, 40.0))

        scene_rect = rects[0] if rects else QRectF(-50.0, -40.0, 100.0, 80.0)
        for rect in rects[1:]:
            scene_rect = scene_rect.united(rect)
        self.scene.setSceneRect(scene_rect)

    def refresh_ratsnest(self):
        for line in self._ratsnest:
            self.scene.removeItem(line)
        self._ratsnest.clear()
        pen = QPen(_color('wire', '#70a5ff'), 0.25, Qt.PenStyle.DashLine)
        for pads in self.board.pads_by_net().values():
            if len(pads) < 2:
                continue
            first_footprint, first_pad = pads[0]
            first_item = self._footprint_items.get(first_footprint.reference)
            if first_item is None:
                continue
            start = first_item.mapToScene(QPointF(first_pad.x_mm, first_pad.y_mm))
            for footprint, pad in pads[1:]:
                item = self._footprint_items.get(footprint.reference)
                if item is None:
                    continue
                end = item.mapToScene(QPointF(pad.x_mm, pad.y_mm))
                line = QGraphicsLineItem(QLineF(start, end))
                line.setPen(pen)
                line.setZValue(-1)
                self.scene.addItem(line)
                self._ratsnest.append(line)

    def _update_info(self):
        if self.board.outline is None:
            text = self.tr('{count} footprint(s) · PCB area not assigned').format(
                count=len(self.board.footprints))
        else:
            _, _, width, height = self.board.outline
            text = self.tr('{count} footprint(s) · PCB area {width} × {height}').format(
                count=len(self.board.footprints),
                width=self._format_length(width),
                height=self._format_length(height),
            )
        text += self.tr(' · Grid: {unit}').format(unit=self.unit)
        if self.board.warnings:
            text += '\n' + self.tr('Warnings: ') + ' '.join(self.board.warnings)
        self.info.setText(text)

    def _format_length(self, value_mm: float) -> str:
        if self.unit == 'in':
            return f'{value_mm / 25.4:.3f} in'
        return f'{value_mm:.2f} mm'

    def set_unit(self, unit: str):
        if unit not in ('mm', 'in'):
            return
        self.unit = unit
        self.scene.set_unit(unit)
        self._update_info()

    def set_area_mode(self, enabled: bool):
        if enabled and self.route_mode:
            self.set_route_mode(False)
        self.area_mode = bool(enabled)
        self.view.setDragMode(
            QGraphicsView.DragMode.NoDrag if self.area_mode
            else QGraphicsView.DragMode.RubberBandDrag)
        if not self.area_mode and self._area_preview is not None:
            self.scene.removeItem(self._area_preview)
            self._area_preview = None

    def _start_area(self, point: QPointF):
        point = self.snap_position(point)
        self._area_start = point
        self._area_preview = QGraphicsRectItem(QRectF(point, point))
        pen = QPen(_color('component', '#65d6a0'), 0.6, Qt.PenStyle.DashLine)
        self._area_preview.setPen(pen)
        self._area_preview.setBrush(QBrush(Qt.BrushStyle.NoBrush))
        self._area_preview.setZValue(-1)
        self.scene.addItem(self._area_preview)

    def _update_area_preview(self, point: QPointF):
        if self._area_preview is not None and self._area_start is not None:
            point = self.snap_position(point)
            self._area_preview.setRect(QRectF(self._area_start, point).normalized())

    def _finish_area(self, point: QPointF):
        if self._area_start is None:
            return
        point = self.snap_position(point)
        rect = QRectF(self._area_start, point).normalized()
        if self._area_preview is not None:
            self.scene.removeItem(self._area_preview)
            self._area_preview = None
        self._area_start = None
        if rect.width() < 2.0 or rect.height() < 2.0:
            return
        self.area_mode = False
        self.view.setDragMode(QGraphicsView.DragMode.RubberBandDrag)
        self._push_undo()
        self._set_outline((rect.x(), rect.y(), rect.width(), rect.height()), emit=True)
        self._draw_board()

    def _set_outline(self, outline, emit=False):
        self.board.outline = tuple(float(value) for value in outline)
        self.update_scene_rect()
        if emit:
            self.board_changed.emit(self.board)

    def _regenerate(self):
        if self.source_scene is None:
            return
        self._push_undo()
        outline = self.board.outline
        placements = {
            footprint.reference: (
                footprint.x_mm, footprint.y_mm,
                footprint.angle, footprint.side,
            )
            for footprint in self.board.footprints
        }
        self.board = build_pcb_board(self.source_scene)
        self.board.outline = outline
        for footprint in self.board.footprints:
            placement = placements.get(footprint.reference)
            if placement is not None:
                (footprint.x_mm, footprint.y_mm,
                 footprint.angle, footprint.side) = placement
        self.board_changed.emit(self.board)
        self._draw_board()
