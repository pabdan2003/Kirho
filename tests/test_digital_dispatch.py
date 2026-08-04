from types import SimpleNamespace

from main import MainWindow


def test_digital_gates_with_led_indicators_use_digital_tick():
    items = [
        SimpleNamespace(comp_type=kind)
        for kind in ('LOGIC_STATE', 'AND', 'LED', 'GND')
    ]

    assert MainWindow._is_digital_indicator_circuit(items)


def test_analog_circuit_is_not_a_digital_indicator_circuit():
    items = [SimpleNamespace(comp_type=kind) for kind in ('LOGIC_STATE', 'AND', 'R')]

    assert not MainWindow._is_digital_indicator_circuit(items)
