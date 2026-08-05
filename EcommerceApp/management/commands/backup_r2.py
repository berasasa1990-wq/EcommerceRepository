"""
Backup svih objekata sa Cloudflare R2 u jedan ZIP.

Na MacBooku (lokalno):
  python manage.py backup_r2
  → snima u ~/Downloads/r2-backup-....zip

  python manage.py backup_r2 --out ~/Desktop

Na Renderu (samo server disk — ne tvoj Mac):
  python manage.py backup_r2 --out /var/data/downloads

Opcije:
  --prefix products/
  --force
  --limit 20
"""
from __future__ import annotations

import os
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


def default_backup_out_dir() -> Path:
    """
    Mac/lokalno → ~/Downloads (ako postoji).
    Render → /var/data/downloads ili project/downloads.
    """
    on_render = bool(
        os.environ.get('RENDER')
        or os.environ.get('RENDER_EXTERNAL_HOSTNAME')
        or os.environ.get('RENDER_DISK_PATH')
    )
    mac_downloads = Path.home() / 'Downloads'
    if not on_render and mac_downloads.is_dir():
        return mac_downloads

    disk = (os.environ.get('RENDER_DISK_PATH') or '').strip()
    if disk:
        return Path(disk) / 'downloads'

    media_root = Path(settings.MEDIA_ROOT)
    if media_root.name == 'media':
        return media_root.parent / 'downloads'
    return Path(settings.BASE_DIR) / 'downloads'


def r2_client():
    import boto3
    from botocore.client import Config

    if not getattr(settings, 'USE_R2_MEDIA', False):
        raise CommandError(
            'R2 nije uključen. Na Macu u .env stavi R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, '
            'R2_SECRET_ACCESS_KEY, R2_BUCKET_NAME (i R2_CUSTOM_DOMAIN).',
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


def iter_r2_keys(client, bucket: str, prefix: str = ''):
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
            if not key or key.endswith('/'):
                continue
            yield key, int(obj.get('Size') or 0)
        if not resp.get('IsTruncated'):
            break
        token = resp.get('NextContinuationToken')


def compress_type_for_key(key: str) -> int:
    lower = key.lower()
    if any(lower.endswith(s) for s in _STORED_SUFFIXES):
        return zipfile.ZIP_STORED
    return zipfile.ZIP_DEFLATED


def build_r2_zip(
    dest: Path,
    *,
    prefix: str = '',
    limit: int = 0,
    log=None,
) -> dict:
    """
    Preuzmi R2 objekte u ZIP na putanju dest.
    log: callable(str) za progress (npr. self.stdout.write)
    """
    def _log(msg: str):
        if log:
            log(msg)

    client = r2_client()
    bucket = settings.AWS_STORAGE_BUCKET_NAME or settings.R2_BUCKET_NAME
    prefix = (prefix or '').strip()

    keys = list(iter_r2_keys(client, bucket, prefix=prefix))
    if limit > 0:
        keys = keys[:limit]

    total = len(keys)
    total_bytes = sum(s for _, s in keys)
    if total == 0:
        raise CommandError('Nema objekata u bucketu (ili prefixu) za backup.')

    _log(f'Bucket:   {bucket}')
    _log(f'Prefix:   {prefix or "(sve)"}')
    _log(f'Izlaz:    {dest}')
    _log(f'Objekata: {total}  (~{total_bytes / (1024 * 1024):.1f} MB prijavljeno)')

    dest.parent.mkdir(parents=True, exist_ok=True)
    downloaded = 0
    errors = 0
    bytes_done = 0

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
                info.compress_type = compress_type_for_key(key)
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
                    _log(
                        f'  [{downloaded}/{total}] {key} '
                        f'({bytes_done / (1024 * 1024):.1f} MB)',
                    )
            except Exception as exc:
                errors += 1
                _log(f'GREŠKA {key}: {exc}')

    zip_size = dest.stat().st_size
    return {
        'path': dest,
        'downloaded': downloaded,
        'errors': errors,
        'zip_bytes': zip_size,
        'total': total,
    }


class Command(BaseCommand):
    help = (
        'Preuzmi sve sa Cloudflare R2 u ZIP. '
        'Na Macu default: ~/Downloads/r2-backup-….zip'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--out',
            type=str,
            default='',
            help=(
                'Folder za ZIP. Default na Macu: ~/Downloads. '
                'Na Renderu: /var/data/downloads (ne ide na tvoj Mac).'
            ),
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
        if options.get('out'):
            out_dir = Path(options['out']).expanduser().resolve()
        else:
            out_dir = default_backup_out_dir().resolve()
        out_dir.mkdir(parents=True, exist_ok=True)

        prefix = (options.get('prefix') or '').strip()
        force = options['force']
        limit = int(options.get('limit') or 0)

        stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
        safe_prefix = prefix.replace('/', '-').strip('-') if prefix else 'all'
        zip_name = f'r2-backup-{safe_prefix}-{stamp}.zip'
        dest = out_dir / zip_name

        if dest.exists() and not force:
            raise CommandError(f'Već postoji {dest} — koristi --force')

        on_render = bool(
            os.environ.get('RENDER')
            or os.environ.get('RENDER_EXTERNAL_HOSTNAME')
            or os.environ.get('RENDER_DISK_PATH')
        )
        if on_render:
            self.stdout.write(self.style.WARNING(
                'Na Renderu ZIP ostaje na serveru (disk), ne na tvom Macu.\n'
                'Za Mac: pokreni istu komandu LOKALNO u projektu '
                '(R2_* u .env) → snima u ~/Downloads.',
            ))

        result = build_r2_zip(
            dest,
            prefix=prefix,
            limit=limit,
            log=self.stdout.write,
        )

        self.stdout.write(self.style.SUCCESS('✓ R2 backup gotov'))
        self.stdout.write(f'  ZIP:      {result["path"].resolve()}')
        self.stdout.write(f'  Veličina: {result["zip_bytes"] / (1024 * 1024):.2f} MB')
        self.stdout.write(
            f'  Fajlova:  {result["downloaded"]}  (grešaka: {result["errors"]})',
        )
        self.stdout.write(f'  Folder:   {out_dir}')
        if not on_render and out_dir == (Path.home() / 'Downloads').resolve():
            self.stdout.write(self.style.SUCCESS(
                '  → Otvori Finder → Downloads (Preuzimanja)',
            ))
        if result['errors']:
            self.stdout.write(self.style.WARNING(
                f'  {result["errors"]} fajl(ova) nije u ZIP-u — vidi GREŠKA linije gore.',
            ))
