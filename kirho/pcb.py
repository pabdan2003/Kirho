"""Modelo de PCB y conversión desde una hoja esquemática.

Las coordenadas se guardan siempre en milímetros. Los footprints son land
patterns editables: describen pads, taladros, capas y una zona de courtyard,
en lugar de reducir cada componente a un rectángulo visual.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from typing import Dict, List, Tuple

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QPainterPath, QPainterPathStroker, QTransform


MM_PER_MIL = 0.0254
LAYER_COLORS = {
    'F.Cu': '#ed6963', 'B.Cu': '#639ff5',
    'F.SilkS': '#e9eee6', 'B.SilkS': '#adbdd3',
    'F.Mask': '#659a78', 'B.Mask': '#768aad', 'Edge.Cuts': '#e7cd82',
}


def mil_to_mm(value: float) -> float:
    """Convierte thou/mil a milímetros."""
    return float(value) * MM_PER_MIL


def mm_to_mil(value: float) -> float:
    """Convierte milímetros a thou/mil."""
    return float(value) / MM_PER_MIL


@dataclass(frozen=True)
class PadSpec:
    """Geometría de un pad dentro del origen del footprint."""

    x_mm: float
    y_mm: float
    width_mm: float = 2.0
    height_mm: float = 2.0
    shape: str = 'circle'
    drill_mm: float = 1.0
    pad_type: str = 'tht'
    layers: Tuple[str, ...] = ('F.Cu', 'B.Cu')


@dataclass(frozen=True)
class PcbSilk:
    """Trazo local del footprint, en mm, sobre la serigrafía de su cara.

    Una circunferencia usa dos esquinas de su caja; una polilínea usa vértices.
    Se guarda la geometría, no una referencia mutable a la biblioteca.
    """

    kind: str
    points: Tuple[Tuple[float, float], ...]
    width_mm: float = 0.15

    def __post_init__(self):
        if (self.kind not in ('polyline', 'circle') or len(self.points) < 2
                or (self.kind == 'circle' and len(self.points) != 2)
                or not math.isfinite(self.width_mm) or self.width_mm <= 0
                or any(len(p) != 2 or not all(math.isfinite(v) for v in p)
                       for p in self.points)):
            raise ValueError('Invalid footprint silkscreen geometry')

    def path(self):
        path = QPainterPath()
        if self.kind == 'circle':
            path.addEllipse(QRectF(QPointF(*self.points[0]),
                                  QPointF(*self.points[1])).normalized())
        else:
            path.moveTo(QPointF(*self.points[0]))
            for point in self.points[1:]:
                path.lineTo(QPointF(*point))
        stroker = QPainterPathStroker()
        stroker.setWidth(self.width_mm)
        stroker.setCurveThreshold(0.001)
        stroker.setCapStyle(Qt.PenCapStyle.RoundCap)
        stroker.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        return stroker.createStroke(path)


@dataclass(frozen=True)
class FootprintSpec:
    name: str
    pads: Tuple[PadSpec, ...]
    width_mm: float
    height_mm: float
    silkscreen: Tuple[PcbSilk, ...] = ()


@dataclass
class PcbPad:
    number: int
    x_mm: float
    y_mm: float
    net: str
    width_mm: float = 2.0
    height_mm: float = 2.0
    shape: str = 'circle'
    drill_mm: float = 1.0
    pad_type: str = 'tht'
    layers: Tuple[str, ...] = ('F.Cu', 'B.Cu')


@dataclass
class PcbLayer:
    """Capa disponible en la placa."""

    name: str
    layer_type: str = 'copper'
    enabled: bool = True
    color: str = ''


@dataclass
class PcbTrack:
    """Pista como polilínea de puntos en coordenadas PCB (mm)."""

    net: str
    layer: str = 'F.Cu'
    width_mm: float = 0.25
    points: List[Tuple[float, float]] = field(default_factory=list)


@dataclass
class PcbVia:
    """Vía que conecta las capas indicadas."""

    x_mm: float
    y_mm: float
    net: str = '?'
    drill_mm: float = 0.3
    diameter_mm: float = 0.7
    layers: Tuple[str, ...] = ('F.Cu', 'B.Cu')


@dataclass
class PcbText:
    """Texto de usuario, normalmente colocado en una capa de serigrafía."""

    text: str
    x_mm: float
    y_mm: float
    layer: str = 'F.SilkS'
    size_mm: float = 2.0
    width_mm: float = 0.3
    angle: float = 0.0


@dataclass
class PcbRule:
    """Reglas mínimas globales para futuras comprobaciones DRC."""

    clearance_mm: float = 0.2
    min_track_width_mm: float = 0.2
    min_drill_mm: float = 0.3
    edge_clearance_mm: float = 0.25


def _default_layers() -> List[PcbLayer]:
    return [
        PcbLayer('F.Cu', 'copper'),
        PcbLayer('B.Cu', 'copper'),
        PcbLayer('F.SilkS', 'silkscreen'),
        PcbLayer('B.SilkS', 'silkscreen'),
        PcbLayer('F.Mask', 'mask'),
        PcbLayer('B.Mask', 'mask'),
        PcbLayer('Edge.Cuts', 'edge'),
    ]


@dataclass
class PcbFootprint:
    reference: str
    component_type: str
    value: str
    footprint_name: str
    x_mm: float
    y_mm: float
    angle: float
    width_mm: float
    height_mm: float
    pads: List[PcbPad] = field(default_factory=list)
    side: str = 'F.Cu'
    courtyard_margin_mm: float = 0.25
    silkscreen: List[PcbSilk] | None = None

    def __post_init__(self):
        if self.silkscreen is None:
            spec = FOOTPRINT_LIBRARY.get(self.footprint_name)
            self.silkscreen = list(spec.silkscreen) if spec and spec.silkscreen else [
                _silk_box(self.width_mm, self.height_mm)]

    def transform(self):
        """Origen en mm; vista superior, giro horario y espejo X en cara B."""
        transform = QTransform().translate(self.x_mm, self.y_mm)
        transform.rotate(self.angle)
        if self.side == 'B.Cu':
            transform.scale(-1, 1)
        return transform

    def pad_position(self, pad):
        point = self.transform().map(QPointF(pad.x_mm, pad.y_mm))
        return point.x(), point.y()

    def pad_layers(self, pad):
        # Las capas del pad son locales al footprint frontal, también en
        # archivos antiguos. Cambiar de cara transforma geometría Y capas.
        if self.side == 'B.Cu':
            return tuple({'F.Cu': 'B.Cu', 'B.Cu': 'F.Cu'}.get(layer, layer)
                         for layer in pad.layers)
        return pad.layers

    def package_warning(self):
        if self.footprint_name not in ('DIP-8 W7.62mm', 'SOIC-8 1.27mm'):
            return ''
        spec = FOOTPRINT_LIBRARY[self.footprint_name]
        if len(self.pads) != len(spec.pads) or any(
                not math.isclose(pad.x_mm, expected.x_mm)
                or not math.isclose(pad.y_mm, expected.y_mm)
                for pad, expected in zip(self.pads, spec.pads)):
            return (f'{self.reference}: legacy/custom {spec.name} pad positions; '
                    'pads preserved. Verify package dimensions before manufacture.')
        return ''


def stroke_path(points, width):
    path = QPainterPath()
    if len(points) < 2 or width <= 0:
        return path
    path.moveTo(QPointF(*points[0]))
    for point in points[1:]:
        path.lineTo(QPointF(*point))
    stroker = QPainterPathStroker()
    stroker.setWidth(width)
    stroker.setCapStyle(Qt.PenCapStyle.RoundCap)
    stroker.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    return stroker.createStroke(path)


def pad_shape(pad, drilled=True):
    """La misma superficie física se usa para pintar, conectar y validar."""
    path = QPainterPath()
    if pad.width_mm <= 0 or pad.height_mm <= 0:
        return path
    rect = QRectF(pad.x_mm - pad.width_mm / 2,
                  pad.y_mm - pad.height_mm / 2, pad.width_mm, pad.height_mm)
    if pad.shape == 'circle':
        path.addEllipse(rect)
    elif pad.shape in ('oval', 'roundrect', 'rounded_rect'):
        radius = min(pad.width_mm, pad.height_mm) * (
            0.5 if pad.shape == 'oval' else 0.25)
        path.addRoundedRect(rect, radius, radius)
    else:
        path.addRect(rect)
    if drilled and pad.drill_mm > 0:
        hole = QPainterPath()
        hole.addEllipse(QPointF(pad.x_mm, pad.y_mm),
                        pad.drill_mm / 2, pad.drill_mm / 2)
        path = subtract_path(path, hole)
    return path


def via_shape(via):
    return pad_shape(PcbPad(0, via.x_mm, via.y_mm, via.net,
                           via.diameter_mm, via.diameter_mm,
                           drill_mm=via.drill_mm))


def expanded_path(path, margin):
    if margin <= 0:
        return path
    # Qt Boolean operations flatten curves with a tolerance in path units.
    # Work at 100x so millimeter-scale circles do not turn into octagons.
    path = QTransform.fromScale(100, 100).map(path)
    stroker = QPainterPathStroker()
    stroker.setWidth(margin * 200)
    stroker.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    return QTransform.fromScale(0.01, 0.01).map(path.united(stroker.createStroke(path)))


def subtract_path(path, other):
    scale = QTransform.fromScale(100, 100)
    return QTransform.fromScale(0.01, 0.01).map(
        scale.map(path).subtracted(scale.map(other)))


@dataclass
class PcbViolation:
    code: str
    message: str
    objects: tuple = ()
    severity: str = 'error'


@dataclass
class PcbBoard:
    width_mm: float = 100.0
    height_mm: float = 80.0
    footprints: List[PcbFootprint] = field(default_factory=list)
    tracks: List[PcbTrack] = field(default_factory=list)
    vias: List[PcbVia] = field(default_factory=list)
    texts: List[PcbText] = field(default_factory=list)
    layers: List[PcbLayer] = field(default_factory=_default_layers)
    rules: PcbRule = field(default_factory=PcbRule)
    warnings: List[str] = field(default_factory=list)
    outline: Tuple[float, float, float, float] | None = None

    def copper_items(self):
        items = [(pad, footprint.transform().map(pad_shape(pad)),
                  footprint.pad_layers(pad))
                 for footprint in self.footprints for pad in footprint.pads]
        items.extend((track, stroke_path(track.points, track.width_mm),
                      (track.layer,)) for track in self.tracks)
        items.extend((via, via_shape(via), via.layers) for via in self.vias)
        return items

    def routing_error(self, obj, copper=None):
        """Validación compartida por ruteo manual y automático, en mm."""
        if isinstance(obj, PcbTrack):
            if (len(obj.points) < 2 or obj.width_mm < self.rules.min_track_width_mm
                    or obj.layer not in ('F.Cu', 'B.Cu')):
                return 'Invalid track width, layer or vertices'
            shape, layers = stroke_path(obj.points, obj.width_mm), (obj.layer,)
        else:
            if (obj.drill_mm < self.rules.min_drill_mm
                    or obj.drill_mm >= obj.diameter_mm
                    or set(obj.layers) != {'F.Cu', 'B.Cu'}):
                return 'Invalid via drill, diameter or layer transition'
            shape, layers = via_shape(obj), obj.layers
        if self.outline is not None:
            interior = QPainterPath()
            interior.addRect(QRectF(*self.outline).adjusted(
                self.rules.edge_clearance_mm, self.rules.edge_clearance_mm,
                -self.rules.edge_clearance_mm, -self.rules.edge_clearance_mm))
            if not interior.contains(shape):
                return 'Copper too close to or outside Edge.Cuts'
        clearance_shape = expanded_path(shape, self.rules.clearance_mm)
        for other, other_shape, other_layers in (
                self.copper_items() if copper is None else copper):
            if other is obj or (obj.net not in ('', '?') and other.net == obj.net):
                continue
            if (set(layers).intersection(other_layers)
                    and clearance_shape.intersects(other_shape)):
                return f'Copper clearance to net {other.net}'
        return None

    def check_drc(self):
        """Base DRC geométrica; sin zonas de cobre ni reglas por clase de red."""
        violations = []
        copper = self.copper_items()
        if self.outline is None:
            violations.append(PcbViolation('outline', 'Edge.Cuts is not defined'))
        interior = QPainterPath()
        if self.outline is not None:
            interior.addRect(QRectF(*self.outline).adjusted(
                self.rules.edge_clearance_mm, self.rules.edge_clearance_mm,
                -self.rules.edge_clearance_mm, -self.rules.edge_clearance_mm))
        # ponytail: O(n²) copper checks; add spatial indexing for large boards.
        for index, (obj, shape, layers) in enumerate(copper):
            invalid = shape.isEmpty() or not set(layers).issubset({'F.Cu', 'B.Cu'})
            if isinstance(obj, PcbPad):
                invalid |= (obj.number <= 0 or obj.drill_mm < 0
                            or obj.drill_mm >= min(obj.width_mm, obj.height_mm)
                            or (obj.pad_type == 'tht'
                                and obj.drill_mm < self.rules.min_drill_mm))
            elif isinstance(obj, PcbVia):
                invalid |= (obj.drill_mm < self.rules.min_drill_mm
                            or obj.drill_mm >= obj.diameter_mm
                            or set(layers) != {'F.Cu', 'B.Cu'})
            else:
                invalid |= obj.width_mm < self.rules.min_track_width_mm
            if invalid:
                violations.append(PcbViolation('geometry', 'Invalid copper / drill geometry', (obj,)))
            if self.outline is not None and not interior.contains(shape):
                violations.append(PcbViolation('edge', 'Copper clearance to Edge.Cuts', (obj,)))
            touching = False
            for other, other_shape, other_layers in copper:
                if other is not obj and set(layers).intersection(other_layers):
                    touching |= (obj.net == other.net and shape.intersects(other_shape))
            if isinstance(obj, PcbTrack) and not touching:
                violations.append(PcbViolation('dangling', 'Disconnected track', (obj,), 'warning'))
            for other, other_shape, other_layers in copper[index + 1:]:
                if (not set(layers).intersection(other_layers)
                        or (obj.net not in ('', '?') and obj.net == other.net)):
                    continue
                if shape.intersects(other_shape):
                    violations.append(PcbViolation('short', f'Short: {obj.net} / {other.net}', (obj, other)))
                elif expanded_path(shape, self.rules.clearance_mm).intersects(other_shape):
                    violations.append(PcbViolation('clearance', f'Copper clearance: {obj.net} / {other.net}', (obj, other)))
        for first, second in self.unrouted_connections():
            violations.append(PcbViolation('unrouted', f'Unrouted net {first[1].net}',
                                           (first[1], second[1]), 'warning'))
        return violations

    def pads_by_net(self) -> Dict[str, List[Tuple[PcbFootprint, PcbPad]]]:
        result: Dict[str, List[Tuple[PcbFootprint, PcbPad]]] = {}
        for footprint in self.footprints:
            for pad in footprint.pads:
                if pad.net and pad.net != "?":
                    result.setdefault(pad.net, []).append((footprint, pad))
        return result

    def _routing_groups(self, net: str):
        pads = self.pads_by_net().get(net, [])
        copper = [entry for entry in self.copper_items() if entry[0].net == net]
        parent = list(range(len(copper)))

        def find(index):
            while parent[index] != index:
                parent[index] = parent[parent[index]]
                index = parent[index]
            return index

        def union(first, second):
            first, second = find(first), find(second)
            if first != second:
                parent[second] = first

        # ponytail: O(n²) routing scan; add a spatial index only for boards
        # large enough for this to become measurable.
        for first, (_, shape, layers) in enumerate(copper):
            for second in range(first + 1, len(copper)):
                _, other_shape, other_layers = copper[second]
                if set(layers).intersection(other_layers) and shape.intersects(other_shape):
                    union(first, second)

        groups = {}
        indices = {id(obj): index for index, (obj, _, _) in enumerate(copper)}
        for footprint, pad in pads:
            groups.setdefault(find(indices[id(pad)]), []).append((footprint, pad))
        return list(groups.values())

    def routing_status(self) -> Dict[str, dict]:
        """Devuelve el estado de conexión de cada red con dos o más pads."""
        status = {}
        for net, pads in self.pads_by_net().items():
            groups = self._routing_groups(net)
            status[net] = {
                'pad_count': len(pads),
                'connected_pad_count': max(
                    (len(group) for group in groups), default=0),
                'complete': len(groups) <= 1,
            }
        return status

    def unrouted_connections(self):
        """Pares de pads representativos que aún requieren conexión."""
        pairs = []
        for net in self.pads_by_net():
            groups = self._routing_groups(net)
            if len(groups) < 2:
                continue
            first = groups[0][0]
            pairs.extend((first, group[0]) for group in groups[1:])
        return pairs

    def to_dict(self) -> dict:
        return {
            "units": "mm",
            "warnings": list(self.warnings),
            "width_mm": self.width_mm,
            "height_mm": self.height_mm,
            "outline": list(self.outline) if self.outline is not None else None,
            "layers": [
                {
                    "name": layer.name,
                    "layer_type": layer.layer_type,
                    "enabled": layer.enabled,
                    "color": layer.color,
                }
                for layer in self.layers
            ],
            "rules": {
                "clearance_mm": self.rules.clearance_mm,
                "min_track_width_mm": self.rules.min_track_width_mm,
                "min_drill_mm": self.rules.min_drill_mm,
                "edge_clearance_mm": self.rules.edge_clearance_mm,
            },
            "footprints": [
                {
                    "reference": footprint.reference,
                    "component_type": footprint.component_type,
                    "value": footprint.value,
                    "footprint_name": footprint.footprint_name,
                    "x_mm": footprint.x_mm,
                    "y_mm": footprint.y_mm,
                    "angle": footprint.angle,
                    "width_mm": footprint.width_mm,
                    "height_mm": footprint.height_mm,
                    "side": footprint.side,
                    "courtyard_margin_mm": footprint.courtyard_margin_mm,
                    "silkscreen": [
                        {"kind": graphic.kind, "width_mm": graphic.width_mm,
                         "points": [list(point) for point in graphic.points]}
                        for graphic in footprint.silkscreen
                    ],
                    "pads": [
                        {
                            "number": pad.number,
                            "x_mm": pad.x_mm,
                            "y_mm": pad.y_mm,
                            "net": pad.net,
                            "width_mm": pad.width_mm,
                            "height_mm": pad.height_mm,
                            "shape": pad.shape,
                            "drill_mm": pad.drill_mm,
                            "pad_type": pad.pad_type,
                            "layers": list(pad.layers),
                        }
                        for pad in footprint.pads
                    ],
                }
                for footprint in self.footprints
            ],
            "tracks": [
                {
                    "net": track.net,
                    "layer": track.layer,
                    "width_mm": track.width_mm,
                    "points": [list(point) for point in track.points],
                }
                for track in self.tracks
            ],
            "vias": [
                {
                    "x_mm": via.x_mm,
                    "y_mm": via.y_mm,
                    "net": via.net,
                    "drill_mm": via.drill_mm,
                    "diameter_mm": via.diameter_mm,
                    "layers": list(via.layers),
                }
                for via in self.vias
            ],
            "texts": [
                {
                    "text": text.text,
                    "x_mm": text.x_mm,
                    "y_mm": text.y_mm,
                    "layer": text.layer,
                    "size_mm": text.size_mm,
                    "width_mm": text.width_mm,
                    "angle": text.angle,
                }
                for text in self.texts
            ],
        }

    @classmethod
    def from_dict(cls, data: dict | None) -> "PcbBoard | None":
        if not isinstance(data, dict):
            return None
        if data.get('units', 'mm') != 'mm':
            raise ValueError('PCB coordinates must be in millimeters')
        board = cls(
            width_mm=float(data.get("width_mm", 100.0)),
            height_mm=float(data.get("height_mm", 80.0)),
        )
        raw_outline = data.get("outline")
        if isinstance(raw_outline, (list, tuple)) and len(raw_outline) == 4:
            board.outline = tuple(float(value) for value in raw_outline)
            board.width_mm, board.height_mm = board.outline[2:]
        board.warnings = [str(warning) for warning in data.get('warnings', [])]
        raw_layers = data.get("layers")
        if isinstance(raw_layers, list):
            board.layers = [PcbLayer(
                name=str(layer.get("name", "")),
                layer_type=str(layer.get("layer_type", "copper")),
                enabled=bool(layer.get("enabled", True)),
                color=str(layer.get("color", "")),
            ) for layer in raw_layers if isinstance(layer, dict)
            and layer.get("name")]
            known = {layer.name for layer in board.layers}
            board.layers.extend(layer for layer in _default_layers()
                                if layer.name not in known)
        raw_rules = data.get("rules")
        if isinstance(raw_rules, dict):
            board.rules = PcbRule(
                clearance_mm=float(raw_rules.get("clearance_mm", 0.2)),
                min_track_width_mm=float(
                    raw_rules.get("min_track_width_mm", 0.2)),
                min_drill_mm=float(raw_rules.get("min_drill_mm", 0.3)),
                edge_clearance_mm=float(
                    raw_rules.get("edge_clearance_mm", 0.25)),
            )
        for raw in data.get("footprints", []):
            if not isinstance(raw, dict):
                continue
            pads = []
            for pad in raw.get("pads", []):
                if not isinstance(pad, dict):
                    continue
                number = int(pad.get("number", 0))
                pad_type = str(pad.get("pad_type", "tht"))
                default_layers = ('F.Cu',) if pad_type == 'smd' \
                    else ('F.Cu', 'B.Cu')
                raw_layers = pad.get("layers", default_layers)
                layers = tuple(str(layer) for layer in raw_layers) \
                    if isinstance(raw_layers, (list, tuple)) else default_layers
                pads.append(PcbPad(
                    number=number,
                    x_mm=float(pad.get("x_mm", 0.0)),
                    y_mm=float(pad.get("y_mm", 0.0)),
                    net=str(pad.get("net", "?")),
                    width_mm=float(pad.get("width_mm", 2.0)),
                    height_mm=float(pad.get("height_mm", 2.0)),
                    shape=str(pad.get(
                        "shape", "rect" if number == 1 else "circle")),
                    drill_mm=float(pad.get(
                        "drill_mm", 0.0 if pad_type == 'smd' else 1.0)),
                    pad_type=pad_type,
                    layers=layers,
                ))
            board.footprints.append(PcbFootprint(
                reference=str(raw.get("reference", "?")),
                component_type=str(raw.get("component_type", "")),
                value=str(raw.get("value", "")),
                footprint_name=str(raw.get("footprint_name", "")),
                x_mm=float(raw.get("x_mm", 0.0)),
                y_mm=float(raw.get("y_mm", 0.0)),
                angle=float(raw.get("angle", 0.0)),
                width_mm=float(raw.get("width_mm", 5.0)),
                height_mm=float(raw.get("height_mm", 5.0)),
                pads=pads,
                side=str(raw.get("side", "F.Cu")),
                courtyard_margin_mm=float(
                    raw.get("courtyard_margin_mm", 0.25)),
                silkscreen=[PcbSilk(
                    str(graphic['kind']),
                    tuple(tuple(float(v) for v in point) for point in graphic['points']),
                    float(graphic.get('width_mm', 0.15)))
                    for graphic in raw['silkscreen']]
                    if 'silkscreen' in raw else None,
            ))
            warning = board.footprints[-1].package_warning()
            if warning and warning not in board.warnings:
                board.warnings.append(warning)
        for raw in data.get("tracks", []):
            if not isinstance(raw, dict):
                continue
            points = []
            for point in raw.get("points", []):
                if isinstance(point, (list, tuple)) and len(point) >= 2:
                    points.append((float(point[0]), float(point[1])))
            board.tracks.append(PcbTrack(
                net=str(raw.get("net", "?")),
                layer=str(raw.get("layer", "F.Cu")),
                width_mm=float(raw.get("width_mm", 0.25)),
                points=points,
            ))
        for raw in data.get("vias", []):
            if not isinstance(raw, dict):
                continue
            raw_layers = raw.get("layers", ('F.Cu', 'B.Cu'))
            layers = tuple(str(layer) for layer in raw_layers) \
                if isinstance(raw_layers, (list, tuple)) else ('F.Cu', 'B.Cu')
            board.vias.append(PcbVia(
                x_mm=float(raw.get("x_mm", 0.0)),
                y_mm=float(raw.get("y_mm", 0.0)),
                net=str(raw.get("net", "?")),
                drill_mm=float(raw.get("drill_mm", 0.3)),
                diameter_mm=float(raw.get("diameter_mm", 0.7)),
                layers=layers,
            ))
        for raw in data.get("texts", []):
            if not isinstance(raw, dict):
                continue
            text = str(raw.get("text", ""))
            if not text:
                continue
            board.texts.append(PcbText(
                text=text,
                x_mm=float(raw.get("x_mm", 0.0)),
                y_mm=float(raw.get("y_mm", 0.0)),
                layer=str(raw.get("layer", "F.SilkS")),
                size_mm=float(raw.get("size_mm", 2.0)),
                width_mm=float(raw.get("width_mm", 0.3)),
                angle=float(raw.get("angle", 0.0)),
            ))
        # Reject NaN/Infinity before coordinates can reach Qt or the grid search.
        json.dumps(board.to_dict(), allow_nan=False)
        return board


def _pads(coords, width_mm=2.0, height_mm=2.0, drill_mm=1.0):
    return tuple(PadSpec(
        x_mm=x,
        y_mm=y,
        width_mm=width_mm,
        height_mm=height_mm,
        shape='rect' if index == 0 else 'circle',
        drill_mm=drill_mm,
    ) for index, (x, y) in enumerate(coords))


def _silk_box(width, height):
    x, y = width / 2, height / 2
    return PcbSilk('polyline', ((-x, -y), (x, -y), (x, y), (-x, y), (-x, -y)))


def _axial_silk(width, height, cathode=False):
    lines = [_silk_box(width, height),
             PcbSilk('polyline', ((-5.08, 0), (-width / 2, 0))),
             PcbSilk('polyline', ((width / 2, 0), (5.08, 0)))]
    if cathode:
        lines.append(PcbSilk('polyline', ((width / 2 - 0.6, -height / 2),
                                         (width / 2 - 0.6, height / 2))))
    return tuple(lines)


def _ic_silk(width, height):
    x, y = width / 2, height / 2
    radius = min(0.8, width / 4)
    notch = tuple((radius * math.cos(i * math.pi / 16),
                   -y + radius * math.sin(i * math.pi / 16)) for i in range(17))
    return (PcbSilk('polyline', ((-x, -y), (-x, y), (x, y), (x, -y))
                    + notch + ((-x, -y),)),)


def _inline(name: str, count: int, pitch: float = 2.54,
            width: float | None = None, height: float = 5.0,
            pad_size: float = 2.0, drill: float = 1.0) -> FootprintSpec:
    width = width if width is not None else max(5.0, (count - 1) * pitch + 2.0)
    start = -(count - 1) * pitch / 2
    return FootprintSpec(
        name,
        _pads(((start + i * pitch, 0.0) for i in range(count)),
              pad_size, pad_size, drill),
        width,
        height,
        (_silk_box(width, height),),
    )


def _dip8() -> FootprintSpec:
    # TI NE555, P (PDIP-8): 2.54 mm lead pitch, 7.62 mm row spacing.
    # https://www.ti.com/lit/ds/symlink/ne555.pdf
    coords = (
        (-3.81, -3.81), (-3.81, -1.27), (-3.81, 1.27), (-3.81, 3.81),
        (3.81, 3.81), (3.81, 1.27), (3.81, -1.27), (3.81, -3.81),
    )
    return FootprintSpec(
        "DIP-8 W7.62mm",
        _pads(coords, 2.0, 2.0, 0.9),
        # Printed silk is inset from the body so the side lines clear the pads.
        10.16, 10.0, _ic_silk(5.0, 9.6))


def _smd_two_pad(name: str, pitch: float, body_width: float,
                 body_height: float, pad_width: float,
                 pad_height: float) -> FootprintSpec:
    return FootprintSpec(
        name,
        (
            PadSpec(-pitch / 2, 0.0, pad_width, pad_height,
                    'roundrect', 0.0, 'smd', ('F.Cu',)),
            PadSpec(pitch / 2, 0.0, pad_width, pad_height,
                    'roundrect', 0.0, 'smd', ('F.Cu',)),
        ),
        body_width,
        body_height,
        (PcbSilk('polyline', ((-0.4, -body_height / 2 - 0.2),
                               (0.4, -body_height / 2 - 0.2))),
         PcbSilk('polyline', ((-0.4, body_height / 2 + 0.2),
                               (0.4, body_height / 2 + 0.2)))),
    )


def _soic8() -> FootprintSpec:
    # TI NE555 D0008A land-pattern example: 5.4 mm rows, 1.27 mm pitch.
    coords = (
        (-2.7, -1.905), (-2.7, -0.635),
        (-2.7, 0.635), (-2.7, 1.905),
        (2.7, 1.905), (2.7, 0.635),
        (2.7, -0.635), (2.7, -1.905),
    )
    pads = tuple(PadSpec(
        x_mm=x,
        y_mm=y,
        width_mm=1.55,
        height_mm=0.6,
        shape='roundrect',
        drill_mm=0.0,
        pad_type='smd',
        layers=('F.Cu',),
    ) for x, y in coords)
    return FootprintSpec('SOIC-8 1.27mm', pads, 7.0, 5.0, _ic_silk(3.2, 4.9))


FOOTPRINT_SPECS: Dict[str, FootprintSpec] = {
    "R": FootprintSpec(
        "R_Axial_P10.16mm",
        _pads(((-5.08, 0.0), (5.08, 0.0)), 2.0, 2.0, 1.0),
        12.0, 4.0, _axial_silk(6.3, 2.5)),
    "C": FootprintSpec(
        "C_Radial_P5.08mm",
        _pads(((-2.54, 0.0), (2.54, 0.0)), 2.2, 2.2, 1.0),
        7.0, 7.0, (PcbSilk('circle', ((-3.5, -3.5), (3.5, 3.5))),)),
    "L": FootprintSpec(
        "L_Axial_P10.16mm",
        _pads(((-5.08, 0.0), (5.08, 0.0)), 2.0, 2.0, 1.0),
        12.0, 4.0, _axial_silk(7.0, 3.5)),
    "D": FootprintSpec(
        "D_Axial_P10.16mm",
        _pads(((-5.08, 0.0), (5.08, 0.0)), 2.0, 2.0, 1.0),
        12.0, 4.0, _axial_silk(5.2, 2.7, cathode=True)),
    "LED": FootprintSpec(
        "LED_THT_D5.0mm",
        _pads(((-1.27, 0.0), (1.27, 0.0)), 2.0, 2.0, 0.9),
        6.0, 6.0, (PcbSilk('polyline', tuple(
            (2.5 * math.cos(math.radians(a)), 2.5 * math.sin(math.radians(a)))
            for a in range(30, 331, 5)) + ((2.5 * math.cos(math.pi / 6), 1.25),)),)),
    "POT": _inline("Potentiometer THT P5.08mm", 3, pitch=5.08,
                    width=10.0, height=10.0, pad_size=2.2, drill=1.0),
    "BJT_NPN": _inline("TO-92 generic", 3, width=7.62),
    "BJT_PNP": _inline("TO-92 generic", 3, width=7.62),
    "NMOS": _inline("TO-92 generic", 3, width=7.62),
    "PMOS": _inline("TO-92 generic", 3, width=7.62),
    "SPST": _inline("Switch 2-pin", 2, width=7.62),
    "SPDT": _inline("Switch 3-pin", 3, width=7.62),
    "SPDT3": _inline("Switch 3-pin", 3, width=7.62),
    "DPDT": FootprintSpec(
        "Switch 6-pin",
        _pads(((-3.81, -2.54), (-3.81, 0.0), (-3.81, 2.54),
               (3.81, -2.54), (3.81, 0.0), (3.81, 2.54)),
              2.0, 2.0, 1.0),
        10.16, 7.0),
    "RELAY": FootprintSpec(
        "Relay 4-pin",
        _pads(((-3.81, -2.54), (-3.81, 2.54),
               (3.81, -2.54), (3.81, 2.54)), 2.0, 2.0, 1.0),
        10.16, 7.0),
    "XFMR": FootprintSpec(
        "Transformer 4-pin",
        _pads(((-5.08, -2.54), (-5.08, 2.54),
               (5.08, -2.54), (5.08, 2.54)), 2.4, 2.4, 1.0),
        14.0, 8.0),
    "BRIDGE": FootprintSpec(
        "Bridge 4-pin",
        _pads(((-3.81, -3.81), (-3.81, 3.81),
               (3.81, -3.81), (3.81, 3.81)), 2.0, 2.0, 1.0),
        10.16, 10.16),
    "IC555": _dip8(),
}


FOOTPRINT_LIBRARY: Dict[str, FootprintSpec] = {
    spec.name: spec for spec in FOOTPRINT_SPECS.values()
}
FOOTPRINT_LIBRARY.update({
    'R_0805': _smd_two_pad('R_0805', 2.0, 2.0, 1.25, 1.1, 0.9),
    'C_0805': _smd_two_pad('C_0805', 2.0, 2.0, 1.25, 1.1, 0.9),
    'LED_0805': _smd_two_pad('LED_0805', 2.0, 2.0, 1.25, 1.1, 0.9),
    'SOIC-8 1.27mm': _soic8(),
})

# Las opciones se mantienen explícitas para no permitir que un componente
# reciba por accidente un footprint con otra cantidad de pines.
FOOTPRINT_OPTIONS: Dict[str, Tuple[str, ...]] = {
    component_type: (spec.name,)
    for component_type, spec in FOOTPRINT_SPECS.items()
}
FOOTPRINT_OPTIONS.update({
    'R': ('R_Axial_P10.16mm', 'R_0805'),
    'C': ('C_Radial_P5.08mm', 'C_0805'),
    'LED': ('LED_THT_D5.0mm', 'LED_0805'),
    'IC555': ('DIP-8 W7.62mm', 'SOIC-8 1.27mm'),
})

# Own land patterns: numbered terminals, not universal manufacturer pinouts.
# Existing generic names remain available so saved assignments do not migrate.
for component_types, name, coords, width, height, silk in (
        (('BJT_NPN', 'BJT_PNP', 'NMOS', 'PMOS'), 'Kirho_3pin_Dbody_P2.54',
         ((-2.54, 0), (0, 0), (2.54, 0)), 8, 6,
         (PcbSilk('polyline', tuple((3.5 * math.cos(i * math.pi / 24),
                                    -3.5 * math.sin(i * math.pi / 24))
                                   for i in range(25)) + ((3.5, 0),)),)),
        (('POT',), 'Kirho_Pot_2terminal_P5.08',
         ((-2.54, 0), (2.54, 0)), 8, 8,
         (_silk_box(8, 8), PcbSilk('circle', ((-1.5, -1.5), (1.5, 1.5))),
          PcbSilk('polyline', ((-1, 0), (1, 0))))),
        (('SPST',), 'Kirho_Switch_2pin_P5.08',
         ((-2.54, 0), (2.54, 0)), 8, 6,
         (_silk_box(8, 6), _silk_box(2, 3))),
        (('SPDT', 'SPDT3'), 'Kirho_Slide_3pin_P2.54',
         ((-2.54, 0), (0, 0), (2.54, 0)), 10, 6,
         (_silk_box(10, 6), PcbSilk('polyline', ((-2, -2), (2, -2))))),
        (('DPDT',), 'Kirho_Slide_6pin_P2.54_R5.08',
         ((-2.54, -2.54), (-2.54, 0), (-2.54, 2.54),
          (2.54, -2.54), (2.54, 0), (2.54, 2.54)), 9, 10,
         (_silk_box(9, 10), _silk_box(1.5, 3))),
        (('RELAY',), 'Kirho_Relay_4pin_P10.16_R7.62',
         ((-5.08, -3.81), (-5.08, 3.81), (5.08, -3.81), (5.08, 3.81)), 14, 11,
         (_silk_box(14, 11), PcbSilk('polyline', ((-6, -4), (-6, -2))))),
        (('XFMR',), 'Kirho_Transformer_4pin_P15.24_R10.16',
         ((-7.62, -5.08), (-7.62, 5.08), (7.62, -5.08), (7.62, 5.08)), 20, 16,
         (_silk_box(20, 16), _silk_box(8, 13))),
        (('BRIDGE',), 'Kirho_Bridge_Inline4_P2.54',
         ((-3.81, 0), (-1.27, 0), (1.27, 0), (3.81, 0)), 11, 5,
         (_silk_box(11, 5), PcbSilk('polyline', ((-4.5, -1.5), (-3.5, -1.5))))),
):
    spec = FootprintSpec(name, _pads(coords, 1.8, 1.8, 0.8), width, height, silk)
    FOOTPRINT_LIBRARY[name] = spec
    for component_type in component_types:
        FOOTPRINT_OPTIONS[component_type] += (name,)


def footprint_names_for_type(component_type: str) -> Tuple[str, ...]:
    """Devuelve los packages compatibles con un tipo de esquemático."""
    return tuple(
        name for name in FOOTPRINT_OPTIONS.get(component_type, ())
        if name in FOOTPRINT_LIBRARY
    )


def default_footprint_name(component_type: str) -> str:
    spec = FOOTPRINT_SPECS.get(component_type)
    return spec.name if spec is not None else ''


def resolve_footprint(component_type: str,
                      footprint_name: str = '') -> FootprintSpec | None:
    """Resuelve una selección o devuelve el package por defecto."""
    options = footprint_names_for_type(component_type)
    if footprint_name in options:
        return FOOTPRINT_LIBRARY[footprint_name]
    default_name = default_footprint_name(component_type)
    return FOOTPRINT_LIBRARY.get(default_name)

# Componentes del esquemático que no representan una pieza colocable.
IGNORED_COMPONENT_TYPES = {
    "GND", "NODE", "NET_LABEL_IN", "NET_LABEL_OUT", "PORT", "SUBCKT",
    "V", "VAC", "I", "FGEN", "OSC", "MULTIMETER", "LOGIC_STATE", "CLK",
    "KEYPAD4X4",
}


def _item_value(item) -> str:
    from kirho.ui.style import format_si_value

    # Is, beta and similar simulation parameters are not BOM values.
    if item.comp_type not in ('R', 'C', 'L', 'POT'):
        return item.comp_type
    value = getattr(item, "value", 0.0)
    unit = getattr(item, "unit", "")
    return format_si_value(value, unit)


def build_pcb_board(scene, placement_scale: float = 0.10,
                    margin_mm: float = 10.0) -> PcbBoard:
    """Convierte los componentes físicos de una escena en una placa.

    La posición inicial es una heurística: ajusta las coordenadas del
    esquemático a milímetros para dar un punto de partida editable.
    """
    board = PcbBoard()
    pin_net = scene.extract_netlist()
    candidates = []
    positions = []

    for item in scene.components:
        component_type = getattr(item, "comp_type", "")
        if (component_type in IGNORED_COMPONENT_TYPES
                or getattr(item, 'external_backend', '')
                or isinstance(getattr(item, 'external_definition', None), dict)):
            continue
        requested_footprint = getattr(item, 'footprint_name', '')
        spec = resolve_footprint(component_type, requested_footprint)
        if spec is None:
            board.warnings.append(
                f"{item.name}: tipo {component_type} no tiene footprint.")
            continue
        if requested_footprint and requested_footprint != spec.name:
            board.warnings.append(
                f"{item.name}: footprint {requested_footprint} no es compatible; "
                f"se usará {spec.name}.")
        pin_count = len(item.all_pin_positions_scene())
        if pin_count != len(spec.pads):
            board.warnings.append(
                f"{item.name}: {pin_count} pines esquemáticos, "
                f"pero el footprint necesita {len(spec.pads)}.")
            continue
        candidates.append((item, spec))
        positions.append(item.pos())

    if not candidates:
        board.warnings.append("No hay componentes físicos con footprint asignado.")
        return board

    if any(spec.name.startswith('Kirho_') for item, spec in candidates):
        board.warnings.append('Kirho nominal patterns: verify dimensions and terminal mapping '
                              'against the actual parts before manufacture.')

    min_x = min(point.x() for point in positions)
    min_y = min(point.y() for point in positions)
    max_x = max(point.x() for point in positions)
    max_y = max(point.y() for point in positions)
    board.width_mm = max(100.0, (max_x - min_x) * placement_scale + 2 * margin_mm)
    board.height_mm = max(80.0, (max_y - min_y) * placement_scale + 2 * margin_mm)
    board.outline = (0.0, 0.0, board.width_mm, board.height_mm)

    # ponytail: el ajuste esquemático→mm es sólo una colocación inicial;
    # guardar coordenadas PCB independientes será necesario para producción.
    for item, spec in candidates:
        x_mm = margin_mm + (item.pos().x() - min_x) * placement_scale
        y_mm = margin_mm + (item.pos().y() - min_y) * placement_scale
        manual_nodes = (list(getattr(item, 'timer_nodes', []))
                        if item.comp_type == 'IC555' else
                        [getattr(item, f'node{index}', '')
                         for index in range(1, len(spec.pads) + 1)])
        manual_nodes += [''] * (len(spec.pads) - len(manual_nodes))
        pads = [PcbPad(
            number=index,
            x_mm=pad.x_mm,
            y_mm=pad.y_mm,
            net=manual_nodes[index - 1].strip() or pin_net.get(
                f"{item.name}__p{index}", "?"),
            width_mm=pad.width_mm,
            height_mm=pad.height_mm,
            shape=pad.shape,
            drill_mm=pad.drill_mm,
            pad_type=pad.pad_type,
            layers=pad.layers,
        ) for index, pad in enumerate(spec.pads, 1)]
        board.footprints.append(PcbFootprint(
            reference=item.name,
            component_type=item.comp_type,
            value=_item_value(item),
            footprint_name=spec.name,
            x_mm=x_mm,
            y_mm=y_mm,
            angle=float(getattr(item, "_angle", 0)),
            width_mm=spec.width_mm,
            height_mm=spec.height_mm,
            pads=pads,
            side='F.Cu',
            courtyard_margin_mm=0.25,
        ))
    return board
