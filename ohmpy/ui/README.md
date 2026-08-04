# `ohmpy.ui` — Quick map

The entire graphical layer (PyQt6). For the complete architecture, see
[`docs/architecture.md`](../../docs/architecture.md).

## Layers

```
                       MainWindow (main.py)
                       │
                       │  contains one
                       ▼
       ┌────────────────────────────────────┐
       │  CircuitScene  (ui/scene.py)       │
       │  Handles the grid, snapping,       │
       │  picking, connections, routing,    │
       │  and construction of the netlist.  │
       └─────────┬───────────────┬──────────┘
                 │               │
        contains │               │ opens
                 ▼               ▼
       ┌──────────────────┐  ┌──────────────────┐
       │  Graphic items   │  │  Dialogs         │
       │  (ui/items/)     │  │  (ui/dialogs/)   │
       │  ComponentItem,  │  │  Editor, scope,  │
       │  WireItem        │  │  multimeter, …   │
       └──────────────────┘  └──────────────────┘
```

## Files

| File | Contents | When to read it |
|---|---|---|
| `scene.py` | `CircuitScene` and `build_engine_components_for_item`: translates the scene into the engine netlist. | Understand how drawings become simulations. |
| `style.py` | Active color palette (`COLORS`), font helpers, geometry constants, and SI parsers. | Change the global look and feel. |
| `component_metadata.py` | Pin labels by type, digital-type list, and edit-dialog value labels. | Add a component to the catalog. |
| `items/component_item.py` | Canvas component rendering and behavior: shape, pins, rotation, and dragging. | Change rendering or interaction. |
| `items/wire_item.py` | Orthogonal wires between pins with editable segments. | Work on auto-routing. |
| `dialogs/component_dialog.py` | Generic component value/parameter editor. | Add a parameter. |
| `dialogs/component_picker_dialog.py` | Catalog from which components are dragged onto the scene. | Reorganize the catalog. |
| `dialogs/oscilloscope_dialog.py` | Two-channel oscilloscope: time base, cursors, trigger, and hardware support. | Improve the scope. |
| `dialogs/multimeter_dialog.py` | V/I/Ω multimeter. | |
| `dialogs/function_generator_dialog.py` | Sine, square, and triangle generator. | |
| `dialogs/bode_dialog.py` | Bode plot based on `solve_ac`: magnitude, phase, and cursor. | |
| `dialogs/power_triangle_dialog.py` | P, Q, S, and power factor from an AC analysis. | |
| `dialogs/resistor_calc_dialog.py` | Color-code ↔ value converter and E12/E24/E96 series. | |
| `dialogs/circuit_analyzer_dialog.py` | Digital analyzer: truth table, SOP/POS minimization, and automatic circuit construction. | |
| `dialogs/tl082_unit_dialog.py` | Selector for the dual TL082's “A/B unit”. | |
| `dialogs/hardware_source_dialog.py` | Serial-port configuration for the physical probe. See `firmware/README.md`. | |
| `dialogs/settings_dialog.py` | Appearance settings and theme management. | |

## Conventions

- Any module that needs the palette imports `from ohmpy.ui.style import COLORS`.
  `COLORS` is **dynamic**: changing the theme updates the dictionary in place,
  so subsequent repaints use the new colors. Do not capture it in local
  variables that outlive a theme change.
- Geometry constants (`GRID_SIZE`, `COMP_W`, etc.) are fixed so snap-to-grid
  works consistently.
- Every user-supplied numeric value passes through `parse_si_value` before
  reaching the engine — the engine uses SI units without prefixes only.
