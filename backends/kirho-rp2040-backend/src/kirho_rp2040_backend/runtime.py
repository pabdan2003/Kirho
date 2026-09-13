"""Runtime ligero por eventos para la placa RP2040.

Este módulo define el contrato que usará Kirho para comunicarse con un
runtime de firmware. Todavía no ejecuta ELF ni C/C++; mantiene el estado GPIO
y transporta eventos por JSON Lines para que el emulador futuro pueda
reemplazarlo sin cambiar la integración con el esquemático.
"""
from __future__ import annotations

import json
import sys
from typing import TextIO


PROTOCOL_VERSION = 1
MAX_MESSAGE_BYTES = 64 * 1024
GPIO_MODES = frozenset({"INPUT", "OUTPUT", "INPUT_PULLUP", "INPUT_PULLDOWN"})


class RuntimeProtocolError(ValueError):
    """Solicitud inválida para el protocolo del runtime."""


def _as_gpio(value) -> int:
    try:
        gpio = int(value)
    except (TypeError, ValueError) as exc:
        raise RuntimeProtocolError("gpio must be an integer") from exc
    if gpio < 0 or gpio > 29:
        raise RuntimeProtocolError("gpio must be between 0 and 29")
    return gpio


def _as_logic(value) -> int:
    if isinstance(value, bool):
        return int(value)
    if value in (0, 1):
        return int(value)
    raise RuntimeProtocolError("value must be 0 or 1")


def _as_mode(value) -> str:
    mode = str(value).strip().upper()
    if mode not in GPIO_MODES:
        allowed = ", ".join(sorted(GPIO_MODES))
        raise RuntimeProtocolError(f"mode must be one of: {allowed}")
    return mode


