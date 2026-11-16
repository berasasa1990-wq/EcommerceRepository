"""Database-side canonical search keys, also used by PostgreSQL expression indexes.

Read original columns: imports/bulk updates cannot leave stale normalized columns.
"""
import re
import unicodedata

from django.db.models import Func, TextField


def fold(value):
    value = unicodedata.normalize('NFC', str(value or '')).lower()
    return value.translate(str.maketrans({'š': 's', 'č': 'c', 'ć': 'c', 'ž': 'z', 'đ': 'dj'}))


def compact(value):
    return re.sub(r'[^a-z0-9]', '', fold(value))


class _SearchFunction(Func):
    def as_postgresql(self, compiler, connection, **extra_context):
        return self.as_sql(compiler, connection, function=f'public.{self.function}', **extra_context)


class SearchFold(_SearchFunction):
    function = 'shop_search_fold'
    output_field = TextField()


class SearchCompact(_SearchFunction):
    function = 'shop_search_compact'
    output_field = TextField()
