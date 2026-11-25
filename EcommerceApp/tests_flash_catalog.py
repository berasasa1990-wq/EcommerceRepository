from datetime import timedelta
from decimal import Decimal

from django.test import TestCase, RequestFactory, override_settings
from django.contrib.sessions.middleware import SessionMiddleware
from django.utils import timezone

from .models import Akcija, AkcijaFlashLine, Product, ProductVariation
from .views import _akcija_products_qs, _prefetch_product_cards
from .cart import Cart


class FlashCatalogPriceTests(TestCase):
    def test_cached_storefront_offer_is_refreshed(self):
        from .storefront_stock import storefront_products
        cached = _prefetch_product_cards(Product.objects.filter(pk=self.product.pk)).get()
        self.assertEqual(cached.prikazna_cijena, Decimal('80'))
        Akcija.objects.filter(pk=self.offer.pk).update(aktivan=False)
        self.assertEqual(storefront_products([cached])[0].prikazna_cijena, Decimal('100'))
        self.assertEqual(cached.prikazna_cijena, Decimal('80'))
        Akcija.objects.filter(pk=self.offer.pk).update(aktivan=True, popust_postotak=30)
        self.assertEqual(storefront_products([cached])[0].prikazna_cijena, Decimal('70'))

    def setUp(self):
        self.product = Product.objects.create(naziv='Odabrani', cijena=100, aktivan=True, na_stanju=True, stanje=20)
        self.other = Product.objects.create(naziv='Drugi', cijena=80, aktivan=True, na_stanju=True, stanje=20)
        self.offer = Akcija.objects.create(naziv='Vikend', tip='akcijska', popust_postotak=20,
                                          pocetak=timezone.now()-timedelta(hours=1), trajanje_sati=24,
                                          flash_trigger='product', artikal=self.other)
        self.line = AkcijaFlashLine.objects.create(akcija=self.offer, product=self.product)

    def test_only_selected_product_gets_catalog_and_cart_discount(self):
        self.assertEqual(self.product.prikazna_cijena, Decimal('80'))
        self.assertTrue(self.product.na_akciji)
        self.assertEqual(self.other.prikazna_cijena, Decimal('80'))
        self.assertFalse(self.other.na_akciji)
        self.assertFalse(self.offer.flash_applies_to_product(self.other))
        self.assertEqual(list(_akcija_products_qs(Product.objects.all())), [self.product])
        request = RequestFactory().get('/')
        SessionMiddleware(lambda r: None).process_request(request)
        cart = Cart(request)
        cart.add(self.product, quantity=1)
        self.assertEqual(next(iter(cart))['cijena_decimal'], Decimal('80'))

    def test_variation_uses_its_own_base_without_double_discount(self):
        variation = ProductVariation.objects.create(artikal=self.product, naziv='Veliki', cijena=150, na_stanju=True, stanje=20)
        self.assertEqual(variation.prikazna_cijena, Decimal('120'))
        self.line.popust_postotak = 10
        self.line.save()
        self.assertEqual(variation.prikazna_cijena, Decimal('135'))
        self.assertEqual(self.product.prikazna_cijena, Decimal('90'))

    def test_future_expired_disabled_and_removed_offers_stop_discount(self):
        for changes in [{'pocetak': timezone.now()+timedelta(hours=1)},
                        {'pocetak': timezone.now()-timedelta(hours=30)}, {'aktivan': False}]:
            Akcija.objects.filter(pk=self.offer.pk).update(pocetak=timezone.now()-timedelta(hours=1), aktivan=True)
            Akcija.objects.filter(pk=self.offer.pk).update(**changes)
            self.assertEqual(self.product.prikazna_cijena, Decimal('100'))
            self.assertFalse(_akcija_products_qs(Product.objects.all()).exists())
        Akcija.objects.filter(pk=self.offer.pk).update(aktivan=True)
        self.line.delete()
        self.assertEqual(self.product.prikazna_cijena, Decimal('100'))

    def test_existing_lower_sale_is_preserved_and_cards_are_prefetched(self):
        self.product.akcijska_cijena = Decimal('70')
        self.product.save()
        products = list(_prefetch_product_cards(Product.objects.all()))
        with self.assertNumQueries(0):
            prices = {p.pk: p.prikazna_cijena for p in products}
        self.assertEqual(prices[self.product.pk], Decimal('70'))

    def test_add_to_cart_uses_same_price_with_or_without_old_offer_parameter(self):
        from django.contrib.auth.models import AnonymousUser
        from .views import add_to_cart
        variation = ProductVariation.objects.create(artikal=self.product, naziv='Varijanta', cijena=150, na_stanju=True, stanje=20)
        for extra in [{}, {'flash_offer_id': self.offer.pk}]:
            request = RequestFactory().post('/', {'quantity': 1, 'stay': '1', 'variation_id': variation.pk, **extra})
            request.user = AnonymousUser()
            SessionMiddleware(lambda r: None).process_request(request)
            request.session.save()
            response = add_to_cart(request, self.product.slug)
            self.assertEqual(response.status_code, 200, response.content)
            self.assertEqual(next(iter(Cart(request)))['cijena_decimal'], Decimal('120'))

    def test_complete_stock_sale_reprices_catalog_and_cart_at_schedule_boundaries(self):
        from unittest.mock import patch
        from .storefront_stock import storefront_products

        now = timezone.now()
        Product.objects.filter(pk=self.other.pk).update(akcijska_cijena=Decimal('75'))
        self.other.refresh_from_db()
        variant_item = Product.objects.create(naziv='Varijante', cijena=100)
        available = ProductVariation.objects.create(
            artikal=variant_item, naziv='Dostupna', cijena=150, akcijska_cijena=140, na_stanju=True, stanje=2,
        )
        sold_out = ProductVariation.objects.create(
            artikal=variant_item, naziv='Rasprodana', cijena=100, na_stanju=False, stanje=0,
        )
        unavailable = Product.objects.create(naziv='Rasprodano', cijena=100, na_stanju=False, stanje=0)
        Akcija.objects.create(
            naziv='Kompletan lager', tip=Akcija.Tip.AKCIJSKA, kompletan_lager=True,
            popust_postotak=10, pocetak=now + timedelta(minutes=1),
            kraj_lagera=now + timedelta(minutes=2),
        )

        request = RequestFactory().get('/')
        SessionMiddleware(lambda r: None).process_request(request)
        cart = Cart(request)
        cart.add(self.other)
        self.assertEqual(next(iter(cart))['cijena_decimal'], Decimal('75'))

        with patch('django.utils.timezone.now', return_value=now + timedelta(minutes=1)):
            self.assertEqual(next(iter(cart))['cijena_decimal'], Decimal('72'))
            self.assertEqual(available.prikazna_cijena, Decimal('135'))
            self.assertEqual(sold_out.prikazna_cijena, Decimal('100'))
            self.assertEqual(unavailable.prikazna_cijena, Decimal('100'))
            self.assertEqual(
                set(_akcija_products_qs(Product.objects.all())), {self.product, self.other, variant_item},
            )
            cached = _prefetch_product_cards(Product.objects.filter(pk=self.product.pk)).get()
            self.assertEqual(cached.prikazna_cijena, Decimal('80'))

        with patch('django.utils.timezone.now', return_value=now + timedelta(minutes=2)):
            self.assertEqual(next(iter(cart))['cijena_decimal'], Decimal('75'))
            self.assertEqual(available.prikazna_cijena, Decimal('140'))
            self.assertEqual(storefront_products([cached])[0].prikazna_cijena, Decimal('80'))

    def test_admin_stock_sale_requires_discount_and_ordered_dates(self):
        from django.contrib.auth import get_user_model
        from django.urls import reverse

        user = get_user_model().objects.create_superuser('stock-admin', password='test-password')
        self.client.force_login(user)
        url = reverse('admin:EcommerceApp_akcija_add')
        start = timezone.localtime().replace(microsecond=0) + timedelta(days=1)
        end = start + timedelta(minutes=90)
        self.assertContains(self.client.get(url), 'id_kompletan_lager')
        data = {
            'naziv': 'Planirani popust', 'tip': Akcija.Tip.AKCIJSKA, 'aktivan': 'on',
            'redoslijed': '0', 'kompletan_lager': 'on', 'popust_postotak': '10',
            'pocetak_0': start.date().isoformat(), 'pocetak_1': start.time().isoformat(),
            'kraj_lagera_0': end.date().isoformat(), 'kraj_lagera_1': end.time().isoformat(),
            '_save': 'Sačuvaj',
        }
        invalid = self.client.post(url, {
            **data, 'popust_postotak': '0',
            'kraj_lagera_0': start.date().isoformat(), 'kraj_lagera_1': start.time().isoformat(),
        })
        self.assertContains(invalid, 'Završetak mora biti nakon početka.')
        self.assertContains(invalid, 'Unesite popust veći od 0%.')
        self.assertEqual(self.client.post(url, data).status_code, 302)
        sale = Akcija.objects.get(naziv='Planirani popust')
        self.assertTrue(sale.kompletan_lager)
        self.assertEqual(sale.pocetak, start)
        self.assertEqual(sale.zavrsava, end)
        self.assertFalse(sale.flash_lines.exists())

    def test_all_catalog_discounts_use_the_same_card_badge(self):
        from unittest.mock import patch

        from django.template.loader import render_to_string
        from .storefront_stock import storefront_products

        now = timezone.now()
        end = now + timedelta(hours=1)
        Akcija.objects.create(
            naziv='Kompletan lager', tip=Akcija.Tip.AKCIJSKA, kompletan_lager=True,
            popust_postotak=25, pocetak=now, kraj_lagera=end,
        )
        template = 'partials/product_catalog_card.html'
        product = storefront_products([self.other])[0]
        card = render_to_string(template, {'product': product, 'hide_variations': True})
        self.assertIn('product-card--on-sale', card)
        self.assertIn('product-sale-badge">−25%', card)
        self.assertNotIn('product-sale-ribbon', card)

        with patch('django.utils.timezone.now', return_value=end):
            product = storefront_products([product])[0]
            card = render_to_string(template, {'product': product, 'hide_variations': True})
        self.assertNotIn('product-card--on-sale', card)
        self.assertNotIn('product-sale-badge', card)

        Product.objects.filter(pk=self.other.pk).update(akcijska_cijena=Decimal('60'))
        with patch('django.utils.timezone.now', return_value=end):
            product = storefront_products([product])[0]
            card = render_to_string(template, {'product': product, 'hide_variations': True})
        self.assertIn('product-sale-badge">−25%', card)


    @override_settings(STORAGES={
        'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
        'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
    })
    def test_product_detail_displays_current_discount_on_gallery_and_variation(self):
        from django.urls import reverse

        path = reverse('product_detail', args=[self.product.slug])
        response = self.client.get(path)
        self.assertContains(response, 'product-sale-badge">−20%')
        self.assertRegex(response.content.decode(), r'class="price-sale card-dwell-now">80[.,]00 KM')

        ProductVariation.objects.create(
            artikal=self.product, naziv='Veliki', cijena=150, na_stanju=True, stanje=20,
        )
        response = self.client.get(path)
        self.assertContains(response, 'product-sale-badge">−20%', count=2)
        Akcija.objects.filter(pk=self.offer.pk).update(aktivan=False)
        self.assertNotContains(self.client.get(path), 'product-sale-badge')
