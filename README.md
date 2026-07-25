# PyNode

**Open-source electronic circuit simulator — analog, digital, and mixed-signal.**

[Leer en español](docs/README.es.md)

PyNode is a schematic-capture and simulation environment built with Python + PyQt6. Its custom MNA (Modified Nodal Analysis) engine solves DC, AC, and transient analyses from the same netlist, with virtual instruments (a multimeter, two-channel oscilloscope, and function generator) integrated into the canvas.

![Main interface](docs/img/screenshot-main.png)

---

## Features

### Simulation engine

- **DC analysis** — linear and nonlinear (Newton–Raphson), with source stepping for circuits containing diodes, LEDs, BJTs, MOSFETs, and op-amps.
- **AC analysis** — frequency sweeps with cached LU factorization at each frequency point.
- **Transient analysis** — adaptive time steps with LTE error control and automatic refinement around switching events (LEDs, diodes, comparators).
- **Digital engine** — event-driven binary simulation with propagation delays.
- **Mixed signal** — ADC, DAC, comparator, PWM, and sample-and-hold bridges couple analog and digital domains on the same time step.

### Supported components

| Category | Components |
|---|---|
| Passive | Resistor, potentiometer, capacitor, inductor, generic impedance |
| Sources | DC voltage, AC voltage, current, function generator |
| Semiconductors | Diode, LED (color-specific Vf), NPN/PNP BJT, N/P MOSFET, ideal op-amp, dual TL082 |
| Converters | Ideal transformer, diode bridge rectifier |
| Digital | AND, OR, NOT, NAND, NOR, XOR, XNOR, BUF, tristate buffer, NE555 timer |
| Memory/sequential | DFF, JKFF, TFF, SRFF, shift registers, binary counters |
| Combinational | MUX, DEMUX, ROM, RAM |
| A/D bridges | ADC, DAC, comparator, PWM, sample-and-hold |

### Virtual instruments

- **Multimeter** — DC/AC voltage, current, and resistance, with probes that can be placed on the schematic.
- **Oscilloscope** — two differential channels with configurable time base and vertical scale.
- **Function generator** — sine, square, triangle, and sawtooth waveforms with amplitude, frequency, and offset controls.

### Additional tools

- **Circuit analyzer** — detects implicit shorts and validates topology before simulation.
- **Resistor calculator** — color code ↔ value conversion and E12/E24/E96 series.
- **Power triangle** — P, Q, S, and power factor for AC analysis.
- **Themes** — support for customizable JSON themes. See [`themes/README.md`](themes/README.md) to create your own.

---

## Installation

**Requirements:** Python 3.10 or later; Windows, Linux, or macOS.

```bash
git clone https://github.com/pabdan2003/PyNode.git
cd PyNode
python -m venv .venv
# Windows
.venv\Scripts\activate
# Linux/macOS
source .venv/bin/activate
pip install -r requirements.txt
python main.py
```

---

## Quick start

1. Start PyNode with `python main.py`.
2. Drag components from the side panel onto the canvas.
3. Connect pins by clicking one pin and then another.
4. Double-click a component to edit its value.
5. Click **▶ SIMULATE**; PyNode automatically detects DC, AC, digital, or mixed-signal mode.

![Live simulation demo](docs/img/demo-live.gif)

### Minimal example (engine from Python)

```python
from pynode.engine import Resistor, VoltageSource, MNASolver

solver = MNASolver()
circuit = [
    VoltageSource("V1", "in", "0", 10.0),
    Resistor("R1", "in", "out", 1000.0),
    Resistor("R2", "out", "0", 1000.0),
]
result = solver.solve_dc(circuit)
print(result["voltages"]["out"])  # 5.0 V
```

---

## Project structure

```
PyNode/
├── main.py                  # Entrypoint (opens the main window)
├── pynode/                  # Main package
│   ├── circuit_analyzer.py  # Topology validation and short detection
│   ├── theme_manager.py     # Theme loading and persistence
│   ├── engine/
│   │   ├── mna.py           # MNA solver (DC, AC, transient)
│   │   ├── components.py    # Analog component models
│   │   ├── digital_engine.py# Event-driven digital simulator
│   │   ├── bridges.py       # Analog ↔ digital converters
│   │   └── mixed_signal.py  # Mixed-signal simulation coordinator
│   └── ui/
│       ├── scene.py         # QGraphics scene and netlist construction
│       ├── items/           # ComponentItem, WireItem
│       ├── dialogs/         # Instruments and configuration dialogs
│       └── style.py         # Theme, fonts, and visual constants
├── themes/                  # JSON themes (data)
├── firmware/                # Reference firmware for a physical probe
└── tests/                   # pytest suite (engine, digital, and mixed signal)
```

---

## Testing

```bash
pip install -r requirements-dev.txt
pytest -v
```

Every push and pull request runs the suite on Python 3.10, 3.11, and 3.12 through GitHub Actions (see `.github/workflows/ci.yml`).

---

## Roadmap

- [x] Migrate tests to `pytest` + GitHub Actions CI.
- [x] Bode plots (magnitude and phase) for the existing AC analysis.
- [ ] FFT in the oscilloscope.
- [x] SPICE netlist export (interoperability with ngspice / LTspice).
- [x] Reusable subcircuits (selection encapsulation).
- [x] Undo changes using snapshots.
- [ ] Redo and optional migration to `QUndoStack`.
- [ ] Orthogonal wire auto-routing.
- [ ] Persistent probes on the schematic.

---

## Contributing

Contributions are welcome. Before opening a pull request:

1. Read [`docs/architecture.md`](docs/architecture.md) to understand the separation between engines and global conventions (signs, units, node names).
2. Run the existing tests and add the ones relevant to your change.
3. For engine changes, include a validation case against a known analytical solution.
4. For visual changes, attach before-and-after screenshots.

Quick package maps:

- [`pynode/engine/README.md`](pynode/engine/README.md) — purpose of each engine file.
- [`pynode/ui/README.md`](pynode/ui/README.md) — purpose of each UI file.
- [`themes/README.md`](themes/README.md) — JSON theme format and how to create one.
- [`firmware/README.md`](firmware/README.md) — binary protocol for the physical oscilloscope probe.

---

## License

License to be determined. Until then, all rights are reserved by the authors.
