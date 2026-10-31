"""Uniform catalogue artwork derived from originals, without changing uploads."""
import hashlib
import logging
from io import BytesIO

from django.core.cache import cache
from django.core.files.base import ContentFile
from PIL import Image, ImageChops, ImageOps

from .utils.images import _image_to_rgb, product_image_responsive_meta

logger = logging.getLogger(__name__)


def fit_catalog_artwork(source, size=600):
    image = _image_to_rgb(ImageOps.exif_transpose(source))
    # Any non-white channel counts as content, including bright coloured edges.
    channels = [channel.point(lambda value: 255 if value < 245 else 0) for channel in image.split()]
    mask = ImageChops.lighter(ImageChops.lighter(channels[0], channels[1]), channels[2])
    bounds = mask.getbbox()
    if bounds:
        left, top, right, bottom = bounds
        safety = max(2, round(max(right - left, bottom - top) * .02))
        image = image.crop((max(0, left - safety), max(0, top - safety),
                            min(image.width, right + safety), min(image.height, bottom + safety)))
    artwork = ImageOps.contain(image, (size, size), Image.Resampling.LANCZOS)
    canvas = Image.new('RGB', (size, size), 'white')
    canvas.paste(artwork, ((size - artwork.width) // 2, (size - artwork.height) // 2))
    return canvas


def catalog_card_image_meta(image_field):
    if not image_field:
        return {}
    storage = image_field.storage
    identity = f'{type(storage).__module__}.{type(storage).__name__}:{getattr(storage, "location", "")}:{image_field.name}'
    key = 'catalog-artwork-v1:' + hashlib.sha256(identity.encode()).hexdigest()
    cached = cache.get(key)
    if cached is not None:
        return cached
    stem = image_field.name.rsplit('.', 1)[0]
    names = {width: f'{stem}-card-fit-v1-{width}w.webp' for width in (320, 600)}
    try:
        missing = [width for width, name in names.items() if not storage.exists(name)]
        if missing:
            with storage.open(image_field.name, 'rb') as original:
                with Image.open(original) as source:
                    source.load()
                    canvas = fit_catalog_artwork(source)
            for width in missing:
                image = canvas if width == 600 else canvas.resize((width, width), Image.Resampling.LANCZOS)
                output = BytesIO()
                image.save(output, format='WEBP', quality=88, method=6)
                names[width] = storage.save(names[width], ContentFile(output.getvalue()))
        urls = {width: storage.url(name) for width, name in names.items()}
        result = {'src': urls[320], 'srcset': f'{urls[320]} 320w, {urls[600]} 600w',
                  'width': 600, 'height': 600}
        cache.set(key, result, 300)
        return result
    except Exception:
        # A missing original or temporary storage outage must not break the shop.
        logger.debug('Catalogue artwork could not be prepared: %s', image_field.name, exc_info=True)
        fallback = product_image_responsive_meta(image_field)
        cache.set(key, fallback, 60)
        return fallback
