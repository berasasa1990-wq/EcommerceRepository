import hashlib
from importlib import import_module
from pathlib import Path
from types import SimpleNamespace

from django.apps import apps
from django.conf import settings
from django.db import connection
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from . import live_visitors
from .almost_cart import record_almost_cart, get_almost_cart_products, top_almost_cart_product_id
from .models import LiveVisitor, SiteVisitorIdentity
from .retention_triggers import EXCLUDED, install_history
from . import tests_checkout_customers as checkout_tests


class DisabledTrackingContractTests(SimpleTestCase):
    def test_compatibility_functions_need_neither_session_nor_database(self):
        request = RequestFactory().get('/')
        for name in ('track_live_visitor', 'heartbeat_live_visitor', 'mark_live_visitor_left',
                     'remember_owner_ip', 'should_track_visitor'):
            self.assertFalse(getattr(live_visitors, name)(request))
        self.assertIsNone(live_visitors._touch_visitor_identity(request, 'old-session'))
        self.assertIsNone(record_almost_cart(request, 123, clicked=True))
        self.assertEqual(get_almost_cart_products(object()), [])
        self.assertIsNone(top_almost_cart_product_id(object()))
        self.assertFalse(live_visitors.maybe_notify_visitor_online('old-session'))
        live_visitors.touch_visitor_presence('old-session')
        live_visitors.clear_visitor_presence('old-session')

    def test_middleware_and_browser_tracking_are_disconnected(self):
        self.assertNotIn('EcommerceApp.middleware.live_visitor.LiveVisitorMiddleware', settings.MIDDLEWARE)
        root = Path(__file__).parent
        base = (root / 'template/base.html').read_text()
        for asset in ('almost-cart.js', 'visitor-presence.js', 'public-online-visitors.js', 'visitorPresenceRoot'):
            self.assertNotIn(asset, base)
        for template in (root / 'template').rglob('*.html'):
            self.assertNotIn('data-almost-cart', template.read_text(), str(template))
        for asset in ('almost-cart.js', 'visitor-presence.js', 'public-online-visitors.js'):
            script = (root / 'static/js' / asset).read_text()
            for hook in ('fetch(', 'sendBeacon(', 'addEventListener(', 'setInterval('):
                self.assertNotIn(hook, script)


@override_settings(ALLOWED_HOSTS=['testserver'])
class DisabledTrackingPersistenceTests(TestCase):
    def test_old_clients_cannot_create_or_change_visitors(self):
        old = LiveVisitor.objects.create(session_key='historic', ime='Sačuvano ime', last_seen=timezone.now())
        before = LiveVisitor.objects.values().get(pk=old.pk)
        for route in ('live_visitor_heartbeat', 'live_visitor_leave', 'almost_cart_track', 'public_online_visitors'):
            method = self.client.get if route == 'public_online_visitors' else self.client.post
            with self.subTest(route=route):
                response = method(reverse(route), {'session_key': 'historic', 'product_id': 123, 'clicked': True})
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.json()['disabled'])
                self.assertEqual(response['Cache-Control'], 'no-store')
                self.assertNotIn(settings.SESSION_COOKIE_NAME, response.cookies)
        self.assertEqual(list(LiveVisitor.objects.values()), [before])
        self.assertEqual(SiteVisitorIdentity.objects.count(), 0)

    def test_existing_history_trigger_is_removed_and_not_reinstalled(self):
        table = LiveVisitor._meta.db_table
        self.assertIn(table.lower(), EXCLUDED)
        if connection.vendor != 'sqlite':
            self.skipTest('SQLite trigger integration; PostgreSQL is tested on deployment.')
        name = 'retain_' + hashlib.sha256(table.encode()).hexdigest()[:16] + '_insert'
        quote = connection.ops.quote_name
        with connection.cursor() as cursor:
            cursor.execute(f'CREATE TRIGGER {quote(name)} AFTER INSERT ON {quote(table)} BEGIN SELECT 1; END')
        migration = import_module('EcommerceApp.migrations.0349_disable_livevisitor_history')
        migration.remove_livevisitor_triggers(apps, SimpleNamespace(connection=connection))
        install_history(connection, tables=[table])
        with connection.cursor() as cursor:
            cursor.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name=%s", [table])
            self.assertEqual(cursor.fetchall(), [])


class PurchaseWithoutVisitorTrackingTests(checkout_tests.CheckoutCustomerTests):
    """Run existing real checkout regression cases with a no-tracking invariant."""
    def tearDown(self):
        self.assertEqual(LiveVisitor.objects.count(), 0)
        self.assertEqual(SiteVisitorIdentity.objects.count(), 0)
        super().tearDown()

    def test_add_to_cart_still_persists_the_standard_cart(self):
        from unittest.mock import patch
        from .models import ActiveCartItem, Product
        product = Product.objects.create(naziv='Regular cart', cijena=10, stanje=5, na_stanju=True)
        with patch('EcommerceApp.staff_alerts.notify_cart_add'):
            response = self.client.post(reverse('add_to_cart', args=[product.slug]), {'quantity': 2})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.session['cart'][f'{product.pk}:0']['quantity'], 2)
        self.assertTrue(ActiveCartItem.objects.filter(product=product).exists())
