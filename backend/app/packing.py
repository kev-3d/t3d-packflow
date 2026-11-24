from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from functools import lru_cache
from typing import Any


EPSILON = 1e-7


@dataclass(frozen=True)
class PackingSettings:
    """Physical packing rules. The defaults follow the DHL calculator reference app
    (``allowance: .5, gap: 0, rotate: true, caps: true, maxWeight: 20``)."""

    allowance_cm: float = 0.5   # wall allowance on every side of the carton
    gap_cm: float = 0.0         # spacing added to every unit dimension
    use_capacities: bool = True  # enforce the workbook "what fits" capacities
    allow_on_side: bool = True   # off: every product stays upright (a floor quarter turn is still allowed)
    max_parcel_kg: float | None = 20.0  # global parcel mass limit; a lower carton limit still applies


@lru_cache(maxsize=4096)
def _orientations(length: float, width: float, height: float, rotation_allowed: bool) -> tuple[tuple[float, float, float], ...]:
    if rotation_allowed:
        candidates = [
            (length, width, height), (length, height, width), (width, length, height),
            (width, height, length), (height, length, width), (height, width, length),
        ]
    else:
        # "Keep upright" still allows a quarter turn on the floor.
        candidates = [(length, width, height), (width, length, height)]
    return tuple(dict.fromkeys(candidates))


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
    capacity_family: str = ""

    @property
    def volume(self) -> float:
        return self.length * self.width * self.height

    @property
    def signature(self) -> tuple:
        """Units with the same signature are interchangeable for packing."""
        return (self.sku, self.length, self.width, self.height, self.weight,
                self.rotation_allowed, self.fragile, self.capacity_family)

    def orientations(self) -> tuple[tuple[float, float, float], ...]:
        return _orientations(self.length, self.width, self.height, self.rotation_allowed)


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
    # Workbook "what fits" capacities, keyed by capacity family (or product SKU).
    capacities: dict[str, float] = field(default_factory=dict, compare=False, hash=False)

    @property
    def volume(self) -> float:
        return self.length * self.width * self.height

    def capacity_for(self, unit: Unit) -> float | None:
        cap = self.capacities.get(unit.sku)
        if cap is None and unit.capacity_family:
            cap = self.capacities.get(unit.capacity_family)
        return cap if cap and cap > 0 else None


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
    allowance: float = 0.0
    gap: float = 0.0
    use_capacities: bool = True
    capacity_load: float = 0.0
    min_side: float = 0.0   # free spaces thinner than this cannot hold any unit of the order
    split: int = 0          # how this layout was built, so it can be replayed exactly
    offset: int = 0

    def __post_init__(self) -> None:
        if not self.free_spaces:
            inner = self.usable_dimensions
            if all(side > EPSILON for side in inner):
                a = self.allowance
                self.free_spaces = [FreeSpace(a, a, a, *inner)]

    @property
    def usable_dimensions(self) -> tuple[float, float, float]:
        a2 = 2 * self.allowance
        return tuple(max(0.0, side - a2) for side in (self.spec.length, self.spec.width, self.spec.height))

    @property
    def usable_volume(self) -> float:
        length, width, height = self.usable_dimensions
        return length * width * height

    @property
    def used_volume(self) -> float:
        return sum(p.unit.volume for p in self.placements)

    @property
    def utilization(self) -> float:
        usable = self.usable_volume
        return self.used_volume / usable if usable else 0

    @property
    def gross_weight(self) -> float:
        return self.product_weight + self.spec.empty_weight

    def capacity_share(self, unit: Unit) -> float:
        if not self.use_capacities:
            return 0.0
        cap = self.spec.capacity_for(unit)
        return 1 / cap if cap else 0.0

    def register(self, unit: Unit) -> None:
        self.product_weight += unit.weight
        self.capacity_load += self.capacity_share(unit)


