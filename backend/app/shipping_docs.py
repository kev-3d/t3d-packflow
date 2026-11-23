"""Shipping documents built from a complete packing plan: packing list (and box labels)."""
from __future__ import annotations

import re
from datetime import date
from io import BytesIO
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.pdfgen import canvas as pdf_canvas
from reportlab.platypus import (Flowable, Image, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table,
                                TableStyle)

from .box_views import FALLBACK, IsoBoxFlowable, LayerPlanFlowable, Unit, layer_span, layer_step, layers, product_palette
from .country_style import country_code, country_color, flag_path

LOGO_PATH = Path(__file__).parent / "assets" / "logo.jpg"

DEFAULT_EXPORTER: dict[str, str] = {
    "company": "Thought3D Ltd.",
    "address": "30V, Binja San Guzepp Haddiem,\nTriq Indri Psaila,\nQormi, QRM 4000\nMalta",
    "phone": "",
    "contact": "",
    "country_of_origin": "Malta",
    "packing_info": "3D Printing bed adhesives, non-flammable, non-hazardous",
    "signatory_name": "",
    "signatory_title": "",
}

NAVY = colors.HexColor("#142537")
MUTED = colors.HexColor("#526174")
GRID = colors.HexColor("#1F2A36")
BOX_SHADE = colors.HexColor("#F0F4F7")


def _round(value: float, digits: int = 2) -> float:
    return round(value + 0.0, digits)


def format_date(value: str | None) -> str:
    """ISO date (YYYY-MM-DD...) -> DD/MM/YYYY; anything else is returned unchanged."""
    if not value:
        return ""
    try:
        return date.fromisoformat(value[:10]).strftime("%d/%m/%Y")
    except ValueError:
        return value


INTERNAL_CODE = re.compile(r"^PRODUCT-[0-9A-F]{10}$")


def product_code(line: dict[str, Any]) -> str:
    """Code printed for a goods line: the PO's own code first, then a real catalog SKU.

    Catalog products created from a PO get an internal hash (``PRODUCT-<10 hex>``) that means
    nothing to the customer or customs, so it is never printed.
    """
    raw = (line.get("sku_raw") or "").strip()
    if raw:
        return raw
    sku = (line.get("sku") or "").strip()
    return "" if INTERNAL_CODE.match(sku) else sku


def build_shipment(order: dict[str, Any], *, exporter: dict[str, str] | None = None,
                   consignee: dict[str, str] | None = None, destination: str = "",
                   dispatch: str = "") -> dict[str, Any]:
    """Normalize an order with a complete plan into the data both shipping documents print.

    The plan stores ``str(products.id)`` as each item's sku, so real product codes,
    descriptions and unit weights are resolved through the order lines.
    """
    result = order["plan"]["result"]
    details: dict[str, dict[str, Any]] = {}
    for item in order["items"]:
        if item.get("product_id") is not None:
            details.setdefault(str(item["product_id"]), item)

    boxes = []
    for box in result["boxes"]:
        length, width, height = box["dimensions_cm"]
        lines = []
        for item in box["items"]:
            detail = details.get(str(item["sku"]), {})
            unit_weight = detail.get("weight_kg") or 0
            lines.append({
                "sku": str(item["sku"]),
                "code": product_code(detail),
                "description": detail.get("description") or item["name"],
                "quantity": item["quantity"],
                "net_weight_kg": _round(unit_weight * item["quantity"], 3),
            })
        boxes.append({
            "sequence": box["sequence"],
            "box_code": box["box_code"],
            "box_name": box.get("box_name") or box["box_code"],
            "dimensions_cm": [length, width, height],
            "volume_m3": _round(length * width * height / 1_000_000, 4),
            "net_weight_kg": _round(box.get("product_weight_kg") or sum(l["net_weight_kg"] for l in lines), 2),
            "gross_weight_kg": _round(box["gross_weight_kg"], 2),
            "utilization": box.get("utilization"),
            "lines": lines,
            # Unit positions for the 3D packing guide (absent in old or synthetic plans).
            "placements": box.get("placements") or [],
        })

    products = {}
    for sku, detail in details.items():
        dims = [detail.get("length_cm"), detail.get("width_cm"), detail.get("height_cm")]
        products[sku] = {"code": product_code(detail), "description": detail.get("description") or "",
                         "dims": [float(v) for v in dims] if all(v for v in dims) else None}

    return {
        "order_number": order["order_number"],
        "order_date": format_date(order.get("order_date")),
        "client": order.get("client") or "",
        "exporter": {**DEFAULT_EXPORTER, **(exporter or {})},
        "consignee": consignee or {"name": order.get("client") or "", "address": order.get("delivery_address") or ""},
        "destination": destination,
        "dispatch": dispatch,
        "boxes": boxes,
        "products": products,
        "totals": {
            "boxes": len(boxes),
            "units": sum(l["quantity"] for b in boxes for l in b["lines"]),
            "net_weight_kg": _round(sum(b["net_weight_kg"] for b in boxes), 2),
            "gross_weight_kg": _round(sum(b["gross_weight_kg"] for b in boxes), 2),
            "volume_m3": _round(sum(b["volume_m3"] for b in boxes), 4),
        },
    }


