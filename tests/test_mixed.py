"""
Tests del motor digital, puentes A/D y simulación mixta.
"""
import pytest
import numpy as np

from pynode.engine import (
    MNASolver, Resistor, VoltageSource, Capacitor,
    DigitalSimulator, AND, DFF, Timer555, BinaryCounter, ShiftRegister, Bus,
    ADC, DAC, ComparatorBridge, PWMBridge,
    MixedSignalInterface, TimingAnalyzer,
)
from pynode.engine.components import Timer555Analog
from pynode.circuit_analyzer import CircuitAnalyzer
from types import SimpleNamespace


# ──────────────────────────────────────────────
# Motor digital
# ──────────────────────────────────────────────

@pytest.mark.parametrize("a,b,expected", [(1, 1, 1), (1, 0, 0), (0, 1, 0), (0, 0, 0)])
def test_and_gate(a, b, expected):
    dsim = DigitalSimulator()
    dsim.add(AND("U1", inputs=["A", "B"], output="Y"))
    dsim.set_input("A", a, at=0)
    dsim.set_input("B", b, at=0)
    dsim.run(until=5e-9)
    assert dsim.final_value("Y") == expected


@pytest.mark.parametrize("d_value", [0, 1])
def test_dff_captures_on_rising_edge(d_value):
    dsim = DigitalSimulator()
    dsim.add(DFF("FF1", d="D", clk="CLK", q="Q"))
    dsim.set_input("D", d_value, at=0)
    dsim.set_input("CLK", 0, at=0)
    dsim.set_input("CLK", 1, at=5e-9)
    dsim.run(until=20e-9)
    assert dsim.final_value("Q") == d_value


def test_555_pinout_and_latch_behavior():
    """El 555 fija OUT con TRIG bajo y lo borra con THRESH o RESET."""
    dsim = DigitalSimulator()
    dsim.add(Timer555("U1", "GND", "TRIG", "OUT", "RESET", "CTRL",
                      "THRESH", "DISCH", "VCC"))
    dsim.set_input("RESET", 1, at=0)
    dsim.set_input("TRIG", 1, at=0)
    dsim.set_input("THRESH", 0, at=0)
    dsim.set_input("TRIG", 0, at=2e-9)
    dsim.set_input("TRIG", 1, at=4e-9)
    dsim.run(until=6e-9)
    assert dsim.final_value("OUT") == 1
    assert dsim.final_value("DISCH") == 0
    dsim.set_input("THRESH", 1, at=7e-9)
    dsim.run(until=10e-9)
    assert dsim.final_value("OUT") == 0
    assert dsim.final_value("DISCH") == 1


def test_555_can_reset_before_mixed_simulation():
    """El pin RESET no debe ocultar el hook reset() del componente."""
    dsim = DigitalSimulator()
    dsim.add(Timer555("U1", "GND", "TRIG", "OUT", "RESET", "CTRL",
                      "THRESH", "DISCH", "VCC"))

    dsim.reset()


def test_555_holds_state_during_newton_iterations():
    timer = Timer555Analog("U1", "0", "CAP", "OUT", "RESET", "CTRL",
                           "CAP", "DISCH", "VCC")
    nodes = {"CAP": 0, "OUT": 1, "RESET": 2, "CTRL": 3,
             "DISCH": 4, "VCC": 5}
    timer.prepare_transient_step(np.array([0, 0, 5, 0, 0, 5.0]), nodes)
    assert timer.q == 1
    G, I = np.zeros((6, 6)), np.zeros(6)
    timer.stamp_linear(G, I, nodes, np.array([5, 0, 5, 0, 0, 5.0]))
    assert timer.q == 1  # no conmuta dentro de Newton-Raphson


def test_555_astable_oscillates_over_multiple_periods():
    """R=1k/15k y C=1µF debe producir más de un flanco en 100 ms."""
    comps = [
        VoltageSource("V1", "vcc", "0", 5.0),
        Resistor("R1", "vcc", "disch", 1000.0),
        Resistor("R2", "disch", "cap", 15000.0),
        Capacitor("C1", "cap", "0", 1e-6),
        Timer555Analog("U1", "0", "cap", "out", "vcc", "0",
                       "cap", "disch", "vcc"),
        Resistor("load", "out", "0", 1e6),
    ]

    result = MNASolver().solve_transient(comps, t_stop=0.1, dt=1e-5)
    out_high = result["voltages"]["out"] > 2.5

    assert result["success"]
    assert np.count_nonzero(np.diff(out_high.astype(int))) >= 4


