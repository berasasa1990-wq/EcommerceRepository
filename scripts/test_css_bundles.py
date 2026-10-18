import gzip
import unittest
import rcssmin
from minify_assets import STATIC, CSS_BUNDLES
from css_optimize import public_styles


class CssBundleTests(unittest.TestCase):
    def test_order_and_compressed_content_match_sources(self):
        bundles = {**CSS_BUNDLES, 'storefront-core.min.css': ['style.min.css', 'header-base.min.css']}
        for output, sources in bundles.items():
            with self.subTest(bundle=output):
                path = STATIC / 'css' / output
                expected = rcssmin.cssmin('\n'.join((STATIC / 'css' / source).read_text() for source in sources)).encode()
                self.assertEqual(path.read_bytes(), expected)
                self.assertEqual(gzip.decompress(path.with_suffix('.css.gz').read_bytes()), expected)

    def test_public_variants_match_the_safe_filter(self):
        for bundle in ['storefront-core', 'storefront-theme']:
            path = STATIC / 'css' / (bundle + '-public.min.css')
            source = STATIC / 'css' / (bundle + '.min.css')
            self.assertEqual(path.read_text(), public_styles(source.read_text()))
            self.assertEqual(gzip.decompress(path.with_suffix('.css.gz').read_bytes()), path.read_bytes())


if __name__ == '__main__':
    unittest.main()
