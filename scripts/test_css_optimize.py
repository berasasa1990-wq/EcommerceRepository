import unittest
from css_optimize import deduplicate_rules, public_styles, remove_unused_components


class UnusedComponentTests(unittest.TestCase):
    def test_removes_only_retired_alternatives_and_empty_media(self):
        css = '.fish-advisor,.product-card{color:red}@media(max-width:600px){.staff-dash{display:grid}}'
        self.assertEqual(remove_unused_components(css, ''), '.product-card{color:red}')

    def test_restores_styles_when_component_returns_in_dynamic_source(self):
        css = '.fish-advisor__panel{display:flex}'
        self.assertEqual(remove_unused_components(css, "element.className = 'fish-advisor' + suffix"), css)

    def test_keeps_functional_selectors_states_and_keyframes(self):
        css = 'button:not(.fish-advisor){color:red}:is(.staff-dash,.card){color:blue}.is-open{display:block}@keyframes pulse{to{opacity:1}}'
        self.assertEqual(remove_unused_components(css, ''), css)


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


class PublicCssTests(unittest.TestCase):
    def test_keeps_shared_selectors_in_mixed_rules(self):
        self.assertEqual(public_styles('.staff-page,.product-card{color:red}'), '.product-card{color:red}')

    def test_preserves_header_account_button_and_product_rules(self):
        css = '.account-btn{color:red}.product-detail{display:grid}.cart-item{color:green}'
        self.assertEqual(public_styles(css), css)

    def test_nested_media_preserves_storefront_rules(self):
        self.assertEqual(public_styles('@media(max-width:600px){.staff-page{color:red}.product-card{color:blue}}'), '@media(max-width:600px){.product-card{color:blue}}')

    def test_preserves_negation_and_shared_functional_selectors(self):
        css = 'button:not(.staff-page){color:red}:is(.staff-page,.product-card){color:blue}'
        self.assertEqual(public_styles(css), css)

    def test_preserves_fonts_keyframes_and_quoted_commas(self):
        css = '@font-face{font-family:x;src:url(x)}@keyframes pulse{to{opacity:1}}[title="a,b"]{color:red}'
        self.assertEqual(public_styles(css), css)


if __name__ == '__main__':
    unittest.main()
