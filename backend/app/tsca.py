"""TSCA certification (Toxic Substances Control Act) for shipments to the United States.

A draft is built from the order lines, edited by the user and printed on the Thought3D
certificate template. The app never decides whether the products comply with TSCA: the
user selects positive or negative certification after their own compliance review.
"""
from __future__ import annotations

import re
from datetime import date
from io import BytesIO
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_JUSTIFY
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas as pdf_canvas
from reportlab.platypus import Flowable, Frame, PageTemplate, Paragraph, BaseDocTemplate, Spacer, Table, TableStyle

from .shipping_docs import product_code

# Only shipments to these destinations (DHL destination codes) need the certificate.
TSCA_DESTINATIONS = {"US"}

POSITIVE_TEXT = ("I certify that all chemical substances in this shipment comply with all applicable rules or "
                 "orders under TSCA and that I am not offering a chemical substance for entry in violation of "
                 "TSCA or any applicable rule or order thereunder.")
NEGATIVE_TEXT = "I certify that all chemicals in this shipment are not subject to TSCA."
CERTIFICATIONS = ("", "positive", "negative")

LIMITS = {"date": 100, "reference": 200, "items": 200, "number": 150, "name": 500, "description": 4000}

COMPANY = {
    "name": "Thought3D Ltd",
    "registration": "C67651",
    "address": "Triq il-Qasam, Swieqi SWQ 3026, Malta",
    "email": "info@thought3d.com",
    "web": "www.thought3d.com",
    "vat": "MT 2217-2828",
}

# Template palette.
ACCENT = colors.HexColor("#35C5F3")
ACCENT_DARK = colors.HexColor("#1A9FCF")
INK = colors.HexColor("#2E2E30")
GREY = colors.HexColor("#5E5E5F")
SOFT = colors.HexColor("#8A8A8C")
RULE = colors.HexColor("#D9DDE1")
PANEL = colors.HexColor("#EAF8FE")
PANEL_RULE = colors.HexColor("#C8ECFA")
SELECTED_FILL = colors.HexColor("#F7FCFF")
ROW_ALT = colors.HexColor("#F6F8FA")
DRAFT_RED = colors.HexColor("#B4371F")

PAGE_W, PAGE_H = A4
MARGIN = 56.69291
CONTENT_W = PAGE_W - 2 * MARGIN
RIGHT = PAGE_W - MARGIN
FONTS_DIR = Path(__file__).parent / "assets" / "fonts"


def _register_fonts() -> dict[str, str]:
    """Poppins (as in the template) when its TTF files are in assets/fonts, else Helvetica."""
    wanted = {"regular": "Poppins-Regular", "medium": "Poppins-Medium", "light": "Poppins-Light",
              "bold": "Poppins-Bold", "italic": "Poppins-Italic"}
    if all((FONTS_DIR / f"{name}.ttf").exists() for name in wanted.values()):
        for name in wanted.values():
            if name not in pdfmetrics.getRegisteredFontNames():
                pdfmetrics.registerFont(TTFont(name, str(FONTS_DIR / f"{name}.ttf")))
        return wanted
    return {"regular": "Helvetica", "medium": "Helvetica-Bold", "light": "Helvetica",
            "bold": "Helvetica-Bold", "italic": "Helvetica-Oblique"}


class TscaError(ValueError):
    """Invalid TSCA draft (bad structure or text too long)."""


# --------------------------------------------------------------------------- draft


def default_date(today: date | None = None) -> str:
    return (today or date.today()).strftime("%B %Y")


def default_draft(order: dict[str, Any], lines: list[dict[str, Any]], today: date | None = None) -> dict[str, Any]:
    """Draft from the order lines: one row per product (quantity > 0, no duplicates).

    ``lines`` are order items joined with their catalog product (``ean``, ``tsca_name``,
    ``tsca_description``, ``product_name``). Catalog declaration wording takes precedence.
    """
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for line in lines:
        if (line.get("quantity") or 0) <= 0:
            continue
        product_id = line.get("product_id")
        key = f"p{product_id}" if product_id is not None else "d" + " ".join(str(line.get("description") or "").lower().split())
        if key in seen:
            continue
        seen.add(key)
        items.append({
            "product_id": product_id,
            "number": (line.get("ean") or "").strip() or product_code(line),
            "name": (line.get("tsca_name") or "").strip() or line.get("product_name") or line.get("description") or "",
            "description": (line.get("tsca_description") or "").strip(),
        })
    return {"date": default_date(today), "reference": order.get("order_number") or "",
            "certification": "", "items": items}


