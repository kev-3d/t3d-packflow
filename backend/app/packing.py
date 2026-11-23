from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass, field
from itertools import permutations
from typing import Any


EPSILON = 1e-7


@dataclass(frozen=True)
class Unit:
    uid: str
    sku: str
    name: str
    length: float
    width: float
    height: float
    weight: float
    rotation_allowed: bool = True
    fragile: bool = False

    @property
    def volume(self) -> float:
        return self.length * self.width * self.height

    def orientations(self) -> list[tuple[float, float, float]]:
        if not self.rotation_allowed:
            return [(self.length, self.width, self.height)]
        return sorted(set(permutations((self.length, self.width, self.height), 3)))


@dataclass(frozen=True)
class BoxSpec:
    id: int
    code: str
    name: str
    length: float
    width: float
    height: float
    empty_weight: float
    max_weight: float

    @property
    def volume(self) -> float:
        return self.length * self.width * self.height


@dataclass
class FreeSpace:
    x: float
    y: float
    z: float
    length: float
    width: float
    height: float

    @property
    def volume(self) -> float:
        return self.length * self.width * self.height


@dataclass
class Placement:
    unit: Unit
    x: float
    y: float
    z: float
    length: float
    width: float
    height: float


@dataclass
class PackedBox:
    spec: BoxSpec
    placements: list[Placement] = field(default_factory=list)
    free_spaces: list[FreeSpace] = field(default_factory=list)
    product_weight: float = 0

    def __post_init__(self) -> None:
        if not self.free_spaces:
            self.free_spaces = [
                FreeSpace(0, 0, 0, self.spec.length, self.spec.width, self.spec.height)
            ]

    @property
    def used_volume(self) -> float:
        return sum(p.unit.volume for p in self.placements)

    @property
    def utilization(self) -> float:
        return self.used_volume / self.spec.volume if self.spec.volume else 0

    @property
    def gross_weight(self) -> float:
        return self.product_weight + self.spec.empty_weight


def _fits(orientation: tuple[float, float, float], space: FreeSpace) -> bool:
    return all(
        item <= available + EPSILON
        for item, available in zip(
            orientation, (space.length, space.width, space.height), strict=True
        )
    )


def place_unit(box: PackedBox, unit: Unit, split_axis: int = 0) -> bool:
    if box.gross_weight + unit.weight > box.spec.max_weight + EPSILON:
        return False

    candidates: list[tuple[float, int, tuple[float, float, float]]] = []
    for space_index, space in enumerate(box.free_spaces):
        for orientation in unit.orientations():
            if _fits(orientation, space):
                leftover = space.volume - orientation[0] * orientation[1] * orientation[2]
                height_bias = space.z + orientation[2]
                candidates.append((leftover + height_bias * 0.001, space_index, orientation))

    if not candidates:
        return False

    _, space_index, orientation = min(candidates, key=lambda candidate: candidate[0])
    space = box.free_spaces.pop(space_index)
    length, width, height = orientation
    box.placements.append(
        Placement(unit, space.x, space.y, space.z, length, width, height)
    )
    box.product_weight += unit.weight

    # Three disjoint slabs, trying each axis order in the multi-start search.
    origin = [space.x, space.y, space.z]
    available = [space.length, space.width, space.height]
    size = [length, width, height]
    new_spaces = []
    for axis in [(split_axis + i) % 3 for i in range(3)]:
        slab_origin = origin.copy()
        slab_origin[axis] += size[axis]
        slab_size = available.copy()
        slab_size[axis] -= size[axis]
        new_spaces.append(FreeSpace(*slab_origin, *slab_size))
        available[axis] = size[axis]
    box.free_spaces.extend(
        candidate
        for candidate in new_spaces
        if candidate.length > EPSILON
        and candidate.width > EPSILON
        and candidate.height > EPSILON
    )
    box.free_spaces.sort(key=lambda free: (free.z, free.volume))
    return True


def simulate_box(spec: BoxSpec, units: list[Unit]) -> PackedBox:
    strategies = [
        lambda u: (u.volume, max(u.length, u.width, u.height), u.weight),
        lambda u: (max(u.length, u.width, u.height), u.volume, u.weight),
        lambda u: (u.weight, u.volume, max(u.length, u.width, u.height)),
    ]
    best = PackedBox(spec)
    for key in strategies:
        ordered = sorted(units, key=key, reverse=True)
        for axis in range(3):
            trial = PackedBox(spec)
            # Free space only ever shrinks, so a unit shape that failed once cannot fit later.
            failed: set[tuple[float, float, float, float, bool]] = set()
            for unit in ordered:
                signature = (unit.length, unit.width, unit.height, unit.weight, unit.rotation_allowed)
                if signature in failed:
                    continue
                if not place_unit(trial, unit, axis):
                    failed.add(signature)
            if (trial.used_volume, len(trial.placements)) > (best.used_volume, len(best.placements)):
                best = trial
            if len(best.placements) == len(units):
                return best
    return best


