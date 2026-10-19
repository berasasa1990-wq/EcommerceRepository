import imaplib
from decimal import Decimal
from email.message import EmailMessage
from unittest.mock import MagicMock, patch

from django.core.cache import cache
from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase, override_settings
from django.template import Context
from django.template.loader import get_template
from django.template.loader_tags import BlockNode
from django.urls import reverse
from types import SimpleNamespace

from .company_mail import CompanyMailError, fetch_company_mail, parse_message, payment_amounts
from .views import staff_company_debts


def email_bytes(subject='Dnevni izvod', body='Željko Test Iznos uplate: 1.234,56 KM\nStanje računa: 9.000,00 KM'):
    mail = EmailMessage()
    mail['Subject'] = subject
    mail['From'] = 'Banka <test@example.com>'
    mail['Date'] = 'Mon, 19 Oct 2026 10:00:00 +0200'
    mail.set_content(body)
    return mail.as_bytes()


@override_settings(COMPANY_MAIL_USER='test@example.com', COMPANY_MAIL_APP_PASSWORD='test-password', COMPANY_MAIL_FOLDER='INBOX')
class CompanyMailTests(SimpleTestCase):
    def setUp(self):
        cache.clear()

    def test_filter_matches_statement_contents_and_not_subject_or_sender(self):
        raw = email_bytes(subject='Dnevni izvod')
        self.assertIsNotNone(parse_message(raw, '12', 'žELJKO TEST'))
        self.assertIsNone(parse_message(raw, '12', 'Banka'))
        self.assertIsNone(parse_message(email_bytes(subject='Damjanovic', body='Nema tog imena u izvodu'), '12', 'Damjanovic'))

    def test_explicit_payment_is_distinct_from_account_balance(self):
        parsed = parse_message(email_bytes(), '1')
        self.assertEqual(len(parsed['payment_amounts']), 1)
        self.assertEqual(parsed['payment_amounts'][0]['value'], Decimal('1234.56'))
        self.assertEqual(parsed['payment_amounts'][0]['currency'], 'BAM')
        self.assertEqual(len(parsed['amounts']), 2)
        self.assertFalse(parsed['amounts'][1]['explicit'])
        amounts = payment_amounts('', 'Payment amount: USD 1,234.56\nIznos uplate: 25,00 EUR')
        self.assertEqual({(row['value'], row['currency']) for row in amounts}, {(Decimal('1234.56'), 'USD'), (Decimal('25.00'), 'EUR')})
        self.assertTrue(all(row['explicit'] for row in amounts))

    def test_negative_money_is_not_presented_as_positive_payment_and_duplicates_keep_label(self):
        self.assertEqual(payment_amounts('', 'Iznos uplate: -100,00 KM'), [])
        amounts = payment_amounts('Uplata 100,00 KM', 'Iznos uplate: 100,00 KM')
        self.assertEqual(len(amounts), 1)
        self.assertTrue(amounts[0]['explicit'])

    def test_html_is_plain_text_and_attachments_are_not_payment_text(self):
        mail = EmailMessage()
        mail['Subject'] = '<script>Naslov</script>'
        mail.set_content('<style>secret-style</style><p>Iznos uplate: <b>12,00 KM</b></p><script>alert(1)</script>', subtype='html')
        mail.add_attachment(b'Iznos uplate: 999,00 KM', maintype='application', subtype='pdf', filename='racun.pdf')
        result = parse_message(mail.as_bytes(), '2')
        self.assertNotIn('alert(1)', result['body'])
        self.assertNotIn('secret-style', result['body'])
        self.assertNotIn('999', result['body'])
        self.assertEqual(result['attachments'], ['racun.pdf'])
        self.assertEqual(result['payment_amounts'][0]['value'], Decimal('12.00'))

    @patch('EcommerceApp.company_mail.imaplib.IMAP4_SSL')
    def test_imap_is_read_only_search_uses_literal_and_peek(self, connect):
        client = connect.return_value
        client.select.return_value = ('OK', [b'3'])
        rows = [
            (b'3 (UID 3 BODY[] {200}', email_bytes()),
            (b'2 (UID 2 BODY[] {200}', email_bytes(subject='Druga osoba', body='Druga osoba Iznos uplate: 20,00 KM')),
        ]
        client.uid.side_effect = [('OK', [b'1 2 3']), ('OK', rows), ('OK', rows)]
        result = fetch_company_mail('Željko Test')
        client.select.assert_called_once_with('INBOX', readonly=True)
        self.assertEqual(client.literal, '"Željko Test" (subject:izvod OR subject:statement OR filename:pdf)'.encode('utf-8'))
        self.assertEqual(client.uid.call_args_list[0].args, ('search', None, 'NOT', 'SUBJECT', '"CMS"', 'X-GM-RAW'))
        self.assertIn('BODY.PEEK[]', client.uid.call_args_list[2].args[2])
        self.assertEqual(len(result['messages']), 1)
        client.logout.assert_called_once()
        fetch_company_mail('Željko Test')
        connect.assert_called_once()

    @patch('EcommerceApp.company_mail.imaplib.IMAP4_SSL')
    def test_pagination_fetches_only_page_of_latest_messages(self, connect):
        client = connect.return_value
        client.select.return_value = ('OK', [b'45'])
        client.uid.side_effect = [('OK', [b' '.join(str(i).encode() for i in range(1, 46))]), *[('OK', [])] * 5]
        result = fetch_company_mail(page=2)
        self.assertEqual(result['pages'], 3)
        self.assertTrue(result['has_previous'])
        self.assertTrue(result['has_next'])
        self.assertEqual(','.join(call.args[1] for call in client.uid.call_args_list[1:]), ','.join(str(i) for i in range(25, 5, -1)))

    @patch('EcommerceApp.company_mail.imaplib.IMAP4_SSL')
    def test_errors_do_not_disclose_credentials_or_raw_server_output(self, connect):
        connect.return_value.login.side_effect = imaplib.IMAP4.error('private server detail test-password')
        with self.assertRaises(CompanyMailError) as error:
            fetch_company_mail()
        self.assertNotIn('test-password', str(error.exception))
        self.assertNotIn('private server detail', str(error.exception))


    @override_settings(ALLOWED_HOSTS=['testserver'])
    @patch('EcommerceApp.company_mail.fetch_company_mail')
    def test_non_admin_cannot_trigger_mail_fetch(self, fetch):
        request = RequestFactory().get(reverse('staff_company_debts'))
        request.user = SimpleNamespace(is_authenticated=True, is_superuser=False)
        self.assertEqual(staff_company_debts(request).status_code, 302)
        fetch.assert_not_called()

    def test_pdf_matching_reads_only_named_credit_and_not_debit_or_other_person(self):
        from io import BytesIO
        from pypdf import PdfWriter
        from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject
        writer = PdfWriter()
        page = writer.add_blank_page(width=800, height=900)
        page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'): DictionaryObject({
            NameObject('/F1'): DictionaryObject({NameObject('/Type'): NameObject('/Font'),
                NameObject('/Subtype'): NameObject('/Type1'), NameObject('/BaseFont'): NameObject('/Courier')})})})
        lines = [
            'CARPOLOGIJA BH',
            'Racun                       Duguje          Potrazuje',
            '111-222-12345678-90          0.00            1,500.00',
            '19.10.26    DAMJANOVIC PROMEX DOO',
            '111-222-12345678-91          0.00            9,000.00',
            '19.10.26    Druga Firma',
            '111-222-12345678-92          500.00          0.00',
            '19.10.26    Damjanovic Plata',
        ]
        stream = DecodedStreamObject()
        stream.set_data(('BT /F1 10 Tf 30 820 Td ' + ' 0 -18 Td '.join('(' + line + ') Tj' for line in lines) + ' ET').encode())
        page[NameObject('/Contents')] = writer._add_object(stream)
        output = BytesIO(); writer.write(output)
        mail = EmailMessage()
        mail['Subject'] = 'BAM izvod po racunu'
        mail.set_content('Izvod je u prilogu.')
        mail.add_attachment(output.getvalue(), maintype='application', subtype='pdf', filename='izvod.pdf')
        parsed = parse_message(mail.as_bytes(), '3', 'Damjanovic')
        self.assertIsNotNone(parsed)
        self.assertEqual(len(parsed['statement_matches']), 1)
        self.assertEqual(parsed['payment_amounts'][0]['value'], Decimal('1500.00'))
        self.assertNotIn('Druga Firma', parsed['statement_matches'][0]['text'])
        all_credits = parse_message(mail.as_bytes(), '3', None)
        self.assertEqual(len(all_credits['statement_matches']), 2)
        self.assertEqual({row['payer'] for row in all_credits['statement_matches']}, {'DAMJANOVIC PROMEX DOO', 'Druga Firma'})
        self.assertEqual({row['amount'] for row in all_credits['statement_matches']}, {Decimal('1500.00'), Decimal('9000.00')})


    def test_monthly_statement_date_rows_and_unknown_layout_never_guess_balance(self):
        from .company_statements import statement_matches
        text = 'Dat. promjene Opis Duguje Potrazuje\n19.10.2026 1112221234567890 Damjanovic 0.00 345.67\n20.10.2026 1112221234567891 Drugi 0.00 999.00'
        rows = statement_matches(text, 'Damjanovic', 'Izvod', 'BAM')
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['amount'], Decimal('345.67'))
        rows = statement_matches('Damjanović\nStanje racuna: 9.000,00 KM', 'Damjanovic', 'Nepoznat izvod', 'Bankovni izvod')
        self.assertEqual(len(rows), 1)
        self.assertIsNone(rows[0]['amount'])

    def test_order_notifications_and_reviews_are_not_bank_statements(self):
        self.assertIsNone(parse_message(email_bytes(subject='Nova narudzba', body='Primili ste narudzbu od Damjanovic. Iznos uplate: 100,00 KM'), '4', 'Damjanovic'))
        self.assertIsNone(parse_message(email_bytes(subject='Nova recenzija', body='Borislav Damjanovic je ostavio ocjenu.'), '5', 'Damjanovic'))


    @patch('EcommerceApp.company_mail.imaplib.IMAP4_SSL')
    def test_cms_bodies_are_never_fetched_even_if_search_returns_them(self, connect):
        client = connect.return_value
        client.select.return_value = ('OK', [b'2'])
        client.uid.side_effect = [
            ('OK', [b'1 2']),
            ('OK', [(b'1 (UID 1)', b'Subject: Bankovni izvod\r\n\r\n'),
                    (b'2 (UID 2)', b'Subject: cMs mjesecni izvod\r\n\r\n')]),
            ('OK', [(b'1 (UID 1)', email_bytes())]),
        ]
        fetch_company_mail()
        body_fetch = client.uid.call_args_list[2]
        self.assertEqual(body_fetch.args[1], '1')
        self.assertIn('BODY.PEEK[]', body_fetch.args[2])
        with patch('EcommerceApp.company_statements.pdf_statement_matches') as pdf:
            self.assertIsNone(parse_message(email_bytes(subject='Izvod Cms za racun'), '2', 'Damjanovic'))
            pdf.assert_not_called()

    def test_all_statement_mode_rejects_different_account_owner(self):
        from .company_statements import pdf_statement_matches
        page = MagicMock()
        page.extract_text.return_value = 'DRUGA FIRMA\nRacun Duguje Potrazuje'
        reader = MagicMock()
        reader.is_encrypted = False
        reader.pages = [page]
        with patch('pypdf.PdfReader', return_value=reader):
            rows, warning = pdf_statement_matches(b'%PDF-1.7 fake %%EOF', None, 'Izvod.pdf')
        self.assertEqual(rows, [])
        self.assertIn('Carpologije', warning)

    @patch('EcommerceApp.company_mail.imaplib.IMAP4_SSL')
    def test_all_statements_search_has_no_payer_limit_and_only_new_uids(self, connect):
        client = connect.return_value
        client.select.return_value = ('OK', [b'3'])
        client.uid.side_effect = [('OK', [b'10 20 30']), ('OK', [])]
        result = fetch_company_mail(None, since_uid=20)
        self.assertNotIn(b'Damjanovic', client.literal)
        self.assertIn('UID', client.uid.call_args_list[0].args)
        self.assertIn('21:*', client.uid.call_args_list[0].args)
        self.assertEqual(client.uid.call_args_list[1].args[1], '30')
        self.assertEqual(result['latest_uid'], 30)

    def test_carpologija_as_counterparty_is_not_proof_of_account_ownership(self):
        from .company_statements import pdf_statement_matches
        page = MagicMock()
        page.extract_text.return_value = 'DRUGA FIRMA DOO\nRacun Duguje Potrazuje\n111-222-12345678-90 0.00 500.00\n19.10.26 CARPOLOGIJA BH'
        reader = MagicMock()
        reader.is_encrypted = False
        reader.pages = [page]
        with patch('pypdf.PdfReader', return_value=reader):
            rows, warning = pdf_statement_matches(b'%PDF-1.7 fake %%EOF', None, 'Izvod.pdf')
        self.assertEqual(rows, [])
        self.assertTrue(warning)


