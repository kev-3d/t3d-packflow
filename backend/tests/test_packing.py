import random
import unittest

from app.packing import (
    BoxSpec,
    PackedBox,
    PackingSettings,
    Placement,
    Unit,
    pack_order,
    place_unit,
    simulate_box,
    verify_plan,
)

EXACT = PackingSettings(allowance_cm=0)


def item(sku="A-1", quantity=1, dims=(10, 10, 10), weight=0.5, **extra):
    return {"order_item_id": 1, "sku": sku, "name": sku, "quantity": quantity,
            "length_cm": dims[0], "width_cm": dims[1], "height_cm": dims[2], "weight_kg": weight,
            "rotation_allowed": 1, "fragile": 0, **extra}


def box(id=1, dims=(40, 30, 20), max_weight=10, empty=0.5, **extra):
    return {"id": id, "code": f"B{id}", "name": f"Box {id}", "length_cm": dims[0], "width_cm": dims[1],
            "height_cm": dims[2], "empty_weight_kg": empty, "max_weight_kg": max_weight, **extra}


class PackingTests(unittest.TestCase):
    def setUp(self):
        self.boxes = [box(1, (40, 30, 20), max_weight=10)]

    def test_packs_units_without_exceeding_weight(self):
        result = pack_order([item(quantity=4, dims=(20, 15, 10), weight=2)], self.boxes, settings=EXACT)
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["box_count"], 1)
        self.assertLessEqual(result["boxes"][0]["gross_weight_kg"], 10)
        self.assertEqual(result["boxes"][0]["utilization"], 50.0)
        self.assertTrue(result["verification"]["passed"])

    def test_reports_item_that_does_not_fit(self):
        result = pack_order([item("BIG", dims=(80, 60, 50), weight=2)], self.boxes)
        self.assertEqual(result["status"], "partial")
        self.assertEqual(len(result["unplaced"]), 1)


