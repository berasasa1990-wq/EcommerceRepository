from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from .models import Product, WarehouseStock, WarehouseMovement, WarehouseLocation, Order, OrderItem
from .magacin import apply_movement, refresh_catalog_qty, reserve_web_order_stock, validate_order_stock, MagacinError


class StockTrackingTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = get_user_model().objects.create_superuser('tracking-owner', 'owner@example.invalid', 'password')
        self.client.force_login(self.user)
        self.create_url = reverse('staff_magacin_brzi_unos_novi')

    def create_product(self, mode):
        response = self.client.post(self.create_url, {
            'naziv': 'Artikal ' + mode, 'cijena': '20', 'rezim_zaliha': mode,
        })
        self.assertEqual(response.status_code, 302)
        return Product.objects.get(naziv='Artikal ' + mode)

    def test_stock_mode_is_required_and_invalid_choice_rejected(self):
        self.assertContains(self.client.get(self.create_url), 'Praćenje zaliha')
        for mode in ('', 'invalid'):
            response = self.client.post(self.create_url, {'naziv': 'Nema izbora', 'cijena': '20', 'rezim_zaliha': mode})
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.context['details_form'].errors['rezim_zaliha'])
        self.assertFalse(Product.objects.filter(naziv='Nema izbora').exists())

    def test_tracked_product_starts_at_zero_until_location_receipt(self):
        product = self.create_product('tracked')
        self.assertTrue(product.pracenje_zaliha)
        self.assertEqual(product.stanje, 0)
        self.assertFalse(product.na_stanju)
        self.assertFalse(WarehouseStock.objects.filter(product=product).exists())
        location = WarehouseLocation.objects.create(sifra='TRACK-A', naziv='Lokacija')
        apply_movement(product=product, location=location, tip='prijem', kolicina=3)
        product.refresh_from_db()
        self.assertTrue(product.na_stanju)
        self.assertEqual(product.stanje, 3)

    def test_untracked_product_is_available_without_quantity_or_locations(self):
        product = self.create_product('untracked')
        self.assertFalse(product.pracenje_zaliha)
        self.assertTrue(product.na_stanju)
        self.assertEqual(product.stanje, 0)
        from .views import _home_product_queryset
        self.assertIn(product, _home_product_queryset())
        detail = self.client.get(reverse('product_detail', args=[product.slug]))
        self.assertContains(detail, 'Na stanju')
        self.assertNotContains(detail, 'product-stock-label__qty')
        warehouse = self.client.get(reverse('staff_magacin_artikal', args=[product.pk]))
        self.assertNotContains(warehouse, 'Dodaj u novu lokaciju')
        self.assertNotContains(warehouse, 'Zalihe po lokacijama')
        self.client.post(reverse('add_to_cart', args=[product.slug]), {'quantity': '7'})
        self.assertEqual(sum(item['quantity'] for item in self.client.session['cart'].values()), 7)
        self.client.get(reverse('cart'))
        self.assertEqual(sum(item['quantity'] for item in self.client.session['cart'].values()), 7)
        refresh_catalog_qty(product)
        product.refresh_from_db()
        self.assertTrue(product.na_stanju)
        self.assertFalse(WarehouseStock.objects.filter(product=product).exists())
        self.client.post(reverse('staff_magacin_artikal', args=[product.pk]), {'action': 'skini'})
        product.refresh_from_db()
        self.assertFalse(product.na_stanju)
        unavailable = self.client.get(reverse('cart'))
        self.assertTrue(unavailable.context['cart_items'][0]['sold_out'])
        rejected = self.client.post(reverse('checkout'), {'ime_prezime': 'Kupac'})
        self.assertRedirects(rejected, reverse('cart'), fetch_redirect_response=False)
        self.assertFalse(Order.objects.exists())
        refresh_catalog_qty(product)
        product.refresh_from_db()
        self.assertFalse(product.na_stanju)
        self.client.post(reverse('staff_magacin_artikal', args=[product.pk]), {'action': 'ubaci'})
        product.refresh_from_db()
        self.assertTrue(product.na_stanju)

    def test_untracked_order_can_be_reserved_picked_and_finished_without_stock(self):
        from .views_magacin import _order_pick_bundle, apply_order_pick
        product = self.create_product('untracked')
        order = Order.objects.create(ime_prezime='Kupac', ukupno=100)
        OrderItem.objects.create(narudzba=order, artikal=product, naziv=product.naziv, cijena=20, kolicina=5)
        reserve_web_order_stock(order)
        queue, _, error = _order_pick_bundle(order)
        self.assertFalse(error)
        self.assertEqual(queue[0]['loc'], 'Bez praćenja')
        apply_order_pick(order, [dict(row, got=row['need'], done=True) for row in queue], finalize=True)
        validate_order_stock(order)
        product.refresh_from_db()
        self.assertTrue(product.na_stanju)
        self.assertEqual(product.stanje, 0)
        self.assertFalse(WarehouseStock.objects.filter(product=product).exists())
        self.assertFalse(WarehouseMovement.objects.filter(product=product).exists())
        self.assertFalse(order.magacin_holds.exists())

    def test_untracked_product_rejects_physical_stock_movements(self):
        product = self.create_product('untracked')
        location = WarehouseLocation.objects.create(sifra='NO-TRACK', naziv='Lokacija')
        with self.assertRaises(MagacinError):
            apply_movement(product=product, location=location, tip='prijem', kolicina=5)
        self.assertFalse(WarehouseStock.objects.filter(product=product).exists())
        self.assertFalse(WarehouseMovement.objects.filter(product=product).exists())
