"""Prueba mínima del traspaso de una hoja a la placa."""
import unittest

from kirho.pcb import (
    PcbBoard, PcbFootprint, PcbPad, PcbRule, PcbTrack, PcbVia,
    build_pcb_board, footprint_names_for_type, mil_to_mm, mm_to_mil,
    resolve_footprint,
)


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
        first_pad = board.footprints[0].pads[0]
        self.assertEqual(first_pad.shape, 'rect')
        self.assertEqual(first_pad.pad_type, 'tht')
        self.assertEqual(first_pad.layers, ('F.Cu', 'B.Cu'))
        self.assertEqual(first_pad.drill_mm, 1.0)

    def test_units_and_pad_geometry_round_trip(self):
        self.assertAlmostEqual(mil_to_mm(1000), 25.4)
        self.assertAlmostEqual(mm_to_mil(25.4), 1000)
        board = PcbBoard(footprints=[PcbFootprint(
            reference='U1',
            component_type='IC',
            value='QFN',
            footprint_name='QFN-4',
            x_mm=12.0,
            y_mm=8.0,
            angle=0.0,
            width_mm=5.0,
            height_mm=5.0,
            side='F.Cu',
            courtyard_margin_mm=0.25,
            pads=[PcbPad(
                number=1,
                x_mm=-1.5,
                y_mm=0.0,
                net='VCC',
                width_mm=1.2,
                height_mm=0.6,
                shape='roundrect',
                drill_mm=0.0,
                pad_type='smd',
                layers=('F.Cu',),
            )],
        )])

        restored = PcbBoard.from_dict(board.to_dict())

        pad = restored.footprints[0].pads[0]
        self.assertEqual(restored.to_dict()['units'], 'mm')
        self.assertEqual(restored.footprints[0].side, 'F.Cu')
        self.assertEqual(restored.footprints[0].courtyard_margin_mm, 0.25)
        self.assertEqual((pad.width_mm, pad.height_mm), (1.2, 0.6))
        self.assertEqual((pad.shape, pad.drill_mm, pad.pad_type),
                         ('roundrect', 0.0, 'smd'))
        self.assertEqual(pad.layers, ('F.Cu',))

    def test_component_footprint_assignment_selects_library_package(self):
        scene = _Scene()
        scene.components = [
            _Item('R', 'R1', 0, 0),
            _Item('C', 'C1', 200, 100),
        ]
        scene.components[0].footprint_name = 'R_0805'

        board = build_pcb_board(scene)

        self.assertEqual(footprint_names_for_type('R'),
                         ('R_Axial_P10.16mm', 'R_0805'))
        self.assertEqual(resolve_footprint('R', 'R_0805').name, 'R_0805')
        resistor = board.footprints[0]
        self.assertEqual(resistor.footprint_name, 'R_0805')
        self.assertEqual(resistor.pads[0].pad_type, 'smd')
        self.assertEqual(resistor.pads[0].drill_mm, 0.0)

    def test_board_round_trip_preserves_editable_positions(self):
        board = build_pcb_board(_Scene())
        board.footprints[0].x_mm = 42.5
        board.outline = (-5.0, -4.0, 80.0, 60.0)

        restored = PcbBoard.from_dict(board.to_dict())

        self.assertEqual(restored.footprints[0].x_mm, 42.5)
        self.assertEqual(restored.footprints[1].pads[1].net, '0')
        self.assertEqual(restored.outline, (-5.0, -4.0, 80.0, 60.0))

    def test_board_round_trip_preserves_routing_model(self):
        board = PcbBoard(
            tracks=[PcbTrack(
                net='N1',
                layer='B.Cu',
                width_mm=0.3,
                points=[(1.0, 2.0), (3.0, 2.0), (3.0, 5.0)],
            )],
            vias=[PcbVia(
                x_mm=3.0,
                y_mm=2.0,
                net='N1',
                drill_mm=0.35,
                diameter_mm=0.8,
            )],
            rules=PcbRule(
                clearance_mm=0.15,
                min_track_width_mm=0.25,
                min_drill_mm=0.3,
                edge_clearance_mm=0.2,
            ),
        )

        restored = PcbBoard.from_dict(board.to_dict())

        self.assertEqual(
            [layer.name for layer in restored.layers],
            ['F.Cu', 'B.Cu', 'F.SilkS', 'B.SilkS', 'Edge.Cuts'],
        )
        self.assertEqual(restored.tracks[0].points,
                         [(1.0, 2.0), (3.0, 2.0), (3.0, 5.0)])
        self.assertEqual(restored.tracks[0].layer, 'B.Cu')
        self.assertEqual(restored.vias[0].diameter_mm, 0.8)
        self.assertEqual(restored.rules.clearance_mm, 0.15)

    def test_legacy_board_gets_new_defaults(self):
        restored = PcbBoard.from_dict({
            'footprints': [{
                'reference': 'R1',
                'component_type': 'R',
                'value': '1 kΩ',
                'footprint_name': 'old',
                'x_mm': 10.0,
                'y_mm': 10.0,
                'angle': 0.0,
                'width_mm': 12.0,
                'height_mm': 4.0,
                'pads': [{
                    'number': 1,
                    'x_mm': -5.08,
                    'y_mm': 0.0,
                    'net': 'N1',
                }],
            }],
        })

        self.assertEqual(len(restored.layers), 5)
        self.assertEqual(restored.tracks, [])
        self.assertEqual(restored.vias, [])
        self.assertEqual(restored.footprints[0].pads[0].shape, 'rect')
        self.assertEqual(restored.footprints[0].pads[0].drill_mm, 1.0)


if __name__ == '__main__':
    unittest.main()
