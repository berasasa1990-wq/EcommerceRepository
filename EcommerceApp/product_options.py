"""Druge opcije artikla — slični nazivi (boja, veličina kao zasebni SKU)."""

from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher

SIMILAR_NAME_THRESHOLD = 0.90
SIMILAR_NAME_LIMIT = 24
_CANDIDATE_CAP = 400

# Boje / konfekcijske veličine na kraju naziva — nisu identitet modela.
_VARIANT_TOKENS = frozenset({
    'xs', 's', 'm', 'l', 'xl', 'xxl', 'xxxl', '2xl', '3xl',
    'crvena', 'crveni', 'crveno',
    'plava', 'plavi', 'plavo',
    'crna', 'crni', 'crno',
    'bijela', 'bijeli', 'bijelo',
    'bela', 'beli', 'belo',
    'zelena', 'zeleni', 'zeleno',
    'zuta', 'zuti', 'zuto',
    'siva', 'sivi', 'sivo',
    'srebrna', 'srebrni', 'srebrno',
    'zlatna', 'zlatni', 'zlatno',
    'narandzasta', 'narandzasti', 'narandzasto',
    'ljubicasta', 'ljubicasti', 'ljubicasto',
    'roza', 'pink', 'braon', 'bez', 'teget', 'krem',
    'maslinasta', 'maslinasti', 'maslinasto',
    'tirkizna', 'tirkizni', 'tirkizno',
    'red', 'blue', 'black', 'white', 'green', 'yellow',
    'grey', 'gray', 'gold', 'silver', 'orange',
})

_NON_ALNUM = re.compile(r'[^a-z0-9]+')
_MULTI_SPACE = re.compile(r'\s+')
_DIGITS = re.compile(r'\d+')
_CLOTHING_SIZE = re.compile(r'^\d+$')
# Interna šifra u nazivu (npr. MT12345 / MT-12345) — ne ulazi u podudaranje.
_MT_CODE = re.compile(r'\bmt[\s\-]*\d+\b')
_MT_TOKEN = re.compile(r'^mt\d+$')
_MT_STRIP = re.compile(r'[\s\-]+')


def extract_mt_codes(*texts):
    """Šifre iz teksta koje počinju sa MT pa brojevi, npr. MT12122 / MT-12122."""
    codes = []
    seen = set()
    for raw in texts:
        text = unicodedata.normalize('NFKD', raw or '')
        text = ''.join(ch for ch in text if not unicodedata.combining(ch)).casefold()
        for match in _MT_CODE.finditer(text):
            code = _MT_STRIP.sub('', match.group(0)).upper()
            if code and code not in seen:
                seen.add(code)
                codes.append(code)
    return codes


def duplicate_mt_name_groups(products=None):
    """Artikli čiji naziv dijeli istu MT+broj šifru s barem još jednim artiklom."""
    from collections import defaultdict

    from .models import Product

    groups = defaultdict(list)
    rows = products
    if rows is None:
        rows = Product.objects.all().only('pk', 'naziv')
    for product in rows:
        for code in extract_mt_codes(getattr(product, 'naziv', None)):
            groups[code].append(product)
    return {
        code: members
        for code, members in groups.items()
        if len(members) >= 2
    }


def duplicate_mt_name_product_ids():
    ids = []
    for members in duplicate_mt_name_groups().values():
        ids.extend(product.pk for product in members)
    return set(ids)
# Mjere i jedinice koje se zanemaruju u nazivu.
_MEASURE_TOKENS = frozenset({'cm', 'mm', 'm', 'g', 'kg', 'ml', 'l'})


def fold_product_name(value):
    text = unicodedata.normalize('NFKD', value or '')
    text = ''.join(ch for ch in text if not unicodedata.combining(ch))
    text = text.casefold()
    text = _MT_CODE.sub(' ', text)
    text = _DIGITS.sub(' ', text)
    text = _NON_ALNUM.sub(' ', text)
    tokens = [
        token
        for token in _MULTI_SPACE.sub(' ', text).split()
        if token and token not in _MEASURE_TOKENS and token != 'mt'
    ]
    return ' '.join(tokens)


def name_similarity(left, right):
    folded_left = fold_product_name(left)
    folded_right = fold_product_name(right)
    if not folded_left or not folded_right:
        return 0.0
    if folded_left == folded_right:
        return 1.0
    return SequenceMatcher(None, folded_left, folded_right).ratio()


def _is_variant_token(token):
    return (
        token in _VARIANT_TOKENS
        or bool(_CLOTHING_SIZE.fullmatch(token))
        or bool(_MT_TOKEN.fullmatch(token))
        or token == 'mt'
    )


def _core_tokens(name):
    return [
        token
        for token in fold_product_name(name).split()
        if not _is_variant_token(token)
    ]


def names_are_similar(left, right, threshold=SIMILAR_NAME_THRESHOLD):
    """True ako je ≥90% istog teksta (bez MT šifre, brojeva, cm/g/'/\"), ili ista jezgra."""
    folded_left = fold_product_name(left)
    folded_right = fold_product_name(right)
    if not folded_left or not folded_right:
        return False
    ratio = name_similarity(left, right)
    if ratio >= threshold:
        return True
    core_left = _core_tokens(left)
    core_right = _core_tokens(right)
    if not core_left or not core_right:
        return False
    if core_left != core_right:
        return False
    # Jezgra jednaka nakon skidanja boje/veličine, i bar jedan naziv je imao varijantu.
    left_had_variant = fold_product_name(left).split() != core_left
    right_had_variant = fold_product_name(right).split() != core_right
    return left_had_variant or right_had_variant


def _candidate_tokens(name):
    return [token for token in _core_tokens(name) if len(token) >= 3]


def find_similar_name_products(product, queryset, *, threshold=SIMILAR_NAME_THRESHOLD, limit=SIMILAR_NAME_LIMIT):
    """Ostali artikli čiji je naziv dovoljno sličan (zasebni SKU za boju/veličinu)."""
    naziv = (getattr(product, 'naziv', None) or '').strip()
    if not naziv:
        return []

    tokens = _candidate_tokens(naziv)
    if not tokens:
        return []

    candidates = queryset.exclude(pk=product.pk)
    if getattr(product, 'kategorija_id', None):
        candidates = candidates.filter(kategorija_id=product.kategorija_id)

    filtered = candidates.filter(naziv__icontains=tokens[0])
    if len(tokens) >= 2:
        filtered = filtered.filter(naziv__icontains=tokens[1])

    rows = list(filtered.values_list('pk', 'naziv')[:_CANDIDATE_CAP])
    scored = []
    for pk, other_name in rows:
        if not names_are_similar(naziv, other_name, threshold=threshold):
            continue
        scored.append((name_similarity(naziv, other_name), pk))
    if not scored:
        return []

    scored.sort(key=lambda item: (-item[0], item[1]))
    keep_ids = [pk for _score, pk in scored[:limit]]
    by_id = {item.pk: item for item in queryset.filter(pk__in=keep_ids)}
    return [by_id[pk] for pk in keep_ids if pk in by_id]
