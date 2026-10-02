from django.test import TestCase, RequestFactory
from django.contrib.auth.models import AnonymousUser
from .models import Category, Product, ProductVariation, Tag
from .views import _apply_search_filter, _search_relevance_score, _suggest_relevance_annotation, search_suggest


class ProductSearchScopeTests(TestCase):
    def setUp(self):
        root = Category.objects.create(naziv='Oprema')
        category = Category.objects.create(naziv='Podkategorija', roditelj=root, search_tagovi='unikatpecanje')
        self.category_only = Product.objects.create(naziv='Nepovezan proizvod', cijena=10, kategorija=category)
        self.named = Product.objects.create(naziv='Unikatpecanje štap', cijena=10)
        self.coded = Product.objects.create(naziv='Drugi proizvod', sifra='unikatpecanje-42', cijena=10)
        self.tagged = Product.objects.create(naziv='Treći proizvod', cijena=10)
        self.tagged.tagovi.add(Tag.objects.create(naziv='unikatpecanje'))
        self.variant = Product.objects.create(naziv='Četvrti proizvod', cijena=10)
        ProductVariation.objects.create(artikal=self.variant, naziv='Crvena', sifra='unikatpecanje-99')

    def test_only_name_sku_and_product_tags_match(self):
        ids = set(_apply_search_filter(Product.objects.all(), 'unikatpecanje').values_list('pk', flat=True))
        self.assertEqual(ids, {self.named.pk, self.coded.pk, self.tagged.pk, self.variant.pk})

    def test_category_tags_do_not_affect_ranking(self):
        self.assertEqual(_search_relevance_score(self.category_only, 'unikatpecanje'), 0)
        score = Product.objects.filter(pk=self.category_only.pk).annotate(score=_suggest_relevance_annotation('unikatpecanje')).get().score
        self.assertEqual(score, 10)

    def test_autocomplete_excludes_category_only_matches(self):
        import json
        request = RequestFactory().get('/pretraga/', {'q': 'unikatpecanje'})
        request.user = AnonymousUser()
        request.session = {}
        response = search_suggest(request)
        data = json.loads(response.content)
        self.assertNotIn(self.category_only.naziv, [row['naziv'] for row in data['results']])
        self.assertIn(self.tagged.naziv, [row['naziv'] for row in data['results']])

    def test_search_prioritizes_new_badges_by_creation_date(self):
        import json
        from datetime import timedelta
        from django.utils import timezone
        from django.contrib.sessions.backends.signed_cookies import SessionStore
        from unittest.mock import patch
        from .views import _apply_product_filters

        older = Product.objects.create(naziv='Unikatpecanje novo starije', cijena=30, je_novitet=True)
        newer = Product.objects.create(naziv='Unikatpecanje novo novije', cijena=40, je_novitet=True)
        unrelated = Product.objects.create(naziv='Nepovezan novitet', cijena=10, je_novitet=True)
        sold = Product.objects.create(naziv='Unikatpecanje rasprodato', cijena=10, je_novitet=True, na_stanju=False)
        Product.objects.filter(pk=older.pk).update(kreiran=timezone.now() - timedelta(days=2))
        Product.objects.filter(pk=self.named.pk).update(prioritet_lagera=2)
        request = RequestFactory().get('/pretraga/', {'q': 'unikatpecanje'})
        request.user = AnonymousUser()
        request.session = SessionStore()
        data = json.loads(search_suggest(request).content)
        self.assertEqual([row['naziv'] for row in data['results'][:2]], [newer.naziv, older.naziv])
        with patch('EcommerceApp.views.SiteSearchEvent.objects.create'):
            products, _ = _apply_product_filters(Product.objects.all(), request)
        ids = [p.pk for p in products]
        self.assertEqual(ids[:2], [newer.pk, older.pk])
        self.assertNotIn(unrelated.pk, ids)
        self.assertEqual(ids[-1], sold.pk)