def test_binary_counter_overflow():
    """Contador 3-bit con 8 pulsos vuelve a 0."""
    dsim = DigitalSimulator()
    dsim.add(BinaryCounter("CNT", n=3, clk="CLK", q_prefix="Q", carry="CARRY"))
    t = 0
    for _ in range(8):
        dsim.set_input("CLK", 0, at=t)
        dsim.set_input("CLK", 1, at=t + 1e-9)
        t += 2e-9
    dsim.run(until=t + 1e-9)
    assert dsim.components[0].count == 0


def test_shift_register_sipo_4bit():
    """ShiftRegister SIPO 4-bit con entrada 1,0,1,1 → Q=[1,0,1,1]."""
    dsim = DigitalSimulator()
    dsim.add(ShiftRegister("SR1", n=4, din="DIN", clk="CLK", q_prefix="Q"))
    t = 0
    for b in [1, 0, 1, 1]:
        dsim.set_input("DIN", b, at=t)
        dsim.set_input("CLK", 0, at=t + 1e-9)
        dsim.set_input("CLK", 1, at=t + 5e-9)
        t += 10e-9
    dsim.run(until=t + 5e-9)
    q = [dsim.final_value(f"Q{i}") for i in range(4)]
    assert q == [1, 0, 1, 1]


def test_bus_read_write():
    """Bus 4-bit: write 10 (0b1010), read 10."""
    dsim = DigitalSimulator()
    bus = Bus("DATA", ["D0", "D1", "D2", "D3"])
    bus.write(dsim, 10, at=0)
    dsim.run(until=5e-9)
    assert bus.read(dsim) == 10


# ──────────────────────────────────────────────
# Puentes analógico ↔ digital
# ──────────────────────────────────────────────

@pytest.mark.parametrize("vin,expected_code,tol", [
    (2.5, 128, 1),
    (0.0, 0, 0),
    (5.0, 255, 0),
])
def test_adc_quantization(vin, expected_code, tol):
    """ADC 8-bit, Vref=5V."""
    adc = ADC("A", node="Vout", bits=8, vref=5.0)
    adc.sample({"Vout": vin, "0": 0.0}, {}, 0.0)
    assert abs(adc.last_code - expected_code) <= tol


def test_dac_midscale():
    """DAC 8-bit code=128 → V≈2.5V."""
    dac = DAC("D1", bits=8, vref=5.0, in_prefix="D")
    d_nets = {f"D{i}": (128 >> i) & 1 for i in range(8)}
    v_out = dac.convert(d_nets, 0.0)
    assert v_out == pytest.approx(2.5, abs=0.02)


def test_dac_zero():
    dac = DAC("D1", bits=8, vref=5.0, in_prefix="D")
    v = dac.convert({f"D{i}": 0 for i in range(8)}, 1e-9)
    assert abs(v) < 1e-9


@pytest.mark.parametrize("vin,expected", [(3.5, 1), (1.0, 0)])
def test_comparator_bridge(vin, expected):
    """Comparador con Vref=2.5V e histéresis."""
    comp = ComparatorBridge("CMP", node_pos="Vin", vref=2.5, hysteresis=0.1)
    out = comp.evaluate({"Vin": vin})
    assert out[comp.output_net] == expected


def test_mixed_simulation_preserves_scheduled_digital_inputs():
    """Los estímulos previos sobreviven al arranque de la co-simulación."""
    dsim = DigitalSimulator()
    dsim.add(AND("U1", inputs=["A", "B"], output="Y"))
    dsim.set_input("A", 1, at=0.0)
    dsim.set_input("B", 1, at=0.0)
    iface = MixedSignalInterface(MNASolver(), dsim, [
        VoltageSource("V1", "in", "0", 5.0),
        Resistor("R1", "in", "0", 1000.0),
    ])

    result = iface.run_iterative(t_stop=1e-6, dt_chunk=1e-6, dt_analog=1e-7)

    assert result.success, result.error
    assert result.digital_waveforms["Y"][-1][1] == 1


