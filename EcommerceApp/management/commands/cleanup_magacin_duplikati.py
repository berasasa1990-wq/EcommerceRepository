from django.core.management.base import BaseCommand

from EcommerceApp.magacin import cleanup_duplicate_identities


class Command(BaseCommand):
    help = (
        'Obriši duple artikle u Magacinu (isti naziv, šifra ili barkod). '
        'Zadrži original (narudžbe / zaliha / prava šifra).'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Samo ispiši šta bi se obrisalo, ne briši.',
        )

    def handle(self, *args, **options):
        result = cleanup_duplicate_identities(dry_run=options['dry_run'])
        deleted = result['obrisano']
        skipped = result['preskoceno']
        if options['dry_run']:
            self.stdout.write(f'Pregled: {len(deleted)} bi se obrisalo, {len(skipped)} preskočeno.')
        else:
            self.stdout.write(self.style.SUCCESS(
                f'Obrisano {len(deleted)} duplikata, preskočeno {len(skipped)}.'
            ))
        for row in deleted:
            self.stdout.write(
                f"  - #{row['pk']} {row['naziv']!r} ({row['sifra']}) "
                f"odoo={row['odoo_template_id']} → zadržan #{row['zadrzan']}"
            )
        for row in skipped:
            self.stdout.write(self.style.WARNING(
                f"  ! #{row['pk']} {row['naziv']!r} ({row['sifra']}) {row['razlog']}"
            ))