def _box_to_dict(box: PackedBox, sequence: int) -> dict[str, Any]:
    sku_summary: dict[str, dict[str, Any]] = {}
    for placement in box.placements:
        summary = sku_summary.setdefault(
            placement.unit.sku,
            {"sku": placement.unit.sku, "name": placement.unit.name, "quantity": 0},
        )
        summary["quantity"] += 1
    return {
        "sequence": sequence,
        "box_type_id": box.spec.id,
        "box_code": box.spec.code,
        "box_name": box.spec.name,
        "dimensions_cm": [box.spec.length, box.spec.width, box.spec.height],
        "utilization": round(box.utilization * 100, 1),
        "product_weight_kg": round(box.product_weight, 2),
        "gross_weight_kg": round(box.gross_weight, 2),
        "max_weight_kg": box.spec.max_weight,
        "empty_weight_kg": box.spec.empty_weight,
        "remaining_volume_l": round((box.spec.volume - box.used_volume) / 1000, 2),
        "items": list(sku_summary.values()),
        "free_spaces": [asdict(space) for space in box.free_spaces],
        "placements": [
            {
                "uid": placement.unit.uid,
                "sku": placement.unit.sku,
                "name": placement.unit.name,
                "position": [placement.x, placement.y, placement.z],
                "dimensions": [placement.length, placement.width, placement.height],
            }
            for placement in box.placements
        ],
    }


OBJECTIVES: dict[str, str] = {
    "fewest_boxes": "Fewest boxes",
    "lowest_dhl_cost": "Lower DHL shipping cost",
    "smallest_volume": "Smallest total carton volume",
}
DEFAULT_OBJECTIVE = "fewest_boxes"
# Ranking zone when the destination is unknown. Base rates rise with weight in every
# zone, so the ranking barely depends on it; the estimate is flagged as assumed.
DEFAULT_DHL_ZONE = 1


def pack_order(
    item_rows: list[dict[str, Any]], box_rows: list[dict[str, Any]],
    objective: str = DEFAULT_OBJECTIVE, dhl_zone: int | None = None,
) -> dict[str, Any]:
    """Pack an order for one objective and report the best plan for every objective.

    ``objective``: ``fewest_boxes`` (count, then carton volume), ``lowest_dhl_cost``
    (estimated DHL transport + piece surcharges, then count) or ``smallest_volume``
    (total carton volume = highest utilization, then count).
    """
    if objective not in OBJECTIVES:
        raise ValueError(f"Unknown packing objective {objective!r}.")
    units: list[Unit] = []
    for item in item_rows:
        for index in range(int(item["quantity"])):
            units.append(
                Unit(
                    uid=f"{item['sku']}-{index + 1}-{item.get('order_item_id', 0)}",
                    sku=item["sku"],
                    name=item["name"],
                    length=float(item["length_cm"]),
                    width=float(item["width_cm"]),
                    height=float(item["height_cm"]),
                    weight=float(item["weight_kg"]),
                    rotation_allowed=bool(item.get("rotation_allowed", True)),
                    fragile=bool(item.get("fragile", False)),
                )
            )

    specs = [
        BoxSpec(
            id=int(row["id"]),
            code=row["code"],
            name=row["name"],
            length=float(row["length_cm"]),
            width=float(row["width_cm"]),
            height=float(row["height_cm"]),
            empty_weight=float(row["empty_weight_kg"]),
            max_weight=float(row["max_weight_kg"]),
        )
        for row in box_rows
    ]
    specs.sort(key=lambda box: box.volume)

    zone = dhl_zone if dhl_zone in range(1, 7) else None
    candidates = _candidate_plans(units, specs)
    scored = [(plan, remaining, _plan_metrics(plan, zone)) for plan, remaining in candidates]
    best = {
        name: min(scored, key=lambda entry: (len(entry[1]), _objective_key(name, entry[2])))
        for name in OBJECTIVES
    }
    packed_boxes, remaining, _ = best[objective]
    result = _serialize_plan(packed_boxes, remaining, len(units))
    result["objective"] = objective
    result["objective_label"] = OBJECTIVES[objective]
    result["dhl_zone"] = zone or DEFAULT_DHL_ZONE
    result["dhl_zone_assumed"] = zone is None
    result["options"] = [
        {
            "objective": name,
            "label": label,
            "selected": name == objective,
            "complete": not best[name][1],
            **best[name][2],
            "boxes": _box_summary(best[name][0]),
        }
        for name, label in OBJECTIVES.items()
    ]
    return result


