"""Explicit one-off import; never runs during a page request."""
import hashlib

from django.conf import settings
from django.db import transaction
from django.core.management.base import BaseCommand, CommandError

from EcommerceApp.company_mail import CompanyMailError, fetch_company_mail, excluded_mail_subject
from EcommerceApp.models import CompanyPayment, CompanyStatementImportState
from EcommerceApp.company_payment_filters import PAYMENT_FILTERS, FILTER_PAYERS


class Command(BaseCommand):
    help = 'Sačuvaj sve prepoznate prilive na račun Carpologije iz Gmail izvoda.'

    def add_arguments(self, parser):
        parser.add_argument('--filter', choices=list(FILTER_PAYERS), help='Opcionalno ograniči ručni uvoz na jedan naziv.')
        parser.add_argument('--full', action='store_true', help='Ponovo provjeri cijelu historiju izvoda.')

    def handle(self, *args, **options):
        created_count = 0
        filters = [(options['filter'], FILTER_PAYERS[options['filter']])] if options.get('filter') else [(None, None)]
        state_key = hashlib.sha256((settings.COMPANY_MAIL_USER + ':' + settings.COMPANY_MAIL_FOLDER).encode()).hexdigest()
        state, _ = CompanyStatementImportState.objects.get_or_create(key=state_key)
        since_uid = 0 if options.get('full') else state.last_uid
        latest_uid = since_uid
        errors = 0
        for phrase, payer in filters:
            page = 1
            try:
                while True:
                    result = fetch_company_mail(phrase, page, since_uid=since_uid) if phrase is None else fetch_company_mail(phrase, page)
                    if page == 1:
                        latest_uid = max(latest_uid, result.get('latest_uid', since_uid))
                    for message in result['messages']:
                        if excluded_mail_subject(message.get('subject', '')):
                            continue
                        for row in message.get('statement_matches', []):
                            if row['amount'] is None or row['amount'] <= 0:
                                continue
                            actual_payer = row.get('payer') or payer or 'Uplatilac bez naziva'
                            identity = '\n'.join([settings.COMPANY_MAIL_USER, settings.COMPANY_MAIL_FOLDER,
                                                   message['uid'], row['source'], row['text'],
                                                   str(row['amount']), row['currency']])
                            with transaction.atomic():
                                payment, created = CompanyPayment.objects.select_for_update().get_or_create(
                                    source_key=hashlib.sha256(identity.encode()).hexdigest(),
                                    defaults={'date': message['date'], 'payer': actual_payer,
                                              'amount': row['amount'], 'currency': row['currency']},
                                )
                                flags = dict(payment.filter_matches)
                                flags.update({alias: True for alias, canonical in PAYMENT_FILTERS if canonical == payer})
                                payment.filter_matches = flags
                                if phrase is None:
                                    payment.payer = actual_payer[:160]
                                payment.source_subject = message.get('subject', '')[:998]
                                payment.source_uid = message['uid']
                                payment.source_verified = True
                                payment.save(update_fields=['filter_matches', 'source_subject', 'source_uid', 'source_verified', 'payer'])
                            created_count += int(created)
                    if phrase is None:
                        self.stdout.write(f'Obrađena stranica izvoda {page}; sačuvano {created_count} novih uplata.')
                        self.stdout.flush()
                    if not result['has_next']:
                        break
                    page += 1
            except CompanyMailError:
                errors += 1
                self.stderr.write('Provjera izvoda nije dovršena. Sačuvani podaci ostaju dostupni.')
        if errors:
            raise CommandError(f'Neuspješna provjera {errors} filtera. Sačuvane uplate ostaju dostupne.')
        if not options.get('filter'):
            with transaction.atomic():
                saved_state = CompanyStatementImportState.objects.select_for_update().get(key=state_key)
                saved_state.last_uid = max(saved_state.last_uid, latest_uid)
                saved_state.save(update_fields=['last_uid'])
        self.stdout.write(self.style.SUCCESS(f'Sačuvano {created_count} novih uplata. Otvaranje stranice ne pristupa Gmailu.'))
