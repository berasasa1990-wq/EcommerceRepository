from types import SimpleNamespace
from unittest.mock import patch

from django.template.loader import render_to_string
from django.test import RequestFactory, TestCase, override_settings

from .models import Order, SiteSettings
from .views_magacin import magacin_wms


@override_settings(STORAGES={'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'}, 'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class WmsTests(TestCase):
    def request(self, **user_flags):
        request = RequestFactory().get('/nalog/magacin/wms/')
        request.user = SimpleNamespace(is_authenticated=True, is_active=True, is_superuser=True, **user_flags)
        return request

    def context(self, params=None):
        with patch('EcommerceApp.views_magacin._magacin_context', return_value={}), patch(
            'EcommerceApp.views_magacin.render', side_effect=lambda request, template, context: context
        ):
            request = self.request()
            request.GET = request.GET.copy()
            request.GET.update(params or {})
            return magacin_wms(request)

    def test_lists_all_pending_orders_without_date_or_row_limit(self):
        pending = [Order.objects.create(ime_prezime=f'Kupac {i}', ukupno=25,
                   izvor=Order.Izvor.MAGACIN if i % 2 else Order.Izvor.WEBSHOP) for i in range(85)]
        for status in (Order.Status.ZAVRSENA, Order.Status.OTKAZANA, Order.Status.CEKA_PLACANJE, Order.Status.REZERVACIJA):
            Order.objects.create(ime_prezime='Isključena', ukupno=25, status=status)
        Order.objects.create(ime_prezime='Validirana', ukupno=25, lager_status=Order.LagerStatus.VALIDIRANO)
        orders = list(self.context()['orders'])
        self.assertEqual([order.pk for order in orders], [order.pk for order in reversed(pending)])
        html = render_to_string('staff/magacin/wms.html', {'orders': orders, 'request': self.request(), 'site_settings': SiteSettings.load()})
        from django.utils.html import strip_tags
        self.assertIn('Nevalidatovane narudžbe · 85', strip_tags(html))
        self.assertIn(pending[0].broj, html)
        self.assertNotIn('Isključena', html)

    def test_empty_state(self):
        html = render_to_string('staff/magacin/wms.html', {'orders': [], 'request': self.request(), 'site_settings': SiteSettings.load()})
        self.assertIn('Nema narudžbi koje čekaju validaciju.', html)

    def test_non_admin_is_denied(self):
        request = self.request()
        request.user.is_superuser = False
        self.assertEqual(magacin_wms(request).status_code, 302)

    def test_prints_all_selected_pending_orders_without_thirty_order_limit(self):
        from .views_magacin import magacin_narudzbe_stampa
        pending = [Order.objects.create(ime_prezime='Kupac', ukupno=25) for _ in range(31)]
        validated = Order.objects.create(ime_prezime='Validirana', ukupno=25, lager_status=Order.LagerStatus.VALIDIRANO)
        request = self.request()
        request.GET = request.GET.copy()
        request.GET['wms'] = '1'
        request.GET.setlist('b', [order.broj for order in pending] + [validated.broj])
        with patch('EcommerceApp.views._order_print_job', return_value={}), patch(
            'EcommerceApp.views_magacin.collect_mp_checks', return_value=[]
        ), patch('EcommerceApp.views_magacin.pending_mp_brojevi', return_value=set()), patch(
            'EcommerceApp.views_magacin.render', side_effect=lambda request, template, context: context
        ):
            context = magacin_narudzbe_stampa(request)
        self.assertEqual(context['print_brojevi'], [order.broj for order in pending])

    def test_validated_view_is_separate_from_pending_list(self):
        pending = Order.objects.create(ime_prezime='Čeka picking', ukupno=25)
        completed = Order.objects.create(ime_prezime='Gotov kupac', ukupno=25, lager_status=Order.LagerStatus.VALIDIRANO)
        legacy = Order.objects.create(ime_prezime='Ranije završena', ukupno=25, status=Order.Status.ZAVRSENA)
        cancelled = Order.objects.create(ime_prezime='Otkazana', ukupno=25, status=Order.Status.OTKAZANA, lager_status=Order.LagerStatus.VALIDIRANO)
        default = self.context()
        self.assertEqual(list(default['orders']), [pending])
        self.assertEqual(default['validated_count'], 2)
        validated = self.context({'validirane': '1'})
        self.assertTrue(validated['show_validated'])
        self.assertEqual({o.pk for o in validated['orders']}, {completed.pk, legacy.pk})
        html = render_to_string('staff/magacin/wms.html', {**validated, 'request': self.request(), 'site_settings': SiteSettings.load()})
        self.assertIn('wms-status-validated', html)
        self.assertIn('Gotov kupac', html)
        self.assertNotIn('Čeka picking', html)
        self.assertNotIn('Otkazana', html)

    def test_live_state_updates_counts_and_removes_validated_id(self):
        import json
        order = Order.objects.create(ime_prezime='Kupac', ukupno=25)
        request = self.request()
        request.GET = request.GET.copy()
        request.GET['wms_state'] = '1'
        initial = json.loads(magacin_wms(request).content)
        self.assertIn(order.pk, initial['pending_ids'])
        Order.objects.filter(pk=order.pk).update(status=Order.Status.ZAVRSENA, lager_status=Order.LagerStatus.VALIDIRANO)
        response = magacin_wms(request)
        updated = json.loads(response.content)
        self.assertNotIn(order.pk, updated['pending_ids'])
        self.assertEqual(updated['validated_count'], 1)
        self.assertEqual(response['Cache-Control'], 'no-store')

    def test_archived_wms_print_includes_only_validated_invoices(self):
        from .views_magacin import magacin_narudzbe_stampa
        validated = [Order.objects.create(ime_prezime='Gotova', ukupno=25, status=Order.Status.ZAVRSENA) for _ in range(31)]
        pending = Order.objects.create(ime_prezime='Čeka', ukupno=25)
        request = self.request()
        request.GET = request.GET.copy()
        request.GET['wms'] = 'validated'
        request.GET.setlist('b', [o.broj for o in validated] + [pending.broj])
        with patch('EcommerceApp.views._order_print_job', return_value={}), patch(
            'EcommerceApp.views_magacin.render', side_effect=lambda r, t, c: c
        ):
            context = magacin_narudzbe_stampa(request)
        self.assertEqual(context['print_brojevi'], [o.broj for o in validated])
        self.assertNotIn('wms_picking', context['print_jobs'][0])
