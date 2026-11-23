"""Printable views of a packed box for the packing team: an isometric 3D view and layer plans.

Engine coordinates (cm): x runs along the box length, y along its width, z is height.
The packer stands at the FRONT, the y = width side, so x reads left to right.
In the isometric view the visible faces are the top, the right (x = x1) and the front (y = y1).
"""
from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass
from typing import Any

from reportlab.lib import colors
from reportlab.platypus import Flowable

EPS = 1e-6
COS30, SIN30 = math.cos(math.pi / 6), 0.5

# Print colors per product: (top face, right face, front face, edges). Strong enough for laser print.
PRODUCT_COLORS = [
    ("#F0997B", "#D85A30", "#B5532E", "#712B13"),  # coral
    ("#85B7EB", "#5C9AD8", "#3F7FBF", "#0C447C"),  # blue
    ("#97C459", "#7CAA3E", "#639922", "#27500A"),  # green
    ("#FAC775", "#EF9F27", "#BA7517", "#633806"),  # amber
    ("#AFA9EC", "#8F87E0", "#7F77DD", "#3C3489"),  # purple
    ("#ED93B1", "#DB6F95", "#D4537E", "#72243E"),  # pink
    ("#5DCAA5", "#33B088", "#1D9E75", "#085041"),  # teal
    ("#B4B2A9", "#9C9A91", "#888780", "#444441"),  # gray
]
FLOOR = colors.HexColor("#F1EFE8")
GHOST = colors.HexColor("#E3E2DC")
GHOST_EDGE = colors.HexColor("#C9C7BE")
OUTLINE = colors.HexColor("#142537")
FRONT_MARK = colors.HexColor("#A32D2D")
TEXT = colors.HexColor("#444441")
FALLBACK = tuple(colors.HexColor(c) for c in PRODUCT_COLORS[-1])


def front_mark(c, center_x: float, baseline: float, text: str, size: float) -> None:
    """Red up-triangle + label, centred on center_x (drawn: the standard PDF fonts have no ▲)."""
    text_w = c.stringWidth(text, "Helvetica-Bold", size)
    side = size * 0.8
    start = center_x - (text_w + side + 3) / 2
    c.setFillColor(FRONT_MARK)
    path = c.beginPath()
    path.moveTo(start, baseline)
    path.lineTo(start + side, baseline)
    path.lineTo(start + side / 2, baseline + side * 0.9)
    path.close()
    c.drawPath(path, stroke=0, fill=1)
    c.setFont("Helvetica-Bold", size)
    c.drawString(start + side + 3, baseline, text)


@dataclass(frozen=True)
class Unit:
    sku: str
    x0: float
    y0: float
    z0: float
    x1: float
    y1: float
    z1: float

    @classmethod
    def from_placement(cls, placement: dict[str, Any]) -> "Unit":
        (x, y, z), (l, w, h) = placement["position"], placement["dimensions"]
        return cls(str(placement["sku"]), x, y, z, x + l, y + w, z + h)

    @property
    def size(self) -> tuple[float, float, float]:
        return self.x1 - self.x0, self.y1 - self.y0, self.z1 - self.z0


def product_palette(shipment: dict[str, Any]) -> dict[str, tuple[colors.Color, ...]]:
    """One color per product, by order of first appearance, so it matches across every box."""
    order: list[str] = []
    for box in shipment["boxes"]:
        for sku in [line.get("sku") for line in box["lines"]] + [str(p["sku"]) for p in box.get("placements", [])]:
            if sku is not None and str(sku) not in order:
                order.append(str(sku))
    return {sku: tuple(colors.HexColor(c) for c in PRODUCT_COLORS[i % len(PRODUCT_COLORS)])
            for i, sku in enumerate(order)}


# --- Geometry -------------------------------------------------------------------------------
def project(x: float, y: float, z: float) -> tuple[float, float]:
    """Isometric projection to drawing units (y up), viewer above the front-right corner."""
    return (x - y) * COS30, z - (x + y) * SIN30


