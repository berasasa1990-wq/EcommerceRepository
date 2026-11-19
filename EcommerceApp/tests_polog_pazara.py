from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import BankovniPolog, PologPazara


@override_settings(
    ALLOWED_HOSTS=['testserver'], SITE_PREP_ENABLED=False,
    STORAGES={
        'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
        'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
    },
)
class PologPazaraTests(TestCase):
    def test_bank_deposit_reduces_balance_once_and_preserves_cash_history(self):
        admin = get_user_model().objects.create_superuser(username='admin-banka')
        self.client.force_login(admin)
        url = reverse('staff_polog_pazara')
        original = PologPazara.objects.create(iznos=Decimal('150.25'), evidentirao=admin)
        initial = self.client.get(url)
        self.assertContains(initial, 'data-polog-bank-open')
        token = str(initial.context['banka_form']['zahtjev'].value())
        data = {'action': 'banka', 'banka-zahtjev': token, 'banka-iznos': '30,25'}

        too_much = self.client.post(url, {**data, 'banka-iznos': '150.26'})
        self.assertContains(too_much, 'Iznos za banku ne može biti veći od trenutnog salda.')
        self.assertContains(too_much, 'data-open-on-load')
        self.assertFalse(BankovniPolog.objects.exists())

        for _ in range(2):
            self.assertRedirects(self.client.post(url, data), url)
        self.assertEqual(BankovniPolog.objects.get().iznos, Decimal('30.25'))
        after = self.client.get(url)
        self.assertEqual(after.context['ukupno'], Decimal('120.00'))
        self.assertContains(after, 'Uzeto za banku')
        self.assertContains(after, '−30.25 KM')
        self.assertEqual(
            [(row.bankovni, row.iznos, row.osoba) for row in after.context['page']],
            [(True, Decimal('30.25'), admin.username), (False, original.iznos, admin.username)],
        )
        remaining_token = str(after.context['banka_form']['zahtjev'].value())
        self.assertRedirects(self.client.post(url, {
            **data, 'banka-zahtjev': remaining_token, 'banka-iznos': '120',
        }), url)
        self.assertEqual(self.client.get(url).context['ukupno'], Decimal('0.00'))

    def test_history_popup_lists_ten_deposits_per_page(self):
        admin = get_user_model().objects.create_superuser(username='admin-polog-historija')
        self.client.force_login(admin)
        PologPazara.objects.bulk_create([
            PologPazara(evidentirao=admin, iznos=amount, upisao=PologPazara.Upisao.BORISLAV)
            for amount in range(1, 12)
        ])
        BankovniPolog.objects.create(iznos=Decimal('5.25'), evidentirao=admin)
        url = reverse('staff_polog_pazara')
        first = self.client.get(url)
        self.assertContains(first, 'data-polog-history-open')
        self.assertNotContains(first, 'data-open-on-load')
        self.assertContains(first, '?historija=1&amp;page=2')
        self.assertEqual([p.iznos for p in first.context['page']], [Decimal('5.25'), *range(11, 2, -1)])
        self.assertEqual(first.context['ukupno'], Decimal('60.75'))

        second = self.client.get(url, {'historija': '1', 'page': 2})
        self.assertContains(second, 'data-open-on-load')
        self.assertContains(second, '?historija=1&amp;page=1')
        self.assertEqual([p.iznos for p in second.context['page']], [2, 1])
        self.assertEqual(second.context['ukupno'], Decimal('60.75'))

    def test_only_superuser_can_record_and_view_a_deposit(self):
        users = get_user_model()
        staff = users.objects.create_user(username='staff-polog', is_staff=True)
        admin = users.objects.create_superuser(username='admin-polog')
        url = reverse('staff_polog_pazara')
        panel = reverse('staff_admin_panel')
        data = {'datum': '2000-01-01', 'iznos': '150,25', 'napomena': 'Polog za smjenu', 'upisao': 'sasa'}

        self.client.force_login(staff)
        self.assertNotContains(self.client.get(panel), url)
        self.assertEqual(self.client.get(url).status_code, 302)
        self.assertEqual(self.client.post(url, data).status_code, 302)
        self.assertFalse(PologPazara.objects.exists())

        self.client.force_login(admin)
        self.assertContains(self.client.get(panel), url)
        page = self.client.get(url)
        self.assertEqual(page.context['ukupno'], Decimal('0.00'))
        self.assertContains(page, f'datetime="{timezone.localdate().isoformat()}"')
        self.assertNotContains(page, 'name="datum"')
        self.assertNotContains(page, 'name="napomena"')
        for person in ('Borislav', 'Sasa', 'Bojan'):
            self.assertContains(page, f'>{person}</button>')
        self.assertEqual(self.client.post(url, {**data, 'iznos': '-1'}).status_code, 200)
        for invalid_person in ('', 'nepoznat'):
            self.assertEqual(self.client.post(url, {**data, 'upisao': invalid_person}).status_code, 200)
        self.assertFalse(PologPazara.objects.exists())
        response = self.client.post(url, data, follow=True)
        self.assertEqual(response.context['ukupno'], Decimal('150.25'))
        self.assertContains(response, '<td>Sasa</td>', html=True)
        self.assertNotContains(response, 'Polog za smjenu')
        self.assertIn('no-store', response['Cache-Control'])
        polog = PologPazara.objects.get()
        self.assertEqual(polog.datum, timezone.localdate())
        self.assertEqual(polog.iznos, Decimal('150.25'))
        self.assertEqual(polog.upisao, PologPazara.Upisao.SASA)
        self.assertEqual(polog.napomena, '')
        self.assertEqual(polog.evidentirao, admin)
