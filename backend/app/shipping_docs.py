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
from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

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
            "lines": lines,
        })

    return {
        "order_number": order["order_number"],
        "order_date": format_date(order.get("order_date")),
        "client": order.get("client") or "",
        "exporter": {**DEFAULT_EXPORTER, **(exporter or {})},
        "consignee": consignee or {"name": order.get("client") or "", "address": order.get("delivery_address") or ""},
        "destination": destination,
        "dispatch": dispatch,
        "boxes": boxes,
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

    columns = [("Box No.", .07), ("Product Code", .13), ("Description of Goods", .27), ("Unit Qty", .08),
               ("Type of Pkg", .10), ("Net Weight (Kg)", .1), ("Gross Weight (Kg)", .1),
               ("Measurements (m³)", .15)]
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
                p(box["box_code"] if first else "", "num"),
                p(f"{line['net_weight_kg']:.2f}", "num"),
                p(f"{box['gross_weight_kg']:.2f}" if first else "", "num"),
                p(f"{box['volume_m3']:.4f}\n{dims} cm" if first else "", "num"),
            ])
        if index % 2:
            style_cmds.append(("BACKGROUND", (0, start), (-1, len(rows) - 1), BOX_SHADE))
        style_cmds.append(("LINEABOVE", (0, start), (-1, start), 0.8, GRID))
    total_row = len(rows)
    rows.append([p("Consignment Total", "total_label"), "", "", p(totals["units"], "total"),
                 p(f"{totals['boxes']} box{'es' if totals['boxes'] != 1 else ''}", "total"),
                 p(f"{totals['net_weight_kg']:.2f}", "total"), p(f"{totals['gross_weight_kg']:.2f}", "total"),
                 p(f"{totals['volume_m3']:.4f}", "total")])
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
               f"Total gross weight: {totals['gross_weight_kg']:.2f} kg\n"
               f"Total volume: {totals['volume_m3']:.4f} m³")
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
    document.build(story, canvasmaker=_numbered_canvas(
        f"{exporter['company']} | Packing list {shipment['order_number']}"))
    return stream.getvalue()
