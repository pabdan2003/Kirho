"""Panel y propiedades del componente RP2040 para la UI de Kirho."""
from __future__ import annotations

from pathlib import Path


class Rp2040ComponentController:
    """Controlador opcional que Kirho aloja sin conocer el RP2040."""

    def __init__(self, component, context: dict):
        from PyQt6.QtWidgets import (
            QHBoxLayout, QLabel, QPushButton, QComboBox, QVBoxLayout, QWidget,
        )

        self.component = component
        self.context = context
        self.runtime = context.get("runtime")
        self.tr = context.get("tr", lambda text: text)
        self._load_firmware = context.get("load_firmware")
        self._open_file = context.get("open_file")
        self._warning = context.get("warning")
        self._status_message = context.get("status_message")
        self.firmware = getattr(component, "external_firmware_image", None)
        if self.firmware is None:
            path = getattr(component, "external_firmware_path", "")
            if path and callable(self._load_firmware):
                self.firmware = self._load_firmware(path)
                if self.firmware is not None:
                    component.external_firmware_image = self.firmware

        self.widget = QWidget(context.get("parent"))
        layout = QVBoxLayout(self.widget)
        layout.setContentsMargins(0, 4, 0, 4)
        title = QLabel(self.tr("RP2040 GPIO TEST"))
        layout.addWidget(title)

        self.gpio_combo = QComboBox()
        layout.addWidget(self.gpio_combo)
        buttons = QHBoxLayout()
        low_button = QPushButton(self.tr("Set LOW"))
        high_button = QPushButton(self.tr("Set HIGH"))
        low_button.clicked.connect(lambda: self.set_gpio(0))
        high_button.clicked.connect(lambda: self.set_gpio(1))
        buttons.addWidget(low_button)
        buttons.addWidget(high_button)
        layout.addLayout(buttons)

        load_button = QPushButton(self.tr("Load firmware…"))
        load_button.clicked.connect(self.load_firmware)
        layout.addWidget(load_button)
        self.firmware_status = QLabel(self._firmware_status_text())
        layout.addWidget(self.firmware_status)
        self.test_status = QLabel(self.tr("No GPIO test signal"))
        layout.addWidget(self.test_status)

        self._populate_gpios()

    def _populate_gpios(self):
        self.gpio_combo.clear()
        definition = getattr(self.component, "external_definition", None) or {}
        pins = [pin for pin in definition.get("pins", [])
                if isinstance(pin, dict) and isinstance(pin.get("gpio"), int)]
        for pin in pins:
            name = pin.get("name") or f"GP{pin['gpio']}"
            self.gpio_combo.addItem(
                f"{name}  (GPIO{pin['gpio']})", pin["gpio"])

    def set_gpio(self, value: int):
        if self.gpio_combo.currentIndex() < 0:
            return
        gpio = int(self.gpio_combo.currentData())
        if self.runtime is not None:
            self.runtime.pin_mode(gpio, "OUTPUT")
            self.runtime.set_gpio(gpio, value)
        else:
            emit = self.context.get("emit_runtime_event")
            if callable(emit):
                emit({"type": "gpio", "gpio": gpio, "value": value,
                      "source": "firmware"})
        self.test_status.setText(
            self.tr("GPIO{gpio}: {state}").format(
                gpio=gpio, state="HIGH" if value else "LOW"))

    def load_firmware(self):
        if not callable(self._open_file) or not callable(self._load_firmware):
            return
        path = self._open_file(
            self.tr("Load RP2040 firmware"),
            self.tr("RP2040 firmware (*.py *.uf2)"))
        if not path:
            return
        image = self._load_firmware(path)
        if image is None:
            if callable(self._warning):
                self._warning(
                    self.tr("Invalid firmware"),
                    self.tr("The selected file is not a valid RP2040 .py or .uf2 firmware."))
            return
        self.firmware = image
        self.component.external_firmware_path = image.path
        self.component.external_firmware_image = image
        self.firmware_status.setText(
            self._firmware_status_text())
        if callable(self._status_message):
            self._status_message(
                self.tr("RP2040 firmware loaded: {path}").format(path=path))

    def _firmware_status_text(self):
        if self.firmware is None:
            return self.tr("No firmware loaded")
        status = self.tr("Loaded: {name} ({format})").format(
            name=Path(self.firmware.path).name,
            format=self.firmware.format.upper())
        if self.firmware.format == "py":
            return status + self.tr(" — press SIMULATE to run")
        return status + self.tr(" — execution is not available yet")

    def property_rows(self, _netlist=None):
        definition = getattr(self.component, "external_definition", None) or {}
        rows = [
            (self.tr("Board"), definition.get("name", self.tr("External board"))),
            (self.tr("Pins"), str(len(definition.get("pins", [])))),
            (self.tr("Status"), self.tr(
                "Schematic symbol only. PCB footprint is not available yet.")),
        ]
        if self.firmware is not None:
            rows.append((self.tr("Firmware"), Path(self.firmware.path).name))
        return rows
