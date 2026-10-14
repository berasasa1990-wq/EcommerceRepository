"""Trim empty logo edges in presentation without changing source images."""
import hashlib

from django import template
from django.core.cache import cache
from PIL import Image, ImageChops

register = template.Library()


@register.simple_tag
def brand_logo_geometry(image):
    if not image:
        return None
    try:
        version = str(image.storage.get_modified_time(image.name))
    except (OSError, NotImplementedError, AttributeError):
        version = ''
    key = 'brand-logo-bounds-v1:' + hashlib.sha256((image.name + version).encode()).hexdigest()
    bounds = cache.get(key)
    if bounds is None:
        try:
            with image.open('rb') as source:
                with Image.open(source) as original:
                    rgba = original.convert('RGBA')
                    background = Image.new('RGBA', rgba.size, (255, 255, 255, 255))
                    visible = Image.alpha_composite(background, rgba).convert('RGB')
                    difference = ImageChops.difference(visible, Image.new('RGB', visible.size, 'white'))
                    box = difference.convert('L').point(lambda value: 255 if value > 30 else 0).getbbox()
                    box = box or rgba.getchannel('A').getbbox() or (0, 0, *rgba.size)
                    bounds = (*rgba.size, *box)
            cache.set(key, bounds, 3600)
        except (OSError, ValueError, Image.DecompressionBombError):
            return None
    width, height, left, top, right, bottom = bounds
    artwork_width = max(1, right - left)
    artwork_height = max(1, bottom - top)
    frame_width, frame_height = 64, 18
    scale = min(frame_width / artwork_width, frame_height / artwork_height)
    return {
        'width': str(frame_width), 'height': str(frame_height),
        'image_width': f'{width * scale:.2f}', 'image_height': f'{height * scale:.2f}',
        'left': f'{-left * scale:.2f}',
        'top': f'{(frame_height - artwork_height * scale) / 2 - top * scale:.2f}',
    }
