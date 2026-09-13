"""Pruebas del paquete instalable de la placa RP2040."""
from __future__ import annotations

import importlib.util
import io
import json
from pathlib import Path
import sys
import struct
import types


PACKAGE_ROOT = Path(__file__).parents[1] / "backends" / "kirho-rp2040-backend"
MODULE_PATH = PACKAGE_ROOT / "src" / "kirho_rp2040_backend" / "__init__.py"
RUNTIME_PATH = PACKAGE_ROOT / "src" / "kirho_rp2040_backend" / "runtime.py"


def _load_backend_module():
    spec = importlib.util.spec_from_file_location(
        "kirho_rp2040_backend", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    module.__package__ = "kirho_rp2040_backend"
    module.__path__ = [str(PACKAGE_ROOT / "src" / "kirho_rp2040_backend")]
    sys.modules["kirho_rp2040_backend"] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _load_runtime_module():
    spec = importlib.util.spec_from_file_location(
        "kirho_rp2040_runtime", RUNTIME_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _load_arduino_api_module():
    package_name = "_kirho_rp2040_test_package"
    package = types.ModuleType(package_name)
    package.__path__ = [str(PACKAGE_ROOT / "src" / "kirho_rp2040_backend")]
    sys.modules[package_name] = package
    sys.modules[f"{package_name}.runtime"] = _load_runtime_module()
    api_path = PACKAGE_ROOT / "src" / "kirho_rp2040_backend" / "arduino_api.py"
    spec = importlib.util.spec_from_file_location(
        f"{package_name}.arduino_api", api_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_rp2040_backend_factory_exposes_metadata():
    module = _load_backend_module()

    backend = module.create_backend()

    assert backend.metadata() == {
        "id": "rp2040",
        "name": "RP2040 Dev Board",
        "version": "0.1.0",
        "status": "board-definition",
        "board_id": "rp2040-40-pin",
    }


def test_rp2040_board_definition_has_complete_pinout():
    module = _load_backend_module()

    definition = module.load_board_definition()
    pins = definition["pins"]

    assert definition["id"] == "rp2040-40-pin"
    assert len(pins) == 40
    assert [pin["number"] for pin in pins] == list(range(1, 41))
    assert pins[0]["functions"][:2] == ["pwm", "uart0.tx"]
    assert pins[30]["name"] == "GP26"
    assert pins[30]["adc_channel"] == 0
    assert pins[39]["voltage"] == 5.0
    assert definition["internal_pins"][0]["name"] == "LED"


def test_rp2040_backend_publishes_schematic_symbol_without_footprint():
    module = _load_backend_module()
    component = module.create_backend().schematic_components()[0]

    assert component["type"] == "RP2040"
    assert component["board_id"] == "rp2040-40-pin"
    assert component["board_definition"]["pin_count"] == 40
    assert "footprint" not in component


def test_rp2040_runtime_is_event_driven_and_includes_internal_gpios():
    backend_module = _load_backend_module()
    runtime_module = _load_runtime_module()
    runtime = runtime_module.Rp2040Runtime(
        backend_module.load_board_definition())
    events = []
    runtime.add_listener(events.append)

    assert runtime.configure({25: "LED_NET"}) == {
        "gpio_nets": {"25": "LED_NET"}}
    event = runtime.set_gpio(25, 1, time_us=1000)

    assert event == {
        "type": "gpio", "protocol": 1, "gpio": 25, "value": 1,
        "source": "firmware", "time_us": 1000, "net": "LED_NET",
    }
    assert events == [event]
    assert runtime.set_gpio(25, 1) is None
    assert runtime.snapshot()["gpio"]["25"] == 1


def test_rp2040_runtime_supports_pin_modes_and_board_pin_names():
    backend_module = _load_backend_module()
    runtime_module = _load_runtime_module()
    runtime = runtime_module.Rp2040Runtime(
        backend_module.load_board_definition())

    assert runtime.pin_mode("GP11", "output")["mode"] == "OUTPUT"
    event = runtime.set_gpio("GPIO11", 1)

    assert event["gpio"] == 11
    assert runtime.read_gpio("GP11") == 1
    assert runtime.snapshot()["modes"]["11"] == "OUTPUT"
    assert runtime.snapshot()["gpio"]["11"] == 1


def test_arduino_api_translates_gpio_calls_without_sleeping():
    runtime_module = _load_runtime_module()
    api_module = _load_arduino_api_module()
    runtime = runtime_module.Rp2040Runtime()
    api = api_module.ArduinoApi(runtime)

    api.pinMode("GP11", api_module.OUTPUT)
    event = api.digitalWrite("GPIO11", api_module.HIGH)
    assert event["gpio"] == 11
    assert event["time_us"] == 0

    api.delay(10)
    low_event = api.digitalWrite(11, api_module.LOW)
    assert low_event["time_us"] == 10_000
    assert api.digitalRead("GP11") == api_module.LOW


def test_micropython_runner_drives_pin_pwm_and_adc():
    backend_module = _load_backend_module()
    runtime = backend_module.create_backend().create_runtime()
    runtime.set_adc_input(26, 1234)
    events = []
    runtime.add_listener(events.append)
    runner = backend_module.create_backend().create_micropython_runner(runtime)

    runner.start(
        "from machine import Pin, PWM, ADC\n"
        "from time import sleep_ms\n"
        "led = Pin(11, Pin.OUT)\n"
        "led.on()\n"
        "sleep_ms(2)\n"
        "led.off()\n"
        "pwm = PWM(12)\n"
        "pwm.freq(2000)\n"
        "pwm.duty_u16(32768)\n"
        "adc_value = ADC(26).read_u16()\n",
        filename="main.py",
    )

    assert runner.wait(1)
    assert runner.error is None
    assert [event["value"] for event in events if event["type"] == "gpio"] == [1, 0]
    assert runner.runtime.pwm_values[12] == {
        "frequency_hz": 2000.0, "duty_u16": 32768}
    assert runner.api.time_us == 2_000


def test_micropython_runner_can_stop_a_loop():
    backend_module = _load_backend_module()
    runner = backend_module.create_backend().create_micropython_runner()

    runner.start("from time import sleep_ms\nwhile True:\n    sleep_ms(1)\n")

    assert not runner.wait(0.02)
    assert runner.stop()
    assert runner.error is None


def test_rp2040_firmware_loader_accepts_python_and_uf2(tmp_path):
    backend_module = _load_backend_module()
    backend = backend_module.create_backend()
    python_path = tmp_path / "main.py"
    python_path.write_text(
        "from machine import Pin\nled = Pin(11, Pin.OUT)\nled.value(1)\n",
        encoding="utf-8")

    python_image = backend.load_firmware(python_path)
    assert python_image.format == "py"
    assert python_image.metadata["syntax_valid"]

    block = bytearray(512)
    struct.pack_into("<8I", block, 0,
                     0x0A324655, 0x9E5D5157, 0x2000,
                     0x10000000, 4, 0, 1, 0xE48BFF56)
    block[32:36] = b"TEST"
    struct.pack_into("<I", block, 508, 0x0AB16F30)
    uf2_path = tmp_path / "firmware.uf2"
    uf2_path.write_bytes(block)

    uf2_image = backend.load_firmware(uf2_path)
    assert uf2_image.format == "uf2"
    assert uf2_image.metadata["block_count"] == 1


def test_rp2040_runtime_stdio_protocol_has_no_extra_output():
    runtime_module = _load_runtime_module()
    stdin = io.StringIO('\n'.join((
        '{"type":"ping"}',
        '{"type":"configure","gpio_nets":{"0":"N0"}}',
        '{"type":"pin_mode","gpio":"GP0","mode":"OUTPUT"}',
        '{"type":"set_gpio","gpio":0,"value":1}',
        '{"type":"read_gpio","gpio":"GPIO0"}',
        '{"type":"stop"}',
    )))
    stdout = io.StringIO()

    runtime_module.serve(stdin, stdout)
    messages = [json.loads(line) for line in stdout.getvalue().splitlines()]

    assert [message["type"] for message in messages] == [
        "pong", "configured", "gpio_mode", "gpio", "gpio_read", "stopped"]
    assert messages[3]["net"] == "N0"
    assert messages[4]["value"] == 1
