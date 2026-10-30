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
        self.assertNotContains(response, 'staff-wms-qr.js')
        self.assertEqual(self.stock(), [2, 4])
        response = validate_qr(self.request('post'), token)
        self.assertContains(response, 'je validatovana.')
        self.assertEqual(self.stock(), [0, 1])

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
