from datetime import timedelta
from decimal import Decimal
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from bera_control.models import ModuleActivation, ModuleSwitch
from bera_control.services import issue_module_code, display_activation_code, redeem_module_code
from bera_licensing.services import module_enabled
from .models import Product, Order, OrderItem
from .cart import priced_items, article_plus_offer, quantity_offers
from .forms import SaleForm


class PaidPromotionTests(TestCase):
    def setUp(self):
        self.second = Product.objects.create(name='Drugi artikal', price=50, stock=20)
        self.plus = Product.objects.create(name='Prvi artikal', price=100, stock=20, sale_type='article_plus', plus_product=self.second, plus_percent=20)
        self.quantity = Product.objects.create(name='Količinski artikal', price=100, stock=20, sale_type='quantity', quantity_percent_2=10, quantity_percent_3=15, quantity_percent_over_3=20)
        self.bundle = Product.objects.create(name='Set artikal', price=100, stock=20, sale_type='bundle', bundle_percent=20)
        self.bundle.bundle_products.add(self.second)
        self.staff = get_user_model().objects.create_user('paid-promotions-manager', is_staff=True)
        self.client.force_login(self.staff)

    def activate(self, code):
        activation = issue_module_code(code)
        self.assertTrue(redeem_module_code(code, display_activation_code(activation)))

    def total(self, *products, quantity=1):
        return sum(item['subtotal'] for item in priced_items(products, {str(p.pk): quantity for p in products}))

    def test_paid_promotions_are_locked_by_default_and_free_discount_works(self):
        for module in ('article_plus', 'quantity', 'bundle'):
            self.assertFalse(module_enabled(module))
        self.assertEqual(self.total(self.plus, self.second), 150)
        self.assertEqual(self.total(self.quantity, quantity=3), 300)
        self.assertEqual(self.total(self.bundle, self.second), 150)
        self.assertIsNone(article_plus_offer(self.plus, {str(self.plus.pk): 1}))
        self.assertEqual(quantity_offers(self.quantity, 1), [])
        self.assertEqual(list(Product.objects.on_sale()), [])
        self.assertNotContains(self.client.get(self.plus.get_absolute_url()), 'productPlusPreview')
        self.assertNotContains(self.client.get(self.bundle.get_absolute_url()), 'SET AKCIJA')
        self.assertNotContains(self.client.get(self.quantity.get_absolute_url()), 'Kupujte više komada')
        self.assertEqual(self.client.post(f'/korpa/set/{self.bundle.pk}/').status_code, 403)
        regular = Product.objects.create(name='Besplatna akcija', price=80, original_price=100)
        self.assertEqual(self.total(regular), 80)
        self.assertIn(regular, Product.objects.on_sale())
        form = SaleForm({'sale_type': 'regular', 'product': regular.pk, 'discount_percent': 30}, instance=regular)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['price'], Decimal('70'))

    def test_codes_unlock_only_the_selected_module(self):
        self.activate('article_plus')
        self.assertEqual(self.total(self.plus, self.second), 140)
        self.assertEqual(self.total(self.quantity, quantity=3), 300)
        self.assertEqual(self.total(self.bundle, self.second), 150)
        self.assertContains(self.client.get(self.plus.get_absolute_url()), 'productPlusPreview')
        self.activate('quantity')
        self.assertEqual(self.total(self.quantity, quantity=3), 255)
        self.activate('bundle')
        self.assertEqual(self.total(self.bundle, self.second), 120)
        self.assertContains(self.client.get(self.bundle.get_absolute_url()), 'SET AKCIJA')
        self.assertTrue(quantity_offers(self.quantity, 1))

    def test_disabled_expired_and_pending_modules_do_not_apply_or_erase_data(self):
        for code, products, count, expected in [
            ('article_plus', [self.plus, self.second], 1, 150),
            ('quantity', [self.quantity], 3, 300),
            ('bundle', [self.bundle, self.second], 1, 150),
        ]:
            self.activate(code)
            ModuleActivation.objects.filter(module=code).update(expires_at=timezone.now()-timedelta(seconds=1))
            self.assertEqual(self.total(*products, quantity=count), expected)
            ModuleActivation.objects.filter(module=code).update(expires_at=None)
            ModuleSwitch.objects.update_or_create(code=code, defaults={'enabled': False})
            self.assertEqual(self.total(*products, quantity=count), expected)
            ModuleSwitch.objects.filter(code=code).update(enabled=True)
            issue_module_code(code)  # Reissued but not redeemed.
            self.assertEqual(self.total(*products, quantity=count), expected)
        self.plus.refresh_from_db()
        self.quantity.refresh_from_db()
        self.assertEqual(self.plus.plus_percent, 20)
        self.assertEqual(self.quantity.quantity_percent_3, 15)
        self.assertEqual(list(self.bundle.bundle_products.all()), [self.second])

    def test_direct_posts_cannot_create_unlicensed_promotions(self):
        for code, product, extra in [
            ('article_plus', self.plus, {'plus_product': self.second.pk, 'plus_percent': 30}),
            ('quantity', self.quantity, {'quantity_percent_2': 30, 'quantity_percent_3': 40, 'quantity_percent_over_3': 50}),
            ('bundle', self.bundle, {'bundle_products': [self.bundle.pk, self.second.pk], 'bundle_percent': 50}),
        ]:
            response = self.client.post('/panel/akcije/dodaj/', {'sale_type': code, 'product': product.pk, **extra})
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, 'Ova akcija zahtijeva kupljen i aktiviran modul.')
        self.quantity.refresh_from_db()
        self.assertEqual(self.quantity.quantity_percent_2, 10)
        form = SaleForm()
        html = str(form['sale_type'])
        self.assertIn('disabled', html)
        self.assertIn('aktivacija u Moduli', html)
        response = self.client.get('/panel/moduli/')
        for label in ('FREE', 'Akcija – Ponuda +', 'Količinski popust', 'Bundle set'):
            self.assertContains(response, label)

    def test_checkout_rechecks_expired_promotion(self):
        self.activate('quantity')
        self.client.post(f'/korpa/{self.quantity.pk}/', {'quantity': 3})
        self.assertEqual(self.client.get('/korpa/').context['cart_total'], 255)
        ModuleActivation.objects.filter(module='quantity').update(expires_at=timezone.now()-timedelta(seconds=1))
        response = self.client.post('/checkout/', {'customer_name': 'Kupac', 'email': 'kupac@example.com', 'phone': '061123456', 'address': 'Ulica 1', 'city': 'Sarajevo', 'postal_code': '71000'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(sum(item.unit_price * item.quantity for item in OrderItem.objects.filter(order=Order.objects.get())), 300)
