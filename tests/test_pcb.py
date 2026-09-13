"""Prueba mínima del traspaso de una hoja a la placa."""
import unittest

from kirho.pcb import (
    PcbBoard, PcbFootprint, PcbPad, PcbRule, PcbSilk, PcbTrack, PcbVia,
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
    def test_own_packages_preserve_numbered_nets_and_roundtrip(self):
        for kind, pins in (('BJT_NPN', 3), ('POT', 2), ('SPST', 2),
                            ('SPDT', 3), ('DPDT', 6), ('RELAY', 4),
                            ('XFMR', 4), ('BRIDGE', 4)):
            scene = _Scene()
            item = _Item(kind, 'X1', 0, 0, pins=pins)
            item.footprint_name = footprint_names_for_type(kind)[-1]
            self.assertTrue(item.footprint_name.startswith('Kirho_'))
            scene.components = [item]
            board = build_pcb_board(scene)
            footprint = board.footprints[0]
            self.assertEqual(len(footprint.pads), pins)
            self.assertTrue(footprint.silkscreen)
            self.assertEqual(PcbBoard.from_dict(board.to_dict()).to_dict(), board.to_dict())

    def test_silkscreen_snapshot_and_legacy_dip_do_not_move_copper(self):
        board = build_pcb_board(_Scene())
        self.assertEqual(len(board.footprints[0].silkscreen), 3)
        self.assertEqual(board.footprints[1].silkscreen[0].kind, 'circle')
        board.footprints[0].silkscreen.append(
            PcbSilk('polyline', ((-1, -3), (1, -3)), 0.2))
        self.assertEqual(PcbBoard.from_dict(board.to_dict()).to_dict(), board.to_dict())
        dip = resolve_footprint('IC555')
        self.assertAlmostEqual(dip.pads[1].y_mm - dip.pads[0].y_mm, 2.54)
        self.assertAlmostEqual(dip.pads[7].x_mm - dip.pads[0].x_mm, 7.62)
        soic = resolve_footprint('IC555', 'SOIC-8 1.27mm')
        self.assertAlmostEqual(soic.pads[7].x_mm - soic.pads[0].x_mm, 5.4)
        self.assertAlmostEqual(soic.pads[1].y_mm - soic.pads[0].y_mm, 1.27)
        legacy = board.to_dict()
        raw = legacy['footprints'][0]
        raw.pop('silkscreen')
        raw['footprint_name'] = dip.name
        raw['pads'][0]['y_mm'], raw['pads'][1]['y_mm'] = -8.89, -2.54
        restored = PcbBoard.from_dict(legacy)
        self.assertEqual(restored.to_dict()['footprints'][0]['pads'], raw['pads'])
        self.assertTrue(any('DIP-8' in w for w in restored.warnings))
        self.assertEqual(PcbBoard.from_dict(restored.to_dict()).to_dict(), restored.to_dict())
        with self.assertRaises(ValueError):
            PcbSilk('circle', ((0, 0), (1, 1)), float('nan'))

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
        scene.components[0].node1 = 'MANUAL_VCC'

        board = build_pcb_board(scene)

        self.assertEqual(footprint_names_for_type('R'),
                         ('R_Axial_P10.16mm', 'R_0805'))
        self.assertEqual(resolve_footprint('R', 'R_0805').name, 'R_0805')
        resistor = board.footprints[0]
        self.assertEqual(resistor.footprint_name, 'R_0805')
        self.assertEqual(resistor.pads[0].pad_type, 'smd')
        self.assertEqual(resistor.pads[0].drill_mm, 0.0)
        self.assertEqual(resistor.pads[0].net, 'MANUAL_VCC')

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
            ['F.Cu', 'B.Cu', 'F.SilkS', 'B.SilkS', 'F.Mask', 'B.Mask', 'Edge.Cuts'],
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

        self.assertEqual(len(restored.layers), 7)
        self.assertEqual(restored.tracks, [])
        self.assertEqual(restored.vias, [])
        self.assertEqual(restored.footprints[0].pads[0].shape, 'rect')
        self.assertEqual(restored.footprints[0].pads[0].drill_mm, 1.0)
        with self.assertRaises(ValueError):
            PcbBoard.from_dict({'width_mm': 'nan'})

    def test_continuity_uses_copper_shapes_crossings_and_vias(self):
        first = PcbFootprint('R1', 'R', '', '', 2, 10, 0, 2, 2,
                             pads=[PcbPad(1, 0, 0, 'N', drill_mm=0,
                                          pad_type='smd', layers=('F.Cu',))])
        second = PcbFootprint('R2', 'R', '', '', 10, 2, 0, 2, 2,
                              pads=[PcbPad(1, 0, 0, 'N', drill_mm=0,
                                           pad_type='smd', layers=('F.Cu',))])
        board = PcbBoard(footprints=[first, second], tracks=[
            PcbTrack('N', points=[(2, 10), (18, 10)]),
            PcbTrack('N', points=[(10, 2), (10, 18)])])
        self.assertTrue(board.routing_status()['N']['complete'])
        second.side = 'B.Cu'
        board.tracks[1].layer = 'B.Cu'
        self.assertFalse(board.routing_status()['N']['complete'])
        board.vias.append(PcbVia(10, 10, 'N'))
        self.assertTrue(board.routing_status()['N']['complete'])
        board.vias.clear()
        self.assertEqual(len(board.unrouted_connections()), 1)

        second.side = 'F.Cu'
        first.x_mm, first.y_mm = 5, 5
        first.pads[0].width_mm, first.pads[0].height_mm = 4, 0.4
        first.pads[0].shape = 'rect'
        second.x_mm, second.y_mm = 15, 6
        board.tracks = [PcbTrack('N', points=[(5, 6), (15, 6)])]
        self.assertFalse(board.routing_status()['N']['complete'])
        first.pads[0].x_mm = 2
        first.angle, first.side = 90, 'B.Cu'
        x, y = first.pad_position(first.pads[0])
        self.assertAlmostEqual(x, 5)
        self.assertAlmostEqual(y, 3)
        self.assertEqual(first.pad_layers(first.pads[0]), ('B.Cu',))

    def test_routing_clearance_and_drc_share_geometry(self):
        board = PcbBoard(outline=(10, 20, 20, 20), tracks=[
            PcbTrack('BLOCK', width_mm=0.2, points=[(20, 22), (20, 38)])])
        crossing = PcbTrack('N', points=[(12, 30), (28, 30)])
        self.assertIsNotNone(board.routing_error(crossing))
        crossing.layer = 'B.Cu'
        self.assertIsNone(board.routing_error(crossing))
        self.assertIsNotNone(board.routing_error(PcbVia(10.2, 30, 'N')))
        self.assertIsNotNone(board.routing_error(PcbVia(20, 30, 'N')))
        self.assertIsNone(board.routing_error(PcbVia(15, 30, 'N')))
        crossing.layer = 'F.Cu'
        board.tracks += [crossing, PcbTrack('NEAR', width_mm=0.2,
                                           points=[(20.3, 22), (20.3, 25)])]
        board.vias = [PcbVia(31, 30, 'N'), PcbVia(15, 25, 'N', drill_mm=1)]
        codes = {violation.code for violation in board.check_drc()}
        self.assertTrue({'short', 'clearance', 'edge', 'geometry', 'dangling'} <= codes)


if __name__ == '__main__':
    unittest.main()
