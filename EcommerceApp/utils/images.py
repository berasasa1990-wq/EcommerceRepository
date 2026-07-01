import logging
from io import BytesIO

from django.core.files.base import ContentFile
from django.core.files.uploadedfile import UploadedFile
from PIL import Image, ImageOps, features

logger = logging.getLogger(__name__)

_BANNER_AVIF_SUPPORTED = None

BRAND_LOGO_SIZE = (200, 48)
BRAND_LOGO_FILL_RATIO = 0.80
SITE_LOGO_SIZE = (640, 128)
PRODUCT_WHITE_THRESHOLD = 248
PRODUCT_MAX_DIMENSION = 400
PRODUCT_RESPONSIVE_WIDTHS = (120, 200)
AVIF_SPEED = 6
BANNER_AVIF_SPEED = 4
MAX_VLOG_AVIF_BYTES = 30 * 1024
VLOG_MAX_DIMENSION = 420
BANNER_MAX_WIDTH = 1920
HERO_BANNER_MAX_WIDTH = 1920
HERO_BANNER_MAX_HEIGHT = 640
MAX_GRID_BANNER_AVIF_BYTES = 85 * 1024
GRID_BANNER_MAX_DIMENSION = 420
MAX_HERO_BANNER_AVIF_BYTES = 220 * 1024
MAX_FEATURED_BANNER_AVIF_BYTES = 200 * 1024
MAX_SPOTLIGHT_BANNER_AVIF_BYTES = 200 * 1024
MAX_DEFAULT_BANNER_AVIF_BYTES = 200 * 1024
BANNER_AVIF_MAX_QUALITY = 88
BANNER_AVIF_MIN_QUALITY = 72
BANNER_AVIF_QUALITY_STEP = 3
MAX_PRODUCT_AVIF_BYTES = 15 * 1024
PRODUCT_VARIANT_MAX_BYTES = {
    120: 4 * 1024,
    200: 8 * 1024,
    400: MAX_PRODUCT_AVIF_BYTES,
}

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


def _encode_avif_under_budget(
    img,
    filename,
    *,
    max_bytes,
    max_dimension,
    strict=False,
    scale_steps=None,
    quality_step=5,
):
    filename = _avif_filename(filename)
    working = _fit_product_dimensions(_image_to_rgb(img), max_dimension=max_dimension)

    best_data = None
    best_size = float('inf')
    scales = scale_steps or (
        1.0, 0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.35, 0.3, 0.25, 0.2,
    )

    for scale in scales:
        if scale < 1.0:
            new_w = max(1, int(working.width * scale))
            new_h = max(1, int(working.height * scale))
            candidate = working.resize((new_w, new_h), Image.Resampling.LANCZOS)
        else:
            candidate = working

        for quality in range(85, 0, -quality_step):
            data = _encode_avif(candidate, quality)
            size = len(data)
            if size <= max_bytes:
                return ContentFile(data, name=filename)
            if size < best_size:
                best_size = size
                best_data = data

    if strict:
        raise ValueError(
            f'Slika se ne može smanjiti ispod {max_bytes // 1024}KB. '
            'Koristite jednostavniju sliku ili manju rezoluciju.',
        )

    logger.warning(
        'Slika nije smanjena ispod %dKB (najmanje: %d bytes), čuva se najbliža AVIF verzija.',
        max_bytes // 1024,
        best_size,
    )
    return ContentFile(best_data, name=filename)


def _product_responsive_variant_name(main_name, width):
    base = main_name.rsplit('/', 1)[-1].rsplit('.', 1)[0]
    folder = main_name.rsplit('/', 1)[0] if '/' in main_name else ''
    variant = f'{base}-{width}w.avif'
    return f'{folder}/{variant}' if folder else variant


def _encode_product_variant(rgb_img, filename, *, max_dimension):
    return _encode_avif_under_budget(
        rgb_img,
        filename,
        max_bytes=PRODUCT_VARIANT_MAX_BYTES.get(max_dimension, MAX_PRODUCT_AVIF_BYTES),
        max_dimension=max_dimension,
        strict=True,
        scale_steps=(
            1.0, 0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.35, 0.3, 0.25, 0.2, 0.15, 0.12, 0.1,
        ),
        quality_step=3,
    )


def _encode_product_avif(img, filename):
    return _encode_product_variant(
        _image_to_rgb(img),
        _avif_filename(filename),
        max_dimension=PRODUCT_MAX_DIMENSION,
    )


def _build_product_responsive_variants(rgb_img, main_filename):
    variants = {}
    for width in PRODUCT_RESPONSIVE_WIDTHS:
        variant_name = _product_responsive_variant_name(
            _avif_filename(main_filename),
            width,
        )
        variants[width] = _encode_product_variant(
            rgb_img,
            variant_name,
            max_dimension=width,
        )
    return variants


