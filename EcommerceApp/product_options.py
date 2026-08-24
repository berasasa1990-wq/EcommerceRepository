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
_CLOTHING_SIZE = re.compile(r'^\d{1,3}$')


def fold_product_name(value):
    text = unicodedata.normalize('NFKD', value or '')
    text = ''.join(ch for ch in text if not unicodedata.combining(ch))
    text = _NON_ALNUM.sub(' ', text.casefold())
    return _MULTI_SPACE.sub(' ', text).strip()


def name_similarity(left, right):
    folded_left = fold_product_name(left)
    folded_right = fold_product_name(right)
    if not folded_left or not folded_right:
        return 0.0
    if folded_left == folded_right:
        return 1.0
    return SequenceMatcher(None, folded_left, folded_right).ratio()


def _is_variant_token(token):
    return token in _VARIANT_TOKENS or bool(_CLOTHING_SIZE.fullmatch(token))


def _core_tokens(name):
    return [
        token
        for token in fold_product_name(name).split()
        if not _is_variant_token(token)
    ]


def names_are_similar(left, right, threshold=SIMILAR_NAME_THRESHOLD):
    """True ako je ≥90% istog teksta, ili ista jezgra uz razliku boje/veličine."""
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
