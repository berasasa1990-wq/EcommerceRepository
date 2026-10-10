"""Assign legacy magacin locations and physical quantities to the new WMS."""
import json
from pathlib import Path
from django.core.management.base import BaseCommand, CommandError
from EcommerceApp.wms_legacy_import import import_magacin_locations, MagacinImportError


class Command(BaseCommand):
    help = 'Prenosi lokacije i fizičke količine iz magacina u WMS; podrazumijevano samo pregled.'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true')
        parser.add_argument('--backup', help='JSON snimak postojećih WMS veza prije upisa.')

    def handle(self, *args, **options):
        if options['apply'] and not options['backup']:
            raise CommandError('Za upis navedite --backup putanju.')

        def save_backup(before):
            backup = Path(options['backup'])
            backup.parent.mkdir(parents=True, exist_ok=True)
            try:
                with backup.open('x') as file:
                    json.dump(before, file, ensure_ascii=False, indent=2)
                    file.write('\n')
            except FileExistsError as exc:
                raise CommandError('Backup već postoji; navedite novu putanju da se prethodni snimak sačuva.') from exc

        try:
            result = import_magacin_locations(dry_run=not options['apply'], backup_callback=save_backup if options['apply'] else None)
        except MagacinImportError as exc:
            raise CommandError(str(exc)) from exc
        if options['apply']:
            result['backup'] = str(Path(options['backup']))
        self.stdout.write(json.dumps(result, ensure_ascii=False))
