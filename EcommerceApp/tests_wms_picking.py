from unittest.mock import patch
from urllib.parse import urlsplit

from django.contrib.auth import get_user_model
from django.core import signing
from django.http import Http404
from django.template.loader import render_to_string
from django.test import Client, RequestFactory, TestCase, override_settings
from django.urls import resolve

from .magacin import MagacinError, apply_movement, reserve_for_order
from .models import Order, OrderItem, Product, WarehouseLocation, WarehouseStock
from .wms import SALT, complete_paper_picking, paper_picking_job, picking_plan, validate_qr


@override_settings(SECURE_SSL_REDIRECT=False, SITE_PREP_ENABLED=False, STORAGES={
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
})
class PaperPickingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_superuser('paper-admin', 'paper@example.com', 'password')

    def setUp(self):
        self.product = Product.objects.create(naziv='Štap za ribolov', sifra='STAP-1', cijena=10)
        self.a = WarehouseLocation.objects.create(sifra='A-01', naziv='Polica A')
        self.b = WarehouseLocation.objects.create(sifra='B-02', naziv='Polica B')
        apply_movement(product=self.product, location=self.a, tip='prijem', kolicina=2)
        apply_movement(product=self.product, location=self.b, tip='prijem', kolicina=4)
        self.order = Order.objects.create(ime_prezime='Picking kupac', ukupno=50, medjuzbir=50, izvor=Order.Izvor.MAGACIN)
        self.item = OrderItem.objects.create(narudzba=self.order, artikal=self.product, naziv=self.product.naziv,
                                            sifra=self.product.sifra, cijena=10, kolicina=5)
        reserve_for_order(self.order, self.product, 2, location=self.a, user=self.user)
        reserve_for_order(self.order, self.product, 3, location=self.b, user=self.user)
        self.order.refresh_from_db()

    def request(self, method='get', data=None):
        request = getattr(RequestFactory(), method)('/nalog/magacin/wms/', data if data is not None else ({'action': 'all'} if method == 'post' else {}))
        request.user = self.user
        return request

    def token(self):
        return signing.dumps({'order': self.order.pk, 'digest': picking_plan(self.order)['digest']}, salt=SALT)

    def stock(self):
        return list(WarehouseStock.objects.filter(product=self.product).order_by('location__sifra').values_list('kolicina', flat=True))

    def test_exact_location_quantities_and_printed_order_reference(self):
        job = paper_picking_job(self.request(), self.order)
        self.assertEqual([(r['loc'], r['need']) for r in job['rows']], [('A-01', 2), ('B-02', 3)])
        self.assertTrue(job['qr'].startswith('data:image/png;base64,'))
        html = render_to_string('staff/partials/order_print_job.html', {'job': {'order': self.order, 'wms_picking': job}})
        self.assertIn('Picking lista · Narudžba #' + self.order.broj, html)
        self.assertIn('STAP-1', html)
        self.assertIn('A-01', html)
        self.assertIn('B-02', html)
        self.assertIn('wms-picking-sheet', html)
        self.assertLess(html.index('invoice-sheet'), html.index('wms-picking-sheet'))

    def test_qr_validation_deducts_exact_locations_and_is_idempotent(self):
        digest = picking_plan(self.order)['digest']
        order, changed = complete_paper_picking(self.order.pk, digest, self.user)
        self.assertTrue(changed)
        self.assertEqual(order.lager_status, Order.LagerStatus.VALIDIRANO)
        self.assertEqual(order.status, Order.Status.ZAVRSENA)
        self.assertEqual(self.stock(), [0, 1])
        self.item.refresh_from_db()
        self.assertEqual(self.item.kolicina_pokupljeno, 5)
        _, changed = complete_paper_picking(self.order.pk, digest, self.user)
        self.assertFalse(changed)
        self.assertEqual(self.stock(), [0, 1])

    def test_changed_order_rejects_old_paper(self):
        digest = picking_plan(self.order)['digest']
        OrderItem.objects.filter(pk=self.item.pk).update(kolicina=4)
        with self.assertRaisesMessage(MagacinError, 'promijenjene'):
            complete_paper_picking(self.order.pk, digest, self.user)
        self.assertEqual(self.stock(), [2, 4])

    def test_row_order_does_not_invalidate_printed_qr(self):
        from .views_magacin import _order_pick_bundle

        digest = picking_plan(self.order)['digest']
        queue, groups, error = _order_pick_bundle(self.order)
        with patch('EcommerceApp.views_magacin._order_pick_bundle',
                   return_value=(list(reversed(queue)), groups, error)):
            self.assertEqual(picking_plan(self.order)['digest'], digest)
            order, changed = complete_paper_picking(self.order.pk, digest, self.user)
        self.assertTrue(changed)
        self.assertEqual(order.lager_status, Order.LagerStatus.VALIDIRANO)
        self.assertEqual(self.stock(), [0, 1])

    def test_database_row_order_does_not_change_location_allocation(self):
        from django.db import connection

        if connection.vendor != 'sqlite':
            self.skipTest('SQLite unordered-query reversal reproduces database row ordering changes.')
        # Two lines for one product share the same location reservations.
        self.item.kolicina = 3
        self.item.save(update_fields=['kolicina'])
        OrderItem.objects.create(narudzba=self.order, artikal=self.product,
                                 naziv=self.product.naziv, sifra=self.product.sifra,
                                 cijena=10, kolicina=2)
        digest = picking_plan(self.order)['digest']
        with connection.cursor() as cursor:
            cursor.execute('PRAGMA reverse_unordered_selects = ON')
        try:
            self.assertEqual(picking_plan(Order.objects.get(pk=self.order.pk))['digest'], digest)
            order, changed = complete_paper_picking(self.order.pk, digest, self.user)
        finally:
            with connection.cursor() as cursor:
                cursor.execute('PRAGMA reverse_unordered_selects = OFF')
        self.assertTrue(changed)
        self.assertEqual(order.lager_status, Order.LagerStatus.VALIDIRANO)
        self.assertEqual(self.stock(), [0, 1])

    def test_validation_failure_rolls_back_pick_and_stock(self):
        digest = picking_plan(self.order)['digest']
        with patch('EcommerceApp.wms.validate_order_stock', side_effect=MagacinError('Greška')):
            with self.assertRaises(MagacinError):
                complete_paper_picking(self.order.pk, digest, self.user)
        self.order.refresh_from_db()
        self.item.refresh_from_db()
        self.assertEqual(self.order.pick_state, {})
        self.assertIsNone(self.item.kolicina_pokupljeno)
        self.assertEqual(self.stock(), [2, 4])

    def test_scan_get_does_not_mutate_and_post_validates(self):
        token = self.token()
        response = validate_qr(self.request(), token)
        self.assertContains(response, 'Da li su svi artikli sa liste pokupljeni?')
        self.assertContains(response, 'data-scan-new')
        self.assertContains(response, 'Redoslijed nije bitan.')
        self.assertContains(response, 'staff-wms-qr.js')
        self.assertEqual(self.stock(), [2, 4])
        response = validate_qr(self.request('post'), token)
        self.assertContains(response, 'je validatovana.')
        self.assertEqual(self.stock(), [0, 1])

    def test_printed_orders_sharing_stock_validate_in_reverse_order(self):
        from .magacin import release_holds_for_product

        release_holds_for_product(self.order, self.product, user=self.user)
        self.item.kolicina = 3
        self.item.save(update_fields=['kolicina'])
        other = Order.objects.create(ime_prezime='Druga picking narudžba', ukupno=30,
                                     medjuzbir=30, izvor=Order.Izvor.MAGACIN)
        OrderItem.objects.create(narudzba=other, artikal=self.product, naziv=self.product.naziv,
                                 sifra=self.product.sifra, cijena=10, kolicina=3)
        tokens = []
        for order in (self.order, other):
            with patch('EcommerceApp.views_magacin._etiketa_qr_data_uri', return_value='qr') as qr:
                paper_picking_job(self.request(), order)
            tokens.append(resolve(urlsplit(qr.call_args.args[0]).path).kwargs['token'])
        self.assertEqual(self.stock(), [2, 4])  # Printing reserves, never deducts.
        printed_digest = picking_plan(self.order)['digest']
        response = validate_qr(self.request('post'), tokens[1])
        self.assertContains(response, 'je validatovana.')
        self.order.refresh_from_db()
        self.assertNotEqual(self.order.lager_status, Order.LagerStatus.VALIDIRANO)
        self.assertEqual(picking_plan(self.order)['digest'], printed_digest)
        response = validate_qr(self.request('post'), tokens[0])
        self.assertContains(response, 'je validatovana.')
        self.assertEqual(self.stock(), [0, 0])

    def test_reprinting_does_not_duplicate_reservations(self):
        from .magacin import release_holds_for_product

        release_holds_for_product(self.order, self.product, user=self.user)
        paper_picking_job(self.request(), self.order)
        digest = picking_plan(self.order)['digest']
        holds = list(self.order.magacin_holds.values_list('pk', 'kolicina', 'status'))
        paper_picking_job(self.request(), self.order)
        self.assertEqual(list(self.order.magacin_holds.values_list('pk', 'kolicina', 'status')), holds)
        self.assertEqual(picking_plan(self.order)['digest'], digest)
        self.assertEqual(self.stock(), [2, 4])

    def test_tampered_code_and_non_admin_are_denied(self):
        with self.assertRaises(Http404):
            validate_qr(self.request('post'), self.token() + 'x')
        request = self.request('post')
        request.user = get_user_model().objects.create_user('warehouse-viewer')
        self.assertEqual(validate_qr(request, self.token()).status_code, 302)
        self.assertEqual(self.stock(), [2, 4])

    def test_qr_uses_signed_application_url(self):
        with patch('EcommerceApp.views_magacin._etiketa_qr_data_uri', return_value='qr') as qr:
            paper_picking_job(self.request(), self.order)
        route = resolve(urlsplit(qr.call_args.args[0]).path)
        self.assertEqual(route.url_name, 'staff_magacin_wms_validate')
        self.assertEqual(signing.loads(route.kwargs['token'], salt=SALT)['order'], self.order.pk)

    def test_stock_reallocation_rejects_old_qr(self):
        digest = picking_plan(self.order)['digest']
        WarehouseStock.objects.filter(product=self.product, location=self.a).update(kolicina=0)
        with self.assertRaises(MagacinError):
            complete_paper_picking(self.order.pk, digest, self.user)
        self.order.refresh_from_db()
        self.assertNotEqual(self.order.lager_status, Order.LagerStatus.VALIDIRANO)

    def test_cancelled_order_cannot_be_validated(self):
        digest = picking_plan(self.order)['digest']
        Order.objects.filter(pk=self.order.pk).update(status=Order.Status.OTKAZANA)
        with self.assertRaises(MagacinError):
            complete_paper_picking(self.order.pk, digest, self.user)
        self.assertEqual(self.stock(), [2, 4])

    def test_another_workers_claim_is_respected(self):
        other = get_user_model().objects.create_user('other-picker')
        self.order.pick_claimed_by = other
        self.order.save(update_fields=['pick_claimed_by'])
        digest = picking_plan(self.order)['digest']
        with self.assertRaisesMessage(MagacinError, 'već preuzeo'):
            complete_paper_picking(self.order.pk, digest, self.user)
        self.assertEqual(self.stock(), [2, 4])

    def test_webshop_scan_confirms_picking_before_validating(self):
        self.order.izvor = Order.Izvor.WEBSHOP
        self.order.save(update_fields=['izvor'])
        digest = picking_plan(self.order)['digest']
        order, changed = complete_paper_picking(self.order.pk, digest, self.user)
        self.assertTrue(changed)
        self.assertEqual(order.lager_status, Order.LagerStatus.VALIDIRANO)
        self.assertTrue(order.stanje_skinuto)
        self.assertEqual(self.stock(), [0, 1])

    def test_post_requires_csrf(self):
        from django.urls import reverse
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        response = client.post(reverse('staff_magacin_wms_validate', kwargs={'token': self.token()}))
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.stock(), [2, 4])

    def test_post_without_yes_or_final_confirmation_does_not_validate(self):
        response = validate_qr(self.request('post', {}), self.token())
        self.assertContains(response, 'Da li su svi artikli sa liste pokupljeni?')
        self.assertEqual(self.stock(), [2, 4])

    def test_no_opens_search_limited_to_this_order(self):
        Order.objects.create(ime_prezime='Drugi kupac', ukupno=10)
        response = validate_qr(self.request(data={'mode': 'missing', 'q': 'STAP'}), self.token())
        self.assertContains(response, self.product.naziv)
        self.assertContains(response, 'Pronađi artikal')
        self.assertEqual(self.stock(), [2, 4])

    def test_save_shortage_is_staged_then_final_confirmation_updates_invoice(self):
        from .pricing import pripremi_stavke_za_racun
        token = self.token()
        data = {'action': 'save', 'item': str(self.item.pk), 'found_0': '1', 'found_1': '0'}
        with patch('EcommerceApp.wms.render', side_effect=lambda request, template, context, **kw: context):
            context = validate_qr(self.request('post', data), token)
        self.assertTrue(context['saved'])
        self.assertEqual(context['summary'][0]['found'], 1)
        self.item.refresh_from_db()
        self.assertIsNone(self.item.kolicina_pokupljeno)
        self.assertEqual(self.stock(), [2, 4])
        response = validate_qr(self.request('post', {'action': 'complete', 'changes': context['changes']}), token)
        self.assertContains(response, 'je validatovana.')
        self.assertEqual(self.stock(), [1, 4])
        self.item.refresh_from_db()
        self.assertEqual(self.item.kolicina_faktura, 1)
        self.order.refresh_from_db()
        self.assertEqual(self.order.medjuzbir, 10)
        self.assertEqual(pripremi_stavke_za_racun(self.order)[0]['kolicina'], 1)

    def test_zero_stays_on_invoice_without_charging_or_deducting(self):
        from .pricing import pripremi_stavke_za_racun, sazetak_iz_narudzbe
        plan = picking_plan(self.order)
        quantities = {row['key']: 0 for row in plan['rows']}
        order, _ = complete_paper_picking(self.order.pk, plan['digest'], self.user, quantities)
        self.item.refresh_from_db()
        self.assertTrue(self.item.wms_out_of_stock)
        self.assertEqual(self.item.kolicina_faktura, 0)
        self.assertEqual(self.stock(), [2, 4])
        items = pripremi_stavke_za_racun(order)
        self.assertEqual(len(items), 1)
        self.assertTrue(items[0]['out_of_stock'])
        self.assertEqual(items[0]['ukupno'], 0)
        self.assertEqual(order.ukupno, 0)
        self.assertEqual(sazetak_iz_narudzbe(order)['ukupno'], 0)
        html = render_to_string('staff/partials/order_preview.html', {'order': order, 'stavke': items, 'summary': {}, 'print_preview': True})
        self.assertIn('Nema na stanju', html)
        self.assertIn(self.item.naziv, html)

    def test_webshop_zero_is_preserved(self):
        from .pricing import pripremi_stavke_za_racun
        self.order.izvor = Order.Izvor.WEBSHOP
        self.order.save(update_fields=['izvor'])
        plan = picking_plan(self.order)
        order, _ = complete_paper_picking(self.order.pk, plan['digest'], self.user, {r['key']: 0 for r in plan['rows']})
        self.assertTrue(order.stavke.filter(pk=self.item.pk).exists())
        self.assertTrue(pripremi_stavke_za_racun(order)[0]['out_of_stock'])
        self.assertEqual(order.ukupno, 0)
        self.assertEqual(self.stock(), [2, 4])

    def test_mixed_zero_and_picked_items_charge_only_found_goods(self):
        from .pricing import pripremi_stavke_za_racun
        other = Product.objects.create(naziv='Udice', sifra='UD-2', cijena=10)
        apply_movement(product=other, location=self.a, tip='prijem', kolicina=2)
        OrderItem.objects.create(narudzba=self.order, artikal=other, naziv=other.naziv, sifra=other.sifra, cijena=10, kolicina=1)
        reserve_for_order(self.order, other, 1, location=self.a, user=self.user)
        plan = picking_plan(self.order)
        quantities = {row['key']: 0 for row in plan['rows'] if row['item_id'] == self.item.pk}
        order, _ = complete_paper_picking(self.order.pk, plan['digest'], self.user, quantities)
        self.assertEqual(order.medjuzbir, 10)
        self.assertEqual(len(pripremi_stavke_za_racun(order)), 2)
        self.assertEqual(self.stock(), [2, 4])
        self.assertEqual(WarehouseStock.objects.get(product=other, location=self.a).kolicina, 1)

    def test_invalid_found_quantities_and_tampered_changes_are_rejected(self):
        plan = picking_plan(self.order)
        for quantity in (-1, 99):
            with self.subTest(quantity=quantity), self.assertRaises(MagacinError):
                complete_paper_picking(self.order.pk, plan['digest'], self.user, {plan['rows'][0]['key']: quantity})
        response = validate_qr(self.request('post', {'action': 'complete', 'changes': 'tampered'}), self.token())
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.stock(), [2, 4])

    def test_no_stock_can_be_confirmed_as_zero(self):
        from .pricing import pripremi_stavke_za_racun
        self.order.magacin_holds.all().delete()
        WarehouseStock.objects.filter(product=self.product).update(kolicina=0, rezervisano=0)
        plan = picking_plan(self.order)
        self.assertTrue(plan['error'])
        self.assertTrue(any(row.get('missing') for row in plan['rows']))
        order, _ = complete_paper_picking(self.order.pk, plan['digest'], self.user, {r['key']: 0 for r in plan['rows']})
        self.assertEqual(order.ukupno, 0)
        self.assertTrue(pripremi_stavke_za_racun(order)[0]['out_of_stock'])

    def test_qr_completion_moves_order_to_validated_wms_view(self):
        import json
        from .views_magacin import magacin_wms
        plan = picking_plan(self.order)
        response = validate_qr(self.request('post', {'action': 'all'}), self.token())
        self.assertContains(response, '✓ Validatovana')
        self.assertContains(response, '?validirane=1')
        state = json.loads(magacin_wms(self.request(data={'wms_state': '1'})).content)
        self.assertNotIn(self.order.pk, state['pending_ids'])
        self.assertEqual(state['validated_count'], 1)
        with patch('EcommerceApp.views_magacin._magacin_context', return_value={}), patch(
            'EcommerceApp.views_magacin.render', side_effect=lambda r, t, c: c
        ):
            validated = magacin_wms(self.request(data={'validirane': '1'}))
        self.assertEqual([o.pk for o in validated['orders']], [self.order.pk])

    def test_https_qr_confirmation_preserves_referer_and_validates_once(self):
        import re
        from django.urls import reverse
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        url = reverse('staff_magacin_wms_validate', kwargs={'token': self.token()})
        page = client.get(url, secure=True)
        self.assertEqual(page.status_code, 200)
        self.assertEqual(page['Referrer-Policy'], 'same-origin')
        self.assertIn('no-store', page['Cache-Control'])
        self.assertContains(page, 'data-csrf-refresh-url=')
        self.assertContains(page, 'js/csrf-session.js')
        token = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', page.content.decode()).group(1)
        referer = 'https://testserver' + url
        response = client.post(url, {'action': 'all', 'csrfmiddlewaretoken': token},
                               secure=True, HTTP_REFERER=referer)
        self.assertEqual(response.status_code, 200)
        self.order.refresh_from_db()
        self.assertEqual(self.order.lager_status, Order.LagerStatus.VALIDIRANO)
        self.assertEqual(self.stock(), [0, 1])
        repeated = client.post(url, {'action': 'all', 'csrfmiddlewaretoken': token},
                               secure=True, HTTP_REFERER=referer)
        self.assertEqual(repeated.status_code, 200)
        self.assertEqual(self.stock(), [0, 1])

    def test_qr_confirmation_refreshes_token_after_login_rotation(self):
        from django.middleware.csrf import _get_new_csrf_string
        from django.urls import reverse
        from django.conf import settings
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        url = reverse('staff_magacin_wms_validate', kwargs={'token': self.token()})
        client.get(url, secure=True)
        # Another tab may rotate the cookie while the scanned QR page is open.
        client.cookies[settings.CSRF_COOKIE_NAME] = _get_new_csrf_string()
        refresh = client.get(reverse('auth_csrf_token'), secure=True)
        response = client.post(url, {'action': 'all', 'csrfmiddlewaretoken': refresh.json()['csrfToken']},
                               secure=True, HTTP_REFERER='https://testserver' + url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.stock(), [0, 1])

    def test_qr_confirmation_still_rejects_cross_origin_requests(self):
        from django.urls import reverse
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        url = reverse('staff_magacin_wms_validate', kwargs={'token': self.token()})
        token = client.get(reverse('auth_csrf_token'), secure=True).json()['csrfToken']
        response = client.post(url, {'action': 'all', 'csrfmiddlewaretoken': token}, secure=True,
                               HTTP_ORIGIN='https://other.example', HTTP_REFERER='https://other.example/')
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.stock(), [2, 4])

    def test_validated_page_offers_next_scan_and_scoped_styles(self):
        response = validate_qr(self.request('post'), self.token())
        self.assertContains(response, 'css/wms-qr.css')
        self.assertContains(response, 'Skeniraj novu narudžbu')
        self.assertContains(response, 'data-scan-new')
        self.assertContains(response, 'data-validation-template=')
        self.assertContains(response, 'pickingQrReader')
        self.assertContains(response, 'data-scan-close')
        self.assertContains(response, 'js/csrf-session.js')
        self.assertContains(response, 'js/staff-wms-qr.js')
        self.assertEqual(response['Referrer-Policy'], 'same-origin')

    def stage_shortage(self, token=None, first=1, second=0):
        token = token or self.token()
        with patch('EcommerceApp.wms.render', side_effect=lambda request, template, context, **kw: context):
            return validate_qr(self.request('post', {'action': 'save', 'item': str(self.item.pk),
                'found_0': str(first), 'found_1': str(second)}), token)

    def stage_alternatives(self, context, token, **choices):
        data = {'action': 'save_alternatives', 'item': str(self.item.pk), 'changes': context['changes'], **choices}
        with patch('EcommerceApp.wms.render', side_effect=lambda request, template, context, **kw: context):
            return validate_qr(self.request('post', data), token)

    def test_shortage_offers_popup_and_deducts_chosen_locations_only_on_final_confirmation(self):
        c = WarehouseLocation.objects.create(sifra='C-03', naziv='Polica C')
        d = WarehouseLocation.objects.create(sifra='D-04', naziv='Polica D')
        apply_movement(product=self.product, location=c, tip='prijem', kolicina=4)
        apply_movement(product=self.product, location=d, tip='prijem', kolicina=2)
        token = self.token()
        context = self.stage_shortage(token)
        self.assertEqual(context['alternative_remaining'], 4)
        self.assertEqual({row['location'].pk for row in context['alternatives']}, {c.pk, d.pk})
        html = render_to_string('staff/magacin/wms_qr.html', context)
        self.assertIn('data-alternative-dialog', html)
        self.assertIn('C-03', html)
        self.assertIn('D-04', html)
        stocks = {row.location_id: row for row in WarehouseStock.objects.filter(product=self.product)}
        chosen = self.stage_alternatives(context, token, **{f'alt_{stocks[c.pk].pk}': '3', f'alt_{stocks[d.pk].pk}': '1'})
        self.assertTrue(chosen['saved'])
        self.assertEqual(chosen['summary'][0]['found'], 5)
        self.assertEqual(WarehouseStock.objects.get(pk=stocks[c.pk].pk).kolicina, 4)
        response = validate_qr(self.request('post', {'action': 'complete', 'changes': chosen['changes']}), token)
        self.assertContains(response, 'je validatovana.')
        self.assertEqual(self.stock(), [1, 4, 1, 1])
        self.item.refresh_from_db()
        self.assertEqual(self.item.kolicina_faktura, 5)
        repeated = validate_qr(self.request('post', {'action': 'complete', 'changes': chosen['changes']}), token)
        self.assertEqual(repeated.status_code, 200)
        self.assertEqual(self.stock(), [1, 4, 1, 1])

    def test_zero_original_quantity_can_be_replaced_with_other_location(self):
        c = WarehouseLocation.objects.create(sifra='C-03')
        apply_movement(product=self.product, location=c, tip='prijem', kolicina=5)
        stock = WarehouseStock.objects.get(product=self.product, location=c)
        token = self.token()
        context = self.stage_shortage(token, first=0)
        chosen = self.stage_alternatives(context, token, **{f'alt_{stock.pk}': '5'})
        response = validate_qr(self.request('post', {'action': 'complete', 'changes': chosen['changes']}), token)
        self.assertContains(response, 'je validatovana.')
        self.assertEqual(self.stock(), [2, 4, 0])
        self.item.refresh_from_db()
        self.assertFalse(self.item.wms_out_of_stock)

    def test_alternatives_can_be_skipped_without_deducting_them(self):
        c = WarehouseLocation.objects.create(sifra='C-03')
        apply_movement(product=self.product, location=c, tip='prijem', kolicina=5)
        token = self.token()
        context = self.stage_shortage(token)
        with patch('EcommerceApp.wms.render', side_effect=lambda request, template, context, **kw: context):
            skipped = validate_qr(self.request('post', {'action': 'skip_alternatives', 'item': str(self.item.pk),
                'changes': context['changes']}), token)
        self.assertEqual(skipped['summary'][0]['found'], 1)
        response = validate_qr(self.request('post', {'action': 'complete', 'changes': skipped['changes']}), token)
        self.assertContains(response, 'je validatovana.')
        self.assertEqual(self.stock(), [1, 4, 5])

    def test_changed_alternative_stock_rolls_back_instead_of_deducting_another_location(self):
        c = WarehouseLocation.objects.create(sifra='C-03')
        apply_movement(product=self.product, location=c, tip='prijem', kolicina=4)
        stock = WarehouseStock.objects.get(product=self.product, location=c)
        token = self.token()
        chosen = self.stage_alternatives(self.stage_shortage(token), token, **{f'alt_{stock.pk}': '4'})
        WarehouseStock.objects.filter(pk=stock.pk).update(kolicina=1)
        response = validate_qr(self.request('post', {'action': 'complete', 'changes': chosen['changes']}), token)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.stock(), [2, 4, 1])
        self.order.refresh_from_db()
        self.assertNotEqual(self.order.lager_status, Order.LagerStatus.VALIDIRANO)
        self.item.refresh_from_db()
        self.assertIsNone(self.item.kolicina_pokupljeno)

    def test_rejects_over_order_quantity_and_other_product_location(self):
        c = WarehouseLocation.objects.create(sifra='C-03')
        apply_movement(product=self.product, location=c, tip='prijem', kolicina=10)
        stock = WarehouseStock.objects.get(product=self.product, location=c)
        token = self.token()
        context = self.stage_shortage(token)
        response = validate_qr(self.request('post', {'action': 'save_alternatives', 'item': str(self.item.pk),
            'changes': context['changes'], f'alt_{stock.pk}': '5'}), token)
        self.assertEqual(response.status_code, 409)
        other = Product.objects.create(naziv='Drugi artikal', sifra='ALT-OTHER', cijena=10)
        apply_movement(product=other, location=c, tip='prijem', kolicina=10)
        other_stock = WarehouseStock.objects.get(product=other, location=c)
        response = validate_qr(self.request('post', {'action': 'save_alternatives', 'item': str(self.item.pk),
            'changes': context['changes'], f'alt_{other_stock.pk}': '1'}), token)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.stock(), [2, 4, 10])

    def test_alternatives_respect_other_orders_reservations(self):
        c = WarehouseLocation.objects.create(sifra='C-03')
        apply_movement(product=self.product, location=c, tip='prijem', kolicina=5)
        other = Order.objects.create(ime_prezime='Druga narudžba', ukupno=40)
        reserve_for_order(other, self.product, 4, location=c, user=self.user)
        token = self.token()
        context = self.stage_shortage(token)
        self.assertEqual(context['alternatives'][0]['available'], 1)
        stock = WarehouseStock.objects.get(product=self.product, location=c)
        chosen = self.stage_alternatives(context, token, **{f'alt_{stock.pk}': '1'})
        response = validate_qr(self.request('post', {'action': 'complete', 'changes': chosen['changes']}), token)
        self.assertContains(response, 'je validatovana.')
        stock.refresh_from_db()
        self.assertEqual((stock.kolicina, stock.rezervisano), (4, 4))
