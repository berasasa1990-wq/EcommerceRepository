from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .magacin import apply_movement
from .models import Product, ProductVariation, WarehouseLocation, WarehouseStock, Order


@override_settings(SECURE_SSL_REDIRECT=False, SITE_PREP_ENABLED=False)
class BulkMpTransferTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser('bulk-admin', 'bulk@example.com', 'pass')
        self.client.force_login(self.user)
        self.location = WarehouseLocation.objects.create(sifra='BULK-SRC', naziv='Magacin test')
        self.mp = WarehouseLocation.objects.create(sifra='BULK-MP', naziv='Maloprodaja test')
        self.products = [Product.objects.create(naziv=f'Bulk {n}', sifra=f'BULK-{n}',
            cijena=10, stanje=8, na_stanju=True, magacin_sync_at=timezone.now()) for n in range(3)]
        for product in self.products:
            apply_movement(product=product, location=self.location, tip='prijem', kolicina=8)
        self.url = reverse('staff_magacin_fali_na_sajtu')

    def payload(self, quantities):
        data = {'action': 'bulk_prenos_mp', 'selected': []}
        for product, qty in zip(self.products, quantities):
            key = f'{product.pk}:'
            data['selected'].append(key)
            data[f'quantity_{key}'] = str(qty)
            data[f'location_{key}'] = str(self.location.pk)
        return data

    def test_selected_quantities_are_reserved_on_one_picking(self):
        data = self.payload([2, 5])
        # Fields for an unselected product must have no effect.
        data[f'quantity_{self.products[2].pk}:'] = '7'
        data[f'location_{self.products[2].pk}:'] = str(self.location.pk)
        response = self.client.post(self.url, data)
        self.assertEqual(response.status_code, 302)
        order = Order.objects.get(ime_prezime='Prenos u MP')
        self.assertEqual(dict(order.stavke.values_list('artikal_id', 'kolicina')),
                         {self.products[0].pk: 2, self.products[1].pk: 5})
        for product, reserved in zip(self.products, [2, 5, 0]):
            stock = WarehouseStock.objects.get(product=product, location=self.location)
            self.assertEqual((stock.kolicina, stock.rezervisano), (8, reserved))
        self.assertFalse(WarehouseStock.objects.filter(location=self.mp, kolicina__gt=0).exists())

    def test_invalid_second_row_rolls_back_first_row_and_reservations(self):
        for invalid in (9, 0, -1, '1.5', ''):
            self.client.post(self.url, self.payload([2, invalid]))
            self.assertFalse(Order.objects.filter(ime_prezime='Prenos u MP').exists())
            self.assertFalse(WarehouseStock.objects.filter(rezervisano__gt=0).exists())

    def test_variation_transfer_uses_its_own_stock_and_checks_product(self):
        product = self.products[0]
        variation = ProductVariation.objects.create(artikal=product, naziv='Variant', sifra='BULK-V', cijena=10)
        apply_movement(product=product, variation=variation, location=self.location, tip='prijem', kolicina=4)
        key = f'{product.pk}:{variation.pk}'
        data = {'action': 'bulk_prenos_mp', 'selected': [key],
                f'quantity_{key}': '3', f'location_{key}': str(self.location.pk)}
        self.client.post(self.url, data)
        order = Order.objects.get(ime_prezime='Prenos u MP')
        self.assertEqual(order.stavke.get().varijacija_id, variation.pk)
        stock = WarehouseStock.objects.get(product=product, variation=variation, location=self.location)
        self.assertEqual((stock.kolicina, stock.rezervisano), (4, 3))
        wrong = f'{self.products[1].pk}:{variation.pk}'
        self.client.post(self.url, {'action': 'bulk_prenos_mp', 'selected': [wrong],
                f'quantity_{wrong}': '1', f'location_{wrong}': str(self.location.pk)})
        self.assertEqual(order.stavke.count(), 1)
        stock.refresh_from_db()
        self.assertEqual(stock.rezervisano, 3)

    def test_empty_duplicate_and_mp_source_are_rejected(self):
        self.client.post(self.url, {'action': 'bulk_prenos_mp'})
        data = self.payload([2])
        data['selected'] *= 2
        self.client.post(self.url, data)
        data = self.payload([2])
        data[f'location_{self.products[0].pk}:'] = str(self.mp.pk)
        self.client.post(self.url, data)
        self.assertFalse(Order.objects.filter(ime_prezime='Prenos u MP').exists())
        self.assertFalse(WarehouseStock.objects.filter(rezervisano__gt=0).exists())

    def test_bulk_controls_render_and_non_staff_cannot_transfer(self):
        page = self.client.get(self.url)
        self.assertContains(page, 'Označi sve na ovoj stranici')
        self.assertContains(page, 'data-fali-bulk-submit')
        self.assertContains(page, f'value="{self.products[0].pk}:"')
        self.client.logout()
        response = self.client.post(self.url, self.payload([2]))
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Order.objects.filter(ime_prezime='Prenos u MP').exists())
