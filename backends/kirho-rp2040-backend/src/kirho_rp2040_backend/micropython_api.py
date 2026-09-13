"""Pequeño adaptador de la API MicroPython para el runtime virtual."""
from __future__ import annotations

import threading
import types

from .runtime import RuntimeProtocolError


_MISSING = object()


class ExecutionStopped(RuntimeError):
    """El runner fue detenido mientras el firmware estaba ejecutándose."""


class MicroPythonApi:
    """Expone solo los periféricos internos que necesita la primera fase."""

    def __init__(self, runtime, stop_event: threading.Event | None = None):
        self.runtime = runtime
        self.stop_event = stop_event or threading.Event()
        self.time_us = 0
        self._modules = self._build_modules()

    def globals(self) -> dict:
        machine = self._modules["machine"]
        return {
            "machine": machine,
            "time": self._modules["time"],
            "utime": self._modules["utime"],
            "Pin": machine.Pin,
            "PWM": machine.PWM,
            "ADC": machine.ADC,
        }

    def import_module(self, name: str):
        try:
            return self._modules[name]
        except KeyError as exc:
            raise ImportError(f"module {name!r} is not available in Kirho") from exc

    def sleep(self, seconds: float) -> None:
        try:
            seconds = float(seconds)
        except (TypeError, ValueError) as exc:
            raise TypeError("sleep duration must be numeric") from exc
        if seconds < 0:
            raise ValueError("sleep duration cannot be negative")
        self._wait(seconds, int(seconds * 1_000_000))

    def sleep_ms(self, milliseconds: int) -> None:
        try:
            milliseconds = int(milliseconds)
        except (TypeError, ValueError) as exc:
            raise TypeError("sleep_ms duration must be an integer") from exc
        if milliseconds < 0:
            raise ValueError("sleep_ms duration cannot be negative")
        self._wait(milliseconds / 1_000, milliseconds * 1_000)

    def sleep_us(self, microseconds: int) -> None:
        try:
            microseconds = int(microseconds)
        except (TypeError, ValueError) as exc:
            raise TypeError("sleep_us duration must be an integer") from exc
        if microseconds < 0:
            raise ValueError("sleep_us duration cannot be negative")
        self._wait(microseconds / 1_000_000, microseconds)

    def _wait(self, seconds: float, elapsed_us: int) -> None:
        if self.stop_event.wait(seconds):
            raise ExecutionStopped("MicroPython runner stopped")
        self.time_us += max(0, elapsed_us)

    def _build_modules(self) -> dict:
        api = self

        class BoundPin(Pin):
            def __init__(self, pin, mode=-1, pull=None, *, value=None):
                super().__init__(api, pin, mode, pull, value=value)

        class BoundPWM(PWM):
            def __init__(self, pin):
                super().__init__(api, pin)

        class BoundADC(ADC):
            def __init__(self, pin):
                super().__init__(api, pin)

        machine = types.ModuleType("machine")
        machine.Pin = BoundPin
        machine.PWM = BoundPWM
        machine.ADC = BoundADC
        machine.IN = Pin.IN
        machine.OUT = Pin.OUT
        machine.OPEN_DRAIN = Pin.OPEN_DRAIN
        machine.PULL_UP = Pin.PULL_UP
        machine.PULL_DOWN = Pin.PULL_DOWN

        time_module = types.ModuleType("time")
        time_module.sleep = api.sleep
        time_module.sleep_ms = api.sleep_ms
        time_module.sleep_us = api.sleep_us
        time_module.ticks_ms = lambda: api.time_us // 1_000
        time_module.ticks_us = lambda: api.time_us
        time_module.ticks_cpu = time_module.ticks_us
        time_module.ticks_diff = lambda end, start: int(end) - int(start)
        time_module.ticks_add = lambda ticks, delta: int(ticks) + int(delta)
        time_module.time = lambda: api.time_us / 1_000_000
        return {"machine": machine, "time": time_module, "utime": time_module}


