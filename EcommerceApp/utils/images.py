import logging
from io import BytesIO

from django.core.files.base import ContentFile
from django.core.files.uploadedfile import UploadedFile
from PIL import Image, ImageOps

logger = logging.getLogger(__name__)

BRAND_LOGO_SIZE = (200, 48)


def rembg_je_dostupan():
    import importlib.util

    return importlib.util.find_spec('onnxruntime') is not None
BRAND_LOGO_FILL_RATIO = 0.80
SITE_LOGO_SIZE = (640, 128)
PRODUCT_CANVAS_SIZE = (800, 800)
PRODUCT_IMAGE_PADDING_RATIO = 0.04
PRODUCT_WHITE_THRESHOLD = 248
AVIF_SPEED = 6
MAX_PRODUCT_AVIF_BYTES = 20 * 1024


def is_new_upload(image_field):
    return hasattr(image_field, 'file') and isinstance(image_field.file, UploadedFile)


def _png_filename(original_name):
    base = original_name.rsplit('/', 1)[-1]
    return base.rsplit('.', 1)[0] + '.png'


def _avif_filename(original_name):
    base = original_name.rsplit('/', 1)[-1]
    return base.rsplit('.', 1)[0] + '.avif'


def remove_background(image_field):
    if not rembg_je_dostupan():
        logger.warning('rembg/onnxruntime nije dostupan, čuva se originalna slika.')
        image_field.seek(0)
        return ContentFile(image_field.read(), name=_png_filename(image_field.name))

    from rembg import remove

    image_field.seek(0)
    output_bytes = remove(image_field.read())
    return ContentFile(output_bytes, name=_png_filename(image_field.name))


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


def _encode_avif(img, quality):
    buffer = BytesIO()
    img.save(buffer, format='AVIF', quality=quality, speed=AVIF_SPEED)
    return buffer.getvalue()


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


def _trim_white_borders(img, *, white_threshold=PRODUCT_WHITE_THRESHOLD):
    """Skida bijele margine s izvorne slike prije uklanjanja pozadine."""
    rgba = _ensure_rgba(img)
    mask = _content_mask(rgba, white_threshold=white_threshold)
    bbox = mask.getbbox()
    if bbox:
        return rgba.crop(bbox)
    return rgba


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


def _inner_canvas_size():
    padding = PRODUCT_IMAGE_PADDING_RATIO
    return (
        int(PRODUCT_CANVAS_SIZE[0] * (1 - 2 * padding)),
        int(PRODUCT_CANVAS_SIZE[1] * (1 - 2 * padding)),
    )


def _scale_content_to_inner(rgba_img, inner_size):
    """
    Skalira artikal tako da mu najduža strana uvijek popuni isti prostor.
    Svi artikli izgledaju jednako veliki bez obzira na omjer stranica ili
    koliko bijele pozadine je bilo u originalu.
    """
    rgba = _ensure_rgba(rgba_img)
    content_w, content_h = rgba.size
    if content_w < 1 or content_h < 1:
        return rgba

    inner_w, inner_h = inner_size
    target = min(inner_w, inner_h)
    scale = target / max(content_w, content_h)
    new_w = max(1, round(content_w * scale))
    new_h = max(1, round(content_h * scale))
    if (new_w, new_h) == rgba.size:
        return rgba
    return rgba.resize((new_w, new_h), Image.Resampling.LANCZOS)


def _fit_on_product_canvas(rgba_img):
    """Centrira artikal na bijeloj podlozi s fiksnim % margine (PRODUCT_IMAGE_PADDING_RATIO)."""
    inner_size = _inner_canvas_size()
    rgba = _ensure_rgba(rgba_img)
    fitted = _scale_content_to_inner(rgba, inner_size)
    layer = Image.new('RGB', PRODUCT_CANVAS_SIZE, (255, 255, 255))
    offset = (
        (PRODUCT_CANVAS_SIZE[0] - fitted.size[0]) // 2,
        (PRODUCT_CANVAS_SIZE[1] - fitted.size[1]) // 2,
    )
    layer.paste(fitted, offset, fitted)
    return layer


