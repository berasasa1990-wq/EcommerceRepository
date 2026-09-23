from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction

from EcommerceApp.models import DeploymentVersion


class Command(BaseCommand):
    help = 'Increase the persistent public site version by 0.1 after a deploy.'

    def handle(self, *args, **options):
        with transaction.atomic():
            version, _ = DeploymentVersion.objects.select_for_update().get_or_create(pk=1)
            version.number += Decimal('0.1')
            version.save(update_fields=['number', 'updated_at'])
        self.stdout.write(self.style.SUCCESS(f'Site version is now {version.number:.1f}'))