class Rp2040Runtime:
    """Estado GPIO determinista y basado en eventos.

    ``set_gpio`` representa una salida producida por el firmware y
    ``set_input`` representa una entrada producida por el esquemático. Ambos
    generan un evento solo cuando el valor cambia.
    """

    def __init__(self, board_definition: dict | None = None):
        self.board_definition = board_definition or {}
        self.gpio_nets: dict[int, str] = {}
        self.gpio_values: dict[int, int] = {}
        self.gpio_modes: dict[int, str] = {}
        self.pwm_values: dict[int, dict] = {}
        self.adc_values: dict[int, int] = {}
        self._listeners = []
        self.reset()

    def add_listener(self, listener) -> None:
        if callable(listener):
            self._listeners.append(listener)

    def configure(self, gpio_nets: dict | None = None) -> dict:
        if gpio_nets is not None and not isinstance(gpio_nets, dict):
            raise RuntimeProtocolError("gpio_nets must be an object")
        configured = {}
        for raw_gpio, raw_net in (gpio_nets or {}).items():
            gpio = self._resolve_gpio(raw_gpio)
            net = str(raw_net).strip()
            if not net:
                raise RuntimeProtocolError("net names cannot be empty")
            self.gpio_nets[gpio] = net
            configured[str(gpio)] = net
        return {"gpio_nets": configured}

    def reset(self) -> None:
        self.gpio_values = {gpio: 0 for gpio in self._available_gpios()}
        self.gpio_modes = {gpio: "INPUT" for gpio in self.gpio_values}
        self.pwm_values = {}
        self.adc_values = {gpio: 0 for gpio in self.gpio_values}

    def pin_mode(self, gpio, mode) -> dict:
        gpio = self._resolve_gpio(gpio)
        if gpio not in self.gpio_values:
            raise RuntimeProtocolError(f"gpio {gpio} is not available on this board")
        mode = _as_mode(mode)
        self.gpio_modes[gpio] = mode
        event = {
            "type": "gpio_mode",
            "protocol": PROTOCOL_VERSION,
            "gpio": gpio,
            "mode": mode,
        }
        for listener in tuple(self._listeners):
            listener(event)
        return event

    def set_gpio(self, gpio, value, time_us: int = 0) -> dict | None:
        return self._set_value(gpio, value, "firmware", time_us)

    def set_input(self, gpio, value, time_us: int = 0) -> dict | None:
        return self._set_value(gpio, value, "schematic", time_us)

    def read_gpio(self, gpio) -> int:
        gpio = self._resolve_gpio(gpio)
        if gpio not in self.gpio_values:
            raise RuntimeProtocolError(f"gpio {gpio} is not available on this board")
        return self.gpio_values[gpio]

    def set_pwm(self, gpio, frequency_hz, duty_u16, time_us: int = 0) -> dict | None:
        gpio = self._resolve_gpio(gpio)
        if gpio not in self.gpio_values:
            raise RuntimeProtocolError(f"gpio {gpio} is not available on this board")
        try:
            frequency_hz = float(frequency_hz)
            duty_u16 = int(duty_u16)
        except (TypeError, ValueError) as exc:
            raise RuntimeProtocolError("PWM frequency and duty must be numeric") from exc
        if frequency_hz <= 0 or not frequency_hz < float("inf"):
            raise RuntimeProtocolError("PWM frequency must be positive and finite")
        if duty_u16 < 0 or duty_u16 > 65535:
            raise RuntimeProtocolError("PWM duty_u16 must be between 0 and 65535")
        state = {"frequency_hz": frequency_hz, "duty_u16": duty_u16}
        if self.pwm_values.get(gpio) == state:
            return None
        self.pwm_values[gpio] = state
        event = {
            "type": "pwm",
            "protocol": PROTOCOL_VERSION,
            "gpio": gpio,
            "frequency_hz": frequency_hz,
            "duty_u16": duty_u16,
            "source": "firmware",
            "time_us": max(0, int(time_us)),
        }
        if gpio in self.gpio_nets:
            event["net"] = self.gpio_nets[gpio]
        for listener in tuple(self._listeners):
            listener(event)
        return event

    def set_adc_input(self, gpio, value) -> None:
        gpio = self._resolve_gpio(gpio)
        if gpio not in self.adc_values:
            raise RuntimeProtocolError(f"gpio {gpio} is not available on this board")
        try:
            value = int(value)
        except (TypeError, ValueError) as exc:
            raise RuntimeProtocolError("ADC value must be an integer") from exc
        if value < 0 or value > 65535:
            raise RuntimeProtocolError("ADC value must be between 0 and 65535")
        self.adc_values[gpio] = value

    def read_adc(self, gpio) -> int:
        gpio = self._resolve_gpio(gpio)
        if gpio not in self.adc_values:
            raise RuntimeProtocolError(f"gpio {gpio} is not available on this board")
        return self.adc_values[gpio]

    def snapshot(self) -> dict:
        return {
            "type": "snapshot",
            "protocol": PROTOCOL_VERSION,
            "gpio": {str(gpio): value
                     for gpio, value in sorted(self.gpio_values.items())},
            "modes": {str(gpio): mode
                      for gpio, mode in sorted(self.gpio_modes.items())},
            "pwm": {str(gpio): state
                    for gpio, state in sorted(self.pwm_values.items())},
            "adc": {str(gpio): value
                    for gpio, value in sorted(self.adc_values.items())},
        }

    def _resolve_gpio(self, value) -> int:
        try:
            return _as_gpio(value)
        except RuntimeProtocolError:
            name = str(value).strip().upper()
            for prefix in ("GPIO", "GP"):
                suffix = name[len(prefix):] if name.startswith(prefix) else ""
                if suffix.isdigit():
                    return _as_gpio(suffix)
            for pin in self._board_pins():
                aliases = pin.get("aliases", [])
                names = [pin.get("name"), *aliases]
                if name in {str(candidate).upper() for candidate in names}:
                    if isinstance(pin.get("gpio"), int):
                        return pin["gpio"]
            raise RuntimeProtocolError(f"unknown RP2040 pin: {value!r}")

    def _board_pins(self) -> list[dict]:
        pins = list(self.board_definition.get("pins", []))
        pins.extend(self.board_definition.get("internal_pins", []))
        return [pin for pin in pins if isinstance(pin, dict)]

    def _available_gpios(self) -> list[int]:
        gpios = {
            int(pin["gpio"])
            for pin in self._board_pins()
            if isinstance(pin.get("gpio"), int)
        }
        return sorted(gpios or range(30))

    def _set_value(self, gpio, value, source: str, time_us: int) -> dict | None:
        gpio = self._resolve_gpio(gpio)
        if gpio not in self.gpio_values:
            raise RuntimeProtocolError(f"gpio {gpio} is not available on this board")
        value = _as_logic(value)
        previous = self.gpio_values[gpio]
        self.gpio_values[gpio] = value
        if previous == value:
            return None
        event = {
            "type": "gpio",
            "protocol": PROTOCOL_VERSION,
            "gpio": gpio,
            "value": value,
            "source": source,
            "time_us": max(0, int(time_us)),
        }
        if gpio in self.gpio_nets:
            event["net"] = self.gpio_nets[gpio]
        for listener in tuple(self._listeners):
            listener(event)
        return event


