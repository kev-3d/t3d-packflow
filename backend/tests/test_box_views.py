"""Geometry behind the packing guide: draw order, layers, orientation and step wording."""
import unittest

from app.box_views import Unit, draw_order, grid_shape, layer_span, layer_step, layers, orientation


def stick_box():
    """35 x 25 x 16 box: 48 sticks standing (8 x 6) and 16 lying on top, like order P2624730."""
    placements = [{"sku": "1" if i + j * 8 < 25 else "2", "position": [i * 4, j * 4, 0], "dimensions": [4, 4, 12]}
                  for j in range(6) for i in range(8)]
    placements += [{"sku": "2", "position": [a * 12, b * 4, 12], "dimensions": [12, 4, 4]}
                   for a in range(2) for b in range(6)]
    placements += [{"sku": "2", "position": [24 + c * 4, d * 12, 12], "dimensions": [4, 12, 4]}
                   for c in range(2) for d in range(2)]
    return [Unit.from_placement(p) for p in placements]


PRODUCTS = {"1": {"code": "MPP2018", "dims": [4.0, 4.0, 12.0]}, "2": {"code": "MO2016", "dims": [4.0, 4.0, 12.0]}}


class BoxViewTests(unittest.TestCase):
    def test_layers_follow_start_height(self):
        plan = layers(stick_box())
        self.assertEqual([len(layer["units"]) for layer in plan], [48, 16])
        self.assertEqual((plan[0]["z0"], plan[0]["z1"]), (0, 12))
        self.assertEqual(plan[0]["counts"], {"1": 25, "2": 23})
        self.assertEqual(layer_span(plan, 1), "12–16 cm")

    def test_layer_steps_describe_orientation_and_grid(self):
        plan = layers(stick_box())
        self.assertEqual(layer_step(1, plan[0], PRODUCTS), ("Layer 1: 48 standing, 8 × 6", "25 MPP2018 · 23 MO2016"))
        self.assertEqual(layer_step(2, plan[1], PRODUCTS)[0], "Layer 2: 16 lying")  # mixed footprints: no grid

    def test_orientation(self):
        standing = Unit("1", 0, 0, 0, 4, 4, 12)
        lying = Unit("1", 0, 0, 0, 12, 4, 4)
        side = Unit("1", 0, 0, 0, 10, 2, 6)
        self.assertEqual(orientation(standing, [4, 4, 12]), "standing")
        self.assertEqual(orientation(lying, [4, 4, 12]), "lying")
        self.assertEqual(orientation(side, [2, 6, 10]), "on its side")
        self.assertEqual(orientation(Unit("1", 0, 0, 0, 5, 5, 5), [5, 5, 5]), "")
        self.assertEqual(orientation(standing, None), "")

    def test_draw_order_paints_far_units_first(self):
        # A long bar at the back (small y) and a cube in front of it: the bar must be painted first,
        # even though the cube's x + y + z sum is smaller.
        bar = Unit("1", 0, 0, 0, 30, 4, 4)
        cube = Unit("2", 0, 5, 0, 4, 9, 4)
        self.assertEqual(draw_order([cube, bar]), [bar, cube])
        top = Unit("1", 0, 0, 4, 4, 4, 8)
        bottom = Unit("1", 0, 0, 0, 4, 4, 4)
        self.assertEqual(draw_order([top, bottom]), [bottom, top])
        self.assertEqual(len(draw_order(stick_box())), 64)

    def test_irregular_layers_say_where_they_start(self):
        tall = Unit("1", 0, 0, 0, 10, 10, 20)
        short = Unit("1", 10, 0, 0, 20, 10, 5)
        on_short = Unit("1", 10, 0, 5, 20, 10, 10)
        plan = layers([tall, short, on_short])
        self.assertEqual(layer_span(plan, 1), "from 5 cm")
        self.assertIsNone(grid_shape([tall, Unit("1", 10, 0, 0, 15, 10, 5)]))  # different footprints
        self.assertEqual(grid_shape([tall, short]), (2, 1))


if __name__ == "__main__":
    unittest.main()
