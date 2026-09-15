from django.contrib import admin
from django.contrib.auth.models import User
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from .models import (Akcija, Category, Product, SiteSettings, ChatConversation, ChatMessage,
                     OnlineGiftCampaign, OnlineGiftPush, OnlineGiftClaim, AIProdajaSettings, LiveVisitorOffer,
                     AdvisorBeginnerFishType, AdvisorBeginnerSet)


@override_settings(STORAGES={
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
})
class RetiredFeaturesTests(TestCase):
    def setUp(self):
        from django.core.cache import cache
        cache.clear()
        self.settings = SiteSettings.load()
        self.settings.savjetnik_aktivan = True
        self.settings.chat_sa_kupcem_aktivan = True
        self.settings.online_nagrada_bočni_aktivan = True
        self.settings.browse_interest_popup_aktivan = True
        self.settings.product_dwell_popup_aktivan = True
        self.settings.save()
        category = Category.objects.create(naziv='Test kategorija', slug='test-kategorija')
        self.product = Product.objects.create(naziv='Test artikal', slug='test-artikal',
                                             kategorija=category, cijena=100, aktivan=True, na_stanju=True)
        self.owner = User.objects.create_superuser('cleanup-owner', 'owner@example.com', 'test-pass-123')

    def test_storefront_does_not_load_removed_features_even_with_old_settings_enabled(self):
        for url in [reverse('home'), reverse('product_detail', args=[self.product.slug]), reverse('cart')]:
            with self.subTest(url=url):
                with CaptureQueriesContext(connection) as queries:
                    response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
                for asset in ['chat.min.js', 'fishing-advisor.js', 'online-gift.js', 'live-offer-poll.min.js', 'dwellFlashConfigData']:
                    self.assertNotContains(response, asset)
                sql = '\n'.join(query['sql'].lower() for query in queries)
                for table in ['ecommerceapp_onlinegift', 'ecommerceapp_chatconversation', 'ecommerceapp_chatmessage', 'ecommerceapp_aipopupitem', 'ecommerceapp_productdwellitem']:
                    self.assertNotIn(table, sql)

    def test_retired_models_are_not_exposed_in_admin(self):
        for model in [ChatConversation, ChatMessage, OnlineGiftCampaign, OnlineGiftPush,
                      OnlineGiftClaim, AIProdajaSettings, LiveVisitorOffer, AdvisorBeginnerFishType, AdvisorBeginnerSet]:
            self.assertNotIn(model, admin.site._registry)

    def test_admin_panel_settings_and_promotions_still_render_without_retired_controls(self):
        self.client.force_login(self.owner)
        response = self.client.get(reverse('staff_admin_panel'))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'Pregled sajta')
        response = self.client.get(reverse('admin:EcommerceApp_sitesettings_change', args=[self.settings.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'name="chat_sa_kupcem_aktivan"')
        self.assertNotContains(response, 'name="online_nagrada_bočni_aktivan"')
        response = self.client.get(reverse('admin:EcommerceApp_akcija_add'))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'value="ai_prodaja"')
        self.assertNotContains(response, 'name="browse_interest_popup_aktivan"')
        self.assertNotIn(Akcija.Tip.AI_PRODAJA, Akcija.ACTIVE_TIPS)

    def test_live_visitor_pages_remain_available_without_ai_and_gift_queries(self):
        self.client.force_login(self.owner)
        for name in ['staff_live_analytics', 'staff_live_analytics_data']:
            with CaptureQueriesContext(connection) as queries:
                response = self.client.get(reverse(name))
            self.assertEqual(response.status_code, 200)
            sql = '\n'.join(query['sql'].lower() for query in queries)
            self.assertNotIn('ecommerceapp_onlinegift', sql)
            self.assertNotIn('ecommerceapp_cityvisittotal', sql)
