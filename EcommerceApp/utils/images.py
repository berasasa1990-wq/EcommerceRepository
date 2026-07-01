import logging
import re
from io import BytesIO

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import UploadedFile
from PIL import Image, ImageOps, features

logger = logging.getLogger(__name__)

_BANNER_AVIF_SUPPORTED = None

BRAND_LOGO_SIZE = (200, 48)
BRAND_LOGO_FILL_RATIO = 0.80
SITE_LOGO_SIZE = (640, 128)
PRODUCT_WHITE_THRESHOLD = 248
PRODUCT_MAX_DIMENSION = 400
AVIF_SPEED = 6
BANNER_AVIF_SPEED = 4
MAX_VLOG_AVIF_BYTES = 30 * 1024
VLOG_MAX_DIMENSION = 420
BANNER_MAX_WIDTH = 1920
HERO_BANNER_MAX_WIDTH = 1920
HERO_BANNER_MAX_HEIGHT = 640
HERO_RESPONSIVE_WIDTHS = (640, 768, 1024, 1280, 1920)
HERO_LCP_PRELOAD_WIDTH = 768
MAX_GRID_BANNER_AVIF_BYTES = 85 * 1024
GRID_BANNER_MAX_DIMENSION = 420
MAX_HERO_BANNER_AVIF_BYTES = 220 * 1024
MAX_FEATURED_BANNER_AVIF_BYTES = 200 * 1024
MAX_SPOTLIGHT_BANNER_AVIF_BYTES = 200 * 1024
MAX_DEFAULT_BANNER_AVIF_BYTES = 200 * 1024
BANNER_AVIF_MAX_QUALITY = 88
BANNER_AVIF_MIN_QUALITY = 72
BANNER_AVIF_QUALITY_STEP = 3
MAX_PRODUCT_AVIF_BYTES = 20 * 1024

BANNER_AVIF_SETTINGS = {
    'grid': {
        'max_bytes': MAX_GRID_BANNER_AVIF_BYTES,
        'max_width': GRID_BANNER_MAX_DIMENSION,
        'max_height': GRID_BANNER_MAX_DIMENSION,
    },
    'hero': {
        'max_bytes': MAX_HERO_BANNER_AVIF_BYTES,
        'max_width': HERO_BANNER_MAX_WIDTH,
        'max_height': HERO_BANNER_MAX_HEIGHT,
        'crop': True,
    },
    'featured': {
        'max_bytes': MAX_FEATURED_BANNER_AVIF_BYTES,
        'max_width': BANNER_MAX_WIDTH,
    },
    'spotlight': {
        'max_bytes': MAX_SPOTLIGHT_BANNER_AVIF_BYTES,
        'max_width': BANNER_MAX_WIDTH,
    },
}


def is_new_upload(image_field):
    return hasattr(image_field, 'file') and isinstance(image_field.file, UploadedFile)


def image_field_dimensions(image_field, *, default=(1600, 900)):
    if not image_field or not image_field.name:
        return default
    try:
        image_field.open('rb')
        try:
            with Image.open(image_field) as img:
                return img.width, img.height
        finally:
            image_field.close()
    except Exception:
        logger.debug('Ne mogu učitati dimenzije za %s', image_field.name, exc_info=True)
        return default


def _png_filename(original_name):
    base = original_name.rsplit('/', 1)[-1]
    return base.rsplit('.', 1)[0] + '.png'


def _avif_filename(original_name):
    base = original_name.rsplit('/', 1)[-1]
    return base.rsplit('.', 1)[0] + '.avif'


def _jpeg_filename(original_name):
    base = original_name.rsplit('/', 1)[-1]
    return base.rsplit('.', 1)[0] + '.jpg'


def _reset_upload(image_field):
    upload = getattr(image_field, 'file', None)
    if upload is not None and hasattr(upload, 'seek'):
        upload.seek(0)


