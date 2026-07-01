from django.core.management import call_command
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Ponovo obrađuje sve slike: artikle, varijacije, banere i vlogove.'

    def handle(self, *args, **options):
        call_command('reprocess_product_images')
        call_command('reprocess_banner_images')
        call_command('reprocess_vlog_images')
        self.stdout.write(self.style.SUCCESS('Sve slike su ponovo obrađene.'))