def place_unit(box: PackedBox, unit: Unit, split: int = 0, orientation_offset: int = 0) -> bool:
    """Place one unit bottom-left-back, as the reference engine does.

    The lowest free space wins, then the earliest orientation (rotated by
    ``orientation_offset`` for multi-start), then the back-left position. The space
    left above the unit always has the unit's own footprint, so every unit rests fully
    on the floor or on the units below it. Nothing is stacked on a fragile unit.
    """
    if box.gross_weight + unit.weight > box.spec.max_weight + EPSILON:
        return False
    if box.capacity_load + box.capacity_share(unit) > 1 + 1e-9:
        return False

    orientations = unit.orientations()
    start = orientation_offset % len(orientations)
    orientations = orientations[start:] + orientations[:start]
    gap = box.gap
    best = None
    best_key = None
    for space_index, space in enumerate(box.free_spaces):
        # Coordinates are sums of the same dimensions, so plain comparison is stable enough.
        if best_key is not None and space.z > best_key[0] + EPSILON:
            continue
        for orientation_index, (length, width, height) in enumerate(orientations):
            el, ew, eh = length + gap, width + gap, height + gap
            if el > space.length + EPSILON or ew > space.width + EPSILON or eh > space.height + EPSILON:
                continue
            key = (space.z, orientation_index, space.y, space.x)
            if best_key is None or key < best_key:
                best_key = key
                best = (space_index, (length, width, height), (el, ew, eh))
            break  # later orientations in this space only score worse
    if best is None:
        return False

    space_index, (length, width, height), (el, ew, eh) = best
    space = box.free_spaces.pop(space_index)
    box.placements.append(Placement(unit, space.x, space.y, space.z, length, width, height))
    box.register(unit)

    if split == 0:
        new_spaces = [
            FreeSpace(space.x + el, space.y, space.z, space.length - el, space.width, space.height),
            FreeSpace(space.x, space.y + ew, space.z, el, space.width - ew, space.height),
        ]
    else:
        new_spaces = [
            FreeSpace(space.x + el, space.y, space.z, space.length - el, ew, space.height),
            FreeSpace(space.x, space.y + ew, space.z, space.length, space.width - ew, space.height),
        ]
    if not unit.fragile:
        new_spaces.append(FreeSpace(space.x, space.y, space.z + eh, el, ew, space.height - eh))
    minimum = max(box.min_side, EPSILON)
    box.free_spaces.extend(
        candidate for candidate in new_spaces
        if candidate.length > minimum - EPSILON and candidate.width > minimum - EPSILON
        and candidate.height > minimum - EPSILON
    )
    return True


def clone_box(box: PackedBox) -> PackedBox:
    """Cheap copy for trial placements. Free spaces and placements are never changed in
    place (``place_unit`` pops and appends), so copying the lists is enough."""
    copy = PackedBox(box.spec, placements=list(box.placements), free_spaces=[FreeSpace(0, 0, 0, 0, 0, 0)],
                     product_weight=box.product_weight, allowance=box.allowance, gap=box.gap,
                     use_capacities=box.use_capacities, capacity_load=box.capacity_load,
                     min_side=box.min_side, split=box.split, offset=box.offset)
    copy.free_spaces = list(box.free_spaces)  # may be empty for a full box
    return copy


def new_box(spec: BoxSpec, settings: PackingSettings, min_side: float = 0.0,
            split: int = 0, offset: int = 0) -> PackedBox:
    return PackedBox(spec, allowance=settings.allowance_cm, gap=settings.gap_cm,
                     use_capacities=settings.use_capacities, min_side=min_side,
                     split=split, offset=offset)


def _product_groups(units: list[Unit]) -> list[list[Unit]]:
    groups: dict[tuple, list[Unit]] = {}
    for unit in units:
        groups.setdefault(unit.signature, []).append(unit)
    return list(groups.values())


def _min_side(units: list[Unit], gap: float) -> float:
    return min((min(u.length, u.width, u.height) for u in units), default=0.0) + gap


