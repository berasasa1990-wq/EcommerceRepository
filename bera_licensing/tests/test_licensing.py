import base64
from datetime import timedelta
from unittest.mock import patch
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.db import connections
from django.http import JsonResponse
from django.test import TestCase, RequestFactory, override_settings
from django.urls import reverse
from django.utils import timezone
from django.views import View
from bera_licensing.decorators import module_required
from bera_licensing.mixins import ModuleRequiredMixin
from bera_licensing.models import LicenseState, ActivationRequest
from bera_licensing.security import LicenseError, canonical_payload, verify_document
from bera_licensing.services import module_enabled, require_module, sync_license


@override_settings(BERA_LICENSE_INSTALLATION_ID='test-install', BERA_LICENSE_ENFORCED_MODULES=('loyalty',), BERA_LICENSE_GRACE_PERIOD=3600)
class LicensingTests(TestCase):
    def setUp(self):
        self.key = Ed25519PrivateKey.generate()
        self.config = override_settings(BERA_LICENSE_PUBLIC_KEY=base64.b64encode(self.key.public_key().public_bytes_raw()).decode())
        self.config.enable()
        self.addCleanup(self.config.disable)

    def document(self, **changes):
        now = timezone.now()
        doc = dict(installation_id='test-install', protocol_version=1, license_version=1,
                   issued_at=(now-timedelta(hours=2)).isoformat(), expires_at=(now+timedelta(hours=1)).isoformat(),
                   grace_until=(now+timedelta(hours=2)).isoformat(), modules={'loyalty': True})
        doc.update(changes)
        doc['signature'] = base64.b64encode(self.key.sign(canonical_payload(doc))).decode()
        return doc

    def install(self, doc=None):
        doc = doc or self.document()
        LicenseState.objects.update_or_create(installation_id='test-install', defaults={'document': doc, 'version': doc['license_version']})

    def test_points_follow_successful_signed_sync(self):
        from shop.models import Order, LoyaltyEntry
        from shop.loyalty import settle_points, redeem_points
        from decimal import Decimal
        self.install()
        user = get_user_model().objects.create_user('signed-points')
        order = Order.objects.create(customer=user, total=100, status='completed')
        self.assertEqual(LoyaltyEntry.objects.get(order=order).points, 100)
        self.assertEqual(self.client.get(reverse('loyalty')).status_code, 200)
        with patch('bera_licensing.services.fetch_license', return_value=self.document(license_version=2, modules={'loyalty': False})):
            self.assertTrue(sync_license(force=True))
        self.assertEqual(self.client.get(reverse('loyalty')).status_code, 403)
        with self.assertRaises(PermissionDenied):
            settle_points(order.pk)
        with self.assertRaises(PermissionDenied):
            redeem_points(order, user, Decimal('100'), 10)
        Order.objects.create(customer=user, total=100, status='completed')
        self.assertEqual(LoyaltyEntry.objects.count(), 1)

    def test_active_and_disabled(self):
        self.install()
        self.assertTrue(module_enabled('loyalty'))
        self.install(self.document(modules={'loyalty': False}, license_version=2))
        self.assertFalse(module_enabled('loyalty'))
        self.assertFalse(module_enabled('unknown'))

    def test_grace_and_expiry(self):
        self.install(self.document(expires_at=(timezone.now()-timedelta(minutes=10)).isoformat()))
        self.assertTrue(module_enabled('loyalty'))
        with override_settings(BERA_LICENSE_GRACE_PERIOD=0):
            self.assertFalse(module_enabled('loyalty'))
        self.install(self.document(expires_at=(timezone.now()-timedelta(hours=1)).isoformat(), grace_until=(timezone.now()-timedelta(minutes=1)).isoformat()))
        self.assertFalse(module_enabled('loyalty'))

    def test_invalid_documents(self):
        for changes in ({'installation_id': 'other'}, {'protocol_version': 2}, {'license_version': True},
                        {'modules': {'loyalty': 'true'}}, {'issued_at': 'bad'}, {'expires_at': None}):
            with self.subTest(changes=changes), self.assertRaises(LicenseError):
                verify_document(self.document(**changes), accepting=True)
        doc = self.document()
        doc['modules']['loyalty'] = False
        with self.assertRaises(LicenseError):
            verify_document(doc)
        doc.pop('signature')
        with self.assertRaises(LicenseError):
            verify_document(doc)

    def test_local_tampering(self):
        self.install()
        state = LicenseState.objects.get()
        state.document['modules']['loyalty'] = False
        state.save()
        self.assertFalse(module_enabled('loyalty'))
        from shop.models import SiteModule
        SiteModule.objects.update_or_create(code='loyalty', defaults={'enabled': True})
        self.assertFalse(module_enabled('loyalty'))

    @patch('bera_licensing.services.fetch_license')
    def test_sync_and_replay(self, fetch):
        fetch.return_value = self.document(license_version=2)
        self.assertTrue(sync_license())
        self.assertFalse(sync_license())
        self.assertEqual(fetch.call_count, 1)
        self.assertTrue(sync_license(force=True))  # Identical response is idempotent.
        fetch.return_value = self.document(license_version=1)
        with self.assertRaises(LicenseError):
            sync_license(force=True)
        fetch.return_value = self.document(license_version=2, modules={'loyalty': False})
        with self.assertRaises(LicenseError):
            sync_license(force=True)
        self.assertTrue(module_enabled('loyalty'))

    @patch('bera_licensing.services.fetch_license', side_effect=LicenseError('sync_unavailable'))
    def test_outage(self, fetch):
        self.assertFalse(module_enabled('loyalty'))
        self.install()
        with self.assertRaises(LicenseError):
            sync_license(force=True)
        self.assertTrue(module_enabled('loyalty'))
        state = LicenseState.objects.get()
        self.assertEqual(state.last_error, 'sync_unavailable')
        self.assertIsNone(state.lease_until)

    @patch('bera_licensing.services.fetch_license')
    def test_workers_share_lease_and_storage(self, fetch):
        self.install()
        state = LicenseState.objects.get()
        state.lease_until = timezone.now()+timedelta(seconds=60)
        state.lease_token = 'other-worker'
        state.save()
        self.assertFalse(sync_license(force=True))
        fetch.assert_not_called()
        with connections['default'].cursor() as cursor:
            cursor.execute('SELECT version FROM bera_licensing_licensestate')
            self.assertEqual(cursor.fetchone()[0], 1)
        self.assertTrue(module_enabled('loyalty'))
        LicenseState.objects.filter(pk=state.pk).update(document=self.document(license_version=2, modules={'loyalty': False}), version=2)
        self.assertFalse(module_enabled('loyalty'))

    def test_direct_urls_and_api(self):
        user = get_user_model().objects.create_user('manager', is_staff=True)
        self.client.force_login(user)
        self.assertEqual(self.client.get(reverse('panel_section', args=['loyalty'])).status_code, 404)
        self.assertEqual(self.client.post(reverse('module_loyalty'), {'enabled': True}).status_code, 410)
        self.client.post(reverse('cart_loyalty'), {'action': 'apply'})
        self.assertNotIn('loyalty_customer', self.client.session)
        from shop.loyalty import loyalty_status, applied_loyalty_percent
        self.assertEqual(loyalty_status(user), (0, 0))
        self.assertRedirects(self.client.get(reverse('checkout')), reverse('cart'))
        response = self.client.get(reverse('panel_section', args=['moduli']))
        self.assertContains(response, 'moduleActivationDialog')
        self.client.post(reverse('bera_activation'), {'module': 'loyalty'})
        self.assertEqual(ActivationRequest.objects.count(), 1)
        self.assertFalse(module_enabled('loyalty'))

    def test_checkout_rechecks_revoked_license(self):
        from shop.models import Product, Order, LoyaltyTier, LoyaltyCard
        self.install()
        user = get_user_model().objects.create_user('checkout-license')
        LoyaltyTier.objects.create(minimum=0, percent=10)
        self.client.force_login(user)
        product = Product.objects.create(name='Artikal', price=100, stock=1)
        self.client.post(reverse('cart_update', args=[product.pk]), {'quantity': 1})
        self.client.post(reverse('cart_loyalty'), {'action': 'apply'})
        self.assertEqual(self.client.get(reverse('cart')).context['loyalty_discount'], 10)
        self.install(self.document(license_version=2, modules={'loyalty': False}))
        self.client.post(reverse('checkout'), {'customer_name': 'Kupac', 'email': 'kupac@example.com', 'phone': '061123456', 'address': 'Ulica 1', 'city': 'Sarajevo', 'postal_code': '71000', 'loyalty_percent': '100'})
        order = Order.objects.get()
        self.assertEqual(order.loyalty_discount, 0)
        self.assertEqual(order.total, 100)
        self.assertTrue(LoyaltyCard.objects.filter(customer=user).exists())
        new_user = get_user_model().objects.create_user('no-license-card')
        self.assertFalse(LoyaltyCard.objects.filter(customer=new_user).exists())

    @patch('bera_licensing.services.fetch_license')
    def test_key_rotation_and_corrupt_cache_recovery(self, fetch):
        self.install()
        other_key = Ed25519PrivateKey.generate()
        self.key = other_key
        fetch.return_value = self.document(license_version=2)
        with override_settings(BERA_LICENSE_PUBLIC_KEY=base64.b64encode(other_key.public_key().public_bytes_raw()).decode()):
            self.assertFalse(module_enabled('loyalty'))
            self.assertTrue(sync_license(force=True))
            self.assertTrue(module_enabled('loyalty'))

    def test_view_mixin_job_guards(self):
        @module_required('loyalty')
        def endpoint(request):
            return JsonResponse({'ok': True})
        class Endpoint(ModuleRequiredMixin, View):
            module_code = 'loyalty'
            def get(self, request):
                return JsonResponse({'ok': True})
        request = RequestFactory().get('/')
        self.assertEqual(endpoint(request).status_code, 403)
        self.assertEqual(Endpoint.as_view()(request).status_code, 403)
        with self.assertRaises(PermissionDenied):
            require_module('loyalty')
        self.install()
        self.assertEqual(endpoint(request).status_code, 200)
        self.assertEqual(Endpoint.as_view()(request).status_code, 200)

    def test_rollout_and_environment_identity(self):
        for debug in (True, False):
            LicenseState.objects.all().delete()
            with override_settings(DEBUG=debug):
                self.assertFalse(module_enabled('loyalty'))
                self.install()
                self.assertTrue(module_enabled('loyalty'))
                with override_settings(BERA_LICENSE_INSTALLATION_ID='production-other'):
                    self.assertFalse(module_enabled('loyalty'))
        with override_settings(BERA_LICENSE_ENFORCED_MODULES=()):
            self.assertTrue(module_enabled('loyalty'))
            self.assertFalse(module_enabled('booking'))

    @patch('bera_licensing.client.build_opener')
    def test_https_client(self, opener):
        from bera_licensing.client import fetch_license, NoRedirect
        with override_settings(BERA_LICENSE_SERVER_URL='http://example.com', BERA_LICENSE_API_CREDENTIAL='secret'):
            with self.assertRaises(LicenseError):
                fetch_license()
        opener.assert_not_called()
        with self.assertRaises(LicenseError):
            NoRedirect().redirect_request(None, None, 302, '', {}, 'https://evil.example')


