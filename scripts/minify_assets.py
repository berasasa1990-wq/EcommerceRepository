#!/usr/bin/env python3
"""Minifikacija statičkih CSS/JS fajlova prije collectstatic deploya."""
from pathlib import Path
import gzip

try:
    import brotli
except ImportError:
    brotli = None

import rcssmin
import rjsmin

from css_optimize import deduplicate_rules, public_styles

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / 'EcommerceApp' / 'static'
PAIRS = [
    (STATIC / 'js' / 'main.js', STATIC / 'js' / 'main.min.js'),
    (STATIC / 'js' / 'product-detail.js', STATIC / 'js' / 'product-detail.min.js'),
    (STATIC / 'js' / 'cart-qty.js', STATIC / 'js' / 'cart-qty.min.js'),
    (STATIC / 'js' / 'set-builder.js', STATIC / 'js' / 'set-builder.min.js'),
]


def build_style_min():
    fonts = STATIC / 'css' / 'fonts.css'
    style = STATIC / 'css' / 'style.css'
    dest = STATIC / 'css' / 'style.min.css'
    parts = []
    if fonts.exists():
        parts.append(fonts.read_text(encoding='utf-8'))
    if style.exists():
        parts.append(style.read_text(encoding='utf-8'))
    if not parts:
        return
    minified = rcssmin.cssmin('\n'.join(parts))
    optimized = deduplicate_rules(minified)
    dest.write_text(optimized, encoding='utf-8')
    data = optimized.encode('utf-8')
    dest.with_suffix('.css.gz').write_bytes(gzip.compress(data, compresslevel=9, mtime=0))
    if brotli is not None:
        dest.with_suffix('.css.br').write_bytes(brotli.compress(data, quality=11))
    print(f'CSS duplicate removal: {len(minified):,} -> {len(optimized):,} characters')
    print('fonts.css + style.css -> style.min.css')


CSS_BUNDLES = {
    'header-base.min.css': ['menu-color.v20260909.css', 'site-logo-compact.v20260912.css', 'mobile-header-actions.v20260909.css'],
    'navigation.min.css': ['touch-navigation.css', 'wholesale-nav.css'],
    'storefront-extras.min.css': ['search-popup-compact.css', 'newsletter-gray.css'],
    'mobile-chrome.min.css': ['home-mobile-reference.css', 'mobile-dock.css', 'wishlist.css'],
    'storefront-theme.min.css': ['product-card-reference.css', 'site-orange-theme.css'],
}


def write_css_bundle(output, sources):
    content = '\n'.join((STATIC / 'css' / name).read_text(encoding='utf-8') for name in sources)
    data = rcssmin.cssmin(content).encode('utf-8')
    dest = STATIC / 'css' / output
    dest.write_bytes(data)
    dest.with_suffix('.css.gz').write_bytes(gzip.compress(data, compresslevel=9, mtime=0))
    if brotli is not None:
        dest.with_suffix('.css.br').write_bytes(brotli.compress(data, quality=11))
    print(f"{', '.join(sources)} -> {output}")


def build_css_bundles():
    for output, sources in CSS_BUNDLES.items():
        write_css_bundle(output, sources)
    # Keep the base rules and header fixes in their original cascade order.
    write_css_bundle('storefront-core.min.css', ['style.min.css', 'header-base.min.css'])
    for source in ['storefront-core.min.css', 'storefront-theme.min.css']:
        path = STATIC / 'css' / source
        full = path.read_text(encoding='utf-8')
        public = public_styles(full).encode('utf-8')
        dest = path.with_name(source.replace('.min.css', '-public.min.css'))
        dest.write_bytes(public)
        dest.with_suffix('.css.gz').write_bytes(gzip.compress(public, compresslevel=9, mtime=0))
        if brotli is not None:
            dest.with_suffix('.css.br').write_bytes(brotli.compress(public, quality=11))
        print(f'{source}: public CSS {len(full.encode()):,} -> {len(public):,} bytes')



def main():
    build_style_min()
    build_css_bundles()
    for src, dest in PAIRS:
        if not src.exists():
            continue
        content = src.read_text(encoding='utf-8')
        if dest.suffix == '.css':
            dest.write_text(rcssmin.cssmin(content), encoding='utf-8')
        else:
            dest.write_text(rjsmin.jsmin(content), encoding='utf-8')
        print(f'{src.name} -> {dest.name}')


if __name__ == '__main__':
    main()