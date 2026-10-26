from decimal import Decimal
from django.contrib.auth import get_user_model
from django.test import TestCase
from .models import Product, OrderItem
from .forms import SaleForm
from .cart import priced_items


class BundleSaleTests(TestCase):
    def setUp(self):
        from bera_control.services import issue_module_code, display_activation_code, redeem_module_code
        for module in ('article_plus', 'quantity', 'bundle'):
            activation = issue_module_code(module)
            redeem_module_code(module, display_activation_code(activation))
        self.first = Product.objects.create(name='Štap', price=100, stock=10, sale_type='bundle', bundle_percent=20)
        self.second = Product.objects.create(name='Mašinica', price=50, stock=10)
        self.third = Product.objects.create(name='Najlon', price=10, stock=10)
        self.first.bundle_products.set([self.second, self.third])

    def prices(self, counts):
        return priced_items([self.first, self.second, self.third], {str(product.pk): count for product, count in zip([self.first, self.second, self.third], counts)})

    def test_complete_and_incomplete_sets(self):
        self.assertEqual(sum(item['subtotal'] for item in self.prices([1, 1, 1])), 128)
        self.assertEqual(sum(item['subtotal'] for item in self.prices([1, 1, 0])), 150)
        self.assertEqual(sum(item['subtotal'] for item in self.prices([1, 2, 1])), 178)
        self.assertEqual(sum(item['subtotal'] for item in self.prices([2, 2, 2])), 256)

    def test_does_not_stack_with_quantity_discount(self):
        self.second.sale_type = 'quantity'
        self.second.quantity_percent_2 = Decimal('30')
        self.second.save()
        items = self.prices([1, 2, 1])
        second = next(item for item in items if item['product'] == self.second)
        self.assertEqual(second['subtotal'], 70)

    def test_panel_saves_and_edits_set(self):
        user = get_user_model().objects.create_user('manager', is_staff=True)
        self.client.force_login(user)
        data = {'sale_type': 'bundle', 'product': self.first.pk, 'bundle_products': [self.first.pk, self.second.pk, self.third.pk], 'bundle_percent': 15}
        response = self.client.post('/panel/akcije/dodaj/', data)
        self.assertEqual(response.status_code, 302)
        self.first.refresh_from_db()
        self.assertEqual(self.first.bundle_percent, 15)
        self.assertEqual(set(self.first.bundle_products.all()), {self.second, self.third})
        response = self.client.get(f'/panel/akcije/{self.first.pk}/uredi/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(set(response.context['edit_form'].initial['bundle_products']), {self.first.pk, self.second.pk, self.third.pk})
        data['bundle_products'] = [self.first.pk, self.second.pk]
        self.client.post(f'/panel/akcije/{self.first.pk}/uredi/', data)
        self.assertEqual(list(self.first.bundle_products.all()), [self.second])

    def test_invalid_set_is_rejected(self):
        for members, percent in [([], 20), ([self.first.pk], 20), ([self.second.pk], 0), ([self.second.pk], 101)]:
            with self.subTest(members=members, percent=percent):
                form = SaleForm(data={'sale_type': 'bundle', 'product': self.first.pk, 'bundle_products': members, 'bundle_percent': percent}, instance=self.first)
                self.assertFalse(form.is_valid())

    def test_inactive_member_rejected(self):
        self.second.is_active = False
        self.second.save()
        form = SaleForm(data={'sale_type': 'bundle', 'product': self.first.pk, 'bundle_products': [self.second.pk], 'bundle_percent': 20}, instance=self.first)
        self.assertFalse(form.is_valid())

    def test_cart_checkout_and_removing_member(self):
        for product in [self.first, self.second, self.third]:
            self.client.post(f'/korpa/{product.pk}/', {'quantity': 1})
        response = self.client.get('/korpa/')
        self.assertEqual(response.context['cart_total'], 128)
        self.assertContains(response, 'Bundle set · −20%')
        self.client.post(f'/korpa/{self.third.pk}/', {'quantity': 0})
        self.assertEqual(self.client.get('/korpa/').context['cart_total'], 150)
        self.client.post(f'/korpa/{self.third.pk}/', {'quantity': 1})
        response = self.client.post('/checkout/', {'customer_name': 'Kupac', 'email': 'test@example.com', 'phone': '061123456', 'address': 'Ulica 1', 'city': 'Sarajevo', 'postal_code': '71000'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(sum(item.unit_price * item.quantity for item in OrderItem.objects.all()), 128)

    def test_product_page_and_sales_show_bundle(self):
        self.assertContains(self.client.get(self.first.get_absolute_url()), 'SET AKCIJA -20%')
        self.assertContains(self.client.get('/akcije/'), 'Štap')

    def test_new_set_needs_no_main_product_and_shows_on_every_member(self):
        user = get_user_model().objects.create_user('bundle-manager', is_staff=True)
        self.client.force_login(user)
        self.first.sale_type = 'regular'
        self.first.bundle_percent = None
        self.first.save()
        self.first.bundle_products.clear()
        response = self.client.post('/panel/akcije/dodaj/', {
            'sale_type': 'bundle', 'bundle_products': [self.first.pk, self.second.pk, self.third.pk], 'bundle_percent': 25,
        })
        self.assertEqual(response.status_code, 302)
        stored = Product.objects.get(sale_type='bundle')
        self.assertEqual(stored.bundle_percent, 25)
        self.assertEqual({stored.pk, *stored.bundle_products.values_list('pk', flat=True)}, {self.first.pk, self.second.pk, self.third.pk})
        for member in [self.first, self.second, self.third]:
            page = self.client.get(member.get_absolute_url())
            self.assertContains(page, 'SET AKCIJA -25%')
            for name in ['Štap', 'Mašinica', 'Najlon']:
                self.assertContains(page, name)

    def test_edit_can_remove_any_member(self):
        user = get_user_model().objects.create_user('edit-manager', is_staff=True)
        self.client.force_login(user)
        response = self.client.post(f'/panel/akcije/{self.first.pk}/uredi/', {
            'sale_type': 'bundle', 'bundle_products': [self.second.pk, self.third.pk], 'bundle_percent': 10,
        })
        self.assertEqual(response.status_code, 302)
        self.first.refresh_from_db()
        self.assertEqual(self.first.sale_type, 'regular')
        stored = Product.objects.get(sale_type='bundle')
        self.assertEqual({stored.pk, *stored.bundle_products.values_list('pk', flat=True)}, {self.second.pk, self.third.pk})
        self.assertNotContains(self.client.get(self.first.get_absolute_url()), 'SET AKCIJA -')

    def test_add_bundle_adds_every_member_and_applies_discount(self):
        response = self.client.post(f'/korpa/set/{self.first.pk}/', HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['count'], 3)
        self.assertEqual(self.client.get('/korpa/').context['cart_total'], 128)
        self.client.post(f'/korpa/set/{self.first.pk}/')
        self.assertEqual(self.client.get('/korpa/').context['cart_total'], 256)

    def test_unavailable_bundle_does_not_add_partial_set(self):
        self.third.is_out_of_stock = True
        self.third.save()
        response = self.client.post(f'/korpa/set/{self.first.pk}/')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.client.session.get('cart', {}), {})

    def test_promotion_groups_match_the_actual_winning_discount(self):
        self.second.sale_type = 'quantity'
        self.second.quantity_percent_2 = Decimal('30')
        self.second.save()
        items = self.prices([1, 2, 1])
        first = next(item for item in items if item['product'] == self.first)
        second = next(item for item in items if item['product'] == self.second)
        self.assertIn('Bundle set: Štap', first['promotion_groups'][0][2])
        self.assertIn('Količinsko sniženje', second['promotion_groups'][0][2])
        self.assertNotIn('Bundle', second['promotion_groups'][0][2])
        for item in items:
            self.assertEqual(sum(price * count for price, count, label in item['promotion_groups']), item['subtotal'])
