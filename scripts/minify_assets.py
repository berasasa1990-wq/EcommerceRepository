#!/usr/bin/env python3
"""Minifikacija statičkih CSS/JS fajlova prije collectstatic deploya."""
from pathlib import Path

import rcssmin
import rjsmin

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / 'EcommerceApp' / 'static'
PAIRS = [
    (STATIC / 'js' / 'main.js', STATIC / 'js' / 'main.min.js'),
    (STATIC / 'js' / 'product-detail.js', STATIC / 'js' / 'product-detail.min.js'),
    (STATIC / 'js' / 'cart-qty.js', STATIC / 'js' / 'cart-qty.min.js'),
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
    dest.write_text(rcssmin.cssmin('\n'.join(parts)), encoding='utf-8')
    print('fonts.css + style.css -> style.min.css')


def main():
    build_style_min()
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