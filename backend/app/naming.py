"""Product-name normalisation shared by catalog matching and data backfills."""
from __future__ import annotations

import re
import unicodedata


def normalize_product_name(value: str) -> str:
    """Lower-case ASCII words; '250 ml' and '250ml' compare equal."""
    ascii_value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    normalized = re.sub(r"[^a-z0-9]+", " ", ascii_value.lower()).strip()
    return re.sub(r"(\d)\s+ml\b", r"\1ml", normalized)