def _numbered_canvas(label: str):
    """Canvas that prints 'Page x of y' once the total page count is known."""

    class NumberedCanvas(pdf_canvas.Canvas):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._pages: list[dict[str, Any]] = []

        def showPage(self):
            self._pages.append(dict(self.__dict__))
            self._startPage()

        def save(self):
            total = len(self._pages)
            for state in self._pages:
                self.__dict__.update(state)
                self.saveState()
                self.setFont("Helvetica", 8)
                self.setFillColor(MUTED)
                self.drawString(1.4 * cm, 0.9 * cm, label)
                self.drawRightString(A4[0] - 1.4 * cm, 0.9 * cm, f"Page {self._pageNumber} of {total}")
                self.restoreState()
                super().showPage()
            super().save()

    return NumberedCanvas


GUIDE_LAYERS_PER_PAGE = 4


class _CheckBox(Flowable):
    """Empty square the packer ticks by hand."""

    def __init__(self, size: float = 8):
        super().__init__()
        self.size = size

    def wrap(self, *_):
        return self.size, self.size

    def draw(self):
        self.canv.setStrokeColor(GRID)
        self.canv.setLineWidth(0.8)
        self.canv.rect(0, 0, self.size, self.size, stroke=1, fill=0)


def _guide_pages(shipment: dict[str, Any], box: dict[str, Any], palette: dict[str, tuple],
                 width: float, p) -> list[Any]:
    """PACKING GUIDE page(s) for one box: 3D view, contents, steps and layer-by-layer plans."""
    total = shipment["totals"]["boxes"]
    products = shipment.get("products", {})
    destination = country_code(shipment.get("destination", ""))
    accent = country_color(destination)
    small = ParagraphStyle("GuideSmall", fontName="Helvetica", fontSize=8, leading=10)
    bold = ParagraphStyle("GuideBold", parent=small, fontName="Helvetica-Bold")
    label = ParagraphStyle("GuideLabel", parent=small, fontName="Helvetica-Bold", fontSize=7, textColor=MUTED)
    right = ParagraphStyle("GuideRight", parent=small, alignment=2)

    def para(text: Any, style=small) -> Paragraph:
        return Paragraph(escape(str(text)).replace("\n", "<br/>"), style)

    units = [Unit.from_placement(pl) for pl in box.get("placements", [])]
    plan_layers = layers(units)
    chunks = [list(range(i, min(i + GUIDE_LAYERS_PER_PAGE, len(plan_layers))))
              for i in range(0, len(plan_layers), GUIDE_LAYERS_PER_PAGE)] or [[]]

    def header(continued: bool) -> Table:
        badge = Table([[para(f"BOX {box['sequence']} / {total}",
                             ParagraphStyle("Badge", fontName="Helvetica-Bold", fontSize=14, leading=16,
                                            textColor=colors.white, alignment=1))]], colWidths=[3 * cm])
        badge.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), accent),
                                   ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 6)]))
        flag = flag_path(destination)
        right_cell = [badge] if not flag else Table([[Image(str(flag), width=0.9 * cm, height=0.6 * cm), badge]],
                                                     colWidths=[1.1 * cm, 3 * cm])
        if flag:
            right_cell.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                                            ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0)]))
        title = "PACKING GUIDE" + (" (continued)" if continued else "")
        left_cell = [para(title, ParagraphStyle("GuideTitle", fontName="Helvetica-Bold", fontSize=15, leading=18,
                                                textColor=NAVY)),
                     para(f"Packing list {shipment['order_number']} · for the packing team · "
                          f"{shipment['consignee'].get('name') or shipment['client']}", label)]
        table = Table([[left_cell, right_cell]], colWidths=[width - 4.3 * cm, 4.3 * cm])
        table.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("ALIGN", (1, 0), (1, 0), "RIGHT"),
                                   ("LINEBELOW", (0, 0), (-1, 0), 1.5, NAVY),
                                   ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                                   ("BOTTOMPADDING", (0, 0), (-1, -1), 7)]))
        return table

    def panel(title: str, rows: list[list[Any]], widths: list[float], extra=()) -> Table:
        table = Table([[para(title, label)] + [""] * (len(widths) - 1), *rows], colWidths=widths)
        table.setStyle(TableStyle([("SPAN", (0, 0), (-1, 0)), ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#B4B2A9")),
                                   ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                                   ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
                                   ("LEFTPADDING", (0, 0), (-1, -1), 5), ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                                   *extra]))
        return table

    side_w = width * 0.4
    dims = " × ".join(f"{v:g}" for v in box["dimensions_cm"])
    info_rows = [[para("Type"), para(box["box_code"], ParagraphStyle("B1", parent=bold, alignment=2))],
                 [para("Inside size"), para(f"{dims} cm", ParagraphStyle("B2", parent=bold, alignment=2))],
                 [para("Gross weight"), para(f"{box['gross_weight_kg']:.2f} kg", ParagraphStyle("B3", parent=bold, alignment=2))]]
    if box.get("utilization") is not None:
        info_rows.append([para("Volume used"), para(f"{box['utilization']}%", ParagraphStyle("B4", parent=bold, alignment=2))])
    info = panel("BOX", info_rows, [side_w * .45, side_w * .55])

    legend_rows, legend_style = [], []
    for row, line in enumerate(box["lines"], start=1):
        swatch = palette.get(line.get("sku"), FALLBACK)
        legend_rows.append(["", para(" ".join(v for v in [line["code"], line["description"]] if v)),
                            para(str(line["quantity"]), ParagraphStyle("Q", parent=bold, alignment=2))])
        legend_style += [("BACKGROUND", (0, row), (0, row), swatch[0]), ("BOX", (0, row), (0, row), 0.6, swatch[3])]
    units_total = sum(line["quantity"] for line in box["lines"])
    legend = panel(f"CONTENTS · {units_total} units", legend_rows, [0.35 * cm, side_w - 1.55 * cm, 1.2 * cm],
                   [("LEFTPADDING", (0, 1), (0, -1), 0), ("RIGHTPADDING", (0, 1), (0, -1), 0)] + legend_style)

    steps = [layer_step(i + 1, layer, products)[0] for i, layer in enumerate(plan_layers)]
    steps.append(f"Fill gaps, close the box and stick label {box['sequence']}/{total} on the front")
    step_rows = [[_CheckBox(), para(f"{i}. {text}")] for i, text in enumerate(steps, start=1)]
    steps_panel = panel("STEPS", step_rows, [0.45 * cm, side_w - 0.45 * cm])

    if units:
        view: Any = IsoBoxFlowable(box, palette, width - side_w - 0.4 * cm, 7.6 * cm)
    else:
        view = para("3D layout not available for this box: the packing plan was saved without unit positions. "
                    "Recalculate the order to get it.", small)
    main = Table([[[para("3D VIEW · the finished box", label), view], [info, Spacer(1, 6), legend, Spacer(1, 6), steps_panel]]],
                 colWidths=[width - side_w, side_w])
    main.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"),
                              ("BOX", (0, 0), (0, 0), 0.5, colors.HexColor("#B4B2A9")),
                              ("LEFTPADDING", (1, 0), (1, 0), 0.4 * cm), ("RIGHTPADDING", (1, 0), (1, 0), 0),
                              ("TOPPADDING", (0, 0), (-1, -1), 4)]))

    def layer_grid(indexes: list[int]) -> Table:
        cell_w = (width - 0.4 * cm) / 2
        # Bigger plans when the page only holds one row of them.
        plan_h = 6.8 * cm if len(indexes) <= 2 else 4.3 * cm
        cells = []
        for i in indexes:
            layer = plan_layers[i]
            title, detail = layer_step(i + 1, layer, products)
            head = Table([[_CheckBox(), para(f"Layer {i + 1} · {layer_span(plan_layers, i)}", bold),
                           para(title.split(": ", 1)[1], right)]],
                         colWidths=[0.45 * cm, cell_w * .45, cell_w * .55 - 0.45 * cm - 12])
            head.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                                      ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0)]))
            cells.append([head, LayerPlanFlowable(box, plan_layers, i, palette, cell_w - 12, plan_h),
                          para(detail, label)])
        rows = [cells[k:k + 2] + [""] * (2 - len(cells[k:k + 2])) for k in range(0, len(cells), 2)]
        grid = Table(rows, colWidths=[cell_w + 0.2 * cm, cell_w + 0.2 * cm])
        commands = [("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 6), ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 6)]
        for r, row in enumerate(rows):
            for col, cell in enumerate(row):
                if cell:
                    commands.append(("BOX", (col, r), (col, r), 0.5, colors.HexColor("#B4B2A9")))
        grid.setStyle(TableStyle(commands))
        return grid

    sign = Table([[para("Packed by", label), para("Checked by", label), para("Date", label)]],
                 colWidths=[width / 3] * 3)
    sign.setStyle(TableStyle([("LINEABOVE", (0, 0), (0, 0), 0.8, GRID), ("LINEABOVE", (1, 0), (1, 0), 0.8, GRID),
                              ("LINEABOVE", (2, 0), (2, 0), 0.8, GRID),
                              ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 12)]))

    story: list[Any] = []
    for page, indexes in enumerate(chunks):
        story += [PageBreak(), header(page > 0), Spacer(1, 8)]
        if page == 0:
            story += [main, Spacer(1, 10)]
        if indexes:
            story += [para("LAYER BY LAYER · seen from above, front edge at the bottom, bottom layer first", label),
                      Spacer(1, 4), layer_grid(indexes)]
        if page == len(chunks) - 1:
            story += [Spacer(1, 18), sign]
    return story


def render_packing_list_pdf(shipment: dict[str, Any]) -> bytes:
    stream = BytesIO()
    document = SimpleDocTemplate(stream, pagesize=A4, leftMargin=1.4 * cm, rightMargin=1.4 * cm,
                                 topMargin=1.3 * cm, bottomMargin=1.6 * cm,
                                 title=f"Packing List - {shipment['order_number']}",
                                 author=shipment["exporter"]["company"])
    base = ParagraphStyle("Base", fontName="Helvetica", fontSize=8.5, leading=11)
    styles = {
        "label": ParagraphStyle("Label", parent=base, fontName="Helvetica-Bold", fontSize=7.5, textColor=MUTED),
        "value": base,
        "strong": ParagraphStyle("Strong", parent=base, fontName="Helvetica-Bold", fontSize=9.5, leading=12),
        "head": ParagraphStyle("Head", parent=base, fontName="Helvetica-Bold", fontSize=7.5, leading=9,
                               alignment=1, textColor=colors.white),
        "cell": ParagraphStyle("Cell", parent=base, fontSize=8, leading=10),
        "num": ParagraphStyle("Num", parent=base, fontSize=8, leading=10, alignment=1),
        "total": ParagraphStyle("Total", parent=base, fontName="Helvetica-Bold", fontSize=8.5, alignment=1),
        "total_label": ParagraphStyle("TotalLabel", parent=base, fontName="Helvetica-Bold", fontSize=8.5, alignment=2),
        "title": ParagraphStyle("Title", parent=base, fontName="Helvetica-Bold", fontSize=18, leading=22, alignment=1),
    }

    def p(value: Any, style: str = "value") -> Paragraph:
        text = "" if value is None else str(value)
        return Paragraph(escape(text).replace("\n", "<br/>"), styles[style])

    def field(label: str, value: Any, style: str = "value") -> list[Paragraph]:
        return [p(label, "label"), p(value or "—", style)]

    exporter = shipment["exporter"]
    consignee = shipment["consignee"]
    totals = shipment["totals"]
    width = document.width
    half = width / 2

    # Company logo to the left of the exporter name; the name alone if the file is missing.
    exporter_name: Any = p(exporter["company"], "strong")
    if LOGO_PATH.exists():
        logo_size = 1.1 * cm
        exporter_name = Table([[Image(str(LOGO_PATH), width=logo_size, height=logo_size), exporter_name]],
                              colWidths=[logo_size + 0.2 * cm, None], hAlign="LEFT")
        exporter_name.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ]))

    exporter_lines = [exporter["address"]]
    if exporter.get("phone"):
        exporter_lines.append(f"Phone: {exporter['phone']}")
    if exporter.get("contact"):
        exporter_lines.append(f"Contact: {exporter['contact']}")
    consignee_lines = [consignee.get("address") or ""]
    if consignee.get("contact"):
        consignee_lines.append(f"Attn: {consignee['contact']}")
    if consignee.get("phone"):
        consignee_lines.append(f"Phone: {consignee['phone']}")
    if consignee.get("email"):
        consignee_lines.append(consignee["email"])

    header = Table([
        [[p("EXPORTER", "label"), exporter_name, p("\n".join(l for l in exporter_lines if l))],
         field("Export Invoice / PO No. & Date",
               f"{shipment['order_number']}    {shipment['order_date']}".strip(), "strong"), ""],
        ["", field("Reference", shipment["order_number"]), ""],
        [[p("CONSIGNEE", "label"), p(consignee.get("name") or "—", "strong"),
          p("\n".join(l for l in consignee_lines if l))],
         field("Country of Origin", exporter.get("country_of_origin"), "strong"),
         field("Final Destination", shipment["destination"], "strong")],
        ["", field("Method of Dispatch", shipment["dispatch"]), ""],
        ["", field("Packing Information", exporter.get("packing_info"), "strong"), ""],
    ], colWidths=[half, half / 2, half / 2])
    header.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.8, GRID),
        ("SPAN", (0, 0), (0, 1)), ("SPAN", (0, 2), (0, 4)),
        ("SPAN", (1, 0), (2, 0)), ("SPAN", (1, 1), (2, 1)),
        ("SPAN", (1, 3), (2, 3)), ("SPAN", (1, 4), (2, 4)),
        ("LINEAFTER", (0, 0), (0, -1), 0.8, GRID),
        ("LINEBELOW", (0, 1), (0, 1), 0.8, GRID),
        ("LINEBELOW", (1, 0), (2, 3), 0.5, GRID),
        ("LINEAFTER", (1, 2), (1, 2), 0.5, GRID),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING", (0, 0), (-1, -1), 7), ("RIGHTPADDING", (0, 0), (-1, -1), 7),
    ]))

    columns = [("Box No.", .07), ("Product Code", .14), ("Description of Goods", .35), ("Unit Qty", .08),
               ("Type of Pkg", .16), ("Net Weight (Kg)", .1), ("Gross Weight (Kg)", .1)]
    rows: list[list[Any]] = [[p(name, "head") for name, _ in columns]]
    style_cmds: list[tuple] = []
    for index, box in enumerate(shipment["boxes"]):
        start = len(rows)
        dims = " x ".join(f"{v:g}" for v in box["dimensions_cm"])
        for line_index, line in enumerate(box["lines"]):
            first = line_index == 0
            rows.append([
                p(f"{box['sequence']} / {totals['boxes']}" if first else "", "num"),
                p(line["code"], "cell"),
                p(line["description"], "cell"),
                p(line["quantity"], "num"),
                p(f"{box['box_code']}\n{dims} cm" if first else "", "num"),
                p(f"{line['net_weight_kg']:.2f}", "num"),
                p(f"{box['gross_weight_kg']:.2f}" if first else "", "num"),
            ])
        if index % 2:
            style_cmds.append(("BACKGROUND", (0, start), (-1, len(rows) - 1), BOX_SHADE))
        style_cmds.append(("LINEABOVE", (0, start), (-1, start), 0.8, GRID))
    total_row = len(rows)
    rows.append([p("Consignment Total", "total_label"), "", "", p(totals["units"], "total"),
                 p(f"{totals['boxes']} box{'es' if totals['boxes'] != 1 else ''}", "total"),
                 p(f"{totals['net_weight_kg']:.2f}", "total"), p(f"{totals['gross_weight_kg']:.2f}", "total")])
    goods = Table(rows, colWidths=[width * share for _, share in columns], repeatRows=1)
    goods.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), NAVY),
        ("BOX", (0, 0), (-1, -1), 0.8, GRID),
        ("INNERGRID", (0, 0), (-1, 0), 0.5, colors.white),
        ("LINEAFTER", (0, 1), (-2, -1), 0.4, colors.HexColor("#9AA6B2")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("VALIGN", (0, 1), (-1, total_row - 1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("SPAN", (0, total_row), (2, total_row)),
        ("LINEABOVE", (0, total_row), (-1, total_row), 1.2, GRID),
        ("BACKGROUND", (0, total_row), (-1, total_row), colors.HexColor("#DCE8ED")),
        *style_cmds,
    ]))

    signatory = exporter.get("signatory_name") or ""
    title_line = " / ".join(v for v in [exporter.get("signatory_title") or "", ""] if v) or "Title / Date"
    summary = (f"Total no. of boxes: {totals['boxes']}\n"
               f"Total units: {totals['units']}\n"
               f"Total net weight: {totals['net_weight_kg']:.2f} kg\n"
               f"Total gross weight: {totals['gross_weight_kg']:.2f} kg")
    footer = Table([[
        [p("Additional Info", "label"), p(summary)],
        [p("Signatory Company", "label"), p(exporter["company"], "strong"), Spacer(1, 6),
         p("Name of Authorized Signatory", "label"),
         Table([[p(signatory or "Name"), p(title_line)]], colWidths=[half * .55, half * .35]),
         Spacer(1, 18), p("Signature", "label")],
    ]], colWidths=[half, half])
    footer.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.8, GRID), ("LINEAFTER", (0, 0), (0, 0), 0.8, GRID),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ("LEFTPADDING", (0, 0), (-1, -1), 7),
    ]))

    story = [p("PACKING LIST", "title"), Spacer(1, 10), header, Spacer(1, 12), goods,
             Spacer(1, 12), footer]
    # One packing guide page per box (more if a box has over GUIDE_LAYERS_PER_PAGE layers).
    palette = product_palette(shipment)
    for box in shipment["boxes"]:
        story += _guide_pages(shipment, box, palette, width, p)
    document.build(story, canvasmaker=_numbered_canvas(
        f"{exporter['company']} | Packing list {shipment['order_number']}"))
    return stream.getvalue()