def fill_from_catalog(draft: dict[str, Any], products: dict[int, dict[str, Any]]) -> dict[str, Any]:
    """Complete empty number / name / description of catalog rows from the catalog (EAN and
    TSCA wording); text the user typed is kept."""
    for item in draft["items"]:
        product = products.get(item.get("product_id"))  # type: ignore[arg-type]
        if not product:
            continue
        if not item["number"] and (product.get("ean") or "").strip():
            item["number"] = product["ean"].strip()
        if not item["name"]:
            item["name"] = (product.get("tsca_name") or "").strip() or product.get("name") or ""
        if not item["description"] and (product.get("tsca_description") or "").strip():
            item["description"] = product["tsca_description"].strip()
    return draft


def normalize_draft(raw: Any) -> dict[str, Any]:
    """Validate structure and lengths; returns a clean copy. Raises TscaError."""
    if not isinstance(raw, dict):
        raise TscaError("Invalid TSCA draft.")
    out: dict[str, Any] = {}
    for key in ("date", "reference"):
        value = raw.get(key, "")
        if not isinstance(value, str) or len(value) > LIMITS[key]:
            raise TscaError(f"The {key} is invalid or too long.")
        out[key] = value.strip()
    if raw.get("certification", "") not in CERTIFICATIONS:
        raise TscaError("Choose positive or negative certification.")
    out["certification"] = raw.get("certification", "")
    items = raw.get("items", [])
    if not isinstance(items, list) or len(items) > LIMITS["items"]:
        raise TscaError(f"A declaration can list at most {LIMITS['items']} products.")
    out["items"] = []
    for index, item in enumerate(items, 1):
        if not isinstance(item, dict):
            raise TscaError(f"Product {index} is invalid.")
        row: dict[str, Any] = {}
        for key in ("number", "name", "description"):
            value = item.get(key, "")
            if not isinstance(value, str) or len(value) > LIMITS[key]:
                raise TscaError(f"Product {index}: the {key} is invalid or too long.")
            row[key] = value.strip()
        product_id = item.get("product_id")
        if product_id is not None and (not isinstance(product_id, int) or isinstance(product_id, bool)):
            raise TscaError(f"Product {index} is invalid.")
        row["product_id"] = product_id
        out["items"].append(row)
    return out


def draft_issues(draft: dict[str, Any]) -> list[str]:
    """What still blocks the final certificate (the draft PDF can always be printed)."""
    issues = []
    if not draft["date"]:
        issues.append("Enter the declaration date.")
    if not draft["certification"]:
        issues.append("Choose positive or negative certification after checking applicability.")
    if not draft["items"]:
        issues.append("Add at least one product.")
    for index, item in enumerate(draft["items"], 1):
        missing = [label for key, label in (("number", "product number"), ("name", "name"),
                                            ("description", "description")) if not item[key]]
        if missing:
            issues.append(f"Product {index}: enter its {', '.join(missing)}.")
    return issues


def looks_like_ean(value: str) -> bool:
    return bool(re.fullmatch(r"\d{8,14}", value.strip()))


# --------------------------------------------------------------------------- PDF


def _rounded(c: pdf_canvas.Canvas, x: float, y: float, w: float, h: float, r: float, *, fill, stroke=None, width=0.6):
    c.saveState()
    c.setFillColor(fill)
    if stroke is not None:
        c.setStrokeColor(stroke)
        c.setLineWidth(width)
    c.roundRect(x, y, w, h, r, stroke=1 if stroke is not None else 0, fill=1)
    c.restoreState()


def _wordmark(c: pdf_canvas.Canvas, x: float, y: float, size: float, font: str, main) -> None:
    c.setFont(font, size)
    c.setFillColor(main)
    c.drawString(x, y, "THOUGHT")
    c.setFillColor(ACCENT)
    c.drawString(x + c.stringWidth("THOUGHT", font, size), y, "3D")


