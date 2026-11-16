"""Paginator source that loads product objects only for the requested page."""
from django.utils.functional import cached_property


class SearchResults:
    def __init__(self, queryset, limit):
        self.queryset = queryset
        self.limit = limit

    @cached_property
    def total(self):
        return min(self.queryset.order_by().values('pk').distinct().count(), self.limit)

    def count(self):
        return self.total

    def __len__(self):
        return self.total

    def __iter__(self):
        return iter(self.queryset[:self.limit])

    def __getitem__(self, key):
        return self.queryset[:self.limit][key]