def _banner_avif_supported():
    global _BANNER_AVIF_SUPPORTED
    if _BANNER_AVIF_SUPPORTED is not None:
        return _BANNER_AVIF_SUPPORTED
    try:
        if not features.check('avif'):
            _BANNER_AVIF_SUPPORTED = False
            return False
        buffer = BytesIO()
        Image.new('RGB', (16, 16), (255, 255, 255)).save(
            buffer,
            format='AVIF',
            quality=80,
            speed=BANNER_AVIF_SPEED,
        )
        _BANNER_AVIF_SUPPORTED = len(buffer.getvalue()) > 0
    except Exception:
        logger.warning('AVIF enkoder nije dostupan na serveru, banneri idu u JPEG.', exc_info=True)
        _BANNER_AVIF_SUPPORTED = False
    return _BANNER_AVIF_SUPPORTED


def _read_image_source(image_source, *, filename='image.jpg'):
    if hasattr(image_source, 'read'):
        image_source.seek(0)
        raw = image_source.read()
        if not raw:
            raise ValueError('Prazna slika')
        if not filename or filename == 'image.jpg':
            filename = getattr(image_source, 'name', None) or 'image.jpg'
        return raw, filename
    if not image_source:
        raise ValueError('Prazna slika')
    return image_source, filename


def _encode_avif(img, quality, *, speed=AVIF_SPEED):
    buffer = BytesIO()
    img.save(buffer, format='AVIF', quality=quality, speed=speed)
    return buffer.getvalue()


def _encode_banner_avif_data(img, quality):
    return _encode_avif(img, quality, speed=BANNER_AVIF_SPEED)


def _image_to_rgb(img):
    img = ImageOps.exif_transpose(img)
    if img.mode in ('RGBA', 'LA', 'P'):
        rgba = _ensure_rgba(img)
        background = Image.new('RGB', rgba.size, (255, 255, 255))
        background.paste(rgba, mask=rgba.split()[3])
        return background
    if img.mode != 'RGB':
        return img.convert('RGB')
    return img


def _fit_product_dimensions(img, max_dimension=PRODUCT_MAX_DIMENSION):
    if max(img.size) <= max_dimension:
        return img
    return ImageOps.contain(img, (max_dimension, max_dimension), method=Image.Resampling.LANCZOS)


def _fit_banner_dimensions(img, *, max_width, max_height=None, crop=False):
    rgb = _image_to_rgb(img)
    if max_height is not None:
        if crop:
            return ImageOps.fit(
                rgb,
                (max_width, max_height),
                method=Image.Resampling.LANCZOS,
                centering=(0.5, 0.5),
            )
        return ImageOps.contain(
            rgb,
            (max_width, max_height),
            method=Image.Resampling.LANCZOS,
        )
    if rgb.width > max_width:
        ratio = max_width / rgb.width
        return rgb.resize(
            (max_width, max(1, int(rgb.height * ratio))),
            Image.Resampling.LANCZOS,
        )
    return rgb


def _encode_avif_under_budget(img, filename, *, max_bytes, max_dimension):
    filename = _avif_filename(filename)
    working = _fit_product_dimensions(_image_to_rgb(img), max_dimension=max_dimension)

    best_data = None
    best_size = float('inf')

    for scale in (1.0, 0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.35, 0.3, 0.25, 0.2):
        if scale < 1.0:
            new_w = max(1, int(working.width * scale))
            new_h = max(1, int(working.height * scale))
            candidate = working.resize((new_w, new_h), Image.Resampling.LANCZOS)
        else:
            candidate = working

        for quality in range(85, 0, -5):
            data = _encode_avif(candidate, quality)
            size = len(data)
            if size <= max_bytes:
                return ContentFile(data, name=filename)
            if size < best_size:
                best_size = size
                best_data = data

    logger.warning(
        'Slika nije smanjena ispod %dKB (najmanje: %d bytes), čuva se najbliža AVIF verzija.',
        max_bytes // 1024,
        best_size,
    )
    return ContentFile(best_data, name=filename)


def _encode_product_avif(img, filename):
    return _encode_avif_under_budget(
        img,
        filename,
        max_bytes=MAX_PRODUCT_AVIF_BYTES,
        max_dimension=PRODUCT_MAX_DIMENSION,
    )


