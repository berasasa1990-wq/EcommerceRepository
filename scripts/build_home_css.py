"""Build minified, cascade-ordered active storefront CSS before collectstatic."""
from pathlib import Path
import rcssmin
try:
    from .css_safe_minify import minify_css
except ImportError:  # Direct Render build command: python scripts/build_home_css.py
    from css_safe_minify import minify_css

ROOT = Path(__file__).resolve().parents[1] / 'EcommerceApp/static/bera_reference/css'
CRITICAL_TEMPLATE = ROOT.parents[2] / 'template/bera_reference/partials/home_critical_css.html'
BUNDLES = {
    'home-core.bundle.css': ('style.min.css', 'header-base.min.css'),
    'home-ui.bundle.css': ('home-mobile-reference.css', 'mobile-dock.css', 'wishlist.css'),
}


def bundle_content(sources):
    return b'\n'.join((ROOT / name).read_bytes() for name in sources) + b'\n'


def build():
    # Rebuild the active theme, not just the legacy static/css directory.
    source_bundles = {
        'style.min.css': ('fonts.css', 'style.css'),
        'header-base.min.css': ('menu-color.v20260909.css', 'site-logo-compact.v20260912.css', 'mobile-header-actions.v20260909.css'),
        'navigation.min.css': ('touch-navigation.css', 'wholesale-nav.css'),
        'storefront-extras.min.css': ('search-popup-compact.css', 'newsletter-gray.css'),
    }
    for name, sources in source_bundles.items():
        (ROOT / name).write_text(rcssmin.cssmin(bundle_content(sources).decode('utf-8')))
    for name, sources in BUNDLES.items():
        content = bundle_content(sources).decode('utf-8')
        (ROOT / name).write_text(minify_css(content))
    (ROOT / "bera-home.min.css").write_text(minify_css((ROOT / "bera-home.css").read_text()))
    build_critical_css()
    popup = (ROOT / "bera-offer-popup.css").read_text()
    if "url(" in popup or "{%" in popup or "</style" in popup.lower():
        raise ValueError("Popup CSS needs explicit URL/template escaping")
    (CRITICAL_TEMPLATE.parent / "home_popup_css.html").write_text(
        '<style id="home-popup-css">' + minify_css(popup) + '</style>\n')


def build_critical_css():
    """Split a contiguous prefix, preserving every rule and its cascade order."""
    import re
    core = minify_css(bundle_content(BUNDLES['home-core.bundle.css']).decode('utf-8'))
    product_start = core.index('.product-card{')
    cut = core.rfind('}', 0, product_start) + 1
    critical, remaining = core[:cut], core[cut:]
    if critical.count('{') != critical.count('}'):
        raise ValueError('Critical CSS split must end outside a media/keyframe block')
    # Inline CSS resolves URLs against HTML, rather than the CSS directory.
    def static_url(match):
        path = match.group(1)
        if not path.startswith('../fonts/'):
            raise ValueError('Unexpected critical CSS URL: ' + path)
        return "url('{% static 'bera_reference/" + path[3:] + "' %}')"
    critical = re.sub(r"url\(['\"]?([^)'\"]+)['\"]?\)", static_url, critical)
    CRITICAL_TEMPLATE.write_text('{% load static %}\n<style id="home-critical-css">' + critical + '</style>\n')
    (ROOT / 'home-core-rest.bundle.css').write_text(remaining)


if __name__ == '__main__':
    build()
