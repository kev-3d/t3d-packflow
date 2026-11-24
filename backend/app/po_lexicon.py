"""Purchase-order vocabulary loaded from po_lexicon.json.

The parser never compares raw strings with a regex per label. Every label, column name and title is
normalized into a tuple of word tokens (no case, accents or edge punctuation), and a text matches a
synonym when its words match the synonym's words in order. Among all synonyms of a table the longest
one wins, so "Total qty" is a quantity total and "Date de la demande" is a requested date even though
"Total" and "Date" are synonyms of other fields.
"""
from __future__ import annotations

import json
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable

LEXICON_PATH = Path(__file__).with_name("po_lexicon.json")
_EDGE = ".:,;()[]{}°º*'\"“”‘’"


def normalize_token(word: str) -> str:
    """'Order' -> 'order', 'No.:' -> 'no', 'QTÉ' -> 'qte', 'Nº' -> 'no', '#' -> '#', '(%)' -> '%'."""
    text = unicodedata.normalize("NFKD", word).encode("ascii", "ignore").decode().lower()
    text = text.strip(_EDGE)
    if len(text) > 1:
        text = text.strip("#").strip(_EDGE)
    return text


def word_tokens(text: str) -> list[tuple[int, str]]:
    """(index of the word in text.split(), normalized token) for each word that keeps some text."""
    result: list[tuple[int, str]] = []
    for index, word in enumerate(text.split()):
        token = normalize_token(word)
        if token:
            result.append((index, token))
    return result


def _phrase(value: str) -> tuple[str, ...]:
    return tuple(token for _, token in word_tokens(value))


def _compile(values: Iterable[str]) -> list[tuple[str, tuple[str, ...]]]:
    compiled = {(value, _phrase(value)) for value in values}
    return sorted((item for item in compiled if item[1]), key=lambda item: -len(item[1]))


@dataclass(frozen=True)
class LabelHit:
    field: str
    synonym: str
    start: int       # index in text.split() of the first label word
    end: int         # index in text.split() after the last label word
    remainder: str   # the text after the label, without a leading ":" or "#"


class Lexicon:
    def __init__(self, data: dict[str, Any]):
        self.own_company_name: str = data["own_company"]["name"]
        self.own_company = [_phrase(alias) for alias in data["own_company"]["aliases"]]
        self.labels = {field: _compile(values) for field, values in data["labels"].items()}
        self.columns = {field: _compile(values) for field, values in data["columns"].items()}
        self.titles = {phrase for _, phrase in _compile(data["document_titles"])}
        self.company_suffixes = set(data["company_suffixes"])
        self.client_exclude = [phrase for _, phrase in _compile(data["client_exclude"])]
        self.measure_units = {normalize_token(unit) for unit in data["measure_units"]}
        self.quantity_units = {normalize_token(unit) for unit in data["quantity_units"]}
        self.currencies: dict[str, list[str]] = data["currencies"]
        self.months = {
            normalize_token(name): int(number) for number, names in data["months"].items() for name in names
        }

    # -- label matching -------------------------------------------------------------------------

    def _best_at(self, tokens: list[tuple[int, str]], position: int, table: dict) -> tuple[str, str, int] | None:
        best: tuple[str, str, int] | None = None
        for field, synonyms in table.items():
            for synonym, phrase in synonyms:
                size = len(phrase)
                if best and size <= best[2]:
                    break  # synonyms are sorted longest first
                if tuple(token for _, token in tokens[position:position + size]) == phrase:
                    best = (field, synonym, size)
                    break
        return best

    def find_label(self, text: str, fields: Iterable[str], *, anywhere: bool = False, table: str = "labels") -> LabelHit | None:
        """First label of one of `fields` in `text`; at the start only, unless `anywhere`.

        The longest synonym across the whole table is chosen before filtering by field, so a
        longer synonym of another field ("Total qty") is never read as a shorter one ("Total").
        """
        wanted = set(fields)
        words = text.split()
        tokens = word_tokens(text)
        source = self.labels if table == "labels" else self.columns
        positions = range(len(tokens)) if anywhere else range(min(1, len(tokens)))
        for position in positions:
            best = self._best_at(tokens, position, source)
            if not best or best[0] not in wanted:
                continue
            field, synonym, size = best
            start = tokens[position][0]
            end = tokens[position + size - 1][0] + 1
            remainder = " ".join(words[end:]).lstrip(":#-–— ").strip()
            return LabelHit(field, synonym, start, end, remainder)
        return None

    def column_fields(self, text: str) -> set[str]:
        """Column names present in a row of text, read greedily left to right."""
        tokens = word_tokens(text)
        fields: set[str] = set()
        position = 0
        while position < len(tokens):
            best = self._best_at(tokens, position, self.columns)
            if best:
                fields.add(best[0])
                position += best[2]
            else:
                position += 1
        return fields

    # -- other vocabulary -----------------------------------------------------------------------

    @staticmethod
    def _contains(tokens: tuple[str, ...], phrase: tuple[str, ...]) -> bool:
        size = len(phrase)
        return any(tokens[index:index + size] == phrase for index in range(len(tokens) - size + 1))

    def is_own_company(self, text: str) -> bool:
        tokens = _phrase(text)
        joined = "".join(tokens)
        return any(self._contains(tokens, alias) or "".join(alias) in joined for alias in self.own_company)

    def is_excluded_client(self, text: str) -> bool:
        tokens = _phrase(text)
        return any(self._contains(tokens, phrase) for phrase in self.client_exclude)

    def is_title(self, text: str) -> bool:
        return _phrase(text) in self.titles

    def has_company_suffix(self, text: str) -> bool:
        return any(word.rstrip(",;") in self.company_suffixes for word in text.split())


@lru_cache(maxsize=1)
def lexicon() -> Lexicon:
    return Lexicon(json.loads(LEXICON_PATH.read_text(encoding="utf-8")))
