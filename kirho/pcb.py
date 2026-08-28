"""Modelo mínimo de PCB y conversión desde una hoja esquemática.

La placa todavía no enruta ni conoce librerías externas: representa
footprints genéricos, pads y redes para que el editor PCB pueda trabajar con
datos propios de Kirho.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Tuple


@dataclass(frozen=True)
class FootprintSpec:
    name: str
    pads: Tuple[Tuple[float, float], ...]
    width_mm: float
    height_mm: float


@dataclass
class PcbPad:
    number: int
    x_mm: float
    y_mm: float
    net: str


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


@dataclass
class PcbBoard:
    width_mm: float = 100.0
    height_mm: float = 80.0
    footprints: List[PcbFootprint] = field(default_factory=list)
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
            "width_mm": self.width_mm,
            "height_mm": self.height_mm,
            "outline": list(self.outline) if self.outline is not None else None,
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
                    "pads": [pad.__dict__ for pad in footprint.pads],
                }
                for footprint in self.footprints
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
        for raw in data.get("footprints", []):
            if not isinstance(raw, dict):
                continue
            pads = [PcbPad(
                number=int(pad.get("number", 0)),
                x_mm=float(pad.get("x_mm", 0.0)),
                y_mm=float(pad.get("y_mm", 0.0)),
                net=str(pad.get("net", "?")),
            ) for pad in raw.get("pads", []) if isinstance(pad, dict)]
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
            ))
        return board


def _inline(name: str, count: int, pitch: float = 2.54,
            width: float | None = None, height: float = 5.0) -> FootprintSpec:
    width = width if width is not None else max(5.0, (count - 1) * pitch + 2.0)
    start = -(count - 1) * pitch / 2
    return FootprintSpec(name, tuple((start + i * pitch, 0.0)
                                     for i in range(count)), width, height)


def _dip8() -> FootprintSpec:
    return FootprintSpec(
        "DIP-8",
        ((-3.81, -8.89), (-3.81, -2.54), (-3.81, 3.81), (-3.81, 10.16),
         (3.81, 10.16), (3.81, 3.81), (3.81, -2.54), (3.81, -8.89)),
        10.16, 25.4)


FOOTPRINT_SPECS: Dict[str, FootprintSpec] = {
    "R": FootprintSpec("Axial 10.16 mm", ((-5.08, 0.0), (5.08, 0.0)), 12.0, 4.0),
    "C": FootprintSpec("Radial 2-pin", ((-2.54, 0.0), (2.54, 0.0)), 7.0, 7.0),
    "L": FootprintSpec("Axial 10.16 mm", ((-5.08, 0.0), (5.08, 0.0)), 12.0, 4.0),
    "D": FootprintSpec("Diode axial 10.16 mm", ((-5.08, 0.0), (5.08, 0.0)), 12.0, 4.0),
    "LED": FootprintSpec("LED 5 mm", ((-1.27, 0.0), (1.27, 0.0)), 6.0, 6.0),
    "BJT_NPN": _inline("TO-92 generic", 3, width=7.62),
    "BJT_PNP": _inline("TO-92 generic", 3, width=7.62),
    "NMOS": _inline("TO-92 generic", 3, width=7.62),
    "PMOS": _inline("TO-92 generic", 3, width=7.62),
    "SPST": _inline("Switch 2-pin", 2, width=7.62),
    "SPDT": _inline("Switch 3-pin", 3, width=7.62),
    "SPDT3": _inline("Switch 3-pin", 3, width=7.62),
    "DPDT": FootprintSpec(
        "Switch 6-pin",
        ((-3.81, -2.54), (-3.81, 0.0), (-3.81, 2.54),
         (3.81, -2.54), (3.81, 0.0), (3.81, 2.54)),
        10.16, 7.0),
    "RELAY": FootprintSpec(
        "Relay 4-pin",
        ((-3.81, -2.54), (-3.81, 2.54), (3.81, -2.54), (3.81, 2.54)),
        10.16, 7.0),
    "XFMR": FootprintSpec(
        "Transformer 4-pin",
        ((-5.08, -2.54), (-5.08, 2.54), (5.08, -2.54), (5.08, 2.54)),
        14.0, 8.0),
    "BRIDGE": FootprintSpec(
        "Bridge 4-pin",
        ((-3.81, -3.81), (-3.81, 3.81), (3.81, -3.81), (3.81, 3.81)),
        10.16, 10.16),
    "IC555": _dip8(),
}

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
        spec = FOOTPRINT_SPECS.get(component_type)
        if spec is None:
            board.warnings.append(
                f"{item.name}: tipo {component_type} no tiene footprint.")
            continue
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
        pads = [
            PcbPad(index, x, y, pin_net.get(f"{item.name}__p{index}", "?"))
            for index, (x, y) in enumerate(spec.pads, 1)
        ]
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
        ))
    return board