def _bulb(c: pdf_canvas.Canvas, x: float, y: float, scale: float = 0.95) -> None:
    """The Thought3D bulb icon, as drawn in the template."""
    c.saveState()
    c.translate(x, y)
    c.scale(scale, scale)
    c.setStrokeColor(ACCENT)
    c.setLineWidth(2.6)
    c.setLineCap(1)
    c.setLineJoin(1)
    path = c.beginPath()
    path.moveTo(9, 15)
    path.curveTo(9, 22, 2, 25, 2, 36)
    path.curveTo(2, 44.28, 8.72, 51, 17, 51)
    path.curveTo(25.28, 51, 32, 44.28, 32, 36)
    path.curveTo(32, 25, 25, 22, 25, 15)
    c.drawPath(path, stroke=1, fill=0)
    for x1, y1, x2, y2 in ((10, 40, 24, 40), (17, 40, 17, 15), (9, 15, 25, 15), (10, 10.5, 24, 10.5), (12, 6, 22, 6)):
        c.line(x1, y1, x2, y2)
    c.restoreState()


class _Section(Flowable):
    """Numbered section heading with a rule to the right edge (and an optional note below)."""

    def __init__(self, number: str, title: str, fonts: dict[str, str], note: str = ""):
        super().__init__()
        self.number, self.title, self.fonts, self.note = number, title, fonts, note

    def wrap(self, avail_w, avail_h):
        self.width = avail_w
        return avail_w, 26 if self.note else 14

    def draw(self):
        c, f = self.canv, self.fonts
        y = 26 - 12 if self.note else 2
        c.setFillColor(ACCENT)
        c.setFont(f["bold"], 8.5)
        c.drawString(0, y, self.number)
        c.setFillColor(INK)
        c.setFont(f["medium"], 10)
        c.drawString(16, y, self.title)
        start = 16 + c.stringWidth(self.title, f["medium"], 10) + 12
        c.setStrokeColor(RULE)
        c.setLineWidth(0.6)
        c.line(start, y + 3, self.width, y + 3)
        if self.note:
            c.setFillColor(SOFT)
            c.setFont(f["italic"], 7.6)
            c.drawString(16, y - 14, self.note)


class _InfoPanel(Flowable):
    def __init__(self, cells: list[tuple[str, str]], fonts: dict[str, str]):
        super().__init__()
        self.cells, self.fonts = cells, fonts

    def wrap(self, avail_w, avail_h):
        self.width = avail_w
        return avail_w, 40

    def draw(self):
        c, f = self.canv, self.fonts
        _rounded(c, 0, 0, self.width, 40, 5, fill=PANEL)
        offsets = (12, 127.65, 252.95, 368.6)
        for index, ((label, value), x) in enumerate(zip(self.cells, offsets)):
            if index:
                c.setStrokeColor(PANEL_RULE)
                c.setLineWidth(0.8)
                c.line(x - 12, 8, x - 12, 32)
            c.setFillColor(ACCENT_DARK)
            c.setFont(f["medium"], 6.6)
            c.drawString(x, 25, label)
            c.setFillColor(INK)
            c.setFont(f["medium"], 9.6)
            limit = (offsets[index + 1] - offsets[index] - 16) if index < 3 else self.width - x - 8
            text = value
            while text and c.stringWidth(text, f["medium"], 9.6) > limit:
                text = text[:-2] + "…"
            c.drawString(x, 11, text)


class _Option(Flowable):
    """One certification option: a rounded card with a checkbox, highlighted when chosen."""

    def __init__(self, title: str, text: str, selected: bool, fonts: dict[str, str]):
        super().__init__()
        self.title, self.selected, self.fonts = title, selected, fonts
        self.body = Paragraph(escape(text), ParagraphStyle(
            "option", fontName=fonts["regular"], fontSize=8.8, leading=13.2, textColor=INK, alignment=TA_JUSTIFY))

    def wrap(self, avail_w, avail_h):
        self.width = avail_w
        _, self.body_h = self.body.wrap(avail_w - 32 - 10, avail_h)
        self.height = self.body_h + 30
        return avail_w, self.height

    def draw(self):
        c, f, h = self.canv, self.fonts, self.height
        if self.selected:
            _rounded(c, 0, 0, self.width, h, 5, fill=SELECTED_FILL, stroke=ACCENT, width=0.9)
            c.setFillColor(ACCENT)
            c.rect(0, 5, 2.4, h - 10, stroke=0, fill=1)
            _rounded(c, 14, h - 22, 11, 11, 2, fill=ACCENT, stroke=ACCENT, width=1)
            c.setStrokeColor(colors.white)
            c.setLineWidth(1.8)
            c.setLineCap(1)
            path = c.beginPath()
            path.moveTo(16.6, h - 16.4)
            path.lineTo(18.7, h - 18.8)
            path.lineTo(22.6, h - 13.8)
            c.drawPath(path, stroke=1, fill=0)
        else:
            _rounded(c, 0, 0, self.width, h, 5, fill=colors.white, stroke=RULE, width=0.6)
            _rounded(c, 14, h - 22, 11, 11, 2, fill=colors.white, stroke=SOFT, width=1)
        c.setFillColor(INK if self.selected else GREY)
        c.setFont(f["medium"], 9)
        c.drawString(32, h - 19.5, self.title)
        self.body.drawOn(c, 32, 6)


