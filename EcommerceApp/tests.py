from decimal import Decimal

from django.test import SimpleTestCase

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