def pack_attempts(spec: BoxSpec, units: list[Unit], settings: PackingSettings,
                  stop_when_complete: bool = True) -> list[PackedBox]:
    """Multi-start packing of one carton, as the reference ``packBox``: three product
    orders x every orientation offset x two split variants. Products are placed one
    after another; a product stops at its first unit that does not fit (free space only
    shrinks, so identical units cannot fit later). Distinct outcomes, fullest first."""
    if not units:
        return []
    probe = new_box(spec, settings)
    if not probe.free_spaces or spec.empty_weight >= spec.max_weight:
        return []
    groups = _product_groups(units)
    by_volume = sorted(groups, key=lambda g: (-g[0].volume, g[0].sku))
    by_sku = sorted(groups, key=lambda g: (g[0].sku, -g[0].volume))
    orders = list({tuple(id(g) for g in order): order for order in (by_volume, by_sku, by_sku[::-1])}.values())
    offsets = max(len(g[0].orientations()) for g in groups)
    min_side = _min_side(units, settings.gap_cm)
    attempts: list[PackedBox] = []
    seen: set[tuple] = set()
    for order in orders:
        for offset in range(offsets):
            for split in (0, 1):
                box = new_box(spec, settings, min_side, split, offset)
                for group in order:
                    for unit in group:
                        if not place_unit(box, unit, split, offset):
                            break
                key = tuple(sorted((p.unit.signature for p in box.placements), key=repr))
                if key in seen:
                    continue
                seen.add(key)
                attempts.append(box)
                if stop_when_complete and len(box.placements) == len(units):
                    return [box]
    attempts.sort(key=lambda b: (-b.used_volume, -len(b.placements)))
    return attempts


def simulate_box(spec: BoxSpec, units: list[Unit], settings: PackingSettings | None = None) -> PackedBox:
    settings = settings or PackingSettings()
    attempts = pack_attempts(spec, units, settings)
    return attempts[0] if attempts else new_box(spec, settings)


def replay_layout(source: PackedBox, units: list[Unit]) -> PackedBox:
    """Rebuild ``source`` (or a prefix of it) with other, interchangeable units.

    Placement is deterministic, so placing the same unit shapes in the same order with
    the same split/offset reproduces the layout, including its free spaces. A prefix of
    a layout is always physically valid: a unit is only ever supported by units placed
    before it.
    """
    box = PackedBox(source.spec, allowance=source.allowance, gap=source.gap,
                    use_capacities=source.use_capacities, min_side=source.min_side,
                    split=source.split, offset=source.offset)
    for unit in units:
        if not place_unit(box, unit, source.split, source.offset):
            raise ValueError("A packing layout could not be reproduced.")
    return box


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
        "usable_dimensions_cm": [round(side, 3) for side in box.usable_dimensions],
        "allowance_cm": box.allowance,
        "gap_cm": box.gap,
        "capacities": dict(box.spec.capacities),
        "capacity_load_percent": round(box.capacity_load * 100, 1),
        "utilization": round(box.utilization * 100, 1),
        "product_weight_kg": round(box.product_weight, 2),
        "gross_weight_kg": round(box.gross_weight, 2),
        "max_weight_kg": box.spec.max_weight,
        "empty_weight_kg": box.spec.empty_weight,
        "remaining_volume_l": round((box.usable_volume - box.used_volume) / 1000, 2),
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


def _parse_capacities(value: Any) -> dict[str, float]:
    if not value:
        return {}
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return {}
    if not isinstance(value, dict):
        return {}
    return {str(key): float(cap) for key, cap in value.items() if isinstance(cap, (int, float)) and cap > 0}


def build_units(item_rows: list[dict[str, Any]], allow_on_side: bool = True) -> list[Unit]:
    units: list[Unit] = []
    for item in item_rows:
        for index in range(int(item["quantity"])):
            units.append(
                Unit(
                    uid=f"{item['sku']}-{index + 1}-{item.get('order_item_id', 0)}",
                    sku=str(item["sku"]),
                    name=item["name"],
                    length=float(item["length_cm"]),
                    width=float(item["width_cm"]),
                    height=float(item["height_cm"]),
                    weight=float(item["weight_kg"]),
                    rotation_allowed=allow_on_side and bool(item.get("rotation_allowed", True)),
                    fragile=bool(item.get("fragile", False)),
                    capacity_family=str(item.get("capacity_family") or ""),
                )
            )
    return units


def _parcel_limit(box_limit: float, max_parcel_kg: float | None) -> float:
    return min(box_limit, max_parcel_kg) if max_parcel_kg and max_parcel_kg > 0 else box_limit


# Box columns with the user's theoretical "how many fit" counts, per capacity family.
REFERENCE_CAPACITY_COLUMNS = (("50ml", "reference_50ml"), ("glide_250ml", "reference_250ml"))


