from unittest.mock import patch
from django.test import TestCase, RequestFactory
from .models import Product, HomeNovoProduct
from .views import _home_latest_products, _apply_product_filters


class NovitetiSelectionTests(TestCase):
    def test_random_ten_from_recent_available_products(self):
        products = [Product.objects.create(naziv=f'Novo {i}', cijena=10, stanje=2, na_stanju=True) for i in range(52)]
        Product.objects.create(naziv='Nema', cijena=10, stanje=0, na_stanju=False, je_novitet=True)
        with patch('EcommerceApp.views.random.sample', side_effect=lambda ids, count: ids[:count]) as sample:
            selected = _home_latest_products()
        self.assertEqual(len(selected), 10)
        self.assertEqual(len(set(p.pk for p in selected)), 10)
        self.assertEqual(len(sample.call_args.args[0]), 50)
        self.assertNotIn(products[0].pk, sample.call_args.args[0])
        self.assertTrue(all(p.stanje > 0 for p in selected))

    def test_see_all_uses_only_active_new_offer_selection(self):
        available = Product.objects.create(naziv='Dostupan', cijena=10, stanje=3, na_stanju=True, je_novitet=False)
        Product.objects.create(naziv='Rasprodan', cijena=10, stanje=0, na_stanju=True, je_novitet=True)
        HomeNovoProduct.objects.create(artikal=available, aktivan=True)
        Product.objects.create(naziv='Drugi dostupan', cijena=10, stanje=3, na_stanju=True)
        inactive = Product.objects.create(naziv='Iskljucen izbor', cijena=10, stanje=3, na_stanju=True)
        HomeNovoProduct.objects.create(artikal=inactive, aktivan=False)
        request = RequestFactory().get('/', {'noviteti': '1'})
        products, _ = _apply_product_filters(Product.objects.filter(aktivan=True), request)
        self.assertEqual([p.pk for p in products], [available.pk])

    def test_header_and_both_banner_variants_link_to_new_offer_catalog(self):
        from django.template.loader import render_to_string
        nav = render_to_string('bera_reference/partials/bera_home_nav.html')
        self.assertIn('href="/?noviteti=1">Novo', nav)
        for context in ({}, {'home_offer_slots': [{'url': '/stari-link/', 'title': 'Otkrijte novitete'}]}):
            html = render_to_string('bera_reference/partials/bera_offer_banners.html', context)
            self.assertIn('href="/?noviteti=1"', html)
            self.assertNotIn('/stari-link/', html)

    def test_catalog_http_shows_curated_products_and_combines_search(self):
        chosen = Product.objects.create(naziv='Odabrani novitet', cijena=10, stanje=3, na_stanju=True)
        other = Product.objects.create(naziv='Drugi dostupni artikal', cijena=10, stanje=3, na_stanju=True)
        HomeNovoProduct.objects.create(artikal=chosen, aktivan=True)
        response = self.client.get('/', {'noviteti': '1'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual({p.pk for p in response.context['page_obj']}, {chosen.pk})
        response = self.client.get('/', {'noviteti': '1', 'q': 'Drugi dostupni'})
        self.assertEqual(list(response.context['page_obj']), [])
