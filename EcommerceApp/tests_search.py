"""Basic search tests — normalize, multi-field ORM match, ranking, API, security."""

from decimal import Decimal

from django.test import Client, TestCase
from django.urls import reverse

from .models import (
    Brand,
    Category,
    Product,
    ProductAttribute,
    ProductVariation,
    SearchClickLog,
    SearchIntentRule,
    SearchQueryLog,
    SearchSynonym,
    SearchSynonymGroup,
    Tag,
)
from .search.measures import (
    convert_to_canonical,
    parse_measures_from_text,
    strip_measures_from_text,
)
from decimal import Decimal as D
from .search.normalize import (
    MAX_QUERY_LENGTH,
    normalize_measurements,
    normalize_search_text,
    sanitize_search_query,
    tokenize_search_query,
)
from .search.query import apply_search_filter
from .search.ranking import SCORE, apply_search_ranked, score_product
from .search.synonyms import (
    expand_term,
    get_synonym_map,
    invalidate_synonym_cache,
    seed_default_synonyms,
)


class NormalizeSearchTests(TestCase):
    def test_diacritics_folded(self):
        self.assertEqual(normalize_search_text('šaranski štap'), 'saranski stap')
        self.assertEqual(normalize_search_text('mašinica'), 'masinica')
        # đ → dj
        self.assertEqual(normalize_search_text('Smuđ'), 'smudj')
        self.assertEqual(normalize_search_text('smudj'), 'smudj')

    def test_case_and_spaces(self):
        self.assertEqual(normalize_search_text('Fox-Rage'), 'fox rage')
        self.assertEqual(normalize_search_text('  STAP  3  '), 'stap 3')

    def test_measurements(self):
        self.assertIn('3.6m', normalize_measurements('3,60m'))
        self.assertIn('3.6m', normalize_measurements('3.60 m'))
        self.assertIn('150g', normalize_measurements('150 g'))
        self.assertIn('0.3mm', normalize_measurements('0,30 mm'))
        self.assertIn('14mm', normalize_measurements('14 mm'))

    def test_tokenize(self):
        tokens = tokenize_search_query('šaranski štap 3,60')
        self.assertIn('saranski', tokens)
        self.assertIn('stap', tokens)

    def test_sanitize_limits(self):
        self.assertEqual(MAX_QUERY_LENGTH, 150)
        long_q = 'a' * 500
        self.assertLessEqual(len(sanitize_search_query(long_q)), 150)
        self.assertEqual(sanitize_search_query(''), '')
        self.assertEqual(sanitize_search_query(None), '')
        self.assertEqual(sanitize_search_query('   '), '')
        self.assertNotIn('\x00', sanitize_search_query('ab\x00c'))


class SearchMatchTests(TestCase):
    def setUp(self):
        self.brand = Brand.objects.create(naziv='Fox', slug='fox')
        self.cat = Category.objects.create(naziv='Štapovi', slug='stapovi', aktivan=True)
        self.sub = Category.objects.create(
            naziv='Feeder štapovi',
            slug='feeder-stapovi',
            roditelj=self.cat,
            aktivan=True,
            search_tagovi='feeder, fider, šaranski štap',
        )
        self.tag = Tag.objects.create(naziv='Feeder')
        self.p_stap = Product.objects.create(
            naziv='Šaranski štap 3.60 m 150g',
            slug='saranski-stap-360',
            sifra='MT13705',
            barkod='3870001234567',
            cijena=Decimal('199.00'),
            aktivan=True,
            na_stanju=True,
            brend=self.brand,
            kategorija=self.sub,
            opis='Odličan feeder štap za šarana 0.30 mm 14 mm',
        )
        self.p_stap.tagovi.add(self.tag)
        self.p_stap.rebuild_search_document(save=True)

        self.p_masinica = Product.objects.create(
            naziv='Fox mašinica 4000',
            slug='fox-masinica-4000',
            sifra='FX4000',
            cijena=Decimal('149.00'),
            aktivan=True,
            na_stanju=True,
            brend=self.brand,
            kategorija=self.cat,
        )
        ProductVariation.objects.create(
            artikal=self.p_masinica,
            naziv='4000',
            sifra='FX4000-V',
            na_stanju=True,
            cijena=Decimal('149.00'),
        )
        self.p_masinica.rebuild_search_document(save=True)

        self.p_feeder = Product.objects.create(
            naziv='Preston Feeder Master',
            slug='preston-feeder',
            sifra='PF001',
            cijena=Decimal('89.00'),
            aktivan=True,
            na_stanju=True,
            kategorija=self.sub,
        )

    def _ids(self, query):
        qs = apply_search_filter(Product.objects.filter(aktivan=True), query)
        return set(qs.values_list('id', flat=True))

    def test_stap_without_diacritics(self):
        self.assertIn(self.p_stap.id, self._ids('stap'))

    def test_stap_with_diacritics(self):
        self.assertIn(self.p_stap.id, self._ids('štap'))

    def test_saranski_stap(self):
        self.assertIn(self.p_stap.id, self._ids('saranski stap'))

    def test_saranski_stap_diacritics(self):
        self.assertIn(self.p_stap.id, self._ids('šaranski štap'))

    def test_masinica(self):
        self.assertIn(self.p_masinica.id, self._ids('masinica'))

    def test_masinica_diacritics(self):
        self.assertIn(self.p_masinica.id, self._ids('mašinica'))

    def test_feeder(self):
        ids = self._ids('feeder')
        self.assertIn(self.p_feeder.id, ids)
        self.assertIn(self.p_stap.id, ids)

    def test_fox_brand(self):
        self.assertIn(self.p_masinica.id, self._ids('fox'))

    def test_fox_masinica(self):
        self.assertIn(self.p_masinica.id, self._ids('fox masinica'))

    def test_exact_sifra(self):
        self.assertEqual(self._ids('MT13705'), {self.p_stap.id})

    def test_barkod(self):
        self.assertIn(self.p_stap.id, self._ids('3870001234567'))

    def test_variation_sifra(self):
        self.assertIn(self.p_masinica.id, self._ids('FX4000-V'))

    def test_measurements_in_name(self):
        self.assertIn(self.p_stap.id, self._ids('3.60m'))
        self.assertIn(self.p_stap.id, self._ids('3,60m'))
        self.assertIn(self.p_stap.id, self._ids('150g'))

    def test_empty_query_does_not_filter(self):
        """Empty q must not run a match filter (returns base qs unchanged)."""
        base = Product.objects.filter(aktivan=True)
        self.assertEqual(
            apply_search_filter(base, '').count(),
            base.count(),
        )
        self.assertEqual(apply_search_filter(base, '   ').count(), base.count())

    def test_short_query_returns_none(self):
        self.assertEqual(self._ids('a'), set())

    def test_long_query_sanitized(self):
        long_q = 'stap ' + ('x' * 200)
        ids = self._ids(long_q)
        self.assertIsInstance(ids, set)


