from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import urlsplit

from django.contrib.auth import get_user_model
from django.template.loader import render_to_string
from django.test import Client, RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .cart import Cart
from .loyalty import validiraj_kupon
from .models import Coupon, Order, ViberKuponDodjela
from .pricing import izracunaj_sazetak
from .views import staff_online_orders, staff_orders_validation, staff_recent_web_buyers


class WebOrdersOnlyTests(TestCase):
    @override_settings(
        ALLOWED_HOSTS=['testserver'], SITE_PREP_ENABLED=False,
        STORAGES={
            'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
            'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
        },
    )
    def test_viber_coupon_is_assigned_to_the_buyer_and_redeemable_in_cart(self):
        users = get_user_model()
        buyer = users.objects.create_user(username='kupac-viber')
        admin = users.objects.create_superuser(username='admin-viber')
        self.web.korisnik = buyer
        self.web.save(update_fields=['korisnik'])
        registered_url = reverse('staff_assign_web_buyer_coupon', args=[self.web.pk])
        guest_order = Order.objects.create(ime_prezime='Gost', telefon='061123456', ukupno=45)
        guest_url = reverse('staff_assign_web_buyer_coupon', args=[guest_order.pk])
        data = {'kod': 'viber-15', 'postotak': '15,50'}
        self.client.force_login(buyer)
        self.assertEqual(self.client.post(registered_url, data).status_code, 302)
        self.client.force_login(admin)
        self.assertEqual(self.client.post(registered_url, {**data, 'postotak': '101'}).status_code, 400)
        for _ in range(2):
            result = self.client.post(registered_url, data)
            self.assertEqual(result.json(), {'code': 'VIBER-15', 'percent': '15.5', 'claim_url': ''})
        coupon = Coupon.objects.get(kod='VIBER-15')
        self.assertEqual(validiraj_kupon(coupon.kod, buyer), (coupon, None))
        self.assertIsNone(validiraj_kupon(coupon.kod, admin)[0])
        registered_buyer = Client()
        registered_buyer.force_login(buyer)
        self.assertContains(registered_buyer.post(reverse('apply_coupon'), {'choose_reward': '1'}), 'VIBER-15')
        self.assertEqual(self.client.post(guest_url, data).status_code, 409)
        self.assertEqual(self.client.post(reverse('staff_assign_web_buyer_coupon', args=[self.manual.pk]), data).status_code, 404)

        guest_coupon = self.client.post(guest_url, {**data, 'kod': 'gost-15'}).json()
        self.assertEqual(ViberKuponDodjela.objects.count(), 2)
        guest = Client()
        apply_url = reverse('apply_coupon')
        guest.post(apply_url, {'kod': guest_coupon['code']})
        self.assertNotIn(Cart.COUPON_KEY, guest.session)
        claim_path = urlsplit(guest_coupon['claim_url']).path
        self.assertEqual(guest.get(claim_path.replace('/viber/', '/viber/x')).status_code, 404)
        self.assertRedirects(guest.get(claim_path), reverse('cart'), fetch_redirect_response=False)
        choice = guest.post(apply_url, {'choose_reward': '1'})
        self.assertContains(choice, 'GOST-15')
        coupon_box = render_to_string('partials/coupon_form.html', {'coupon_choices': choice.context['coupons']})
        modal = render_to_string('partials/coupon_choice_modal.html', {'coupon_choices': choice.context['coupons']})
        self.assertIn('data-coupon-choice-open', coupon_box)
        self.assertIn('GOST-15', modal)
        expires = Coupon.objects.get(kod='GOST-15').kreiran + timedelta(days=7)
        expiry_label = f'Vrijedi do {timezone.localtime(expires):%d.%m.%Y. %H:%M}'
        self.assertContains(choice, expiry_label)
        self.assertIn(expiry_label, modal)
        guest.post(apply_url, {'kod': guest_coupon['code']})
        self.assertEqual(guest.session[Cart.COUPON_KEY], 'GOST-15')
        summary = izracunaj_sazetak(
            Decimal('100.00'), coupon_code=guest.session[Cart.COUPON_KEY],
            viber_claims=guest.session[Cart.VIBER_COUPON_CLAIMS_KEY],
        )
        self.assertTrue(summary['kupon_primijenjen'])
        self.assertEqual(summary['kupon_kod'], 'GOST-15')

        before_expiry = timezone.now() - timedelta(days=7) + timedelta(minutes=1)
        guest_code = guest_coupon['code']
        Coupon.objects.filter(kod__in=['VIBER-15', guest_code]).update(kreiran=before_expiry)
        self.assertContains(guest.post(apply_url, {'choose_reward': '1'}), guest_code)
        with patch('EcommerceApp.models.timezone.now', return_value=before_expiry + timedelta(days=7)):
            self.assertNotContains(guest.post(apply_url, {'choose_reward': '1'}), guest_code)
            self.assertIsNone(validiraj_kupon('VIBER-15', buyer)[0])
            summary = izracunaj_sazetak(
                Decimal('100.00'), coupon_code=guest_code,
                viber_claims=guest.session[Cart.VIBER_COUPON_CLAIMS_KEY],
            )
            self.assertFalse(summary['kupon_primijenjen'])
            late_guest = Client()
            self.assertRedirects(late_guest.get(claim_path), reverse('cart'), fetch_redirect_response=False)
            self.assertNotIn(Cart.VIBER_COUPON_CLAIMS_KEY, late_guest.session)
            self.assertEqual(self.client.post(registered_url, data).status_code, 409)

    def test_recent_web_buyers_show_twenty_distinct_customers_and_latest_purchase(self):
        for index in range(22):
            Order.objects.create(ime_prezime=f'Kupac {index:02}', telefon=f'061{index:06}', ukupno=25)
        Order.objects.create(
            ime_prezime='Zadnja kupovina', telefon='+387 61 000 021', ukupno=87,
            status=Order.Status.ZAVRSENA, popust_detalji=[{'placanje': 'kartica'}],
        )
        Order.objects.create(
            ime_prezime='Otkazana kupovina', telefon='061555555', ukupno=15, status=Order.Status.OTKAZANA,
        )
        request = RequestFactory().get('/nalog/narudzbe/kupci/', HTTP_HOST='localhost')
        request.user = SimpleNamespace(is_authenticated=True, is_superuser=False)
        self.assertEqual(staff_recent_web_buyers(request).status_code, 302)
        request.user.is_superuser = True
        response = staff_recent_web_buyers(request)
        self.assertIn('no-store', response['Cache-Control'])
        self.assertContains(response, 'data-buyer-viber=', count=20)
        self.assertContains(response, 'Zadnja kupovina')
        self.assertContains(response, '87.00 KM')
        self.assertContains(response, 'Karticom')
        self.assertContains(response, 'Pouzećem')
        self.assertContains(response, 'viber://chat?number=%2B38761000021')
        for excluded in ('Kupac 21', 'Kupac 01', 'Otkazana kupovina', self.manual.ime_prezime, self.transfer.ime_prezime):
            self.assertNotContains(response, excluded)

    def setUp(self):
        self.web = Order.objects.create(ime_prezime='Web kupac', ukupno=25)
        self.manual = Order.objects.create(ime_prezime='Ručni kupac', ukupno=40, izvor=Order.Izvor.MAGACIN)
        self.transfer = Order.objects.create(ime_prezime='Prenos u MP', ukupno=60,
                                             izvor=Order.Izvor.MAGACIN, pick_state={'kind': 'prenos_mp'})
        self.legacy_transfer = Order.objects.create(ime_prezime='Prenos u MP', ukupno=80)
        self.marked_transfer = Order.objects.create(ime_prezime='MP dokument', ukupno=90, pick_state={'kind': 'prenos_mp'})

    def context(self, view, params=None):
        request = RequestFactory().get('/nalog/narudzbe/', params or {})
        request.user = SimpleNamespace(is_authenticated=True, is_superuser=True)
        with patch('EcommerceApp.views.render', side_effect=lambda r, t, c: c), patch('EcommerceApp.views._base_context', return_value={}):
            return view(request)

    def test_list_count_total_and_search_only_include_web_orders(self):
        context = self.context(staff_orders_validation)
        self.assertEqual([o.pk for o in context['orders']], [self.web.pk])
        self.assertEqual(context['pending_count'], 1)
        self.assertEqual(context['pending_total'], Decimal('25'))
        for params in ({'q': 'Prenos'}, {'source': 'magacin'}):
            self.assertEqual(list(self.context(staff_orders_validation, params)['orders']), [])

    def test_legacy_online_list_and_search_exclude_transfers(self):
        context = self.context(staff_online_orders, {'filter': 'sve'})
        self.assertEqual([o.pk for o in context['orders']], [self.web.pk])
        self.assertEqual(context['nova_count'], 1)
        self.assertEqual(self.context(staff_online_orders, {'q': self.transfer.broj})['orders'], [])


    def test_transfer_document_label_including_legacy_records(self):
        for order in (self.transfer, self.legacy_transfer, self.marked_transfer):
            self.assertTrue(order.is_mp_transfer)
            self.assertEqual(order.display_customer_name, 'Prenosnica u MP')
            html = render_to_string('partials/order_invoice_document.html', {'order': order})
            self.assertIn('Prenosnica u MP br.', html)
            self.assertNotIn('Online Narudžbe', html)
        self.assertFalse(self.web.is_mp_transfer)
        self.assertEqual(self.web.display_customer_name, 'Web kupac')