def _products_table(items: list[dict[str, Any]], fonts: dict[str, str]) -> Table:
    head = ParagraphStyle("head", fontName=fonts["medium"], fontSize=8, leading=10, textColor=colors.white)
    cell = ParagraphStyle("cell", fontName=fonts["regular"], fontSize=8.4, leading=11, textColor=INK)
    rows: list[list[Any]] = [[Paragraph(t, head) for t in ("#", "Product number (EAN)", "Product name", "Product description")]]
    for index, item in enumerate(items, 1):
        rows.append([Paragraph(str(index), cell)] + [
            Paragraph(escape(item[key]).replace("\n", "<br/>") or "&nbsp;", cell)
            for key in ("number", "name", "description")])
    widths = [22, 112, 160, CONTENT_W - 294]
    table = Table(rows, colWidths=widths, repeatRows=1, splitByRow=1)
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), GREY),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, 0), 5.5),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 5.5),
        ("TOPPADDING", (0, 1), (-1, -1), 5.5),
        ("BOTTOMPADDING", (0, 1), (-1, -1), 5.5),
        ("LINEBELOW", (0, 1), (-1, -1), 0.5, RULE),
        ("LINEBELOW", (0, -1), (-1, -1), 1, ACCENT),
    ]
    for row in range(2, len(rows), 2):
        style.append(("BACKGROUND", (0, row), (-1, row), ROW_ALT))
    table.setStyle(TableStyle(style))
    return table


def _canvas_class(label: str, fonts: dict[str, str], draft_mode: bool):
    """Canvas that adds the footer label and 'Page x of y' once the page count is known."""

    class TscaCanvas(pdf_canvas.Canvas):
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
                self.setFont(fonts["regular"], 6.8)
                self.setFillColor(SOFT)
                self.drawString(MARGIN, 93.04, label)
                self.drawRightString(RIGHT, 93.04, f"Page {self._pageNumber} of {total}")
                if draft_mode:
                    self.setFillColor(DRAFT_RED)
                    self.setFont(fonts["bold"], 6.8)
                    self.drawCentredString(PAGE_W / 2, 93.04, "DRAFT - NOT FOR SUBMISSION")
                self.restoreState()
                super().showPage()
            super().save()

    return TscaCanvas


