"""Box labels: one 10 x 10 cm page per box with the box number and a readable contents list."""
from __future__ import annotations

from io import BytesIO
from typing import Any
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.pdfgen import canvas as pdf_canvas
from reportlab.platypus import Paragraph, Table, TableStyle

from .shipping_docs import GRID, MUTED, NAVY

LABEL_SIZE = (10 * cm, 10 * cm)
RULE = colors.HexColor("#9AA6B2")


def _clip(text: Any, limit: int) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _contents_table(box: dict[str, Any], shown: int, width: float, style: ParagraphStyle) -> Table:
    lines = box["lines"]
    rows = [[Paragraph("<b>Qty</b>", style), Paragraph("<b>Code</b>", style), Paragraph("<b>Description</b>", style)]]
    rows += [[Paragraph(str(l["quantity"]), style), Paragraph(escape(_clip(l["code"], 14)), style),
              Paragraph(escape(_clip(l["description"], 60)), style)] for l in lines[:shown]]
    if shown < len(lines):
        rows.append(["", "", Paragraph(f"<i>+{len(lines) - shown} more line(s) - see packing list</i>", style)])
    table = Table(rows, colWidths=[width * .12, width * .24, width * .64])
    table.setStyle(TableStyle([
        ("LINEBELOW", (0, 0), (-1, -1), 0.3, RULE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 1.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 1.5),
        ("LEFTPADDING", (0, 0), (-1, -1), 2), ("RIGHTPADDING", (0, 0), (-1, -1), 2),
    ]))
    return table


def render_box_labels_pdf(shipment: dict[str, Any], box_number: int | None = None) -> bytes:
    """One label per box, or only ``box_number``. Raises ValueError for a box not in the plan."""
    boxes = shipment["boxes"]
    if box_number is not None:
        boxes = [b for b in boxes if b["sequence"] == box_number]
        if not boxes:
            raise ValueError(f"Box {box_number} is not in this packing plan.")
    width, height = LABEL_SIZE
    margin = 0.35 * cm
    inner = width - 2 * margin
    total = shipment["totals"]["boxes"]
    consignee = shipment["consignee"]
    exporter = shipment["exporter"]
    cell = ParagraphStyle("LabelCell", fontName="Helvetica", fontSize=7, leading=8.4)

    stream = BytesIO()
    canvas = pdf_canvas.Canvas(stream, pagesize=LABEL_SIZE)
    canvas.setTitle(f"Box labels - {shipment['order_number']}")
    canvas.setAuthor(exporter["company"])

    for box in boxes:
        top = height - margin
        # Header band: sender and PO.
        canvas.setFillColor(NAVY)
        canvas.rect(margin, top - 0.85 * cm, inner, 0.85 * cm, stroke=0, fill=1)
        canvas.setFillColor(colors.white)
        canvas.setFont("Helvetica-Bold", 8.5)
        canvas.drawString(margin + 5, top - 0.36 * cm, _clip(exporter["company"], 30))
        canvas.setFont("Helvetica", 7.5)
        po = f"PO {shipment['order_number']}"
        canvas.drawString(margin + 5, top - 0.7 * cm,
                          f"{po}   {shipment['order_date']}" if shipment["order_date"] else po)

        # Ship to: name + up to two address lines.
        y = top - 1.2 * cm
        canvas.setFillColor(MUTED)
        canvas.setFont("Helvetica-Bold", 6.5)
        canvas.drawString(margin, y, "SHIP TO")
        canvas.setFillColor(colors.black)
        canvas.setFont("Helvetica-Bold", 8.5)
        y -= 0.36 * cm
        canvas.drawString(margin, y, _clip(consignee.get("name") or shipment["client"], 46))
        canvas.setFont("Helvetica", 7)
        address = [l for l in (consignee.get("address") or "").splitlines() if l.strip()]
        if len(address) > 2:
            address = [", ".join(address[:-1]), address[-1]]
        for line in address:
            y -= 0.3 * cm
            canvas.drawString(margin, y, _clip(line, 64))
        y -= 0.25 * cm
        canvas.setStrokeColor(GRID)
        canvas.setLineWidth(0.7)
        canvas.line(margin, y, width - margin, y)

        # Box number: readable from a distance.
        y -= 0.95 * cm
        canvas.setFont("Helvetica-Bold", 24)
        canvas.drawString(margin, y, f"BOX {box['sequence']} / {total}")
        y -= 0.42 * cm
        canvas.setFont("Helvetica", 7.5)
        dims = " x ".join(f"{v:g}" for v in box["dimensions_cm"])
        canvas.drawString(margin, y, f"{box['box_code']}  |  {dims} cm")
        canvas.setFont("Helvetica-Bold", 8.5)
        canvas.drawRightString(width - margin, y, f"Gross {box['gross_weight_kg']:.2f} kg")
        y -= 0.25 * cm
        canvas.line(margin, y, width - margin, y)

        # Contents: as many lines as fit; the rest is pointed to the packing list.
        y -= 0.38 * cm
        canvas.setFillColor(MUTED)
        canvas.setFont("Helvetica-Bold", 6.5)
        canvas.drawString(margin, y, "CONTENTS")
        units = sum(l["quantity"] for l in box["lines"])
        canvas.drawRightString(width - margin, y, f"{units} units  |  {len(box['lines'])} line(s)")
        canvas.setFillColor(colors.black)
        available = y - 0.12 * cm - margin
        shown = len(box["lines"])
        table = _contents_table(box, shown, inner, cell)
        _, table_height = table.wrap(inner, available)
        while table_height > available and shown > 1:
            shown -= 1
            table = _contents_table(box, shown, inner, cell)
            _, table_height = table.wrap(inner, available)
        table.drawOn(canvas, margin, y - 0.12 * cm - table_height)
        canvas.showPage()
    canvas.save()
    return stream.getvalue()