class ReferenceRulesTests(unittest.TestCase):
    """The rules adopted from the DHL calculator reference app (AUDIT_REPORT.md)."""

    def test_every_unit_is_fully_supported(self):
        # F1: the old three-slab split left units over empty space in about 3% of boxes.
        rng = random.Random(7)
        for trial in range(120):
            spec = BoxSpec(1, "B", "B", rng.choice([30, 35, 40]), rng.choice([20, 25, 30]), rng.choice([15, 20, 25]), 0.3, 25)
            kinds = [(rng.randint(4, 20), rng.randint(4, 15), rng.randint(3, 15)) for _ in range(rng.randint(1, 3))]
            units = [Unit(f"u{trial}-{i}", f"S{i % len(kinds)}", "S", *rng.choice(kinds), 0.2) for i in range(rng.randint(5, 40))]
            packed = simulate_box(spec, units, EXACT)
            issues = verify_plan([packed], [u for u in units if u.uid not in {p.unit.uid for p in packed.placements}], units, EXACT)
            self.assertEqual(issues, [], f"trial {trial}")

    def test_upright_units_may_still_turn_on_the_floor(self):
        # F6: a non-rotatable 30 x 10 x 10 unit only fits a 12 x 32 x 12 box turned 90 degrees.
        result = pack_order([item(dims=(30, 10, 10), rotation_allowed=0)], [box(1, (12, 32, 12))], settings=EXACT)
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["boxes"][0]["placements"][0]["dimensions"], [10, 30, 10])

    def test_nothing_is_stacked_on_a_fragile_unit(self):
        # F7: the box fits two cubes on top of each other, but not on a fragile one.
        rows = [item(quantity=2, fragile=1, rotation_allowed=0)]
        result = pack_order(rows, [box(1, (10, 10, 20))], settings=EXACT)
        self.assertEqual(result["box_count"], 2)
        rows = [item(quantity=2, rotation_allowed=0)]
        self.assertEqual(pack_order(rows, [box(1, (10, 10, 20))], settings=EXACT)["box_count"], 1)

    def test_wall_allowance_reduces_the_usable_carton(self):
        # F3: a 10 cm cube does not fit a 10 cm carton once 0.5 cm per side is reserved.
        rows = [item()]
        self.assertEqual(pack_order(rows, [box(1, (10, 10, 10))], settings=EXACT)["status"], "complete")
        result = pack_order(rows, [box(1, (10, 10, 10))])
        self.assertEqual(result["status"], "partial")
        fits = pack_order(rows, [box(1, (11, 11, 11))])
        self.assertEqual(fits["boxes"][0]["placements"][0]["position"], [0.5, 0.5, 0.5])
        self.assertEqual(fits["boxes"][0]["usable_dimensions_cm"], [10, 10, 10])

    def test_workbook_capacities_limit_units_per_box(self):
        # F4: geometry fits 20 bottles, but the workbook says the box holds 14 of the family.
        rows = [item("BOTTLE", quantity=20, dims=(4, 4, 12), weight=0.085, capacity_family="50ml")]
        boxes = [box(1, (30, 20, 13), max_weight=20, capacities={"50ml": 14})]
        result = pack_order(rows, boxes, settings=EXACT)
        self.assertEqual(result["box_count"], 2)
        self.assertTrue(all(len(b["placements"]) <= 14 for b in result["boxes"]))
        without = pack_order(rows, boxes, settings=PackingSettings(allowance_cm=0, use_capacities=False))
        self.assertEqual(without["box_count"], 1)

    def test_family_members_share_one_capacity_pool(self):
        rows = [item("A", quantity=8, dims=(4, 4, 12), capacity_family="50ml"),
                dict(item("B", quantity=8, dims=(4, 4, 12), capacity_family="50ml"), order_item_id=2)]
        result = pack_order(rows, [box(1, (30, 20, 13), max_weight=20, capacities={"50ml": 14})], settings=EXACT)
        self.assertEqual(result["box_count"], 2)

    def test_verification_catches_an_overlap(self):
        # F2: an impossible layout is reported, not accepted.
        spec = BoxSpec(1, "B", "B", 20, 20, 20, 0.1, 10)
        packed = PackedBox(spec)
        a, b = Unit("a", "S", "S", 10, 10, 10, 1), Unit("b", "S", "S", 10, 10, 10, 1)
        self.assertTrue(place_unit(packed, a))
        packed.placements.append(Placement(b, 5, 5, 0, 10, 10, 10))
        packed.register(b)
        self.assertIn("Box 1: overlapping units", verify_plan([packed], [], [a, b]))

    def test_lowest_cost_comes_from_the_pattern_search(self):
        # F5: light cubes; two small boxes cost less than one large one in DHL volumetric weight.
        rows = [item(quantity=16, dims=(6, 6, 6), weight=0.05)]
        boxes = [box(1, (12, 12, 12), max_weight=20, empty=0.2), box(2, (30, 30, 30), max_weight=20, empty=0.2)]
        result = pack_order(rows, boxes, "lowest_dhl_cost", dhl_zone=1, settings=EXACT)
        self.assertEqual(result["box_count"], 2)
        self.assertTrue(result["search"]["complete"])
        self.assertTrue(result["verification"]["passed"])

    def test_imported_boxes_default_to_20_kg(self):
        # F8: the DHL calculator uses 20 kg per parcel.
        from app.extraction import DEFAULT_BOX_MAX_WEIGHT_KG
        self.assertEqual(DEFAULT_BOX_MAX_WEIGHT_KG, 20.0)

    def test_global_switch_keeps_every_product_upright(self):
        # A 30 x 10 x 10 unit only fits a 12 x 12 x 32 box standing on its end.
        rows, boxes = [item(dims=(30, 10, 10))], [box(1, (12, 12, 32))]
        self.assertEqual(pack_order(rows, boxes, settings=EXACT)["status"], "complete")
        upright = PackingSettings(allowance_cm=0, allow_on_side=False)
        self.assertEqual(pack_order(rows, boxes, settings=upright)["status"], "partial")

    def test_global_parcel_limit_overrides_heavier_cartons(self):
        # Five 5 kg units in a 25 kg carton: 20 kg per parcel forces a second box.
        rows, boxes = [item(quantity=4, weight=5)], [box(1, (40, 40, 40), max_weight=25, empty=0)]
        self.assertEqual(pack_order(rows, boxes, settings=PackingSettings(allowance_cm=0, max_parcel_kg=None))["box_count"], 1)
        rows = [item(quantity=5, weight=5)]
        result = pack_order(rows, boxes, settings=EXACT)
        self.assertEqual(result["box_count"], 2)
        self.assertTrue(all(b["gross_weight_kg"] <= 20 for b in result["boxes"]))
        self.assertEqual(result["boxes"][0]["max_weight_kg"], 20)



class ReferenceCapacityTests(unittest.TestCase):
    def _box(self, **extra):
        return {"id": 1, "code": "B", "name": "Box", "length_cm": 40, "width_cm": 40, "height_cm": 20,
                "empty_weight_kg": 0.3, "max_weight_kg": 25, "capacities": '{"50ml": 100}', **extra}

    def test_reference_counts_override_workbook_capacities(self):
        from app.packing import build_specs
        spec = build_specs([self._box(reference_50ml=12, reference_250ml=None)])[0]
        self.assertEqual(spec.capacities["50ml"], 12)
        self.assertNotIn("glide_250ml", spec.capacities)
        self.assertEqual(build_specs([self._box()])[0].capacities["50ml"], 100)

    def test_reference_count_caps_units_per_box(self):
        bottle = {"order_item_id": 1, "sku": "MO", "name": "50 ml", "quantity": 20, "length_cm": 3.6,
                  "width_cm": 3.6, "height_cm": 16.5, "weight_kg": 0.085, "capacity_family": "50ml"}
        result = pack_order([bottle], [self._box(reference_50ml=12)])
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["box_count"], 2)
        self.assertTrue(all(len(b["placements"]) <= 12 for b in result["boxes"]))

if __name__ == "__main__":
    unittest.main()
