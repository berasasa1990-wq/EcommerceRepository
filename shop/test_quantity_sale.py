from decimal import Decimal
from django.contrib.auth import get_user_model
from django.test import TestCase
from .models import Product, OrderItem
from .forms import SaleForm
from .cart import priced_items, quantity_offers


class QuantitySaleTests(TestCase):
    def setUp(self):
        from bera_control.services import issue_module_code, display_activation_code, redeem_module_code
        for module in ('article_plus', 'quantity', 'bundle'):
            activation = issue_module_code(module)
            redeem_module_code(module, display_activation_code(activation))
        self.product = Product.objects.create(name='Najlon', price=100, stock=10, sale_type='quantity', quantity_percent_2=10, quantity_percent_3=15, quantity_percent_over_3=20)

    def test_boundaries_and_rounding(self):
        for count, total in [(1, 100), (2, 180), (3, 255), (4, 320), (5, 400)]:
            with self.subTest(count=count):
                item = priced_items([self.product], {str(self.product.pk): count})[0]
                self.assertEqual(item['subtotal'], total)
        self.product.price = Decimal('1.99')
        self.assertEqual(priced_items([self.product], {str(self.product.pk): 2})[0]['subtotal'], Decimal('3.58'))

    def test_panel_saves_separate_percentages(self):
        manager = get_user_model().objects.create_user('manager', is_staff=True)
        self.client.force_login(manager)
        response = self.client.post('/panel/akcije/dodaj/', {'sale_type': 'quantity', 'product': self.product.pk, 'quantity_percent_2': 5, 'quantity_percent_3': 10, 'quantity_percent_over_3': 15})
        self.assertEqual(response.status_code, 302)
        self.product.refresh_from_db()
        self.assertEqual(self.product.quantity_percent_2, 5)
        self.assertEqual(self.product.quantity_percent_3, 10)
        self.assertEqual(self.product.quantity_percent_over_3, 15)
        self.assertEqual(self.product.price, 100)

    def test_form_rejects_invalid_or_missing_percentages(self):
        for percent in [-1, 101, '']:
            form = SaleForm(data={'sale_type': 'quantity', 'product': self.product.pk, 'quantity_percent_2': percent, 'quantity_percent_3': 10, 'quantity_percent_over_3': 20}, instance=self.product)
            self.assertFalse(form.is_valid())
            self.assertIn('quantity_percent_2', form.errors)

    def test_cart_and_checkout_share_prices(self):
        self.client.post(f'/korpa/{self.product.pk}/', {'quantity': 1})
        response = self.client.get('/korpa/')
        self.assertContains(response, 'Odaberi 2 kom')
        self.assertContains(response, 'Odaberi 3 kom')
        self.assertContains(response, 'Odaberi 4 kom')
        self.client.post(f'/korpa/{self.product.pk}/', {'quantity': 4})
        self.assertEqual(self.client.get('/korpa/').context['cart_total'], 320)
        response = self.client.post('/checkout/', {'customer_name': 'Kupac', 'email': 'test@example.com', 'phone': '061123456', 'address': 'Ulica 1', 'city': 'Sarajevo', 'postal_code': '71000'})
        self.assertEqual(response.status_code, 302)
        item = OrderItem.objects.get()
        self.assertEqual(item.quantity, 4)
        self.assertEqual(item.unit_price, 80)
        self.assertEqual(item.order.total, 320)
        self.assertIn('Količinsko sniženje: 4 kom', item.promotion)
        self.assertIn('20%', item.promotion)
        self.product.quantity_percent_over_3 = 5
        self.product.save()
        item.refresh_from_db()
        self.assertIn('20%', item.promotion)

    def test_offers_respect_stock(self):
        self.product.stock = 2
        self.assertEqual([offer['quantity'] for offer in quantity_offers(self.product, 1)], [2])

    def test_discount_disappears_when_quantity_reduced(self):
        self.client.post(f'/korpa/{self.product.pk}/', {'quantity': 3})
        self.client.post(f'/korpa/{self.product.pk}/', {'quantity': 1})
        response = self.client.get('/korpa/')
        self.assertEqual(response.context['cart_total'], 100)
        self.assertNotContains(response, 'Količinsko sniženje ·')

    def test_product_page_hides_quantity_offers(self):
        response = self.client.get(self.product.get_absolute_url())
        self.assertNotContains(response, 'product-quantity-offers')
        self.assertNotContains(response, 'data-quantity-offer')