def serve(stdin: TextIO = sys.stdin, stdout: TextIO = sys.stdout,
          board_definition: dict | None = None) -> None:
    """Sirve el protocolo JSON Lines sobre stdin/stdout.

    Mensajes aceptados: ``ping``, ``configure``, ``pin_mode``, ``set_gpio``,
    ``set_input``, ``set_pwm``, ``read_gpio``, ``read_adc``, ``snapshot``,
    ``reset`` y ``stop``. Las respuestas y eventos son una línea JSON cada
    uno; stdout no contiene logs para mantener el canal parseable.
    """
    runtime = Rp2040Runtime(board_definition)

    def send(message: dict) -> None:
        stdout.write(json.dumps(message, separators=(",", ":")) + "\n")
        stdout.flush()

    for line in stdin:
        if len(line.encode("utf-8")) > MAX_MESSAGE_BYTES:
            send({"type": "error", "error": "message too large"})
            continue
        try:
            request = json.loads(line)
            if not isinstance(request, dict):
                raise RuntimeProtocolError("request must be an object")
            kind = request.get("type")
            if kind == "ping":
                send({"type": "pong", "protocol": PROTOCOL_VERSION})
            elif kind == "configure":
                result = runtime.configure(request.get("gpio_nets"))
                send({"type": "configured", "protocol": PROTOCOL_VERSION,
                      **result})
            elif kind == "pin_mode":
                send(runtime.pin_mode(request.get("gpio"), request.get("mode")))
            elif kind == "read_gpio":
                gpio = runtime._resolve_gpio(request.get("gpio"))
                send({"type": "gpio_read", "protocol": PROTOCOL_VERSION,
                      "gpio": gpio, "value": runtime.read_gpio(gpio)})
            elif kind == "set_pwm":
                event = runtime.set_pwm(
                    request.get("gpio"), request.get("frequency_hz"),
                    request.get("duty_u16"), request.get("time_us", 0))
                send(event or {"type": "unchanged",
                                "gpio": runtime._resolve_gpio(request.get("gpio"))})
            elif kind == "read_adc":
                gpio = runtime._resolve_gpio(request.get("gpio"))
                send({"type": "adc_read", "protocol": PROTOCOL_VERSION,
                      "gpio": gpio, "value": runtime.read_adc(gpio)})
            elif kind in ("set_gpio", "set_input"):
                setter = runtime.set_gpio if kind == "set_gpio" else runtime.set_input
                event = setter(request.get("gpio"), request.get("value"),
                               request.get("time_us", 0))
                gpio = runtime._resolve_gpio(request.get("gpio"))
                send(event or {"type": "unchanged", "gpio": gpio})
            elif kind == "snapshot":
                send(runtime.snapshot())
            elif kind == "reset":
                runtime.reset()
                send({"type": "reset", "protocol": PROTOCOL_VERSION})
            elif kind == "stop":
                send({"type": "stopped", "protocol": PROTOCOL_VERSION})
                return
            else:
                raise RuntimeProtocolError(f"unknown message type: {kind!r}")
        except (RuntimeProtocolError, KeyError, TypeError, ValueError,
                json.JSONDecodeError) as exc:
            send({"type": "error", "error": str(exc)})


def main() -> None:
    serve()


if __name__ == "__main__":
    main()
