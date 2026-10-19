from decimal import Decimal
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from .models import Order, OrderItem


@override_settings(ALLOWED_HOSTS=['testserver'], STORAGES={
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
})
class OrderPreviewTests(TestCase):
    def test_preview_uses_saved_amounts_and_customer(self):
        user = get_user_model().objects.create_user('preview-admin', is_staff=True, is_superuser=True)
        self.client.force_login(user)
        order = Order.objects.create(ime_prezime='Kupac Test', email='kupac@example.com',
            telefon='061123456', adresa='Ulica 10', grad='Brčko', ukupno=Decimal('75.00'),
            medjuzbir=Decimal('80.00'), popust=Decimal('10.00'), dostava=Decimal('5.00'),
            status=Order.Status.POSLANA)
        OrderItem.objects.create(narudzba=order, naziv='Proizvod test', sifra='TEST',
                                 cijena=Decimal('40.00'), kolicina=2)
        response = self.client.get(reverse('staff_order_detail', args=[order.broj]))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'staff/partials/order_preview.html')
        for text in ['Kupac Test', 'Ulica 10', 'Brčko', 'Proizvod test', 'Ukupno za naplatu']:
            self.assertContains(response, text)
        self.assertNotContains(response, 'Status narudžbe')
        self.assertNotContains(response, 'order-preview__barcode')
        self.assertEqual(response.context['summary']['ukupno'], Decimal('75.00'))
        self.assertEqual(response.context['summary']['dostava'], Decimal('5.00'))

    def test_print_invoice_shares_preview_layout_and_totals(self):
        from django.template.loader import render_to_string
        from .views import _order_print_job
        order = Order.objects.create(ime_prezime='Print Kupac', email='print@example.com',
            telefon='061123456', adresa='Ulica 1', grad='Bijeljina',
            medjuzbir=Decimal('59.63'), dostava=Decimal('11.00'), ukupno=Decimal('70.63'))
        OrderItem.objects.create(narudzba=order, naziv='Print artikal', sifra='PRINT',
                                 cijena=Decimal('59.63'), kolicina=1)
        job = _order_print_job(order)
        html = render_to_string('staff/order_print.html', {'print_jobs': [job]})
        for text in ['order-preview__customer-grid', 'Print Kupac', 'Print artikal',
                     'PDV je uračunat u cijenu', 'Dostava – xExpress', 'Gotovinski',
                     'staff-order-print.css', 'window.print()']:
            self.assertIn(text, html)
        self.assertNotIn('Status narudžbe', html)
        self.assertEqual(job['summary']['ukupno'], Decimal('70.63'))
