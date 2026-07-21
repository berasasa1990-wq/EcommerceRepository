from decimal import Decimal

from django.test import SimpleTestCase, TestCase

from .models import Product, ProductVariation
from .pricing import _loyalty_osnovica_iz_korpe


class LoyaltyCouponPricingTests(SimpleTestCase):
    def test_loyalty_excludes_discounted_items(self):
        cart_items = [
            {
                'cijena': '20.00',
                'bazna_cijena': '25.00',
                'quantity': 2,
                'na_akciji': True,
                'cijena_decimal': Decimal('20.00'),
                'bazna_cijena_decimal': Decimal('25.00'),
            },
            {
                'cijena': '30.00',
                'bazna_cijena': '30.00',
                'quantity': 1,
                'na_akciji': False,
                'cijena_decimal': Decimal('30.00'),
                'bazna_cijena_decimal': Decimal('30.00'),
            },
        ]
        self.assertEqual(_loyalty_osnovica_iz_korpe(cart_items), Decimal('30.00'))

    def test_loyalty_counts_only_full_price_units_in_deal(self):
        cart_items = [
            {
                'cijena': '10.00',
                'bazna_cijena': '10.00',
                'quantity': 3,
                'na_akciji': False,
                'cijena_decimal': Decimal('10.00'),
                'bazna_cijena_decimal': Decimal('10.00'),
                'deal_info': {
                    'has_discount': True,
                    'full_price_count': 2,
                    'discounted_count': 1,
                },
            },
        ]
        self.assertEqual(_loyalty_osnovica_iz_korpe(cart_items), Decimal('20.00'))


class ProductPakovanjeKatalogHintTests(TestCase):
    """Pretraga/katalog: ista količina → Cijena za N; različite → Cijena na pakovanje."""

    def _product(self, **kwargs):
        defaults = {
            'naziv': 'Test pakovanje',
            'slug': 'test-pakovanje-hint',
            'cijena': Decimal('9.99'),
            'aktivan': True,
            'na_stanju': True,
        }
        defaults.update(kwargs)
        return Product.objects.create(**defaults)

    def test_product_level_pack_without_variations(self):
        product = self._product(pakovanje_komada=10)
        self.assertTrue(product.je_pakovanje)
        self.assertEqual(product.pakovanje_cijena_hint, 'Cijena za 10 kom.')
        self.assertEqual(product.pakovanje_label, 'Pakovanje 10 kom.')

    def test_all_variations_same_pack_via_product(self):
        product = self._product(pakovanje_komada=10, slug='pack-same-inherit')
        ProductVariation.objects.create(artikal=product, naziv='A', redoslijed=1)
        ProductVariation.objects.create(artikal=product, naziv='B', redoslijed=2)
        product = Product.objects.get(pk=product.pk)
        self.assertEqual(product.pakovanje_jedinstvena_kolicina, 10)
        self.assertEqual(product.pakovanje_cijena_hint, 'Cijena za 10 kom.')

    def test_variations_different_pack_sizes(self):
        product = self._product(slug='pack-diff', pakovanje_komada=None)
        ProductVariation.objects.create(
            artikal=product, naziv='A', redoslijed=1, pakovanje_komada=10,
        )
        ProductVariation.objects.create(
            artikal=product, naziv='B', redoslijed=2, pakovanje_komada=20,
        )
        product = Product.objects.get(pk=product.pk)
        self.assertTrue(product.je_pakovanje)
        self.assertEqual(product.pakovanje_cijena_hint, 'Cijena na pakovanje / ne na komad')
        self.assertEqual(product.pakovanje_label, 'Pakovanje')

    def test_variations_same_override_ignores_product_field(self):
        product = self._product(slug='pack-override', pakovanje_komada=9)
        ProductVariation.objects.create(
            artikal=product, naziv='A', redoslijed=1, pakovanje_komada=5,
        )
        ProductVariation.objects.create(
            artikal=product, naziv='B', redoslijed=2, pakovanje_komada=5,
        )
        product = Product.objects.get(pk=product.pk)
        self.assertEqual(product.pakovanje_cijena_hint, 'Cijena za 5 kom.')
        # Fallback polje artikla ostaje netaknuto za varijacije bez override-a
        self.assertEqual(product.pakovanje_komada_prikaz, 9)