def _screen_box(unit: Unit) -> tuple[float, float, float, float]:
    points = [project(x, y, z) for x in (unit.x0, unit.x1) for y in (unit.y0, unit.y1) for z in (unit.z0, unit.z1)]
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    return min(xs), min(ys), max(xs), max(ys)


def _behind(a: Unit, b: Unit) -> bool:
    """True when a must be painted before b (a is farther from the viewer along a separating axis)."""
    return a.x1 <= b.x0 + EPS or a.y1 <= b.y0 + EPS or a.z1 <= b.z0 + EPS


def draw_order(units: list[Unit]) -> list[Unit]:
    """Painter's order for non-overlapping boxes: topological sort on screen-overlapping pairs.

    Falls back to x + y + z when the constraints contain a cycle, which only happens for
    pathological layouts.
    """
    n = len(units)
    boxes = [_screen_box(u) for u in units]
    after: list[list[int]] = [[] for _ in range(n)]
    pending = [0] * n
    for i in range(n):
        ax0, ay0, ax1, ay1 = boxes[i]
        for j in range(i + 1, n):
            bx0, by0, bx1, by1 = boxes[j]
            if ax1 <= bx0 + EPS or bx1 <= ax0 + EPS or ay1 <= by0 + EPS or by1 <= ay0 + EPS:
                continue  # projections do not overlap: order is irrelevant
            if _behind(units[i], units[j]):
                after[i].append(j)
                pending[j] += 1
            elif _behind(units[j], units[i]):
                after[j].append(i)
                pending[i] += 1
    depth = lambda k: (units[k].x0 + units[k].y0 + units[k].z0, units[k].z0)  # noqa: E731
    ready = sorted((k for k in range(n) if pending[k] == 0), key=depth)
    result: list[int] = []
    while ready:
        k = ready.pop(0)
        result.append(k)
        for m in after[k]:
            pending[m] -= 1
            if pending[m] == 0:
                ready.append(m)
        ready.sort(key=depth)
    if len(result) != n:
        return sorted(units, key=lambda u: (u.x0 + u.y0 + u.z0, u.z0))
    return [units[k] for k in result]


def orientation(unit: Unit, product_dims: list[float] | None) -> str:
    """'standing', 'lying' or 'on its side' from the placed height vs. the product's own sizes."""
    if not product_dims or len(product_dims) != 3:
        return ""
    low, high = min(product_dims), max(product_dims)
    if high - low < 0.05:
        return ""  # a cube looks the same any way up
    height = unit.size[2]
    if abs(height - high) < 0.05:
        return "standing"
    if abs(height - low) < 0.05:
        return "lying"
    return "on its side"


def layers(units: list[Unit]) -> list[dict[str, Any]]:
    """Group units by the height they start at (bottom first). Each layer is one packing step."""
    groups: dict[float, list[Unit]] = {}
    for unit in units:
        groups.setdefault(round(unit.z0, 1), []).append(unit)
    result = []
    for z0 in sorted(groups):
        members = sorted(groups[z0], key=lambda u: (u.y0, u.x0))
        result.append({"z0": z0, "z1": round(max(u.z1 for u in members), 1), "units": members,
                       "counts": Counter(u.sku for u in members)})
    return result


def layer_span(plan_layers: list[dict[str, Any]], index: int) -> str:
    """'0–12 cm' for a clean layer; 'from 12 cm' when it starts inside the one below (irregular boxes)."""
    layer = plan_layers[index]
    below_top = max((u.z1 for previous in plan_layers[:index] for u in previous["units"]), default=0)
    if layer["z0"] < below_top - EPS:
        return f"from {layer['z0']:g} cm"
    return f"{layer['z0']:g}–{layer['z1']:g} cm"


