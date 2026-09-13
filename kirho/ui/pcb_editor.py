"""Editor PCB interno, deliberadamente pequeño: footprints y ratsnest."""
from __future__ import annotations

import math
from copy import deepcopy
from heapq import heappop, heappush

from PyQt6.QtCore import QEvent, QPointF, QRectF, QLineF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import (
    QBrush, QColor, QFont, QPainter, QPainterPath,
    QPainterPathStroker, QPen, QTransform,
)
from PyQt6.QtWidgets import (
    QGraphicsEllipseItem, QGraphicsItem, QGraphicsLineItem, QGraphicsObject,
    QGraphicsRectItem, QGraphicsPathItem, QGraphicsScene, QGraphicsView,
    QHBoxLayout, QLabel, QPushButton, QInputDialog, QCheckBox, QComboBox,
    QColorDialog, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout,
    QLineEdit, QListWidget, QPlainTextEdit,
    QStyle, QTabWidget, QToolBar, QVBoxLayout, QWidget,
)

from kirho.pcb import (
    LAYER_COLORS, PcbBoard, PcbFootprint, PcbPad, PcbText, PcbTrack, PcbVia,
    build_pcb_board, expanded_path, pad_shape, stroke_path, subtract_path, via_shape,
    footprint_names_for_type, resolve_footprint, _silk_box,
)
from kirho.ui.style import COLORS, format_si_value


def _color(name: str, fallback: str) -> QColor:
    return QColor(COLORS.get(name, fallback))


def _layer_color(layer: str, editor=None) -> QColor:
    color = next((entry.color for entry in editor.board.layers
                  if entry.name == layer), '') if editor is not None else ''
    return QColor(color if QColor(color).isValid()
                  else LAYER_COLORS.get(layer, '#e9eee6'))


def _text_path(text, size_mm):
    font = QFont('Arial')
    font.setPixelSize(100)
    path = QPainterPath()
    path.addText(QPointF(), font, text)
    height = path.boundingRect().height()
    return QTransform.fromScale(size_mm / height, size_mm / height).map(path) \
        if height else path


def _paint_copper(painter, path, color, outline=False):
    if outline:
        painter.setPen(QPen(color, 0))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(path)
    else:
        painter.fillPath(path, color)


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
        painter.fillRect(rect, QColor('#101318'))
        if self.editor.board.outline is not None:
            painter.fillRect(QRectF(*self.editor.board.outline), QColor('#192127'))
        step = self.grid_step_mm
        while step * abs(painter.worldTransform().m11()) < 12:
            step *= 2
        first_x = math.floor(rect.left() / step)
        last_x = math.ceil(rect.right() / step)
        first_y = math.floor(rect.top() / step)
        last_y = math.ceil(rect.bottom() / step)
        painter.setPen(QPen(QColor('#39424b'), 0))
        painter.drawPoints([QPointF(x * step, y * step)
                            for x in range(first_x, last_x + 1)
                            for y in range(first_y, last_y + 1)])

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

    def mouseDoubleClickEvent(self, event):
        if not self.editor.route_mode and not self.editor.area_mode:
            item = next((item for item in self.items(event.scenePos())
                         if isinstance(item, (PcbFootprintItem, PcbPadItem,
                                              PcbTrackItem, PcbViaItem, PcbTextItem))), None)
            if item is not None:
                self.editor.edit_properties(item)
                event.accept()
                return
        super().mouseDoubleClickEvent(event)


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
        current = abs(self.transform().m11())
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
        if event.key() == Qt.Key.Key_F and editor.flip_selected():
            event.accept()
            return
        if event.key() == Qt.Key.Key_R and editor.rotate_selected():
            event.accept()
            return
        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            if editor.delete_selected():
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
        self.setPen(QPen(_layer_color('Edge.Cuts', editor), 0.15))
        self.setBrush(QBrush(Qt.BrushStyle.NoBrush))
        self.setZValue(3)
        self.setVisible(editor.is_layer_visible('Edge.Cuts'))

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
        painter.setBrush(self.brush())
        painter.drawRect(self.rect())
        if self.isSelected():
            painter.setPen(QPen(_color('component', '#f59e0b'), 0.4))
            painter.setBrush(QBrush(Qt.BrushStyle.NoBrush))
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
    def __init__(self, track: PcbTrack, editor=None):
        self.track = track
        self.editor = editor
        path = QPainterPath()
        if track.points:
            path.moveTo(QPointF(*track.points[0]))
            for point in track.points[1:]:
                path.lineTo(QPointF(*point))
        super().__init__(path)
        pen = QPen(_layer_color(track.layer, editor), max(0.1, track.width_mm))
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        self.setPen(pen)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
        self.setZValue(1 if track.layer == 'F.Cu' else 0)
        self.setToolTip(f'{track.net} · {track.layer} · {track.width_mm:g} mm')
        if editor is not None:
            self.setVisible(editor.is_layer_visible(track.layer))

    def shape(self):
        stroker = QPainterPathStroker()
        stroker.setWidth(max(1.5, self.track.width_mm + 1.0))
        return stroker.createStroke(self.path())

    def boundingRect(self):
        return self.shape().boundingRect()

    def paint(self, painter, option, widget=None):
        if self.isSelected() or self.track.net == getattr(self.editor, 'highlight_net', None):
            pen = QPen(QColor('#f2ce72') if self.isSelected() else QColor('#76e3c6'),
                       max(0.1, self.track.width_mm) + 0.35)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            painter.drawPath(self.path())
        _paint_copper(painter, stroke_path(self.track.points, self.track.width_mm),
                      _layer_color(self.track.layer, self.editor),
                      getattr(self.editor, 'outline_copper', False))
        if self.isSelected():
            painter.setBrush(QColor('#f2ce72'))
            for point in self.track.points:
                painter.drawEllipse(QPointF(*point), 0.22, 0.22)


