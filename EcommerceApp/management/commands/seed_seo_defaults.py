"""
Upiši / osvježi best-practice SEO defaults na PageSEO i SiteSettings.

  python manage.py seed_seo_defaults
  python manage.py seed_seo_defaults --force   # prepiši i popunjena polja
"""

from django.core.management.base import BaseCommand

from EcommerceApp.models import SiteSettings
from EcommerceApp.utils.seo import PAGE_SEO_DEFAULTS, apply_page_seo_defaults


class Command(BaseCommand):
    help = 'Seed best-practice SEO sadržaja (PageSEO + SiteSettings fallbacks)'

    def add_arguments(self, parser):
        parser.add_argument(
            '--force',
            action='store_true',
            help='Prepiši i polja koja su već unesena (oprezno).',
        )

    def handle(self, *args, **options):
        force = options['force']
        n = apply_page_seo_defaults(only_empty=not force)
        self.stdout.write(self.style.SUCCESS(f'PageSEO: ažurirano {n} stranica'))

        home = PAGE_SEO_DEFAULTS.get('home', {})
        for ss in SiteSettings.objects.all():
            changed = False
            if force or not (ss.seo_title or '').strip():
                if home.get('seo_title'):
                    ss.seo_title = home['seo_title'][:70]
                    changed = True
            if force or not (ss.meta_description or '').strip():
                if home.get('meta_description'):
                    ss.meta_description = home['meta_description'][:160]
                    changed = True
            if changed:
                ss.save()
                self.stdout.write(self.style.SUCCESS('SiteSettings: SEO fallbacks ažurirani'))
            else:
                self.stdout.write('SiteSettings: bez izmjena (već popunjeno)')

        self.stdout.write(self.style.WARNING(
            'Dalje ručno: kategorije (title+opis+tekst), OG slika 1200×630, '
            'Google Search Console verification, Instagram URL. '
            'Provjeri /sitemap.xml i Search Console.'
        ))
