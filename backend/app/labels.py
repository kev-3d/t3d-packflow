"""Box labels: one 10 x 10 cm page per box, Thought3D branded, with a self-contained QR code."""
from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import Any

from reportlab.graphics import renderPDF
from reportlab.graphics.barcode.qr import QrCodeWidget
from reportlab.graphics.shapes import Drawing
from reportlab.lib import colors
from reportlab.lib.units import cm
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas as pdf_canvas

from .country_style import FLAGS_DIR, country_code, country_color, flag_path as _flag_path  # noqa: F401
from .shipping_docs import LOGO_PATH, NAVY

LABEL_SIZE = (10 * cm, 10 * cm)    # square thermal label
QR_MAX_CHARS = 1000                # keeps the code dense enough for any phone camera
TEAL = colors.HexColor("#389D9B")  # Thought3D brand teal
INK = colors.HexColor("#171717")
SOFT = colors.HexColor("#5C6B7A")


def _clip(text: Any, limit: int) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def box_qr_payload(shipment: dict[str, Any], box: dict[str, Any]) -> str:
    """Plain text stored in the QR: a phone camera shows it with no network, app or login."""
    dims = "x".join(f"{v:g}" for v in box["dimensions_cm"])
    head = [f"PO {shipment['order_number']}",
            f"BOX {box['sequence']}/{shipment['totals']['boxes']} - {box['box_code']} {dims} cm",
            f"Gross {box['gross_weight_kg']:.2f} kg",
            f"To: {_clip(shipment['consignee'].get('name') or shipment['client'], 60)}",
            "CONTENTS:"]
    lines = [f"{line['quantity']} x {line['code'] or '-'} {_clip(line['description'], 45)}"
             for line in box["lines"]]
    payload = "\n".join(head + lines)
    if len(payload) <= QR_MAX_CHARS:
        return payload
    short = [f"{line['quantity']} x {line['code'] or _clip(line['description'], 20)}" for line in box["lines"]]
    return "\n".join(head + short)[:QR_MAX_CHARS]


def _draw_qr(c: pdf_canvas.Canvas, text: str, x: float, y: float, size: float) -> None:
    widget = QrCodeWidget(text, barLevel="M", barBorder=0)
    x0, y0, x1, y1 = widget.getBounds()
    drawing = Drawing(size, size, transform=[size / (x1 - x0), 0, 0, size / (y1 - y0), 0, 0])
    drawing.add(widget)
    renderPDF.draw(drawing, c, x, y)


def _pill(c: pdf_canvas.Canvas, text: str, x: float, y: float, fill=INK) -> float:
    """Small rounded tag with white text; (x, y) is its bottom-left corner. Returns its width."""
    c.setFont("Helvetica-Bold", 5.5)
    width = c.stringWidth(text, "Helvetica-Bold", 5.5) + 10
    c.setFillColor(fill)
    c.roundRect(x, y, width, 9, 4.5, stroke=0, fill=1)
    c.setFillColor(colors.white)
    c.drawString(x + 5, y + 2.8, text)
    return width


def wrap_text(text: str, font: str, size: float, width: float) -> list[str]:
    """Word-wrap ``text`` to ``width`` points; words longer than a line are split. Nothing is cut."""
    out: list[str] = []
    for raw in (text or "").splitlines() or [""]:
        line = ""
        for word in raw.split():
            while stringWidth(word, font, size) > width:  # e.g. long e-mail addresses
                cut = len(word)
                while cut > 1 and stringWidth(word[:cut], font, size) > width:
                    cut -= 1
                if line:
                    out.append(line)
                    line = ""
                out.append(word[:cut])
                word = word[cut:]
            candidate = f"{line} {word}".strip()
            if line and stringWidth(candidate, font, size) > width:
                out.append(line)
                line = word
            else:
                line = candidate
        if line:
            out.append(line)
    return out


PARTY_HEAD = 11  # tag row height


def _party_layout(name: str, address: list[str], width: float, size: float) -> tuple[list[str], list[str], float]:
    name_lines = wrap_text(name, "Helvetica-Bold", size + 1, width)
    address_lines = [l for line in address for l in wrap_text(line, "Courier", size, width)]
    return name_lines, address_lines, _party_height(len(name_lines), len(address_lines), size)


def _party_height(names: int, addresses: int, size: float) -> float:
    """Exact drawn height of a FROM / TO block: tag row down to the last line's descender."""
    steps = [size + 2.2] * names + [size + 1.6] * addresses
    return PARTY_HEAD + 2 + size + sum(steps[:-1]) + 0.3 * size


