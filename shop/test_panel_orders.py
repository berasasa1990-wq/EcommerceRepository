from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from .models import Order


class PanelOrdersTests(TestCase):
    def setUp(self):
        self.manager = get_user_model().objects.create_user('orders-manager', is_staff=True)

    def test_badge_counts_only_new_orders_on_panel_and_edit_pages(self):
        for status in ['placed', 'placed', 'draft', 'completed', 'cancelled']:
            Order.objects.create(status=status)
        self.client.force_login(self.manager)
        for url in [reverse('panel'), reverse('panel_section', args=['narudzbe']),
                    reverse('panel_add', args=['brendovi'])]:
            response = self.client.get(url)
            self.assertContains(response, 'aria-label="2 novih neposlatih narudžbi"')

    def test_orders_show_customer_details_and_hide_drafts(self):
        Order.objects.create(customer_name='Novi kupac', status='placed')
        Order.objects.create(customer_name='Kupac u pripremi', status='draft')
        self.client.force_login(self.manager)
        response = self.client.get(reverse('panel_section', args=['narudzbe']))
        self.assertContains(response, 'Novi kupac')
        self.assertNotContains(response, 'Kupac u pripremi')

    def test_orders_require_panel_access(self):
        response = self.client.get(reverse('panel_section', args=['narudzbe']))
        self.assertEqual(response.status_code, 302)
        self.assertIn('/panel/prijava/', response.url)

    def test_packing_marks_order_shipped_and_updates_badge(self):
        customer = get_user_model().objects.create_user('order-customer')
        order = Order.objects.create(customer=customer, status='placed')
        url = reverse('panel_section', args=['narudzbe'])
        self.client.force_login(self.manager)
        response = self.client.post(url, {'order_id': order.pk, 'action': 'pack'}, follow=True)
        order.refresh_from_db()
        self.assertEqual(order.status, 'shipped')
        self.assertContains(response, 'aria-label="0 novih neposlatih narudžbi"')
        self.assertNotContains(response, 'class="panel-pack-button"')
        self.client.force_login(customer)
        response = self.client.get(reverse('account'))
        self.assertContains(response, 'Poslato')

    def test_packing_does_not_change_cancelled_orders(self):
        order = Order.objects.create(status='cancelled')
        self.client.force_login(self.manager)
        self.client.post(reverse('panel_section', args=['narudzbe']), {'order_id': order.pk, 'action': 'pack'})
        order.refresh_from_db()
        self.assertEqual(order.status, 'cancelled')

    def test_customer_cannot_pack_order(self):
        customer = get_user_model().objects.create_user('restricted-customer')
        order = Order.objects.create(customer=customer)
        self.client.force_login(customer)
        response = self.client.post(reverse('panel_section', args=['narudzbe']), {'order_id': order.pk, 'action': 'pack'})
        self.assertEqual(response.status_code, 302)
        order.refresh_from_db()
        self.assertEqual(order.status, 'placed')

    def test_order_filters_and_sorting(self):
        older = Order.objects.create(status='placed')
        newer = Order.objects.create(status='placed')
        shipped = Order.objects.create(status='shipped')
        completed = Order.objects.create(status='completed')
        self.client.force_login(self.manager)
        url = reverse('panel_section', args=['narudzbe'])
        for query, expected in [('', [newer, older]), ('?status=nove', [newer, older]),
                                ('?status=poslate', [shipped]),
                                ('?status=sve', [completed, shipped, newer, older]),
                                ('?status=invalid', [newer, older])]:
            response = self.client.get(url + query)
            self.assertEqual(list(response.context['orders']), expected)

    def test_order_preview_has_images_snapshot_prices_and_customer_details(self):
        from .models import Product, OrderItem
        product = Product.objects.create(name='Artikal sa slikom', price=99, image='products/preview.avif')
        order = Order.objects.create(customer_name='Pregled kupca', address='Ulica 7', phone='061123456', total=48, delivery_price=10, loyalty_discount=2)
        OrderItem.objects.create(order=order, product=product, quantity=2, unit_price=20)
        self.client.force_login(self.manager)
        url = reverse('panel_section', args=['narudzbe'])
        response = self.client.get(url)
        self.assertContains(response, f'data-order-preview="{order.pk}"')
        response = self.client.get(url, {'preview': order.pk})
        self.assertContains(response, 'Pregled kupca')
        self.assertContains(response, 'Ulica 7')
        self.assertContains(response, 'products/preview.avif')
        self.assertEqual(response.context['subtotal'], 40)
        self.assertEqual(response.context['items'][0].line_total, 40)
        self.assertIn('no-store', response['Cache-Control'])

    def test_order_preview_requires_staff_and_rejects_drafts(self):
        url = reverse('panel_section', args=['narudzbe'])
        order = Order.objects.create(status='draft')
        self.assertEqual(self.client.get(url, {'preview': order.pk}).status_code, 302)
        customer = get_user_model().objects.create_user('preview-restricted')
        self.client.force_login(customer)
        self.assertEqual(self.client.get(url, {'preview': order.pk}).status_code, 302)
        self.client.force_login(self.manager)
        self.assertEqual(self.client.get(url, {'preview': order.pk}).status_code, 404)
        self.assertEqual(self.client.get(url, {'preview': 'invalid'}).status_code, 404)

    def test_backup_list_is_paginated(self):
        from unittest.mock import patch
        from django.utils import timezone

        copies = [{'name': f'copy-{index}', 'created': timezone.now(), 'size': 100, 'safety': False}
                  for index in range(23)]
        self.client.force_login(self.manager)
        with patch('shop.backups.prune_backups'), patch('shop.backups.list_backups', return_value=copies):
            url = reverse('panel_section', args=['backup'])
            first = self.client.get(url)
            self.assertEqual(len(first.context['backups']), 10)
            self.assertContains(first, '?page=2')
            last = self.client.get(url, {'page': 3})
            self.assertEqual(len(last.context['backups']), 3)
            self.assertContains(last, 'Stranica 3 od 3')
            self.assertContains(last, '?page=2')

    def test_preview_shows_saved_promotion_after_product_changes(self):
        from .models import Product, OrderItem
        product = Product.objects.create(name='Akcijski artikal', price=100)
        order = Order.objects.create(customer_name='Kupac')
        OrderItem.objects.create(order=order, product=product, quantity=1, unit_price=80,
                                 promotion='Bundle set: Ljetni set (−20%)')
        self.client.force_login(self.manager)
        url = reverse('panel_section', args=['narudzbe'])
        self.assertContains(self.client.get(url, {'preview': order.pk}), 'Bundle set: Ljetni set (−20%)')
        self.assertNotContains(self.client.get(url, {'preview': order.pk, 'customer_only': '1'}), 'Bundle set: Ljetni set')