def _box_capacities(row: dict[str, Any]) -> dict[str, float]:
    """Workbook capacities, with the box's 50 ml / 250 ml reference counts taking precedence.
    They cap how many units of that family a box takes; the 3D layout still has to fit."""
    capacities = _parse_capacities(row.get("capacities"))
    for family, column in REFERENCE_CAPACITY_COLUMNS:
        value = row.get(column)
        if isinstance(value, (int, float)) and value > 0:
            capacities[family] = float(value)
    return capacities


def build_specs(box_rows: list[dict[str, Any]], max_parcel_kg: float | None = None) -> list[BoxSpec]:
    specs = [
        BoxSpec(
            id=int(row["id"]),
            code=row["code"],
            name=row["name"],
            length=float(row["length_cm"]),
            width=float(row["width_cm"]),
            height=float(row["height_cm"]),
            empty_weight=float(row["empty_weight_kg"]),
            max_weight=_parcel_limit(float(row["max_weight_kg"]), max_parcel_kg),
            capacities=_box_capacities(row),
        )
        for row in box_rows
    ]
    specs.sort(key=lambda box: box.volume)
    return specs


def pack_order(
    item_rows: list[dict[str, Any]], box_rows: list[dict[str, Any]],
    objective: str = DEFAULT_OBJECTIVE, dhl_zone: int | None = None,
    settings: PackingSettings | None = None,
) -> dict[str, Any]:
    """Pack an order for one objective and report the best plan for every objective.

    ``objective``: ``fewest_boxes`` (count, then carton volume), ``lowest_dhl_cost``
    (estimated DHL transport + piece surcharges, then count) or ``smallest_volume``
    (total carton volume, then count). Every candidate plan is verified independently;
    plans that fail verification are never selected.
    """
    from .cost_search import cost_search  # local import: cost_search builds on this module

    if objective not in OBJECTIVES:
        raise ValueError(f"Unknown packing objective {objective!r}.")
    settings = settings or PackingSettings()
    units = build_units(item_rows, settings.allow_on_side)
    specs = build_specs(box_rows, settings.max_parcel_kg)

    zone = dhl_zone if dhl_zone in range(1, 7) else None
    candidates = _candidate_plans(units, specs, settings)
    cost_plans, search = cost_search(units, specs, settings, zone or DEFAULT_DHL_ZONE)
    candidates += [(plan, []) for plan in cost_plans]

    verified = []
    rejected: list[str] = []
    for plan, remaining in candidates:
        issues = verify_plan(plan, remaining, units, settings)
        if issues:
            rejected.extend(issues)
        else:
            verified.append((plan, remaining))
    pool = verified or candidates
    scored = [(plan, remaining, _plan_metrics(plan, zone)) for plan, remaining in pool]
    best = {
        name: min(scored, key=lambda entry: (len(entry[1]), _objective_key(name, entry[2])))
        for name in OBJECTIVES
    }
    packed_boxes, remaining, _ = best[objective]
    issues = verify_plan(packed_boxes, remaining, units, settings)
    result = _serialize_plan(packed_boxes, remaining, len(units))
    result["objective"] = objective
    result["objective_label"] = OBJECTIVES[objective]
    result["dhl_zone"] = zone or DEFAULT_DHL_ZONE
    result["dhl_zone_assumed"] = zone is None
    result["settings"] = asdict(settings)
    result["search"] = search
    result["verification"] = {
        "passed": not issues,
        "issues": issues,
        "candidates_checked": len(candidates),
        "candidates_rejected": len(candidates) - len(verified),
    }
    if issues:
        result["suggestions"].insert(0, {
            "type": "warning",
            "title": "Layout failed verification",
            "detail": "; ".join(sorted(set(issues))[:3]) + ". Review the plan before packing.",
        })
    if objective == "lowest_dhl_cost" and not search["complete"]:
        result["suggestions"].append({"type": "info", "title": "Cost search limit reached", "detail": search["note"]})
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
    usable_volume = sum(box.usable_volume for box in plan)
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
        "utilization": round(item_volume / usable_volume * 100, 1) if usable_volume else 0.0,
        "gross_weight_kg": round(sum(box.gross_weight for box in plan), 2),
        "chargeable_kg": round(chargeable, 2),
        "tariff_weight_kg": tariff_kg,
        "handling_eur": round(handling, 2),
        "estimated_dhl_eur": round(transport + handling, 2),
    }


