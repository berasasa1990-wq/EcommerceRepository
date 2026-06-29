#!/usr/bin/env python3
"""Minifikacija statičkih CSS/JS fajlova prije collectstatic deploya."""
from pathlib import Path

import rcssmin
import rjsmin

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / 'EcommerceApp' / 'static'
PAIRS = [
    (STATIC / 'css' / 'style.css', STATIC / 'css' / 'style.min.css'),
    (STATIC / 'js' / 'main.js', STATIC / 'js' / 'main.min.js'),
    (STATIC / 'js' / 'product-detail.js', STATIC / 'js' / 'product-detail.min.js'),
    (STATIC / 'js' / 'cart-qty.js', STATIC / 'js' / 'cart-qty.min.js'),
]


def main():
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