def _objective_key(objective: str, metrics: dict[str, Any]) -> tuple[float, ...]:
    count, volume = metrics["box_count"], metrics["total_volume_l"]
    cost = metrics["estimated_dhl_eur"]
    if objective == "lowest_dhl_cost":
        return (cost, count, volume)
    if objective == "smallest_volume":
        return (volume, count, cost)
    return (count, volume, cost)


def _box_summary(plan: list[PackedBox]) -> list[dict[str, Any]]:
    summary: dict[str, dict[str, Any]] = {}
    for box in plan:
        entry = summary.setdefault(box.spec.code, {"box_code": box.spec.code, "box_name": box.spec.name, "count": 0})
        entry["count"] += 1
    return list(summary.values())


def _plan_metrics(plan: list[PackedBox], zone: int | None) -> dict[str, Any]:
    """Box count, carton volume, utilization and the DHL figures that drive the price."""
    from . import dhl_pricing  # local import keeps packing usable without the tariff file

    tariff = dhl_pricing.load_tariff()
    divisor = tariff["meta"]["volumetric_divisor"]
    total_volume = sum(box.spec.volume for box in plan)
    item_volume = sum(box.used_volume for box in plan)
    chargeable = 0.0
    handling = 0.0
    for box in plan:
        dims = (box.spec.length, box.spec.width, box.spec.height)
        chargeable += max(box.gross_weight, box.spec.volume / divisor)
        handling += dhl_pricing.piece_handling(box.gross_weight, dims, tariff)["amount"]
    tariff_kg = dhl_pricing.tariff_weight(chargeable) if plan else 0.0
    transport = dhl_pricing.base_rate(tariff_kg, zone or DEFAULT_DHL_ZONE, tariff) if plan else 0.0
    return {
        "box_count": len(plan),
        "total_volume_l": round(total_volume / 1000, 2),
        "utilization": round(item_volume / total_volume * 100, 1) if total_volume else 0.0,
        "gross_weight_kg": round(sum(box.gross_weight for box in plan), 2),
        "chargeable_kg": round(chargeable, 2),
        "tariff_weight_kg": tariff_kg,
        "handling_eur": round(handling, 2),
        "estimated_dhl_eur": round(transport + handling, 2),
    }


def _contents_key(units: list[Unit]) -> tuple:
    """Same multiset of unit shapes -> same packing outcome, whatever the unit ids."""
    return tuple(sorted(
        (unit.length, unit.width, unit.height, unit.weight, unit.rotation_allowed) for unit in units
    ))