def _page_decoration(fonts: dict[str, str], draft_mode: bool):
    def draw(c: pdf_canvas.Canvas, _doc) -> None:
        f = fonts
        c.saveState()
        # Top accent bar.
        c.setFillColor(ACCENT)
        c.rect(0, PAGE_H - 5, PAGE_W, 5, stroke=0, fill=1)
        # Brand block.
        _bulb(c, MARGIN, 756.85)
        _wordmark(c, 94.69, 772.85, 17, f["medium"], GREY)
        c.setFillColor(SOFT)
        c.setFont(f["light"], 7.2)
        c.drawString(94.69, 762.85, f"{COMPANY['name']}  ·  {COMPANY['registration']}")
        # Title block.
        c.setFillColor(ACCENT_DARK)
        c.setFont(f["medium"], 7.5)
        c.drawRightString(RIGHT, 788.85, "CERTIFICATE OF COMPLIANCE")
        c.setFillColor(INK)
        c.setFont(f["bold"], 17)
        c.drawRightString(RIGHT, 769.85, "TSCA Certification")
        c.setFillColor(GREY)
        c.setFont(f["light"], 7.8)
        c.drawRightString(RIGHT, 757.85, "Toxic Substances Control Act  ·  15 U.S.C. 2612  ·  19 CFR 12.121")
        c.setStrokeColor(RULE)
        c.setLineWidth(0.8)
        c.line(MARGIN, 746.85, RIGHT, 746.85)
        if draft_mode:
            c.saveState()
            c.setFillColor(DRAFT_RED)
            c.setFillAlpha(0.07)
            c.setFont(f["bold"], 96)
            c.translate(PAGE_W / 2, PAGE_H / 2)
            c.rotate(35)
            c.drawCentredString(0, -30, "DRAFT")
            c.restoreState()
        # Footer band.
        c.setFillColor(GREY)
        c.rect(0, 0, PAGE_W, 85.04, stroke=0, fill=1)
        c.setFillColor(ACCENT)
        c.rect(0, 85.04, PAGE_W, 2, stroke=0, fill=1)
        _wordmark(c, MARGIN, 59.04, 13, f["medium"], colors.white)
        c.setFillColor(colors.white)
        c.setFont(f["regular"], 8)
        c.drawString(MARGIN, 41.04, f"{COMPANY['name']}  ·  Reg. {COMPANY['registration']}")
        c.drawString(MARGIN, 29.04, COMPANY["address"])
        for y, key, value in ((63.04, "E", COMPANY["email"]), (50.04, "W", COMPANY["web"]), (37.04, "VAT", COMPANY["vat"])):
            c.setFont(f["regular"], 8)
            c.setFillColor(colors.white)
            c.drawRightString(RIGHT, y, value)
            value_x = RIGHT - c.stringWidth(value, f["regular"], 8)
            c.setFont(f["medium"], 6.8)
            c.setFillColor(ACCENT)
            c.drawRightString(value_x - 6, y, key)
        c.restoreState()
    return draw


def render_tsca_pdf(draft: dict[str, Any], *, final: bool) -> bytes:
    """Certificate PDF in the Thought3D template. ``final=False`` marks every page as a draft."""
    draft = normalize_draft(draft)
    if final and draft_issues(draft):
        raise TscaError(" ".join(draft_issues(draft)))
    fonts = _register_fonts()
    reference = draft["reference"]
    buffer = BytesIO()
    doc = BaseDocTemplate(
        buffer, pagesize=A4, leftMargin=MARGIN, rightMargin=MARGIN, topMargin=PAGE_H - 734.85,
        bottomMargin=104, title=f"TSCA Certification{f' - {reference}' if reference else ''}{'' if final else ' - DRAFT'}",
        author=COMPANY["name"], subject="TSCA certification",
    )
    frame = Frame(MARGIN, 104, CONTENT_W, 734.85 - 104, leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)
    doc.addPageTemplates([PageTemplate(id="certificate", frames=[frame], onPage=_page_decoration(fonts, not final))])

    count = len(draft["items"])
    intro = ParagraphStyle("intro", fontName=fonts["regular"], fontSize=9.2, leading=14, textColor=INK, alignment=TA_JUSTIFY)
    bold = fonts["bold"]
    story: list[Any] = [
        _InfoPanel([("DATE OF ISSUE", draft["date"] or "—"), ("SHIPMENT / REFERENCE", reference or "—"),
                    ("VALIDITY", "12 months"), ("PRODUCTS COVERED", f"{count} item{'' if count == 1 else 's'}")], fonts),
        Spacer(1, 12),
        Paragraph(
            "This certification is made in accordance with Section 13 of the Toxic Substances Control Act (TSCA) and "
            "the U.S. Customs and Border Protection regulations at 19 CFR 12.118–12.127. It is valid for a period of "
            f"<font name='{bold}'>twelve (12) months</font> from the date submitted and applies "
            f"<font name='{bold}'>exclusively</font> to the products listed in Section 2. Any product not listed, or "
            "any change in the formulation of a listed product, requires a new certification.", intro),
        Spacer(1, 12),
        _Section("01", "CERTIFICATION STATEMENT", fonts, note="Select one option only."),
        Spacer(1, 8),
        _Option("Positive certification", POSITIVE_TEXT, draft["certification"] == "positive", fonts),
        Spacer(1, 8),
        _Option("Negative certification", NEGATIVE_TEXT, draft["certification"] == "negative", fonts),
        Spacer(1, 12),
        _Section("02", "PRODUCTS COVERED", fonts),
        Spacer(1, 12),
        _products_table(draft["items"], fonts),
    ]
    label = f"TSCA Certification{f'  ·  {reference}' if reference else ''}"
    doc.build(story, canvasmaker=_canvas_class(label, fonts, not final))
    return buffer.getvalue()
