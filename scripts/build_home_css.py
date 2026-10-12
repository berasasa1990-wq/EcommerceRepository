"""Build byte-preserving, cascade-ordered homepage CSS bundles. Run before collectstatic."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / 'EcommerceApp/static/bera_reference/css'
BUNDLES = {
    'home-core.bundle.css': ('style.min.css', 'header-base.min.css'),
    'home-ui.bundle.css': ('home-mobile-reference.css', 'mobile-dock.css', 'wishlist.css'),
}


def bundle_content(sources):
    return b'\n'.join((ROOT / name).read_bytes() for name in sources) + b'\n'


def build():
    for name, sources in BUNDLES.items():
        (ROOT / name).write_bytes(bundle_content(sources))


if __name__ == '__main__':
    build()