def grid_shape(members: list[Unit]) -> tuple[int, int] | None:
    """(columns, rows) when the layer is a full regular grid of equal footprints, else None."""
    sizes = {(round(u.size[0], 2), round(u.size[1], 2)) for u in members}
    if len(sizes) != 1:
        return None
    columns = len({round(u.x0, 2) for u in members})
    rows = len({round(u.y0, 2) for u in members})
    return (columns, rows) if columns * rows == len(members) else None


def layer_step(index: int, layer: dict[str, Any], products: dict[str, dict[str, Any]]) -> tuple[str, str]:
    """Short title and detail for a layer, e.g. ('Layer 1: 48 standing, 8 × 6', '25 MPP2018 · 23 MO2016')."""
    members = layer["units"]
    kinds = {orientation(u, products.get(u.sku, {}).get("dims")) for u in members}
    how = kinds.pop() if len(kinds) == 1 else "mixed"
    title = f"Layer {index}: {len(members)}" + (f" {how}" if how else "")
    shape = grid_shape(members) if len(members) >= 4 else None
    if shape:
        title += f", {shape[0]} × {shape[1]}"
    detail = " · ".join(f"{count} {products.get(sku, {}).get('code') or products.get(sku, {}).get('description', sku)}"
                        for sku, count in layer["counts"].most_common())
    return title, detail


# --- Flowables ------------------------------------------------------------------------------
class IsoBoxFlowable(Flowable):
    """Isometric 3D drawing of the packed box, scaled to fit width x height."""

    def __init__(self, box: dict[str, Any], palette: dict[str, tuple], width: float, height: float):
        super().__init__()
        self.box, self.palette, self.width, self.height = box, palette, width, height
        self.units = [Unit.from_placement(p) for p in box.get("placements", [])]

    def wrap(self, *_):
        return self.width, self.height

    def draw(self):
        c = self.canv
        L, W, H = self.box["dimensions_cm"]
        corners = [project(x, y, z) for x in (0, L) for y in (0, W) for z in (0, H)]
        min_x, max_x = min(p[0] for p in corners), max(p[0] for p in corners)
        min_y, max_y = min(p[1] for p in corners), max(p[1] for p in corners)
        margin_x, margin_top, margin_bottom = 34, 8, 30  # room for dimension and FRONT labels
        scale = min((self.width - 2 * margin_x) / (max_x - min_x),
                    (self.height - margin_top - margin_bottom) / (max_y - min_y))
        off_x = (self.width - (max_x - min_x) * scale) / 2 - min_x * scale
        off_y = margin_bottom - min_y * scale + ((self.height - margin_top - margin_bottom) - (max_y - min_y) * scale) / 2

        def P(x, y, z):
            px, py = project(x, y, z)
            return off_x + px * scale, off_y + py * scale

        def polygon(points, fill, stroke, width=0.4):
            path = c.beginPath()
            path.moveTo(*P(*points[0]))
            for point in points[1:]:
                path.lineTo(*P(*point))
            path.close()
            c.setFillColor(fill)
            c.setStrokeColor(stroke)
            c.setLineWidth(width)
            c.drawPath(path, stroke=1, fill=1)

        def line(a, b):
            c.line(*P(*a), *P(*b))

        # Back edges (dashed) and floor.
        c.setStrokeColor(colors.HexColor("#888780"))
        c.setLineWidth(0.6)
        c.setDash(3, 2)
        for a, b in [((0, 0, 0), (L, 0, 0)), ((0, 0, 0), (0, W, 0)), ((0, 0, 0), (0, 0, H))]:
            line(a, b)
        c.setDash()
        polygon([(0, 0, 0), (L, 0, 0), (L, W, 0), (0, W, 0)], FLOOR, colors.HexColor("#B4B2A9"))

        for u in draw_order(self.units):
            top, right, front, edge = self.palette.get(u.sku, FALLBACK)
            polygon([(u.x1, u.y0, u.z0), (u.x1, u.y1, u.z0), (u.x1, u.y1, u.z1), (u.x1, u.y0, u.z1)], right, edge)
            polygon([(u.x0, u.y1, u.z0), (u.x1, u.y1, u.z0), (u.x1, u.y1, u.z1), (u.x0, u.y1, u.z1)], front, edge)
            polygon([(u.x0, u.y0, u.z1), (u.x1, u.y0, u.z1), (u.x1, u.y1, u.z1), (u.x0, u.y1, u.z1)], top, edge)

        # Front edges of the box on top of the contents.
        c.setStrokeColor(OUTLINE)
        c.setLineWidth(1.2)
        for a, b in [((L, 0, 0), (L, W, 0)), ((0, W, 0), (L, W, 0)), ((L, 0, 0), (L, 0, H)), ((0, W, 0), (0, W, H)),
                     ((L, W, 0), (L, W, H)), ((0, 0, H), (L, 0, H)), ((0, 0, H), (0, W, H)),
                     ((L, 0, H), (L, W, H)), ((0, W, H), (L, W, H))]:
            line(a, b)

        # Dimensions and the FRONT mark.
        c.setFillColor(TEXT)
        c.setFont("Helvetica", 8)
        x, y = P(L / 2, W, 0)
        c.drawRightString(x - 4, y - 12, f"{L:g} cm")
        x, y = P(L, W / 2, 0)
        c.drawString(x + 4, y - 10, f"{W:g} cm")
        x, y = P(0, W, H / 2)  # left vertical edge: nothing is drawn beyond it
        c.drawRightString(x - 5, y, f"{H:g} cm")
        x, y = P(L / 2, W, 0)
        front_mark(c, x - 14, y - 25, "FRONT · label side", 8)


