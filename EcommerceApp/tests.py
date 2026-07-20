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