def _encode_banner_avif(
    img,
    filename,
    *,
    max_bytes,
    max_width,
    max_height=None,
    crop=False,
    min_quality=BANNER_AVIF_MIN_QUALITY,
):
    """AVIF za banere: visok kvalitet na punoj rezoluciji, zatim blago smanjenje dimenzija."""
    filename = _avif_filename(filename)
    working = _fit_banner_dimensions(
        img,
        max_width=max_width,
        max_height=max_height,
        crop=crop,
    )

    best_data = None
    best_size = float('inf')

    for scale in (1.0, 0.95, 0.9, 0.85, 0.8, 0.75, 0.7, 0.65, 0.6):
        if scale < 1.0:
            new_w = max(1, int(working.width * scale))
            new_h = max(1, int(working.height * scale))
            candidate = working.resize((new_w, new_h), Image.Resampling.LANCZOS)
        else:
            candidate = working

        for quality in range(
            BANNER_AVIF_MAX_QUALITY,
            min_quality - 1,
            -BANNER_AVIF_QUALITY_STEP,
        ):
            data = _encode_banner_avif_data(candidate, quality)
            size = len(data)
            if size <= max_bytes:
                return ContentFile(data, name=filename)
            if size < best_size:
                best_size = size
                best_data = data

    logger.warning(
        'Banner nije smanjen ispod %dKB (najmanje: %d bytes), čuva se najbliža AVIF verzija.',
        max_bytes // 1024,
        best_size,
    )
    return ContentFile(best_data, name=filename)


def process_vlog_image(image_field):
    """Vlog slike: AVIF max 30KB, max 420px (3 u redu na početnoj)."""
    img = Image.open(image_field)
    filename = image_field.name if hasattr(image_field, 'name') else 'vlog.jpg'
    return _encode_avif_under_budget(
        img,
        filename,
        max_bytes=MAX_VLOG_AVIF_BYTES,
        max_dimension=VLOG_MAX_DIMENSION,
    )


def _encode_banner_jpeg_fallback(
    img,
    filename,
    *,
    max_width,
    max_height=None,
    crop=False,
    quality=88,
):
    """Pouzdan JPEG format za banere (posebno Hero)."""
    working = _fit_banner_dimensions(
        img,
        max_width=max_width,
        max_height=max_height,
        crop=crop,
    )
    buffer = BytesIO()
    working.save(buffer, format='JPEG', quality=quality, optimize=True, progressive=True)
    return ContentFile(buffer.getvalue(), name=_jpeg_filename(filename))


def _load_banner_source(image_field):
    _reset_upload(image_field)
    with Image.open(image_field) as img:
        img.load()
        return _image_to_rgb(img)


def _hero_height_for_width(width):
    return max(1, int(width / 3))


def _hero_variant_max_bytes(width):
    scale = (width / HERO_BANNER_MAX_WIDTH) ** 2
    return max(24 * 1024, int(MAX_HERO_BANNER_AVIF_BYTES * scale))


def _hero_upload_stem(filename):
    folder, base_name = filename.rsplit('/', 1) if '/' in filename else ('banners', filename)
    stem = re.sub(r'-w\d+$', '', base_name.rsplit('.', 1)[0])
    return f'{folder}/{stem}'


def _hero_asset_stem(path):
    base, _ext = path.rsplit('.', 1)
    match = re.match(r'^(?P<stem>.+)-w\d+$', base)
    if match:
        return match.group('stem')
    return base


def _hero_variant_storage_name(stem, width, extension):
    return f'{stem}-w{width}.{extension}'


def _encode_hero_variant_avif(img, *, width, crop, max_bytes):
    height = _hero_height_for_width(width)
    working = _fit_banner_dimensions(
        img,
        max_width=width,
        max_height=height,
        crop=crop,
    )

    best_data = None
    best_size = float('inf')

    for scale in (1.0, 0.95, 0.9, 0.85, 0.8, 0.75, 0.7, 0.65, 0.6):
        if scale < 1.0:
            new_w = max(1, int(working.width * scale))
            new_h = max(1, int(working.height * scale))
            candidate = working.resize((new_w, new_h), Image.Resampling.LANCZOS)
        else:
            candidate = working

        for quality in range(
            BANNER_AVIF_MAX_QUALITY,
            BANNER_AVIF_MIN_QUALITY - 1,
            -BANNER_AVIF_QUALITY_STEP,
        ):
            data = _encode_banner_avif_data(candidate, quality)
            size = len(data)
            if size <= max_bytes:
                return data
            if size < best_size:
                best_size = size
                best_data = data

    return best_data


