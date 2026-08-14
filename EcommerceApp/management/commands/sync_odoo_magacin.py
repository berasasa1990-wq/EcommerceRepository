from django.core.management.base import BaseCommand, CommandError

from EcommerceApp.magacin import MagacinError, persist_sync_job, run_sync_chunk, start_full_sync


class Command(BaseCommand):
    help = (
        'Odoo → Magacin na ovom serveru: doda artikle kojih nema, '
        'prepiše zalihe/lokacije. Pokreni na Render Shell, ne ovisi o browseru.'
    )

    def handle(self, *args, **options):
        self.stdout.write('Pokrećem puni Odoo → Magacin sync…')
        try:
            job = start_full_sync()
            persist_sync_job(job)
            n = 0
            while not job.get('done'):
                n += 1
                phase = job.get('phase') or '?'
                extra = ''
                if phase == 'discover':
                    extra = f' pročitano {len(job.get("discovered_ids") or [])}'
                elif phase == 'catalog':
                    extra = (
                        f' {job.get("position") or 0}/{len(job.get("template_ids") or [])}'
                        f' novo {job.get("kreirano") or 0}'
                    )
                elif phase == 'stock':
                    extra = f' {job.get("stock_position") or 0}/{len(job.get("stock_ids") or [])}'
                self.stdout.write(f'  [{n}] {phase}{extra}')
                job = run_sync_chunk(job)
                persist_sync_job(job)
        except MagacinError as exc:
            raise CommandError(str(exc)) from exc

        if job.get('error'):
            raise CommandError(job['error'])
        self.stdout.write(self.style.SUCCESS(
            f'Gotovo. Novo {job.get("kreirano") or 0}, '
            f'ažurirano {job.get("azurirano") or 0}, '
            f'zaliha {job.get("zaliha") or 0}, '
            f'artikala {job.get("artikala") or 0}.'
        ))
