"""Worker-local candidate index; prices/stock are never cached here.

Read only IDs, names, codes and explicit tag links on rebuild, not Product objects.
Signals invalidate ordinary edits; a 60-second refresh also covers bulk SQL imports
and workers using independent local-memory caches.
"""
import re
import threading
import time
import uuid
from collections import defaultdict, OrderedDict

from django.core.cache import cache
from django.db import models, connection, transaction
from .search_expressions import fold, compact

_LOCK = threading.RLock()
_SNAPSHOT = None
_BUILT_AT = 0
_GENERATION = None
_TRANSACTION = None
_KEY = 'storefront-search-index-generation-v1'
_INDEX_FIELDS = {'naziv', 'sifra', 'aktivan', 'artikal', 'artikal_id'}


def invalidate_search_index(*args, **kwargs):
    global _SNAPSHOT
    with _LOCK:
        _SNAPSHOT = None
        cache.set(_KEY, uuid.uuid4().hex, timeout=None)


def search_index_edited(sender=None, using='default', **kwargs):
    fields = kwargs.get('update_fields')
    if fields is not None and not _INDEX_FIELDS & set(fields):
        return
    invalidate_search_index()
    transaction.on_commit(invalidate_search_index, using=using)


class SearchIndexQuerySet(models.QuerySet):
    def _invalidate(self):
        invalidate_search_index()
        transaction.on_commit(invalidate_search_index, using=self.db)

    def update(self, **kwargs):
        count = super().update(**kwargs)
        if _INDEX_FIELDS & kwargs.keys():
            self._invalidate()
        return count

    def bulk_create(self, objs, *args, **kwargs):
        result = super().bulk_create(objs, *args, **kwargs)
        self._invalidate()
        return result

    def bulk_update(self, objs, fields, *args, **kwargs):
        result = super().bulk_update(objs, fields, *args, **kwargs)
        if _INDEX_FIELDS & set(fields):
            self._invalidate()
        return result


def _grams(text):
    return {text[i:i + 2] for i in range(len(text) - 1)}


def _add(postings, text, pk):
    for gram in _grams(text):
        postings[gram].add(pk)


def _find(postings, text):
    groups = [postings.get(g, set()) for g in _grams(text)]
    if not groups or any(not g for g in groups):
        return set()
    groups.sort(key=len)
    result = set(groups[0])
    for group in groups[1:]:
        result.intersection_update(group)
    return result


class CandidateIndex:
    def __init__(self):
        from .models import Product, ProductVariation, Tag
        from .views import _search_tokens
        self.names = {}
        self.codes = defaultdict(set)
        self.name_grams = defaultdict(set)
        self.code_grams = defaultdict(set)
        self.compact_name_grams = defaultdict(set)
        self.words = defaultdict(set)
        self.word_prefixes = defaultdict(set)
        self.results = OrderedDict()
        for pk, name, code in Product.objects.filter(aktivan=True).order_by().values_list('pk', 'naziv', 'sifra').iterator():
            name = fold(name)
            self.names[pk] = name
            _add(self.name_grams, name, pk)
            _add(self.compact_name_grams, compact(name), pk)
            if code:
                self.codes[pk].add(compact(code))
            for word in _search_tokens(name):
                self.words[word].add(pk)
                self.word_prefixes[word[:2]].add(word)
        for pk, code in ProductVariation.objects.filter(artikal__aktivan=True).exclude(sifra='').order_by().values_list('artikal_id', 'sifra').iterator():
            if code:
                self.codes[pk].add(compact(code))
        for pk, codes in self.codes.items():
            for code in codes:
                _add(self.code_grams, code, pk)
        self.tags = dict(Tag.objects.order_by().values_list('pk', 'naziv'))
        self.tag_products = defaultdict(set)
        for product_id, tag_id in Product.tagovi.through.objects.order_by().values_list('product_id', 'tag_id').iterator():
            if product_id in self.names:
                self.tag_products[tag_id].add(product_id)

    def match(self, query):
        from .views import _search_tokens, _normalize_phrase, _word_fuzzy_eq, _tag_matches_query, _tag_matches_category_search_tag
        query = fold(_normalize_phrase(query))
        if query in self.results:
            return self.results[query]
        words = _search_tokens(query)
        key = compact(query)
        scores = {}
        candidates = None
        for word in words:
            ids = _find(self.name_grams, word)
            if word.isdigit():
                pattern = re.compile(r'(?<![0-9])' + re.escape(word) + r'(?![0-9])')
                ids = {pk for pk in ids if pattern.search(self.names[pk])}
            else:
                ids = {pk for pk in ids if word in self.names[pk]}
            candidates = ids if candidates is None else candidates & ids
        for pk in candidates or ():
            scores[pk] = 150 if self.names[pk] == query else 140
        for pk in _find(self.code_grams, key):
            codes = self.codes[pk]
            if key in codes:
                scores[pk] = 200
            elif any(key in code for code in codes):
                scores[pk] = max(scores.get(pk, 0), 120)
        if any(c.isalpha() for c in key) and any(c.isdigit() for c in key):
            pattern = re.compile(r'(?<![a-z0-9])' + r'[-\s]*'.join(re.findall(r'[a-z]+|[0-9]+', key)) + r'(?![a-z0-9])')
            for pk in _find(self.compact_name_grams, key):
                if pattern.search(self.names[pk]):
                    scores[pk] = max(scores.get(pk, 0), 190)
        fuzzy = set()
        if not scores and words and len(words) <= 10:
            possible = None
            for word in words:
                matching = [w for w in self.words if word in w]
                if not matching and len(word) >= 4:
                    matching = [w for w in self.word_prefixes.get(word[:2], ()) if _word_fuzzy_eq(word, w)]
                ids = set()
                for token in matching:
                    ids.update(self.words[token])
                possible = ids if possible is None else possible & ids
                if not possible:
                    break
            fuzzy = set(sorted(possible or ())[:300])
            scores.update((pk, 110) for pk in fuzzy)
        for tag_id, name in self.tags.items():
            if _tag_matches_category_search_tag(name, query) or _tag_matches_query(name, query):
                for pk in self.tag_products[tag_id]:
                    scores.setdefault(pk, 10)
        groups = defaultdict(set)
        for pk, score in scores.items():
            groups[score].add(pk)
        result = (set(scores), dict(groups), fuzzy)
        if len(self.results) >= 256:
            self.results.popitem(last=False)
        self.results[query] = result
        return result


def search_candidates(query):
    global _SNAPSHOT, _BUILT_AT, _GENERATION, _TRANSACTION
    context = (connection.atomic_blocks[0] if connection.in_atomic_block else None, tuple(connection.savepoint_ids))
    generation = cache.get(_KEY)
    with _LOCK:
        if _SNAPSHOT is None or generation != _GENERATION or context != _TRANSACTION or time.monotonic() - _BUILT_AT > 60:
            _SNAPSHOT = CandidateIndex()
            _BUILT_AT = time.monotonic()
            _GENERATION = generation
            _TRANSACTION = context
        return _SNAPSHOT.match(query)
