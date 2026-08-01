"""
Rule-based search intent (no paid AI).

Matches query against admin-configured SearchIntentRule trigger phrases.
Recommendations are returned for a SEPARATE UI section and must never
be merged into the main product ranking queryset.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from django.core.cache import cache

from .normalize import normalize_search_text, sanitize_search_query, tokenize_search_query

CACHE_KEY = 'ecommerce_search_intent_rules_v1'
CACHE_TTL = 60 * 60 * 6  # 6h; primary invalidation on save/delete
MAX_INTENT_PRODUCTS = 8
MAX_INTENT_CATEGORIES = 8
MAX_INTENT_TAGS = 8
MAX_INTENT_BRANDS = 6
MAX_MATCHED_RULES = 3


def invalidate_intent_cache() -> None:
    cache.delete(CACHE_KEY)


def _load_rules_from_db() -> list[dict]:
    """Lightweight serializable rule snapshots for matching."""
    from EcommerceApp.models import SearchIntentRule

    rules = (
        SearchIntentRule.objects.filter(aktivno=True)
        .order_by('-prioritet', 'id')
        .prefetch_related(
            'povezane_kategorije',
            'povezani_tagovi',
            'povezani_brendovi',
            'povezani_proizvodi',
        )
    )
    out = []
    for rule in rules:
        triggers = []
        for phrase in rule.trigger_list():
            norm = normalize_search_text(phrase)
            if norm:
                triggers.append(norm)
        if not triggers:
            continue
        out.append({
            'id': rule.pk,
            'naziv': rule.naziv,
            'triggers': triggers,
            'prioritet': rule.prioritet,
            'naslov_preporuke': rule.naslov_preporuke or rule.naziv,
            'objasnjenje': rule.objasnjenje or '',
            'category_ids': list(
                rule.povezane_kategorije.filter(aktivan=True).values_list('id', flat=True),
            ),
            'tag_ids': list(rule.povezani_tagovi.values_list('id', flat=True)),
            'brand_ids': list(rule.povezani_brendovi.values_list('id', flat=True)),
            'product_ids': list(
                rule.povezani_proizvodi.filter(aktivan=True).values_list('id', flat=True),
            ),
        })
    return out


def get_cached_intent_rules() -> list[dict]:
    data = cache.get(CACHE_KEY)
    if data is not None:
        return data
    try:
        data = _load_rules_from_db()
    except Exception:
        data = []
    cache.set(CACHE_KEY, data, CACHE_TTL)
    return data


def _phrase_matches(query_fold: str, query_tokens: set[str], trigger_fold: str) -> bool:
    """
    Match strategies (all on normalized text):
    - exact equality
    - trigger is substring of query (or vice versa for multi-word)
    - all trigger tokens appear in query tokens
    """
    if not query_fold or not trigger_fold:
        return False
    if query_fold == trigger_fold:
        return True
    if trigger_fold in query_fold or query_fold in trigger_fold:
        return True
    t_tokens = [t for t in trigger_fold.split() if t]
    if len(t_tokens) >= 2:
        return all(t in query_tokens or t in query_fold for t in t_tokens)
    # single token: whole-word style
    if len(trigger_fold) >= 3 and trigger_fold in query_tokens:
        return True
    return False


def match_intent_rules(query: str, *, limit: int = MAX_MATCHED_RULES) -> list[dict]:
    """Return matched rule snapshots ordered by priority (highest first)."""
    raw = sanitize_search_query(query)
    if not raw:
        return []
    q_fold = normalize_search_text(raw)
    q_tokens = set(tokenize_search_query(raw))
    # also add full fold tokens
    for t in q_fold.split():
        if t:
            q_tokens.add(t)

    matched = []
    for rule in get_cached_intent_rules():
        for trig in rule['triggers']:
            if _phrase_matches(q_fold, q_tokens, trig):
                matched.append(rule)
                break
        if len(matched) >= limit:
            break
    return matched


@dataclass
class IntentRecommendation:
    """Payload for template — separate from main product list."""
    rules: list[dict] = field(default_factory=list)
    categories: list = field(default_factory=list)
    tags: list = field(default_factory=list)
    brands: list = field(default_factory=list)
    products: list = field(default_factory=list)
    titles: list[str] = field(default_factory=list)
    explanations: list[str] = field(default_factory=list)

    @property
    def has_content(self) -> bool:
        return bool(
            self.categories or self.tags or self.brands or self.products,
        )


def resolve_intent_recommendations(
    query: str,
    *,
    exclude_product_ids: set[int] | None = None,
    in_stock_only: bool = True,
) -> IntentRecommendation:
    """
    Load full model instances for matched intent rules.

    exclude_product_ids: main search hit IDs — related products already in main
    results are skipped so the side section stays complementary.
    """
    from EcommerceApp.models import Brand, Category, Product, Tag

    rec = IntentRecommendation()
    rules = match_intent_rules(query)
    if not rules:
        return rec

    rec.rules = rules
    exclude = set(exclude_product_ids or ())

    cat_ids, tag_ids, brand_ids, product_ids = [], [], [], []
    seen_c, seen_t, seen_b, seen_p = set(), set(), set(), set()

    for rule in rules:
        if rule.get('naslov_preporuke'):
            rec.titles.append(rule['naslov_preporuke'])
        if rule.get('objasnjenje'):
            rec.explanations.append(rule['objasnjenje'])
        for cid in rule.get('category_ids') or []:
            if cid not in seen_c:
                seen_c.add(cid)
                cat_ids.append(cid)
        for tid in rule.get('tag_ids') or []:
            if tid not in seen_t:
                seen_t.add(tid)
                tag_ids.append(tid)
        for bid in rule.get('brand_ids') or []:
            if bid not in seen_b:
                seen_b.add(bid)
                brand_ids.append(bid)
        for pid in rule.get('product_ids') or []:
            if pid in exclude or pid in seen_p:
                continue
            seen_p.add(pid)
            product_ids.append(pid)

    if cat_ids:
        cats = {
            c.pk: c
            for c in Category.objects.filter(pk__in=cat_ids, aktivan=True)
            .select_related('roditelj')
        }
        rec.categories = [cats[i] for i in cat_ids if i in cats][:MAX_INTENT_CATEGORIES]

    if tag_ids:
        tags = {t.pk: t for t in Tag.objects.filter(pk__in=tag_ids)}
        rec.tags = [tags[i] for i in tag_ids if i in tags][:MAX_INTENT_TAGS]

    if brand_ids:
        brands = {b.pk: b for b in Brand.objects.filter(pk__in=brand_ids)}
        rec.brands = [brands[i] for i in brand_ids if i in brands][:MAX_INTENT_BRANDS]

    if product_ids:
        qs = Product.objects.filter(pk__in=product_ids, aktivan=True)
        if in_stock_only:
            qs = qs.filter(na_stanju=True)
        qs = qs.select_related('brend', 'kategorija').prefetch_related('varijacije')
        by_id = {p.pk: p for p in qs}
        rec.products = [by_id[i] for i in product_ids if i in by_id][:MAX_INTENT_PRODUCTS]

    return rec


def seed_default_intent_rules() -> dict:
    """Optional seed for common fishing intents (idempotent by naziv)."""
    from EcommerceApp.models import SearchIntentRule

    defaults = [
        {
            'naziv': 'Som — oprema',
            'trigger_phrases': 'som\ncatfish\nsomovski',
            'naslov_preporuke': 'Za lov na soma preporučujemo',
            'objasnjenje': (
                'Namjera: oprema za soma. Ispod su grupe artikala koje ribolovci '
                'često koriste zajedno — odvojeno od direktnih rezultata pretrage.'
            ),
            'prioritet': 100,
        },
        {
            'naziv': 'Početnički feeder set',
            'trigger_phrases': (
                'početnički feeder set\npocetnicki feeder set\n'
                'feeder set\nfeeder za početnike\nfeeder za pocetnike'
            ),
            'naslov_preporuke': 'Početnički feeder — što još treba',
            'objasnjenje': (
                'Namjera: feeder set za početnike. Preporuke su grupe (setovi, štapovi, '
                'mašinice, hranilice, pribor), ne zamjena za tačne rezultate upita.'
            ),
            'prioritet': 95,
        },
        {
            'naziv': 'Štap za Savu / riječni',
            'trigger_phrases': (
                'štap za savu\nstap za savu\nza savu\nriječni ribolov\n'
                'rijecni ribolov\nmrena\nza mrenu'
            ),
            'naslov_preporuke': 'Riječni ribolov — preporuke',
            'objasnjenje': (
                'Namjera: jači štapovi / riječni setup (npr. Sava). Ovo su preporuke, '
                'ne potpuno poklapanje sa svakim artiklom u rezultatima.'
            ),
            'prioritet': 90,
        },
    ]
    created = 0
    for data in defaults:
        obj, was = SearchIntentRule.objects.get_or_create(
            naziv=data['naziv'],
            defaults={
                'trigger_phrases': data['trigger_phrases'],
                'naslov_preporuke': data['naslov_preporuke'],
                'objasnjenje': data['objasnjenje'],
                'prioritet': data['prioritet'],
                'aktivno': True,
            },
        )
        if was:
            created += 1
    invalidate_intent_cache()
    return {
        'created': created,
        'total': SearchIntentRule.objects.count(),
    }