def _party(c: pdf_canvas.Canvas, label: str, name_lines: list[str], address_lines: list[str],
           x: float, top: float, size: float, tag_fill=INK, flag: Path | None = None,
           flag_text: str = "", right_edge: float = 0) -> float:
    """FROM / TO block: tag (+ flag) row, full name, full address. Returns the y just below it."""
    tag = _pill(c, label, x, top - 9, tag_fill)
    if flag:
        flag_h = 11
        c.drawImage(str(flag), x + tag + 4, top - 10, height=flag_h, width=flag_h * 1.5,
                    preserveAspectRatio=True, anchor="w", mask="auto")
        c.setStrokeColor(colors.HexColor("#C9CED6"))
        c.setLineWidth(0.3)
        c.rect(x + tag + 4, top - 10, flag_h * 1.5, flag_h, stroke=1, fill=0)
        if flag_text:
            text_x = x + tag + 4 + flag_h * 1.5 + 4
            font_size = 6.5
            while font_size > 4 and stringWidth(flag_text, "Helvetica-Bold", font_size) > right_edge - text_x:
                font_size -= 0.25
            c.setFillColor(tag_fill)
            c.setFont("Helvetica-Bold", font_size)
            c.drawString(text_x, top - 7, flag_text)
    y = top - PARTY_HEAD - 2 - size
    c.setFillColor(INK)
    c.setFont("Helvetica-Bold", size + 1)
    for line in name_lines:
        c.drawString(x, y, line)
        y -= size + 2.2
    c.setFont("Courier", size)
    for line in address_lines:
        c.drawString(x, y, line)
        y -= size + 1.6
    return top - _party_height(len(name_lines), len(address_lines), size)