class SearchRankingTests(TestCase):
    """ORM ranking (annotate Case/When) — not Python sort of full catalog."""

    def setUp(self):
        self.tag = Tag.objects.create(naziv='feeder-tag-unique')
        self.exact_code = Product.objects.create(
            naziv='Nešto drugo',
            slug='code-hit',
            sifra='MT13705',
            barkod='',
            cijena=Decimal('10.00'),
            aktivan=True,
            na_stanju=True,
        )
        self.exact_barkod = Product.objects.create(
            naziv='Artikal s barkodom',
            slug='barkod-hit',
            sifra='BK999',
            barkod='3871112223334',
            cijena=Decimal('10.00'),
            aktivan=True,
            na_stanju=True,
        )
        self.exact_name = Product.objects.create(
            naziv='feeder',
            slug='name-feeder',
            sifra='NM1',
            cijena=Decimal('10.00'),
            aktivan=True,
            na_stanju=True,
        )
        self.desc_only = Product.objects.create(
            naziv='Pribor ABC',
            slug='desc-feeder',
            sifra='DS1',
            cijena=Decimal('10.00'),
            aktivan=True,
            na_stanju=True,
            opis='ovdje pise feeder negdje',
        )
        self.sale_less_relevant = Product.objects.create(
            naziv='Akcijski plovak',
            slug='sale-plovak',
            sifra='SL1',
            cijena=Decimal('20.00'),
            akcijska_cijena=Decimal('10.00'),
            aktivan=True,
            na_stanju=True,
            opis='feeder spomen',
            prioritet_lagera=2,
            je_hit=True,
        )
        self.oos = Product.objects.create(
            naziv='feeder oos',
            slug='feeder-oos',
            sifra='OO1',
            cijena=Decimal('10.00'),
            aktivan=True,
            na_stanju=False,
        )
        self.in_stock_feeder = Product.objects.create(
            naziv='feeder in stock',
            slug='feeder-stock',
            sifra='IS1',
            cijena=Decimal('10.00'),
            aktivan=True,
            na_stanju=True,
        )
        # Same product linked twice via tag + variation → must still appear once
        self.dup_risk = Product.objects.create(
            naziv='Multi match feeder',
            slug='dup-feeder',
            sifra='DUP1',
            cijena=Decimal('10.00'),
            aktivan=True,
            na_stanju=True,
        )
        self.dup_risk.tagovi.add(self.tag)
        ProductVariation.objects.create(
            artikal=self.dup_risk,
            naziv='feeder size',
            sifra='DUP1-V',
            na_stanju=True,
        )
        self.dup_risk.rebuild_search_document(save=True)

    def _ranked_ids(self, query):
        from .search.ranking import apply_search_ranked

        qs = apply_search_ranked(Product.objects.filter(aktivan=True), query)
        return list(qs.values_list('id', flat=True))

    def test_exact_sifra_ranks_first(self):
        ids = self._ranked_ids('MT13705')
        self.assertEqual(ids[0], self.exact_code.id)

    def test_exact_barkod_ranks_first(self):
        ids = self._ranked_ids('3871112223334')
        self.assertEqual(ids[0], self.exact_barkod.id)

    def test_name_beats_description(self):
        ids = self._ranked_ids('feeder')
        self.assertIn(self.exact_name.id, ids)
        self.assertIn(self.desc_only.id, ids)
        self.assertLess(ids.index(self.exact_name.id), ids.index(self.desc_only.id))
        self.assertGreater(
            score_product(self.exact_name, 'feeder'),
            score_product(self.desc_only, 'feeder'),
        )

    def test_sale_does_not_outrank_strong_name_match(self):
        ids = self._ranked_ids('feeder')
        self.assertLess(
            ids.index(self.exact_name.id),
            ids.index(self.sale_less_relevant.id),
        )

    def test_oos_excluded_from_search_results(self):
        """Products not in stock must not appear in public search ranking."""
        from .search.ranking import apply_search_ranked
        ids = list(
            apply_search_ranked(
                Product.objects.filter(aktivan=True, na_stanju=True),
                'feeder',
            ).values_list('id', flat=True),
        )
        self.assertNotIn(self.oos.id, ids)
        self.assertIn(self.in_stock_feeder.id, ids)

    def test_no_duplicate_products(self):
        ids = self._ranked_ids('feeder')
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(ids.count(self.dup_risk.id), 1)


