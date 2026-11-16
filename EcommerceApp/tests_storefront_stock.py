from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from .models import Product, ProductVariation, WarehouseStock, WarehouseLocation
from .cart import stock_on_hand
from .storefront_stock import storefront_products


@override_settings(ALLOWED_HOSTS=['testserver'], STORAGES={'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'}, 'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class StorefrontWarehouseStockTests(TestCase):
    def setUp(self):
        self.product = Product.objects.create(naziv='Stale catalog stock', cijena=10, stanje=2, na_stanju=True, magacin_sync_at=timezone.now())
        self.location = WarehouseLocation.objects.create(sifra='LIVE-A', naziv='Warehouse shelf')

    def test_empty_warehouse_overrides_stale_two_units(self):
        live = storefront_products([self.product])[0]
        self.assertEqual(live.stanje, 0)
        self.assertFalse(live.na_stanju)
        self.assertEqual(stock_on_hand(self.product), 0)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stanje, 2)  # Rendering never writes catalog fields.
        response = self.client.post(reverse('add_to_cart', args=[self.product.slug]), {'stay': '1'})
        self.assertEqual(response.status_code, 400)
        self.assertFalse(self.client.session.get('cart'))

    def test_live_stock_reservations_and_deletion_are_read_afresh(self):
        stock = WarehouseStock.objects.create(product=self.product, location=self.location, kolicina=5, rezervisano=2)
        self.assertEqual(storefront_products([self.product])[0].stanje, 3)
        self.assertEqual(stock_on_hand(self.product), 3)
        stock.kolicina = 2
        stock.save()
        self.assertEqual(storefront_products([self.product])[0].stanje, 0)
        stock.delete()
        self.assertEqual(storefront_products([self.product])[0].stanje, 0)

    def test_variant_uses_own_stock_even_when_flags_are_stale(self):
        variant = ProductVariation.objects.create(artikal=self.product, naziv='Red', stanje=2, na_stanju=True)
        other = ProductVariation.objects.create(artikal=self.product, naziv='Blue', stanje=0, na_stanju=False)
        WarehouseStock.objects.create(product=self.product, variation=other, location=self.location, kolicina=4)
        live = storefront_products([self.product])[0]
        self.assertEqual(live.stanje, 4)
        self.assertEqual([v.stanje for v in live.varijacije.all()], [0, 4])
        self.assertEqual(stock_on_hand(self.product, variant), 0)
        self.assertEqual(stock_on_hand(self.product, other), 4)

    def test_multiple_cards_use_four_queries_including_prices(self):
        other = Product.objects.create(naziv='Another card', cijena=20)
        with self.assertNumQueries(4):
            cards = storefront_products([self.product, other])
            self.assertEqual([p.katalog_prikazna_cijena for p in cards], [10, 20])

    def test_cached_card_uses_current_price_without_mutating_cache(self):
        from decimal import Decimal
        Product.objects.filter(pk=self.product.pk).update(cijena=Decimal('33.90'))
        card = storefront_products([self.product])[0]
        self.assertEqual(card.katalog_prikazna_cijena, Decimal('33.90'))
        self.assertEqual(self.product.cijena, 10)
        Product.objects.filter(pk=self.product.pk).update(akcijska_cijena=Decimal('29.90'))
        self.assertEqual(storefront_products([self.product])[0].katalog_prikazna_cijena, Decimal('29.90'))
        Product.objects.filter(pk=self.product.pk).update(akcijska_cijena=None)
        self.assertEqual(storefront_products([self.product])[0].katalog_prikazna_cijena, Decimal('33.90'))

    def test_template_card_displays_live_stock_from_cached_product(self):
        from django.template.loader import render_to_string
        stock = WarehouseStock.objects.create(product=self.product, location=self.location, kolicina=7)
        Product.objects.filter(pk=self.product.pk).update(cijena='33.90')
        html = render_to_string('partials/home_catalog_grid.html', {'products': [self.product]})
        self.assertIn('data-in-stock="1"', html)
        from django.utils.html import strip_tags
        self.assertIn('Na stanju · 7 kom', strip_tags(html))
        self.assertIn('data-default-price="33.90"', html)
        self.assertIn('class="price-current">33.90', html)
        stock.kolicina = 0
        stock.save()
        html = render_to_string('partials/home_catalog_grid.html', {'products': [self.product]})
        self.assertIn('Nije na stanju', html)
        self.assertNotIn('2 kom', html)

    def test_product_detail_uses_warehouse_zero_then_restock(self):
        response = self.client.get(reverse('product_detail', args=[self.product.slug]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['product'].stanje, 0)
        self.assertFalse(response.context['product_available'])
        WarehouseStock.objects.create(product=self.product, location=self.location, kolicina=6)
        response = self.client.get(reverse('product_detail', args=[self.product.slug]))
        self.assertEqual(response.context['product'].stanje, 6)
        self.assertTrue(response.context['product_available'])

    def test_add_accepts_real_stock_despite_stale_sold_out_flag(self):
        self.product.na_stanju = False
        self.product.stanje = 0
        self.product.save()
        WarehouseStock.objects.create(product=self.product, location=self.location, kolicina=3)
        response = self.client.post(reverse('add_to_cart', args=[self.product.slug]), {'stay': '1', 'quantity': '1'})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(self.client.session.get('cart'))

    def test_fresh_product_can_skip_price_reread_without_skipping_warehouse(self):
        Product.objects.filter(pk=self.product.pk).update(cijena='33.90')
        WarehouseStock.objects.create(product=self.product, location=self.location, kolicina=7)
        fresh = Product.objects.get(pk=self.product.pk)
        with self.assertNumQueries(3):
            card = storefront_products([fresh], refresh_prices=False)[0]
            self.assertEqual(str(card.katalog_prikazna_cijena), '33.90')
            self.assertEqual(card.stanje, 7)
        self.assertEqual(self.product.stanje, 2)

    def test_product_detail_batches_main_related_and_options(self):
        from unittest.mock import patch
        from .models import Category
        category = Category.objects.create(naziv='Test oprema')
        self.product.kategorija = category
        self.product.save(update_fields=['kategorija'])
        related = Product.objects.create(naziv='Drugi artikal', cijena=20, kategorija=category)
        WarehouseStock.objects.create(product=related, location=self.location, kolicina=4)
        with patch('EcommerceApp.storefront_stock.storefront_products', wraps=storefront_products) as refresh:
            response = self.client.get(reverse('product_detail', args=[self.product.slug]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(refresh.call_count, 1)
        self.assertEqual(response.context['storefront_products_by_id'][related.pk].stanje, 4)
        self.assertEqual(response.context['related_products'][0].pk, related.pk)
        WarehouseStock.objects.filter(product=related).update(kolicina=0)
        response = self.client.get(reverse('product_detail', args=[self.product.slug]))
        self.assertFalse(response.context['storefront_products_by_id'][related.pk].na_stanju)