class PcbViaItem(QGraphicsEllipseItem):
    def __init__(self, via: PcbVia, editor=None):
        diameter = max(0.1, via.diameter_mm)
        super().__init__(via.x_mm - diameter / 2,
                         via.y_mm - diameter / 2,
                         diameter, diameter)
        self.via = via
        self.editor = editor
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
        self.setZValue(2.5)
        self.setToolTip(f'{via.net} · {" ↔ ".join(via.layers)}\n'
                        f'Ø {via.diameter_mm:g} / drill {via.drill_mm:g} mm')
        if editor is not None:
            self.setVisible(any(editor.is_layer_visible(layer)
                                for layer in via.layers))

    def paint(self, painter, option, widget=None):
        rect = self.rect()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor('#0d141d'))
        painter.drawEllipse(rect)
        painter.save()
        painter.setClipPath(via_shape(self.via))
        visible = [layer for layer in self.via.layers
                   if self.editor is None or self.editor.is_layer_visible(layer)]
        for index, layer in enumerate(visible):
            painter.setBrush(_layer_color(layer, self.editor))
            painter.drawPie(rect, index * 360 * 16 // len(visible),
                            360 * 16 // len(visible))
        painter.restore()
        if self.isSelected() or self.via.net == getattr(self.editor, 'highlight_net', None):
            painter.setPen(QPen(QColor('#f2ce72'), 0.15))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(rect.adjusted(-0.15, -0.15, 0.15, 0.15))


class PcbPadItem(QGraphicsPathItem):
    def __init__(self, pad, parent):
        super().__init__(pad_shape(pad, drilled=False), parent)
        self.pad = pad
        self.editor = parent.editor
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
        self.setPen(QPen(Qt.PenStyle.NoPen))
        self.setZValue(0.5)
        self.setToolTip(f'{parent.footprint.reference}.{pad.number} · {pad.net}')
        self.sync_visibility()

    def copper_layers(self):
        return self.parentItem().footprint.pad_layers(self.pad)

    def sync_visibility(self):
        self.setVisible(any(self.editor.is_layer_visible(layer)
                            or self.editor.is_layer_visible(layer.replace('.Cu', '.Mask'))
                            for layer in self.copper_layers()))
        self.update()

    def paint(self, painter, option, widget=None):
        visible = [layer for layer in self.copper_layers()
                   if self.editor.is_layer_visible(layer)]
        painter.setPen(Qt.PenStyle.NoPen)
        if visible:
            layer = self.editor.active_layer if self.editor.active_layer in visible else visible[0]
            _paint_copper(painter, pad_shape(self.pad), _layer_color(layer, self.editor),
                          self.editor.outline_copper)
        for layer in self.copper_layers():
            mask = layer.replace('.Cu', '.Mask')
            if self.editor.is_layer_visible(mask):
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.setPen(QPen(_layer_color(mask, self.editor), 0.08))
                painter.drawPath(self.path())
        if visible and self.pad.drill_mm > 0:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor('#0d141d'))
            painter.drawEllipse(QPointF(self.pad.x_mm, self.pad.y_mm),
                                self.pad.drill_mm / 2, self.pad.drill_mm / 2)
        if visible and self.editor.show_pad_numbers:
            self._paint_number(painter)
        if self.isSelected() or self.pad.net == self.editor.highlight_net:
            painter.setPen(QPen(QColor('#f2ce72') if self.isSelected()
                                else QColor('#76e3c6'), 0.16))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(self.path())

    def _paint_number(self, painter):
        size = (self.pad.drill_mm * 0.65 if self.pad.drill_mm else
                min(self.pad.width_mm, self.pad.height_mm) * 0.65)
        transform = painter.worldTransform()
        scale = math.hypot(transform.m11(), transform.m12())
        if size * scale < 7:
            return  # Keep unreadable annotations out of the zoomed-out view.
        path = _text_path(str(self.pad.number), size)
        bounds = path.boundingRect()
        available = (self.pad.drill_mm if self.pad.drill_mm else self.pad.width_mm) * 0.8
        if bounds.width() > available:
            factor = available / bounds.width()
            path = QTransform.fromScale(factor, factor).map(path)
            bounds = path.boundingRect()
        center = transform.map(QPointF(self.pad.x_mm, self.pad.y_mm))
        painter.save()
        # Pad identifiers are editor annotations, readable from either board side.
        painter.resetTransform()
        painter.translate(center)
        painter.scale(scale, scale)
        painter.translate(-bounds.center())
        painter.fillPath(path, QColor('#e9eee6') if self.pad.drill_mm
                         or self.editor.outline_copper else QColor('#151b22'))
        painter.restore()


class PcbFootprintItem(QGraphicsObject):
    def __init__(self, footprint: PcbFootprint, editor):
        super().__init__()
        self.footprint = footprint
        self.editor = editor
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges)
        self.setPos(footprint.x_mm, footprint.y_mm)
        self.sync_transform()
        self._snap_ready = True
        self.setZValue(2)
        self.pad_items = [PcbPadItem(pad, self) for pad in footprint.pads]
        self.silkscreen_path = QPainterPath()
        self.silkscreen_path.setFillRule(Qt.FillRule.WindingFill)
        for graphic in footprint.silkscreen:
            self.silkscreen_path.addPath(graphic.path())
        for pad in footprint.pads:
            self.silkscreen_path = subtract_path(self.silkscreen_path,
                                                 expanded_path(pad_shape(pad, False), 0.2))
        self.setToolTip(f'{footprint.reference} · {footprint.footprint_name}\n'
                        'R: rotate · F: flip · double click: properties')

    def silk_layer(self):
        return self.footprint.side.replace('.Cu', '.SilkS')

    def sync_transform(self):
        transform = QTransform().rotate(self.footprint.angle)
        if self.footprint.side == 'B.Cu':
            transform.scale(-1, 1)
        self.setTransform(transform)

    def mousePressEvent(self, event):
        self.editor._push_undo()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        self.editor.board_changed.emit(self.editor.board)
        self.editor._emit_selection()

    def _body_rect(self):
        rect = QRectF()
        for graphic in self.footprint.silkscreen:
            rect = rect.united(graphic.path().boundingRect())
        for pad in self.footprint.pads:
            rect = rect.united(pad_shape(pad, False).boundingRect())
        return rect if not rect.isNull() else QRectF(
            -self.footprint.width_mm / 2, -self.footprint.height_mm / 2,
            self.footprint.width_mm, self.footprint.height_mm)

    def _display_value(self):
        value = self.footprint.value
        # Legacy files retain their saved value; only the annotation is formatted.
        try:
            number, unit = value.split(maxsplit=1)
            if self.footprint.component_type in ('R', 'C', 'L', 'POT'):
                return format_si_value(float(number), unit)
            if unit in ('A', 'V', ''):
                float(number)
                return self.footprint.component_type
        except ValueError:
            pass
        return value

    def _label_paths(self):
        body = self._body_rect()
        for pad in self.footprint.pads:
            body = body.united(pad_shape(pad, drilled=False).boundingRect())
        paths = []
        for text, size, top in (
                (self.footprint.reference, 1.0, body.top() - 1.65),
                (self._display_value(), 0.7, body.bottom() + 0.65)):
            path = _text_path(text, size)
            rect = path.boundingRect()
            paths.append(QTransform().translate(-rect.center().x(),
                                                top - rect.top()).map(path))
        return paths

    def _label_rects(self):
        return [path.boundingRect() for path in self._label_paths()]

    def boundingRect(self) -> QRectF:
        rect = self._body_rect().adjusted(-0.5, -0.5, 0.5, 0.5)
        for label_rect in self._label_rects():
            rect = rect.united(label_rect)
        for pad in self.footprint.pads:
            rect = rect.united(pad_shape(pad, drilled=False).boundingRect())
        return rect.adjusted(-0.2, -0.2, 0.2, 0.2)

    def paint(self, painter: QPainter, option, widget=None):
        body = self._body_rect()
        selected = bool(option.state & QStyleState.State_Selected)
        courtyard_margin = max(0.0, self.footprint.courtyard_margin_mm)
        if courtyard_margin and selected:
            courtyard = body.adjusted(-courtyard_margin, -courtyard_margin,
                                      courtyard_margin, courtyard_margin)
            courtyard_pen = QPen(_color('wire', '#70a5ff'), 0.2,
                                 Qt.PenStyle.DashLine)
            painter.setPen(courtyard_pen)
            painter.setBrush(QBrush(Qt.BrushStyle.NoBrush))
            painter.drawRect(courtyard)

        if self.editor.is_layer_visible(self.silk_layer()):
            silk = _layer_color(self.silk_layer(), self.editor)
            painter.fillPath(self.silkscreen_path, silk)
            for index, path in enumerate(self._label_paths()):
                if index == 0 or self.editor.show_values:
                    color = QColor(silk)
                    if index == 1:
                        color.setAlpha(165)
                    painter.fillPath(path, color)

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
                self.editor._update_info()
        return result