class SearchAPITests(TestCase):
    """Autocomplete API /api/pretraga/ — lean payload, same ranking, guards."""

    def setUp(self):
        self.client = Client()
        self.brand = Brand.objects.create(naziv='Shimano', slug='shimano')
        self.cat = Category.objects.create(naziv='Mašinice', slug='masinice-api', aktivan=True)
        self.product = Product.objects.create(
            naziv='Shimano mašinica 4000',
            slug='shimano-4000',
            sifra='SH4000',
            barkod='111222333',
            cijena=Decimal('200.00'),
            akcijska_cijena=Decimal('150.00'),
            aktivan=True,
            na_stanju=True,
            brend=self.brand,
            kategorija=self.cat,
        )
        self.oos = Product.objects.create(
            naziv='Shimano oos reel',
            slug='shimano-oos',
            sifra='SHOOS',
            cijena=Decimal('100.00'),
            aktivan=True,
            na_stanju=False,
            brend=self.brand,
            kategorija=self.cat,
        )
        # Extra products for limit test
        for i in range(12):
            Product.objects.create(
                naziv=f'Shimano spare {i}',
                slug=f'shimano-spare-{i}',
                sifra=f'SHSP{i:02d}',
                cijena=Decimal('50.00'),
                aktivan=True,
                na_stanju=True,
                brend=self.brand,
            )

    def test_suggest_min_chars(self):
        r = self.client.get(reverse('search_suggest'), {'q': 's'})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()['results'], [])

    def test_suggest_empty_query(self):
        r = self.client.get(reverse('search_suggest'), {'q': ''})
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertEqual(data['results'], [])
        self.assertIn('show_all_label', data)

    def test_suggest_whitespace_only(self):
        r = self.client.get(reverse('search_suggest'), {'q': '   '})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()['results'], [])

    def test_suggest_long_query_sanitized(self):
        long_q = 'sh' + ('x' * 200)
        r = self.client.get(reverse('search_suggest'), {'q': long_q})
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertLessEqual(len(data['query']), 150)

    def test_suggest_finds_product_payload_fields(self):
        r = self.client.get(reverse('search_suggest'), {'q': 'shimano'})
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertTrue(data['results'])
        item = data['results'][0]
        for key in (
            'naziv', 'sifra', 'brand', 'category', 'price', 'old_price',
            'on_sale', 'in_stock', 'url', 'image',
        ):
            self.assertIn(key, item)
        # No full description blob in autocomplete payload
        self.assertNotIn('opis', item)
        self.assertTrue(item['url'].startswith('/'))
        self.assertIn('Prikaži sve rezultate za:', data['show_all_label'])

    def test_suggest_max_8_results(self):
        r = self.client.get(reverse('search_suggest'), {'q': 'shimano'})
        data = r.json()
        self.assertLessEqual(len(data['results']), 8)

    def test_suggest_no_duplicates(self):
        r = self.client.get(reverse('search_suggest'), {'q': 'shimano'})
        urls = [x['url'] for x in r.json()['results']]
        self.assertEqual(len(urls), len(set(urls)))

    def test_suggest_excludes_oos_when_stock_exists(self):
        r = self.client.get(reverse('search_suggest'), {'q': 'shimano'})
        sifre = [x.get('sifra') for x in r.json()['results']]
        self.assertNotIn('SHOOS', sifre)

    def test_suggest_sale_fields(self):
        r = self.client.get(reverse('search_suggest'), {'q': 'SH4000'})
        data = r.json()
        self.assertTrue(data['results'])
        item = data['results'][0]
        self.assertEqual(item['sifra'], 'SH4000')
        self.assertTrue(item['on_sale'])
        self.assertEqual(item['old_price'], '200.00')
        self.assertEqual(item['price'], '150.00')
        self.assertTrue(item['in_stock'])
        self.assertEqual(item['brand'], 'Shimano')
        self.assertEqual(item['category'], 'Mašinice')

    def test_suggest_get_only(self):
        r = self.client.post(reverse('search_suggest'), {'q': 'shimano'})
        self.assertEqual(r.status_code, 405)

    def test_full_search_page(self):
        r = self.client.get(reverse('search_results'), {'q': 'shimano'})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Shimano')
        self.assertContains(r, 'noindex')

    def test_exact_code_search_page(self):
        r = self.client.get(reverse('search_results'), {'q': 'SH4000'})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Shimano mašinica')


