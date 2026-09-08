from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch, Mock

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.test import TestCase, RequestFactory, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import Akcija, SiteSettings, Product, AIPopupItem, Category
from . import browse_interest_offer as offers


@override_settings(STORAGES={'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'}, 'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class AISettingsTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser('ai-admin', 'ai@example.com', 'test')
        self.site = SiteSettings.load()
        self.action = Akcija.objects.create(naziv='AI prodaja / AI dwell', tip=Akcija.Tip.AI_PRODAJA)
        self.request = RequestFactory().post('/', {'tip': Akcija.Tip.AI_PRODAJA})
        self.request.user = self.user
        self.form_class = admin.site._registry[Akcija].get_form(self.request, self.action)
        self.data = {'naziv': self.action.naziv, 'tip': self.action.tip,
                     'browse_interest_popup_aktivan': 'on', 'browse_interest_mode': 'balanced',
                     'browse_interest_source': 'category', 'browse_interest_popust': '10'}
        self.category = Category.objects.create(naziv='Stapovi')
        self.other_category = Category.objects.create(naziv='Masinice')
        self.products = [Product.objects.create(naziv=f'Artikal {i}', cijena=100, kategorija=self.category,
                                               aktivan=True, na_stanju=True) for i in range(3)]
        self.outside = Product.objects.create(naziv='Druga kategorija', cijena=50, kategorija=self.other_category, aktivan=True, na_stanju=True)
        self.visitor = SimpleNamespace(pregledani_proizvodi=[], pregledane_kategorije=[])

    def test_simple_form_saves_mode_and_disables_old_flash(self):
        self.site.product_dwell_popup_aktivan = True
        self.site.save()
        form = self.form_class(data={**self.data, 'browse_interest_mode': 'no_discount', 'browse_interest_popust': '20'}, instance=self.action)
        self.assertTrue(form.is_valid(), form.errors)
        form.save_ai_settings()
        self.site.refresh_from_db()
        self.assertEqual(self.site.browse_interest_mode, 'no_discount')
        self.assertEqual(self.site.browse_interest_popust, 0)
        self.assertFalse(self.site.product_dwell_popup_aktivan)
        self.assertEqual(offers._settings()[1], 0)

    def test_local_percentage_and_invalid_configuration(self):
        form = self.form_class(data={**self.data, 'browse_interest_popust': '12,5%'}, instance=self.action)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['browse_interest_popust'], Decimal('12.5'))
        for key,value in [('browse_interest_popust','51'),('browse_interest_popust','-1'),('browse_interest_mode','invalid'),('browse_interest_source','invalid')]:
            form = self.form_class(data={**self.data,key:value}, instance=self.action)
            self.assertFalse(form.is_valid())
            self.assertIn(key, form.errors)

    def test_manual_rotation_one_article_at_a_time_and_no_repeat(self):
        self.site.browse_interest_source = 'manual'
        self.site.browse_interest_mode = 'assertive'
        self.site.save()
        for product in self.products:
            AIPopupItem.objects.create(settings=self.site, product=product)
        state = {}
        with patch.object(offers, '_cart_product_ids', return_value=set()), patch.object(offers, '_claimed_ids', return_value=set()), patch.object(offers, '_create_tracking_offer', return_value=None), patch.object(offers, '_save_state'), patch.object(offers, '_focus_category_name', return_value=''), patch.object(offers, '_top_category_name', return_value=''):
            for index, product in enumerate(self.products):
                result = offers._create_active_offer(self.request, self.visitor, state, Decimal('10'), index+1)
                self.assertEqual(result['product_ids'], [product.pk])
                self.assertEqual(result['expires_ts']-result['created_ts'],180)
                state = offers._complete_active_offer(self.request, state)
                self.assertEqual(state['offers_done'],index+1)
            self.assertIsNone(offers._create_active_offer(self.request, self.visitor, state, Decimal('10'),4))

    def test_manual_source_skips_unavailable_and_excluded_products(self):
        self.site.browse_interest_source = 'manual'
        self.site.save()
        for product in self.products:
            AIPopupItem.objects.create(settings=self.site, product=product)
        Product.objects.filter(pk=self.products[1].pk).update(na_stanju=False)
        self.assertEqual(offers._popup_product_ids(self.visitor,{self.products[0].pk}), [self.products[2].pk])

    def test_category_source_never_offers_unrelated_products(self):
        with patch.object(offers, '_focus_category_name', return_value=self.category.naziv):
            ids = offers._popup_product_ids(self.visitor)
            self.assertEqual(set(ids), {p.pk for p in self.products})
            self.assertNotIn(self.outside.pk,ids)
        with patch.object(offers, '_focus_category_name', return_value=''):
            self.assertEqual(offers._popup_product_ids(self.visitor), [])

    def test_mode_controls_timing_and_allows_more_than_two_rotations(self):
        self.site.browse_interest_mode = 'assertive'
        self.site.browse_interest_source = 'manual'
        self.site.save()
        state = {'offers_done': 3, 'last_completed_ts':timezone.now().timestamp()-61}
        with patch.object(offers,'compute_purchase_intent_score',return_value=0), patch.object(offers,'_site_seconds',return_value=1000):
            self.assertTrue(offers._should_trigger_wave(self.request,self.visitor,state,4))
            state['last_completed_ts'] = timezone.now().timestamp()-30
            self.assertFalse(offers._should_trigger_wave(self.request,self.visitor,state,4))
        self.site.browse_interest_mode='balanced'
        self.site.save()
        self.assertFalse(offers._should_trigger_wave(self.request,self.visitor,state,4))

    def test_admin_saves_manual_selection_and_rejects_empty_list(self):
        self.client.force_login(self.user)
        url = reverse('admin:EcommerceApp_akcija_change',args=[self.action.pk])
        data = {**self.data,'browse_interest_source':'manual', 'popup_items-TOTAL_FORMS':'1', 'popup_items-INITIAL_FORMS':'0',
                'popup_items-MIN_NUM_FORMS':'0','popup_items-MAX_NUM_FORMS':'1000'}
        response = self.client.post(url,data)
        self.assertEqual(response.status_code,200)
        self.assertContains(response,'Dodaj barem jedan artikal')
        data['popup_items-0-product'] = str(self.products[0].pk)
        response = self.client.post(url,data)
        self.assertEqual(response.status_code,302)
        self.site.refresh_from_db()
        self.assertEqual(self.site.browse_interest_source,'manual')
        self.assertEqual(list(self.site.popup_items.values_list('product_id',flat=True)),[self.products[0].pk])

    def test_no_discount_mode_overrides_old_session_discount_and_expiry_is_enforced(self):
        from django.contrib.sessions.backends.db import SessionStore
        self.site.browse_interest_source='manual'
        self.site.browse_interest_mode='no_discount'
        self.site.save()
        AIPopupItem.objects.create(settings=self.site,product=self.products[0])
        request=RequestFactory().post('/',{'product_id':self.products[0].pk})
        request.session=SessionStore()
        active={'show':True,'product_ids':[self.products[0].pk],'discount_percent':'25',
                'expires_ts':timezone.now().timestamp()+60}
        request.session[offers.SESSION_STATE_KEY]={'active':active,'offers_done':0}
        cart=Mock()
        with patch.object(offers,'get_cart_session_key',return_value='no-discount'), patch('EcommerceApp.staff_alerts.notify_offer_accepted'):
            ok,_=offers.apply_browse_interest_offer(request,cart)
        self.assertTrue(ok)
        self.assertNotIn('custom_price',cart.add.call_args.kwargs)
        self.assertIn('bez popusta',cart.add.call_args.kwargs['discount_source'])
        active['expires_ts']=timezone.now().timestamp()-1
        request.session[offers.SESSION_STATE_KEY]={'active':active,'offers_done':0}
        cart.reset_mock()
        ok,_=offers.apply_browse_interest_offer(request,cart)
        self.assertFalse(ok)
        cart.add.assert_not_called()

    def test_admin_preserves_selected_item_that_is_now_unavailable(self):
        item = AIPopupItem.objects.create(settings=self.site, product=self.products[0])
        Product.objects.filter(pk=item.product_id).update(na_stanju=False, aktivan=False)
        self.client.force_login(self.user)
        response = self.client.post(reverse('admin:EcommerceApp_akcija_change', args=[self.action.pk]), {
            **self.data, 'browse_interest_source': 'manual',
            'popup_items-TOTAL_FORMS': '1', 'popup_items-INITIAL_FORMS': '1',
            'popup_items-MIN_NUM_FORMS': '0', 'popup_items-MAX_NUM_FORMS': '1000',
            'popup_items-0-id': str(item.pk), 'popup_items-0-product': str(item.product_id),
        })
        self.assertEqual(response.status_code, 302)
        self.assertTrue(AIPopupItem.objects.filter(pk=item.pk).exists())
        self.assertEqual(offers._popup_product_ids(self.visitor), [])

    def test_admin_has_only_new_configuration(self):
        self.client.force_login(self.user)
        response=self.client.get(reverse('admin:EcommerceApp_akcija_change',args=[self.action.pk]))
        self.assertEqual(response.status_code,200)
        for label in ['Nametljivo','Uravnoteženo','Bez popusta','Artikli koje ja odaberem','ai-settings.v20260909.js']:
            self.assertContains(response,label)
        for field in ['id_product_dwell_popust','id_product_dwell_boja_box','id_browse_interest_min_seconds','id_product_dwell_flash_seconds']:
            self.assertNotContains(response,field)
