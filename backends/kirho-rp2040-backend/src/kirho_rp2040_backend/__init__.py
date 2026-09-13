"""Definición de placa RP2040 para Kirho."""
from __future__ import annotations

import json
from pathlib import Path


_BOARD_PATH = Path(__file__).parent / "data" / "board.json"


def load_board_definition() -> dict:
    """Carga y valida la definición de pines incluida en el paquete."""
    definition = json.loads(_BOARD_PATH.read_text(encoding="utf-8"))
    pins = definition.get("pins")
    if (definition.get("schema_version") != 1
            or not isinstance(pins, list)
            or len(pins) != definition.get("pin_count")):
        raise ValueError("Invalid RP2040 board definition")
    numbers = [pin.get("number") for pin in pins]
    if numbers != list(range(1, len(pins) + 1)):
        raise ValueError("RP2040 physical pin numbers must be consecutive")
    return definition


class Rp2040Backend:
    backend_id = "rp2040"
    display_name = "RP2040 Dev Board"
    version = "0.1.0"

    def metadata(self) -> dict:
        return {
            "id": self.backend_id,
            "name": self.display_name,
            "version": self.version,
            "status": "board-definition",
            "board_id": "rp2040-40-pin",
        }

    def board_definition(self) -> dict:
        return load_board_definition()

    def schematic_components(self) -> list[dict]:
        """Publica el símbolo; la aplicación aún no crea su footprint PCB."""
        definition = self.board_definition()
        return [{
            "type": "RP2040",
            "label": self.display_name,
            "symbol": "▣40",
            "reference_prefix": "PICO",
            "board_id": definition["id"],
            "board_definition": definition,
        }]

    def create_runtime(self):
        """Crea el runtime local por eventos; no conecta hardware físico."""
        from .runtime import Rp2040Runtime
        return Rp2040Runtime(self.board_definition())

    def create_arduino_api(self):
        """Crea el adaptador Arduino sobre un runtime nuevo."""
        from .arduino_api import ArduinoApi
        return ArduinoApi(self.create_runtime())

    def create_micropython_runner(self, runtime=None):
        """Crea el runner de compatibilidad MicroPython para este runtime."""
        from .runner import MicroPythonRunner
        return MicroPythonRunner(runtime or self.create_runtime())

    def create_component_controller(self, component, context):
        """Publica el panel del componente para que Kirho solo lo aloje."""
        from .component_controller import Rp2040ComponentController
        return Rp2040ComponentController(component, context)

    def load_firmware(self, path):
        """Carga y valida firmware .py o .uf2 para este backend."""
        from .firmware import load_firmware
        return load_firmware(path)


def create_backend() -> Rp2040Backend:
    """Crea la instancia que Kirho cargará bajo demanda."""
    return Rp2040Backend()