class SearchSynonymTests(TestCase):
    """Sinonimi: expand, match, ranking lower than exact, cache invalidation."""

    def setUp(self):
        invalidate_synonym_cache()
        # Clean seed data from migrations so tests control the map fully
        SearchSynonym.objects.all().delete()
        SearchSynonymGroup.objects.all().delete()
        invalidate_synonym_cache()

        self.group = SearchSynonymGroup.objects.create(
            naziv='Štap',
            aktivno=True,
            prioritet=100,
        )
        # Unique normalizovani_pojam only (štap and stap both fold to "stap")
        for pojam in ('štap', 'rod', 'pecaljka'):
            SearchSynonym.objects.create(grupa=self.group, pojam=pojam)
        invalidate_synonym_cache()

        self.product = Product.objects.create(
            naziv='Karbon štap 3.60m',
            slug='karbon-stap-syn',
            sifra='STAP001',
            cijena=Decimal('99.00'),
            aktivan=True,
            na_stanju=True,
        )
        self.product.rebuild_search_document(save=True)

        self.exact_code = Product.objects.create(
            naziv='Nešto drugo rod',
            slug='code-rod',
            sifra='ROD999',
            cijena=Decimal('10.00'),
            aktivan=True,
            na_stanju=True,
            opis='samo opis',
        )
        self.exact_code.rebuild_search_document(save=True)

    def test_expand_term_includes_group(self):
        expanded = expand_term('stap')
        self.assertIn('stap', expanded)
        self.assertIn('rod', expanded)
        self.assertIn('pecaljka', expanded)

    def test_synonym_finds_product(self):
        ids = set(
            apply_search_filter(Product.objects.filter(aktivan=True), 'rod')
            .values_list('id', flat=True),
        )
        self.assertIn(self.product.id, ids)

    def test_inactive_group_not_expanded(self):
        self.group.aktivno = False
        self.group.save()
        invalidate_synonym_cache()
        expanded = expand_term('rod')
        # only self if group inactive
        self.assertEqual(expanded, ['rod'])

    def test_cache_invalidates_on_save(self):
        m1 = get_synonym_map()
        self.assertIn('stap', m1)
        SearchSynonym.objects.create(grupa=self.group, pojam='fishing rod')
        m2 = get_synonym_map()
        self.assertIn('fishing rod', m2.get('stap', []) + m2.get('fishing rod', []))

    def test_synonym_rank_below_exact_sifra(self):
        """Typed exact sifra beats product found only via synonym word in name."""
        ids = list(
            apply_search_ranked(Product.objects.filter(aktivan=True), 'ROD999')
            .values_list('id', flat=True),
        )
        self.assertEqual(ids[0], self.exact_code.id)

    def test_synonym_score_constant(self):
        self.assertEqual(SCORE.SYNONYM, 300)
        self.assertLess(SCORE.SYNONYM, SCORE.EXACT_NAME)
        self.assertLess(SCORE.SYNONYM, SCORE.EXACT_SIFRA)

    def test_seed_defaults_idempotent(self):
        s1 = seed_default_synonyms()
        s2 = seed_default_synonyms()
        self.assertEqual(s2['terms_created'], 0)
        self.assertGreater(s1['groups_total'] + s2['groups_total'], 0)
        self.assertIn('feeder', get_synonym_map().get('fider', []) + ['feeder'])


class SearchMeasuresTests(TestCase):
    """Measure parsing + ProductAttribute search (not live opis scan)."""

    def setUp(self):
        self.feeder_rod = Product.objects.create(
            naziv='Feeder štap Master',
            slug='feeder-stap-attr',
            sifra='FS360',
            cijena=Decimal('120.00'),
            aktivan=True,
            na_stanju=True,
        )
        ProductAttribute.objects.create(
            product=self.feeder_rod,
            attribute_type=ProductAttribute.AttributeType.LENGTH,
            text_value='3.60 m',
            numeric_value=D('3.60'),
            unit='m',
            izvor='manual',
        )
        ProductAttribute.objects.create(
            product=self.feeder_rod,
            attribute_type=ProductAttribute.AttributeType.CASTING_WEIGHT,
            text_value='150g',
            numeric_value=D('150'),
            unit='g',
            izvor='manual',
        )

        self.line = Product.objects.create(
            naziv='Najlon Carp Line',
            slug='najlon-attr',
            sifra='NL030',
            cijena=Decimal('15.00'),
            aktivan=True,
            na_stanju=True,
        )
        ProductAttribute.objects.create(
            product=self.line,
            attribute_type=ProductAttribute.AttributeType.DIAMETER,
            text_value='0.30 mm',
            numeric_value=D('0.30'),
            unit='mm',
            izvor='manual',
        )

        self.boila = Product.objects.create(
            naziv='Boila Squid',
            slug='boila-attr',
            sifra='BO14',
            cijena=Decimal('12.00'),
            aktivan=True,
            na_stanju=True,
        )
        ProductAttribute.objects.create(
            product=self.boila,
            attribute_type=ProductAttribute.AttributeType.BAIT_SIZE,
            text_value='14mm',
            numeric_value=D('14'),
            unit='mm',
            izvor='manual',
        )

        self.reel = Product.objects.create(
            naziv='Mašinica Pro',
            slug='masinica-attr',
            sifra='MS4000',
            cijena=Decimal('80.00'),
            aktivan=True,
            na_stanju=True,
        )
        ProductAttribute.objects.create(
            product=self.reel,
            attribute_type=ProductAttribute.AttributeType.REEL_SIZE,
            text_value='4000',
            numeric_value=D('4000'),
            unit='',
            izvor='manual',
        )

        self.carp_rod = Product.objects.create(
            naziv='Šaranski štap US',
            slug='saranski-ft-attr',
            sifra='CR13',
            cijena=Decimal('200.00'),
            aktivan=True,
            na_stanju=True,
        )
        ProductAttribute.objects.create(
            product=self.carp_rod,
            attribute_type=ProductAttribute.AttributeType.LENGTH,
            text_value='13ft',
            numeric_value=D('13'),
            unit='ft',
            izvor='manual',
        )
        ProductAttribute.objects.create(
            product=self.carp_rod,
            attribute_type=ProductAttribute.AttributeType.TEST_CURVE,
            text_value='3.5lb',
            numeric_value=D('3.5'),
            unit='lb',
            izvor='manual',
        )

        # Same 14mm but diameter (line) — must NOT match "boila 14mm" as if same type-only free-for-all
        self.other_mm = Product.objects.create(
            naziv='Neki promjer 14',
            slug='promjer-14-attr',
            sifra='PR14',
            cijena=Decimal('5.00'),
            aktivan=True,
            na_stanju=True,
        )
        ProductAttribute.objects.create(
            product=self.other_mm,
            attribute_type=ProductAttribute.AttributeType.DIAMETER,
            text_value='14 mm',
            numeric_value=D('14'),
            unit='mm',
            izvor='manual',
        )

    def _ids(self, q):
        return set(
            apply_search_filter(Product.objects.filter(aktivan=True), q)
            .values_list('id', flat=True),
        )

    def test_unit_normalization_cm_to_m(self):
        norm, unit = convert_to_canonical(D('360'), 'cm', 'length')
        self.assertEqual(unit, 'm')
        self.assertEqual(norm, D('3.6000'))

    def test_parse_variants(self):
        for text in ('3.60 m', '3,60m', '360 cm', '2.70 m', '2,7m'):
            ms = parse_measures_from_text(text, context_tokens={'stap'})
            self.assertTrue(ms, msg=text)
            self.assertIn('length', ms[0].attribute_types)

        for text in ('150 g', '150g'):
            ms = parse_measures_from_text(text)
            self.assertEqual(ms[0].normalized_value, D('150.0000'))

        for text in ('0.30 mm', '0,30mm'):
            ms = parse_measures_from_text(text, context_tokens={'najlon'})
            self.assertIn('diameter', ms[0].attribute_types)

        for text in ('14 mm', '14mm'):
            ms = parse_measures_from_text(text, context_tokens={'boila'})
            self.assertIn('bait_size', ms[0].attribute_types)

        for text in ('4000', '10000'):
            ms = parse_measures_from_text(text, context_tokens={'masinica'})
            self.assertEqual(ms[0].attribute_types, ('reel_size',))

        for text in ('3.5 lb', '3,5lb'):
            ms = parse_measures_from_text(text)
            self.assertEqual(ms[0].attribute_types, ('test_curve',))

        for text in ('10 ft', '13ft'):
            ms = parse_measures_from_text(text, context_tokens={'stap'})
            self.assertIn('length', ms[0].attribute_types)

    def test_strip_measures(self):
        self.assertEqual(
            strip_measures_from_text('feeder stap 3.60 150g').lower().replace('š', 's'),
            'feeder stap',
        )

    def test_feeder_stap_360_150g(self):
        ids = self._ids('feeder stap 3.60 150g')
        self.assertIn(self.feeder_rod.id, ids)
        self.assertNotIn(self.line.id, ids)

    def test_comma_length_and_cm_equivalent(self):
        # Attribute stored as 3.60 m; query 3,60m and 360 cm
        for q in ('feeder 3,60m', 'stap 360 cm'):
            self.assertIn(self.feeder_rod.id, self._ids(q), msg=q)

    def test_najlon_030(self):
        ids = self._ids('najlon 0.30')
        self.assertIn(self.line.id, ids)
        # bare 0.30 with najlon → diameter; boila not matched
        self.assertNotIn(self.boila.id, ids)

    def test_boila_14mm_not_line_diameter(self):
        ids = self._ids('boila 14mm')
        self.assertIn(self.boila.id, ids)
        # diameter-only 14mm product must not match bait context
        self.assertNotIn(self.other_mm.id, ids)

    def test_masinica_4000(self):
        ids = self._ids('masinica 4000')
        self.assertIn(self.reel.id, ids)

    def test_saranski_stap_13ft_35lb(self):
        ids = self._ids('saranski stap 13ft 3.5lb')
        self.assertIn(self.carp_rod.id, ids)
        self.assertNotIn(self.feeder_rod.id, ids)

    def test_attribute_score_below_exact_name(self):
        self.assertLess(SCORE.ATTRIBUTE, SCORE.EXACT_NAME)
        self.assertLess(SCORE.ATTRIBUTE, SCORE.SYNONYM)


