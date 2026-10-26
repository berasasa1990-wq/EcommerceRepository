"""Serve public images with a site-branded suggested download filename."""
from pathlib import Path
import re

from django.conf import settings
from django.http import Http404
from django.utils.http import content_disposition_header
from django.utils.html import strip_tags
from django.utils.text import slugify
from django.views.static import serve

from .models import SEOSettings

IMAGE_EXTENSIONS = {'.avif', '.jpg', '.jpeg', '.png', '.webp', '.gif', '.svg', '.ico', '.bmp'}


def image_download_name(path):
    config = SEOSettings.objects.filter(pk=1).first()
    name = config.site_name if config else 'BERA'
    description = ' '.join(strip_tags(config.description if config else '').split())
    first_sentence = re.split(r'[.!?](?:\s|$)', description, maxsplit=1)[0]
    stem = slugify(f'{name} {first_sentence}')[:180].rstrip('-') or 'slika'
    return stem + Path(path).suffix.lower()


def public_image(request, path, **kwargs):
    if Path(path).suffix.lower() not in IMAGE_EXTENSIONS:
        raise Http404('Slika nije pronađena.')
    response = serve(request, path, document_root=settings.MEDIA_ROOT)
    if response.status_code == 200:
        response['Content-Disposition'] = content_disposition_header(False, image_download_name(path))
    return response