class _Packer:
    """Caches 'which is the smallest box type that holds these contents' by content shape."""

    def __init__(self, specs: list[BoxSpec]) -> None:
        self.specs = specs
        self._smallest: dict[tuple, int | None] = {}

    def smallest_box_for(self, units: list[Unit], max_volume: float | None = None) -> PackedBox | None:
        """Smallest box type holding every unit, optionally no larger than ``max_volume``."""
        key = (_contents_key(units), max_volume)
        if key in self._smallest:
            spec_id = self._smallest[key]
            if spec_id is None:
                return None
            spec = next(spec for spec in self.specs if spec.id == spec_id)
            return simulate_box(spec, units)
        total_volume = sum(unit.volume for unit in units)
        total_weight = sum(unit.weight for unit in units)
        for spec in self.specs:
            if max_volume is not None and spec.volume > max_volume + EPSILON:
                break
            if spec.volume + EPSILON < total_volume or spec.empty_weight + total_weight > spec.max_weight + EPSILON:
                continue
            trial = simulate_box(spec, units)
            if len(trial.placements) == len(units):
                self._smallest[key] = spec.id
                return trial
        self._smallest[key] = None
        return None

    def greedy(self, units: list[Unit], fill_specs: list[BoxSpec], score,
               close_max_volume: float | None = None) -> tuple[list[PackedBox], list[Unit]]:
        """Fill one box at a time with the fill box type scoring best on what is left; as
        soon as one box (up to ``close_max_volume``) takes everything left, close with the
        smallest such box."""
        remaining = list(units)
        plan: list[PackedBox] = []
        while remaining:
            closing = self.smallest_box_for(remaining, close_max_volume)
            if closing is not None:
                plan.append(closing)
                return plan, []
            best = None
            for spec in fill_specs:
                trial = simulate_box(spec, remaining)
                if trial.placements and (best is None or score(trial) > score(best)):
                    best = trial
            if best is None:
                break
            plan.append(best)
            packed_ids = {placement.unit.uid for placement in best.placements}
            remaining = [unit for unit in remaining if unit.uid not in packed_ids]
        return plan, remaining

    def merge_boxes(self, plan: list[PackedBox], window: int = 6) -> list[PackedBox]:
        """Merge two of the emptiest boxes into one box type while that saves a box
        (the merged box may be larger than the two together)."""
        improved = True
        while improved and len(plan) > 1:
            improved = False
            order = sorted(range(len(plan)), key=lambda i: plan[i].used_volume)[:window]
            for a_pos, a in enumerate(order):
                for b in order[a_pos + 1:]:
                    contents = [p.unit for p in plan[a].placements] + [p.unit for p in plan[b].placements]
                    merged = self.smallest_box_for(contents)
                    if merged is not None:
                        plan = [box for i, box in enumerate(plan) if i not in (a, b)] + [merged]
                        improved = True
                        break
                if improved:
                    break
        return plan

    def shrink(self, plan: list[PackedBox], full: float = 1.0) -> list[PackedBox]:
        """Swap each box for the smallest box type that still holds its contents.
        Boxes at least ``full`` utilized are kept as they are."""
        result = []
        for box in plan:
            if box.utilization >= full:
                result.append(box)
                continue
            smaller = self.smallest_box_for([placement.unit for placement in box.placements])
            result.append(smaller if smaller is not None and smaller.spec.volume < box.spec.volume else box)
        return result


def _candidate_plans(units: list[Unit], specs: list[BoxSpec],
                     max_boxes_per_type: int = 60) -> list[tuple[list[PackedBox], list[Unit]]]:
    """Diverse complete plans for the objectives to choose from.

    - fill with the box type that takes the most volume / the most units (few big boxes);
    - fill with one box type, closing with a box no larger than it (uniform cartons; small
      ones cut carton volume and DHL volumetric weight). Box types that would need more
      than ``max_boxes_per_type`` cartons are skipped.
    Each plan is kept as filled (shrunk to the smallest box per carton) and with its
    emptiest boxes merged (fewer cartons), so every objective has real alternatives.
    """
    if not units or not specs:
        return [([], list(units))]
    packer = _Packer(specs)
    by_volume = lambda box: (box.used_volume, -box.spec.volume)  # noqa: E731
    by_units = lambda box: (len(box.placements), -box.spec.volume)  # noqa: E731
    total_volume = sum(unit.volume for unit in units)
    runs: list[tuple[list[BoxSpec], Any, float | None, float]] = [
        (specs, by_volume, None, 1.0), (specs, by_units, None, 1.0),
    ]
    # Uniform cartons: only boxes that are not well filled are worth trying smaller.
    runs += [
        ([spec], by_volume, spec.volume, 0.8) for spec in specs
        if total_volume / spec.volume <= max_boxes_per_type
    ]
    candidates: list[tuple[list[PackedBox], list[Unit]]] = []
    seen: set[tuple] = set()

    def keep(plan: list[PackedBox], remaining: list[Unit]) -> None:
        signature = (tuple(sorted(
            (box.spec.id, _contents_key([p.unit for p in box.placements])) for box in plan
        )), len(remaining))
        if signature not in seen:
            seen.add(signature)
            candidates.append((plan, remaining))

    for fill_specs, score, close_max, full in runs:
        plan, remaining = packer.greedy(units, fill_specs, score, close_max)
        shrunk = packer.shrink(plan, full)
        keep(shrunk, remaining)
        keep(packer.shrink(packer.merge_boxes(shrunk)), remaining)
    return candidates