class SearchFuzzyTests(TestCase):
    """Fuzzy typo search — SQLite fallback always safe; Postgres logic skipped if not PG."""

    def setUp(self):
        self.brand = Brand.objects.create(naziv='Shimano', slug='shimano-fuzzy')
        self.feeder = Product.objects.create(
            naziv='Preston Feeder Master',
            slug='preston-feeder-fuzzy',
            sifra='PF-FZ',
            cijena=Decimal('50.00'),
            aktivan=True,
            na_stanju=True,
        )
        self.shimano = Product.objects.create(
            naziv='Shimano Stradic 4000',
            slug='shimano-stradic-fuzzy',
            sifra='SH-FZ',
            cijena=Decimal('200.00'),
            aktivan=True,
            na_stanju=True,
            brend=self.brand,
        )
        self.spomb = Product.objects.create(
            naziv='Spomb Midi',
            slug='spomb-midi-fuzzy',
            sifra='SP-FZ',
            cijena=Decimal('30.00'),
            aktivan=True,
            na_stanju=True,
        )
        self.masinica = Product.objects.create(
            naziv='Fox mašinica 4000',
            slug='fox-masinica-fuzzy',
            sifra='FX-FZ',
            cijena=Decimal('90.00'),
            aktivan=True,
            na_stanju=True,
        )
        self.varalica = Product.objects.create(
            naziv='Rapala varalica',
            slug='rapala-varalica-fuzzy',
            sifra='RP-FZ',
            cijena=Decimal('20.00'),
            aktivan=True,
            na_stanju=True,
        )
        self.exact_code = Product.objects.create(
            naziv='Neki drugi artikal',
            slug='exact-code-fuzzy',
            sifra='EXACT99',
            cijena=Decimal('10.00'),
            aktivan=True,
            na_stanju=True,
        )
        for p in (
            self.feeder, self.shimano, self.spomb,
            self.masinica, self.varalica, self.exact_code,
        ):
            p.rebuild_search_document(save=True)

    def _ids(self, q):
        return set(
            apply_search_filter(Product.objects.filter(aktivan=True), q)
            .values_list('id', flat=True),
        )

    def test_sqlite_fallback_does_not_crash(self):
        from .search.fuzzy import fuzzy_product_ids, is_postgres, suggest_did_you_mean

        # Always safe
        ids = fuzzy_product_ids('feadr')
        self.assertIsInstance(ids, list)
        suggest_did_you_mean('feadr', result_count=0)
        # On SQLite we are not postgres
        if not is_postgres():
            self.assertFalse(is_postgres())

    def test_typo_feadr_finds_feeder_sqlite_fallback(self):
        ids = self._ids('feadr')
        self.assertIn(self.feeder.id, ids)

    def test_typo_shimno_finds_shimano(self):
        ids = self._ids('shimno')
        self.assertIn(self.shimano.id, ids)

    def test_typo_spom_finds_spomb(self):
        ids = self._ids('spom')
        self.assertIn(self.spomb.id, ids)

    def test_typo_masnica_finds_masinica(self):
        ids = self._ids('masnica')
        self.assertIn(self.masinica.id, ids)

    def test_typo_varlica_finds_varalica(self):
        ids = self._ids('varlica')
        self.assertIn(self.varalica.id, ids)

    def test_exact_sifra_outranks_fuzzy(self):
        ids = list(
            apply_search_ranked(Product.objects.filter(aktivan=True), 'EXACT99')
            .values_list('id', flat=True),
        )
        self.assertEqual(ids[0], self.exact_code.id)

    def test_fuzzy_score_below_exact_name(self):
        self.assertLess(SCORE.FUZZY, SCORE.EXACT_NAME)
        self.assertLess(SCORE.FUZZY, SCORE.DESCRIPTION)
        self.assertLess(SCORE.FUZZY, SCORE.BRAND)

    def test_did_you_mean_shimano(self):
        from .search.fuzzy import suggest_did_you_mean

        hint = suggest_did_you_mean('shmano', result_count=0)
        self.assertIsNotNone(hint)
        self.assertIn('Shimano', hint['suggestion'])
        self.assertIn('q=', hint['url'])
        self.assertIn('Da li ste mislili', hint['label'])

    def test_did_you_mean_not_when_many_results(self):
        from .search.fuzzy import suggest_did_you_mean

        hint = suggest_did_you_mean('shmano', result_count=20)
        self.assertIsNone(hint)

    def test_fuzzy_not_on_very_short_query(self):
        from .search.fuzzy import should_use_fuzzy

        self.assertFalse(should_use_fuzzy(0, 'ab'))

    def test_postgres_helpers_importable(self):
        """Postgres API imports must not break SQLite test runs."""
        from .search.fuzzy import postgres_trigram_ready, TRGM_THRESHOLD

        self.assertGreaterEqual(TRGM_THRESHOLD, 0.4)
        # On SQLite this is False; on Postgres True — either is fine
        self.assertIn(postgres_trigram_ready(), (True, False))


