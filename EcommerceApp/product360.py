"""Validated, bounded 360° image sets stored separately from the normal gallery."""
import io
import logging
import re
import warnings
from pathlib import PurePosixPath
from uuid import uuid4
from zipfile import ZipFile, BadZipFile
from PIL import Image, ImageOps, UnidentifiedImageError
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.db import transaction
from .models import Product, Product360Image

MAX_FRAMES = 120
MIN_FRAMES = 2
MAX_UPLOAD = 100 * 1024 * 1024
MAX_FRAME = 12 * 1024 * 1024
MAX_TOTAL = 150 * 1024 * 1024
MAX_PIXELS = 24_000_000
FORMATS = {'JPEG', 'PNG', 'WEBP'}


def natural_key(name):
    return [int(part) if part.isdigit() else part.casefold() for part in re.split(r'(\d+)', name)]


def prepare_frames(zip_upload=None, uploads=()):
    sources = []
    total = 0
    try:
        if zip_upload and uploads:
            raise ValidationError('Odaberite ZIP ili pojedinačne fotografije, ne oboje.')
        if zip_upload:
            if zip_upload.size > MAX_UPLOAD:
                raise ValidationError('ZIP smije imati najviše 100 MB.')
            with ZipFile(zip_upload) as archive:
                members = archive.infolist()
                if len(members) > MAX_FRAMES + 20:
                    raise ValidationError('Previše datoteka u ZIP-u.')
                for entry in members:
                    path = PurePosixPath(entry.filename)
                    if path.is_absolute() or '..' in path.parts or '\\' in entry.filename or ':' in entry.filename:
                        raise ValidationError('ZIP sadrži nedozvoljenu putanju.')
                    if (entry.external_attr >> 16) & 0o170000 == 0o120000:
                        raise ValidationError('ZIP ne smije sadržavati simboličke linkove.')
                    if entry.is_dir():
                        continue
                    if path.parts[0] == '__MACOSX' or path.name.startswith('.'):
                        continue
                    if path.suffix.lower() not in {'.jpg', '.jpeg', '.png', '.webp'}:
                        raise ValidationError('ZIP smije sadržavati samo JPG, PNG i WebP fotografije.')
                    total += entry.file_size
                    if entry.file_size > MAX_FRAME or total > MAX_TOTAL or entry.file_size / max(1, entry.compress_size) > 200:
                        raise ValidationError('ZIP prelazi dozvoljenu veličinu ili omjer kompresije.')
                    with archive.open(entry) as source:
                        data = source.read(MAX_FRAME + 1)
                    sources.append((path.name, data))
        else:
            if len(uploads) > MAX_FRAMES:
                raise ValidationError('Najviše 120 fotografija po setu.')
            for upload in uploads:
                if PurePosixPath(upload.name).suffix.lower() not in {'.jpg', '.jpeg', '.png', '.webp'} or upload.size > MAX_FRAME:
                    raise ValidationError('Dozvoljene su JPG, PNG i WebP fotografije do 12 MB.')
                data = upload.read(MAX_FRAME + 1)
                total += len(data)
                if total > MAX_TOTAL:
                    raise ValidationError('Fotografije ukupno smiju imati najviše 150 MB.')
                sources.append((upload.name, data))
        if not MIN_FRAMES <= len(sources) <= MAX_FRAMES:
            raise ValidationError('360° set mora sadržavati 2–120 fotografija.')
        frames = []
        dimensions = None
        for name, data in sorted(sources, key=lambda pair: natural_key(pair[0])):
            if len(data) > MAX_FRAME:
                raise ValidationError('Fotografija prelazi 12 MB.')
            with warnings.catch_warnings():
                warnings.simplefilter('error', Image.DecompressionBombWarning)
                with Image.open(io.BytesIO(data)) as image:
                    if image.format not in FORMATS or getattr(image, 'n_frames', 1) != 1:
                        raise ValidationError('Dozvoljene su samo statične JPG, PNG i WebP fotografije.')
                    width, height = image.size
                    if min(width, height) < 16 or width * height > MAX_PIXELS:
                        raise ValidationError('Neispravne dimenzije fotografije (minimum 16 px, maksimum 24 MP).')
                    image.load()
                    image = ImageOps.exif_transpose(image)
                    if dimensions is None:
                        dimensions = image.size
                    elif dimensions != image.size:
                        raise ValidationError('Svi kadrovi moraju imati iste dimenzije.')
                    image.thumbnail((1600, 1600))
                    image = image.convert('RGBA' if 'A' in image.getbands() else 'RGB')
                    output = io.BytesIO()
                    image.save(output, 'WEBP', quality=84, method=4)
                    frames.append(output.getvalue())
        return frames
    except (BadZipFile, UnidentifiedImageError, OSError, RuntimeError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise ValidationError('Neispravna ZIP datoteka ili fotografija. Postojeći set nije promijenjen.') from exc


def replace_frames(product, frames=None, delete=False):
    """Write new files first; retain old set if storage/database replacement fails."""
    storage = Product360Image._meta.get_field('image').storage
    written = []
    try:
        if frames is not None:
            for index, data in enumerate(frames):
                name = storage.save(f'products/360/{product.pk}/{uuid4().hex}-{index:03d}.webp', ContentFile(data))
                written.append(name)
        with transaction.atomic():
            Product.objects.select_for_update().get(pk=product.pk)
            old = list(product.images_360.values_list('image', flat=True))
            product.images_360.all().delete()
            if not delete:
                Product360Image.objects.bulk_create([Product360Image(product=product, image=name, position=i) for i, name in enumerate(written)])
            def cleanup_old():
                for name in old:
                    try:
                        storage.delete(name)
                    except OSError:
                        logging.getLogger(__name__).exception('Cannot remove replaced 360 frame')
            transaction.on_commit(cleanup_old)
    except Exception:
        for name in written:
            storage.delete(name)
        raise