def render_box_labels_pdf(shipment: dict[str, Any], box_number: int | None = None) -> bytes:
    """One label per box, or only ``box_number``. Raises ValueError for a box not in the plan."""
    boxes = shipment["boxes"]
    if box_number is not None:
        boxes = [b for b in boxes if b["sequence"] == box_number]
        if not boxes:
            raise ValueError(f"Box {box_number} is not in this packing plan.")
    W, H = LABEL_SIZE
    m, pad = 0.25 * cm, 7            # outer margin, inner padding
    left, right, top, bottom = m + pad, W - m - pad, H - m - pad, m + pad
    total = shipment["totals"]["boxes"]
    consignee = shipment["consignee"]
    exporter = shipment["exporter"]
    origin = country_code(exporter.get("country_of_origin", ""))
    destination = country_code(shipment.get("destination", ""))
    accent = country_color(destination)  # one color per destination country

    stream = BytesIO()
    c = pdf_canvas.Canvas(stream, pagesize=LABEL_SIZE)
    c.setTitle(f"Box labels - {shipment['order_number']}")
    c.setAuthor(exporter["company"])

    for box in boxes:
        c.setStrokeColor(INK)
        c.setLineWidth(1)
        c.roundRect(m, m, W - 2 * m, H - 2 * m, 5, stroke=1, fill=0)
        c.setLineWidth(0.7)

        # --- Header: logo + brand | order no. and date | box badge -------------------------
        header_bottom = top - 30
        x = left
        if LOGO_PATH.exists():
            c.drawImage(str(LOGO_PATH), x - 2, top - 24, width=24, height=24, mask="auto")
            x += 23
        c.setFillColor(NAVY)
        c.setFont("Helvetica-Bold", 12)
        c.drawString(x, top - 12, "THOUGHT3D.")
        c.setFillColor(SOFT)
        c.setFont("Helvetica", 5)
        c.drawString(x + 0.5, top - 20,
                     _clip(f"{exporter['company']} · {exporter.get('country_of_origin') or ''}".upper(), 30))

        badge = f"BOX {box['sequence']}/{total}"
        c.setFont("Helvetica-Bold", 12)
        badge_w = max(56, c.stringWidth(badge, "Helvetica-Bold", 12) + 14)
        c.setFillColor(accent)
        c.roundRect(right - badge_w, top - 24, badge_w, 24, 4, stroke=0, fill=1)
        c.setFillColor(colors.white)
        c.drawCentredString(right - badge_w / 2, top - 16, badge)

        meta_left, meta_right = left + 106, right - badge_w - 7
        c.setFillColor(INK)
        c.setFont("Courier", 5.6)
        c.drawString(meta_left, top - 9, "Order No.")
        c.drawString(meta_left, top - 20, "Date.")
        c.setFont("Courier-Bold", 6)
        c.drawRightString(meta_right, top - 9, _clip(shipment["order_number"], 12))
        c.drawRightString(meta_right, top - 20, shipment["order_date"] or "-")
        c.setStrokeColor(INK)
        c.line(m, header_bottom, W - m, header_bottom)

        # --- Middle: gross weight | contents ---------------------------------------------
        middle_bottom = header_bottom - 82
        split = left + 100
        c.line(split, header_bottom, split, middle_bottom)

        weight = f"{box['gross_weight_kg']:.2f}"
        size = 34 if len(weight) <= 5 else 27
        c.setFillColor(accent)
        c.setFont("Helvetica-Bold", size)
        c.drawString(left - 1, header_bottom - 44, weight)
        c.setFont("Helvetica-Bold", 10)
        c.drawString(left + c.stringWidth(weight, "Helvetica-Bold", size) + 1, header_bottom - 44, "kg")
        c.setFillColor(SOFT)
        c.setFont("Helvetica", 5.5)
        c.drawString(left, header_bottom - 54, "GROSS WEIGHT")
        dims = " x ".join(f"{v:g}" for v in box["dimensions_cm"])
        c.setFillColor(INK)
        c.setFont("Courier", 5.6)
        c.drawString(left, middle_bottom + 15, "Dimension")
        c.setFont("Courier-Bold", 6)
        c.drawString(left, middle_bottom + 7, f"{dims} cm")

        cx = split + pad
        _pill(c, "CONTENTS", cx, header_bottom - 15)
        units = sum(l["quantity"] for l in box["lines"])
        c.setFillColor(SOFT)
        c.setFont("Courier", 5.6)
        c.drawRightString(right, header_bottom - 12.5, f"{units} units")
        # As many lines as fit; the QR always carries the full list.
        list_top, list_bottom, step = header_bottom - 24, middle_bottom + 21, 7.2
        max_lines = int((list_top - list_bottom) // step) + 1
        lines = box["lines"]
        shown = lines if len(lines) <= max_lines else lines[: max_lines - 1]
        chars = int((right - cx) / 3.5)
        y = list_top
        for line in shown:
            qty = f"{line['quantity']}x "
            c.setFillColor(INK)
            c.setFont("Courier-Bold", 5.8)
            c.drawString(cx, y, qty)
            text = f"{line['code']} {line['description']}" if line["code"] else line["description"]
            c.setFont("Courier", 5.8)
            c.drawString(cx + c.stringWidth(qty, "Courier-Bold", 5.8), y, _clip(text, chars - len(qty)))
            y -= step
        if len(shown) < len(lines):
            c.setFillColor(SOFT)
            c.setFont("Courier-Oblique", 5.6)
            c.drawString(cx, y, f"+{len(lines) - len(shown)} more line(s) - see packing list")
        c.setStrokeColor(INK)
        c.line(split, middle_bottom + 15, W - m, middle_bottom + 15)
        c.setFillColor(INK)
        c.setFont("Courier", 5.6)
        c.drawString(cx, middle_bottom + 5, f"Box {box['box_code']}")
        c.setFont("Helvetica-Bold", 6.5)
        c.drawRightString(right, middle_bottom + 5, "HANDLE WITH CARE")
        c.line(m, middle_bottom, W - m, middle_bottom)

        # --- Bottom: QR | route | FROM / TO ------------------------------------------------
        band_h = 12
        section_top = middle_bottom - pad
        qr_size = min(84, section_top - (bottom + band_h + 6))
        _draw_qr(c, box_qr_payload(shipment, box), left, section_top - qr_size, qr_size)

        route_x, route_w = left + qr_size + 6, 18
        c.setFillColor(accent)
        c.roundRect(route_x, section_top - qr_size, route_w, qr_size, 4, stroke=0, fill=1)
        c.setFillColor(colors.white)
        c.setStrokeColor(colors.white)
        mid = route_x + route_w / 2
        c.setFont("Helvetica-Bold", 7.5)
        c.drawCentredString(mid, section_top - 11, origin)
        c.drawCentredString(mid, section_top - qr_size + 5, destination)
        arrow_top, arrow_bottom = section_top - 16, section_top - qr_size + 15
        c.setLineWidth(0.7)
        c.line(mid, arrow_top, mid, arrow_bottom)
        c.line(mid - 2.5, arrow_bottom + 3.5, mid, arrow_bottom)
        c.line(mid + 2.5, arrow_bottom + 3.5, mid, arrow_bottom)

        # FROM / TO are printed in full: text wraps, and the font shrinks until both blocks fit.
        party_x = route_x + route_w + 8
        party_w = right - party_x
        from_address = [l.strip() for l in (exporter.get("address") or "").splitlines() if l.strip()]
        from_address += [v for v in (exporter.get("contact"), exporter.get("phone")) if v]
        to_address = [l.strip() for l in (consignee.get("address") or "").splitlines() if l.strip()]
        to_address += [v for v in (consignee.get("contact") and f"Attn: {consignee['contact']}",
                                   consignee.get("phone"), consignee.get("email")) if v]
        from_name, to_name = exporter["company"], consignee.get("name") or shipment["client"]
        available = section_top - (bottom + band_h + 3)
        gap = 6
        for size in (6.0, 5.6, 5.2, 4.8, 4.4, 4.0, 3.6):
            from_layout = _party_layout(from_name, from_address, party_w, size)
            to_layout = _party_layout(to_name, to_address, party_w, size)
            if from_layout[2] + to_layout[2] + gap <= available:
                break
        y = _party(c, "FROM", from_layout[0], from_layout[1], party_x, section_top, size)
        _party(c, "TO", to_layout[0], to_layout[1], party_x, y - gap, size, tag_fill=accent,
               flag=_flag_path(destination), flag_text=(shipment.get("destination") or "").upper(),
               right_edge=right)

        # --- Footer band ------------------------------------------------------------------
        c.setFillColor(accent)
        c.roundRect(left, bottom, right - left, band_h, 3, stroke=0, fill=1)
        c.setFillColor(colors.white)
        c.setFont("Courier-Bold", 6)
        c.drawCentredString((left + right) / 2, bottom + 3.8,
                            f"PO {shipment['order_number']} · BOX {box['sequence']} OF {total} · {origin} > {destination}")
        c.showPage()
    c.save()
    return stream.getvalue()
