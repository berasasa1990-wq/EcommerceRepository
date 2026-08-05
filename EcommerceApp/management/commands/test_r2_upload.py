"""
Brza provjera R2 credentials: ListBucket + PutObject + DeleteObject.

  python manage.py test_r2_upload
"""
from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Test PutObject na Cloudflare R2 (dijagnostika AccessDenied).'

    def handle(self, *args, **options):
        if not getattr(settings, 'USE_R2_MEDIA', False):
            self.stderr.write(self.style.ERROR('USE_R2_MEDIA=False — R2 env nije kompletan.'))
            return

        self.stdout.write(f'USE_R2_MEDIA: {settings.USE_R2_MEDIA}')
        self.stdout.write(f'MEDIA_URL: {settings.MEDIA_URL}')
        self.stdout.write(f'BUCKET: {getattr(settings, "AWS_STORAGE_BUCKET_NAME", "")}')
        self.stdout.write(f'ENDPOINT: {getattr(settings, "AWS_S3_ENDPOINT_URL", "")}')
        self.stdout.write(f'STORAGE: {default_storage.__class__.__module__}.{default_storage.__class__.__name__}')
        self.stdout.write(f'CUSTOM_DOMAIN: {getattr(settings, "AWS_S3_CUSTOM_DOMAIN", "")}')

        key = '_r2_healthcheck/test.txt'
        body = b'r2-ok'
        try:
            if default_storage.exists(key):
                default_storage.delete(key)
            saved = default_storage.save(key, ContentFile(body, name='test.txt'))
            url = default_storage.url(saved)
            self.stdout.write(self.style.SUCCESS(f'PutObject OK → {saved}'))
            self.stdout.write(f'URL: {url}')
            # cleanup
            try:
                default_storage.delete(saved)
                self.stdout.write('DeleteObject OK (cleanup)')
            except Exception as exc:
                self.stdout.write(self.style.WARNING(f'Cleanup delete failed: {exc}'))
        except Exception as exc:
            self.stderr.write(self.style.ERROR(f'FAIL: {exc}'))
            self.stderr.write('')
            self.stderr.write('Česti uzroci AccessDenied:')
            self.stderr.write('  1) R2 API token nema pisanje — napravi novi s Admin Read & Write na bucket')
            self.stderr.write('  2) Token je za drugi account / pogrešan R2_ACCOUNT_ID')
            self.stderr.write('  3) Bucket name se ne poklapa (R2_BUCKET_NAME)')
            self.stderr.write('  4) EU jurisdiction bucket → R2_JURISDICTION=eu u env')
            self.stderr.write('  5) Nakon novog tokena: redeploy + ponovi ovu komandu')
            raise SystemExit(1)
