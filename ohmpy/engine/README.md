# `ohmpy.engine` — Quick map

Analog engine, digital engine, and mixed-signal coordinator. For the complete
architectural overview, see
[`docs/architecture.md`](../../docs/architecture.md).

| File | Contents | When to read it |
|---|---|---|
| `components.py` | Physical models: R, V, I, C, L, diode, BJT, MOSFET, op-amp, TL082, transformer, bridge rectifier, potentiometer, and impedance. Each knows how to stamp its contribution into the MNA matrix. | Add an analog component or understand global conventions (module docstring). |
| `mna.py` | `MNASolver` — the analog core. It solves DC (linear and Newton–Raphson), AC (sweep with cached LU), and transient analysis (adaptive time step with LTE control). | Modify the solver, debug convergence, or understand numerical techniques (`gmin`, diode limiting, circuit fingerprint). |
| `digital_engine.py` | `DigitalSimulator` with gates, flip-flops, registers, counters, MUX/DEMUX, ROM, RAM, and buses. Discrete event simulation (priority queue). | Add a digital component or modify propagation rules. |
| `bridges.py` | Analog ↔ digital adapters: `ADC`, `DAC`, `ComparatorBridge`, `PWMBridge`, `SampleAndHold`, and `MixedSignalBus`. | Connect new mixed domains or change quantization. |
| `mixed_signal.py` | `MixedSignalInterface` — orchestrates MNA, digital simulation, and bridges over time windows. `TimingAnalyzer` performs post-simulation setup/hold analysis. | Co-simulate mixed circuits or analyze timing. |
| `hw_stream.py` | Decoder for the external firmware binary protocol (physical oscilloscope probe). | Work with real hardware — see `firmware/README.md`. |

## Public entry point

`ohmpy/engine/__init__.py` re-exports everything used outside the package. If
you add a public class, export it there as well.

## Golden rules

1. **Every new model must include a closed-form test case.** Without a
   reference (for example, `V_out = V_in · R2/(R1+R2)`), engine regressions
   cannot be detected.
2. **Changing a convention (signs, units, or node names) breaks the public
   API.** Document it in the module docstring AND in
   `docs/architecture.md`.
3. **Before adding a new parameter to an existing component**, make sure the
   UI (`ohmpy/ui/component_metadata.py` + dialog) can request it. A parameter
   that nobody can configure is dead code.
