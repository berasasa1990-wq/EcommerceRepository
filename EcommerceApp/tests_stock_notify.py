from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse
from .models import Brand, Product, StockNotify, WarehouseLocation
from .stock_notify import email_action_token, notify_back_in_stock, subscribe


@override_settings(
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    EMAIL_HOST_USER='shop@example.com',
    EMAIL_HOST_PASSWORD='token',
    ORDER_NOTIFICATION_EMAIL='shop@example.com',
    DEFAULT_FROM_EMAIL='shop@example.com',
    SITE_URL='https://example.com',
    STORAGES={
        'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
        'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
    },
)
class StockNotifyTests(TestCase):
    def setUp(self):
        self.product = Product.objects.create(
            naziv='Fox clip out',
            sifra='FOX-OUT-1',
            cijena=Decimal('9.90'),
            stanje=0,
            na_stanju=False,
            aktivan=True,
        )

    def test_guest_needs_email_then_subscribes(self):
        url = reverse('stock_notify', args=[self.product.slug])
        missing = self.client.post(url)
        self.assertEqual(missing.status_code, 400)
        bad = self.client.post(url, {'email': 'nije-email'})
        self.assertEqual(bad.status_code, 400)
        ok = self.client.post(url, {'email': 'gost@example.com'})
        self.assertEqual(ok.status_code, 200)
        self.assertTrue(ok.json()['ok'])
        self.assertTrue(
            StockNotify.objects.filter(
                product=self.product, email='gost@example.com', notified_at__isnull=True,
            ).exists()
        )
        again = self.client.post(url, {'email': 'Gost@example.com'})
        self.assertEqual(again.status_code, 200)
        self.assertIn('Već si na listi', again.json()['message'])
        self.assertEqual(StockNotify.objects.filter(product=self.product).count(), 1)

    def test_logged_in_uses_account_email(self):
        user = User.objects.create_user('ana', 'ana@example.com', 'pass')
        self.client.force_login(user)
        url = reverse('stock_notify', args=[self.product.slug])
        ok = self.client.post(url)
        self.assertEqual(ok.status_code, 200)
        row = StockNotify.objects.get(product=self.product)
        self.assertEqual(row.email, 'ana@example.com')
        self.assertEqual(row.user, user)

    def test_product_detail_shows_notify_instead_of_sold_out(self):
        page = self.client.get(reverse('product_detail', args=[self.product.slug]))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'Obavijesti kada bude na stanju')
        self.assertContains(page, reverse('stock_notify', args=[self.product.slug]))
        self.assertContains(page, 'stockNotifyOverlay')
        self.assertNotContains(page, 'btn-add-to-bag--sold')
        html = page.content.decode()
        self.assertNotIn('>Rasprodato<', html)

    def test_restock_sends_email(self):
        subscribe(product=self.product, email='gost@example.com')
        self.product.na_stanju = True
        self.product.stanje = 4
        self.product.save(update_fields=['na_stanju', 'stanje'])
        sent = notify_back_in_stock(self.product.pk)
        self.assertEqual(sent, 1)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('ponovo na stanju', mail.outbox[0].subject)
        self.assertIn('gost@example.com', mail.outbox[0].to)
        product_url = f'https://example.com{self.product.get_absolute_url()}'
        html = mail.outbox[0].alternatives[0][0]
        self.assertIn(f'href="{product_url}"', html)
        self.assertIn('Pogledaj artikal', html)
        self.assertIn('Artikal je ponovo na stanju', html)
        self.assertIn('9,90 KM', html)
        self.assertIn('#FF5A00', html)
        self.assertNotIn('img/emails/stock-header.jpg', html)
        self.assertIn('Dodaj u korpu', html)
        self.assertIn(product_url, mail.outbox[0].body)
        row = StockNotify.objects.get(email='gost@example.com')
        self.assertIsNotNone(row.notified_at)

    def test_email_shows_brand_and_unsubscribe(self):
        brand = Brand.objects.create(naziv='Daiwa')
        self.product.brend = brand
        self.product.save(update_fields=['brend'])
        subscribe(product=self.product, email='gost@example.com')
        self.product.na_stanju = True
        self.product.stanje = 2
        self.product.save(update_fields=['na_stanju', 'stanje'])
        notify_back_in_stock(self.product.pk)
        html = mail.outbox[0].alternatives[0][0]
        self.assertIn('DAIWA', html)
        self.assertIn('Šifra: FOX-OUT-1', html)
        self.assertIn('/obavijesti/odjava/', html)
        token = email_action_token('gost@example.com', self.product.pk)
        preview = self.client.get(reverse('stock_back_preview', args=[token]))
        self.assertEqual(preview.status_code, 200)
        self.assertContains(preview, 'Artikal je ponovo na stanju')
        page = self.client.get(reverse('stock_notify_unsubscribe', args=[token]))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'Odjavljeni ste')

    def test_refresh_catalog_qty_notifies_on_restock(self):
        from .magacin import apply_movement

        subscribe(product=self.product, email='kupac@example.com')
        loc = WarehouseLocation.objects.create(sifra='N-1', naziv='Notify loc')
        with patch('EcommerceApp.emails.send_stock_back_email') as send:
            with self.captureOnCommitCallbacks(execute=True):
                apply_movement(product=self.product, location=loc, tip='prijem', kolicina=3)
            self.assertTrue(send.called)
        self.product.refresh_from_db()
        self.assertTrue(self.product.na_stanju)
        self.assertIsNotNone(
            StockNotify.objects.get(email='kupac@example.com').notified_at
        )
