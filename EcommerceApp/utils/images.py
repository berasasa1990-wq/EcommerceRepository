import logging
from io import BytesIO

from django.core.files.base import ContentFile
from django.core.files.uploadedfile import UploadedFile
from PIL import Image, ImageOps

logger = logging.getLogger(__name__)

BRAND_LOGO_SIZE = (200, 48)
BRAND_LOGO_FILL_RATIO = 0.80
SITE_LOGO_SIZE = (640, 128)
PRODUCT_WHITE_THRESHOLD = 248
PRODUCT_MAX_DIMENSION = 800
AVIF_SPEED = 6
MAX_VLOG_AVIF_BYTES = 30 * 1024
VLOG_MAX_DIMENSION = 420
BANNER_MAX_WIDTH = 1920
BANNER_JPEG_QUALITY = 82
MAX_GRID_BANNER_AVIF_BYTES = 60 * 1024
GRID_BANNER_MAX_DIMENSION = 480
MAX_PRODUCT_AVIF_BYTES = 20 * 1024


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


def _encode_avif(img, quality):
    buffer = BytesIO()
    img.save(buffer, format='AVIF', quality=quality, speed=AVIF_SPEED)
    return buffer.getvalue()


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


def _jpeg_filename(original_name):
    base = original_name.rsplit('/', 1)[-1]
    return base.rsplit('.', 1)[0] + '.jpg'


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


def process_banner_image(image_field, tip='hero'):
    """Grid banneri: AVIF max 60KB. Ostali tipovi: JPEG max 1920px."""
    img = Image.open(image_field)
    filename = image_field.name if hasattr(image_field, 'name') else 'banner.jpg'
    if tip == 'grid':
        return _encode_avif_under_budget(
            img,
            filename,
            max_bytes=MAX_GRID_BANNER_AVIF_BYTES,
            max_dimension=GRID_BANNER_MAX_DIMENSION,
        )

    rgb = _image_to_rgb(img)
    if rgb.width > BANNER_MAX_WIDTH:
        ratio = BANNER_MAX_WIDTH / rgb.width
        new_size = (BANNER_MAX_WIDTH, max(1, int(rgb.height * ratio)))
        rgb = rgb.resize(new_size, Image.Resampling.LANCZOS)

    buffer = BytesIO()
    rgb.save(buffer, format='JPEG', quality=BANNER_JPEG_QUALITY, optimize=True)
    buffer.seek(0)
    name = _jpeg_filename(filename)
    return ContentFile(buffer.read(), name=name)


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
    try:
        processed = post_process(image_field) if post_process else image_field
        getattr(instance, field_name).save(processed.name, processed, save=False)
    except Exception as exc:
        logger.warning('Obrada slike nije uspjela za %s.%s: %s', instance, field_name, exc)


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