class LayerPlanFlowable(Flowable):
    """Top view of one layer: earlier layers in gray, this layer in product colors, FRONT at the bottom."""

    def __init__(self, box: dict[str, Any], plan_layers: list[dict[str, Any]], index: int,
                 palette: dict[str, tuple], width: float, height: float):
        super().__init__()
        self.box, self.layers, self.index = box, plan_layers, index
        self.palette, self.width, self.height = palette, width, height

    def wrap(self, *_):
        return self.width, self.height

    def draw(self):
        c = self.canv
        L, W, _ = self.box["dimensions_cm"]
        label_h = 11
        k = min((self.width - 4) / L, (self.height - label_h - 4) / W)
        ox = (self.width - L * k) / 2
        oy = label_h + (self.height - label_h - W * k) / 2

        def rect(u: Unit, fill, stroke, width=0.4):
            # y = W (front) at the bottom of the drawing.
            c.setFillColor(fill)
            c.setStrokeColor(stroke)
            c.setLineWidth(width)
            c.rect(ox + u.x0 * k, oy + (W - u.y1) * k, (u.x1 - u.x0) * k, (u.y1 - u.y0) * k, stroke=1, fill=1)

        c.setFillColor(FLOOR)
        c.setStrokeColor(OUTLINE)
        c.setLineWidth(1.2)
        c.rect(ox, oy, L * k, W * k, stroke=1, fill=1)
        for earlier in self.layers[: self.index]:
            for u in earlier["units"]:
                rect(u, GHOST, GHOST_EDGE)
        members = self.layers[self.index]["units"]
        for number, u in enumerate(members, start=1):
            top, _, _, edge = self.palette.get(u.sku, FALLBACK)
            rect(u, top, edge, 0.5)
            w, h = (u.x1 - u.x0) * k, (u.y1 - u.y0) * k
            if min(w, h) >= 9:
                c.setFillColor(edge)
                c.setFont("Helvetica", 6 if min(w, h) < 14 else 7)
                c.drawCentredString(ox + (u.x0 + u.x1) / 2 * k, oy + (W - (u.y0 + u.y1) / 2) * k - 2.2, str(number))
        front_mark(c, self.width / 2, 1, "FRONT", 7)
