from kirho.engine.components import Capacitor, Relay, Resistor, VoltageSource
from kirho.engine.mna import MNASolver


def test_rc_relay_oscillator_switches():
    components = [
        VoltageSource("V12", "VCC", "0", 12.0),
        Relay("K1", "VCC", "RC", "VCC", "FLASH", coil_r=165.0),
        Resistor("R1", "RC", "0", 165.0),
        Resistor("R2", "FLASH", "RC", 165.0),
        Resistor("RP", "FLASH", "0", 10.0),
        Capacitor("C2", "RC", "0", 0.0022),
    ]
    result = MNASolver().solve_transient(
        components, t_stop=1.0, dt=0.01, adaptive=False)

    assert result["success"]
    flash = result["voltages"]["FLASH"]
    assert max(flash) - min(flash) > 5.0


def test_relay_uses_its_configured_activation_voltage():
    relay = Relay("K1", "A", "0", "COM", "NO", threshold=12.0)

    relay.prepare_transient_step([11.9], {"A": 0})
    assert not relay.active

    relay.prepare_transient_step([12.0], {"A": 0})
    assert relay.active


def test_dc_energized_relay_closes_its_contact():
    relay = Relay("K1", "VCC", "0", "VCC", "LOAD",
                  coil_r=470.0, threshold=12.0)
    result = MNASolver().solve_dc([
        VoltageSource("V1", "VCC", "0", 13.0),
        relay,
        Resistor("L1", "LOAD", "0", 100.0),
    ])

    assert result["success"]
    assert relay.active
    assert abs(result["voltages"]["LOAD"] - 13.0) < 1e-3


def test_default_relay_flashes_a_loaded_rc_output():
    components = [
        VoltageSource("V1", "VCC", "0", 13.0),
        Relay("K1", "VCC", "COIL_N", "VCC", "OUT", coil_r=165.0),
        Capacitor("C1", "VCC", "COIL_N", 0.01),
        Resistor("R1", "COIL_N", "OUT", 470.0),
        *(Resistor(f"L{i}", "OUT", "0", 100.0) for i in range(4)),
    ]

    result = MNASolver().solve_transient(
        components, t_stop=2.0, dt=0.001, adaptive=False)

    assert result["success"]
    output = result["voltages"]["OUT"]
    assert min(output) < 1.0
    assert max(output) > 12.0