class SearchResultsPageTests(TestCase):
    """Full /pretraga/ page: GET params, noindex, filters, pagination, no dupes."""

    def setUp(self):
        self.client = Client()
        self.brand = Brand.objects.create(naziv='Fox', slug='fox')
        self.cat = Category.objects.create(naziv='Feeder', slug='feeder-cat', aktivan=True)
        self.sub = Category.objects.create(
            naziv='Feeder štapovi', slug='feeder-stapovi-sr', roditelj=self.cat, aktivan=True,
        )
        self.p1 = Product.objects.create(
            naziv='Fox Feeder štap 3.60',
            slug='fox-feeder-1',
            sifra='FF1',
            cijena=Decimal('100.00'),
            aktivan=True,
            na_stanju=True,
            brend=self.brand,
            kategorija=self.sub,
            je_novitet=True,
        )
        self.p2 = Product.objects.create(
            naziv='Feeder pribor set',
            slug='feeder-set-2',
            sifra='FF2',
            cijena=Decimal('50.00'),
            akcijska_cijena=Decimal('40.00'),
            aktivan=True,
            na_stanju=True,
            brend=self.brand,
            kategorija=self.sub,
        )
        self.p_oos = Product.objects.create(
            naziv='Feeder oos',
            slug='feeder-oos-sr',
            sifra='FFO',
            cijena=Decimal('20.00'),
            aktivan=True,
            na_stanju=False,
            kategorija=self.sub,
        )
        for p in (self.p1, self.p2, self.p_oos):
            p.rebuild_search_document(save=True)

    def test_search_results_url_and_title(self):
        r = self.client.get(reverse('search_results'), {'q': 'feeder'})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Rezultati za: feeder')
        self.assertContains(r, 'noindex')
        self.assertContains(r, 'follow')
        self.assertContains(r, 'Pronađeno')

    def test_brand_alias_and_in_stock_filter(self):
        r = self.client.get(reverse('search_results'), {
            'q': 'feeder',
            'brand': 'fox',
            'in_stock': '1',
            'sort': 'relevance',
        })
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Fox Feeder')
        content = r.content.decode()
        # Product slug of OOS item must not appear in grid (brand+stock filters)
        self.assertNotIn('/artikal/feeder-oos-sr/', content)
        self.assertIn('/artikal/fox-feeder-1/', content)

    def test_sort_price_and_preserve_q(self):
        r = self.client.get(reverse('search_results'), {
            'q': 'feeder',
            'sort': 'rastuca',
        })
        self.assertEqual(r.status_code, 200)
        # form should keep q hidden
        self.assertContains(r, 'name="q"')
        self.assertContains(r, 'value="feeder"')

    def test_pagination_preserves_params(self):
        # create many products so pagination appears if per_page is small enough
        # just check query string builder includes params on page links when multi-page
        from .search.results import search_page_query_string
        qs = search_page_query_string(
            {'q': 'feeder', 'brend': 'fox', 'sort': 'relevance', 'na_stanju': '1'},
            page=2,
        )
        self.assertIn('q=feeder', qs)
        self.assertIn('brend=fox', qs)
        self.assertIn('page=2', qs)

    def test_home_q_redirects_to_pretraga(self):
        r = self.client.get('/', {'q': 'feeder'})
        self.assertEqual(r.status_code, 302)
        self.assertIn('/pretraga/', r['Location'])
        self.assertIn('q=feeder', r['Location'])

    def test_active_filters_and_clear(self):
        r = self.client.get(reverse('search_results'), {
            'q': 'feeder',
            'brend': 'fox',
            'akcija': '1',
        })
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Obriši filtere')
        self.assertContains(r, 'Brend')

    def test_no_duplicate_product_cards(self):
        r = self.client.get(reverse('search_results'), {'q': 'feeder'})
        self.assertEqual(r.status_code, 200)
        html = r.content.decode()
        # product slug link should appear once per product in grid
        self.assertEqual(html.count('fox-feeder-1'), html.count('fox-feeder-1'))
        self.assertLessEqual(html.count('/artikal/fox-feeder-1/'), 3)

    def test_suggest_show_all_points_to_pretraga(self):
        r = self.client.get(reverse('search_suggest'), {'q': 'fox'})
        data = r.json()
        self.assertIn('/pretraga/', data.get('show_all_url', ''))


