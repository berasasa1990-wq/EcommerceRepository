from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase, RequestFactory

from .cart import Cart
from .plus_percent import discounts_for_cart


class PlusPercentTests(SimpleTestCase):
    def setUp(self):
        self.offer = SimpleNamespace(pk=1, popust_postotak=Decimal('10'))
        self.lines = {
            '1:0': {'product_id': 1, 'quantity': 1},
            '2:0': {'product_id': 2, 'quantity': 2},
        }

    def test_second_product_discount_and_trigger_removal(self):
        self.assertEqual(discounts_for_cart(self.lines, {1: self.offer}, {'1:0': 5, '2:0': 5}), {'2:0': self.offer})
        self.assertEqual(discounts_for_cart({'2:0': self.lines['2:0']}, {1: self.offer}, {'2:0': 5}), {})
        self.assertEqual(discounts_for_cart(self.lines, {1: self.offer}, {'1:0': 0, '2:0': 5}), {})

    def test_same_product_and_variants_do_not_qualify_as_second(self):
        lines = {'1:0': self.lines['1:0'], '1:2': {'product_id': 1, 'quantity': 3}}
        self.assertEqual(discounts_for_cart(lines, {1: self.offer}, {'1:0': 5, '1:2': 5}), {})

    def test_two_category_products_keep_one_trigger(self):
        result = discounts_for_cart(self.lines, {1: self.offer, 2: self.offer}, {'1:0': 5, '2:0': 5})
        self.assertEqual(list(result), ['2:0'])

    def test_cart_total_recalculates_without_changing_session_prices(self):
        request = RequestFactory().get('/')
        request.session = {'cart': {key: {**line, 'cijena': '100', 'bazna_cijena': '100',
            'naziv': str(line['product_id']), 'product_naziv': str(line['product_id'])}
            for key, line in self.lines.items()}}
        products = [
            SimpleNamespace(pk=i, varijacije=SimpleNamespace(all=lambda: []), je_pakovanje=False,
                            has_running_home_offer=lambda: False)
            for i in (1, 2)
        ]
        cart = Cart(request)
        with patch('EcommerceApp.cart.Product.objects') as manager, \
             patch('EcommerceApp.korpa_nudjenje.build_korpa_nudjenje_map', return_value={}), \
             patch('EcommerceApp.plus_percent.offers_for_products', return_value={1: self.offer}), \
             patch.object(cart, 'availability', return_value={'1:0': 5, '2:0': 5}), \
             patch('EcommerceApp.upsell.prime_quantity_deals'), \
             patch('EcommerceApp.cart.get_deal_info_for_cart_item', return_value=None), \
             patch('EcommerceApp.models.Akcija.objects') as actions:
            manager.filter.return_value.prefetch_related.return_value = products
            actions.filter.return_value.order_by.return_value = []
            items = list(cart)
            self.assertEqual(items[1]['cijena_decimal'], Decimal('90'))
            self.assertEqual(items[1]['ukupno_stavka'], Decimal('180'))
            self.assertEqual(cart.ukupno, Decimal('280'))
            self.assertEqual(request.session['cart']['2:0']['cijena'], '100')
            del cart.cart['1:0']
            self.assertEqual(cart.ukupno, Decimal('200'))
