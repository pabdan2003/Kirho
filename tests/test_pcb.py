"""Prueba mínima del traspaso de una hoja a la placa."""
import unittest

from kirho.pcb import PcbBoard, build_pcb_board


class _Point:
    def __init__(self, x, y):
        self._x, self._y = x, y

    def x(self):
        return self._x

    def y(self):
        return self._y


class _Item:
    def __init__(self, comp_type, name, x, y, pins=2):
        self.comp_type = comp_type
        self.name = name
        self.value = 1000.0
        self.unit = 'Ω'
        self._pos = _Point(x, y)
        self._pins = pins
        self._angle = 0

    def pos(self):
        return self._pos

    def all_pin_positions_scene(self):
        return [object()] * self._pins


class _Scene:
    components = [_Item('R', 'R1', 0, 0), _Item('C', 'C1', 200, 100)]

    def extract_netlist(self):
        return {
            'R1__p1': 'VCC', 'R1__p2': 'N1',
            'C1__p1': 'N1', 'C1__p2': '0',
        }


class PcbTransferTest(unittest.TestCase):
    def test_builds_footprints_and_preserves_nets(self):
        board = build_pcb_board(_Scene())

        self.assertEqual([f.reference for f in board.footprints], ['R1', 'C1'])
        self.assertEqual([p.net for p in board.footprints[0].pads], ['VCC', 'N1'])
        self.assertEqual([p.net for p in board.footprints[1].pads], ['N1', '0'])
        self.assertEqual(len(board.pads_by_net()['N1']), 2)

    def test_board_round_trip_preserves_editable_positions(self):
        board = build_pcb_board(_Scene())
        board.footprints[0].x_mm = 42.5
        board.outline = (-5.0, -4.0, 80.0, 60.0)

        restored = PcbBoard.from_dict(board.to_dict())

        self.assertEqual(restored.footprints[0].x_mm, 42.5)
        self.assertEqual(restored.footprints[1].pads[1].net, '0')
        self.assertEqual(restored.outline, (-5.0, -4.0, 80.0, 60.0))


if __name__ == '__main__':
    unittest.main()
