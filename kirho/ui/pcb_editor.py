"""Editor PCB interno, deliberadamente pequeño: footprints y ratsnest."""
from __future__ import annotations

import math
from copy import deepcopy

from PyQt6.QtCore import QEvent, QPointF, QRectF, QLineF, Qt, pyqtSignal
from PyQt6.QtGui import (
    QBrush, QColor, QFont, QPainter, QPainterPath, QPainterPathStroker, QPen,
)
from PyQt6.QtWidgets import (
    QGraphicsItem, QGraphicsLineItem, QGraphicsObject, QGraphicsRectItem,
    QGraphicsScene, QGraphicsView, QHBoxLayout, QLabel, QPushButton,
    QStyle, QVBoxLayout, QWidget,
)

from kirho.pcb import PcbBoard, PcbFootprint, build_pcb_board
from kirho.ui.style import COLORS


def _color(name: str, fallback: str) -> QColor:
    return QColor(COLORS.get(name, fallback))


class PcbScene(QGraphicsScene):
    """Escena PCB con cuadrícula y captura del modo de área."""

    def __init__(self, editor):
        super().__init__(editor)
        self.editor = editor
        self.unit = 'mm'

    def set_unit(self, unit: str):
        if unit in ('mm', 'in'):
            self.unit = unit
            self.update()

    def drawBackground(self, painter: QPainter, rect: QRectF):
        painter.fillRect(rect, QBrush(_color('bg', '#15202b')))
        step = 1.0 if self.unit == 'mm' else 2.54
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
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.editor.area_mode:
            self.editor._update_area_preview(event.scenePos())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self.editor.area_mode:
            self.editor._finish_area(event.scenePos())
            event.accept()
            return
        super().mouseReleaseEvent(event)


class PcbView(QGraphicsView):
    """Vista PCB con zoom de rueda, Ctrl/⌘+rueda y pinch de trackpad."""

    _MIN_ZOOM = 0.2
    _MAX_ZOOM = 100.0

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
        return super().viewportEvent(event)


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
        self.setPos(x, y)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
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
            self._resize_start_scene_pos = event.scenePos()
            self.editor._push_undo()
            event.accept()
            return
        self.editor._push_undo()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._resize_handle is None:
            super().mouseMoveEvent(event)
            return

        delta = event.scenePos() - self._resize_start_scene_pos
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
        result = super().itemChange(change, value)
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged:
            rect = self.rect()
            self.editor._set_outline(
                (value.x(), value.y(), rect.width(), rect.height()),
                emit=self._resize_handle is None)
        elif change == QGraphicsItem.GraphicsItemChange.ItemSelectedHasChanged:
            self.update()
        return result


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

    def mousePressEvent(self, event):
        self.editor._push_undo()
        super().mousePressEvent(event)

    def boundingRect(self) -> QRectF:
        margin = 5.0
        return QRectF(
            -self.footprint.width_mm / 2 - margin,
            -self.footprint.height_mm / 2 - margin - 4,
            self.footprint.width_mm + 2 * margin,
            self.footprint.height_mm + 2 * margin + 8,
        )

    def paint(self, painter: QPainter, option, widget=None):
        body = QRectF(
            -self.footprint.width_mm / 2,
            -self.footprint.height_mm / 2,
            self.footprint.width_mm,
            self.footprint.height_mm,
        )
        selected = bool(option.state & QStyleState.State_Selected)
        painter.setPen(QPen(_color('component', '#e6a23c') if selected
                            else _color('panel_brd', '#6b7280'), 0.35))
        painter.setBrush(QBrush(_color('comp_body', '#263238')))
        painter.drawRoundedRect(body, 1.2, 1.2)

        for pad in self.footprint.pads:
            radius = 1.15
            rect = QRectF(pad.x_mm - radius, pad.y_mm - radius,
                          2 * radius, 2 * radius)
            painter.setBrush(QBrush(_color('current', '#f1c40f')))
            painter.setPen(QPen(_color('text', '#ffffff'), 0.25))
            if pad.number == 1:
                painter.drawRect(rect)
            else:
                painter.drawEllipse(rect)

        painter.setPen(QPen(_color('text', '#ffffff'), 0.25))
        painter.setFont(QFont('Menlo', 3))
        painter.drawText(QRectF(-self.footprint.width_mm / 2,
                                -self.footprint.height_mm / 2 - 4,
                                self.footprint.width_mm, 3),
                         Qt.AlignmentFlag.AlignCenter, self.footprint.reference)
        painter.setFont(QFont('Menlo', 2))
        painter.drawText(QRectF(-self.footprint.width_mm / 2,
                                self.footprint.height_mm / 2 + 0.5,
                                self.footprint.width_mm, 3),
                         Qt.AlignmentFlag.AlignCenter, self.footprint.value)

    def itemChange(self, change, value):
        result = super().itemChange(change, value)
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged:
            self.footprint.x_mm = value.x()
            self.footprint.y_mm = value.y()
            if self.scene() is not None:
                self.editor.refresh_ratsnest()
        return result


