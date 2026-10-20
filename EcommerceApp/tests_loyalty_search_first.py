from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse
from .models import LoyaltyCard


@override_settings(SITE_PREP_ENABLED=False, STORAGES={
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
})
class LoyaltySearchFirstTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser('desk-admin', 'desk@example.com', 'password')
        self.client.force_login(self.admin)

    def test_initial_page_has_search_without_member_list(self):
        response = self.client.get(reverse('staff_loyalty_system'))
        self.assertContains(response, 'Pronađite kupca')
        self.assertContains(response, 'id="loyOfflineQ"')
        self.assertNotContains(response, '<div class="loy-table">')
        self.assertNotContains(response, 'id="loyLedger"')
        self.assertEqual(response.context['table_cards'], [])

    def test_unique_search_opens_member(self):
        user = User.objects.create_user('unique-member', first_name='Jedinstveni', last_name='Kupac')
        card = LoyaltyCard.objects.create(user=user, kod='482913')
        response = self.client.get(reverse('staff_loyalty_system'), {'q': 'Jedinstveni', 'mode': 'any'})
        self.assertEqual(response.status_code, 302)
        from .loyalty import loyalty_member_url
        self.assertEqual(response.url, loyalty_member_url(card.kod))
        detail = self.client.get(response.url)
        self.assertEqual(detail.status_code, 200)
        self.assertContains(detail, 'loyalty-site-theme.css')

    def test_empty_search_results_do_not_show_other_members(self):
        response = self.client.get(reverse('staff_loyalty_system'), {'q': 'nema-ovog-kupca', 'mode': 'any'})
        self.assertContains(response, 'Nema rezultata.')
        self.assertEqual(response.context['table_cards'], [])

    def test_duplicate_phone_is_rejected_with_popup_error(self):
        from .loyalty import izdaj_loyalty_karticu
        card, user = izdaj_loyalty_karticu('Postojeci', 'Clan', '061234567')
        users_before = User.objects.count()
        cards_before = LoyaltyCard.objects.count()
        response = self.client.post(reverse('staff_loyalty_system'), {
            'action': 'open_card', 'channel': 'admin', 'ime': 'Novi',
            'prezime': 'Clan', 'telefon': '061234567',
        }, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(response.status_code, 409)
        self.assertIn('već registrovan', response.json()['error'])
        self.assertEqual(User.objects.count(), users_before)
        self.assertEqual(LoyaltyCard.objects.count(), cards_before)
        user.refresh_from_db()
        self.assertEqual(user.first_name, 'Postojeci')

    def test_purchase_opens_viber_draft_with_updated_balance_once(self):
        from .loyalty import izdaj_loyalty_karticu, loyalty_member_url
        from urllib.parse import parse_qs, urlparse
        card, user = izdaj_loyalty_karticu('Test', 'Kupac', '061234567')
        url = loyalty_member_url(card.kod)
        response = self.client.post(url, {'action': 'evidentiraj_kupovinu', 'iznos': '125.50', 'placanje': 'gotovina'})
        self.assertEqual(response.status_code, 302)
        page = self.client.get(response.url)
        self.assertContains(page, 'id="purchaseViberDialog"')
        receipt = page.context['purchase_viber']
        self.assertIn('125,50 KM', receipt['message'])
        self.assertIn('Trenutno bodova: 125', receipt['message'])
        self.assertIn(card.kod, receipt['message'])
        self.assertIn('Test Kupac', receipt['message'])
        self.assertIn('preostalo je', receipt['message'])
        parsed = parse_qs(urlparse(receipt['url']).query)
        self.assertEqual(parsed['number'], ['+38761234567'])
        self.assertIn('125', parsed['draft'][0])
        self.assertNotContains(self.client.get(url), 'id="purchaseViberDialog"')

    def test_invalid_purchase_does_not_offer_viber(self):
        from .loyalty import izdaj_loyalty_karticu, loyalty_member_url
        card, user = izdaj_loyalty_karticu('Test', 'Kupac', '061234567')
        response = self.client.post(loyalty_member_url(card.kod), {'action': 'evidentiraj_kupovinu', 'iznos': '-10'}, follow=True)
        self.assertNotContains(response, 'id="purchaseViberDialog"')