class PcbTextItem(QGraphicsObject):
    def __init__(self, text: PcbText, editor):
        super().__init__()
        self.text_object = text
        self.editor = editor
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges)
        self.setPos(text.x_mm, text.y_mm)
        transform = QTransform().rotate(text.angle)
        if text.layer.startswith('B.'):
            transform.scale(-1, 1)
        self.setTransform(transform)
        self._snap_ready = True
        self.setZValue(3)
        self.setVisible(editor.is_layer_visible(text.layer))

    def boundingRect(self):
        return _text_path(self.text_object.text, self.text_object.size_mm).boundingRect()

    def paint(self, painter, option, widget=None):
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        painter.fillPath(_text_path(self.text_object.text, self.text_object.size_mm),
                         QColor('#f2ce72') if selected
                         else _layer_color(self.text_object.layer, self.editor))

    def mousePressEvent(self, event):
        self.editor._push_undo()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        self.editor.board_changed.emit(self.editor.board)
        self.editor._emit_selection()

    def itemChange(self, change, value):
        if (change == QGraphicsItem.GraphicsItemChange.ItemPositionChange
                and getattr(self, '_snap_ready', False)):
            return self.editor.snap_position(value)
        result = super().itemChange(change, value)
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged:
            self.text_object.x_mm = value.x()
            self.text_object.y_mm = value.y()
            if self.scene() is not None:
                self.editor.update_scene_rect()
        return result


# Alias to keep the paint method readable.
QStyleState = QStyle.StateFlag


