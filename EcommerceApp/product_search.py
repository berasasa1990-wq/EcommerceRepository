"""
Pametna pretraga artikala (ribolovna domena).

Cilj: kupac može pisati sleng / sinonime / s greškama u dijakriticima
(npr. „prut“, „motka“, „štap“, „rola“, „mašina“) i dobiti relevantne
artikle — bez ručnih tagova na svakom proizvodu.

Slojevi:
1) Normalizacija (diakritici, tokeni)
2) Lokalni rječnik sinonima (brzo, uvijek radi)
3) Opcionalno xAI proširenje upita (XAI_API_KEY) — keširano
4) AND po konceptima (više riječi) + OR po sinonimima unutar koncepta
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
import unicodedata
from functools import lru_cache
from typing import Iterable

from django.conf import settings
from django.db.models import Q

logger = logging.getLogger(__name__)

# ─── sinonimi (ribolov BA/HR + engleski katalog) ─────────────────────────────
# Svaka grupa = istoznačnice. Pretraga jednog člana širi na cijelu grupu.
_SYNONYM_GROUPS: tuple[frozenset[str], ...] = (
    # Štapovi
    frozenset({
        'stap', 'stapovi', 'stapom', 'stapa',
        'prut', 'prutovi', 'prutom',
        'motka', 'motke', 'motkom',
        'stapic', 'stapici', 'stapicem',
        'rod', 'rods', 'pole', 'poles', 'blank',
        'canne', 'canna',
    }),
    # Mašinice / role
    frozenset({
        'masinica', 'masinice', 'masinicu', 'masinicom',
        'masina', 'masine',
        'rola', 'role', 'rolu', 'rolom',
        'navijac', 'navijaci', 'navijacem',
        # Napomena: bez "spool" — na ambalaži najlona često piše "single spool"
        'reel', 'reels',
        'baitrunner', 'freespin',
    }),
    # Udice
    frozenset({
        'udica', 'udice', 'udicu', 'udicom',
        'hook', 'hooks', 'hookbait',
        'trokuka', 'trokuke', 'trohook', 'treble',
        'jednokuka', 'jednokuke',
    }),
    # Šaran / carp
    frozenset({
        'saran', 'sarana', 'saranski', 'saranske', 'saranskih', 'saransko',
        'carp', 'carpfishing', 'carper', 'cyprinus',
    }),
    # Feeder
    frozenset({
        'feeder', 'fidr', 'fidra', 'method', 'picker', 'quiver',
    }),
    # Predator / spinning
    frozenset({
        'spinning', 'spin', 'varalicarski', 'varalicarenje',
        'predator', 'pike', 'stuka', 'smud', 'som', 'catfish',
        'zander', 'asp',
    }),
    # Varalice
    frozenset({
        'varalica', 'varalice', 'varalicu',
        'lure', 'lures', 'bait', 'baits',
        'vobler', 'vobleri', 'voblet', 'voblete', 'wobbler', 'wobblers',
        'crank', 'crankbait', 'minnow', 'jerk', 'jerkbait',
        'silikon', 'silikonske', 'softbait', 'softbaits', 'twister', 'shad',
        'spiner', 'spinner', 'spinnerbait', 'bljeskalica', 'bljeskalice',
        'kasikarica', 'kasikarice', 'spoon', 'spoons',
        'jig', 'jigovi', 'jighead',
    }),
    # Najlon / struna / floro
    frozenset({
        'najlon', 'najloni', 'line', 'mono', 'monofil',
        'struna', 'strune', 'pletenica', 'pletenice', 'braid', 'braided',
        'fluoro', 'fluorocarbon', 'fc',
    }),
    # Utezi / olovo
    frozenset({
        'olovo', 'olova', 'uteg', 'utezi', 'tezina',
        'lead', 'weight', 'sinker', 'sinkers', 'swivel',
    }),
    # Ostalo
    frozenset({
        'kacket', 'kacketi', 'kapa', 'kape', 'cap', 'caps', 'hat', 'beanie',
    }),
    frozenset({
        'garderoba', 'majica', 'majice', 'hoodie', 'dukserica', 'odjeca',
        'jacket', 'softshell', 'tshirt',
    }),
    frozenset({
        'kutija', 'kutije', 'box', 'boxes', 'tackle', 'torba', 'torbe', 'bag',
    }),
    frozenset({
        'virbla', 'virble', 'kopca', 'kopce', 'snap', 'snaps', 'karabin',
        'swivel', 'link',
    }),
)

# Kratke riječi koje ne širimo kao zaseban koncept (šum)
_STOPWORDS = frozenset({
    'za', 'i', 'u', 'na', 'od', 'do', 'sa', 's', 'a', 'the', 'of', 'or', 'and',
    'cm', 'mm', 'm', 'g', 'kg', 'lb', 'oz', 'ft', 'sec',
})

# Minimalna dužina tokena za samostalno širenje (izuzetak: brojevi / šifre)
_MIN_TOKEN_LEN = 2

_DIACRITIC_MAP = str.maketrans({
    'č': 'c', 'ć': 'c', 'š': 's', 'ž': 'z', 'đ': 'd',
    'Č': 'c', 'Ć': 'c', 'Š': 's', 'Ž': 'z', 'Đ': 'd',
    'ä': 'a', 'ö': 'o', 'ü': 'u', 'ß': 'ss',
})

# Jednostavan in-process keš za LLM proširenja (TTL)
_LLM_CACHE: dict[str, tuple[float, list[str]]] = {}
_LLM_CACHE_TTL = 60 * 60 * 24  # 24h
_LLM_CACHE_MAX = 512


def normalize_text(text: str) -> str:
    """Lowercase + skini dijakritike + sredi razmake."""
    if not text:
        return ''
    text = unicodedata.normalize('NFKC', text).translate(_DIACRITIC_MAP)
    text = text.lower().strip()
    text = re.sub(r'[^\w\s\-\./+]', ' ', text, flags=re.UNICODE)
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def tokenize(query: str) -> list[str]:
    norm = normalize_text(query)
    if not norm:
        return []
    tokens = [t for t in re.split(r'[\s/|,+]+', norm) if t]
    return tokens


@lru_cache(maxsize=1)
def _synonym_index() -> dict[str, frozenset[str]]:
    """token → cijela grupa sinonima (normalizovano)."""
    index: dict[str, frozenset[str]] = {}
    for group in _SYNONYM_GROUPS:
        norm_group = frozenset(normalize_text(t) for t in group if t)
        for term in norm_group:
            # spoji grupe ako se preklapaju (npr. swivel u dvije grupe)
            existing = index.get(term)
            if existing:
                merged = frozenset(existing | norm_group)
                for t in merged:
                    index[t] = merged
            else:
                index[term] = norm_group
    return index


def expand_token(token: str) -> frozenset[str]:
    """Vrati skup termina za jedan token (uključujući sebe)."""
    t = normalize_text(token)
    if not t:
        return frozenset()
    group = _synonym_index().get(t)
    if group:
        return group
    # Prefiks / substring match unutar poznatih sinonima (npr. "masinic")
    if len(t) >= 4:
        hits: set[str] = set()
        for key, g in _synonym_index().items():
            if key.startswith(t) or t.startswith(key) and len(key) >= 4:
                hits |= set(g)
        if hits:
            hits.add(t)
            return frozenset(hits)
    return frozenset({t})


def expand_query_terms(query: str, *, use_llm: bool = True) -> list[str]:
    """
    Svi korisni search termini za OR pretragu (jedinstveni, prioritizovani).
    """
    raw = (query or '').strip()
    if not raw:
        return []

    terms: list[str] = []
    seen: set[str] = set()

    def add(term: str) -> None:
        t = (term or '').strip()
        if not t:
            return
        key = normalize_text(t)
        if not key or key in seen:
            return
        # izbjegni prekratke generičke
        if len(key) < 2:
            return
        seen.add(key)
        terms.append(t if t == key else key)

    # original + normalizovana cijela fraza
    add(raw)
    add(normalize_text(raw))

    tokens = [t for t in tokenize(raw) if t not in _STOPWORDS]
    for tok in tokens:
        add(tok)
        for syn in expand_token(tok):
            add(syn)

    if use_llm:
        for extra in _llm_expand_terms(raw):
            add(extra)
            for syn in expand_token(extra):
                add(syn)

    return terms


def concept_groups_for_query(query: str, *, use_llm: bool = True) -> list[frozenset[str]]:
    """
    Koncepti za AND logiku: svaki značajan token → skup sinonima.
    """
    tokens = [t for t in tokenize(query) if t not in _STOPWORDS and len(t) >= _MIN_TOKEN_LEN]
    groups: list[frozenset[str]] = []
    for tok in tokens:
        g = expand_token(tok)
        if g:
            groups.append(g)

    if use_llm:
        # LLM termini koji nisu već pokriveni dodaj kao meki OR (ne novi AND)
        pass

    return groups


def _field_match_q(term: str) -> Q:
    """Jedan termin kroz sva polja artikla / kategorije / brenda / tagova."""
    return (
        Q(naziv__icontains=term)
        | Q(sifra__icontains=term)
        | Q(barkod__icontains=term)
        | Q(opis__icontains=term)
        | Q(tagovi__naziv__icontains=term)
        | Q(varijacije__sifra__icontains=term)
        | Q(varijacije__naziv__icontains=term)
        | Q(kategorija__naziv__icontains=term)
        | Q(kategorija__slug__icontains=term)
        | Q(kategorija__roditelj__naziv__icontains=term)
        | Q(kategorija__roditelj__slug__icontains=term)
        | Q(brend__naziv__icontains=term)
    )


def term_matches_text(term: str, text: str) -> bool:
    """
    Soft word-boundary match.
    - 'carp' ne pogađa 'carpologija'
    - 'stap' pogađa 'stap' i 'stapovi' (kratki nastavak)
    """
    term_n = normalize_text(term)
    text_n = normalize_text(text)
    if not term_n or not text_n:
        return False
    if term_n == text_n or f' {term_n} ' in f' {text_n} ':
        return True
    words = re.findall(r'[a-z0-9]+', text_n)
    for w in words:
        if w == term_n:
            return True
        # stem → duži oblik (stap → stapovi / stapom), max +5 slova
        if len(term_n) >= 3 and w.startswith(term_n) and len(w) <= len(term_n) + 5:
            return True
        if len(w) >= 3 and term_n.startswith(w) and len(term_n) <= len(w) + 5:
            return True
    return False


def _product_search_blob(product) -> str:
    """Spojeni tekst za Python refine (naziv, kat, brend, šifra…)."""
    parts = [
        getattr(product, 'naziv', '') or '',
        getattr(product, 'sifra', '') or '',
        getattr(product, 'barkod', '') or '',
        getattr(product, 'opis', '') or '',
    ]
    cat = getattr(product, 'kategorija', None)
    if cat is not None:
        parts.append(getattr(cat, 'naziv', '') or '')
        parts.append(getattr(cat, 'slug', '') or '')
        parent = getattr(cat, 'roditelj', None)
        if parent is not None:
            parts.append(getattr(parent, 'naziv', '') or '')
    brand = getattr(product, 'brend', None)
    if brand is not None:
        parts.append(getattr(brand, 'naziv', '') or '')
    # tagovi mogu biti pred-fetchani; ignoriši ako ne
    try:
        tag_manager = getattr(product, 'tagovi', None)
        if tag_manager is not None and hasattr(tag_manager, 'all'):
            parts.extend(t.naziv for t in tag_manager.all()[:12] if getattr(t, 'naziv', None))
    except Exception:
        pass
    return ' '.join(parts)


def product_matches_groups(product, groups: list[frozenset[str]]) -> bool:
    """Svaka grupa (koncept) mora imati bar jedan termin u blob-u artikla."""
    if not groups:
        return True
    blob = _product_search_blob(product)
    for group in groups:
        if not any(term_matches_text(term, blob) for term in group):
            return False
    return True


def build_search_q(query: str, *, use_llm: bool | None = None) -> Q:
    """
    Django Q za pametnu pretragu.

    - Šifra / barkod (cijeli upit) → direktan match
    - Više riječi → AND po konceptima (svaki token mora pogoditi nešto),
      unutar koncepta OR sinonima
    - Jedna riječ → OR svih sinonima
    """
    raw = (query or '').strip()
    if not raw:
        return Q()

    if use_llm is None:
        use_llm = bool(getattr(settings, 'PRODUCT_SEARCH_AI_ENABLED', True))

    # Direktna šifra / barkod (bez razmaka, alfanumerički)
    compact = re.sub(r'\s+', '', raw)
    if re.fullmatch(r'[A-Za-z0-9\-_/]{3,}', compact) and ' ' not in raw.strip():
        code_q = (
            Q(sifra__iexact=raw)
            | Q(sifra__icontains=raw)
            | Q(barkod__iexact=raw)
            | Q(barkod__icontains=raw)
            | Q(varijacije__sifra__icontains=raw)
            | Q(naziv__icontains=raw)
        )
        # i dalje proširi sinonimima ako je riječ (npr. "feeder")
        if not re.fullmatch(r'\d+', compact):
            pass
        else:
            return code_q

    groups = concept_groups_for_query(raw, use_llm=False)
    all_terms = expand_query_terms(raw, use_llm=use_llm)

    if not groups and not all_terms:
        return Q(pk__in=[])  # ništa

    # Multi-concept AND: "stap saranski" → rod-ish AND carp-ish
    and_q = Q()
    if len(groups) >= 2:
        and_q = Q()
        for group in groups:
            token_q = Q()
            # ograniči broj sinonima po grupi radi SQL veličine
            for term in sorted(group, key=len)[:14]:
                token_q |= _field_match_q(term)
            and_q &= token_q
    elif len(groups) == 1:
        token_q = Q()
        for term in sorted(groups[0], key=len)[:16]:
            token_q |= _field_match_q(term)
        and_q = token_q
    else:
        and_q = Q()

    # Široki OR svih termina (pomaže LLM + fraza + šifra)
    or_q = Q()
    for term in all_terms[:40]:
        or_q |= _field_match_q(term)

    # original cijeli upit
    or_q |= _field_match_q(raw)
    norm = normalize_text(raw)
    if norm and norm != raw.lower():
        or_q |= _field_match_q(norm)

    if len(groups) >= 2:
        # stroži AND + fallback OR (ako AND ne da ništa, view može proširiti —
        # ovdje spajamo: preferiramo AND, ali OR na pune fraze/šifre ostaje)
        # Zapravo: za multi-token koristi AND; OR freestyle može previše proširiti
        # (npr. "carp" sam u "Carpologija" box). Držimo AND kao primarni.
        return and_q | (
            Q(sifra__icontains=raw)
            | Q(barkod__icontains=raw)
            | Q(naziv__icontains=raw)
        )

    return and_q | or_q if and_q else or_q


def relevance_score(product, query: str) -> int:
    """Veći = bolje. Koristi se za sort unutar već filtriranih rezultata."""
    q_raw = (query or '').strip()
    if not q_raw:
        return 0

    score = 0
    name = normalize_text(getattr(product, 'naziv', '') or '')
    sifra = normalize_text(getattr(product, 'sifra', '') or '')
    barkod = normalize_text(getattr(product, 'barkod', '') or '')
    blob = _product_search_blob(product)
    q_norm = normalize_text(q_raw)
    tokens = [t for t in tokenize(q_raw) if t not in _STOPWORDS]

    if sifra and (sifra == q_norm or term_matches_text(q_norm, sifra)):
        score += 140
    if barkod and barkod == q_norm:
        score += 130
    if name == q_norm:
        score += 110
    elif name.startswith(q_norm):
        score += 85
    elif q_norm and term_matches_text(q_norm, name):
        score += 55

    groups = concept_groups_for_query(q_raw, use_llm=False)
    for group in groups:
        if any(term_matches_text(term, name) for term in group if len(term) >= 3):
            score += 22
        elif any(term_matches_text(term, blob) for term in group if len(term) >= 3):
            score += 12
        else:
            score -= 8

    for tok in tokens:
        expanded = expand_token(tok)
        if any(term_matches_text(t, name) for t in expanded if len(t) >= 2):
            score += 16
        if sifra and any(term_matches_text(t, sifra) for t in expanded if len(t) >= 2):
            score += 20

    cat = getattr(product, 'kategorija', None)
    if cat is not None:
        cat_name = getattr(cat, 'naziv', '') or ''
        parent = getattr(cat, 'roditelj', None)
        parent_name = getattr(parent, 'naziv', '') or '' if parent else ''
        for group in groups:
            if any(
                term_matches_text(term, cat_name) or term_matches_text(term, parent_name)
                for term in group if len(term) >= 3
            ):
                score += 32
                break

    brand = getattr(product, 'brend', None)
    if brand is not None:
        brand_name = getattr(brand, 'naziv', '') or ''
        if brand_name and term_matches_text(q_norm, brand_name):
            score += 25

    return score


# ─── opcioni xAI sloj ───────────────────────────────────────────────────────

def _llm_cache_get(key: str) -> list[str] | None:
    item = _LLM_CACHE.get(key)
    if not item:
        return None
    ts, value = item
    if time.time() - ts > _LLM_CACHE_TTL:
        _LLM_CACHE.pop(key, None)
        return None
    return value


def _llm_cache_set(key: str, value: list[str]) -> None:
    if len(_LLM_CACHE) >= _LLM_CACHE_MAX:
        # izbaci najstarije
        oldest = sorted(_LLM_CACHE.items(), key=lambda kv: kv[1][0])[:64]
        for k, _ in oldest:
            _LLM_CACHE.pop(k, None)
    _LLM_CACHE[key] = (time.time(), value)


def _llm_expand_terms(query: str) -> list[str]:
    """
    xAI (SpaceXAI) proširenje upita u dodatne search riječi.
    Ako nema ključa ili padne mreža — tiho vrati [].
    """
    if not getattr(settings, 'PRODUCT_SEARCH_AI_ENABLED', True):
        return []
    api_key = (getattr(settings, 'XAI_API_KEY', None) or '').strip()
    if not api_key:
        return []

    q = (query or '').strip()
    if len(q) < 2 or len(q) > 120:
        return []

    cache_key = hashlib.sha1(q.lower().encode('utf-8')).hexdigest()
    cached = _llm_cache_get(cache_key)
    if cached is not None:
        return cached

    model = getattr(settings, 'XAI_SEARCH_MODEL', None) or getattr(
        settings, 'XAI_MODEL', 'grok-4-1-fast-non-reasoning',
    )
    base_url = (getattr(settings, 'XAI_API_BASE', None) or 'https://api.x.ai/v1').rstrip('/')

    system = (
        'Ti si asistent za pretragu web shopa ribolovne opreme (BiH/HR). '
        'Za korisnički upit vrati JSON objekat {"terms": ["..."]} sa 4-12 kratkih '
        'search termina (sinonimi, engleski katalog termini, tip opreme, riba). '
        'Bez objašnjenja. Primjeri: prut→stap,rod; rola→masinica,reel; saran→carp; '
        'udica→hook. Ne izmišljaj brendove koji nisu u upitu.'
    )
    user = f'Upit: {q}'

    try:
        import requests

        resp = requests.post(
            f'{base_url}/chat/completions',
            headers={
                'Authorization': f'Bearer {api_key}',
                'Content-Type': 'application/json',
            },
            json={
                'model': model,
                'temperature': 0,
                'max_tokens': 200,
                'messages': [
                    {'role': 'system', 'content': system},
                    {'role': 'user', 'content': user},
                ],
                'response_format': {'type': 'json_object'},
            },
            timeout=float(getattr(settings, 'XAI_SEARCH_TIMEOUT', 2.5)),
        )
        if resp.status_code >= 400:
            logger.warning('product_search LLM expand HTTP %s', resp.status_code)
            _llm_cache_set(cache_key, [])
            return []
        data = resp.json()
        content = (
            (data.get('choices') or [{}])[0]
            .get('message', {})
            .get('content')
            or ''
        )
        terms = _parse_llm_terms(content)
        _llm_cache_set(cache_key, terms)
        return terms
    except Exception as exc:
        logger.info('product_search LLM expand skip: %s', exc)
        _llm_cache_set(cache_key, [])
        return []


def _parse_llm_terms(content: str) -> list[str]:
    if not content:
        return []
    text = content.strip()
    # izvuci JSON ako model doda prose
    if '{' in text:
        try:
            start = text.index('{')
            end = text.rindex('}') + 1
            obj = json.loads(text[start:end])
            raw_terms = obj.get('terms') or obj.get('keywords') or obj.get('synonyms') or []
            if isinstance(raw_terms, str):
                raw_terms = re.split(r'[,;|/]+', raw_terms)
            out: list[str] = []
            for t in raw_terms:
                if isinstance(t, str) and t.strip():
                    out.append(t.strip()[:40])
            return out[:12]
        except (json.JSONDecodeError, ValueError, TypeError):
            pass
    # fallback: zarezom odvojeno
    parts = re.split(r'[,;\n]+', text)
    return [p.strip()[:40] for p in parts if p.strip()][:12]


def apply_search(products_qs, query: str, *, use_llm: bool | None = None):
    """
    Primijeni pametnu pretragu na queryset artikala.

    SQL daje kandidate (brzo), zatim Python refine po soft word-boundary
    da 'carp' ne vuče 'Carpologija' kutije, a 'stap' i dalje vuče štapove.
    """
    raw = (query or '').strip()
    if not raw:
        return products_qs

    q_obj = build_search_q(raw, use_llm=use_llm)
    candidates = products_qs.filter(q_obj).distinct()

    groups = concept_groups_for_query(raw, use_llm=False)
    # Šifra/barkod upit — bez refine-a
    compact = re.sub(r'\s+', '', raw)
    if re.fullmatch(r'\d{4,}', compact) or (
        re.fullmatch(r'[A-Za-z]{1,6}\d{2,}', compact) and ' ' not in raw
    ):
        return candidates

    if not groups:
        return candidates

    # Refine: zadrži samo artikle koji zaista pokrivaju sve koncepte
    # (ograniči evaluaciju da ne vuče cijeli katalog u memoriju)
    ids: list[int] = []
    qs = candidates.select_related('kategorija', 'kategorija__roditelj', 'brend')
    # prefetch tagova samo ako treba — izbjegni N+1 za male setove
    try:
        from django.db.models import Prefetch
        qs = qs.prefetch_related('tagovi')
    except Exception:
        pass

    for product in qs[:800]:
        if product_matches_groups(product, groups):
            ids.append(product.pk)

    if not ids:
        # fallback: vrati SQL kandidate (bolje nešto nego prazno)
        return candidates

    # Zadrži originalni QS filter po id-jevima (paginacija / distinct)
    return products_qs.filter(pk__in=ids).distinct()