from django.test import TestCase
from .models import CompanyPayment


@override_settings(STORAGES={
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
})
class SavedCompanyPaymentTests(TestCase):
    def test_address_suffixes_group_under_one_company_with_all_payments(self):
        for index, name in enumerate([
            'AJDARED DOO SREBRENIK 9',
            'AJDARED DOO SREBRENIK 9 SEPTEMBAR 7 75350',
            'ajdared doo srebrenik',
        ]):
            CompanyPayment.objects.create(source_key=f'address-{index}', source_verified=True,
                                          payer=name, amount='100.00', currency='BAM')
        CompanyPayment.objects.create(source_key='address-eur', source_verified=True,
                                      payer='AJDARED DOO SREBRENIK 9', amount='25.00', currency='EUR')
        response = self.client.get(reverse('staff_company_debts'))
        groups = response.context['payment_groups']
        self.assertEqual(len(groups), 2)
        self.assertEqual(groups[0]['company'], 'AJDARED DOO SREBRENIK')
        self.assertEqual(groups[0]['total'], Decimal('300.00'))
        self.assertEqual(len(groups[0]['payments']), 3)
        self.assertEqual(groups[1]['currency'], 'EUR')
        self.assertEqual(groups[1]['total'], Decimal('25.00'))
        self.assertEqual(CompanyPayment.objects.get(source_key='address-0').payer, 'AJDARED DOO SREBRENIK 9')

    def test_display_name_phrases_match_case_accents_and_only_listed_names(self):
        from .company_payment_filters import DISPLAY_PAYER_PHRASES, display_payer_allowed
        for phrase in DISPLAY_PAYER_PHRASES:
            with self.subTest(phrase=phrase):
                self.assertTrue(display_payer_allowed('Firma ' + phrase.lower() + ' poslovnica'))
        for name in ['RIBARSKA PRIČA', 'Velić Beton', 'TR CVJEĆARA PET SHOP LEO']:
            self.assertTrue(display_payer_allowed(name))
        for name in ['', 'Druga firma', 'Damjanovic', 'Bistro', 'lovac', 'preprelica']:
            self.assertFalse(display_payer_allowed(name))

    def setUp(self):
        from django.contrib.auth import get_user_model
        cache.clear()
        self.user = get_user_model().objects.create(username='payments-owner', is_superuser=True, is_staff=True)
        self.client.force_login(self.user)

    @patch('EcommerceApp.company_mail.fetch_company_mail', side_effect=AssertionError('Page must never contact Gmail'))
    def test_page_only_shows_allowed_positive_payers(self, fetch):
        CompanyPayment.objects.create(source_verified=True, source_key='positive', payer='DAMJANOVIC PROMEX DOO', amount='1500.00', currency='BAM')
        CompanyPayment.objects.create(source_verified=True, source_key='negative', payer='DAMJANOVIC PROMEX DOO', amount='-300.00', currency='BAM')
        CompanyPayment.objects.create(source_verified=True, source_key='other', payer='Druga firma', amount='9000.00', currency='BAM')
        for params in [{}, {'q': 'Druga firma', 'page': 999}]:
            response = self.client.get(reverse('staff_company_debts'), params)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(len(response.context['payments']), 1)
            self.assertEqual({payment.amount for payment in response.context['payments']}, {Decimal('1500.00')})
            self.assertNotContains(response, 'Prikaži poruku')
            self.assertNotContains(response, 'Povezano')
            self.assertEqual(response['Cache-Control'], 'private, no-store')
        fetch.assert_not_called()

    @patch('EcommerceApp.company_mail.fetch_company_mail', side_effect=AssertionError('No live fetch'))
    def test_empty_page_remains_fast_and_does_not_import_automatically(self, fetch):
        response = self.client.get(reverse('staff_company_debts'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Nema sačuvanih uplata')
        fetch.assert_not_called()

    def test_explicit_import_is_idempotent_and_excludes_non_payments(self):
        from django.core.management import call_command
        from io import StringIO
        payload = {'messages': [{'uid': '1', 'date': None, 'statement_matches': [
            {'source': 'Izvod.pdf', 'text': 'Damjanovic credit', 'amount': Decimal('1500.00'), 'currency': 'BAM'},
            {'source': 'Izvod.pdf', 'text': 'Unknown', 'amount': None, 'currency': 'BAM'},
            {'source': 'Izvod.pdf', 'text': 'Debit', 'amount': Decimal('-500.00'), 'currency': 'BAM'},
        ]}], 'has_next': False}
        with patch('EcommerceApp.management.commands.import_company_payments.fetch_company_mail', return_value=payload):
            call_command('import_company_payments', filter='Damjanovic', stdout=StringIO())
            call_command('import_company_payments', filter='Damjanovic', stdout=StringIO())
        self.assertEqual(CompanyPayment.objects.count(), 1)

    @patch('EcommerceApp.company_mail.fetch_company_mail', side_effect=AssertionError('No HTTP sync'))
    def test_all_matching_companies_are_shown_without_filter_controls(self, fetch):
        CompanyPayment.objects.create(source_verified=True, source_key='alias', payer='bhstar', amount='150.00', currency='BAM', filter_matches={'bhstar': True, 'bh star': True})
        CompanyPayment.objects.create(source_verified=True, source_key='overlap', payer='Ribolovačka priča', amount='200.00', currency='BAM', filter_matches={'ribolovacka pri': True, 'lovac': True})
        for params in [{}, {'filter': 'bhstar'}, {'filter': 'all'}]:
            response = self.client.get(reverse('staff_company_debts'), params)
            self.assertEqual(response.status_code, 200)
            self.assertEqual({p.source_key for p in response.context['payments']}, {'alias', 'overlap'})
            self.assertEqual([g['company'] for g in response.context['payment_groups']], ['BHSTAR', 'RIBOLOVACKA PRICA'])
            self.assertNotContains(response, 'id="paymentFilter"')
            self.assertNotContains(response, 'Prikaži uplate')
            self.assertContains(response, '<dialog class="company-debts__dialog"', count=2)
        fetch.assert_not_called()

    def test_import_aliases_preserves_one_payment_and_both_filter_options(self):
        from django.core.management import call_command
        from io import StringIO
        payload = {'messages': [{'uid': 'alias', 'date': None, 'statement_matches': [
            {'source': 'Izvod.pdf', 'text': 'BH STAR', 'amount': Decimal('100.00'), 'currency': 'BAM'},
        ]}], 'has_next': False}
        with patch('EcommerceApp.management.commands.import_company_payments.fetch_company_mail', return_value=payload):
            call_command('import_company_payments', filter='bhstar', stdout=StringIO())
            call_command('import_company_payments', filter='bh star', stdout=StringIO())
        payment = CompanyPayment.objects.get()
        self.assertEqual(payment.payer, 'bhstar')
        self.assertTrue(payment.filter_matches['bhstar'])
        self.assertTrue(payment.filter_matches['bh star'])

    def test_saved_cms_and_unverified_legacy_payments_are_hidden(self):
        CompanyPayment.objects.create(source_key='cms', source_verified=True, source_subject='cMs izvod', payer='DAMJANOVIC PROMEX DOO', amount='999.00')
        CompanyPayment.objects.create(source_key='legacy', payer='DAMJANOVIC PROMEX DOO', amount='999.00')
        CompanyPayment.objects.create(source_key='ok', source_verified=True, source_subject='Dnevni izvod', payer='DAMJANOVIC PROMEX DOO', amount='150.00', currency='BAM')
        response = self.client.get(reverse('staff_company_debts'))
        self.assertEqual([p.source_key for p in response.context['payments']], ['ok'])

    def test_import_rejects_cms_payloads_and_marks_allowed_sources_verified(self):
        from django.core.management import call_command
        from io import StringIO
        row = {'source': 'izvod.pdf', 'text': 'Damjanovic', 'amount': Decimal('100.00'), 'currency': 'BAM'}
        payload = {'messages': [
            {'uid': 'cms', 'subject': 'CMS izvod', 'date': None, 'statement_matches': [row]},
            {'uid': 'allowed', 'subject': 'Dnevni izvod', 'date': None, 'statement_matches': [row]},
        ], 'has_next': False}
        with patch('EcommerceApp.management.commands.import_company_payments.fetch_company_mail', return_value=payload):
            call_command('import_company_payments', filter='Damjanovic', stdout=StringIO())
        payment = CompanyPayment.objects.get()
        self.assertTrue(payment.source_verified)
        self.assertEqual(payment.source_uid, 'allowed')
        self.assertEqual(payment.source_subject, 'Dnevni izvod')

    def test_unknown_currency_is_hidden_in_individual_and_all_filters(self):
        for key, currency in [('known', 'BAM'), ('blank', ''), ('unknown', 'XXX'), ('space', ' ')]:
            CompanyPayment.objects.create(source_key=key, source_verified=True, payer='DAMJANOVIC PROMEX DOO', amount='100.00', currency=currency)
        for selected in ['Damjanovic', 'all']:
            response = self.client.get(reverse('staff_company_debts'), {'filter': selected})
            self.assertEqual([p.source_key for p in response.context['payments']], ['known'])
            self.assertNotContains(response, 'valuta nije prepoznata')

    @patch('EcommerceApp.company_mail.fetch_company_mail', side_effect=AssertionError('No live fetch for totals'))
    def test_company_totals_keep_currencies_separate_and_popup_dates_sorted(self, fetch):
        from django.utils import timezone
        from datetime import timedelta
        now = timezone.now()
        CompanyPayment.objects.create(source_key='new', source_verified=True, payer='DAMJANOVIC PROMEX DOO', amount='100.10', currency='BAM', date=now)
        CompanyPayment.objects.create(source_key='old', source_verified=True, payer='DAMJANOVIC PROMEX DOO', amount='200.20', currency='BAM', date=now-timedelta(days=3))
        CompanyPayment.objects.create(source_key='euro', source_verified=True, payer='DAMJANOVIC PROMEX DOO', amount='50.00', currency='EUR', date=now)
        CompanyPayment.objects.create(source_key='no-date', source_verified=True, payer='DAMJANOVIC PROMEX DOO', amount='10.00', currency='BAM')
        CompanyPayment.objects.create(source_key='unknown-currency', source_verified=True, payer='DAMJANOVIC PROMEX DOO', amount='999.00', currency='')
        CompanyPayment.objects.create(source_key='cms-total', source_verified=True, source_subject='CMS izvod', payer='DAMJANOVIC PROMEX DOO', amount='999.00', currency='BAM')
        response = self.client.get(reverse('staff_company_debts'))
        groups = response.context['payment_groups']
        self.assertEqual(len(groups), 2)
        self.assertEqual(groups[0]['currency'], 'BAM')
        self.assertEqual(groups[0]['total'], Decimal('310.30'))
        self.assertEqual([p.source_key for p in groups[0]['payments']], ['new', 'old', 'no-date'])
        self.assertEqual(groups[1]['currency'], 'EUR')
        self.assertEqual(groups[1]['total'], Decimal('50.00'))
        self.assertContains(response, 'data-payments-dialog="companyPayments1"')
        self.assertContains(response, '<dialog class="company-debts__dialog"', count=2)
        self.assertContains(response, 'company-payments.js')
        self.assertNotContains(response, '999,00')
        fetch.assert_not_called()

    def test_all_payer_import_updates_names_and_advances_checkpoint_only_on_success(self):
        from django.core.management import call_command, CommandError
        from io import StringIO
        from .models import CompanyStatementImportState
        payload = {'messages': [{'uid': '777', 'date': None, 'subject': 'Dnevni izvod', 'statement_matches': [
            {'source': 'Izvod.pdf', 'text': 'Nova firma', 'payer': 'NOVA FIRMA DOO', 'amount': Decimal('25.00'), 'currency': 'BAM'},
        ]}], 'has_next': False, 'latest_uid': 777}
        with patch('EcommerceApp.management.commands.import_company_payments.fetch_company_mail', return_value=payload) as fetch:
            call_command('import_company_payments', stdout=StringIO())
            fetch.assert_called_once_with(None, 1, since_uid=0)
        self.assertEqual(CompanyPayment.objects.get().payer, 'NOVA FIRMA DOO')
        self.assertEqual(CompanyStatementImportState.objects.get().last_uid, 777)
        with patch('EcommerceApp.management.commands.import_company_payments.fetch_company_mail', side_effect=CompanyMailError('test')):
            with self.assertRaises(CommandError):
                call_command('import_company_payments', stdout=StringIO(), stderr=StringIO())
        self.assertEqual(CompanyStatementImportState.objects.get().last_uid, 777)

    def test_new_email_arriving_during_pagination_is_not_skipped_by_checkpoint(self):
        from django.core.management import call_command
        from io import StringIO
        from .models import CompanyStatementImportState
        pages = [
            {'messages': [], 'has_next': True, 'latest_uid': 100},
            {'messages': [], 'has_next': False, 'latest_uid': 101},
        ]
        with patch('EcommerceApp.management.commands.import_company_payments.fetch_company_mail', side_effect=pages):
            call_command('import_company_payments', stdout=StringIO())
        self.assertEqual(CompanyStatementImportState.objects.get().last_uid, 100)


class CompanyPaymentWorkerTests(SimpleTestCase):
    @override_settings(COMPANY_MAIL_APP_PASSWORD='configured-test-password')
    @patch('EcommerceApp.company_payments_worker.run_import_cycle')
    def test_worker_checks_every_ten_minutes_without_http(self, cycle):
        from EcommerceApp.company_payments_worker import run_worker
        stop = MagicMock()
        stop.is_set.return_value = False
        stop.wait.side_effect = [False, True]
        run_worker(stop)
        self.assertEqual(cycle.call_count, 2)
        self.assertEqual([call.args[0] for call in stop.wait.call_args_list], [600, 600])

    @override_settings(COMPANY_MAIL_APP_PASSWORD='configured-test-password')
    @patch('EcommerceApp.company_payments_worker.run_import_cycle', side_effect=RuntimeError('private detail'))
    def test_worker_retries_after_failure_without_leaking_details(self, cycle):
        from EcommerceApp.company_payments_worker import run_worker
        stop = MagicMock()
        stop.is_set.return_value = False
        stop.wait.side_effect = [False, True]
        with self.assertLogs('EcommerceApp.company_payments_worker', level='WARNING') as logs:
            run_worker(stop)
        self.assertEqual(cycle.call_count, 2)
        self.assertNotIn('private detail', '\n'.join(logs.output))

    @override_settings(COMPANY_MAIL_APP_PASSWORD='')
    @patch('EcommerceApp.company_payments_worker.run_import_cycle')
    def test_unconfigured_worker_does_not_connect(self, cycle):
        from EcommerceApp.company_payments_worker import run_worker
        stop = MagicMock()
        stop.is_set.return_value = False
        stop.wait.return_value = True
        run_worker(stop)
        cycle.assert_not_called()

    @patch('EcommerceApp.company_payments_worker.close_old_connections')
    @patch('EcommerceApp.company_payments_worker.call_command', side_effect=RuntimeError('test'))
    def test_import_closes_database_connections_after_failure(self, command, close):
        from EcommerceApp.company_payments_worker import run_import_cycle
        with self.assertRaises(RuntimeError):
            run_import_cycle()
        self.assertEqual(close.call_count, 2)

    def test_incomplete_pdf_is_skipped_without_expensive_repair(self):
        from .company_statements import pdf_statement_matches
        with patch('pypdf.PdfReader') as reader:
            rows, warning = pdf_statement_matches(b'%PDF-1.7 incomplete', 'Damjanovic', 'Izvod.pdf')
        self.assertEqual(rows, [])
        self.assertTrue(warning)
        reader.assert_not_called()