class Pin:
    """Pin MicroPython compatible con el GPIO virtual de Kirho."""

    IN = 0
    OUT = 1
    OPEN_DRAIN = 2
    PULL_UP = 1
    PULL_DOWN = 2
    PULL_HOLD = 3

    def __init__(self, api: MicroPythonApi, pin, mode=-1, pull=None, *, value=None):
        self._api = api
        self._gpio = api.runtime._resolve_gpio(pin)
        self._mode = None
        self._pull = None
        if mode != -1:
            self.init(mode, pull, value=value)
        elif value is not None:
            self.value(value)

    def init(self, mode=-1, pull=None, *, value=None):
        if mode != -1:
            self._mode = mode
            self._pull = pull
            self._api.runtime.pin_mode(self._gpio, self._runtime_mode(mode, pull))
        if value is not None:
            self.value(value)
        return self

    def value(self, value=_MISSING):
        if value is _MISSING:
            return self._api.runtime.read_gpio(self._gpio)
        return self._api.runtime.set_gpio(self._gpio, value,
                                          time_us=self._api.time_us)

    def on(self):
        return self.value(1)

    def off(self):
        return self.value(0)

    high = on
    low = off

    def toggle(self):
        return self.value(not self.value())

    def irq(self, *args, **kwargs):
        raise NotImplementedError("Pin.irq is not simulated yet")

    def __call__(self, value=_MISSING):
        return self.value(value)

    def __repr__(self):
        return f"Pin(GP{self._gpio})"

    @staticmethod
    def _runtime_mode(mode, pull) -> str:
        if mode == Pin.OUT or mode == Pin.OPEN_DRAIN:
            return "OUTPUT"
        if mode != Pin.IN:
            raise RuntimeProtocolError("unsupported MicroPython pin mode")
        if pull == Pin.PULL_UP:
            return "INPUT_PULLUP"
        if pull == Pin.PULL_DOWN:
            return "INPUT_PULLDOWN"
        return "INPUT"


class PWM:
    """PWM interno mínimo, expresado como frecuencia y duty cycle."""

    def __init__(self, api: MicroPythonApi, pin):
        self._api = api
        self._pin = pin if isinstance(pin, Pin) else Pin(api, pin)
        self._frequency_hz = 1_000
        self._duty_u16 = 0

    def freq(self, frequency=_MISSING):
        if frequency is _MISSING:
            return self._frequency_hz
        try:
            frequency = int(frequency)
        except (TypeError, ValueError) as exc:
            raise TypeError("PWM frequency must be an integer") from exc
        if frequency <= 0:
            raise ValueError("PWM frequency must be positive")
        self._frequency_hz = frequency
        return self._api.runtime.set_pwm(
            self._pin._gpio, self._frequency_hz, self._duty_u16,
            time_us=self._api.time_us)

    def duty_u16(self, duty=_MISSING):
        if duty is _MISSING:
            return self._duty_u16
        try:
            duty = int(duty)
        except (TypeError, ValueError) as exc:
            raise TypeError("PWM duty_u16 must be an integer") from exc
        if duty < 0 or duty > 65_535:
            raise ValueError("PWM duty_u16 must be between 0 and 65535")
        self._duty_u16 = duty
        return self._api.runtime.set_pwm(
            self._pin._gpio, self._frequency_hz, self._duty_u16,
            time_us=self._api.time_us)

    def duty(self, duty=_MISSING):
        if duty is _MISSING:
            return round(self._duty_u16 * 1_023 / 65_535)
        try:
            duty = int(duty)
        except (TypeError, ValueError) as exc:
            raise TypeError("PWM duty must be an integer") from exc
        if duty < 0 or duty > 1_023:
            raise ValueError("PWM duty must be between 0 and 1023")
        return self.duty_u16(round(duty * 65_535 / 1_023))

    def deinit(self):
        return self.duty_u16(0)


class ADC:
    """ADC de 16 bits alimentado por entradas virtuales del runtime."""

    ATTN_0DB = 0
    ATTN_2_5DB = 1
    ATTN_6DB = 2
    ATTN_11DB = 3

    def __init__(self, api: MicroPythonApi, pin):
        self._api = api
        self._gpio = pin._gpio if isinstance(pin, Pin) else api.runtime._resolve_gpio(pin)
        self._width = 16
        self._atten = self.ATTN_0DB

    def read_u16(self):
        return self._api.runtime.read_adc(self._gpio)

    def read(self):
        return self.read_u16() >> 6

    def width(self, value=_MISSING):
        if value is _MISSING:
            return self._width
        value = int(value)
        if value not in (9, 10, 11, 12, 13, 14, 15, 16):
            raise ValueError("ADC width must be between 9 and 16 bits")
        self._width = value

    def atten(self, value=_MISSING):
        if value is _MISSING:
            return self._atten
        self._atten = int(value)
