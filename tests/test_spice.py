import pytest

from ohmpy.spice import export_netlist, format_value, parse_netlist, parse_value


def test_spice_numbers_use_standard_milli_and_meg_suffixes():
    assert parse_value("4.7k") == 4700
    assert parse_value("2m") == 0.002
    assert parse_value("1Meg") == 1_000_000
    assert parse_value("10uF") == pytest.approx(10e-6)
    assert format_value(0.0000047) == "4.7u"


def test_parse_basic_spice_primitives_and_ac_source():
    result = parse_netlist("""Example
V1 in 0 DC 5
R1 in out 1k
C1 out 0 10u
V2 ac 0 DC 0 AC 1 90
.op
""")

    assert [(item.name, item.kind, item.node1, item.node2)
            for item in result.elements] == [
                ("V1", "V", "in", "0"), ("R1", "R", "in", "out"),
                ("C1", "C", "out", "0"), ("V2", "VAC", "ac", "0"),
            ]
    assert [item.value for item in result.elements] == pytest.approx([5.0, 1000.0, 10e-6, 1.0])
    assert result.elements[-1].phase_deg == 90.0
    assert result.warnings == []


def test_export_uses_extracted_nodes_instead_of_placeholder_nodes():
    class Item:
        comp_type = "R"
        name = "R1"
        node1 = ""
        node2 = ""
        value = 1000.0

    text, warnings = export_netlist([Item()], {"R1__p1": "in", "R1__p2": "0"}, "Test")

    assert "R1 in 0 1k" in text
    assert "?" not in text
    assert warnings == []
