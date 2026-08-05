"""
Backup svih objekata sa Cloudflare R2 u jedan ZIP.

  python manage.py backup_r2
  python manage.py backup_r2 --out /var/data/downloads
  python manage.py backup_r2 --prefix products/
  python manage.py backup_r2 --force

ZIP se snima u downloads/ (ili --out). Na Renderu: npr. /var/data/downloads
"""
from __future__ import annotations

import shutil
import zipfile
from datetime import datetime
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


# Već kompresovani formati — ZIP_STORED (brže, manje CPU)
_STORED_SUFFIXES = (
    '.avif', '.jpg', '.jpeg', '.png', '.gif', '.webp',
    '.zip', '.gz', '.br', '.mp4', '.webm', '.woff2',
)


def _r2_client():
    import boto3
    from botocore.client import Config

    if not getattr(settings, 'USE_R2_MEDIA', False):
        raise CommandError(
            'R2 nije uključen. Postavi R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, '
            'R2_SECRET_ACCESS_KEY, R2_BUCKET_NAME.',
        )

    endpoint = getattr(settings, 'AWS_S3_ENDPOINT_URL', None) or (
        f'https://{settings.R2_ACCOUNT_ID}.r2.cloudflarestorage.com'
    )
    return boto3.client(
        's3',
        endpoint_url=endpoint,
        aws_access_key_id=settings.R2_ACCESS_KEY_ID,
        aws_secret_access_key=settings.R2_SECRET_ACCESS_KEY,
        region_name=getattr(settings, 'AWS_S3_REGION_NAME', 'auto'),
        config=Config(
            signature_version='s3v4',
            s3={'addressing_style': 'path'},
            connect_timeout=10,
            read_timeout=120,
            retries={'max_attempts': 3, 'mode': 'standard'},
        ),
    )


def _iter_keys(client, bucket: str, prefix: str = ''):
    token = None
    while True:
        kw = {'Bucket': bucket, 'MaxKeys': 1000}
        if prefix:
            kw['Prefix'] = prefix.lstrip('/')
        if token:
            kw['ContinuationToken'] = token
        resp = client.list_objects_v2(**kw)
        for obj in resp.get('Contents') or []:
            key = obj.get('Key') or ''
            # preskoči "foldere" i prazne
            if not key or key.endswith('/'):
                continue
            yield key, int(obj.get('Size') or 0)
        if not resp.get('IsTruncated'):
            break
        token = resp.get('NextContinuationToken')


def _compress_type(key: str) -> int:
    lower = key.lower()
    if any(lower.endswith(s) for s in _STORED_SUFFIXES):
        return zipfile.ZIP_STORED
    return zipfile.ZIP_DEFLATED


class Command(BaseCommand):
    help = 'Preuzmi sve objekte sa Cloudflare R2 i spakuj u ZIP (folder downloads/).'

    def add_arguments(self, parser):
        parser.add_argument(
            '--out',
            type=str,
            default='',
            help='Odredišni folder (default: <project>/downloads ili RENDER_DISK_PATH/downloads).',
        )
        parser.add_argument(
            '--prefix',
            type=str,
            default='',
            help='Samo podfolder u bucketu, npr. products/ ili banners/.',
        )
        parser.add_argument(
            '--force',
            action='store_true',
            help='Prepiši ZIP ako već postoji isto ime.',
        )
        parser.add_argument(
            '--limit',
            type=int,
            default=0,
            help='Maks. broj objekata (0 = svi). Za test.',
        )

    def handle(self, *args, **options):
        client = _r2_client()
        bucket = settings.AWS_STORAGE_BUCKET_NAME or settings.R2_BUCKET_NAME
        prefix = (options.get('prefix') or '').strip()
        force = options['force']
        limit = int(options.get('limit') or 0)

        # downloads/ — na Render disku (pored media/) ako postoji, inače project/downloads
        if options.get('out'):
            out_dir = Path(options['out']).expanduser()
        else:
            import os
            disk = (os.environ.get('RENDER_DISK_PATH') or '').strip()
            if disk:
                out_dir = Path(disk) / 'downloads'
            else:
                media_root = Path(settings.MEDIA_ROOT)
                if media_root.name == 'media':
                    out_dir = media_root.parent / 'downloads'
                else:
                    out_dir = Path(settings.BASE_DIR) / 'downloads'
        out_dir.mkdir(parents=True, exist_ok=True)

        stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
        safe_prefix = prefix.replace('/', '-').strip('-') if prefix else 'all'
        zip_name = f'r2-backup-{safe_prefix}-{stamp}.zip'
        dest = out_dir / zip_name

        if dest.exists() and not force:
            raise CommandError(f'Već postoji {dest} — koristi --force')

        self.stdout.write(f'Bucket:   {bucket}')
        self.stdout.write(f'Prefix:   {prefix or "(sve)"}')
        self.stdout.write(f'Izlaz:    {dest}')

        keys = list(_iter_keys(client, bucket, prefix=prefix))
        if limit > 0:
            keys = keys[:limit]

        total = len(keys)
        total_bytes = sum(s for _, s in keys)
        if total == 0:
            raise CommandError('Nema objekata u bucketu (ili prefixu) za backup.')

        self.stdout.write(
            f'Objekata: {total}  (~{total_bytes / (1024 * 1024):.1f} MB prijavljeno)',
        )

        downloaded = 0
        errors = 0
        bytes_done = 0

        # ZIP64 za velike archive
        with zipfile.ZipFile(
            dest,
            mode='w',
            compression=zipfile.ZIP_STORED,
            allowZip64=True,
        ) as zf:
            for key, size in keys:
                try:
                    resp = client.get_object(Bucket=bucket, Key=key)
                    body = resp['Body']
                    info = zipfile.ZipInfo(filename=key)
                    info.compress_type = _compress_type(key)
                    # mtime iz R2 ako ima
                    lm = resp.get('LastModified')
                    if lm is not None:
                        try:
                            info.date_time = (
                                lm.year, lm.month, lm.day,
                                lm.hour, lm.minute, lm.second,
                            )
                        except Exception:
                            pass
                    with zf.open(info, 'w') as dest_f:
                        shutil.copyfileobj(body, dest_f, length=1024 * 1024)
                    body.close()
                    downloaded += 1
                    bytes_done += size
                    if downloaded <= 20 or downloaded % 100 == 0 or downloaded == total:
                        self.stdout.write(
                            f'  [{downloaded}/{total}] {key} '
                            f'({bytes_done / (1024 * 1024):.1f} MB)',
                        )
                except Exception as exc:
                    errors += 1
                    self.stderr.write(f'GREŠKA {key}: {exc}')

        zip_size = dest.stat().st_size
        self.stdout.write(self.style.SUCCESS('✓ R2 backup gotov'))
        self.stdout.write(f'  ZIP:      {dest.resolve()}')
        self.stdout.write(f'  Veličina: {zip_size / (1024 * 1024):.2f} MB')
        self.stdout.write(f'  Fajlova:  {downloaded}  (grešaka: {errors})')
        self.stdout.write(f'  Folder:   {out_dir.resolve()}')
        if errors:
            self.stdout.write(self.style.WARNING(
                f'  {errors} fajl(ova) nije u ZIP-u — vidi GREŠKA linije gore.',
            ))
