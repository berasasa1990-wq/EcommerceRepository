"""
Parse and normalize fishing product measures for search.

Canonical storage units (normalized_numeric_value):
- length        → metres (m)
- weight        → grams (g)
- casting_weight→ grams (g)
- diameter      → millimetres (mm)
- bait_size     → millimetres (mm)
- test_curve    → pounds (lb)
- diving_depth  → metres (m)
- reel_size     → dimensionless number
- capacity      → raw numeric (unit preserved)
- pieces        → count

Does NOT equate different attribute types (e.g. 14mm bait ≠ 14mm line diameter).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

# Reel sizes commonly used in product names
REEL_SIZES = frozenset({
    '1000', '1500', '2000', '2500', '3000', '3500', '4000', '4500', '5000',
    '5500', '6000', '6500', '7000', '8000', '10000', '12000', '14000',
})

# Explicit unit measures: 3.60 m, 150g, 0,30mm, 3.5 lb, 13ft
_MEASURE_TOKEN_RE = re.compile(
    r'(?P<num>\d+(?:[.,]\d+)?)\s*(?P<unit>mm|cm|m|g|gr|gram|grama|kg|lb|lbs|ft)\b',
    re.IGNORECASE,
)
# Bare reel size as whole token — \b so SH4000 / FX4000-V are NOT treated as size 4000
_BARE_REEL_RE = re.compile(
    r'\b(?P<num>' + '|'.join(sorted(REEL_SIZES, key=len, reverse=True)) + r')\b',
)
# Bare decimal without unit: 3.60 or 3,60 (rod length heuristic when 2.0–5.5)
_BARE_DECIMAL_RE = re.compile(r'(?<![\d.])(?P<num>\d+[.,]\d+)(?![\d.a-zA-Z])')

# Context keywords (normalized fold applied by caller)
_LENGTH_CTX = frozenset({
    'stap', 'rod', 'pecaljka', 'feeder', 'fider', 'spin', 'spinning',
    'varalicarski', 'saranski', 'duzina', 'ft', 'metar', 'metra',
})
_CASTING_CTX = frozenset({
    'stap', 'rod', 'feeder', 'fider', 'gramaza', 'casting', 'cw', 'test',
})
_DIAMETER_CTX = frozenset({
    'najlon', 'struna', 'monofil', 'line', 'pletenica', 'pletenice',
    'fluorocarbon', 'fc', 'promjer', 'debljina',
})
_BAIT_CTX = frozenset({
    'boila', 'boilie', 'boilies', 'mamac', 'mamci', 'pellet', 'peleta',
    'kukuruz', 'dumbell', 'wafter', 'pop up', 'popup',
})
_REEL_CTX = frozenset({
    'masinica', 'reel', 'rolna', 'spinning', 'baitrunner',
})
_TEST_CURVE_CTX = frozenset({
    'lb', 'test', 'curve', 'tc', 'stap', 'rod', 'carp',
})


@dataclass(frozen=True)
class ParsedMeasure:
    """One measure extracted from a search query or product text."""
    attribute_types: tuple[str, ...]  # one or more allowed types for matching
    numeric_value: Decimal
    unit: str  # original/display unit after alias
    normalized_value: Decimal  # in canonical unit for that type family
    canonical_unit: str
    source_text: str
    confidence: str  # 'unit' | 'reel' | 'bare_decimal'


def _to_decimal(num_str: str) -> Decimal | None:
    try:
        return Decimal(str(num_str).replace(',', '.').strip())
    except (InvalidOperation, ValueError, TypeError):
        return None


def _q(value: Decimal, places: str = '0.0001') -> Decimal:
    return value.quantize(Decimal(places), rounding=ROUND_HALF_UP)


def convert_to_canonical(value: Decimal, unit: str, attribute_type: str) -> tuple[Decimal, str]:
    """
    Convert value+unit to canonical (normalized_value, canonical_unit) for type.
    """
    u = (unit or '').lower().strip()
    if attribute_type == 'length' or attribute_type == 'diving_depth':
        if u == 'mm':
            return _q(value / Decimal('1000')), 'm'
        if u == 'cm':
            return _q(value / Decimal('100')), 'm'
        if u == 'ft':
            return _q(value * Decimal('0.3048')), 'm'
        if u == 'm' or u == '':
            return _q(value), 'm'
        return _q(value), 'm'

    if attribute_type in ('weight', 'casting_weight'):
        if u == 'kg':
            return _q(value * Decimal('1000')), 'g'
        if u in ('lb', 'lbs'):
            # mass conversion (not used for test_curve)
            return _q(value * Decimal('453.59237')), 'g'
        return _q(value), 'g'

    if attribute_type in ('diameter', 'bait_size'):
        if u == 'cm':
            return _q(value * Decimal('10')), 'mm'
        if u == 'm':
            return _q(value * Decimal('1000')), 'mm'
        return _q(value), 'mm'

    if attribute_type == 'test_curve':
        return _q(value, '0.01'), 'lb'

    if attribute_type == 'reel_size':
        return _q(value, '1'), ''

    if attribute_type == 'pieces':
        return _q(value, '1'), 'kom'

    # capacity / unknown: keep as-is
    return _q(value), u


def normalize_unit_alias(unit: str | None) -> str:
    u = (unit or '').lower().strip()
    if u in ('gr', 'gram', 'grama'):
        return 'g'
    if u == 'lbs':
        return 'lb'
    return u


def infer_attribute_types(
    value: Decimal,
    unit: str,
    *,
    context_tokens: frozenset[str] | set[str] | None = None,
) -> tuple[str, ...]:
    """
    Infer which ProductAttribute types a measure may belong to.
    Returns multiple types only when truly ambiguous (never mixes unrelated families).
    """
    ctx = context_tokens or frozenset()
    u = normalize_unit_alias(unit)

    if u in ('m', 'cm', 'ft'):
        return ('length',)

    if u in ('lb', 'lbs'):
        return ('test_curve',)

    if u == 'kg':
        return ('weight',)

    if u == 'g':
        if ctx & _CASTING_CTX or not ctx:
            # Fishing shop: "150g" on rods is casting weight; product mass rare
            return ('casting_weight',)
        return ('casting_weight', 'weight')

    if u == 'mm':
        types: list[str] = []
        if ctx & _BAIT_CTX:
            types.append('bait_size')
        if ctx & _DIAMETER_CTX:
            types.append('diameter')
        if types:
            return tuple(types)
        # No context: do NOT force one type — match either (OR), still not length/weight
        try:
            v = float(value)
        except Exception:
            v = 0
        if v <= 1.5:
            # line diameters typically 0.08–1.0 mm
            return ('diameter',)
        if 6 <= v <= 30:
            # boilie / pellet sizes
            return ('bait_size',)
        return ('diameter', 'bait_size')

    if u == '':
        # reel size
        if str(int(value)) if value == value.to_integral_value() else '' in REEL_SIZES:
            return ('reel_size',)
        # bare 3.60 style handled separately as length
        return ('reel_size',)

    return ('capacity',)


def parse_measures_from_text(
    text: str | None,
    *,
    context_tokens: set[str] | frozenset[str] | None = None,
) -> list[ParsedMeasure]:
    """Extract measures from free text (query, product name, description)."""
    if not text:
        return []
    raw = str(text)
    ctx = set(context_tokens or ())
    # fold simple diacritics for context keywords already expected normalized by caller
    results: list[ParsedMeasure] = []
    occupied: list[tuple[int, int]] = []  # char spans consumed

    def _overlaps(start: int, end: int) -> bool:
        for a, b in occupied:
            if start < b and end > a:
                return True
        return False

    # 1) Explicit unit measures
    for m in _MEASURE_TOKEN_RE.finditer(raw):
        num = _to_decimal(m.group('num'))
        if num is None:
            continue
        unit = normalize_unit_alias(m.group('unit'))
        types = infer_attribute_types(num, unit, context_tokens=ctx)
        # Use first type for canonical conversion family
        canon_type = types[0]
        norm, canon_u = convert_to_canonical(num, unit, canon_type)
        # For multi-type same family (diameter/bait_size both mm) same norm
        results.append(ParsedMeasure(
            attribute_types=types,
            numeric_value=num,
            unit=unit,
            normalized_value=norm,
            canonical_unit=canon_u,
            source_text=m.group(0),
            confidence='unit',
        ))
        occupied.append((m.start(), m.end()))

    # 2) Bare reel sizes
    for m in _BARE_REEL_RE.finditer(raw):
        if _overlaps(m.start(), m.end()):
            continue
        num = _to_decimal(m.group('num'))
        if num is None:
            continue
        # Prefer reel if context or always for known sizes
        if ctx & _REEL_CTX or not (ctx & _LENGTH_CTX):
            norm, canon_u = convert_to_canonical(num, '', 'reel_size')
            results.append(ParsedMeasure(
                attribute_types=('reel_size',),
                numeric_value=num,
                unit='',
                normalized_value=norm,
                canonical_unit=canon_u,
                source_text=m.group(0),
                confidence='reel',
            ))
            occupied.append((m.start(), m.end()))

    # 3) Bare decimals like 3.60 / 3,60 → length (m) or diameter (mm)
    for m in _BARE_DECIMAL_RE.finditer(raw):
        if _overlaps(m.start(), m.end()):
            continue
        num = _to_decimal(m.group('num'))
        if num is None:
            continue
        try:
            v = float(num)
        except Exception:
            continue
        # Line diameter: 0.08–1.5 without unit when context is line
        if 0.05 <= v <= 1.5 and (ctx & _DIAMETER_CTX):
            norm, canon_u = convert_to_canonical(num, 'mm', 'diameter')
            results.append(ParsedMeasure(
                attribute_types=('diameter',),
                numeric_value=num,
                unit='mm',
                normalized_value=norm,
                canonical_unit=canon_u,
                source_text=m.group(0),
                confidence='bare_decimal',
            ))
            occupied.append((m.start(), m.end()))
            continue
        # Rod lengths typically 1.8–5.5 m
        if 1.8 <= v <= 5.5 and (ctx & _LENGTH_CTX or not ctx or ctx & _CASTING_CTX):
            norm, canon_u = convert_to_canonical(num, 'm', 'length')
            results.append(ParsedMeasure(
                attribute_types=('length',),
                numeric_value=num,
                unit='m',
                normalized_value=norm,
                canonical_unit=canon_u,
                source_text=m.group(0),
                confidence='bare_decimal',
            ))
            occupied.append((m.start(), m.end()))

    return results


def strip_measures_from_text(text: str | None) -> str:
    """Remove measure tokens so remaining text can be used for word search."""
    if not text:
        return ''
    out = str(text)
    out = _MEASURE_TOKEN_RE.sub(' ', out)
    # strip bare reels carefully
    out = _BARE_REEL_RE.sub(' ', out)
    out = _BARE_DECIMAL_RE.sub(' ', out)
    out = re.sub(r'\s+', ' ', out).strip()
    return out


def measures_match_q(measures: list[ParsedMeasure]):
    """
    Django Q: product must have attributes matching ALL measures (AND).
    Within one measure, allowed attribute_types are OR.
    Uses Exists to avoid JOIN row multiplication.
    """
    from django.db.models import Exists, OuterRef, Q
    from EcommerceApp.models import ProductAttribute

    if not measures:
        return None

    combined = Q()
    first = True
    for measure in measures:
        attr_q = Q(
            product_id=OuterRef('pk'),
            aktivno=True,
            attribute_type__in=list(measure.attribute_types),
            normalized_numeric_value=measure.normalized_value,
        )
        exists = Exists(ProductAttribute.objects.filter(attr_q))
        if first:
            combined = Q(exists)
            # Exists can't go in Q like that — build differently
            first = False
        # We'll build list of Exists and AND them in apply_search_filter
    return measures  # caller applies Exists AND chain


def attribute_exists_for_measure(measure: ParsedMeasure):
    from django.db.models import Exists, OuterRef
    from EcommerceApp.models import ProductAttribute

    return Exists(
        ProductAttribute.objects.filter(
            product_id=OuterRef('pk'),
            aktivno=True,
            attribute_type__in=list(measure.attribute_types),
            normalized_numeric_value=measure.normalized_value,
        ),
    )


def extract_attributes_from_product_text(
    text: str,
    *,
    context_tokens: set[str] | None = None,
) -> list[dict]:
    """
    Build dicts ready for ProductAttribute.objects.create / update.
    Used by management command — not per request.
    """
    measures = parse_measures_from_text(text, context_tokens=context_tokens)
    rows = []
    seen = set()
    for m in measures:
        for atype in m.attribute_types:
            # Prefer single best type for storage when multiple
            if len(m.attribute_types) > 1:
                # store all ambiguous types only for unit-explicit mm without context
                pass
            key = (atype, str(m.normalized_value), m.canonical_unit)
            if key in seen:
                continue
            seen.add(key)
            # For multi-type inference, store each type so search can match either
            norm, canon_u = convert_to_canonical(m.numeric_value, m.unit, atype)
            rows.append({
                'attribute_type': atype,
                'text_value': m.source_text.strip(),
                'numeric_value': m.numeric_value,
                'unit': m.unit or canon_u,
                'normalized_numeric_value': norm,
                'aktivno': True,
            })
    return rows