def _flatten_product_canvas(canvas):
    if canvas.mode == 'RGB' and canvas.size == PRODUCT_CANVAS_SIZE:
        return canvas
    if canvas.mode == 'RGB':
        working = canvas
    else:
        working = Image.new('RGB', canvas.size, (255, 255, 255))
        working.paste(canvas, mask=canvas.split()[3] if canvas.mode == 'RGBA' else None)
    if working.size != PRODUCT_CANVAS_SIZE:
        fitted = ImageOps.contain(working, PRODUCT_CANVAS_SIZE, method=Image.Resampling.LANCZOS)
        layer = Image.new('RGB', PRODUCT_CANVAS_SIZE, (255, 255, 255))
        offset = (
            (PRODUCT_CANVAS_SIZE[0] - fitted.size[0]) // 2,
            (PRODUCT_CANVAS_SIZE[1] - fitted.size[1]) // 2,
        )
        layer.paste(fitted, offset)
        return layer
    return working


def _encode_product_avif(canvas, filename, *, keep_canvas_size=False):
    filename = _avif_filename(filename)
    working_canvas = _flatten_product_canvas(canvas)

    if keep_canvas_size:
        best_data = None
        best_size = float('inf')
        for quality in range(90, 0, -1):
            data = _encode_avif(working_canvas, quality)
            size = len(data)
            if size <= MAX_PRODUCT_AVIF_BYTES:
                return ContentFile(data, name=filename)
            if size < best_size:
                best_size = size
                best_data = data
        logger.warning(
            'Ručni upload: slika 800×800 nije ispod 20KB (najmanje: %d bytes), čuva se najniža kvaliteta.',
            best_size,
        )
        return ContentFile(best_data, name=filename)

    best_data = None
    best_size = float('inf')

    for scale in (1.0, 0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.35, 0.3, 0.25, 0.2):
        if scale < 1.0:
            new_w = max(1, int(working_canvas.width * scale))
            new_h = max(1, int(working_canvas.height * scale))
            working = working_canvas.resize((new_w, new_h), Image.Resampling.LANCZOS)
        else:
            working = working_canvas

        for quality in range(85, 0, -5):
            data = _encode_avif(working, quality)
            size = len(data)
            if size <= MAX_PRODUCT_AVIF_BYTES:
                return ContentFile(data, name=filename)
            if size < best_size:
                best_size = size
                best_data = data

    logger.warning(
        'Slika nije smanjena ispod 20KB (najmanje: %d bytes), čuva se najbliža verzija.',
        best_size,
    )
    return ContentFile(best_data, name=filename)


def _strip_background_from_rgba(img):
    if not rembg_je_dostupan():
        logger.warning('rembg/onnxruntime nije dostupan, preskačem uklanjanje pozadine.')
        return img

    try:
        from rembg import remove

        buffer = BytesIO()
        img.save(buffer, format='PNG')
        stripped = remove(buffer.getvalue())
        return _ensure_rgba(Image.open(BytesIO(stripped)))
    except Exception as exc:
        logger.warning('Uklanjanje pozadine nije uspjelo: %s', exc)
        return img


def _prepare_product_canvas(img, *, strip_background=True):
    rgba = _trim_white_borders(img)
    if strip_background:
        rgba = _strip_background_from_rgba(rgba)
    rgba = _crop_to_content(rgba)
    return _fit_on_product_canvas(rgba)


def process_product_image_bytes(raw_bytes, filename='image.jpg', *, strip_background=True):
    img = Image.open(BytesIO(raw_bytes))
    canvas = _prepare_product_canvas(img, strip_background=strip_background)
    if strip_background:
        return _encode_product_avif(canvas, filename)
    return _encode_product_avif(canvas, filename, keep_canvas_size=True)


def process_product_image(image_field, *, strip_background=True):
    image_field.seek(0)
    try:
        img = Image.open(image_field)
    except Exception as exc:
        logger.warning('Učitavanje slike nije uspjelo: %s', exc)
        raise

    filename = image_field.name if hasattr(image_field, 'name') else 'image.jpg'
    canvas = _prepare_product_canvas(img, strip_background=strip_background)
    if strip_background:
        return _encode_product_avif(canvas, filename)
    return _encode_product_avif(canvas, filename, keep_canvas_size=True)


def process_product_image_manual(image_field):
    """Ručni upload artikla/varijacije: 800×800, bijela pozadina, AVIF max 20KB."""
    image_field.seek(0)
    try:
        img = Image.open(image_field)
    except Exception as exc:
        logger.warning('Učitavanje slike nije uspjelo: %s', exc)
        raise
    canvas = _prepare_product_canvas(img, strip_background=True)
    return _encode_product_avif(
        canvas,
        image_field.name if hasattr(image_field, 'name') else 'image.jpg',
        keep_canvas_size=True,
    )


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
    return process_product_image_bytes(raw, image_field.name)


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