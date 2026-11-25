"""Lowest-cost carton selection, after the DHL calculator reference app (``cost-engine``).

1. Content patterns: for every box type, the contents it can verifiably hold, built
   from real layouts: a single-product fill per product group (used up to whatever is
   still open, since every prefix of a layout is itself a valid layout), the whole
   remaining order, and bounded mixed combinations checked by packing them.
2. Search: a memoized walk over the remaining quantities that combines patterns into
   complete plans, keeping only Pareto-best labels (chargeable weight, piece surcharges,
   parcel count). The DHL estimate is the base rate on the total chargeable weight plus
   the piece surcharges, exactly as ``packing._plan_metrics`` prices a plan.
3. Fixed work limits. When one is reached the result says so: the cheapest plan found
   is then not a proven minimum.

Products with identical geometry, weight and capacity family share one group, as in the
reference, so variants of the same bottle do not multiply the search.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from typing import Any

from . import dhl_pricing
from .packing import (
    BoxSpec,
    PackedBox,
    PackingSettings,
    Unit,
    new_box,
    pack_attempts,
    replay_layout,
)


@dataclass(frozen=True)
class SearchLimits:
    patterns: int = 400      # fixed (mixed) content patterns kept
    vectors: int = 80        # mixed combinations checked by packing them
    states: int = 20000      # remaining-quantity states expanded
    transitions: int = 400000
    labels: int = 6          # Pareto labels kept per state
    max_parcels: int = 40
    max_chargeable_kg: float = 3000.0


def _group_key(unit: Unit) -> tuple:
    return (unit.length, unit.width, unit.height, unit.weight, unit.rotation_allowed,
            unit.fragile, unit.capacity_family)


@dataclass
class _Pattern:
    box_index: int
    source: PackedBox            # layout to replay (a prefix of it for single-group fills)
    counts: tuple[int, ...]      # fixed contents; for a single-group fill, its maximum
    single: int | None = None    # group index of a single-group fill
    cumulative_weight: list[float] = field(default_factory=list)


class _LimitReached(Exception):
    pass


def cost_search(units: list[Unit], specs: list[BoxSpec], settings: PackingSettings, zone: int,
                limits: SearchLimits = SearchLimits()) -> tuple[list[list[PackedBox]], dict[str, Any]]:
    info: dict[str, Any] = {"complete": True, "patterns": 0, "states": 0, "work": 0,
                            "note": "All content combinations were explored within the layout model."}
    if not units or not specs:
        return [], info
    tariff = dhl_pricing.load_tariff()
    divisor = tariff["meta"]["volumetric_divisor"]

    groups: dict[tuple, list[Unit]] = {}
    for unit in units:
        groups.setdefault(_group_key(unit), []).append(unit)
    keys = list(groups)
    index_of = {key: i for i, key in enumerate(keys)}
    totals = tuple(len(groups[key]) for key in keys)

    patterns: list[_Pattern] = []
    seen: set[tuple] = set()
    complete = True

    def counts_of(placements) -> tuple[int, ...]:
        counts = [0] * len(keys)
        for placement in placements:
            counts[index_of[_group_key(placement.unit)]] += 1
        return tuple(counts)

    def add_fixed(box_index: int, source: PackedBox) -> None:
        nonlocal complete
        counts = counts_of(source.placements)
        if sum(1 for count in counts if count) < 2 or (box_index, counts) in seen:
            return  # single-group contents are covered by the single-group fills
        if sum(1 for p in patterns if p.single is None) >= limits.patterns:
            complete = False
            return
        seen.add((box_index, counts))
        patterns.append(_Pattern(box_index, source, counts))

    vectors_checked = 0
    for box_index, spec in enumerate(specs):
        for group_index, key in enumerate(keys):
            attempts = pack_attempts(spec, groups[key], settings)
            if attempts and attempts[0].placements:
                best = attempts[0]
                cumulative, total = [0.0], 0.0
                for placement in best.placements:
                    total += placement.unit.weight
                    cumulative.append(total)
                counts = [0] * len(keys)
                counts[group_index] = len(best.placements)
                patterns.append(_Pattern(box_index, best, tuple(counts), group_index, cumulative))
        if len(keys) > 1:
            for attempt in pack_attempts(spec, units, settings, stop_when_complete=False)[:3]:
                add_fixed(box_index, attempt)
            if complete:
                vectors_checked, complete = _mixed_patterns(
                    spec, box_index, keys, groups, totals, settings, add_fixed, vectors_checked, limits, complete)

    info["patterns"] = len(patterns)
    if not patterns:
        info.update(complete=complete, note="No carton holds any of the ordered products.")
        return [], info

    price_cache: dict[tuple[int, int], tuple[float, float]] = {}

    def piece(pattern_index: int, length: int) -> tuple[float, float]:
        """(chargeable kg, piece surcharge) of a pattern using its first ``length`` units."""
        cached = price_cache.get((pattern_index, length))
        if cached is None:
            pattern = patterns[pattern_index]
            spec = pattern.source.spec
            if pattern.single is not None:
                gross = spec.empty_weight + pattern.cumulative_weight[length]
            else:
                gross = spec.empty_weight + sum(p.unit.weight for p in pattern.source.placements)
            dims = (spec.length, spec.width, spec.height)
            cached = (max(gross, spec.volume / divisor), dhl_pricing.piece_handling(gross, dims, tariff)["amount"])
            price_cache[(pattern_index, length)] = cached
        return cached

    def quote(weight: float, handling: float) -> float:
        return dhl_pricing.base_rate(dhl_pricing.tariff_weight(weight), zone, tariff) + handling

    order = sorted(range(len(patterns)), key=lambda i: (-sum(patterns[i].counts), patterns[i].box_index))
    by_first = [[i for i in order if patterns[i].counts[g]] for g in range(len(keys))]
    memo: dict[tuple[int, ...], list[tuple]] = {}
    work = {"states": 0, "transitions": 0}

    def dominated(a, b) -> bool:  # is ``a`` no better than ``b`` on every criterion?
        return a[0] >= b[0] - 1e-9 and a[1] >= b[1] - 1e-9 and a[2] >= b[2]

    def solve(remaining: tuple[int, ...]):
        if not any(remaining):
            return [(0.0, 0.0, 0, ())]
        cached = memo.get(remaining)
        if cached is not None:
            return cached
        work["states"] += 1
        if work["states"] > limits.states:
            raise _LimitReached
        # Some carton of any complete plan holds the first product still open, so only
        # patterns containing it need to be tried here (the order of cartons is irrelevant).
        first = next(i for i, count in enumerate(remaining) if count)
        labels: list[tuple] = []
        for pattern_index in by_first[first]:
            pattern = patterns[pattern_index]
            work["transitions"] += 1
            if work["transitions"] > limits.transitions:
                raise _LimitReached
            if pattern.single is not None:
                # Fill the box with as many of this product as are still open.
                length = min(remaining[first], pattern.counts[first])
                used = tuple(length if i == first else 0 for i in range(len(remaining)))
            else:
                if any(c > r for c, r in zip(pattern.counts, remaining)):
                    continue
                length = len(pattern.source.placements)
                used = pattern.counts
            weight, handling = piece(pattern_index, length)
            rest = tuple(r - u for r, u in zip(remaining, used))
            for sub_weight, sub_handling, parcels, path in solve(rest):
                label = (sub_weight + weight, sub_handling + handling, parcels + 1, ((pattern_index, length),) + path)
                if label[2] > limits.max_parcels or label[0] > limits.max_chargeable_kg:
                    continue
                if any(dominated(label, other) for other in labels):
                    continue
                labels = [other for other in labels if not dominated(other, label)] + [label]
        if len(labels) > limits.labels:
            labels.sort(key=lambda l: (quote(l[0], l[1]), l[2], l[0]))
            del labels[limits.labels:]
        memo[remaining] = labels
        return labels

    recursion = sys.getrecursionlimit()
    sys.setrecursionlimit(max(recursion, 10000))
    try:
        roots = solve(totals)
    except _LimitReached:
        complete = False
        roots = memo.get(totals, [])
    finally:
        sys.setrecursionlimit(recursion)

    info.update(states=work["states"], work=work["transitions"] + vectors_checked, complete=complete)
    if not complete:
        info["note"] = "Search limit reached: this is the lowest estimated cost found, not a proven minimum."
    if not roots:
        if complete:
            info["note"] = "No complete combination of the tested carton contents holds the whole order."
        return [], info

    roots = sorted(roots, key=lambda l: (quote(l[0], l[1]), l[2], l[0]))
    plans: list[list[PackedBox]] = []
    seen_sets: set[tuple] = set()
    for _, _, _, path in roots:
        box_set = tuple(sorted(patterns[i].box_index for i, _ in path))
        if box_set in seen_sets:
            continue
        seen_sets.add(box_set)
        plans.append(_materialize([(patterns[i], length) for i, length in path], groups))
        if len(plans) == 4:
            break
    return plans, info


def _mixed_patterns(spec, box_index, keys, groups, totals, settings, add_fixed, checked, limits, complete):
    probe = new_box(spec, settings)
    usable = probe.usable_volume
    reps = [groups[key][0] for key in keys]
    bounds = []
    for i, rep in enumerate(reps):
        bound = min(totals[i], int(usable // rep.volume) if rep.volume else totals[i])
        share = probe.capacity_share(rep)
        if share:
            bound = min(bound, int(1 / share + 1e-9))
        bounds.append(bound)
    vector = [0] * len(keys)

    def walk(i: int, volume: float, weight: float, load: float) -> bool:
        nonlocal checked, complete
        if i == len(keys):
            if sum(1 for count in vector if count) < 2:
                return True
            checked += 1
            if checked > limits.vectors:
                complete = False
                return False
            chosen = [unit for key, count in zip(keys, vector) for unit in groups[key][:count]]
            attempts = pack_attempts(spec, chosen, settings)
            if attempts and len(attempts[0].placements) == len(chosen):
                add_fixed(box_index, attempts[0])
            return True
        rep = reps[i]
        share = probe.capacity_share(rep)
        # Largest counts first: full mixed cartons matter most for cost.
        for count in range(bounds[i], -1, -1):
            v, w, l = volume + count * rep.volume, weight + count * rep.weight, load + count * share
            if v > usable + 1e-9 or w > spec.max_weight + 1e-9 or l > 1 + 1e-9:
                continue
            vector[i] = count
            if not walk(i + 1, v, w, l):
                vector[i] = 0
                return False
        vector[i] = 0
        return True

    walk(0, 0.0, spec.empty_weight, 0.0)
    return checked, complete


def _materialize(chosen: list[tuple[_Pattern, int]], groups: dict[tuple, list[Unit]]) -> list[PackedBox]:
    """Rebuild every carton of a plan with real, distinct units of the order."""
    pools = {key: list(units) for key, units in groups.items()}
    plan = []
    for pattern, length in chosen:
        order = [pools[_group_key(p.unit)].pop(0) for p in pattern.source.placements[:length]]
        plan.append(replay_layout(pattern.source, order))
    return plan