class PonudaAkcijaTests(TestCase):
    """+ Ponuda: popup after add-to-cart, optional % discount."""

    def setUp(self):
        from .models import Akcija
        self.trigger = Product.objects.create(
            naziv='Trigger artikal',
            slug='trigger-ponuda',
            cijena=Decimal('20.00'),
            aktivan=True,
            na_stanju=True,
        )
        self.offer = Product.objects.create(
            naziv='Ponuda artikal',
            slug='offer-ponuda',
            cijena=Decimal('10.00'),
            aktivan=True,
            na_stanju=True,
        )
        self.Akcija = Akcija

    def test_offer_with_discount(self):
        from .gratis import (
            build_gratis_offer_response,
            get_active_gratis_akcija_for_product,
        )
        akcija = self.Akcija.objects.create(
            naziv='Test ponuda',
            tip=self.Akcija.Tip.PONUDA,
            artikal=self.trigger,
            gratis_artikal=self.offer,
            popust_postotak=Decimal('20'),
            aktivan=True,
        )
        found = get_active_gratis_akcija_for_product(self.trigger)
        self.assertEqual(found.pk, akcija.pk)
        payload = build_gratis_offer_response(akcija)
        self.assertIsNotNone(payload)
        self.assertTrue(payload['has_discount'])
        self.assertEqual(payload['pct'], '20')
        self.assertEqual(payload['discounted_price'], '8.00')

    def test_offer_without_discount_regular_price(self):
        from .gratis import build_gratis_offer_response, get_active_gratis_akcija_for_product
        akcija = self.Akcija.objects.create(
            naziv='Test ponuda regular',
            tip=self.Akcija.Tip.PONUDA,
            artikal=self.trigger,
            gratis_artikal=self.offer,
            popust_postotak=None,
            aktivan=True,
        )
        found = get_active_gratis_akcija_for_product(self.trigger)
        self.assertEqual(found.pk, akcija.pk)
        payload = build_gratis_offer_response(akcija)
        self.assertIsNotNone(payload)
        self.assertFalse(payload['has_discount'])
        self.assertEqual(payload['original_price'], '10.00')
        self.assertEqual(payload['discounted_price'], '10.00')

    def _post_add(self, data):
        from django.contrib.auth.models import AnonymousUser
        from django.contrib.sessions.middleware import SessionMiddleware
        from django.test import RequestFactory
        from .views import add_to_cart

        factory = RequestFactory()
        request = factory.post(f'/artikal/{self.trigger.slug}/dodaj/', data)
        SessionMiddleware(lambda r: None).process_request(request)
        request.session.save()
        request.user = AnonymousUser()
        return add_to_cart(request, self.trigger.slug)

    def test_add_to_cart_requires_choice(self):
        self.Akcija.objects.create(
            naziv='Cart ponuda',
            tip=self.Akcija.Tip.PONUDA,
            artikal=self.trigger,
            gratis_artikal=self.offer,
            popust_postotak=Decimal('15'),
            aktivan=True,
        )
        resp = self._post_add({'quantity': '1', 'stay': '1'})
        self.assertEqual(resp.status_code, 200)
        import json
        data = json.loads(resp.content)
        self.assertTrue(data.get('ok'))
        self.assertTrue(data.get('requires_gratis_choice'))
        self.assertIn('gratis_offer', data)
        self.assertEqual(data['gratis_offer']['gratis_naziv'], 'Ponuda artikal')

    def test_accept_adds_both_products(self):
        akcija = self.Akcija.objects.create(
            naziv='Accept ponuda',
            tip=self.Akcija.Tip.PONUDA,
            artikal=self.trigger,
            gratis_artikal=self.offer,
            popust_postotak=Decimal('50'),
            aktivan=True,
        )
        resp = self._post_add({
            'quantity': '1',
            'stay': '1',
            'gratis_choice': 'yes',
            'gratis_akcija_id': str(akcija.pk),
        })
        self.assertEqual(resp.status_code, 200)
        import json
        data = json.loads(resp.content)
        self.assertTrue(data.get('ok'))
        self.assertEqual(data.get('cart_count'), 2)
        self.assertIn('Ponuda artikal', data.get('message', ''))