def _serialize_plan(packed_boxes: list[PackedBox], remaining: list[Unit], unit_count: int) -> dict[str, Any]:
    serialized = [
        _box_to_dict(box, index + 1) for index, box in enumerate(packed_boxes)
    ]
    total_box_volume = sum(box.spec.volume for box in packed_boxes)
    total_item_volume = sum(box.used_volume for box in packed_boxes)
    utilization = total_item_volume / total_box_volume if total_box_volume else 0
    total_weight = sum(box.gross_weight for box in packed_boxes)

    suggestions: list[dict[str, str]] = []
    low_fill = [box for box in serialized if box["utilization"] < 70]
    if low_fill:
        labels = ", ".join(f"#{box['sequence']}" for box in low_fill[:3])
        if len(serialized) > 1:
            suggestions.append(
                {
                    "type": "space",
                    "title": "Review box consolidation",
                    "detail": f"Boxes {labels} are below 70% utilization. Consider redistributing their contents.",
                }
            )
        else:
            remaining_liters = low_fill[0]["remaining_volume_l"]
            suggestions.append(
                {
                    "type": "space",
                    "title": "Available space",
                    "detail": f"There are {remaining_liters} L available. Each box is the smallest box type that holds its contents for the selected objective.",
                }
            )
    if utilization >= 0.85:
        suggestions.append(
            {
                "type": "success",
                "title": "Efficient layout",
                "detail": "Total utilization meets or exceeds the 85% operational target.",
            }
        )
    heavy = [box for box in serialized if box["gross_weight_kg"] / box["max_weight_kg"] > 0.85]
    if heavy:
        suggestions.append(
            {
                "type": "weight",
                "title": "Limited weight capacity remaining",
                "detail": "One or more boxes use over 85% of their weight capacity.",
            }
        )
    if remaining:
        suggestions.append(
            {
                "type": "warning",
                "title": "Unassigned units",
                "detail": f"{len(remaining)} units do not fit in the active boxes.",
            }
        )
    if not suggestions:
        suggestions.append(
            {
                "type": "info",
                "title": "Balanced plan",
                "detail": "There are no utilization or weight warnings for the selected plan.",
            }
        )

    return {
        "status": "complete" if not remaining else "partial",
        "box_count": len(serialized),
        "unit_count": unit_count,
        "average_utilization": round(utilization * 100, 1),
        "total_weight_kg": round(total_weight, 2),
        "unplaced": [
            {"sku": unit.sku, "name": unit.name, "uid": unit.uid} for unit in remaining
        ],
        "boxes": serialized,
        "suggestions": suggestions,
        "method": "3D packing across multiple orientations and partitions; best of several candidate plans for the selected objective",
    }


def fill_existing_plan(base_result: dict[str, Any], item_rows: list[dict[str, Any]],
                       additions: list[dict[str, Any]]) -> dict[str, Any]:
    """Add units only into actual free spaces, preserving all original placements."""
    products = {str(row["sku"]): row for row in item_rows}
    packed = []
    for box in base_result["boxes"]:
        spec = BoxSpec(box["box_type_id"], box["box_code"], box["box_name"],
                       *box["dimensions_cm"],
                       box["empty_weight_kg"], box["max_weight_kg"])
        restored = PackedBox(spec)
        restored.free_spaces = [FreeSpace(**space) for space in box["free_spaces"]]
        for placement in box["placements"]:
            row = products[placement["sku"]]
            unit = Unit(placement["uid"], str(row["sku"]), row["name"],
                        row["length_cm"], row["width_cm"], row["height_cm"],
                        row["weight_kg"], bool(row.get("rotation_allowed", True)), bool(row.get("fragile", False)))
            restored.placements.append(Placement(unit, *placement["position"], *placement["dimensions"]))
            restored.product_weight += unit.weight
        packed.append(restored)
    unplaced = []
    for row in additions:
        for index in range(int(row["quantity"])):
            unit = Unit(f"extra-{row['order_item_id']}-{index}", str(row["sku"]), row["name"],
                        row["length_cm"], row["width_cm"], row["height_cm"], row["weight_kg"],
                        bool(row.get("rotation_allowed", True)), bool(row.get("fragile", False)))
            choices = []
            for box_index, box in enumerate(packed):
                for axis in range(3):
                    trial = deepcopy(box)
                    if place_unit(trial, unit, axis):
                        choices.append((max((s.volume for s in trial.free_spaces), default=0),
                                        -box_index, trial))
            if choices:
                _, negative_index, trial = max(choices, key=lambda c: (c[0], c[1]))
                packed[-negative_index] = trial
            else:
                unplaced.append(unit)
    return _serialize_plan(packed, unplaced, base_result["unit_count"] + sum(int(r["quantity"]) for r in additions))