def _contents_key(units: list[Unit]) -> tuple:
    """Same multiset of interchangeable units -> same packing outcome, whatever the ids."""
    return tuple(sorted((unit.signature for unit in units), key=repr))


def _relabel(box: PackedBox, units: list[Unit]) -> PackedBox:
    """A copy of ``box`` whose placements use ``units`` (same signatures, other ids)."""
    pools: dict[tuple, list[Unit]] = {}
    for unit in units:
        pools.setdefault(unit.signature, []).append(unit)
    copy = clone_box(box)
    copy.placements = [
        Placement(pools[p.unit.signature].pop(0), p.x, p.y, p.z, p.length, p.width, p.height)
        for p in box.placements
    ]
    return copy


class _Packer:
    """Packs cartons for one order, caching results by content shape."""

    def __init__(self, specs: list[BoxSpec], settings: PackingSettings) -> None:
        self.specs = specs
        self.settings = settings
        self._smallest: dict[tuple, int | None] = {}
        self._simulated: dict[tuple, PackedBox] = {}

    def simulate(self, spec: BoxSpec, units: list[Unit]) -> PackedBox:
        key = (spec.id, _contents_key(units))
        if key not in self._simulated:
            self._simulated[key] = simulate_box(spec, units, self.settings)
        return _relabel(self._simulated[key], units)

    def fits_all(self, spec: BoxSpec, units: list[Unit]) -> bool:
        probe = new_box(spec, self.settings)
        total_volume = sum(unit.volume for unit in units)
        total_weight = sum(unit.weight for unit in units)
        load = sum(probe.capacity_share(unit) for unit in units)
        return (probe.usable_volume + EPSILON >= total_volume
                and spec.empty_weight + total_weight <= spec.max_weight + EPSILON
                and load <= 1 + 1e-9)

    def smallest_box_for(self, units: list[Unit], max_volume: float | None = None) -> PackedBox | None:
        """Smallest box type holding every unit, optionally no larger than ``max_volume``."""
        key = (_contents_key(units), max_volume)
        if key in self._smallest:
            spec_id = self._smallest[key]
            if spec_id is None:
                return None
            spec = next(spec for spec in self.specs if spec.id == spec_id)
            return self.simulate(spec, units)
        for spec in self.specs:
            if max_volume is not None and spec.volume > max_volume + EPSILON:
                break
            if not self.fits_all(spec, units):
                continue
            trial = self.simulate(spec, units)
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
                trial = self.simulate(spec, remaining)
                if trial.placements and (best is None or score(trial) > score(best)):
                    best = trial
            if best is None:
                break
            plan.append(best)
            packed_ids = {placement.unit.uid for placement in best.placements}
            remaining = [unit for unit in remaining if unit.uid not in packed_ids]
        return plan, remaining

    def reference_greedy(self, units: list[Unit], mode: str, max_boxes: int = 80) -> tuple[list[PackedBox], list[Unit]]:
        """The reference ``solve`` loop: one carton at a time, choosing among every box type.

        ``volume``: least carton volume per packed volume; ``balanced``: the same with a
        5,000 cm3 penalty per parcel; ``parcels``: a box that takes everything left, then
        the most packed volume, then the smaller box.
        """
        remaining = list(units)
        plan: list[PackedBox] = []
        for _ in range(max_boxes):
            if not remaining:
                break
            options = [trial for spec in self.specs if (trial := self.simulate(spec, remaining)).placements]
            if not options:
                break
            if mode == "parcels":
                best = min(options, key=lambda b: (len(b.placements) != len(remaining), -b.used_volume, b.spec.volume))
            else:
                penalty = 5000.0 if mode == "balanced" else 0.0
                best = min(options, key=lambda b: ((b.spec.volume + penalty) / b.used_volume, b.spec.volume))
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


