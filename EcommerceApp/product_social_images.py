"""Public, cacheable JPEG images used by product link previews."""
import hashlib
import logging
from io import BytesIO

from django.core.cache import cache
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.views.decorators.http import require_safe
from PIL import Image, ImageOps, ImageDraw, ImageFont
from pathlib import Path
from functools import lru_cache

from .models import Product

logger = logging.getLogger(__name__)
SIZE = (1200, 630)

DESIGN_VERSION = 'branded-v3'
FONT_ROOT = Path(__file__).resolve().parent / 'static' / 'fonts'


@lru_cache(maxsize=64)
def _font(size, extended=False):
    subset = 'latin-ext' if extended else 'latin'
    try:
        return ImageFont.truetype(str(FONT_ROOT / f'inter-{subset}-700.woff2'), size)
    except OSError:
        # Some server FreeType builds do not decode WOFF2.
        try:
            return ImageFont.truetype('DejaVuSans-Bold.ttf', size)
        except OSError:
            return ImageFont.load_default(size=size)


def _text_width(text, size):
    return sum(_font(size, ord(char) > 255).getlength(char) for char in text)


def _text(draw, xy, text, size, color):
    x, y = xy
    for char in text:
        font = _font(size, ord(char) > 255)
        draw.text((x, y + _font(size).getmetrics()[0]), char, font=font, fill=color, anchor='ls')
        x += font.getlength(char)


def _version(product, sources):
    payload = [DESIGN_VERSION, product.naziv, f'{product.katalog_prikazna_cijena:.2f}',
               f'{product.katalog_bazna_cijena:.2f}', str(len(product.varijacije.all())),
               *[image.name for image in sources]]
    return hashlib.sha256('|'.join(payload).encode()).hexdigest()


def render_share_card(product, original):
    canvas = Image.new('RGB', SIZE, '#ff8500')
    draw = ImageDraw.Draw(canvas)
    for y in range(SIZE[1]):
        ratio = y / (SIZE[1] - 1)
        draw.line((0, y, SIZE[0], y), fill=(255, int(148 - 38 * ratio), 0))
    _text(draw, (38, 27), 'CARPOLOGIJABH', 40, 'white')
    draw.line((454, 29, 454, 69), fill='#ffd7a3', width=2)
    _text(draw, (480, 39), 'Oprema za ribolov', 26, 'white')
    draw.rounded_rectangle((22, 99, 1178, 608), radius=20, fill='#fafafa')
    artwork = ImageOps.exif_transpose(original).convert('RGBA')
    artwork.thumbnail((456, 456), Image.Resampling.LANCZOS)
    canvas.paste(artwork, (42 + (456 - artwork.width) // 2,
                           125 + (456 - artwork.height) // 2), artwork)
    title_size = 32
    for size in (32, 30, 28, 26):
        lines = ['']
        for word in product.naziv.split():
            candidate = (lines[-1] + ' ' + word).strip()
            if lines[-1] and _text_width(candidate, size) > 600:
                lines.append(word)
            else:
                lines[-1] = candidate
        if len(lines) <= 3:
            title_size = size
            break
    lines = lines[:3]
    for index, line in enumerate(lines):
        suffix = '…' if index == 2 and len(product.naziv) > len(' '.join(lines)) else ''
        while line and _text_width(line + suffix, title_size) > 600:
            line = line[:-1]
            suffix = '…'
        _text(draw, (546, 132 + index * 43), line + suffix, title_size, '#182c43')
    draw.line((546, 275, 1146, 275), fill='#efd6bd', width=2)
    price = product.katalog_prikazna_cijena
    old = product.katalog_bazna_cijena
    price_text = f'{price:.2f} KM'.replace('.', ',')
    if len(list(product.varijacije.all())) > 1:
        price_text = 'Od ' + price_text
    price_size = 72
    while _text_width(price_text, price_size) > 600 and price_size > 32:
        price_size -= 2
    _text(draw, (546, 318), price_text, price_size, '#ef5900')
    if old > price:
        old_text = f'{old:.2f} KM'.replace('.', ',')
        _text(draw, (546, 289), old_text, 23, '#7c8793')
        draw.line((546, 301, 546 + _text_width(old_text, 23), 301), fill='#7c8793', width=2)
    _text(draw, (546, 419), 'Cijena i dostupnost na carpologijabh.ba', 21, '#728092')
    draw.rounded_rectangle((546, 480, 1146, 558), radius=39, fill='#ff7900')
    cta = 'Pogledaj artikal'
    _text(draw, (846 - _text_width(cta, 29) / 2, 503), cta, 29, 'white')
    return canvas


def image_sources(product):
    sources = []
    main = product.prikazna_slika
    if main:
        sources.append(main)
    for extra in product.dodatne_slike.all():
        if extra.slika and extra.slika.name not in {image.name for image in sources}:
            sources.append(extra.slika)
        if len(sources) >= 4:
            break
    return sources


def share_image_context(request, product):
    sources = image_sources(product)
    if not sources:
        return {}
    version = _version(product, sources)[:16]
    url = reverse('product_share_image', kwargs={'slug': product.slug})
    return {'og_image': request.build_absolute_uri(f'{url}?v={version}'),
            'og_image_type': 'image/jpeg', 'og_image_width': SIZE[0],
            'og_image_height': SIZE[1], 'og_image_alt': product.naziv}


@require_safe
def product_share_image(request, slug):
    product = get_object_or_404(Product.objects.filter(aktivan=True, sakriven_do_stanja=False)
                               .prefetch_related('varijacije', 'dodatne_slike'), slug=slug)
    sources = image_sources(product)
    version = _version(product, sources)
    key = f'product-share-jpeg-v2:{product.pk}:{version}'
    data = cache.get(key)
    if data is None:
        for image in sources:
            try:
                with image.storage.open(image.name, 'rb') as handle:
                    raw = handle.read(20 * 1024 * 1024 + 1)
                if len(raw) > 20 * 1024 * 1024:
                    continue
                with Image.open(BytesIO(raw)) as original:
                    canvas = render_share_card(product, original)
                    output = BytesIO()
                    canvas.save(output, 'JPEG', quality=88, optimize=True)
                    data = output.getvalue()
                cache.set(key, data, 3600)
                break
            except Exception as error:
                logger.warning('Product share image unavailable: product=%s error=%s', product.pk, type(error).__name__)
        if data is None:
            raise Http404('Slika artikla nije dostupna.')
    response = HttpResponse(data, content_type='image/jpeg')
    response['Cache-Control'] = 'public, max-age=3600'
    response['X-Content-Type-Options'] = 'nosniff'
    return response
