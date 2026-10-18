import unittest
from css_optimize import deduplicate_rules


class CssOptimizationTests(unittest.TestCase):
    def test_keeps_last_rule_so_cascade_stays_identical(self):
        self.assertEqual(deduplicate_rules('.x{color:red}.x{color:blue}.x{color:red}'), '.x{color:blue}.x{color:red}')

    def test_media_contexts_are_independent(self):
        css = '.x{color:red}@media(max-width:600px){.x{color:red}.x{color:red}}'
        self.assertEqual(deduplicate_rules(css), '.x{color:red}@media(max-width:600px){.x{color:red}}')

    def test_preserves_quoted_braces_and_at_rules(self):
        css = '@charset "UTF-8";@font-face{font-family:x;src:url(x)}.x{content:"}"}.x{content:"}"}'
        self.assertEqual(deduplicate_rules(css), '@charset "UTF-8";@font-face{font-family:x;src:url(x)}.x{content:"}"}')

    def test_idempotent(self):
        css = '.x{color:red}.x{color:red}'
        self.assertEqual(deduplicate_rules(deduplicate_rules(css)), deduplicate_rules(css))


if __name__ == '__main__':
    unittest.main()
