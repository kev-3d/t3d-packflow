"""Locate clients on the map by country, entirely offline.

``clients.country`` is free text typed into the Excel import ("USA", "United States",
"Deutschland"...). It is resolved to an ISO 3166-1 alpha-2 code with the same matcher the
DHL destination detection uses, so the map and the shipping quotes agree on spellings.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from .destinations import ORIGIN, country_in_line

# The DHL rate card lists destinations only, so the origin country has no name pattern there.
EXTRA_NAMES = {"malta": ORIGIN}


@lru_cache(maxsize=1024)
def country_code(country: str) -> str | None:
    """ISO alpha-2 code for a client's country text, or None when it cannot be resolved."""
    text = (country or "").strip()
    if not text:
        return None
    return EXTRA_NAMES.get(text.casefold()) or country_in_line(text)


def with_country_code(client: dict[str, Any]) -> dict[str, Any]:
    return {**client, "country_code": country_code(client.get("country") or "")}