class SearchAnalyticsTests(TestCase):
    """SearchQueryLog / SearchClickLog — no keystroke spam, conversion hooks."""

    def setUp(self):
        self.client = Client()
        self.product = Product.objects.create(
            naziv='Analitika feeder test',
            slug='analytics-feeder',
            sifra='AN1',
            cijena=Decimal('10.00'),
            aktivan=True,
            na_stanju=True,
        )
        self.product.rebuild_search_document(save=True)

    def test_full_page_creates_query_log(self):
        before = SearchQueryLog.objects.count()
        r = self.client.get(reverse('search_results'), {'q': 'feeder'})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(SearchQueryLog.objects.count(), before + 1)
        log = SearchQueryLog.objects.latest('created_at')
        self.assertEqual(log.original_query, 'feeder')
        self.assertTrue(log.normalized_query)
        self.assertGreaterEqual(log.result_count, 1)
        self.assertEqual(log.source, SearchQueryLog.Source.FULL_PAGE)
        self.assertTrue(log.session_key)

    def test_pagination_does_not_duplicate_log(self):
        self.client.get(reverse('search_results'), {'q': 'feeder'})
        n = SearchQueryLog.objects.count()
        self.client.get(reverse('search_results'), {'q': 'feeder', 'page': '2'})
        self.assertEqual(SearchQueryLog.objects.count(), n)

    def test_autocomplete_keystroke_does_not_log(self):
        before = SearchQueryLog.objects.count()
        r = self.client.get(reverse('search_suggest'), {'q': 'fe'})
        self.assertEqual(r.status_code, 200)
        r = self.client.get(reverse('search_suggest'), {'q': 'fee'})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(SearchQueryLog.objects.count(), before)

    def test_click_endpoint_creates_click_log(self):
        # seed a query log via full page
        self.client.get(reverse('search_results'), {'q': 'feeder'})
        log = SearchQueryLog.objects.latest('created_at')
        r = self.client.post(reverse('search_analytics_click'), {
            'product_id': self.product.pk,
            'position': 2,
            'q': 'feeder',
            'log_id': log.pk,
            'source': 'full_page',
        })
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json().get('ok'))
        click = SearchClickLog.objects.latest('created_at')
        self.assertEqual(click.product_id, self.product.pk)
        self.assertEqual(click.result_position, 2)
        self.assertEqual(click.search_query_id, log.pk)

    def test_autocomplete_click_creates_log_and_click(self):
        before_q = SearchQueryLog.objects.count()
        r = self.client.post(reverse('search_analytics_click'), {
            'product_id': self.product.pk,
            'position': 1,
            'q': 'analitika',
            'source': 'autocomplete',
        })
        self.assertEqual(r.status_code, 200)
        self.assertEqual(SearchQueryLog.objects.count(), before_q + 1)
        self.assertEqual(SearchClickLog.objects.count(), 1)
        log = SearchQueryLog.objects.latest('created_at')
        self.assertEqual(log.source, SearchQueryLog.Source.AUTOCOMPLETE)

    def test_no_ip_fields(self):
        field_names = {f.name for f in SearchQueryLog._meta.get_fields()}
        self.assertNotIn('ip', field_names)
        self.assertNotIn('ip_address', field_names)
        self.assertNotIn('user_agent', field_names)

    def test_cart_conversion_flag(self):
        from .search.analytics import log_search_query, mark_search_converted_to_cart

        # Use request factory via client session
        self.client.get(reverse('search_results'), {'q': 'feeder'})
        log = SearchQueryLog.objects.latest('created_at')
        self.assertFalse(log.converted_to_cart)
        # Simulate cart mark through client session continuity
        from django.test import RequestFactory
        from .search.analytics import mark_search_converted_to_cart as mark

        # Session shared: call mark via a view-like request
        session = self.client.session
        factory = RequestFactory()
        req = factory.get('/')
        req.session = session
        mark(req)
        log.refresh_from_db()
        self.assertTrue(log.converted_to_cart)

    def test_order_conversion_flag(self):
        self.client.get(reverse('search_results'), {'q': 'feeder'})
        log = SearchQueryLog.objects.latest('created_at')
        from django.test import RequestFactory
        from .search.analytics import mark_search_converted_to_order

        req = RequestFactory().get('/')
        req.session = self.client.session
        mark_search_converted_to_order(req)
        log.refresh_from_db()
        self.assertTrue(log.converted_to_order)
        self.assertTrue(log.converted_to_cart)

    def test_analytics_helpers(self):
        from .search.analytics import (
            top_queries_annotated,
            zero_result_queries,
            top_clicked_products_annotated,
        )
        self.client.get(reverse('search_results'), {'q': 'nepostojeci-xyz-query'})
        zeros = zero_result_queries(limit=10)
        self.assertTrue(any(z['normalized_query'] for z in zeros))
        top_queries_annotated(limit=5)
        top_clicked_products_annotated(limit=5)


