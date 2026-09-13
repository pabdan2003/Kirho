"""Adaptador mínimo de la API Arduino para el runtime RP2040."""
from __future__ import annotations

import math

from .runtime import RuntimeProtocolError


HIGH = 1
LOW = 0
INPUT = "INPUT"
OUTPUT = "OUTPUT"
INPUT_PULLUP = "INPUT_PULLUP"
INPUT_PULLDOWN = "INPUT_PULLDOWN"


class ArduinoApi:
    """Traduce llamadas Arduino a operaciones deterministas del runtime.

    ``delay`` solo avanza el reloj simulado; no bloquea el proceso anfitrión.
    """

    def __init__(self, runtime):
        self.runtime = runtime
        self.time_us = 0

    def pinMode(self, pin, mode) -> None:
        self.runtime.pin_mode(pin, mode)

    def digitalWrite(self, pin, value):
        return self.runtime.set_gpio(pin, value, time_us=self.time_us)

    def digitalRead(self, pin) -> int:
        return self.runtime.read_gpio(pin)

    def delay(self, milliseconds) -> None:
        try:
            milliseconds = float(milliseconds)
        except (TypeError, ValueError) as exc:
            raise RuntimeProtocolError("delay must be a finite number") from exc
        if not math.isfinite(milliseconds) or milliseconds < 0:
            raise RuntimeProtocolError("delay must be a finite non-negative number")
        self.time_us += round(milliseconds * 1000)
