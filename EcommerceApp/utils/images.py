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
PRODUCT_IMAGE_QUALITY = 95


def is_new_upload(image_field):
    return hasattr(image_field, 'file') and isinstance(image_field.file, UploadedFile)


def _png_filename(original_name):
    base = original_name.rsplit('/', 1)[-1]
    return base.rsplit('.', 1)[0] + '.png'


def _output_filename(original_name, fmt):
    base = original_name.rsplit('/', 1)[-1]
    stem = base.rsplit('.', 1)[0] if '.' in base else base
    extensions = {
        'JPEG': '.jpg',
        'PNG': '.png',
        'WEBP': '.webp',
        'GIF': '.gif',
        'AVIF': '.avif',
    }
    return stem + extensions.get(fmt, '.jpg')


def save_product_image_as_is(image_source, *, filename='image.jpg'):
    """Čuva sliku artikla bez uklanjanja pozadine i bez rezanja na canvas."""
    if hasattr(image_source, 'read'):
        image_source.seek(0)
        raw = image_source.read()
        if not raw:
            raise ValueError('Prazna slika')
        if not filename or filename == 'image.jpg':
            filename = getattr(image_source, 'name', None) or 'image.jpg'
    else:
        raw = image_source
        if not raw:
            raise ValueError('Prazna slika')

    img = Image.open(BytesIO(raw))
    img = ImageOps.exif_transpose(img)

    original_format = (img.format or 'JPEG').upper()
    if original_format == 'JPG':
        original_format = 'JPEG'

    buffer = BytesIO()
    save_kwargs = {}
    if original_format == 'JPEG':
        if img.mode in ('RGBA', 'LA', 'P'):
            img = img.convert('RGB')
        save_kwargs = {'quality': PRODUCT_IMAGE_QUALITY, 'optimize': True}
    elif original_format == 'PNG':
        save_kwargs = {'optimize': True}
    elif original_format == 'WEBP':
        save_kwargs = {'quality': PRODUCT_IMAGE_QUALITY}
    elif original_format not in ('GIF', 'AVIF'):
        original_format = 'JPEG'
        if img.mode in ('RGBA', 'LA', 'P'):
            img = img.convert('RGB')
        save_kwargs = {'quality': PRODUCT_IMAGE_QUALITY, 'optimize': True}

    img.save(buffer, format=original_format, **save_kwargs)
    output_name = _output_filename(filename, original_format)
    return ContentFile(buffer.getvalue(), name=output_name)


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
    return save_product_image_as_is(raw_bytes, filename=filename)


def process_product_image_manual(image_field):
    return save_product_image_as_is(image_field)


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
    return save_product_image_as_is(raw, filename=image_field.name)


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