from concurrent.futures import ThreadPoolExecutor
from threading import Event
from django.test import TransactionTestCase
from django.db import close_old_connections


class ConcurrentWorkerTests(TransactionTestCase):
    def test_only_one_worker_fetches(self):
        key = Ed25519PrivateKey.generate()
        now = timezone.now()
        doc = dict(installation_id='concurrent', protocol_version=1, license_version=1,
                   issued_at=(now-timedelta(minutes=1)).isoformat(),
                   expires_at=(now+timedelta(hours=1)).isoformat(),
                   grace_until=(now+timedelta(hours=2)).isoformat(), modules={'loyalty': True})
        doc['signature'] = base64.b64encode(key.sign(canonical_payload(doc))).decode()
        entered, release = Event(), Event()
        def fetch():
            entered.set()
            if not release.wait(timeout=10):
                raise RuntimeError('worker timeout')
            return doc
        def worker():
            close_old_connections()
            try:
                return sync_license(force=True)
            finally:
                connections['default'].close()
        with override_settings(BERA_LICENSE_INSTALLATION_ID='concurrent', BERA_LICENSE_PUBLIC_KEY=base64.b64encode(key.public_key().public_bytes_raw()).decode()):
            LicenseState.objects.create(installation_id='concurrent')
            with patch('bera_licensing.services.fetch_license', side_effect=fetch) as remote, ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(worker)
                try:
                    self.assertTrue(entered.wait(timeout=10))
                    self.assertFalse(sync_license(force=True))
                finally:
                    release.set()
                self.assertTrue(future.result(timeout=10))
                self.assertEqual(remote.call_count, 1)
            self.assertEqual(LicenseState.objects.get().document, doc)