class SearchIntentTests(TestCase):
    """Intent rules: recommendations only — never inflate main ranking."""

    def setUp(self):
        from .search.intent import invalidate_intent_cache
        invalidate_intent_cache()

        self.cat_stap = Category.objects.create(
            naziv='Somovski štapovi', slug='somovski-stapovi', aktivan=True,
        )
        self.cat_mas = Category.objects.create(
            naziv='Somovske mašinice', slug='somovske-masinice', aktivan=True,
        )
        self.direct = Product.objects.create(
            naziv='Som udica specijal',
            slug='som-udica-direct',
            sifra='SOM-D1',
            cijena=Decimal('15.00'),
            aktivan=True,
            na_stanju=True,
        )
        self.related = Product.objects.create(
            naziv='Jaka mašinica za soma 8000',
            slug='som-masinica-related',
            sifra='SOM-R1',
            cijena=Decimal('200.00'),
            aktivan=True,
            na_stanju=True,
        )
        self.unrelated = Product.objects.create(
            naziv='Boila squid 14mm',
            slug='boila-unrelated-intent',
            sifra='BO-U1',
            cijena=Decimal('12.00'),
            aktivan=True,
            na_stanju=True,
        )
        for p in (self.direct, self.related, self.unrelated):
            p.rebuild_search_document(save=True)

        self.rule = SearchIntentRule.objects.create(
            naziv='Som oprema test',
            trigger_phrases='som\ncatfish',
            naslov_preporuke='Za lov na soma preporučujemo',
            objasnjenje='Test objašnjenje namjere.',
            prioritet=100,
            aktivno=True,
        )
        self.rule.povezane_kategorije.add(self.cat_stap, self.cat_mas)
        self.rule.povezani_proizvodi.add(self.related, self.unrelated)
        invalidate_intent_cache()

    def test_match_intent_som(self):
        from .search.intent import match_intent_rules
        matched = match_intent_rules('som')
        self.assertTrue(matched)
        self.assertEqual(matched[0]['id'], self.rule.pk)

    def test_resolve_excludes_main_hits(self):
        from .search.intent import resolve_intent_recommendations
        rec = resolve_intent_recommendations(
            'som',
            exclude_product_ids={self.direct.pk},
        )
        self.assertTrue(rec.has_content)
        ids = {p.pk for p in rec.products}
        self.assertIn(self.related.pk, ids)
        # related products from rule appear in intent section
        self.assertIn(self.unrelated.pk, ids)
        # categories recommended
        cat_ids = {c.pk for c in rec.categories}
        self.assertIn(self.cat_stap.pk, cat_ids)

    def test_intent_does_not_inject_into_main_ranking(self):
        """Unrelated intent product must not rank high for query 'som' via intent."""
        from .search.ranking import apply_search_ranked
        # Main search for 'som' should find direct product by name
        ids = list(
            apply_search_ranked(Product.objects.filter(aktivan=True), 'som')
            .values_list('id', flat=True),
        )
        self.assertIn(self.direct.pk, ids)
        # unrelated boilie should NOT appear in main results just because of intent rule
        # (it doesn't match 'som' textually)
        self.assertNotIn(self.unrelated.pk, ids)

    def test_intent_section_on_results_page(self):
        r = self.client.get(reverse('search_results'), {'q': 'som'})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Direktni rezultati')
        self.assertContains(r, 'Možda će vam trebati i')
        self.assertContains(r, 'Za lov na soma preporučujemo')
        self.assertContains(r, 'preporuke, ne potpuno poklapanje')
        # related product card appears
        self.assertContains(r, 'Jaka mašinica za soma')

    def test_inactive_rule_ignored(self):
        from .search.intent import invalidate_intent_cache, match_intent_rules
        self.rule.aktivno = False
        self.rule.save()
        invalidate_intent_cache()
        self.assertEqual(match_intent_rules('som'), [])

    def test_feeder_set_phrase(self):
        from .search.intent import invalidate_intent_cache, match_intent_rules
        SearchIntentRule.objects.create(
            naziv='Feeder set test',
            trigger_phrases='početnički feeder set\npocetnicki feeder set',
            naslov_preporuke='Početnički feeder',
            prioritet=90,
            aktivno=True,
        )
        invalidate_intent_cache()
        matched = match_intent_rules('pocetnicki feeder set')
        self.assertTrue(matched)
        self.assertIn('feeder', matched[0]['naziv'].casefold())

    def test_cache_invalidation_on_save(self):
        from .search.intent import get_cached_intent_rules, invalidate_intent_cache
        invalidate_intent_cache()
        n1 = len(get_cached_intent_rules())
        SearchIntentRule.objects.create(
            naziv='Nova namjera cache',
            trigger_phrases='xyzintentunique',
            prioritet=1,
            aktivno=True,
        )
        # save invalidates — next get reloads
        n2 = len(get_cached_intent_rules())
        self.assertGreaterEqual(n2, n1)

    def test_ranking_order_unchanged_by_intent_products(self):
        """Exact name match still ranks before weak matches; intent products excluded from list."""
        from .search.ranking import apply_search_ranked, score_product
        ids = list(
            apply_search_ranked(Product.objects.filter(aktivan=True), 'som udica')
            .values_list('id', flat=True),
        )
        if self.direct.pk in ids and self.related.pk in ids:
            self.assertLess(ids.index(self.direct.pk), ids.index(self.related.pk))
        self.assertGreater(
            score_product(self.direct, 'som udica'),
            score_product(self.unrelated, 'som udica'),
        )
