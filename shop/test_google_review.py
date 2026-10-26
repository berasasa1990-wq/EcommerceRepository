from django.test import TestCase, Client
from .models import Product, SEOSettings
from .forms import SEOSettingsForm


class GoogleReviewTests(TestCase):
    def setUp(self):
        SEOSettings.objects.create(pk=1, google_review_url='https://g.page/r/example/review')
        self.product = Product.objects.create(name='Artikal', price=20, stock=10)
        self.client.post(f'/korpa/{self.product.pk}/', {'quantity': 1})

    def test_review_only_after_successful_order(self):
        self.assertNotContains(self.client.get('/checkout/'), 'checkoutReviewTitle')
        self.assertNotContains(self.client.post('/checkout/', {}), 'checkoutReviewTitle')
        response = self.client.post('/checkout/', {'customer_name': 'Kupac', 'email': 'test@example.com', 'phone': '061123456', 'address': 'Ulica 1', 'city': 'Sarajevo', 'postal_code': '71000'}, follow=True)
        self.assertContains(response, 'Hvala na narudžbi!')
        self.assertContains(response, 'Ostavi Google recenziju')
        self.assertContains(response, 'https://g.page/r/example/review')
        self.assertNotContains(Client().get('/checkout/?success=1', follow=True), 'checkoutReviewTitle')
        SEOSettings.objects.filter(pk=1).update(google_review_url='')
        self.assertNotContains(self.client.get('/checkout/?success=1'), 'checkoutReviewTitle')

    def test_review_url_requires_https(self):
        for value in ('http://example.com', 'javascript:alert(1)', 'https://user:password@example.com'):
            form = SEOSettingsForm({'site_name': 'Shop', 'google_review_url': value})
            self.assertFalse(form.is_valid())
            self.assertIn('google_review_url', form.errors)
