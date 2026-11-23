"""Per-country label colors, so a box's destination is recognizable at a glance.

Every color carries white text at WCAG AA contrast (>= 4.5:1). The main destinations get a
fixed, unique color; any other country gets a stable color from the same palette.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from reportlab.lib import colors

from . import dhl_pricing

# Distinct, white-text-safe accents. Order matters only for the hash fallback.
PALETTE = [
    "#1D4ED8",  # blue
    "#047857",  # green
    "#B91C1C",  # red
    "#7C3AED",  # violet
    "#C2410C",  # orange
    "#0F766E",  # teal
    "#BE185D",  # pink
    "#4D7C0F",  # olive
    "#1E3A8A",  # navy
    "#A16207",  # amber
    "#0369A1",  # ocean
    "#6B21A8",  # purple
    "#9F1239",  # crimson
    "#475569",  # slate
    "#7C2D12",  # brown
    "#0E7490",  # cyan
    "#86198F",  # fuchsia
    "#3F6212",  # moss
    "#374151",  # graphite
    "#9A3412",  # rust
]

# Fixed colors for the most frequent destinations (each unique).
COUNTRY_COLORS = {
    "US": "#1D4ED8",  # United States - blue
    "DE": "#374151",  # Germany - graphite
    "GB": "#1E3A8A",  # United Kingdom - navy
    "IT": "#047857",  # Italy - green
    "FR": "#0369A1",  # France - ocean
    "MT": "#B91C1C",  # Malta - red
    "CA": "#9F1239",  # Canada - crimson
    "ES": "#C2410C",  # Spain - orange
    "NL": "#9A3412",  # Netherlands - rust
    "IL": "#0E7490",  # Israel - cyan
    "PL": "#BE185D",  # Poland - pink
    "AE": "#4D7C0F",  # United Arab Emirates - olive
    "CH": "#7C2D12",  # Switzerland - brown
    "SE": "#A16207",  # Sweden - amber
    "FI": "#475569",  # Finland - slate
    "BR": "#3F6212",  # Brazil - moss
    "IN": "#7C3AED",  # India - violet
    "BE": "#86198F",  # Belgium - fuchsia
    "AU": "#0F766E",  # Australia - teal
    "DK": "#6B21A8",  # Denmark - purple
}


def color_hex(code: str) -> str:
    """Hex accent for an ISO country code; unknown codes get a stable palette color."""
    code = (code or "").upper()
    if code in COUNTRY_COLORS:
        return COUNTRY_COLORS[code]
    digest = int(hashlib.sha1(code.encode()).hexdigest(), 16)
    return PALETTE[digest % len(PALETTE)]


def country_color(code: str) -> colors.Color:
    return colors.HexColor(color_hex(code))


FLAGS_DIR = Path(__file__).parent / "assets" / "flags"  # flagcdn.com PNGs, named by ISO code
COUNTRY_FALLBACK = {"malta": "MT", "usa": "US", "uk": "GB", "united kingdom": "GB", "united states": "US"}


def country_code(name: str) -> str:
    """ISO code for a country name from the DHL zone list; first letters as a last resort."""
    name = (name or "").strip()
    if not name:
        return "-"
    if len(name) == 2:
        return name.upper()
    try:
        zones = dhl_pricing.load_tariff()["zones"]
        wanted = name.lower()
        for zone in zones:
            if zone["name"].lower() == wanted:
                return zone["code"]
        if wanted in COUNTRY_FALLBACK:
            return COUNTRY_FALLBACK[wanted]
        # "United States" vs "United States of America" and similar long forms (plain ISO codes only).
        for zone in zones:
            if len(zone["code"]) == 2 and (zone["name"].lower().startswith(wanted + " ")
                                           or wanted.startswith(zone["name"].lower() + " ")):
                return zone["code"]
    except Exception:  # A missing tariff must not break label printing.
        pass
    return COUNTRY_FALLBACK.get(name.lower(), name[:3].upper())


def flag_path(code: str) -> Path | None:
    path = FLAGS_DIR / f"{(code or '').lower()}.png"
    return path if len(code or "") == 2 and path.exists() else None
