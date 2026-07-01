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
PRODUCT_RESPONSIVE_WIDTHS = (120, 200, 320)
AVIF_SPEED = 6
BANNER_AVIF_SPEED = 4
MAX_VLOG_AVIF_BYTES = 18 * 1024
VLOG_MAX_DIMENSION = 360
VLOG_RESPONSIVE_WIDTHS = (200, 280, 320)
BANNER_MAX_WIDTH = 1920
HERO_BANNER_MAX_WIDTH = 1920
HERO_BANNER_MAX_HEIGHT = 560
HERO_BANNER_RESPONSIVE_WIDTHS = (640, 960, 1280)
HERO_JPEG_VARIANT_MAX_BYTES = {
    640: 28 * 1024,
    960: 48 * 1024,
    1280: 85 * 1024,
}
MAX_GRID_BANNER_AVIF_BYTES = 22 * 1024
GRID_BANNER_MAX_DIMENSION = 360
BANNER_GRID_RESPONSIVE_WIDTHS = (280,)
MAX_HERO_BANNER_AVIF_BYTES = 220 * 1024
MAX_FEATURED_BANNER_AVIF_BYTES = 200 * 1024
MAX_SPOTLIGHT_BANNER_AVIF_BYTES = 200 * 1024
MAX_DEFAULT_BANNER_AVIF_BYTES = 200 * 1024
FEATURED_BANNER_RESPONSIVE_WIDTHS = (400, 800, 1200)
SPOTLIGHT_BANNER_RESPONSIVE_WIDTHS = (400, 800, 1200)
BANNER_AVIF_MAX_QUALITY = 82
BANNER_AVIF_MIN_QUALITY = 68
BANNER_AVIF_QUALITY_STEP = 2
MAX_PRODUCT_AVIF_BYTES = 15 * 1024
MAIN_SCALE_STEPS = (1.0, 0.85, 0.7, 0.55, 0.4, 0.3)
FAST_VARIANT_QUALITIES = (68, 58, 48, 38, 28)
PRODUCT_VARIANT_MAX_BYTES = {
    120: 4 * 1024,
    200: 8 * 1024,
    320: 12 * 1024,
    400: MAX_PRODUCT_AVIF_BYTES,
}
VLOG_VARIANT_MAX_BYTES = {
    200: 6 * 1024,
    280: 10 * 1024,
    320: 14 * 1024,
    360: MAX_VLOG_AVIF_BYTES,
}
BANNER_GRID_VARIANT_MAX_BYTES = {
    280: 12 * 1024,
    360: MAX_GRID_BANNER_AVIF_BYTES,
}
BANNER_WIDE_VARIANT_MAX_BYTES = {
    400: 18 * 1024,
    800: 32 * 1024,
    1200: MAX_FEATURED_BANNER_AVIF_BYTES,
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


def _avif_supported():
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
        logger.warning('AVIF enkoder nije dostupan na serveru, koristi se JPEG rezerva.', exc_info=True)
        _BANNER_AVIF_SUPPORTED = False
    return _BANNER_AVIF_SUPPORTED


_banner_avif_supported = _avif_supported


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


def _encode_avif_safe(img, quality, *, speed=AVIF_SPEED):
    try:
        return _encode_avif(img, quality, speed=speed)
    except Exception:
        logger.debug('AVIF enkodiranje nije uspjelo (quality=%s).', quality, exc_info=True)
        return None


def _encode_jpeg_image(rgb_img, filename, *, max_dimension, max_bytes, quality=82):
    filename = _jpeg_filename(filename)
    working = _fit_product_dimensions(_image_to_rgb(rgb_img), max_dimension=max_dimension)
    for q in (quality, 75, 65, 55, 45):
        buffer = BytesIO()
        working.save(buffer, format='JPEG', quality=q, optimize=True, progressive=True)
        data = buffer.getvalue()
        if len(data) <= max_bytes:
            return ContentFile(data, name=filename)
    return ContentFile(data, name=filename)


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
            if rgb.width > max_width or rgb.height > max_height:
                return ImageOps.fit(
                    rgb,
                    (max_width, max_height),
                    method=Image.Resampling.LANCZOS,
                    centering=(0.5, 0.5),
                )
            return rgb
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


def _upload_basename(filename):
    return filename.rsplit('/', 1)[-1] if filename else 'upload.jpg'


def _original_content_file(raw_bytes, filename):
    return ContentFile(raw_bytes, name=_upload_basename(filename))


def _banner_needs_resize(rgb_img, *, max_width, max_height=None, crop=False):
    width, height = rgb_img.size
    if max_height is not None:
        return width > max_width or height > max_height
    return width > max_width


def _cap_byte_budget(configured_max, upload_cap):
    if upload_cap is None:
        return configured_max
    return min(configured_max, upload_cap)


def _read_processed_bytes(processed):
    if hasattr(processed, 'read'):
        processed.seek(0)
        data = processed.read()
        name = getattr(processed, 'name', 'banner.jpg')
        processed.seek(0)
        return data, name
    return processed, 'banner.jpg'


def _finalize_banner_upload(processed, raw_original, original_filename, *, needs_resize):
    data, name = _read_processed_bytes(processed)
    original_size = len(raw_original)
    if not needs_resize and len(data) >= original_size:
        logger.debug(
            'Banner: čuvam original (%d B) umjesto obrade (%d B).',
            original_size,
            len(data),
        )
        return _original_content_file(raw_original, original_filename)
    if needs_resize and len(data) > original_size:
        logger.debug(
            'Banner nakon resize: %d B (original %d B).',
            len(data),
            original_size,
        )
    return ContentFile(data, name=name)


def _finalize_banner_result(result, raw_original, original_filename, *, needs_resize):
    if isinstance(result, dict) and 'main' in result:
        result['main'] = _finalize_banner_upload(
            result['main'],
            raw_original,
            original_filename,
            needs_resize=needs_resize,
        )
        return result
    return _finalize_banner_upload(
        result,
        raw_original,
        original_filename,
        needs_resize=needs_resize,
    )


def _load_banner_rgb_from_raw(raw_bytes):
    with Image.open(BytesIO(raw_bytes)) as img:
        img.load()
        return _image_to_rgb(img)


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

        for quality in range(80, 0, -quality_step):
            data = _encode_avif_safe(candidate, quality)
            if data is None:
                continue
            size = len(data)
            if size <= max_bytes:
                return ContentFile(data, name=filename)
            if size < best_size:
                best_size = size
                best_data = data

    if best_data is None:
        raise ValueError('AVIF enkoder nije dostupan. Koristite JPG ili PNG.')

    if strict:
        logger.warning(
            'Slika nije smanjena ispod %dKB (najmanje: %d bytes), čuva se najbliža verzija.',
            max_bytes // 1024,
            best_size,
        )

    return ContentFile(best_data, name=filename)


def _responsive_variant_name(main_name, width):
    if '/' in main_name:
        folder, filename = main_name.rsplit('/', 1)
    else:
        folder, filename = '', main_name
    base, ext = filename.rsplit('.', 1)
    variant = f'{base}-{width}w.{ext}'
    return f'{folder}/{variant}' if folder else variant


_product_responsive_variant_name = _responsive_variant_name


def _encode_fast_responsive_variant(
    rgb_img,
    filename,
    *,
    max_dimension,
    max_bytes,
    fit_banner=False,
    banner_settings=None,
):
    if fit_banner and banner_settings:
        working = _fit_banner_dimensions(
            rgb_img,
            max_width=banner_settings.get('variant_width', max_dimension),
            max_height=banner_settings.get('variant_height'),
            crop=banner_settings.get('crop', False),
        )
    else:
        working = _fit_product_dimensions(_image_to_rgb(rgb_img), max_dimension=max_dimension)

    if not filename.endswith(('.avif', '.jpg', '.jpeg')):
        filename = _avif_filename(filename)

    best_data = None
    for quality in FAST_VARIANT_QUALITIES:
        data = _encode_avif_safe(working, quality)
        if data is None:
            break
        if len(data) <= max_bytes:
            return ContentFile(data, name=filename)
        best_data = data

    if best_data is not None:
        return ContentFile(best_data, name=filename)

    buffer = BytesIO()
    working.save(buffer, format='JPEG', quality=70, optimize=True, progressive=True)
    jpeg_name = _jpeg_filename(filename)
    return ContentFile(buffer.getvalue(), name=jpeg_name)


def _encode_avif_variant(
    rgb_img,
    filename,
    *,
    max_dimension,
    max_bytes,
    fit_banner=False,
    banner_settings=None,
):
    if fit_banner and banner_settings:
        working = _fit_banner_dimensions(
            rgb_img,
            max_width=banner_settings.get('max_width', max_dimension),
            max_height=banner_settings.get('max_height'),
            crop=banner_settings.get('crop', False),
        )
        filename = _avif_filename(filename) if not filename.endswith('.avif') else filename
        best_data = None
        for quality in range(BANNER_AVIF_MAX_QUALITY, BANNER_AVIF_MIN_QUALITY - 1, -BANNER_AVIF_QUALITY_STEP):
            data = _encode_avif_safe(working, quality, speed=BANNER_AVIF_SPEED)
            if data is None:
                break
            if len(data) <= max_bytes:
                return ContentFile(data, name=filename)
            best_data = data
        if best_data is not None:
            return ContentFile(best_data, name=filename)
        buffer = BytesIO()
        working.save(buffer, format='JPEG', quality=82, optimize=True, progressive=True)
        return ContentFile(buffer.getvalue(), name=_jpeg_filename(filename))

    return _encode_avif_under_budget(
        rgb_img,
        filename,
        max_bytes=max_bytes,
        max_dimension=max_dimension,
        strict=False,
        scale_steps=MAIN_SCALE_STEPS,
        quality_step=4,
    )


def _build_responsive_variants(
    rgb_img,
    main_filename,
    *,
    widths,
    max_bytes_map,
    main_max_dimension,
    fit_banner=False,
    banner_settings=None,
):
    main_name = main_filename
    if not fit_banner:
        main_name = _avif_filename(main_filename)
    variants = {}
    for width in widths:
        if width >= main_max_dimension:
            continue
        variant_name = _responsive_variant_name(main_name, width)
        variant_settings = dict(banner_settings or {})
        if fit_banner:
            variant_settings['variant_width'] = width
            if variant_settings.get('max_height') and variant_settings.get('max_width'):
                ratio = variant_settings['max_height'] / variant_settings['max_width']
                variant_settings['variant_height'] = max(1, int(width * ratio))
        try:
            variants[width] = _encode_fast_responsive_variant(
                rgb_img,
                variant_name,
                max_dimension=width,
                max_bytes=max_bytes_map.get(
                    width,
                    max_bytes_map.get(main_max_dimension, MAX_PRODUCT_AVIF_BYTES),
                ),
                fit_banner=fit_banner,
                banner_settings=variant_settings,
            )
        except Exception:
            logger.warning(
                'Responsive varijanta %sw nije kreirana za %s',
                width,
                main_filename,
                exc_info=True,
            )
    return variants


def delete_responsive_variants(storage, main_name, widths):
    if not main_name:
        return
    if '/' in main_name:
        folder, filename = main_name.rsplit('/', 1)
    else:
        folder, filename = '', main_name
    base = filename.rsplit('.', 1)[0]
    for width in widths:
        for ext in ('avif', 'jpg', 'jpeg', 'png', 'webp'):
            variant = f'{base}-{width}w.{ext}'
            path = f'{folder}/{variant}' if folder else variant
            if storage.exists(path):
                storage.delete(path)


def save_responsive_variants(storage, main_name, variants):
    if not main_name:
        return
    for width, content in variants.items():
        variant_name = _responsive_variant_name(main_name, width)
        if storage.exists(variant_name):
            storage.delete(variant_name)
        storage.save(variant_name, content)


def save_processed_image(image_field, processed, *, responsive_widths=()):
    if isinstance(processed, dict) and 'main' in processed:
        main = processed['main']
        variants = processed.get('variants', {})
        storage = image_field.storage
        old_name = image_field.name
        if old_name:
            delete_responsive_variants(storage, old_name, responsive_widths)
        image_field.save(main.name, main, save=False)
        save_responsive_variants(storage, image_field.name, variants)
        return image_field
    image_field.save(processed.name, processed, save=False)
    return image_field


save_processed_product_image = save_processed_image


def _responsive_variant_url(storage, main_name, width):
    if '/' in main_name:
        folder, filename = main_name.rsplit('/', 1)
    else:
        folder, filename = '', main_name
    base = filename.rsplit('.', 1)[0]
    for ext in ('avif', 'jpg', 'jpeg', 'webp', 'png'):
        variant = f'{base}-{width}w.{ext}'
        path = f'{folder}/{variant}' if folder else variant
        if storage.exists(path):
            return storage.url(path)
    return None


def image_responsive_meta(
    image_field,
    *,
    widths=(),
    max_dimension=None,
    default=(400, 400),
):
    if not image_field or not image_field.name:
        return {
            'src': '',
            'srcset': '',
            'preload_src': '',
            'width': default[0],
            'height': default[1],
        }

    width, height = image_field_dimensions(image_field, default=default)
    cap = max_dimension or width
    display_width = min(width, cap)
    display_height = max(1, int(height * (display_width / width))) if width else default[1]

    storage = image_field.storage
    entries = []
    smallest_url = None
    smallest_width = None
    for variant_width in sorted(widths):
        variant_url = _responsive_variant_url(storage, image_field.name, variant_width)
        if variant_url:
            entries.append(f'{variant_url} {variant_width}w')
            if smallest_width is None or variant_width < smallest_width:
                smallest_width = variant_width
                smallest_url = variant_url

    main_url = image_field.url
    entries.append(f'{main_url} {display_width}w')
    fallback_src = smallest_url or main_url
    return {
        'src': fallback_src,
        'srcset': ', '.join(entries),
        'preload_src': fallback_src,
        'width': display_width,
        'height': display_height,
    }


def product_image_responsive_meta(image_field, *, default=(400, 400)):
    return image_responsive_meta(
        image_field,
        widths=PRODUCT_RESPONSIVE_WIDTHS,
        max_dimension=PRODUCT_MAX_DIMENSION,
        default=default,
    )


def vlog_image_responsive_meta(image_field, *, default=(360, 360)):
    return image_responsive_meta(
        image_field,
        widths=VLOG_RESPONSIVE_WIDTHS,
        max_dimension=VLOG_MAX_DIMENSION,
        default=default,
    )


def banner_image_responsive_meta(image_field, *, tip='grid', default=None):
    if default is None:
        default = (360, 360) if tip == 'grid' else (1200, 800)
    widths = {
        'grid': BANNER_GRID_RESPONSIVE_WIDTHS,
        'hero': HERO_BANNER_RESPONSIVE_WIDTHS,
        'featured': FEATURED_BANNER_RESPONSIVE_WIDTHS,
        'spotlight': SPOTLIGHT_BANNER_RESPONSIVE_WIDTHS,
    }.get(tip, FEATURED_BANNER_RESPONSIVE_WIDTHS)
    max_dimension = {
        'grid': GRID_BANNER_MAX_DIMENSION,
        'hero': HERO_BANNER_MAX_WIDTH,
        'featured': BANNER_MAX_WIDTH,
        'spotlight': BANNER_MAX_WIDTH,
    }.get(tip, BANNER_MAX_WIDTH)
    return image_responsive_meta(
        image_field,
        widths=widths,
        max_dimension=max_dimension,
        default=default,
    )


def _encode_product_avif(rgb_img, filename):
    return _encode_avif_variant(
        rgb_img,
        _avif_filename(filename),
        max_dimension=PRODUCT_MAX_DIMENSION,
        max_bytes=MAX_PRODUCT_AVIF_BYTES,
    )


def _processed_image_result(
    rgb_img,
    filename,
    *,
    widths,
    max_bytes_map,
    main_max_dimension,
    fit_banner=False,
    banner_settings=None,
    upload_byte_cap=None,
):
    main_bytes = max_bytes_map.get(main_max_dimension, MAX_PRODUCT_AVIF_BYTES)
    main_bytes = _cap_byte_budget(main_bytes, upload_byte_cap)
    try:
        if _avif_supported():
            main = _encode_avif_variant(
                rgb_img,
                filename,
                max_dimension=main_max_dimension,
                max_bytes=main_bytes,
                fit_banner=fit_banner,
                banner_settings=banner_settings,
            )
        else:
            main = _encode_jpeg_image(
                rgb_img,
                filename,
                max_dimension=main_max_dimension,
                max_bytes=main_bytes,
            )
    except Exception as exc:
        logger.warning('AVIF obrada glavne slike nije uspjela, koristim JPEG (%s).', exc)
        main = _encode_jpeg_image(
            rgb_img,
            filename,
            max_dimension=main_max_dimension,
            max_bytes=main_bytes,
        )

    variants = {}
    if widths:
        try:
            variants = _build_responsive_variants(
                rgb_img,
                main.name,
                widths=widths,
                max_bytes_map=max_bytes_map,
                main_max_dimension=main_max_dimension,
                fit_banner=fit_banner,
                banner_settings=banner_settings,
            )
        except Exception:
            logger.warning('Responsive varijante nisu kreirane za %s', filename, exc_info=True)

    return {'main': main, 'variants': variants}


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
            data = _encode_avif_safe(candidate, quality, speed=BANNER_AVIF_SPEED)
            if data is None:
                break
            size = len(data)
            if size <= max_bytes:
                return ContentFile(data, name=filename)
            if size < best_size:
                best_size = size
                best_data = data

    if best_data is not None:
        logger.warning(
            'Banner nije smanjen ispod %dKB (najmanje: %d bytes), čuva se najbliža AVIF verzija.',
            max_bytes // 1024,
            best_size,
        )
        return ContentFile(best_data, name=filename)

    return _encode_banner_jpeg_fallback(
        img,
        filename,
        max_width=max_width,
        max_height=max_height,
        crop=crop,
        quality=82,
    )


def process_vlog_image(image_field):
    """Vlog slike: AVIF max 18KB, max 360px + responsive 180/280w."""
    img = Image.open(image_field)
    filename = image_field.name if hasattr(image_field, 'name') else 'vlog.jpg'
    rgb = _image_to_rgb(img)
    return _processed_image_result(
        rgb,
        filename,
        widths=VLOG_RESPONSIVE_WIDTHS,
        max_bytes_map=VLOG_VARIANT_MAX_BYTES,
        main_max_dimension=VLOG_MAX_DIMENSION,
    )


def _encode_banner_jpeg_fallback(
    img,
    filename,
    *,
    max_width,
    max_height=None,
    crop=False,
    quality=88,
    max_bytes=None,
):
    """Pouzdan JPEG format za banere (posebno Hero)."""
    working = _fit_banner_dimensions(
        img,
        max_width=max_width,
        max_height=max_height,
        crop=crop,
    )
    qualities = []
    for q in (quality, 85, 78, 70, 62, 54, 48, 42, 36):
        if q not in qualities:
            qualities.append(q)

    best_data = None
    for q in qualities:
        buffer = BytesIO()
        working.save(buffer, format='JPEG', quality=q, optimize=True, progressive=True)
        data = buffer.getvalue()
        if max_bytes is not None and len(data) <= max_bytes:
            return ContentFile(data, name=_jpeg_filename(filename))
        if best_data is None or len(data) < len(best_data):
            best_data = data

    return ContentFile(best_data, name=_jpeg_filename(filename))


def _load_banner_source(image_field):
    _reset_upload(image_field)
    with Image.open(image_field) as img:
        img.load()
        return _image_to_rgb(img)


def _hero_variant_height(width):
    return max(1, int(width * HERO_BANNER_MAX_HEIGHT / HERO_BANNER_MAX_WIDTH))


def _encode_hero_jpeg_variant(rgb_img, variant_name, *, width, max_bytes):
    working = _fit_banner_dimensions(
        rgb_img,
        max_width=width,
        max_height=_hero_variant_height(width),
        crop=True,
    )
    best_data = None
    for quality in (85, 78, 70, 62, 54):
        buffer = BytesIO()
        working.save(buffer, format='JPEG', quality=quality, optimize=True, progressive=True)
        data = buffer.getvalue()
        if len(data) <= max_bytes:
            return ContentFile(data, name=variant_name)
        best_data = data
    return ContentFile(best_data, name=variant_name)


def _build_hero_jpeg_variants(rgb_img, main_filename):
    variants = {}
    for width in HERO_BANNER_RESPONSIVE_WIDTHS:
        variant_name = _responsive_variant_name(main_filename, width)
        try:
            variants[width] = _encode_hero_jpeg_variant(
                rgb_img,
                variant_name,
                width=width,
                max_bytes=HERO_JPEG_VARIANT_MAX_BYTES.get(width, 60 * 1024),
            )
        except Exception:
            logger.warning(
                'Hero JPEG varijanta %sw nije kreirana za %s',
                width,
                main_filename,
                exc_info=True,
            )
    return variants


def banner_responsive_widths(tip='grid'):
    return {
        'grid': BANNER_GRID_RESPONSIVE_WIDTHS,
        'hero': HERO_BANNER_RESPONSIVE_WIDTHS,
        'featured': FEATURED_BANNER_RESPONSIVE_WIDTHS,
        'spotlight': SPOTLIGHT_BANNER_RESPONSIVE_WIDTHS,
    }.get(tip, FEATURED_BANNER_RESPONSIVE_WIDTHS)


def process_banner_image(image_field, tip='hero'):
    """Banneri: Hero u JPEG (pouzdano), ostalo AVIF uz JPEG rezervu — jedna slika, bez varijanti."""
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
    use_jpeg = tip == 'hero' or not _avif_supported()
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


def process_banner_image_for_admin(image_field, tip='hero'):
    """Upload: optimizacija uz ograničenje da glavni fajl ne bude veći od originala."""
    filename = image_field.name if hasattr(image_field, 'name') else 'banner.jpg'
    settings = BANNER_AVIF_SETTINGS.get(tip, {
        'max_bytes': MAX_DEFAULT_BANNER_AVIF_BYTES,
        'max_width': BANNER_MAX_WIDTH,
    })
    try:
        raw_original, filename = _read_image_source(image_field, filename=filename)
        source = _load_banner_rgb_from_raw(raw_original)
    except Exception as exc:
        raise ValueError(
            f'Slika se ne može očitati ({exc}). Koristite JPG ili PNG.',
        ) from exc

    upload_byte_cap = len(raw_original)
    needs_resize = _banner_needs_resize(
        source,
        max_width=settings['max_width'],
        max_height=settings.get('max_height'),
        crop=settings.get('crop', False),
    )
    settings = dict(settings)
    settings['max_bytes'] = _cap_byte_budget(settings['max_bytes'], upload_byte_cap)

    if not needs_resize:
        main = _original_content_file(raw_original, filename)
        if tip == 'hero':
            try:
                variants = _build_hero_jpeg_variants(source, main.name)
                return {'main': main, 'variants': variants}
            except Exception:
                logger.warning('Hero responsive varijante nisu kreirane.', exc_info=True)
                return main
        if tip == 'grid':
            try:
                variants = _build_responsive_variants(
                    source,
                    main.name,
                    widths=BANNER_GRID_RESPONSIVE_WIDTHS,
                    max_bytes_map=BANNER_GRID_VARIANT_MAX_BYTES,
                    main_max_dimension=GRID_BANNER_MAX_DIMENSION,
                    fit_banner=True,
                    banner_settings={
                        'max_width': GRID_BANNER_MAX_DIMENSION,
                        'max_height': GRID_BANNER_MAX_DIMENSION,
                        'crop': False,
                    },
                )
                return {'main': main, 'variants': variants}
            except Exception:
                logger.warning('Grid responsive varijante nisu kreirane.', exc_info=True)
                return main
        if tip in ('featured', 'spotlight'):
            widths = (
                SPOTLIGHT_BANNER_RESPONSIVE_WIDTHS
                if tip == 'spotlight'
                else FEATURED_BANNER_RESPONSIVE_WIDTHS
            )
            max_bytes_map = dict(BANNER_WIDE_VARIANT_MAX_BYTES)
            if tip == 'spotlight':
                max_bytes_map[1200] = MAX_SPOTLIGHT_BANNER_AVIF_BYTES
            try:
                variants = _build_responsive_variants(
                    source,
                    main.name,
                    widths=widths,
                    max_bytes_map=max_bytes_map,
                    main_max_dimension=BANNER_MAX_WIDTH,
                    fit_banner=True,
                    banner_settings={
                        'max_width': BANNER_MAX_WIDTH,
                        'crop': False,
                    },
                )
                return {'main': main, 'variants': variants}
            except Exception:
                logger.warning('%s responsive varijante nisu kreirane.', tip, exc_info=True)
                return main

    if tip == 'hero':
        main = _encode_banner_jpeg_fallback(
            source,
            filename,
            max_width=settings['max_width'],
            max_height=settings.get('max_height'),
            crop=settings.get('crop', False),
            quality=85,
            max_bytes=upload_byte_cap,
        )
        result = {'main': main, 'variants': {}}
        try:
            result['variants'] = _build_hero_jpeg_variants(source, main.name)
        except Exception:
            logger.warning('Hero responsive varijante nisu kreirane.', exc_info=True)
        return _finalize_banner_result(
            result,
            raw_original,
            filename,
            needs_resize=needs_resize,
        )

    if tip == 'grid':
        try:
            result = _processed_image_result(
                source,
                filename,
                widths=BANNER_GRID_RESPONSIVE_WIDTHS,
                max_bytes_map=BANNER_GRID_VARIANT_MAX_BYTES,
                main_max_dimension=GRID_BANNER_MAX_DIMENSION,
                fit_banner=True,
                banner_settings={
                    'max_width': GRID_BANNER_MAX_DIMENSION,
                    'max_height': GRID_BANNER_MAX_DIMENSION,
                    'crop': False,
                },
                upload_byte_cap=upload_byte_cap,
            )
            return _finalize_banner_result(
                result,
                raw_original,
                filename,
                needs_resize=needs_resize,
            )
        except Exception as exc:
            logger.warning('Grid AVIF obrada nije uspjela (%s), koristim JPEG.', exc)
            main = _encode_banner_jpeg_fallback(
                source,
                filename,
                max_width=GRID_BANNER_MAX_DIMENSION,
                max_height=GRID_BANNER_MAX_DIMENSION,
                crop=False,
                quality=78,
                max_bytes=upload_byte_cap,
            )
            try:
                variants = _build_responsive_variants(
                    source,
                    main.name,
                    widths=BANNER_GRID_RESPONSIVE_WIDTHS,
                    max_bytes_map=BANNER_GRID_VARIANT_MAX_BYTES,
                    main_max_dimension=GRID_BANNER_MAX_DIMENSION,
                    fit_banner=True,
                    banner_settings={
                        'max_width': GRID_BANNER_MAX_DIMENSION,
                        'max_height': GRID_BANNER_MAX_DIMENSION,
                        'crop': False,
                    },
                )
                return _finalize_banner_result(
                    {'main': main, 'variants': variants},
                    raw_original,
                    filename,
                    needs_resize=needs_resize,
                )
            except Exception:
                logger.warning('Grid JPEG varijante nisu kreirane.', exc_info=True)
                return _finalize_banner_result(
                    main,
                    raw_original,
                    filename,
                    needs_resize=needs_resize,
                )

    if tip in ('featured', 'spotlight'):
        max_bytes_map = dict(BANNER_WIDE_VARIANT_MAX_BYTES)
        if tip == 'spotlight':
            max_bytes_map[1200] = MAX_SPOTLIGHT_BANNER_AVIF_BYTES
        try:
            result = _processed_image_result(
                source,
                filename,
                widths=SPOTLIGHT_BANNER_RESPONSIVE_WIDTHS if tip == 'spotlight' else FEATURED_BANNER_RESPONSIVE_WIDTHS,
                max_bytes_map=max_bytes_map,
                main_max_dimension=BANNER_MAX_WIDTH,
                fit_banner=True,
                banner_settings={
                    'max_width': BANNER_MAX_WIDTH,
                    'crop': False,
                },
                upload_byte_cap=upload_byte_cap,
            )
            return _finalize_banner_result(
                result,
                raw_original,
                filename,
                needs_resize=needs_resize,
            )
        except Exception as exc:
            logger.warning('%s banner obrada nije uspjela (%s), čuvam original ili JPEG.', tip, exc)
            main = _encode_banner_jpeg_fallback(
                source,
                filename,
                max_width=settings['max_width'],
                max_height=settings.get('max_height'),
                crop=settings.get('crop', False),
                quality=85,
                max_bytes=upload_byte_cap,
            )
            return _finalize_banner_result(
                main,
                raw_original,
                filename,
                needs_resize=needs_resize,
            )

    result = process_banner_image(image_field, tip=tip)
    return _finalize_banner_result(
        result,
        raw_original,
        filename,
        needs_resize=needs_resize,
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

    buffer = BytesIO(raw)
    buffer.name = image_field.name
    return process_banner_image_for_admin(buffer, tip=tip)


def process_product_image(image_source, *, filename='image.jpg'):
    """Artikal/varijacija: AVIF max 15KB + responsive 120/200/320w."""
    raw, filename = _read_image_source(image_source, filename=filename)
    img = Image.open(BytesIO(raw))
    rgb = _image_to_rgb(img)
    return _processed_image_result(
        rgb,
        filename,
        widths=PRODUCT_RESPONSIVE_WIDTHS,
        max_bytes_map=PRODUCT_VARIANT_MAX_BYTES,
        main_max_dimension=PRODUCT_MAX_DIMENSION,
    )


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


def reprocess_existing_vlog_file(image_field):
    if not image_field or not image_field.name:
        return None
    image_field.open('rb')
    try:
        raw = image_field.read()
    finally:
        image_field.close()
    if not raw:
        return None
    buffer = BytesIO(raw)
    buffer.name = image_field.name
    return process_vlog_image(buffer)


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
            name=_responsive_variant_name(prepared_image['name'], width),
        )
        for width, data in prepared_image.get('variants', {}).items()
    }
    return save_processed_image(
        image_field,
        {'main': content, 'variants': variants},
        responsive_widths=PRODUCT_RESPONSIVE_WIDTHS,
    )


