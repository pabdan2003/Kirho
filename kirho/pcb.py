"""Modelo de PCB y conversión desde una hoja esquemática.

Las coordenadas se guardan siempre en milímetros. Los footprints son land
patterns editables: describen pads, taladros, capas y una zona de courtyard,
en lugar de reducir cada componente a un rectángulo visual.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Tuple


MM_PER_MIL = 0.0254


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
class FootprintSpec:
    name: str
    pads: Tuple[PadSpec, ...]
    width_mm: float
    height_mm: float


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


@dataclass
class PcbBoard:
    width_mm: float = 100.0
    height_mm: float = 80.0
    footprints: List[PcbFootprint] = field(default_factory=list)
    tracks: List[PcbTrack] = field(default_factory=list)
    vias: List[PcbVia] = field(default_factory=list)
    layers: List[PcbLayer] = field(default_factory=_default_layers)
    rules: PcbRule = field(default_factory=PcbRule)
    warnings: List[str] = field(default_factory=list)
    outline: Tuple[float, float, float, float] | None = None

    def pads_by_net(self) -> Dict[str, List[Tuple[PcbFootprint, PcbPad]]]:
        result: Dict[str, List[Tuple[PcbFootprint, PcbPad]]] = {}
        for footprint in self.footprints:
            for pad in footprint.pads:
                if pad.net and pad.net != "?":
                    result.setdefault(pad.net, []).append((footprint, pad))
        return result

    def to_dict(self) -> dict:
        return {
            "units": "mm",
            "width_mm": self.width_mm,
            "height_mm": self.height_mm,
            "outline": list(self.outline) if self.outline is not None else None,
            "layers": [
                {
                    "name": layer.name,
                    "layer_type": layer.layer_type,
                    "enabled": layer.enabled,
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
        }

    @classmethod
    def from_dict(cls, data: dict | None) -> "PcbBoard | None":
        if not isinstance(data, dict):
            return None
        board = cls(
            width_mm=float(data.get("width_mm", 100.0)),
            height_mm=float(data.get("height_mm", 80.0)),
        )
        raw_outline = data.get("outline")
        if isinstance(raw_outline, (list, tuple)) and len(raw_outline) == 4:
            board.outline = tuple(float(value) for value in raw_outline)
        raw_layers = data.get("layers")
        if isinstance(raw_layers, list):
            board.layers = [PcbLayer(
                name=str(layer.get("name", "")),
                layer_type=str(layer.get("layer_type", "copper")),
                enabled=bool(layer.get("enabled", True)),
            ) for layer in raw_layers if isinstance(layer, dict)
            and layer.get("name")]
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
            ))
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
    )


def _dip8() -> FootprintSpec:
    coords = (
        (-3.81, -8.89), (-3.81, -2.54), (-3.81, 3.81), (-3.81, 10.16),
        (3.81, 10.16), (3.81, 3.81), (3.81, -2.54), (3.81, -8.89),
    )
    return FootprintSpec(
        "DIP-8 W7.62mm",
        _pads(coords, 2.0, 2.0, 0.9),
        10.16, 25.4)


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
    )


def _soic8() -> FootprintSpec:
    coords = (
        (-1.905, -1.905), (-1.905, -0.635),
        (-1.905, 0.635), (-1.905, 1.905),
        (1.905, 1.905), (1.905, 0.635),
        (1.905, -0.635), (1.905, -1.905),
    )
    pads = tuple(PadSpec(
        x_mm=x,
        y_mm=y,
        width_mm=1.5,
        height_mm=0.6,
        shape='roundrect',
        drill_mm=0.0,
        pad_type='smd',
        layers=('F.Cu',),
    ) for x, y in coords)
    return FootprintSpec('SOIC-8 1.27mm', pads, 6.0, 5.0)


FOOTPRINT_SPECS: Dict[str, FootprintSpec] = {
    "R": FootprintSpec(
        "R_Axial_P10.16mm",
        _pads(((-5.08, 0.0), (5.08, 0.0)), 2.0, 2.0, 1.0),
        12.0, 4.0),
    "C": FootprintSpec(
        "C_Radial_P5.08mm",
        _pads(((-2.54, 0.0), (2.54, 0.0)), 2.2, 2.2, 1.0),
        7.0, 7.0),
    "L": FootprintSpec(
        "L_Axial_P10.16mm",
        _pads(((-5.08, 0.0), (5.08, 0.0)), 2.0, 2.0, 1.0),
        12.0, 4.0),
    "D": FootprintSpec(
        "D_Axial_P10.16mm",
        _pads(((-5.08, 0.0), (5.08, 0.0)), 2.0, 2.0, 1.0),
        12.0, 4.0),
    "LED": FootprintSpec(
        "LED_THT_D5.0mm",
        _pads(((-1.27, 0.0), (1.27, 0.0)), 2.0, 2.0, 0.9),
        6.0, 6.0),
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
}


def _item_value(item) -> str:
    value = getattr(item, "value", 0.0)
    unit = getattr(item, "unit", "")
    return f"{value:g} {unit}".strip()


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
        if component_type in IGNORED_COMPONENT_TYPES:
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

    min_x = min(point.x() for point in positions)
    min_y = min(point.y() for point in positions)
    max_x = max(point.x() for point in positions)
    max_y = max(point.y() for point in positions)
    board.width_mm = max(100.0, (max_x - min_x) * placement_scale + 2 * margin_mm)
    board.height_mm = max(80.0, (max_y - min_y) * placement_scale + 2 * margin_mm)

    # ponytail: el ajuste esquemático→mm es sólo una colocación inicial;
    # guardar coordenadas PCB independientes será necesario para producción.
    for item, spec in candidates:
        x_mm = margin_mm + (item.pos().x() - min_x) * placement_scale
        y_mm = margin_mm + (item.pos().y() - min_y) * placement_scale
        pads = [PcbPad(
            number=index,
            x_mm=pad.x_mm,
            y_mm=pad.y_mm,
            net=pin_net.get(f"{item.name}__p{index}", "?"),
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