def _encode_hero_variant_jpeg(img, *, width, crop, quality=85):
    height = _hero_height_for_width(width)
    working = _fit_banner_dimensions(
        img,
        max_width=width,
        max_height=height,
        crop=crop,
    )
    buffer = BytesIO()
    working.save(buffer, format='JPEG', quality=quality, optimize=True, progressive=True)
    return buffer.getvalue()


def _save_hero_responsive_variants(source, filename, *, crop):
    stem = _hero_upload_stem(filename)
    use_avif = _banner_avif_supported()
    primary_name = None
    primary_bytes = None
    jpeg_by_width = {}

    for width in HERO_RESPONSIVE_WIDTHS:
        jpeg_quality = 88 if width >= 1280 else 85
        jpeg_bytes = _encode_hero_variant_jpeg(
            source,
            width=width,
            crop=crop,
            quality=jpeg_quality,
        )
        jpeg_name = _hero_variant_storage_name(stem, width, 'jpg')
        default_storage.save(jpeg_name, ContentFile(jpeg_bytes, name=jpeg_name))
        jpeg_by_width[width] = jpeg_bytes

        if use_avif:
            avif_bytes = _encode_hero_variant_avif(
                source,
                width=width,
                crop=crop,
                max_bytes=_hero_variant_max_bytes(width),
            )
            avif_name = _hero_variant_storage_name(stem, width, 'avif')
            default_storage.save(avif_name, ContentFile(avif_bytes, name=avif_name))
            if width == HERO_BANNER_MAX_WIDTH:
                primary_name = avif_name
                primary_bytes = avif_bytes

    if primary_name is None:
        primary_name = _hero_variant_storage_name(stem, HERO_BANNER_MAX_WIDTH, 'jpg')
        primary_bytes = jpeg_by_width[HERO_BANNER_MAX_WIDTH]

    return ContentFile(primary_bytes, name=primary_name)


def build_hero_responsive_sources(image_field, request=None):
    if not image_field or not image_field.name:
        return {}

    def _absolute_url(storage_name):
        url = default_storage.url(storage_name)
        if request and url.startswith('/'):
            return request.build_absolute_uri(url)
        return url

    stem = _hero_asset_stem(image_field.name)
    srcset_avif = []
    srcset_jpeg = []
    avif_urls = {}
    jpeg_urls = {}

    for width in HERO_RESPONSIVE_WIDTHS:
        avif_name = _hero_variant_storage_name(stem, width, 'avif')
        jpeg_name = _hero_variant_storage_name(stem, width, 'jpg')
        if default_storage.exists(avif_name):
            avif_urls[width] = _absolute_url(avif_name)
            srcset_avif.append(f'{avif_urls[width]} {width}w')
        if default_storage.exists(jpeg_name):
            jpeg_urls[width] = _absolute_url(jpeg_name)
            srcset_jpeg.append(f'{jpeg_urls[width]} {width}w')

    if not srcset_avif and not srcset_jpeg:
        width, height = image_field_dimensions(image_field, default=(HERO_BANNER_MAX_WIDTH, HERO_BANNER_MAX_HEIGHT))
        fallback_url = image_field.url
        if request and fallback_url.startswith('/'):
            fallback_url = request.build_absolute_uri(fallback_url)
        return {
            'fallback_url': fallback_url,
            'display_width': width,
            'display_height': height,
            'lcp_preload_url': fallback_url,
        }

    fallback_width = 1280 if 1280 in jpeg_urls else max(jpeg_urls)
    fallback_url = jpeg_urls[fallback_width]
    lcp_preload_url = (
        avif_urls.get(HERO_LCP_PRELOAD_WIDTH)
        or jpeg_urls.get(HERO_LCP_PRELOAD_WIDTH)
        or avif_urls.get(fallback_width)
        or fallback_url
    )

    return {
        'fallback_url': fallback_url,
        'display_width': HERO_BANNER_MAX_WIDTH,
        'display_height': HERO_BANNER_MAX_HEIGHT,
        'srcset_avif': ', '.join(srcset_avif),
        'srcset_jpeg': ', '.join(srcset_jpeg),
        'lcp_preload_url': lcp_preload_url,
    }