def test_internal_logic_driver_updates_analog_source():
    """Una salida digital puede alimentar el dominio MNA sin un bloque DAC UI."""
    dsim = DigitalSimulator()
    dsim.add(AND("U1", inputs=["A", "B"], output="Y"))
    dsim.set_input("A", 1, at=0.0)
    dsim.set_input("B", 1, at=0.0)
    logic_source = VoltageSource("__logic", "out", "0", 0.1)
    iface = MixedSignalInterface(MNASolver(), dsim, [
        logic_source,
        Resistor("R1", "out", "0", 1000.0),
    ])
    iface.add_logic_driver("Y", "__logic", low=0.1, high=4.9)

    result = iface.run_iterative(t_stop=2e-6, dt_chunk=1e-6, dt_analog=1e-7)

    assert result.success, result.error
    assert logic_source.V == pytest.approx(4.9)
    assert result.analog_voltages["out"][-1] == pytest.approx(4.9, abs=1e-3)


def test_implicit_bridges_detect_each_direction():
    """Las fronteras internas distinguen entrada y salida digital."""
    items = [
        SimpleNamespace(comp_type="V", name="V1", node1="0", node2="sense", node3=""),
        SimpleNamespace(comp_type="AND", name="U1", node1="drive", node2="sense", node3="1"),
        SimpleNamespace(comp_type="R", name="R1", node1="drive", node2="0", node3=""),
    ]
    flags = CircuitAnalyzer().analyze(items, {})

    assert flags.boundary_detail["sense"]["analog_to_digital"]
    assert not flags.boundary_detail["sense"]["digital_to_analog"]
    assert flags.boundary_detail["drive"]["digital_to_analog"]


def test_555_uses_analog_transient_without_implicit_bridge():
    timer = SimpleNamespace(comp_type='IC555', name='U1', timer_nodes=[''] * 8)
    pin_nodes = {f'U1__p{i}': node for i, node in enumerate(
        ('0', 'cap', 'out', 'vcc', 'ctrl', 'cap', 'disch', 'vcc'), 1)}

    flags = CircuitAnalyzer().analyze([timer], pin_nodes)

    assert flags.has_dc and flags.has_ac
    assert not flags.has_digital
    assert not flags.implicit_boundary_nodes


def test_pwm_bridge_duty_cycle():
    """PWM 50% duty → V≈Vmax/2."""
    pwm = PWMBridge("PWM1", pwm_net="PWM", vmax=3.3, window=1e-3)
    for i in range(10):
        t = i * 0.1e-3
        val = 1 if (i % 2 == 0) else 0
        pwm.update({"PWM": val}, t)
    assert pwm.output_voltage == pytest.approx(1.65, abs=0.2)


# ──────────────────────────────────────────────
# Integración mixta
# ──────────────────────────────────────────────

def test_mixed_rc_charging_with_adc():
    """RC cargando (τ=1ms), ADC muestrea: tras 5τ el code debe estar saturado."""
    mna = MNASolver()
    analog_comps = [
        VoltageSource("Vin", "in", "0", 5.0),
        Resistor("R1", "in", "mid", 1000),
        Capacitor("C1", "mid", "0", 1e-6),
    ]
    dsim = DigitalSimulator()
    adc = ADC("ADC1", node="mid", bits=8, vref=5.0)
    iface = MixedSignalInterface(mna, dsim, analog_comps)
    iface.add_adc(adc)
    res = iface.run(t_stop=5e-3, dt_analog=1e-4)

    assert res.success, res.error
    samples = res.adc_samples.get("ADC1", [])
    assert len(samples) > 0
    # Tras 5τ: Vc ≈ 5*(1-e^-5) ≈ 4.97V → code ≈ 252
    assert samples[-1][1] > 200


def test_timing_analyzer_detects_setup_violation():
    """D cambia 1 ns antes del flanco — debe violar setup de 3 ns."""
    wf = {
        "CLK": [(0.0, 0), (10e-9, 1), (20e-9, 0), (30e-9, 1)],
        "D":   [(0.0, 0), (9e-9, 1)],
    }
    ta = TimingAnalyzer(wf)
    violations = ta.check_setup_hold("D", "CLK", t_setup=3e-9, t_hold=1e-9)
    assert len(violations) > 0
    assert violations[0]["type"] == "setup"
