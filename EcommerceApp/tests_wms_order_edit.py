from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from .models import Order, Product, WMSOrder, WMSOrderItem


class WMSOrderEditTests(TestCase):
    def setUp(self):
        self.client.force_login(get_user_model().objects.create_superuser('edit-wms', 'edit@example.test', 'test-only'))
        self.product = Product.objects.create(naziv='Artikal A', sifra='A', cijena=10, stanje=8)
        self.second = Product.objects.create(naziv='Artikal B', sifra='B', cijena=20, stanje=6)
        self.order = WMSOrder.objects.create(kupac='Kupac', tip='online')
        self.item = WMSOrderItem.objects.create(order=self.order, artikal=self.product, naziv=self.product.naziv,
                                               sifra='A', kolicina=1, cijena=10, bazna_cijena=10)
        self.url = reverse('staff_wms_edit_order', args=[self.order.pk])
        p = patch('EcommerceApp.views_wms.module_locked', return_value=False)
        p.start()
        self.addCleanup(p.stop)

    def payload(self):
        return {
            'kupac': 'Kupac', 'items-TOTAL_FORMS': '2', 'items-INITIAL_FORMS': '1',
            'items-MIN_NUM_FORMS': '1', 'items-MAX_NUM_FORMS': '500',
            'items-0-id': self.item.pk, 'items-0-artikal': self.product.pk,
            'items-0-kolicina': '3', 'items-0-cijena': '9.50',
            'items-1-artikal': self.second.pk, 'items-1-kolicina': '2', 'items-1-cijena': '20.00',
        }

    def test_list_omits_picking_icon_and_edit_opens_items(self):
        response = self.client.get(reverse('staff_wms_section', args=['narudzbe']))
        self.assertNotContains(response, 'Pošalji na odvajanje robe')
        self.assertContains(response, self.url)
        response = self.client.get(self.url)
        self.assertContains(response, 'Artikli narudžbe')
        self.assertContains(response, 'items-0-kolicina')
        self.assertContains(response, 'Dodaj artikal')

    def test_manual_order_updates_lines_without_changing_stock_or_creating_order(self):
        self.assertEqual(self.client.post(self.url, self.payload()).status_code, 302)
        self.item.refresh_from_db()
        self.assertEqual((self.item.kolicina, self.item.cijena), (3, Decimal('9.50')))
        self.assertEqual(self.order.items.count(), 2)
        added = self.order.items.get(artikal=self.second)
        self.assertEqual((added.naziv, added.sifra, added.kolicina), ('Artikal B', 'B', 2))
        self.assertEqual(WMSOrder.objects.count(), 1)
        self.product.refresh_from_db()
        self.second.refresh_from_db()
        self.assertEqual((self.product.stanje, self.second.stanje), (8, 6))

    def test_remove_line_and_add_replacement(self):
        data = self.payload()
        data['items-0-DELETE'] = 'on'
        self.assertEqual(self.client.post(self.url, data).status_code, 302)
        self.assertFalse(WMSOrderItem.objects.filter(pk=self.item.pk).exists())
        self.assertEqual(self.order.items.get().artikal_id, self.second.pk)

    def test_invalid_quantity_does_not_partially_update_customer_or_lines(self):
        data = self.payload()
        data.update({'kupac': 'Promijenjen', 'items-0-kolicina': '0'})
        self.assertEqual(self.client.post(self.url, data).status_code, 200)
        self.order.refresh_from_db()
        self.item.refresh_from_db()
        self.assertEqual(self.order.kupac, 'Kupac')
        self.assertEqual(self.item.kolicina, 1)
        self.assertEqual(self.order.items.count(), 1)

    def test_finished_order_cannot_be_modified(self):
        self.order.status = 'zapakovana'
        self.order.save(update_fields=['status'])
        self.assertEqual(self.client.post(self.url, self.payload()).status_code, 302)
        self.item.refresh_from_db()
        self.assertEqual(self.item.kolicina, 1)

    def test_source_order_uses_existing_full_order_editor(self):
        source = Order.objects.create(ime_prezime='Web kupac', ukupno=10)
        self.order.source_order = source
        self.order.save(update_fields=['source_order'])
        response = self.client.get(self.url)
        self.assertRedirects(response, reverse('staff_magacin_narudzba_nova') + '?broj=' + source.broj,
                             fetch_redirect_response=False)