def delete_product_responsive_variants(storage, main_name):
    if not main_name:
        return
    for width in PRODUCT_RESPONSIVE_WIDTHS:
        variant_name = _product_responsive_variant_name(main_name, width)
        if storage.exists(variant_name):
            storage.delete(variant_name)


def save_product_responsive_variants(storage, main_name, variants):
    if not main_name:
        return
    for width, content in variants.items():
        variant_name = _product_responsive_variant_name(main_name, width)
        if storage.exists(variant_name):
            storage.delete(variant_name)
        storage.save(variant_name, content)


def save_processed_product_image(image_field, processed):
    if isinstance(processed, dict) and 'main' in processed:
        main = processed['main']
        variants = processed.get('variants', {})
        storage = image_field.storage
        old_name = image_field.name
        if old_name:
            delete_product_responsive_variants(storage, old_name)
        image_field.save(main.name, main, save=False)
        save_product_responsive_variants(storage, image_field.name, variants)
        return image_field
    image_field.save(processed.name, processed, save=False)
    return image_field


def product_image_responsive_meta(image_field, *, default=(400, 400)):
    if not image_field or not image_field.name:
        return {
            'src': '',
            'srcset': '',
            'width': default[0],
            'height': default[1],
        }

    width, height = image_field_dimensions(image_field, default=default)
    display_width = min(width, PRODUCT_MAX_DIMENSION)
    display_height = max(1, int(height * (display_width / width))) if width else default[1]

    storage = image_field.storage
    entries = []
    for variant_width in PRODUCT_RESPONSIVE_WIDTHS:
        variant_name = _product_responsive_variant_name(image_field.name, variant_width)
        if storage.exists(variant_name):
            entries.append(f'{storage.url(variant_name)} {variant_width}w')

    entries.append(f'{image_field.url} {display_width}w')
    return {
        'src': image_field.url,
        'srcset': ', '.join(entries),
        'width': display_width,
        'height': display_height,
    }


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


def process_banner_image(image_field, tip='hero'):
    """Banneri: Hero u JPEG (pouzdano), ostalo AVIF uz JPEG rezervu."""
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

    jpeg_quality = 90 if tip == 'hero' else 88
    use_jpeg = tip == 'hero' or not _banner_avif_supported()
    crop = settings.get('crop', False)
    if use_jpeg:
        return _encode_banner_jpeg_fallback(
            source,
            filename,
            max_width=settings['max_width'],
            max_height=settings.get('max_height'),
            crop=crop,
            quality=jpeg_quality,
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
            quality=jpeg_quality,
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
    img = Image.open(BytesIO(raw))
    settings = BANNER_AVIF_SETTINGS.get(tip, {
        'max_bytes': MAX_DEFAULT_BANNER_AVIF_BYTES,
        'max_width': BANNER_MAX_WIDTH,
    })
    if tip == 'hero':
        source = _image_to_rgb(img)
        return _encode_banner_jpeg_fallback(
            source,
            image_field.name,
            max_width=settings['max_width'],
            max_height=settings.get('max_height'),
            crop=settings.get('crop', False),
            quality=90,
        )
    return _encode_banner_avif(img, image_field.name, **settings)


def process_product_image(image_source, *, filename='image.jpg'):
    """Artikal/varijacija: AVIF max 15KB + responsive varijante 120w/200w."""
    raw, filename = _read_image_source(image_source, filename=filename)
    img = Image.open(BytesIO(raw))
    rgb = _image_to_rgb(img)
    main = _encode_product_avif(rgb, filename)
    return {
        'main': main,
        'variants': _build_product_responsive_variants(rgb, filename),
    }


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


def prepared_product_image_payload(processed):
    if isinstance(processed, dict) and 'main' in processed:
        main = processed['main']
        payload = {
            'name': main.name,
            'data': main.read(),
            'variants': {},
        }
        for width, content in processed.get('variants', {}).items():
            payload['variants'][width] = content.read()
        return payload
    return {'name': processed.name, 'data': processed.read(), 'variants': {}}


def save_prepared_product_image(image_field, prepared_image):
    content = ContentFile(prepared_image['data'], name=prepared_image['name'])
    variants = {
        width: ContentFile(
            data,
            name=_product_responsive_variant_name(prepared_image['name'], width),
        )
        for width, data in prepared_image.get('variants', {}).items()
    }
    return save_processed_product_image(image_field, {'main': content, 'variants': variants})


def apply_image_processing(instance, field_name, post_process=None):
    image_field = getattr(instance, field_name, None)
    if not image_field or not is_new_upload(image_field):
        return
    _reset_upload(image_field)
    try:
        processed = post_process(image_field) if post_process else image_field
        save_processed_product_image(getattr(instance, field_name), processed)
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