def _responsive_widths_for_post_process(post_process):
    if post_process is None:
        return ()
    name = getattr(post_process, '__name__', '')
    if name in ('process_product_image_manual', 'process_product_image'):
        return PRODUCT_RESPONSIVE_WIDTHS
    if name == 'process_vlog_image':
        return VLOG_RESPONSIVE_WIDTHS
    if name == 'process_banner_image_for_admin':
        return ()
    return ()


def _banner_responsive_widths_for_instance(instance):
    return banner_responsive_widths(getattr(instance, 'tip', 'grid'))


def _save_raw_upload(image_field):
    upload = getattr(image_field, 'file', None)
    if upload is None:
        return False
    _reset_upload(image_field)
    name = getattr(upload, 'name', None) or 'upload.jpg'
    image_field.save(name, upload, save=False)
    return True


def apply_image_processing(instance, field_name, post_process=None):
    image_field = getattr(instance, field_name, None)
    if not image_field or not is_new_upload(image_field):
        return
    _reset_upload(image_field)
    try:
        processed = post_process(image_field) if post_process else image_field
        if not isinstance(processed, dict) or 'main' not in processed:
            if hasattr(processed, 'read'):
                processed.seek(0)
            getattr(instance, field_name).save(processed.name, processed, save=False)
            return
        responsive_widths = _responsive_widths_for_post_process(post_process)
        func_name = getattr(post_process, '__name__', '') if callable(post_process) else ''
        if func_name == 'process_banner_image_for_admin':
            responsive_widths = _banner_responsive_widths_for_instance(instance)
        elif func_name == '<lambda>':
            responsive_widths = _banner_responsive_widths_for_instance(instance)
        save_processed_image(
            getattr(instance, field_name),
            processed,
            responsive_widths=responsive_widths,
        )
    except Exception as exc:
        _reset_upload(image_field)
        logger.exception('Obrada slike nije uspjela za %s.%s', instance, field_name)
        if _save_raw_upload(getattr(instance, field_name)):
            logger.warning(
                'Sačuvan je originalni upload bez obrade za %s.%s (%s).',
                instance,
                field_name,
                exc,
            )
            return
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