def process_hero_banner_image(image_field):
    """Hero: AVIF + JPEG responsive varijante (640–1920px, omjer 3:1)."""
    filename = image_field.name if hasattr(image_field, 'name') else 'banner.jpg'
    try:
        source = _load_banner_source(image_field)
    except Exception as exc:
        raise ValueError(
            f'Slika se ne može očitati ({exc}). Koristite JPG ili PNG.',
        ) from exc

    settings = BANNER_AVIF_SETTINGS['hero']
    return _save_hero_responsive_variants(
        source,
        filename,
        crop=settings.get('crop', False),
    )


def process_banner_image(image_field, tip='hero'):
    """Banneri: Hero responsive AVIF+JPEG, ostalo AVIF uz JPEG rezervu."""
    if tip == 'hero':
        return process_hero_banner_image(image_field)

    filename = image_field.name if hasattr(image_field, 'name') else 'banner.jpg'
    settings = BANNER_AVIF_SETTINGS.get(tip, {
        'max_bytes': MAX_DEFAULT_BANNER_AVIF_BYTES,
        'max_width': BANNER_MAX_WIDTH,
    })
    try:
        source = _load_banner_source(image_field)
    except Exception as exc:
        raise ValueError(
            f'Slika se ne može očitati ({exc}). Koristite JPG ili PNG.',
        ) from exc

    crop = settings.get('crop', False)
    if not _banner_avif_supported():
        return _encode_banner_jpeg_fallback(
            source,
            filename,
            max_width=settings['max_width'],
            max_height=settings.get('max_height'),
            crop=crop,
            quality=88,
        )

    try:
        return _encode_banner_avif(source, filename, **settings)
    except Exception as exc:
        logger.warning(
            'AVIF obrada bannera nije uspjela (%s), čuvam optimizovani JPEG.',
            exc,
        )
        return _encode_banner_jpeg_fallback(
            source,
            filename,
            max_width=settings['max_width'],
            max_height=settings.get('max_height'),
            crop=crop,
            quality=88,
        )


def reprocess_existing_banner_file(image_field, *, tip='hero'):
    if not image_field or not image_field.name:
        return None
    image_field.open('rb')
    try:
        raw = image_field.read()
    finally:
        image_field.close()
    if not raw:
        return None

    if tip == 'hero':
        img = Image.open(BytesIO(raw))
        img.load()
        source = _image_to_rgb(img)
        settings = BANNER_AVIF_SETTINGS['hero']
        return _save_hero_responsive_variants(
            source,
            image_field.name,
            crop=settings.get('crop', False),
        )

    img = Image.open(BytesIO(raw))
    settings = BANNER_AVIF_SETTINGS.get(tip, {
        'max_bytes': MAX_DEFAULT_BANNER_AVIF_BYTES,
        'max_width': BANNER_MAX_WIDTH,
    })
    return _encode_banner_avif(img, image_field.name, **settings)


def process_product_image(image_source, *, filename='image.jpg'):
    """Artikal/varijacija: AVIF max 20KB, bez uklanjanja pozadine."""
    raw, filename = _read_image_source(image_source, filename=filename)
    img = Image.open(BytesIO(raw))
    return _encode_product_avif(img, filename)


def _ensure_rgba(img):
    img = ImageOps.exif_transpose(img)
    if img.mode == 'RGBA':
        return img
    if img.mode == 'LA':
        return img.convert('RGBA')
    if img.mode == 'P':
        return img.convert('RGBA')
    if img.mode == 'RGB':
        rgba = Image.new('RGBA', img.size)
        rgba.paste(img)
        return rgba
    return img.convert('RGBA')


def _content_mask(
    rgba_img,
    *,
    alpha_threshold=12,
    white_threshold=PRODUCT_WHITE_THRESHOLD,
):
    """Maska samo stvarnog artikla — bez transparentne ili bijele pozadine."""
    from PIL import ImageChops

    rgba = _ensure_rgba(rgba_img)
    r, g, b, a = rgba.split()
    alpha_mask = a.point(lambda value: 255 if value > alpha_threshold else 0)
    not_white_r = r.point(lambda value: 0 if value >= white_threshold else 255)
    not_white_g = g.point(lambda value: 0 if value >= white_threshold else 255)
    not_white_b = b.point(lambda value: 0 if value >= white_threshold else 255)
    not_white = ImageChops.multiply(
        not_white_r,
        ImageChops.multiply(not_white_g, not_white_b),
    )
    return ImageChops.multiply(alpha_mask, not_white)


