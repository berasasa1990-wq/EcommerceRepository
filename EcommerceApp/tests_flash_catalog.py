from datetime import timedelta
from decimal import Decimal

from django.test import TestCase, RequestFactory
from django.contrib.sessions.middleware import SessionMiddleware
from django.utils import timezone

from .models import Akcija, AkcijaFlashLine, Product, ProductVariation
from .views import _akcija_products_qs, _prefetch_product_cards
from .cart import Cart


class FlashCatalogPriceTests(TestCase):
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
