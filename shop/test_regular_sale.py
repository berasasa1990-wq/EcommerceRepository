from decimal import Decimal
from django.test import TestCase
from .forms import SaleForm
from .models import Product


class RegularSaleTests(TestCase):
    def setUp(self):
        self.product = Product.objects.create(name='Štap', price=80, original_price=100)

    def form(self, **values):
        return SaleForm(data={'sale_type': 'regular', 'product': self.product.pk, **values}, instance=self.product)

    def test_percentage_uses_regular_price(self):
        form = self.form(discount_percent=25)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['price'], Decimal('75.00'))
        self.assertEqual(form.cleaned_data['original_price'], Decimal('100'))

    def test_manual_price_ignores_submitted_original_price(self):
        form = self.form(price=60, original_price=900)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['price'], Decimal('60'))
        self.assertEqual(form.cleaned_data['original_price'], Decimal('100'))

    def test_requires_exactly_one_discount_input(self):
        for values in [{}, {'price': 50, 'discount_percent': 20}, {'price': -1}, {'price': 100}, {'discount_percent': 101}]:
            with self.subTest(values=values):
                self.assertFalse(self.form(**values).is_valid())

    def test_new_sale_uses_current_regular_price(self):
        self.product.original_price = None
        self.product.save()
        form = self.form(discount_percent=10)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['price'], Decimal('72.00'))
        self.assertEqual(form.cleaned_data['original_price'], Decimal('80'))