def _candidate_plans(units: list[Unit], specs: list[BoxSpec], settings: PackingSettings,
                     max_boxes_per_type: int = 60) -> list[tuple[list[PackedBox], list[Unit]]]:
    """Diverse plans for the objectives to choose from.

    - the reference app's three greedy strategies (volume, parcels, balanced) and every
      single box type that takes the whole order;
    - fill with the box type that takes the most volume / the most units (few big boxes);
    - fill with one box type, closing with a box no larger than it (uniform cartons). Box
      types that would need more than ``max_boxes_per_type`` cartons are skipped.
    Each plan is kept as filled (shrunk to the smallest box per carton) and with its
    emptiest boxes merged (fewer cartons), so every objective has real alternatives.
    """
    if not units or not specs:
        return [([], list(units))]
    packer = _Packer(specs, settings)
    candidates: list[tuple[list[PackedBox], list[Unit]]] = []
    seen: set[tuple] = set()

    def keep(plan: list[PackedBox], remaining: list[Unit]) -> None:
        signature = (tuple(sorted(
            ((box.spec.id, _contents_key([p.unit for p in box.placements])) for box in plan), key=repr
        )), len(remaining))
        if signature not in seen:
            seen.add(signature)
            candidates.append((plan, remaining))

    for spec in specs:
        if packer.fits_all(spec, units):
            whole = packer.simulate(spec, units)
            if len(whole.placements) == len(units):
                keep([whole], [])
    for mode in ("volume", "parcels", "balanced"):
        plan, remaining = packer.reference_greedy(units, mode)
        keep(plan, remaining)
        keep(packer.shrink(packer.merge_boxes(packer.shrink(plan))), remaining)

    by_volume = lambda box: (box.used_volume, -box.spec.volume)  # noqa: E731
    by_units = lambda box: (len(box.placements), -box.spec.volume)  # noqa: E731
    total_volume = sum(unit.volume for unit in units)
    runs: list[tuple[list[BoxSpec], Any, float | None, float]] = [
        (specs, by_volume, None, 1.0), (specs, by_units, None, 1.0),
    ]
    runs += [
        ([spec], by_volume, spec.volume, 0.8) for spec in specs
        if spec.volume and total_volume / spec.volume <= max_boxes_per_type
    ]
    for fill_specs, score, close_max, full in runs:
        plan, remaining = packer.greedy(units, fill_specs, score, close_max)
        shrunk = packer.shrink(plan, full)
        keep(shrunk, remaining)
        keep(packer.shrink(packer.merge_boxes(shrunk)), remaining)
    return candidates


def verify_plan(plan: list[PackedBox], remaining: list[Unit], units: list[Unit],
                settings: PackingSettings | None = None) -> list[str]:
    """Independent check of a plan, after the reference ``verifyPlan``: every unit inside
    the usable carton, in an allowed orientation, without overlaps, fully supported, not
    resting on a fragile unit; weight and workbook capacity respected; and every ordered
    unit either packed exactly once or reported as unplaced."""
    issues: list[str] = []
    for index, box in enumerate(plan, start=1):
        label = f"Box {index}"
        a = box.allowance
        usable = box.usable_dimensions
        limits = (a + usable[0], a + usable[1], a + usable[2])
        if box.gross_weight > box.spec.max_weight + 1e-6:
            issues.append(f"{label}: weight limit exceeded")
        load = sum(box.capacity_share(p.unit) for p in box.placements)
        if load > 1 + 1e-9:
            issues.append(f"{label}: workbook capacity exceeded")
        if abs(box.product_weight - sum(p.unit.weight for p in box.placements)) > 1e-6:
            issues.append(f"{label}: weight total mismatch")
        placements = box.placements
        for i, p in enumerate(placements):
            dims = (p.length, p.width, p.height)
            if dims not in p.unit.orientations():
                issues.append(f"{label}: invalid orientation")
            origin = (p.x, p.y, p.z)
            if any(o < a - 1e-6 or o + d > limit + 1e-6 for o, d, limit in zip(origin, dims, limits)):
                issues.append(f"{label}: unit outside the usable carton")
            for q in placements[:i]:
                if (p.x < q.x + q.length - 1e-6 and q.x < p.x + p.length - 1e-6
                        and p.y < q.y + q.width - 1e-6 and q.y < p.y + p.width - 1e-6
                        and p.z < q.z + q.height - 1e-6 and q.z < p.z + p.height - 1e-6):
                    issues.append(f"{label}: overlapping units")
            if p.z > a + 1e-6:
                covered = 0.0
                for q in placements:
                    top = q.z + q.height
                    if q is p or not (p.z - box.gap - 1e-6 <= top <= p.z + 1e-6):
                        continue
                    overlap = (max(0.0, min(p.x + p.length, q.x + q.length) - max(p.x, q.x))
                               * max(0.0, min(p.y + p.width, q.y + q.width) - max(p.y, q.y)))
                    if overlap > 1e-9 and q.unit.fragile:
                        issues.append(f"{label}: unit stacked on a fragile unit")
                    covered += overlap
                if covered < p.length * p.width - 1e-6:
                    issues.append(f"{label}: unit not fully supported")
    packed = [p.unit.uid for box in plan for p in box.placements]
    accounted = packed + [unit.uid for unit in remaining]
    if len(packed) != len(set(packed)):
        issues.append("A unit is packed more than once")
    if sorted(accounted) != sorted(unit.uid for unit in units):
        issues.append("Packed and unplaced units do not match the order")
    return issues


