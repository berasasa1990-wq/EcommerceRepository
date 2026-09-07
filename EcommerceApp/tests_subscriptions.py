from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.test import Client, RequestFactory, TestCase, override_settings
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

from .models import SiteSettings, MagacinPlan, MagacinSubscription, MagacinSubscriptionOwner, MagacinSubscriptionRequest
from .warehouse_access import OWNER_EMAIL, allowed_features, can_access_route, is_subscription_owner


@override_settings(STORAGES={'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'}, 'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}}, MIDDLEWARE=[
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'EcommerceApp.middleware.warehouse_subscription.WarehouseSubscriptionMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
])
class SubscriptionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.owner = User.objects.create_user(username='owner', email=OWNER_EMAIL, is_superuser=True, is_staff=True)
        MagacinSubscriptionOwner.objects.update_or_create(pk=1, defaults={'user': cls.owner})
        cls.user = User.objects.create_user(username='subscriber', email='subscriber@example.com')
        cls.admin = User.objects.create_user(username='admin', email='admin@example.com', is_superuser=True)
        cls.basic = MagacinPlan.objects.get(code='basic')
        cls.premium = MagacinPlan.objects.get(code='premium')
        cls.ultimate = MagacinPlan.objects.get(code='ultimate')

    def fresh_user(self, user=None):
        return get_user_model().objects.get(pk=(user or self.user).pk)

    def subscribe(self, plan, **kwargs):
        return MagacinSubscription.objects.create(user=self.user, plan=plan, **kwargs)

    def test_seeded_plan_matrix(self):
        self.assertEqual(set(self.basic.features), {'artikli', 'narudzbe', 'picking'})
        self.assertEqual(set(self.premium.features), set(self.basic.features) | {'stampa_cijena', 'stampa_deklaracije'})

    def test_basic_routes_and_direct_print_denial(self):
        self.subscribe(self.basic)
        for route in ('staff_magacin_artikli', 'staff_magacin_artikal', 'staff_magacin_narudzbe', 'staff_magacin_pakuj_detail', 'staff_order_detail', 'staff_magacin_kupci_save'):
            self.assertTrue(can_access_route(self.fresh_user(), route), route)
        for route in ('staff_magacin_stampa_cijena', 'staff_magacin_stampa_deklaracije_print', 'staff_magacin_artikal_stampa', 'staff_magacin_backup', 'staff_magacin_kupci', 'staff_magacin_pretplate'):
            self.assertFalse(can_access_route(self.fresh_user(), route), route)
        self.client.force_login(self.user)
        for url in (reverse('staff_magacin_stampa_cijena'), reverse('staff_magacin_artikal_stampa', args=[1])):
            self.assertEqual(self.client.get(url).status_code, 403)
        self.assertEqual(self.client.post(reverse('staff_magacin_sync')).status_code, 403)

    def test_premium_allows_printing_but_not_stock(self):
        self.subscribe(self.premium)
        self.assertTrue(can_access_route(self.fresh_user(), 'staff_magacin_stampa_cijena_print'))
        self.assertTrue(can_access_route(self.fresh_user(), 'staff_magacin_stampa_deklaracije_print'))
        self.assertFalse(can_access_route(self.fresh_user(), 'staff_magacin_zalihe'))

    def test_ultimate_all_warehouse_routes_except_owner_only(self):
        from .urls import urlpatterns
        self.subscribe(self.ultimate)
        for pattern in urlpatterns:
            name = getattr(pattern, 'name', '') or ''
            if name.startswith('staff_magacin') and name != 'staff_magacin_pretplate':
                self.assertTrue(can_access_route(self.fresh_user(), name), name)
        self.assertFalse(can_access_route(self.fresh_user(), 'staff_magacin_pretplate'))
        self.assertFalse(can_access_route(self.fresh_user(), 'staff_magacin_unknown_future_route'))

    def test_expired_and_inactive_block_even_superusers(self):
        sub = self.subscribe(self.basic, expires_on=timezone.localdate() - timedelta(days=1))
        self.assertFalse(allowed_features(self.fresh_user()))
        sub.expires_on = timezone.localdate()
        sub.save()
        self.assertTrue(allowed_features(self.fresh_user()))
        sub.active = False
        sub.save()
        self.user.is_superuser = True
        self.user.save()
        self.assertFalse(allowed_features(self.fresh_user()))

    def test_owner_is_pinned_and_superuser_defaults_to_basic(self):
        self.assertTrue(is_subscription_owner(self.owner))
        self.assertFalse(is_subscription_owner(self.admin))
        self.assertEqual(allowed_features(self.admin), set(self.basic.features))
        impersonator = get_user_model().objects.create_user(username='duplicate', email=OWNER_EMAIL, is_superuser=True)
        self.assertFalse(is_subscription_owner(impersonator))
        self.assertFalse(allowed_features(AnonymousUser()))
        self.assertFalse(allowed_features(self.user))

    def test_designated_owner_has_all_warehouse_controls_without_active_plan(self):
        from .urls import urlpatterns
        from .warehouse_access import FEATURES

        MagacinSubscription.objects.update_or_create(user=self.owner, defaults={
            'plan': self.basic, 'active': False,
            'expires_on': timezone.localdate() - timedelta(days=1),
        })
        owner = self.fresh_user(self.owner)
        self.assertEqual(allowed_features(owner), set(FEATURES))
        for pattern in urlpatterns:
            name = getattr(pattern, 'name', '') or ''
            if name.startswith('staff_magacin'):
                self.assertTrue(can_access_route(owner, name), name)
        owner.is_active = False
        self.assertFalse(allowed_features(owner))
        self.assertFalse(can_access_route(owner, 'staff_magacin_pretplate'))

    def test_menu_visibility(self):
        self.subscribe(self.basic)
        request = RequestFactory().get('/')
        request.user = self.fresh_user()
        html = render_to_string('staff/magacin/base.html', {'request': request, 'user': request.user, 'site_settings': SiteSettings.load()})
        nav = html.split('<nav class="mg-nav">', 1)[1].split('</nav>', 1)[0]
        self.assertIn('Narudžbe', nav)
        self.assertIn('Picking', nav)
        self.assertIn('Štampaj cijenu', nav)
        self.assertIn('aria-disabled="true"', nav)
        self.assertIn('mg-menu-lock', nav)
        self.assertNotIn('href="' + reverse('staff_magacin_stampa_cijena') + '"', nav)
        self.assertIn('href="' + reverse('staff_magacin_artikli') + '"', nav)
        self.assertNotIn('Pretplate', nav)
        request.user = self.owner
        html = render_to_string('staff/magacin/base.html', {'request': request, 'user': request.user, 'site_settings': SiteSettings.load()})
        self.assertIn(reverse('staff_magacin_pretplate'), html)

    def test_request_does_not_grant_access_or_escalate_active_plan(self):
        self.client.force_login(self.user)
        response = self.client.post(reverse('magacin_planovi'), {'plan': 'basic'})
        self.assertEqual(response.status_code, 302)
        self.assertFalse(MagacinSubscription.objects.filter(user=self.user).exists())
        self.assertFalse(allowed_features(self.fresh_user()))
        self.subscribe(self.basic)
        self.client.post(reverse('magacin_planovi'), {'plan': 'ultimate'})
        self.assertEqual(MagacinSubscriptionRequest.objects.filter(user=self.user, status='pending').count(), 1)
        self.assertEqual(MagacinSubscription.objects.get(user=self.user).plan_id, self.basic.pk)

    def test_only_owner_can_activate_and_deactivate(self):
        pending = MagacinSubscriptionRequest.objects.create(user=self.user, plan=self.premium)
        self.client.force_login(self.admin)
        self.assertEqual(self.client.post(reverse('staff_magacin_pretplate'), {'action': 'approve', 'request_id': pending.pk}).status_code, 403)
        self.client.force_login(self.owner)
        self.assertEqual(self.client.post(reverse('staff_magacin_pretplate'), {'action': 'approve', 'request_id': pending.pk}).status_code, 302)
        sub = MagacinSubscription.objects.get(user=self.user)
        self.assertEqual(sub.plan_id, self.premium.pk)
        self.assertFalse(self.fresh_user().is_staff)
        self.assertFalse(self.fresh_user().is_superuser)
        self.assertEqual(self.client.post(reverse('staff_magacin_pretplate'), {'action': 'approve', 'request_id': pending.pk}).status_code, 404)
        self.client.post(reverse('staff_magacin_pretplate'), {'action': 'deactivate', 'subscription_id': sub.pk})
        self.assertFalse(allowed_features(self.fresh_user()))

    def test_owner_can_assign_edit_restrictions_and_downgrade(self):
        self.client.force_login(self.owner)
        self.assertEqual(self.client.post(reverse('staff_magacin_pretplate'), {'action': 'assign', 'email': self.user.email, 'plan': self.premium.pk, 'active': 'on'}).status_code, 302)
        self.assertTrue(can_access_route(self.fresh_user(), 'staff_magacin_stampa_cijena'))
        self.client.post(reverse('staff_magacin_pretplate'), {'action': 'assign', 'email': self.user.email, 'plan': self.basic.pk, 'active': 'on'})
        self.assertFalse(can_access_route(self.fresh_user(), 'staff_magacin_stampa_cijena'))
        self.client.post(reverse('staff_magacin_pretplate'), {'action': 'features', 'plan': 'basic', 'features': ['artikli']})
        self.assertFalse(can_access_route(self.fresh_user(), 'staff_magacin_narudzbe'))

    def test_pages_render_and_csrf_is_required(self):
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(reverse('magacin_planovi')).status_code, 200)
        self.client.force_login(self.owner)
        with patch('EcommerceApp.views_magacin._magacin_context', return_value={}):
            response = self.client.get(reverse('staff_magacin_pretplate'))
        self.assertEqual(response.status_code, 200)
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.owner)
        self.assertEqual(csrf_client.post(reverse('staff_magacin_pretplate'), {'action': 'features', 'plan': 'basic', 'features': []}).status_code, 403)

    def test_allowed_request_reaches_existing_warehouse_view(self):
        self.subscribe(self.basic)
        self.client.force_login(self.user)
        with patch('EcommerceApp.views_magacin._magacin_context', return_value={}), patch('EcommerceApp.views_magacin.render') as rendered:
            from django.http import HttpResponse
            rendered.return_value = HttpResponse('Picking works')
            response = self.client.get(reverse('staff_magacin_pakuj'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Picking works')

    def test_custom_plan_lands_in_first_allowed_section(self):
        self.basic.features = ['narudzbe']
        self.basic.save()
        self.subscribe(self.basic)
        self.client.force_login(self.user)
        self.assertRedirects(self.client.get(reverse('staff_magacin')), reverse('staff_magacin_narudzbe'), fetch_redirect_response=False)
        self.basic.features = ['sync']
        self.basic.save()
        self.assertRedirects(self.client.get(reverse('staff_magacin')), reverse('staff_magacin_sync_istorija'), fetch_redirect_response=False)

    def test_anonymous_user_cannot_manage_or_request_a_plan(self):
        self.assertEqual(self.client.get(reverse('staff_magacin_pretplate')).status_code, 302)
        self.assertEqual(self.client.post(reverse('magacin_planovi'), {'plan': 'ultimate'}).status_code, 302)
        self.assertFalse(MagacinSubscriptionRequest.objects.exists())

    def test_default_plan_created_on_promotion_without_overwriting_assignment(self):
        self.user.is_superuser = True
        self.user.save()
        self.assertEqual(MagacinSubscription.objects.get(user=self.user).plan_id, self.basic.pk)
        MagacinSubscription.objects.filter(user=self.user).update(plan=self.premium, active=False)
        self.user.save()
        sub = MagacinSubscription.objects.get(user=self.user)
        self.assertEqual(sub.plan_id, self.premium.pk)
        self.assertFalse(sub.active)

    def test_superuser_without_subscription_uses_current_basic_restrictions(self):
        MagacinSubscription.objects.filter(user=self.admin).delete()
        self.assertEqual(allowed_features(self.fresh_user(self.admin)), set(self.basic.features))
        self.assertFalse(can_access_route(self.fresh_user(self.admin), 'staff_magacin_backup'))
        self.basic.features = ['artikli']
        self.basic.save()
        self.assertEqual(allowed_features(self.fresh_user(self.admin)), {'artikli'})