def _crop_to_content(rgba_img, *, alpha_threshold=12, white_threshold=PRODUCT_WHITE_THRESHOLD):
    rgba = _ensure_rgba(rgba_img)
    mask = _content_mask(
        rgba,
        alpha_threshold=alpha_threshold,
        white_threshold=white_threshold,
    )
    bbox = mask.getbbox()
    if bbox:
        return rgba.crop(bbox)
    return rgba


def process_product_image_bytes(raw_bytes, filename='image.jpg', **kwargs):
    return process_product_image(raw_bytes, filename=filename)


def process_product_image_manual(image_field):
    return process_product_image(image_field)


def reprocess_existing_image_file(image_field):
    if not image_field or not image_field.name:
        return None
    image_field.open('rb')
    try:
        raw = image_field.read()
    finally:
        image_field.close()
    if not raw:
        return None
    return process_product_image(raw, filename=image_field.name)


def apply_image_processing(instance, field_name, post_process=None):
    image_field = getattr(instance, field_name, None)
    if not image_field or not is_new_upload(image_field):
        return
    _reset_upload(image_field)
    try:
        processed = post_process(image_field) if post_process else image_field
        getattr(instance, field_name).save(processed.name, processed, save=False)
    except Exception as exc:
        _reset_upload(image_field)
        logger.exception('Obrada slike nije uspjela za %s.%s', instance, field_name)
        raise ValueError(
            f'Obrada slike nije uspjela: {exc}. Pokušajte manju sliku ili JPG/PNG format.',
        ) from exc


def _logo_target_size(canvas_size, fill_ratio=1.0):
    if fill_ratio >= 1.0:
        return canvas_size
    padding = (1 - fill_ratio) / 2
    return (
        max(1, int(canvas_size[0] * (1 - 2 * padding))),
        max(1, int(canvas_size[1] * (1 - 2 * padding))),
    )


def _fit_logo_to_canvas(
    image_field,
    canvas_size,
    *,
    white_background=False,
    fill_ratio=1.0,
    trim_content=False,
):
    img = Image.open(image_field)
    img = ImageOps.exif_transpose(img)

    if img.mode in ('RGBA', 'LA', 'P'):
        img = img.convert('RGBA')
    else:
        img = img.convert('RGB')

    if trim_content:
        img = _crop_to_content(_ensure_rgba(img))

    target_size = _logo_target_size(canvas_size, fill_ratio)

    if white_background:
        canvas = Image.new('RGB', canvas_size, (255, 255, 255))
    elif img.mode == 'RGBA':
        canvas = Image.new('RGBA', canvas_size, (255, 255, 255, 0))
    else:
        canvas = Image.new('RGB', canvas_size, (255, 255, 255))

    fitted = ImageOps.contain(img, target_size, method=Image.Resampling.LANCZOS)
    offset = (
        (canvas_size[0] - fitted.size[0]) // 2,
        (canvas_size[1] - fitted.size[1]) // 2,
    )
    if fitted.mode == 'RGBA':
        if white_background:
            white_layer = Image.new('RGB', canvas_size, (255, 255, 255))
            white_layer.paste(fitted, offset, fitted)
            canvas = white_layer
        else:
            canvas.paste(fitted, offset, fitted)
    else:
        canvas.paste(fitted, offset)

    buffer = BytesIO()
    compress_level = 3 if canvas_size == SITE_LOGO_SIZE else 6
    canvas.save(buffer, format='PNG', compress_level=compress_level)
    buffer.seek(0)

    name = _png_filename(image_field.name if hasattr(image_field, 'name') else 'logo.png')
    return ContentFile(buffer.read(), name=name)


def process_site_logo(image_field):
    return _fit_logo_to_canvas(image_field, SITE_LOGO_SIZE, white_background=True)


def process_brand_logo(image_field):
    return _fit_logo_to_canvas(
        image_field,
        BRAND_LOGO_SIZE,
        fill_ratio=BRAND_LOGO_FILL_RATIO,
        trim_content=True,
    )