# Alias to keep the paint method readable.
QStyleState = QStyle.StateFlag


class PcbEditorWidget(QWidget):
    board_changed = pyqtSignal(object)
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

        self.setWindowTitle(self.tr('PCB Editor — Kirho'))
        self.resize(1000, 700)
        root = QVBoxLayout(self)

        self.info = QLabel()
        self.info.setWordWrap(True)
        root.addWidget(self.info)

        self.scene = PcbScene(self)
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
        self.board_changed.emit(self.board)
        return True

    def _draw_board(self):
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

        if self.board.outline is not None:
            x, y, width, height = self.board.outline
            scene_rect = QRectF(x - 10.0, y - 10.0,
                                width + 20.0, height + 20.0)
        elif self.board.footprints:
            min_x = min(item.x_mm - item.width_mm / 2
                        for item in self.board.footprints)
            min_y = min(item.y_mm - item.height_mm / 2
                        for item in self.board.footprints)
            max_x = max(item.x_mm + item.width_mm / 2
                        for item in self.board.footprints)
            max_y = max(item.y_mm + item.height_mm / 2
                        for item in self.board.footprints)
            scene_rect = QRectF(min_x - 20.0, min_y - 20.0,
                                max_x - min_x + 40.0,
                                max_y - min_y + 40.0)
        else:
            scene_rect = QRectF(-50.0, -40.0, 100.0, 80.0)
        self.scene.setSceneRect(scene_rect)
        self.refresh_ratsnest()
        self.view.fitInView(self.scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)
        self._update_info()

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
        self.area_mode = bool(enabled)
        self.view.setDragMode(
            QGraphicsView.DragMode.NoDrag if self.area_mode
            else QGraphicsView.DragMode.RubberBandDrag)
        if not self.area_mode and self._area_preview is not None:
            self.scene.removeItem(self._area_preview)
            self._area_preview = None

    def _start_area(self, point: QPointF):
        self._area_start = point
        self._area_preview = QGraphicsRectItem(QRectF(point, point))
        pen = QPen(_color('component', '#65d6a0'), 0.6, Qt.PenStyle.DashLine)
        self._area_preview.setPen(pen)
        self._area_preview.setBrush(QBrush(Qt.BrushStyle.NoBrush))
        self._area_preview.setZValue(-1)
        self.scene.addItem(self._area_preview)

    def _update_area_preview(self, point: QPointF):
        if self._area_preview is not None and self._area_start is not None:
            self._area_preview.setRect(QRectF(self._area_start, point).normalized())

    def _finish_area(self, point: QPointF):
        if self._area_start is None:
            return
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
        if emit:
            self.board_changed.emit(self.board)

    def _regenerate(self):
        if self.source_scene is None:
            return
        self._push_undo()
        outline = self.board.outline
        self.board = build_pcb_board(self.source_scene)
        self.board.outline = outline
        self.board_changed.emit(self.board)
        self._draw_board()