class PcbEditorWidget(QWidget):
    board_changed = pyqtSignal(object)
    footprint_selected = pyqtSignal(object)
    object_selected = pyqtSignal(object)
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
        self.layer_visibility = {
            layer.name: layer.enabled for layer in self.board.layers
        }
        self.layer_visibility.setdefault('F.Cu', True)
        self.layer_visibility.setdefault('B.Cu', True)
        self.track_width_mm = 0.25
        self.highlight_net = None
        self.ratsnest_visible = True
        self.show_values = True
        self.show_pad_numbers = True
        self.outline_copper = False
        self._route_net = None
        self._route_points = []
        self._route_cursor = None
        self._route_preview = None
        self._drc_markers = []
        self.violations = []

        self.setWindowTitle(self.tr('PCB Editor — Kirho'))
        self.resize(1000, 700)
        root = QVBoxLayout(self)

        self.info = QLabel()
        self.info.setWordWrap(True)
        root.addWidget(self.info)

        self.scene = PcbScene(self)
        self.scene.selectionChanged.connect(self._emit_selection)
        self.view = PcbView(self.scene)
        self.view.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.view.setDragMode(QGraphicsView.DragMode.RubberBandDrag)
        self.view.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        canvas = self.canvas_layout = QHBoxLayout()
        canvas.addWidget(self.view, 1)
        panel = self.sidebar = QTabWidget()
        panel.setMinimumWidth(230)
        panel.setMaximumWidth(340)
        layers_page, view_page, drc_page = QWidget(), QWidget(), QWidget()
        panel.addTab(layers_page, self.tr('Layers'))
        panel.addTab(view_page, self.tr('View'))
        panel.addTab(drc_page, self.tr('DRC'))
        controls = QVBoxLayout(layers_page)
        controls.addWidget(QLabel(self.tr('Active layer')))
        self.layer_combo = QComboBox()
        self.layer_combo.addItems([layer.name for layer in self.board.layers])
        self.layer_combo.currentTextChanged.connect(self.set_active_layer)
        controls.addWidget(self.layer_combo)
        self.layer_checks = {}
        self.layer_color_buttons = {}
        for layer in self.board.layers:
            row = QHBoxLayout()
            check = QCheckBox(layer.name)
            check.setChecked(layer.enabled)
            check.toggled.connect(lambda visible, name=layer.name:
                                  self.set_layer_visible(name, visible))
            self.layer_checks[layer.name] = check
            row.addWidget(check, 1)
            color = QPushButton()
            color.setFixedSize(25, 20)
            color.setToolTip(self.tr('Layer color: ') + layer.name)
            color.clicked.connect(lambda checked=False, name=layer.name:
                                  self.choose_layer_color(name))
            self.layer_color_buttons[layer.name] = color
            row.addWidget(color)
            controls.addLayout(row)
        controls.addStretch()
        controls = QVBoxLayout(view_page)
        controls.addWidget(QLabel(self.tr('Grid step (mm)')))
        self.grid_spin = QDoubleSpinBox()
        self.grid_spin.setDecimals(3)
        self.grid_spin.setRange(0.05, 10)
        self.grid_spin.setSingleStep(0.25)
        self.grid_spin.setValue(self.scene.grid_step_mm)
        self.grid_spin.valueChanged.connect(self.set_grid_step)
        controls.addWidget(self.grid_spin)
        self.ratsnest_check = QCheckBox(self.tr('Ratsnest'))
        self.ratsnest_check.setChecked(True)
        self.ratsnest_check.toggled.connect(self.set_ratsnest_visible)
        controls.addWidget(self.ratsnest_check)
        mirror = QCheckBox(self.tr('View from back'))
        mirror.toggled.connect(lambda enabled: self.view.scale(-1, 1))
        controls.addWidget(mirror)
        self.display_checks = {}
        for label, attr in ((self.tr('Pad numbers'), 'show_pad_numbers'),
                            (self.tr('Component values'), 'show_values'),
                            (self.tr('Copper outlines'), 'outline_copper')):
            check = QCheckBox(label)
            check.setChecked(getattr(self, attr))
            check.toggled.connect(lambda enabled, name=attr: self.set_display_option(name, enabled))
            self.display_checks[attr] = check
            controls.addWidget(check)
        fit = QPushButton(self.tr('Fit board'))
        fit.clicked.connect(self.fit_board)
        controls.addWidget(fit)
        properties = QPushButton(self.tr('Properties…'))
        properties.clicked.connect(lambda: self.edit_properties())
        controls.addWidget(properties)
        controls.addStretch()
        controls = QVBoxLayout(drc_page)
        drc = QPushButton(self.tr('Run basic DRC'))
        drc.clicked.connect(self.run_drc)
        controls.addWidget(drc)
        self.drc_summary = QLabel(self.tr('DRC not run'))
        self.drc_summary.setWordWrap(True)
        controls.addWidget(self.drc_summary)
        self.drc_list = QListWidget()
        self.drc_list.setWordWrap(True)
        self.drc_list.currentRowChanged.connect(self.select_violation)
        controls.addWidget(self.drc_list)
        self.board_warnings = QPlainTextEdit()
        self.board_warnings.setReadOnly(True)
        self.board_warnings.setMaximumHeight(130)
        controls.addWidget(self.board_warnings)
        canvas.addWidget(panel)
        root.addLayout(canvas, 1)

        self.edit_toolbar = QToolBar(self.tr('PCB editing'))
        self.edit_toolbar.setMovable(False)
        if parent is None and source_scene is not None:
            self.edit_toolbar.addAction(self.tr('Regenerate'), self._regenerate)
        for title, callback in (
                ('Select', lambda: (self.set_route_mode(False), self.set_area_mode(False))),
                ('Rotate', self.rotate_selected), ('Flip', self.flip_selected),
                ('Duplicate', self.duplicate_selected), ('Delete', self.delete_selected),
                ('Text', self.add_silkscreen_text), ('Autoroute', self.autoroute),
                ('Fit board', self.fit_board)):
            action = self.edit_toolbar.addAction(self.tr(title))
            action.triggered.connect(lambda checked=False, fn=callback: fn())
        root.insertWidget(0, self.edit_toolbar)
        self.board_changed.connect(self._invalidate_drc)
        self._draw_board()

    def showEvent(self, event):
        super().showEvent(event)
        if not getattr(self, '_fitted_on_show', False):
            self._fitted_on_show = True
            QTimer.singleShot(0, self.fit_board)

    def set_display_option(self, name, enabled):
        if name not in ('show_pad_numbers', 'show_values', 'outline_copper'):
            raise ValueError('Unknown display option')
        setattr(self, name, bool(enabled))
        check = self.display_checks[name]
        check.blockSignals(True)
        check.setChecked(enabled)
        check.blockSignals(False)
        self.scene.update()

    def fit_board(self):
        self.view.fitInView(self.scene.itemsBoundingRect().adjusted(-3, -3, 3, 3),
                            Qt.AspectRatioMode.KeepAspectRatio)

    def _push_undo(self):
        self._undo_stack.append(self.board.to_dict())
        self._undo_stack = self._undo_stack[-50:]
        self._redo_stack.clear()

    def apply_properties(self, obj, values):
        """Aplica una edición atómica y deshacible; también usada por las pruebas."""
        objects = (self.board.footprints + self.board.tracks + self.board.vias
                   + self.board.texts + [pad for fp in self.board.footprints for pad in fp.pads])
        if not any(current is obj for current in objects):
            raise ValueError('Object is no longer in this board; select it again')
        footprint_changed = (isinstance(obj, PcbFootprint)
                             and values.get('footprint_name', obj.footprint_name) != obj.footprint_name)
        if footprint_changed:
            name = values['footprint_name']
            if name not in footprint_names_for_type(obj.component_type):
                raise ValueError('Incompatible footprint')
            spec = resolve_footprint(obj.component_type, name)
            nets = {pad.number: pad.net for pad in obj.pads}
            if set(nets) != set(range(1, len(spec.pads) + 1)):
                raise ValueError('Pad numbers do not match; map pins before replacing')
            values = dict(values, width_mm=spec.width_mm, height_mm=spec.height_mm,
                          silkscreen=list(spec.silkscreen) or [_silk_box(spec.width_mm, spec.height_mm)], pads=[
                              PcbPad(index, net=nets[index], **vars(pad))
                              for index, pad in enumerate(spec.pads, 1)])
        candidate = deepcopy(obj)
        for name, value in values.items():
            if not hasattr(candidate, name):
                raise ValueError(f'Unknown property: {name}')
            if isinstance(value, (int, float)) and not math.isfinite(value):
                raise ValueError('Coordinates and dimensions must be finite')
            setattr(candidate, name, value)
        for name in ('width_mm', 'height_mm', 'diameter_mm', 'size_mm'):
            if hasattr(candidate, name) and getattr(candidate, name) <= 0:
                raise ValueError('Dimensions must be positive')
        if isinstance(candidate, PcbFootprint) and candidate.side not in ('F.Cu', 'B.Cu'):
            raise ValueError('Invalid footprint side')
        if isinstance(candidate, PcbPad):
            if (candidate.drill_mm < 0
                    or candidate.drill_mm >= min(candidate.width_mm, candidate.height_mm)
                    or (candidate.pad_type == 'tht'
                        and candidate.drill_mm < self.board.rules.min_drill_mm)
                    or (candidate.pad_type == 'smd' and candidate.drill_mm != 0)):
                raise ValueError('Invalid pad drill / annular ring')
        if isinstance(candidate, PcbTrack):
            if (len(candidate.points) < 2
                    or any(len(point) != 2 or not all(math.isfinite(v) for v in point)
                           for point in candidate.points)
                    or stroke_path(candidate.points, candidate.width_mm).isEmpty()):
                raise ValueError('A track needs at least two distinct finite vertices')
        if isinstance(candidate, (PcbTrack, PcbVia)):
            error = self.board.routing_error(candidate)
            if error:
                raise ValueError(error)
        self.cancel_route(announce=False)
        self._push_undo()
        for name, value in values.items():
            setattr(obj, name, value)
        if footprint_changed:
            warning = self.tr('Footprint changed: verify terminal mapping and run DRC; copper has not moved.')
            if warning not in self.board.warnings:
                self.board.warnings.append(warning)
        self._draw_board(fit=False)
        self.board_changed.emit(self.board)
        for item in self.scene.items():
            if any(getattr(item, attr, None) is obj for attr in
                   ('footprint', 'pad', 'track', 'via', 'text_object')):
                item.setSelected(True)
                break
        return True

    def property_names(self, obj):
        if isinstance(obj, PcbFootprint):
            return ('footprint_name', 'x_mm', 'y_mm', 'angle', 'value', 'side')
        if isinstance(obj, PcbPad):
            return ('x_mm', 'y_mm', 'width_mm', 'height_mm', 'drill_mm', 'shape')
        if isinstance(obj, PcbTrack):
            return ('width_mm', 'layer', 'points')
        if isinstance(obj, PcbVia):
            return ('x_mm', 'y_mm', 'diameter_mm', 'drill_mm')
        return ('text', 'x_mm', 'y_mm', 'angle', 'size_mm', 'layer')

    def property_field(self, obj, name):
        value = getattr(obj, name)
        if name in ('side', 'layer', 'shape', 'footprint_name'):
            field = QComboBox()
            field.addItems(list(footprint_names_for_type(obj.component_type))
                           if name == 'footprint_name' else
                           ['circle', 'rect', 'oval', 'roundrect'] if name == 'shape'
                           else ['F.Cu', 'B.Cu'] if name == 'side' or isinstance(obj, PcbTrack)
                           else list(self.layer_visibility))
            if field.findText(value) < 0:
                field.addItem(value)
            field.setCurrentText(value)
            if name == 'footprint_name':
                field.setToolTip(self.tr('Verify numbered terminals and dimensions. Copper is not moved. '
                                        'Regeneration uses the schematic footprint assignment.'))
        elif name == 'points':
            field = QPlainTextEdit('\n'.join(f'{x:g}, {y:g}' for x, y in value))
            field.setToolTip(self.tr('One vertex per line: X, Y (mm)'))
        elif isinstance(value, (int, float)):
            field = QDoubleSpinBox()
            field.setDecimals(4)
            field.setRange(-100000, 100000)
            field.setValue(value)
            field.setKeyboardTracking(False)
            field.setSuffix(' mm' if name.endswith('_mm') else '°' if name == 'angle' else '')
        else:
            field = QLineEdit(value)
        field.setObjectName(name)
        return field

    def edit_properties(self, item=None):
        selected = self._selected_objects()
        item = item or (selected[0] if len(selected) == 1 else None)
        if item is None:
            self.route_status.emit(self.tr('Select one object to edit'))
            return False
        obj = next(getattr(item, name) for name in (
            'footprint', 'pad', 'track', 'via', 'text_object') if hasattr(item, name))
        dialog = QDialog(self)
        dialog.setWindowTitle(self.tr('PCB properties · mm'))
        layout = QFormLayout(dialog)
        fields = {}
        if hasattr(obj, 'net'):
            layout.addRow(self.tr('Net (from schematic)'), QLabel(obj.net))
        if isinstance(obj, PcbFootprint):
            layout.addRow(self.tr('Footprint'), QLabel(f'{obj.reference} · {obj.footprint_name}'))
        elif isinstance(obj, PcbPad):
            layout.addRow(self.tr('Pad number'), QLabel(str(obj.number)))
        for name in self.property_names(obj):
            field = self.property_field(obj, name)
            fields[name] = field
            field.setObjectName(name)
            layout.addRow(name.replace('_mm', ' (mm)').replace('_', ' '), field)
        error_label = QLabel()
        error_label.setWordWrap(True)
        layout.addRow(error_label)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                                   QDialogButtonBox.StandardButton.Cancel)

        def accept():
            try:
                values = {}
                for name, field in fields.items():
                    if isinstance(field, QDoubleSpinBox):
                        values[name] = field.value()
                    elif isinstance(field, QComboBox):
                        values[name] = field.currentText()
                    elif isinstance(field, QPlainTextEdit):
                        values[name] = [tuple(float(v.strip()) for v in line.split(','))
                                        for line in field.toPlainText().splitlines() if line.strip()]
                    else:
                        values[name] = field.text()
                self.apply_properties(obj, values)
            except (ValueError, TypeError) as exc:
                error_label.setText(str(exc))
                return
            dialog.accept()

        buttons.accepted.connect(accept)
        buttons.rejected.connect(dialog.reject)
        layout.addRow(buttons)
        return dialog.exec() == QDialog.DialogCode.Accepted

    def _invalidate_drc(self, board=None):
        for marker in self._drc_markers:
            self.scene.removeItem(marker)
        self._drc_markers.clear()
        self.violations.clear()
        self.drc_list.clear()
        self.drc_summary.setText(self.tr('DRC needs update'))

    def run_drc(self):
        self._invalidate_drc()
        self.violations = self.board.check_drc()
        for violation in self.violations:
            self.drc_list.addItem(f'{violation.code}: {violation.message}')
        errors = sum(v.severity == 'error' for v in self.violations)
        self.drc_summary.setText(self.tr('{errors} errors · {warnings} warnings').format(
            errors=errors, warnings=len(self.violations) - errors))
        return self.violations

    def select_violation(self, index):
        for marker in self._drc_markers:
            self.scene.removeItem(marker)
        self._drc_markers.clear()
        if not 0 <= index < len(self.violations):
            return
        self.scene.clearSelection()
        targets = {id(obj) for obj in self.violations[index].objects}
        rect = QRectF()
        for item in self.scene.items():
            if any(id(getattr(item, name, None)) in targets
                   for name in ('pad', 'track', 'via')):
                item.setSelected(True)
                bounds = item.sceneBoundingRect().adjusted(-0.4, -0.4, 0.4, 0.4)
                marker = self.scene.addRect(bounds, QPen(QColor('#ff5470'), 0.15))
                marker.setZValue(5)
                marker.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
                self._drc_markers.append(marker)
                rect = rect.united(bounds)
        if not rect.isNull():
            self.view.ensureVisible(rect, 30, 30)

    def _restore(self, snapshot):
        board = PcbBoard.from_dict(snapshot)
        if board is None:
            return False
        self.cancel_route(announce=False)
        self.board = board
        self.layer_visibility = {
            layer.name: layer.enabled for layer in self.board.layers
        }
        self.layer_visibility.setdefault('F.Cu', True)
        self.layer_visibility.setdefault('B.Cu', True)
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

    def _selected_objects(self):
        return [item for item in self.scene.selectedItems()
                if isinstance(item, (PcbFootprintItem, PcbTrackItem,
                                     PcbViaItem, PcbTextItem, PcbPadItem))]

    def _emit_selection(self):
        selected = self._selected_objects()
        footprint = (selected[0].footprint
                     if len(selected) == 1
                     and isinstance(selected[0], PcbFootprintItem)
                     else None)
        selected_object = None
        if len(selected) == 1:
            item = selected[0]
            for attribute in ('footprint', 'track', 'via', 'text_object', 'pad'):
                selected_object = getattr(item, attribute, None)
                if selected_object is not None:
                    break
        self.footprint_selected.emit(footprint)
        self.object_selected.emit(selected_object)
        self.set_highlight_net(
            getattr(selected_object, 'net', None)
            if isinstance(selected_object, (PcbTrack, PcbVia, PcbPad)) else None)

    def set_highlight_net(self, net):
        self.highlight_net = net or None
        for item in self._footprint_items.values():
            item.update()
            for pad_item in item.pad_items:
                pad_item.update()
        for item in self.scene.items():
            if isinstance(item, (PcbTrackItem, PcbViaItem)):
                item.update()

    def delete_selected(self):
        selected = [item for item in self._selected_objects()
                    if not isinstance(item, PcbPadItem)]
        if not selected:
            return False
        self._push_undo()
        footprints = [item.footprint for item in selected
                      if isinstance(item, PcbFootprintItem)]
        tracks = {id(item.track) for item in selected
                  if isinstance(item, PcbTrackItem)}
        vias = {id(item.via) for item in selected
                if isinstance(item, PcbViaItem)}
        texts = {id(item.text_object) for item in selected
                 if isinstance(item, PcbTextItem)}
        footprint_ids = {id(footprint) for footprint in footprints}
        self.board.footprints = [
            footprint for footprint in self.board.footprints
            if id(footprint) not in footprint_ids
        ]
        self.board.tracks = [
            track for track in self.board.tracks
            if id(track) not in tracks
        ]
        self.board.vias = [
            via for via in self.board.vias
            if id(via) not in vias
        ]
        self.board.texts = [
            text for text in self.board.texts
            if id(text) not in texts
        ]
        self._draw_board(fit=False)
        self.board_changed.emit(self.board)
        return True

    def add_silkscreen_text(self):
        text, accepted = QInputDialog.getText(
            self, self.tr('Add silkscreen text'), self.tr('Text:'))
        text = text.strip()
        if not accepted or not text:
            return False
        outline = self.board.outline or (0.0, 0.0,
                                         self.board.width_mm,
                                         self.board.height_mm)
        x, y, width, height = outline
        self._push_undo()
        layer = self.active_layer if self.active_layer.endswith('SilkS') else 'F.SilkS'
        self.board.texts.append(PcbText(text, x + width / 2, y + height / 2, layer))
        self._draw_board(fit=False)
        self.board_changed.emit(self.board)
        return True

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
            item.sync_transform()
        self.update_scene_rect()
        self.refresh_ratsnest()
        self._update_info()
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
        if self._route_net is not None and layer != self.active_layer:
            self.route_status.emit(self.tr('Use V to change copper layer through a via'))
            self.layer_changed.emit(self.active_layer)
            self._sync_layer_controls()
            return False
        if layer in self.layer_visibility and layer != self.active_layer:
            if self.route_mode and layer not in ('F.Cu', 'B.Cu'):
                self.set_route_mode(False)
            self.active_layer = layer
            self.layer_changed.emit(layer)
            self._sync_layer_controls()
            self.scene.update()
            self._update_info()
        return True

    def is_layer_visible(self, layer: str) -> bool:
        return self.layer_visibility.get(layer, True)

    def set_layer_visible(self, layer: str, visible: bool):
        if layer not in self.layer_visibility:
            return
        visible = bool(visible)
        self.layer_visibility[layer] = visible
        for board_layer in self.board.layers:
            if board_layer.name == layer:
                board_layer.enabled = visible
                break
        if not visible and layer == self.active_layer:
            self.cancel_route()
        self._apply_layer_visibility()
        self._sync_layer_controls()
        self.board_changed.emit(self.board)

    def _apply_layer_visibility(self):
        for item in self.scene.items():
            if isinstance(item, PcbTrackItem):
                item.setVisible(self.is_layer_visible(item.track.layer))
            elif isinstance(item, PcbViaItem):
                item.setVisible(any(self.is_layer_visible(name)
                                    for name in item.via.layers))
            elif isinstance(item, PcbFootprintItem):
                item.setVisible(
                    self.is_layer_visible(item.silk_layer())
                    or any(self.is_layer_visible(layer)
                           or self.is_layer_visible(layer.replace('.Cu', '.Mask'))
                           for pad in item.footprint.pads
                           for layer in item.footprint.pad_layers(pad)))
                item.update()
            elif isinstance(item, PcbPadItem):
                item.sync_visibility()
            elif isinstance(item, PcbTextItem):
                item.setVisible(self.is_layer_visible(item.text_object.layer))
            elif isinstance(item, PcbBoardAreaItem):
                item.setVisible(self.is_layer_visible('Edge.Cuts'))
        self.refresh_ratsnest()

    def _sync_layer_controls(self):
        self.layer_combo.blockSignals(True)
        self.layer_combo.setCurrentText(self.active_layer)
        self.layer_combo.blockSignals(False)
        for name, check in self.layer_checks.items():
            check.blockSignals(True)
            check.setChecked(self.is_layer_visible(name))
            check.blockSignals(False)
            self.layer_color_buttons[name].setStyleSheet(
                f'background: {_layer_color(name, self).name()}; border: 0;')

    def choose_layer_color(self, layer):
        color = QColorDialog.getColor(_layer_color(layer, self), self)
        if color.isValid():
            self._push_undo()
            next(entry for entry in self.board.layers if entry.name == layer).color = color.name()
            self._sync_layer_controls()
            if self._area_item is not None:
                self._area_item.setPen(QPen(_layer_color('Edge.Cuts', self), 0.15))
            self.scene.update()
            self.board_changed.emit(self.board)

    def set_grid_step(self, step):
        if math.isfinite(step) and 0.05 <= step <= 10:
            self.scene.grid_step_mm = step
            self.grid_spin.blockSignals(True)
            self.grid_spin.setValue(step)
            self.grid_spin.blockSignals(False)
            self.scene.update()
            self._update_info()

    def flip_selected(self):
        items = [item for item in self.scene.selectedItems()
                 if isinstance(item, PcbFootprintItem)]
        if not items:
            return False
        self._push_undo()
        for item in items:
            item.footprint.side = ('B.Cu' if item.footprint.side == 'F.Cu'
                                   else 'F.Cu')
            item.sync_transform()
        self._apply_layer_visibility()
        self._update_info()
        self.board_changed.emit(self.board)
        self.route_status.emit(self.tr('Selected footprint side changed'))
        return True

    def set_track_width(self, width_mm: float):
        width_mm = float(width_mm)
        if not math.isfinite(width_mm) or width_mm < self.board.rules.min_track_width_mm:
            self.route_status.emit(self.tr('Track width is below the board rule'))
            return False
        selected = [item.track for item in self.scene.selectedItems()
                    if isinstance(item, PcbTrackItem)]
        if selected and any(track.width_mm != width_mm for track in selected):
            for track in selected:
                candidate = deepcopy(track)
                candidate.width_mm = width_mm
                error = self.board.routing_error(candidate)
                if error:
                    self.route_status.emit(error)
                    return False
            self._push_undo()
            for item in self.scene.items():
                if isinstance(item, PcbTrackItem):
                    item.prepareGeometryChange()
            for track in selected:
                track.width_mm = width_mm
            for item in self.scene.items():
                if isinstance(item, PcbTrackItem):
                    pen = item.pen()
                    pen.setWidthF(item.track.width_mm)
                    item.setPen(pen)
                    item.update()
            self.update_scene_rect()
            self.refresh_ratsnest()
            self._update_info()
            self.board_changed.emit(self.board)
        self.track_width_mm = width_mm
        return True

    def set_ratsnest_visible(self, visible: bool):
        self.ratsnest_visible = bool(visible)
        self.ratsnest_check.blockSignals(True)
        self.ratsnest_check.setChecked(visible)
        self.ratsnest_check.blockSignals(False)
        self.refresh_ratsnest()

    def toggle_route_layer(self):
        self.set_active_layer('B.Cu' if self.active_layer == 'F.Cu'
                              else 'F.Cu')
        self.route_status.emit(
            self.tr('Switched to layer {layer}').format(
                layer=self.active_layer))

    def set_route_mode(self, enabled: bool):
        enabled = bool(enabled)
        if enabled:
            self.set_area_mode(False)
            if self.active_layer not in ('F.Cu', 'B.Cu'):
                self.set_active_layer('F.Cu')
            if not self.is_layer_visible(self.active_layer):
                self.set_layer_visible(self.active_layer, True)
        if not enabled:
            self.cancel_route()
        if self.route_mode != enabled:
            self.route_mode = enabled
            self.route_mode_changed.emit(enabled)
        self.view.setMouseTracking(enabled)
        self.view.setDragMode(QGraphicsView.DragMode.NoDrag if enabled
                              else QGraphicsView.DragMode.RubberBandDrag)
        self.route_status.emit(
            self.tr('Route track: click a pad to start') if enabled
            else self.tr('Track routing cancelled'))

    def _pad_supports_active_layer(self, pad):
        return any(self.active_layer in footprint.pad_layers(pad)
                   for footprint in self.board.footprints
                   if any(candidate is pad for candidate in footprint.pads))

    def _pad_at(self, position):
        closest = None
        closest_distance = float('inf')
        for item in self._footprint_items.values():
            if not item.isVisible():
                continue
            for pad in item.footprint.pads:
                if not any(self.is_layer_visible(layer)
                           for layer in item.footprint.pad_layers(pad)):
                    continue
                pad_position = item.mapToScene(QPointF(pad.x_mm, pad.y_mm))
                distance = math.hypot(
                    position.x() - pad_position.x(),
                    position.y() - pad_position.y())
                hit = item.mapToScene(expanded_path(pad_shape(pad, False), 0.15))
                if hit.contains(position) and distance < closest_distance:
                    closest = (item, pad, pad_position)
                    closest_distance = distance
        return closest

    def _route_leg(self, end):
        start = self._route_points[-1]
        dx, dy = end.x() - start.x(), end.y() - start.y()
        diagonal = min(abs(dx), abs(dy))
        offset = QPointF(math.copysign(diagonal, dx), math.copysign(diagonal, dy))
        candidates = (start + offset, end - offset)
        last_error = None
        for corner in candidates:
            points = list(self._route_points)
            for point in (corner, end):
                if point != points[-1]:
                    points.append(point)
            track = PcbTrack(self._route_net, self.active_layer, self.track_width_mm,
                             [(point.x(), point.y()) for point in points])
            last_error = self.board.routing_error(track)
            if last_error is None:
                return points, None
        return points, last_error

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
        points, error = self._route_leg(cursor)
        path = QPainterPath(points[0])
        for point in points[1:]:
            path.lineTo(point)
        preview = QGraphicsPathItem(path)
        pen = QPen(QColor('#ff5470') if error else _layer_color(self.active_layer, self), self.track_width_mm,
                   Qt.PenStyle.DashLine)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        preview.setPen(pen)
        preview.setZValue(4)
        preview.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
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
            points, error = self._route_leg(pad_position)
            if error:
                self.route_status.emit(error)
                return
            self._route_points = points
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
                self.route_status.emit(self.tr('Track committed; continuity recalculated'))
            return

        point = self.snap_position(position)
        if point == self._route_points[-1]:
            return
        points, error = self._route_leg(point)
        if error:
            self.route_status.emit(error)
            return
        self._route_points = points
        self._update_route_preview(point)

    def place_via(self):
        if not self.route_mode or self._route_net is None:
            return False
        if self._route_cursor is not None and self._route_cursor != self._route_points[-1]:
            points, error = self._route_leg(self._route_cursor)
        else:
            points, error = self._route_points, None
        if error:
            self.route_status.emit(error)
            return False
        if len(points) < 2:
            self.route_status.emit(
                self.tr('Add a track segment before placing a via'))
            return False
        point = points[-1]
        via = PcbVia(point.x(), point.y(), self._route_net,
                     drill_mm=max(0.3, self.board.rules.min_drill_mm),
                     diameter_mm=max(0.7, self.board.rules.min_drill_mm + 0.4))
        error = self.board.routing_error(via)
        if error:
            self.route_status.emit(error)
            return False
        self._push_undo()
        self.board.tracks.append(PcbTrack(
            net=self._route_net,
            layer=self.active_layer,
            width_mm=self.track_width_mm,
            points=[(route_point.x(), route_point.y())
                    for route_point in points],
        ))
        self.board.vias.append(via)
        self._route_points = [point]
        self._clear_route_preview()
        self._draw_board(fit=False)
        self.board_changed.emit(self.board)
        net = self._route_net
        self._route_net = None
        self.toggle_route_layer()
        self._route_net = net
        if not self.is_layer_visible(self.active_layer):
            self.set_layer_visible(self.active_layer, True)
        self._update_route_preview(point)
        return True

    def _autoroute_path(self, start, end, start_layers, end_layers, net):
        """A* pequeño en dos capas; valida cada segmento y el diámetro de vía."""
        outline = self.board.outline or (
            0.0, 0.0, self.board.width_mm, self.board.height_mm)
        origin_x, origin_y, width, height = outline
        step = 1.0
        max_x = max(0, round(width / step))
        max_y = max(0, round(height / step))
        layers = ('F.Cu', 'B.Cu')
        if max_x * max_y > 100000:
            return None

        def grid(point):
            return (max(0, min(max_x, round((point[0] - origin_x) / step))),
                    max(0, min(max_y, round((point[1] - origin_y) / step))))

        def point(node):
            return (origin_x + node[0] * step, origin_y + node[1] * step)

        start_grid = grid(start)
        end_grid = grid(end)
        copper = [entry for entry in self.board.copper_items() if entry[0].net != net]

        def segment_clear(first, second, layer):
            if first == second:
                return True
            return self.board.routing_error(PcbTrack(
                net, layer, self.track_width_mm, [first, second]), copper) is None

        def estimate(node):
            return math.hypot(node[0] - end_grid[0], node[1] - end_grid[1])

        starts = [(start_grid[0], start_grid[1], layer)
                  for layer in layers if layer in start_layers]
        targets = set(end_layers).intersection(layers)
        if not starts or not targets:
            return None
        # ponytail: 1 mm grid, max 100k cells; finer/adaptive routing for dense boards.
        queue = []
        costs = {}
        came_from = {}
        for node in starts:
            if segment_clear(start, point(node), node[2]):
                costs[node] = 0
                heappush(queue, (estimate(node), 0, node))

        goal = None
        while queue:
            _, cost, node = heappop(queue)
            if cost != costs.get(node):
                continue
            if (node[:2] == end_grid and node[2] in targets
                    and segment_clear(point(node), end, node[2])):
                goal = node
                break
            x, y, layer = node
            for next_x, next_y in ((x - 1, y), (x + 1, y),
                                   (x, y - 1), (x, y + 1),
                                   (x - 1, y - 1), (x - 1, y + 1),
                                   (x + 1, y - 1), (x + 1, y + 1)):
                if not (0 <= next_x <= max_x and 0 <= next_y <= max_y):
                    continue
                next_node = (next_x, next_y, layer)
                if not segment_clear(point(node), point(next_node), layer):
                    continue
                next_cost = cost + math.hypot(next_x - x, next_y - y)
                if next_cost < costs.get(next_node, float('inf')):
                    costs[next_node] = next_cost
                    came_from[next_node] = node
                    heappush(queue, (next_cost + estimate(next_node), next_cost, next_node))
            other = 'B.Cu' if layer == 'F.Cu' else 'F.Cu'
            next_node = (x, y, other)
            via = PcbVia(*point(node), net=net,
                         drill_mm=max(0.3, self.board.rules.min_drill_mm),
                         diameter_mm=max(0.7, self.board.rules.min_drill_mm + 0.4))
            if (node[:2] not in (start_grid, end_grid)
                    and self.board.routing_error(via, copper) is None):
                next_cost = cost + 12
                if next_cost < costs.get(next_node, float('inf')):
                    costs[next_node] = next_cost
                    came_from[next_node] = node
                    heappush(queue, (next_cost + estimate(next_node), next_cost, next_node))

        if goal is None:
            return None
        nodes = [goal]
        while nodes[-1] in came_from:
            nodes.append(came_from[nodes[-1]])
        nodes.reverse()
        nodes = [(*point(node), node[2]) for node in nodes]
        nodes.insert(0, (start[0], start[1], nodes[0][2]))
        nodes.append((end[0], end[1], nodes[-1][2]))
        simplified = []
        for node in nodes:
            if simplified and node == simplified[-1]:
                continue
            if len(simplified) >= 2 and node[2] == simplified[-1][2] == simplified[-2][2]:
                first, second = simplified[-2], simplified[-1]
                if ((second[0] - first[0]) * (node[1] - second[1])
                        == (second[1] - first[1]) * (node[0] - second[0])):
                    simplified[-1] = node
                    continue
            simplified.append(node)
        return simplified

    def autoroute(self):
        self.cancel_route(announce=False)
        pairs = self.board.unrouted_connections()
        if not pairs:
            self.route_status.emit(self.tr('No unrouted connections'))
            return False
        routed = 0
        failed = 0
        self._push_undo()
        for (start_footprint, start_pad), (end_footprint, end_pad) in pairs:
            before_groups = len(self.board._routing_groups(start_pad.net))
            before_tracks, before_vias = len(self.board.tracks), len(self.board.vias)
            start_item = self._footprint_items.get(start_footprint.reference)
            end_item = self._footprint_items.get(end_footprint.reference)
            if start_item is None or end_item is None:
                failed += 1
                continue
            start_point = start_item.mapToScene(
                QPointF(start_pad.x_mm, start_pad.y_mm))
            end_point = end_item.mapToScene(QPointF(end_pad.x_mm, end_pad.y_mm))
            path = self._autoroute_path(
                (start_point.x(), start_point.y()),
                (end_point.x(), end_point.y()),
                start_footprint.pad_layers(start_pad),
                end_footprint.pad_layers(end_pad), start_pad.net)
            if not path:
                failed += 1
                continue
            current_layer = path[0][2]
            points = [(path[0][0], path[0][1])]
            for x, y, layer in path[1:]:
                if layer != current_layer:
                    if len(points) >= 2:
                        self.board.tracks.append(PcbTrack(
                            start_pad.net, current_layer,
                            self.track_width_mm, points))
                    self.board.vias.append(PcbVia(
                        x, y, start_pad.net,
                        drill_mm=max(0.3, self.board.rules.min_drill_mm),
                        diameter_mm=max(0.7, self.board.rules.min_drill_mm + 0.4)))
                    current_layer = layer
                    points = [(x, y)]
                elif points[-1] != (x, y):
                    points.append((x, y))
            if len(points) >= 2:
                self.board.tracks.append(PcbTrack(
                    start_pad.net, current_layer, self.track_width_mm, points))
            if len(self.board._routing_groups(start_pad.net)) < before_groups:
                routed += 1
            else:
                del self.board.tracks[before_tracks:]
                del self.board.vias[before_vias:]
                failed += 1
        if not routed:
            self.undo()
            self.route_status.emit(self.tr('Autoroute could not route connections'))
            return False
        self._draw_board(fit=False)
        self.board_changed.emit(self.board)
        self.route_status.emit(self.tr('Autoroute: {routed} routed, {failed} failed').format(
            routed=routed, failed=failed))
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
        self._clear_route_preview()
        self._invalidate_drc()
        self._ratsnest.clear()
        self._footprint_items.clear()
        self._area_item = None
        self._area_preview = None
        self._area_start = None
        self.scene.clear()

        if self.board.outline is not None:
            self._area_item = PcbBoardAreaItem(self.board.outline, self)
            self.scene.addItem(self._area_item)

        for footprint in self.board.footprints:
            item = PcbFootprintItem(footprint, self)
            self._footprint_items[footprint.reference] = item
            self.scene.addItem(item)

        for text in self.board.texts:
            self.scene.addItem(PcbTextItem(text, self))

        for via in self.board.vias:
            self.scene.addItem(PcbViaItem(via, self))

        for track in self.board.tracks:
            if len(track.points) >= 2:
                self.scene.addItem(PcbTrackItem(track, self))

        self.update_scene_rect()
        self._apply_layer_visibility()
        self._sync_layer_controls()
        if fit:
            self.fit_board()
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

        for text in self.board.texts:
            rects.append(QRectF(text.x_mm - 20.0, text.y_mm - 20.0,
                                40.0, 40.0))

        scene_rect = rects[0] if rects else QRectF(-50.0, -40.0, 100.0, 80.0)
        for rect in rects[1:]:
            scene_rect = scene_rect.united(rect)
        self.scene.setSceneRect(scene_rect)

    def refresh_ratsnest(self):
        for line in self._ratsnest:
            self.scene.removeItem(line)
        self._ratsnest.clear()
        if not self.ratsnest_visible:
            return
        pen = QPen(QColor('#8bafa7'), 0, Qt.PenStyle.DashLine)
        pen.setCosmetic(True)
        for (first_footprint, first_pad), (end_footprint, end_pad) in \
                self.board.unrouted_connections():
            first_item = self._footprint_items.get(first_footprint.reference)
            end_item = self._footprint_items.get(end_footprint.reference)
            if first_item is None or end_item is None:
                continue
            start = first_item.mapToScene(QPointF(first_pad.x_mm, first_pad.y_mm))
            end = end_item.mapToScene(QPointF(end_pad.x_mm, end_pad.y_mm))
            line = QGraphicsLineItem(QLineF(start, end))
            line.setPen(pen)
            line.setZValue(2.8)
            line.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
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
        text += f' · Grid: {self._format_length(self.scene.grid_step_mm)} · {self.active_layer}'
        text += self.tr(' · Unrouted: {count}').format(
            count=len(self.board.unrouted_connections()))
        if self.board.warnings:
            text += self.tr(' · Warnings: {count}').format(count=len(self.board.warnings))
        warnings = '\n'.join(self.board.warnings)
        self.info.setToolTip(warnings)
        self.board_warnings.setPlainText(warnings)
        self.board_warnings.setVisible(bool(warnings))
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
        self.grid_spin.setValue(self.scene.grid_step_mm)
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
        self.board.width_mm, self.board.height_mm = self.board.outline[2:]
        self.update_scene_rect()
        self.scene.update()
        self._update_info()
        if emit:
            self.board_changed.emit(self.board)

    def _regenerate(self):
        if self.source_scene is None:
            return
        self._push_undo()
        self.cancel_route(announce=False)
        previous = self.board
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
        self.board.width_mm, self.board.height_mm = previous.width_mm, previous.height_mm
        self.board.tracks = previous.tracks
        self.board.vias = previous.vias
        self.board.texts = previous.texts
        self.board.layers = previous.layers
        self.board.rules = previous.rules
        old_footprints = {fp.reference: fp for fp in previous.footprints}
        for index, footprint in enumerate(self.board.footprints):
            placement = placements.get(footprint.reference)
            if placement is not None:
                (footprint.x_mm, footprint.y_mm,
                 footprint.angle, footprint.side) = placement
                old = old_footprints[footprint.reference]
                if old.footprint_name == footprint.footprint_name:
                    nets = {pad.number: pad.net for pad in footprint.pads}
                    preserved = deepcopy(old)
                    preserved.value = footprint.value
                    for pad in preserved.pads:
                        pad.net = nets.get(pad.number, '?')
                    self.board.footprints[index] = preserved
            warning = self.board.footprints[index].package_warning()
            if warning:
                self.board.warnings.append(warning)
        self.board.warnings.append(self.tr('Copper retained on regeneration; run DRC after schematic changes.'))
        self.board_changed.emit(self.board)
        self._draw_board()
