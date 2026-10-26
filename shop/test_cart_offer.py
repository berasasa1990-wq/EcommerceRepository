from django.test import TestCase
from .models import Product


class CartOfferTests(TestCase):
    def setUp(self):
        from bera_control.services import issue_module_code, display_activation_code, redeem_module_code
        activation = issue_module_code('article_plus')
        redeem_module_code('article_plus', display_activation_code(activation))
        self.second = Product.objects.create(name='Najlon', price=20, stock=5)
        self.first = Product.objects.create(name='Štap', price=100, stock=5, sale_type='article_plus', plus_product=self.second, plus_percent=25)

    def add(self, product):
        return self.client.post(f'/korpa/{product.pk}/', {'action': 'add', 'quantity': 1}, HTTP_X_REQUESTED_WITH='XMLHttpRequest')

    def test_offer_and_accept(self):
        response = self.add(self.first)
        self.assertEqual(response.status_code, 200)
        offer = response.json()['offer']
        self.assertEqual(offer['name'], 'Najlon')
        self.assertEqual(offer['percent'], '25')
        self.assertEqual(offer['price'], '15.00')
        self.assertContains(self.client.get('/korpa/'), 'Dodaj uz 25% popusta')
        self.add(self.second)
        cart = self.client.get('/korpa/')
        self.assertEqual(cart.context['cart_total'], 115)
        self.assertContains(cart, 'cart-applied-offer">Ponuda +')
        discounted = next(item for item in cart.context['cart_items'] if item['product'].pk == self.second.pk)
        self.assertEqual(discounted['plus_discounts'][0]['quantity'], 1)
        self.assertEqual(discounted['plus_discounts'][0]['price'], 15)
        self.client.post(f'/korpa/{self.first.pk}/', {'quantity': 0})
        self.assertNotContains(self.client.get('/korpa/'), 'cart-applied-offer')
        self.assertNotContains(cart, 'Dodaj uz 25% popusta')

    def test_no_offer_when_pair_already_added(self):
        self.add(self.second)
        self.assertIsNone(self.add(self.first).json()['offer'])

    def test_unavailable_second_not_offered(self):
        self.second.is_out_of_stock = True
        self.second.save()
        self.assertIsNone(self.add(self.first).json()['offer'])

    def test_regular_product_has_no_offer(self):
        self.assertIsNone(self.add(self.second).json()['offer'])

    def test_plus_promotion_identifies_anchor_and_only_discounted_units(self):
        from .cart import priced_items
        item = next(item for item in priced_items([self.first, self.second], {str(self.first.pk): 1, str(self.second.pk): 2}) if item['product'] == self.second)
        discounted, regular = item['promotion_groups']
        self.assertEqual(discounted[:2], (15, 1))
        self.assertIn('Ponuda +: Štap', discounted[2])
        self.assertEqual(regular, (20, 1, ''))
