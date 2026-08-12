from __future__ import annotations

from typing import Dict, Optional, Tuple


DEFAULT_NODE_LABELS = ('Node +', 'Node −', None)

COMPONENT_NODE_LABELS: Dict[str, Tuple[str, str, Optional[str]]] = {
    'R':       ('Node 1',       'Node 2',            None),
    'POT':     ('Node 1',       'Node 2 (wiper)',    None),
    'SPST':    ('Terminal 1',   'Terminal 2',         None),
    'SPDT':    ('Common (COM)', 'Normally closed (NC)', 'Normally open (NO)'),
    'C':       ('Node 1',       'Node 2',            None),
    'L':       ('Node 1',       'Node 2',            None),
    'V':       ('Node + (anode)',  'Node − (cathode)', None),
    'I':       ('Node + (output)', 'Node − (input)', None),
    'D':       ('Anode (A)',    'Cathode (K)',        None),
    'LED':     ('Anode (A)',    'Cathode (K)',        None),
    'BJT_NPN': ('Collector (C)', 'Emitter (E)',       'Base (B)'),
    'BJT_PNP': ('Collector (C)', 'Emitter (E)',       'Base (B)'),
    'NMOS':    ('Drain (D)',    'Source (S)',        'Gate (G)'),
    'PMOS':    ('Drain (D)',    'Source (S)',        'Gate (G)'),
    'OPAMP':   ('Output (OUT)', 'Input − (V−)',      'Input + (V+)'),
    # Instrumentos
    'FGEN':    ('Output + (V+)', 'Output − (V−)',    None),
    'MULTIMETER': ('Probe + (red)', 'Probe − (black)', None),
}

FIVE_PIN_NODE_LABELS: Dict[str, Tuple[str, str, str, str, str]] = {
    # TL082 dual op-amp: OUT, IN−, IN+, V+, V−
    'TL082': ('Output (OUT)', 'Input − (IN−)', 'Input + (IN+)',
              'Supply V+', 'Supply V−'),
}

FOUR_PIN_NODE_LABELS: Dict[str, Tuple[str, str, str, str]] = {
    'RELAY':  ('Coil +', 'Coil −', 'Common (COM)', 'Normally open (NO)'),
    'XFMR':   ('Primary + (P1)', 'Primary − (P2)',
               'Secondary + (S1)', 'Secondary − (S2)'),
    'BRIDGE': ('AC1 (input ~)', 'AC2 (input ~)',
               'DC + (output +)', 'DC − (output −)'),
    # Osciloscopio: 2 canales diferenciales
    'OSC':    ('Channel A +', 'Channel A −',
               'Channel B +', 'Channel B −'),
}

VALUE_LABELS = {
    'R': 'Resistance (Ω)',
    'V': 'Voltage (V)',
    'I': 'Current (A)',
    'C': 'Capacitance (F)',
    'L': 'Inductance (H)',
    'POT': 'R total (Ω)',
    'D': 'Is — Saturation current (A)',
    'LED': 'Value (unused — Vf by color)',
    'BJT_NPN': 'hFE — Ganancia β',
    'BJT_PNP': 'hFE — Ganancia β',
    'NMOS': 'Kn — Transconductancia (A/V²)',
    'PMOS': 'Kp — Transconductancia (A/V²)',
    'OPAMP': 'A — Ganancia lazo abierto (V/V)',
    'TL082': 'A — Ganancia lazo abierto (V/V)',
    'XFMR': 'V_pri nominal (V) — informativo',
    'BRIDGE': 'V_f por diodo (V) — informativo',
    'FGEN': 'Amplitud (V)',
}

DIGITAL_GATE_TYPES = {'AND', 'OR', 'NOT', 'NAND', 'NOR', 'XOR'}
DIGITAL_FLIPFLOP_TYPES = {'DFF', 'JKFF', 'TFF', 'SRFF'}
DIGITAL_BRIDGE_TYPES = {'ADC_BRIDGE', 'DAC_BRIDGE', 'COMPARATOR'}
DIGITAL_COUNT_TYPES = {'COUNTER', 'MUX2'}
