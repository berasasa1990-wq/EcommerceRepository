from decimal import Decimal
from django.test import TestCase
from .models import Product
from .forms import SaleForm
from .cart import priced_items


class ArticlePlusTests(TestCase):
    def setUp(self):
        from bera_control.services import issue_module_code, display_activation_code, redeem_module_code
        for module in ('article_plus', 'quantity', 'bundle'):
            activation = issue_module_code(module)
            redeem_module_code(module, display_activation_code(activation))
        self.second = Product.objects.create(name='Drugi', price=20)
        self.first = Product.objects.create(name='Prvi', price=50, sale_type='article_plus', plus_product=self.second, plus_percent=25)

    def test_form_uses_regular_price_without_price_fields(self):
        form = SaleForm(data={'sale_type': 'article_plus', 'product': self.first.pk, 'plus_product': self.second.pk, 'plus_percent': 25}, instance=self.first)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['price'], Decimal('50'))

    def test_discount_requires_pair_and_limits_quantity(self):
        alone = priced_items([self.second], {str(self.second.pk): 2})
        self.assertEqual(alone[0]['subtotal'], 40)
        paired = priced_items([self.first, self.second], {str(self.first.pk): 1, str(self.second.pk): 2})
        self.assertEqual(sum(item['subtotal'] for item in paired), 85)
        self.assertEqual(paired[1]['price_groups'], [(Decimal('15.00'), 1), (Decimal('20'), 1)])

    def test_reject_same_product_and_invalid_percent(self):
        form = SaleForm(data={'sale_type': 'article_plus', 'product': self.first.pk, 'plus_product': self.first.pk, 'plus_percent': 101}, instance=self.first)
        self.assertFalse(form.is_valid())
        self.assertIn('plus_product', form.errors)
        self.assertIn('plus_percent', form.errors)
