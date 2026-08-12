from types import SimpleNamespace

from main import MainWindow
from kirho.circuit_analyzer import DEFAULT_STANDARD


def test_digital_gates_with_led_indicators_use_digital_tick():
    items = [
        SimpleNamespace(comp_type=kind)
        for kind in ('LOGIC_STATE', 'AND', 'LED', 'GND')
    ]

    assert MainWindow._is_digital_indicator_circuit(items)


def test_analog_circuit_is_not_a_digital_indicator_circuit():
    items = [SimpleNamespace(comp_type=kind) for kind in ('LOGIC_STATE', 'AND', 'R')]

    assert not MainWindow._is_digital_indicator_circuit(items)


def _digital_item(comp_type, name, **nodes):
    return SimpleNamespace(
        comp_type=comp_type, name=name, value=0.0,
        node1=nodes.get('node1', ''), node2=nodes.get('node2', ''),
        node3=nodes.get('node3', ''), node4=nodes.get('node4', ''),
        dig_bits=nodes.get('dig_bits', 1), dig_clk=nodes.get('dig_clk', 'CLK'),
        dig_q_state=0, update=lambda: None,
    )


def test_digital_tick_evaluates_jk_t_sr_counter_and_mux():
    """Los componentes visibles se evalúan también fuera de co-simulación."""
    items = [
        _digital_item('JKFF', 'JK1', node1='QJK', node2='J', node3='K', dig_clk='CLK'),
        _digital_item('TFF', 'T1', node1='QT', node2='T', node3='CLK'),
        _digital_item('SRFF', 'SR1', node1='QSR', node2='S', node3='R'),
        _digital_item('COUNTER', 'CNT1', node1='QC', node2='CLK', dig_bits=3),
        _digital_item('MUX2', 'MUX1', node1='YM', node2='I0', node3='I1', node4='SEL'),
        _digital_item('LED', 'LED1', node1='QC', node2='0'),
    ]
    high = DEFAULT_STANDARD.Voh
    voltages = {'J': high, 'K': 0.0, 'T': high, 'S': high, 'R': 0.0,
                'I0': 0.0, 'I1': high, 'SEL': high, 'CLK': 0.0}

    pin_node = {'CNT1__p3': 'QC1', 'CNT1__p4': 'QC2'}
    MainWindow._evaluate_digital_gates(object(), pin_node, voltages, silent=True,
                                       sim_comps=items)
    voltages['CLK'] = high
    MainWindow._evaluate_digital_gates(object(), pin_node, voltages, silent=True,
                                       sim_comps=items)

    assert voltages['QJK'] == high
    assert voltages['QT'] == high
    assert voltages['QSR'] == high
    assert voltages['QC'] == high
    assert voltages['QC1'] == DEFAULT_STANDARD.Vol
    assert voltages['QC2'] == DEFAULT_STANDARD.Vol
    assert voltages['YM'] == high
    assert items[-1].led_on
