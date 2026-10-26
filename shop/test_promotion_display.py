from django.test import TestCase
from .models import Product


class PromotionDisplayTests(TestCase):
    def setUp(self):
        from bera_control.services import issue_module_code, display_activation_code, redeem_module_code
        activation = issue_module_code('article_plus')
        redeem_module_code('article_plus', display_activation_code(activation))
        self.second = Product.objects.create(name='Najlon za akciju', price=20)
        self.first = Product.objects.create(name='Štap za akciju', price=100, sale_type='article_plus', plus_product=self.second, plus_percent=25)

    def test_article_plus_shown_on_detail_and_sales(self):
        for path in [self.first.get_absolute_url(), '/akcije/']:
            response = self.client.get(path)
            self.assertContains(response, 'Ponuda +')
            if path == self.first.get_absolute_url():
                self.assertContains(response, '25% popusta na')
                self.assertContains(response, 'data-plus-preview-open')
                self.assertContains(response, self.second.name)
            else:
                self.assertNotContains(response, '25% popusta na')
        self.assertNotContains(self.client.get(self.second.get_absolute_url()), '25% popusta na')

    def test_inactive_second_hides_offer(self):
        self.second.is_active = False
        self.second.save()
        self.assertNotContains(self.client.get(self.first.get_absolute_url()), '25% popusta na')
        self.assertNotIn(self.first, Product.objects.on_sale())

    def test_regular_sale_shows_discount_and_both_prices(self):
        self.second.original_price = 40
        self.second.save()
        response = self.client.get(self.second.get_absolute_url())
        self.assertContains(response, '-50%')
        self.assertContains(response, '20.00 KM')
        self.assertContains(response, '40.00 KM')