def _serialize_plan(packed_boxes: list[PackedBox], remaining: list[Unit], unit_count: int) -> dict[str, Any]:
    serialized = [
        _box_to_dict(box, index + 1) for index, box in enumerate(packed_boxes)
    ]
    total_usable_volume = sum(box.usable_volume for box in packed_boxes)
    total_item_volume = sum(box.used_volume for box in packed_boxes)
    utilization = total_item_volume / total_usable_volume if total_usable_volume else 0
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
        "method": (
            "3D packing, bottom-left-back with full support, wall allowance and workbook capacities; "
            "every candidate plan verified; lowest DHL cost from a pattern search"
        ),
    }


def fill_existing_plan(base_result: dict[str, Any], item_rows: list[dict[str, Any]],
                       additions: list[dict[str, Any]]) -> dict[str, Any]:
    """Add units only into actual free spaces, preserving all original placements."""
    products = {str(row["sku"]): row for row in item_rows}
    allow_on_side = bool((base_result.get("settings") or {}).get("allow_on_side", True))

    def make_unit(uid: str, row: dict[str, Any]) -> Unit:
        rotation = allow_on_side and bool(row.get("rotation_allowed", True))
        return Unit(uid, str(row["sku"]), row["name"], float(row["length_cm"]), float(row["width_cm"]),
                    float(row["height_cm"]), float(row["weight_kg"]), rotation,
                    bool(row.get("fragile", False)), str(row.get("capacity_family") or ""))

    packed = []
    for box in base_result["boxes"]:
        spec = BoxSpec(box["box_type_id"], box["box_code"], box["box_name"],
                       *box["dimensions_cm"], box["empty_weight_kg"], box["max_weight_kg"],
                       capacities=_parse_capacities(box.get("capacities")))
        restored = PackedBox(spec, free_spaces=[FreeSpace(**space) for space in box["free_spaces"]],
                             allowance=float(box.get("allowance_cm", 0.0)), gap=float(box.get("gap_cm", 0.0)))
        for placement in box["placements"]:
            unit = make_unit(placement["uid"], products[placement["sku"]])
            restored.placements.append(Placement(unit, *placement["position"], *placement["dimensions"]))
            restored.register(unit)
        packed.append(restored)
    unplaced = []
    every_unit = [p.unit for box in packed for p in box.placements]
    for row in additions:
        for index in range(int(row["quantity"])):
            unit = make_unit(f"extra-{row['order_item_id']}-{index}", row)
            every_unit.append(unit)
            choices = []
            for box_index, box in enumerate(packed):
                for split in (0, 1):
                    trial = clone_box(box)
                    if place_unit(trial, unit, split):
                        choices.append((max((s.volume for s in trial.free_spaces), default=0),
                                        -box_index, trial))
            if choices:
                _, negative_index, trial = max(choices, key=lambda c: (c[0], c[1]))
                packed[-negative_index] = trial
            else:
                unplaced.append(unit)
    result = _serialize_plan(packed, unplaced, base_result["unit_count"] + sum(int(r["quantity"]) for r in additions))
    issues = verify_plan(packed, unplaced, every_unit)
    result["verification"] = {"passed": not issues, "issues": issues, "candidates_checked": 1,
                              "candidates_rejected": int(bool(issues))}
    return result
