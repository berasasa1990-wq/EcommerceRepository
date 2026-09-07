from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from .models import Product, ProductVariation, WarehouseStock, WarehouseLocation, Order


@override_settings(ALLOWED_HOSTS=['testserver'], STORAGES={'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'}, 'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class CartAvailabilityTests(TestCase):
    def setUp(self):
        self.product = Product.objects.create(naziv='Varalica', cijena=10, stanje=5, na_stanju=True, magacin_sync_at=timezone.now())
        self.location = WarehouseLocation.objects.create(sifra='CART-A', naziv='Cart A')
        self.stock = WarehouseStock.objects.create(product=self.product, location=self.location, kolicina=1)
        self.key = f'{self.product.pk}:0'
        self.set_cart()

    def set_cart(self, variant=None):
        session = self.client.session
        session['cart'] = {self.key: {'product_id': self.product.pk, 'variation_id': variant.pk if variant else None,
            'quantity': 1, 'cijena': '10.00', 'bazna_cijena': '10.00', 'na_akciji': False,
            'naziv': 'Varalica', 'product_naziv': 'Varalica'}}
        session.save()

    def test_cart_keeps_sold_out_item_and_checkout_rejects_it(self):
        self.stock.kolicina = 0
        self.stock.save()
        response = self.client.get(reverse('cart'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Rasprodat — uklonite iz korpe')
        self.assertTrue(response.context['cart_items'][0]['sold_out'])
        self.assertIn(self.key, self.client.session['cart'])
        response = self.client.post(reverse('checkout'), {'ime_prezime': 'Kupac'})
        self.assertRedirects(response, reverse('cart'), fetch_redirect_response=False)
        self.assertFalse(Order.objects.exists())
        response = self.client.post(reverse('remove_from_cart', args=[self.key]))
        self.assertEqual(response.status_code, 302)
        self.assertNotIn(self.key, self.client.session.get('cart', {}))

    def test_live_check_reflects_sale_reservation_and_restock(self):
        url = reverse('cart_stock')
        self.assertFalse(self.client.get(url).json()['items'][0]['sold_out'])
        self.stock.rezervisano = 1
        self.stock.save()
        response = self.client.get(url)
        self.assertTrue(response.json()['items'][0]['sold_out'])
        self.assertIn('no-store', response['Cache-Control'])
        self.stock.kolicina = 3
        self.stock.save()
        self.assertEqual(self.client.get(url).json()['items'][0]['quantity'], 2)
        self.stock.delete()
        self.assertTrue(self.client.get(url).json()['items'][0]['sold_out'])

    def test_other_variant_does_not_make_sold_out_sku_available(self):
        variant = ProductVariation.objects.create(artikal=self.product, naziv='Crvena', stanje=4, na_stanju=True)
        other = ProductVariation.objects.create(artikal=self.product, naziv='Plava', stanje=4, na_stanju=True)
        WarehouseStock.objects.create(product=self.product, variation=other, location=self.location, kolicina=4)
        self.set_cart(variant)
        self.assertTrue(self.client.get(reverse('cart_stock')).json()['items'][0]['sold_out'])

    def test_unmanaged_catalog_product_keeps_existing_stock_behavior(self):
        self.stock.delete()
        self.product.magacin_sync_at = None
        self.product.save()
        self.assertEqual(self.client.get(reverse('cart_stock')).json()['items'][0]['quantity'], 5)
        self.product.na_stanju = False
        self.product.save()
        self.assertTrue(self.client.get(reverse('cart_stock')).json()['items'][0]['sold_out'])
