from django.test import TestCase, override_settings
from django.urls import reverse
from .models import Order, OrderItem, Product


@override_settings(STORAGES={
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
})
class OrderSuccessLayoutTests(TestCase):
    def test_guest_confirmation_shows_order_shipping_and_items(self):
        product = Product.objects.create(naziv='Artikal potvrde', cijena=25)
        order = Order.objects.create(ime_prezime='Test Kupac', email='test@example.com',
            telefon='061123456', adresa='Test ulica 10', grad='Sarajevo', ukupno=50)
        OrderItem.objects.create(narudzba=order, artikal=product, naziv=product.naziv, kolicina=2, cijena=25)
        response = self.client.get(reverse('order_success', args=[order.broj]))
        self.assertEqual(response.status_code, 200)
        for text in ['uspješno zaprimljena', order.broj, 'Test Kupac', 'Test ulica 10',
                     'Artikal potvrde', 'Stavke narudžbe', '50.00 KM', '#success-order-details']:
            self.assertContains(response, text)
        self.assertFalse(response.context['scratch_game_immediate'])
