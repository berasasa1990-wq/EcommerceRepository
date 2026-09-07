"""Regression tests for warehouse access after subscription removal."""
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.http import HttpResponse
from django.template.loader import render_to_string
from django.test import TestCase, RequestFactory, override_settings
from django.urls import reverse, resolve, Resolver404
from django.utils import timezone

from .models import MagacinPlan, MagacinSubscription, SiteSettings
from .warehouse_access import warehouse_user_required, can_access_route


@override_settings(STORAGES={
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
})
class WarehouseAccessTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_superuser(
            'warehouse-admin', 'another-admin@example.com', 'test',
        )
        self.user = get_user_model().objects.create_user('customer', password='test')

    def test_every_warehouse_route_is_available_without_subscription_or_owner_email(self):
        from .urls import urlpatterns
        self.assertFalse(MagacinSubscription.objects.filter(user=self.admin).exists())
        self.assertTrue(warehouse_user_required(self.admin))
        with self.assertNumQueries(0):
            for pattern in urlpatterns:
                name = getattr(pattern, 'name', '') or ''
                if name.startswith(('staff_magacin', 'staff_order_')):
                    self.assertTrue(can_access_route(self.admin, name), name)

    def test_expired_disabled_basic_plan_cannot_block_superuser(self):
        MagacinSubscription.objects.create(
            user=self.admin, plan=MagacinPlan.objects.get(code='basic'),
            active=False, expires_on=timezone.localdate() - timedelta(days=1),
        )
        self.client.force_login(self.admin)
        with patch('EcommerceApp.views_magacin._magacin_context', return_value={}), patch(
            'EcommerceApp.views_magacin.render', return_value=HttpResponse('Warehouse works'),
        ):
            self.assertContains(self.client.get(reverse('staff_magacin_pakuj')), 'Warehouse works')
        self.assertTrue(can_access_route(self.admin, 'staff_magacin_backup'))

    def test_non_superusers_and_inactive_accounts_cannot_enter(self):
        MagacinSubscription.objects.create(
            user=self.user, plan=MagacinPlan.objects.get(code='ultimate'),
        )
        self.assertFalse(warehouse_user_required(self.user))
        self.assertFalse(warehouse_user_required(AnonymousUser()))
        self.admin.is_active = False
        self.assertFalse(warehouse_user_required(self.admin))
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(reverse('staff_magacin')).status_code, 302)
        self.assertEqual(self.client.post(reverse('staff_magacin_sync')).status_code, 302)

    def test_menu_has_full_controls_and_no_subscription_locks(self):
        request = RequestFactory().get('/')
        request.user = self.admin
        html = render_to_string('staff/magacin/base.html', {
            'request': request, 'user': self.admin, 'site_settings': SiteSettings.load(),
        })
        for name in ['uvoz', 'backup', 'sync', 'zalihe', 'duguje', 'stampa_cijena']:
            self.assertIn(reverse('staff_magacin_' + name), html)
        self.assertNotIn('mg-menu-lock', html)
        self.assertNotIn('Pretplate', html)
        self.assertNotIn('nije uključeno u tvoj plan', html)

    def test_subscription_routes_removed(self):
        for path in ['/nalog/planovi/', '/nalog/magacin/pretplate/']:
            with self.assertRaises(Resolver404):
                resolve(path)
