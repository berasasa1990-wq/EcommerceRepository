"""
Reusable text normalization for product search.

Works the same on SQLite (local) and PostgreSQL (Render).
No external search engines.
"""

from __future__ import annotations

import re
import unicodedata

# Security: hard cap on user query length
MAX_QUERY_LENGTH = 150
MAX_TOKENS = 12

_MULTI_SPACE_RE = re.compile(r'\s+')
_SEPARATOR_RE = re.compile(r'[\s_\-–—/\\|]+')

# 3,60m | 3.60 m | 150 g | 0,30mm | 14 mm
_MEASURE_RE = re.compile(
    r'(?P<num>\d+(?:[.,]\d+)?)\s*(?P<unit>mm|cm|m|g|gr|gram|grama|kg|lb|lbs|ft)\b',
    re.IGNORECASE,
)
_BARE_DECIMAL_RE = re.compile(r'\b(\d+)[,](\d+)\b')

# č/ć→c, š→s, ž→z  (đ handled separately as dj)
_DIACRITIC_MAP = str.maketrans({
    'š': 's', 'č': 'c', 'ć': 'c', 'ž': 'z',
    'Š': 's', 'Č': 'c', 'Ć': 'c', 'Ž': 'z',
    'ö': 'o', 'ü': 'u', 'ä': 'a', 'ß': 'ss',
    'Ö': 'o', 'Ü': 'u', 'Ä': 'a',
})


def sanitize_search_query(query: str | None) -> str:
    """
    Protect search endpoints:
    - strip control characters and null bytes
    - collapse whitespace
    - max 150 characters
    """
    if query is None:
        return ''
    text = str(query).replace('\x00', '')
    text = ''.join(ch for ch in text if ch in '\n\t' or ord(ch) >= 32)
    text = _MULTI_SPACE_RE.sub(' ', text).strip()
    if len(text) > MAX_QUERY_LENGTH:
        text = text[:MAX_QUERY_LENGTH].rstrip()
    return text


def normalize_measurements(value: str | None) -> str:
    """
    Normalize fishing measures so that:
    - 3,60m ≈ 3.60 m ≈ 3.6m
    - 14 mm ≈ 14mm
    - 150 g ≈ 150g
    - 0,30 mm ≈ 0.30mm ≈ 0.3mm
    """
    if not value:
        return ''
    text = str(value)

    def _repl(match: re.Match) -> str:
        num = match.group('num').replace(',', '.')
        if '.' in num:
            num = num.rstrip('0').rstrip('.')
        unit = match.group('unit').lower()
        if unit in ('gr', 'gram', 'grama'):
            unit = 'g'
        elif unit == 'lbs':
            unit = 'lb'
        return f'{num}{unit}'

    text = _MEASURE_RE.sub(_repl, text)
    # Bare European decimals: 3,60 → 3.60
    text = _BARE_DECIMAL_RE.sub(r'\1.\2', text)

    # Trim trailing zeros: 3.60 → 3.6, 0.30 → 0.3
    def _trim_decimal(match: re.Match) -> str:
        num = match.group(0)
        if '.' in num:
            return num.rstrip('0').rstrip('.')
        return num

    text = re.sub(r'\b\d+\.\d+\b', _trim_decimal, text)
    return text


def normalize_search_text(value: str | None) -> str:
    """
    Reusable normalization for search comparison and denormalized DB fields.

    - lowercase
    - collapse extra spaces and treat dashes as spaces
    - č/ć → c, š → s, ž → z
    - đ → dj (so đ and dj are equal)
    - measurement spacing/decimals via normalize_measurements first when building docs
    """
    if not value:
        return ''
    text = str(value).strip()
    if not text:
        return ''

    # đ / Đ → dj BEFORE unicode decomposition (đ can become d + combining mark)
    text = text.replace('đ', 'dj').replace('Đ', 'dj').replace('Ð', 'dj')

    text = unicodedata.normalize('NFKD', text)
    text = ''.join(ch for ch in text if not unicodedata.combining(ch))
    text = text.translate(_DIACRITIC_MAP)
    text = text.casefold()

    text = _SEPARATOR_RE.sub(' ', text)
    text = _MULTI_SPACE_RE.sub(' ', text).strip()
    return text


def tokenize_search_query(query: str | None) -> list[str]:
    """
    Sanitize → normalize measures → fold diacritics → split tokens.
    """
    raw = sanitize_search_query(query)
    if not raw:
        return []

    measured = normalize_measurements(raw)
    folded = normalize_search_text(measured)
    if not folded:
        return []

    tokens: list[str] = []
    seen: set[str] = set()
    for part in folded.split():
        if not part:
            continue
        if len(part) < 2 and not any(ch.isdigit() for ch in part):
            continue
        if part not in seen:
            seen.add(part)
            tokens.append(part)
        if len(tokens) >= MAX_TOKENS:
            break
    return tokens


def query_variants(query: str | None) -> list[str]:
    """
    Distinct strings for ORM icontains:
    sanitized original, measurement-normalized, fully folded.
    """
    raw = sanitize_search_query(query)
    if not raw:
        return []

    variants: list[str] = []
    seen: set[str] = set()

    def add(v: str) -> None:
        v = (v or '').strip()
        if not v:
            return
        key = v.casefold()
        if key not in seen:
            seen.add(key)
            variants.append(v)

    add(raw)
    measured = normalize_measurements(raw)
    add(measured)
    add(normalize_search_text(measured))
    add(raw.casefold())
    return variants
