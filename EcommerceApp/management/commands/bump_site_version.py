from django.core.management.base import BaseCommand
from django.db import transaction

from EcommerceApp.models import DeploymentVersion


class Command(BaseCommand):
    help = 'Increase the semantic public site patch version after a deploy.'

    @staticmethod
    def _next_patch(raw):
        parts = str(raw or '1.1.3').split('.')
        if not all(part.isdigit() for part in parts) or len(parts) > 3:
            return '1.1.3'
        parts = (parts + ['0', '0'])[:3]
        parts[2] = str(int(parts[2]) + 1)
        return '.'.join(parts)

    def handle(self, *args, **options):
        with transaction.atomic():
            version, _ = DeploymentVersion.objects.select_for_update().get_or_create(pk=1)
            version.number = self._next_patch(version.number)
            version.save(update_fields=['number', 'updated_at'])
        self.stdout.write(self.style.SUCCESS(f'Site version is now {version.number}'))
