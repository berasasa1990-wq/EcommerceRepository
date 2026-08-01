"""Učitaj osnovne ribolovačke sinonime za pretragu."""

from django.core.management.base import BaseCommand

from EcommerceApp.search.synonyms import seed_default_synonyms


class Command(BaseCommand):
    help = (
        'Kreira / dopunjava grupe sinonima za search '
        '(štap, mašinica, feeder, šaran, …). Idempotentno.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--reactivate',
            action='store_true',
            help='Ponovo aktiviraj grupe koje su isključene (samo default grupe po nazivu).',
        )

    def handle(self, *args, **options):
        stats = seed_default_synonyms(clear_inactive=options['reactivate'])
        self.stdout.write(self.style.SUCCESS(
            'Sinonimi: '
            f"+{stats['groups_created']} grupa, +{stats['terms_created']} pojmova "
            f"(ukupno {stats['groups_total']} grupa / {stats['terms_total']} pojmova)."
        ))
