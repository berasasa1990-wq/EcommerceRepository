from decimal import Decimal
from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse

from .loyalty import loyalty_merge_candidates, povezi_rucnu_karticu_sa_nalogom
from .models import LoyaltyCard, LoyaltyPurchase, Order, UserProfile


@override_settings(STORAGES={'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'}, 'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class AccountMergeTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user('merge_admin', is_superuser=True, is_staff=True)
        self.store = User.objects.create_user('loy_merge', email='same@example.com', first_name='Isti Kupac')
        self.customer = User.objects.create_user('merge_web', email='same@example.com', password='test-pass-123')
        UserProfile.objects.create(user=self.store, telefon='061123456')
        UserProfile.objects.create(user=self.customer, telefon='061123456')
        self.physical = LoyaltyCard.objects.create(user=self.store, kod='123456', barkod='123456')
        self.online = LoyaltyCard.objects.create(user=self.customer, kod='654321', barkod='654321')
        LoyaltyPurchase.objects.create(kartica=self.physical, iznos=Decimal('40'))
        LoyaltyPurchase.objects.create(kartica=self.online, iznos=Decimal('60'))

    def order(self, user, number, status='poslana'):
        return Order.objects.create(korisnik=user, broj=number, ime_prezime='Kupac', email=user.email,
                                    telefon='061123456', adresa='Ulica 1', grad='Sarajevo',
                                    ukupno=Decimal('50'), status=status)

    def test_same_email_merges_and_keeps_login_and_physical_card(self):
        password = self.customer.password
        order = self.order(self.store, 'MERGE-1')
        linked = povezi_rucnu_karticu_sa_nalogom('123456', 'same@example.com', actor=self.admin)
        self.assertEqual(linked.kod, '123456')
        self.assertEqual(linked.ukupna_potrosnja, Decimal('150'))
        self.assertEqual(linked.evidentirane_kupovine.count(), 2)
        self.assertFalse(LoyaltyCard.objects.filter(pk=self.online.pk).exists())
        self.store.refresh_from_db()
        self.assertFalse(self.store.is_active)
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.password, password)
        order.refresh_from_db()
        self.assertEqual(order.korisnik_id, self.customer.pk)
        with self.assertRaises(ValueError):
            povezi_rucnu_karticu_sa_nalogom('123456', 'same@example.com', actor=self.admin)
        self.assertEqual(LoyaltyPurchase.objects.count(), 2)

    def test_search_from_either_card_and_preview_does_not_double_count(self):
        self.order(self.customer, 'PREVIEW-1')
        forward = loyalty_merge_candidates(self.physical, 'same@example.com')
        backward = loyalty_merge_candidates(self.online, '123456')
        for results in [forward, backward]:
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0]['physical'].pk, self.physical.pk)
            self.assertEqual(results[0]['customer'].pk, self.customer.pk)
            self.assertEqual(results[0]['combined_spend'], Decimal('150'))

    def test_id_selection_rejects_staff_or_manual_target(self):
        for target in [self.admin, self.store]:
            with self.assertRaises(ValueError):
                povezi_rucnu_karticu_sa_nalogom('123456', '', actor=self.admin, target_user_id=target.pk)
        self.assertEqual(LoyaltyCard.objects.count(), 2)

    def test_staff_merge_endpoint_with_selected_account(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse('staff_loyalty_member', args=['123456']), {
            'action': 'povezi_nalog', 'broj_kartice': '123456', 'target_user_id': self.customer.pk,
        })
        self.assertEqual(response.status_code, 302)
        self.physical.refresh_from_db()
        self.assertEqual(self.physical.user_id, self.customer.pk)

    def test_staff_search_renders_review(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse('staff_loyalty_member', args=['123456']), {'merge_q': 'same@example.com'})
        self.assertContains(response, 'Spoji na karticu 123456')
        self.assertEqual(response.context['merge_candidates'][0]['combined_spend'], Decimal('100'))

    def test_customer_cannot_merge_through_staff_endpoint(self):
        self.client.force_login(self.customer)
        response = self.client.post(reverse('staff_loyalty_member', args=['123456']), {
            'action': 'povezi_nalog', 'broj_kartice': '123456', 'target_user_id': self.customer.pk,
        })
        self.assertEqual(response.status_code, 302)
        self.physical.refresh_from_db()
        self.assertEqual(self.physical.user_id, self.store.pk)

    def test_account_shows_merged_purchases_and_order_tracking(self):
        order = self.order(self.store, 'ACCOUNT-1')
        povezi_rucnu_karticu_sa_nalogom('123456', '', actor=self.admin, target_user_id=self.customer.pk)
        self.client.force_login(self.customer)
        response = self.client.get(reverse('account'))
        self.assertNotContains(response, 'Detalji programa')
        self.assertNotContains(response, 'data-acc-panel="loyalty"')
        self.assertContains(response, 'ACCOUNT-1')
        self.assertEqual(self.physical.evidentirane_kupovine.count(), 2)
        detail = self.client.get(reverse('account_order_detail', args=[order.broj]))
        self.assertContains(detail, 'Praćenje narudžbe #ACCOUNT-1')
        self.assertContains(detail, 'označena kao poslana')
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse('account_order_detail', args=[order.broj])).status_code, 404)

    def test_future_purchases_continue_on_surviving_card(self):
        from .loyalty import osiguraj_loyalty_karticu, preracunaj_potrosnju_kartice, pronadji_loyalty_karticu_po_telefonu
        linked = povezi_rucnu_karticu_sa_nalogom('123456', '', actor=self.admin, target_user_id=self.customer.pk)
        fresh_customer = User.objects.get(pk=self.customer.pk)
        self.assertEqual(osiguraj_loyalty_karticu(fresh_customer).pk, linked.pk)
        self.assertEqual(pronadji_loyalty_karticu_po_telefonu('061123456').pk, linked.pk)
        LoyaltyPurchase.objects.create(kartica=linked, iznos=Decimal('25'))
        self.order(fresh_customer, 'NEXT-1')
        preracunaj_potrosnju_kartice(linked)
        self.assertEqual(linked.ukupna_potrosnja, Decimal('175'))
        self.assertEqual(LoyaltyCard.objects.count(), 1)

    def test_merge_rolls_back_on_failure(self):
        from unittest.mock import patch
        with patch('EcommerceApp.loyalty.preracunaj_potrosnju_kartice', side_effect=RuntimeError('test failure')):
            with self.assertRaises(RuntimeError):
                povezi_rucnu_karticu_sa_nalogom('123456', '', actor=self.admin, target_user_id=self.customer.pk)
        self.physical.refresh_from_db()
        self.store.refresh_from_db()
        self.assertEqual(self.physical.user_id, self.store.pk)
        self.assertTrue(self.store.is_active)
        self.assertEqual(LoyaltyCard.objects.count(), 2)
        self.assertEqual(self.physical.evidentirane_kupovine.count(), 1)
        self.assertEqual(self.online.evidentirane_kupovine.count(), 1)

    def test_sync_queue_matches_normalized_phone_and_email_once(self):
        from .loyalty import loyalty_sync_pairs
        profile = self.customer.profil
        profile.telefon = '+387 61 123 456'
        profile.save()
        pairs = loyalty_sync_pairs()
        self.assertEqual(len(pairs), 1)
        self.assertEqual(set(pairs[0]['reasons']), {'telefon', 'email'})
        self.customer.email = 'different@example.com'
        self.customer.save()
        pairs = loyalty_sync_pairs()
        self.assertEqual(len(pairs), 1)
        self.assertEqual(pairs[0]['reasons'], ['telefon'])

    def test_sync_queue_ignores_blank_contacts_and_inactive_users(self):
        from .loyalty import loyalty_sync_pairs
        self.customer.email = ''
        self.customer.save()
        self.customer.profil.telefon = ''
        self.customer.profil.save()
        self.assertEqual(loyalty_sync_pairs(), [])
        self.customer.email = self.store.email
        self.customer.is_active = False
        self.customer.save()
        self.assertEqual(loyalty_sync_pairs(), [])

    def test_sync_page_shows_both_purchase_histories_and_merges(self):
        self.order(self.customer, 'SYNC-WEB')
        self.client.force_login(self.admin)
        url = reverse('staff_loyalty_sync')
        response = self.client.get(url)
        self.assertContains(response, 'Nalog sa sajta')
        self.assertContains(response, 'Kartica iz loyalty sistema')
        self.assertContains(response, 'SYNC-WEB')
        pair = response.context['sync_pairs'][0]
        self.assertEqual(len(pair['store_purchases']), 1)
        self.assertEqual(len(pair['web_purchases']), 1)
        self.assertEqual(pair['combined_spend'], Decimal('150'))
        response = self.client.post(url, {'card_id': self.physical.pk, 'customer_id': self.customer.pk})
        self.assertRedirects(response, url)
        self.physical.refresh_from_db()
        self.assertEqual(self.physical.ukupna_potrosnja, Decimal('150'))
        self.assertEqual(self.physical.evidentirane_kupovine.count(), 2)
        self.assertEqual(self.physical.user_id, self.customer.pk)
        self.assertContains(self.client.get(url), 'Nema kartica za spajanje')
        self.client.post(url, {'card_id': self.physical.pk, 'customer_id': self.customer.pk})
        self.assertEqual(LoyaltyPurchase.objects.count(), 2)

    def test_sync_queue_rejects_changed_match_and_customer_access(self):
        self.client.force_login(self.customer)
        self.assertEqual(self.client.get(reverse('staff_loyalty_sync')).status_code, 302)
        self.customer.email = 'different@example.com'
        self.customer.save()
        self.customer.profil.telefon = '061999999'
        self.customer.profil.save()
        self.client.force_login(self.admin)
        self.client.post(reverse('staff_loyalty_sync'), {'card_id': self.physical.pk, 'customer_id': self.customer.pk})
        self.assertEqual(LoyaltyCard.objects.count(), 2)
        self.physical.refresh_from_db()
        self.assertEqual(self.physical.user_id, self.store.pk)

    def test_customer_account_has_no_sync_controls(self):
        self.client.force_login(self.customer)
        response = self.client.get(reverse('account'))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'Poveži karticu')
        self.assertNotContains(response, 'sinhronizuj-loyalty')
        self.assertNotIn('loyalty_sync', response.context)
        self.assertNotContains(response, 'href="#loyalty"')
        self.assertContains(response, 'LOYALTY STATUS')
