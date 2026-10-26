"""Convert uploaded artwork to size-limited AVIF before storage."""
from io import BytesIO
from pathlib import Path
import warnings

from django import forms
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.db import models
from PIL import Image, ImageOps, UnidentifiedImageError

MAX_IMAGE_BYTES = 15_000
UPLOAD_HELP = 'Slika se automatski sprema kao AVIF do 15 KB. Po potrebi se smanjuju kvalitet i rezolucija; animacije se spremaju kao prva slika.'


def optimize_image(upload, max_bytes=MAX_IMAGE_BYTES):
    if getattr(upload, '_optimized_avif', False) and upload.size <= max_bytes:
        return upload
    try:
        upload.seek(0)
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(upload) as source:
                source.seek(0)
                image = ImageOps.exif_transpose(source)
                image = image.convert('RGBA' if image.has_transparency_data else 'RGB')
        image.thumbnail((1600, 1600), Image.Resampling.LANCZOS)
        # Drop source metadata and retry at smaller dimensions if quality alone
        # cannot meet the hard storage limit. Never stretch the aspect ratio.
        image.info.clear()
        while True:
            qualities = range(95, 44, -5) if max_bytes > MAX_IMAGE_BYTES else (80, 60, 40, 25)
            for quality in qualities:
                output = BytesIO()
                image.save(output, format='AVIF', quality=quality, speed=6, max_threads=2)
                data = output.getvalue()
                if len(data) <= max_bytes:
                    result = ContentFile(data, name=Path(upload.name).stem + '.avif')
                    result.content_type = 'image/avif'
                    result._optimized_avif = True
                    return result
            if max(image.size) <= 32:
                raise ValidationError(f'Slika se ne može smanjiti na {max_bytes // 1000} KB. Odaberite drugu sliku.')
            image = image.resize(tuple(max(1, int(n * .75)) for n in image.size), Image.Resampling.LANCZOS)
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise ValidationError('Odaberite ispravnu PNG, JPG, WebP, GIF ili AVIF sliku.') from exc
    finally:
        upload.seek(0)


class AVIFUploadFormField(forms.FileField):
    def __init__(self, *args, max_bytes=MAX_IMAGE_BYTES, **kwargs):
        self.max_bytes = max_bytes
        super().__init__(*args, **kwargs)
        self.widget.attrs['accept'] = 'image/png,image/jpeg,image/webp,image/gif,image/avif'

    def clean(self, data, initial=None):
        value = super().clean(data, initial)
        if value and value is not initial:
            return optimize_image(value, self.max_bytes)
        return value


class AVIFFileField(models.FileField):
    def __init__(self, *args, max_bytes=MAX_IMAGE_BYTES, **kwargs):
        self.max_bytes = max_bytes
        kwargs.setdefault('help_text', UPLOAD_HELP if max_bytes == MAX_IMAGE_BYTES else f'Slika se automatski sprema kao AVIF do {max_bytes // 1000} KB, uz najviši kvalitet koji stane u zadani limit.')
        super().__init__(*args, **kwargs)

    def deconstruct(self):
        name, path, args, kwargs = super().deconstruct()
        if self.max_bytes != MAX_IMAGE_BYTES:
            kwargs['max_bytes'] = self.max_bytes
        return name, path, args, kwargs

    def formfield(self, **kwargs):
        return super().formfield(**{'form_class': AVIFUploadFormField, 'max_bytes': self.max_bytes, **kwargs})

    def pre_save(self, model_instance, add):
        file = getattr(model_instance, self.attname)
        if file and not file._committed:
            converted = optimize_image(file.file, self.max_bytes)
            setattr(model_instance, self.attname, converted)
        return super().pre_save(model_instance, add)
