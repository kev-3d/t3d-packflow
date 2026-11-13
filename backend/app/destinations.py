"""Detect the DHL destination country of an order from its PO text.

Purchase orders put the ship-to country in a "Delivery Address" / "Deliver To" / "Ship To"
block. That block is searched first, then the other texts in the order given. Malta is the
shipping origin (Thought3D's own address appears on every PO), so it is never a destination.
"""

from __future__ import annotations

import re
import unicodedata
from functools import lru_cache
from typing import Iterable

from .dhl_pricing import load_tariff

ORIGIN = "MT"
DELIVERY_HEADER = re.compile(
    r"\b(deliver(?:y)?\s*(?:to|address)|ship(?:ping)?\s*(?:to|address)|consignee|"
    r"lieferadresse|lieferanschrift|adresse de livraison|indirizzo di consegna|direcci[oó]n de entrega)\b",
    re.I,
)
STOP_HEADER = re.compile(r"\b(invoice|billing|bill to|rechnungsadresse|vendor|supplier)\b", re.I)
US_STATE_ZIP = re.compile(
    r"\b(AL|AK|AZ|AR|CA|CO|CT|DE|FL|GA|HI|ID|IL|IN|IA|KS|KY|LA|ME|MD|MA|MI|MN|MS|MO|MT|NE|NV|NH|NJ|"
    r"NM|NY|NC|ND|OH|OK|OR|PA|RI|SC|SD|TN|TX|UT|VT|VA|WA|WV|WI|WY|DC)\s+\d{5}(?:-\d{4})?\b"
)
TRAILING_ISO = re.compile(r"\d[\w-]*\s+([A-Z]{2})\s*$")

# Common spellings that differ from the rate card's names.
ALIASES = {
    "US": ["United States", "United States of America", "U.S.A.", "U.S.A", "USA"],
    "GB": ["United Kingdom", "Great Britain", "England", "Scotland", "Wales", "Northern Ireland", "UK", "U.K."],
    "DE": ["Deutschland"], "AT": ["Österreich", "Osterreich"], "CH": ["Schweiz", "Suisse", "Svizzera"],
    "NL": ["Netherlands", "Holland", "Nederland"], "BE": ["Belgique", "België", "Belgie"],
    "ES": ["España", "Espana"], "IT": ["Italia"], "PL": ["Polska"], "CZ": ["Czech Republic", "Czechia"],
    "IE": ["Ireland", "Republic of Ireland", "Eire"], "KR": ["South Korea", "Korea"], "KP": ["North Korea"],
    "AE": ["UAE", "U.A.E."], "HK": ["Hong Kong"], "MO": ["Macau", "Macao"], "RU": ["Russia"],
    "PH": ["Philippines"], "RS": ["Serbia"], "MD": ["Moldova"], "ME": ["Montenegro"], "YE": ["Yemen"],
    "CD": ["Democratic Republic of the Congo", "DR Congo"], "CI": ["Ivory Coast", "Côte d'Ivoire", "Cote d'Ivoire"],
    "BA": ["Bosnia and Herzegovina", "Bosnia"], "MK": ["Macedonia"], "IC": ["Canary Islands", "Islas Canarias"],
    "SE": ["Sverige"], "DK": ["Danmark"], "NO": ["Norge"], "FI": ["Suomi"], "GR": ["Hellas"],
    "HR": ["Hrvatska"], "HU": ["Magyarország"], "PT": ["Portugal"], "FR": ["France"], "TR": ["Türkiye", "Turkiye"],
    "MX": ["México"], "BR": ["Brasil"], "TW": ["Taiwan"], "VN": ["Viet Nam"], "SA": ["Saudi Arabia"],
}


def _fold(text: str) -> str:
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()


def _clean_name(name: str) -> str:
    base = re.sub(r"\s*\(.*?\)", "", name)
    return base.split(",")[0].strip()


@lru_cache(maxsize=1)
def _patterns() -> list[tuple[re.Pattern[str], str, bool]]:
    """(pattern, code, folded). Short aliases like UK/USA/UAE must match exactly, in capitals."""
    names: dict[str, str] = {}
    for entry in load_tariff()["zones"]:
        if entry["code"] == "GB-SA":
            continue
        names.setdefault(_clean_name(entry["name"]), entry["code"])
    for code, aliases in ALIASES.items():
        for alias in aliases:
            names[alias] = code
    names.pop("", None)
    patterns = []
    # Longest first so "Papua New Guinea" wins over "Guinea", "Dominican Rep" over "Dominica".
    for name, code in sorted(names.items(), key=lambda item: -len(item[0])):
        short = len(name.replace(".", "")) <= 3
        text = name if short else _fold(name)
        patterns.append((re.compile(r"(?<![\w.])" + re.escape(text) + r"(?![\w])"), code, not short))
    return patterns


def _codes() -> set[str]:
    return {z["code"] for z in load_tariff()["zones"]} | {ORIGIN}


def country_in_line(line: str) -> str | None:
    if US_STATE_ZIP.search(line):
        return "US"
    if line.strip() in _codes():  # a bare ISO code on its own line, e.g. a "Country: US" field value
        return line.strip()
    folded = _fold(line)
    for pattern, code, use_folded in _patterns():
        if not pattern.search(folded if use_folded else line):
            continue
        if code == "JE" and re.search(r"new\s+jersey", folded):
            return "US"
        return code
    iso = TRAILING_ISO.search(line)
    if iso and iso.group(1) in _codes():
        return iso.group(1)
    return None


def _first_country(lines: Iterable[str]) -> str | None:
    for line in lines:
        code = country_in_line(line)
        if code and code != ORIGIN:
            return code
    return None


def delivery_block(text: str, max_lines: int = 10) -> list[str]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    for index, line in enumerate(lines):
        if DELIVERY_HEADER.search(line):
            block = []
            for following in lines[index + 1:index + 1 + max_lines]:
                if STOP_HEADER.search(following) and not DELIVERY_HEADER.search(following):
                    break
                block.append(following)
            if _first_country(block):
                return block
    return []


def detect_destination(sources: list[tuple[str, str]]) -> dict[str, str] | None:
    """``sources`` are (label, text) in priority order. Returns code + where it was found."""
    for label, text in sources:
        if not text or not text.strip():
            continue
        block = delivery_block(text)
        if block:
            return {"code": _first_country(block), "source": f"{label} (delivery address)"}
    for label, text in sources:
        if not text or not text.strip():
            continue
        code = _first_country(reversed([line for line in text.splitlines() if line.strip()]))
        if code:
            return {"code": code, "source": label}
    return None
