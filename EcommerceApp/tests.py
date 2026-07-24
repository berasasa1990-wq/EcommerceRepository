from decimal import Decimal

from django.test import SimpleTestCase, TestCase

from .models import Product, ProductVariation
from .pricing import _loyalty_osnovica_iz_korpe
from .product_search import (
    expand_token,
    normalize_text,
    term_matches_text,
    concept_groups_for_query,
    build_search_q,
)


class LoyaltyCouponPricingTests(SimpleTestCase):
    def test_loyalty_excludes_discounted_items(self):
        cart_items = [
            {
                'cijena': '20.00',
                'bazna_cijena': '25.00',
                'quantity': 2,
                'na_akciji': True,
                'cijena_decimal': Decimal('20.00'),
                'bazna_cijena_decimal': Decimal('25.00'),
            },
            {
                'cijena': '30.00',
                'bazna_cijena': '30.00',
                'quantity': 1,
                'na_akciji': False,
                'cijena_decimal': Decimal('30.00'),
                'bazna_cijena_decimal': Decimal('30.00'),
            },
        ]
        self.assertEqual(_loyalty_osnovica_iz_korpe(cart_items), Decimal('30.00'))

    def test_loyalty_counts_only_full_price_units_in_deal(self):
        cart_items = [
            {
                'cijena': '10.00',
                'bazna_cijena': '10.00',
                'quantity': 3,
                'na_akciji': False,
                'cijena_decimal': Decimal('10.00'),
                'bazna_cijena_decimal': Decimal('10.00'),
                'deal_info': {
                    'has_discount': True,
                    'full_price_count': 2,
                    'discounted_count': 1,
                },
            },
        ]
        self.assertEqual(_loyalty_osnovica_iz_korpe(cart_items), Decimal('20.00'))


class ProductSmartSearchTests(SimpleTestCase):
    """Pametna pretraga: sinonimi, dijakritici, soft word-boundary."""

    def test_diacritics_normalize(self):
        self.assertEqual(normalize_text('Štap'), 'stap')
        self.assertEqual(normalize_text('mašinica'), 'masinica')

    def test_rod_synonyms(self):
        for word in ('prut', 'motka', 'štap', 'stap', 'rod'):
            group = expand_token(word)
            self.assertIn('stap', group)
            self.assertIn('rod', group)

    def test_reel_synonyms(self):
        for word in ('rola', 'mašinica', 'masina', 'reel'):
            group = expand_token(word)
            self.assertIn('masinica', group)
            self.assertIn('reel', group)

    def test_carp_does_not_match_carpologija(self):
        self.assertTrue(term_matches_text('carp', 'CLINE BLACK CARP 3.6M stap'))
        self.assertFalse(term_matches_text('carp', 'Carpologija Lure Box'))

    def test_stem_matches_category_plural(self):
        self.assertTrue(term_matches_text('stap', 'saranski stapovi'))

    def test_multi_concept_groups(self):
        groups = concept_groups_for_query('šaran štap')
        self.assertEqual(len(groups), 2)
        flat = set().union(*groups)
        self.assertIn('carp', flat)
        self.assertIn('rod', flat)

    def test_build_search_q_not_empty(self):
        q = build_search_q('prut', use_llm=False)
        self.assertTrue(bool(q))


class ProductPakovanjeKatalogHintTests(TestCase):
    """Pretraga/katalog: ista količina → Cijena za N; različite → Cijena na pakovanje."""

    def _product(self, **kwargs):
        defaults = {
            'naziv': 'Test pakovanje',
            'slug': 'test-pakovanje-hint',
            'cijena': Decimal('9.99'),
            'aktivan': True,
            'na_stanju': True,
        }
        defaults.update(kwargs)
        return Product.objects.create(**defaults)

    def test_product_level_pack_without_variations(self):
        product = self._product(pakovanje_komada=10)
        self.assertTrue(product.je_pakovanje)
        self.assertEqual(product.pakovanje_cijena_hint, 'Cijena za 10 kom.')
        self.assertEqual(product.pakovanje_label, 'Pakovanje 10 kom.')

    def test_all_variations_same_pack_via_product(self):
        product = self._product(pakovanje_komada=10, slug='pack-same-inherit')
        ProductVariation.objects.create(artikal=product, naziv='A', redoslijed=1)
        ProductVariation.objects.create(artikal=product, naziv='B', redoslijed=2)
        product = Product.objects.get(pk=product.pk)
        self.assertEqual(product.pakovanje_jedinstvena_kolicina, 10)
        self.assertEqual(product.pakovanje_cijena_hint, 'Cijena za 10 kom.')

    def test_variations_different_pack_sizes(self):
        product = self._product(slug='pack-diff', pakovanje_komada=None)
        ProductVariation.objects.create(
            artikal=product, naziv='A', redoslijed=1, pakovanje_komada=10,
        )
        ProductVariation.objects.create(
            artikal=product, naziv='B', redoslijed=2, pakovanje_komada=20,
        )
        product = Product.objects.get(pk=product.pk)
        self.assertTrue(product.je_pakovanje)
        self.assertEqual(product.pakovanje_cijena_hint, 'Cijena na pakovanje / ne na komad')
        self.assertEqual(product.pakovanje_label, 'Pakovanje')

    def test_variations_same_override_ignores_product_field(self):
        product = self._product(slug='pack-override', pakovanje_komada=9)
        ProductVariation.objects.create(
            artikal=product, naziv='A', redoslijed=1, pakovanje_komada=5,
        )
        ProductVariation.objects.create(
            artikal=product, naziv='B', redoslijed=2, pakovanje_komada=5,
        )
        product = Product.objects.get(pk=product.pk)
        self.assertEqual(product.pakovanje_cijena_hint, 'Cijena za 5 kom.')
        # Fallback polje artikla ostaje netaknuto za varijacije bez override-a
        self.assertEqual(product.pakovanje_komada_prikaz, 9)


class PonudaAkcijaTests(TestCase):
    """+ Ponuda: popup after add-to-cart, optional % discount."""

    def setUp(self):
        from .models import Akcija
        self.trigger = Product.objects.create(
            naziv='Trigger artikal',
            slug='trigger-ponuda',
            cijena=Decimal('20.00'),
            aktivan=True,
            na_stanju=True,
        )
        self.offer = Product.objects.create(
            naziv='Ponuda artikal',
            slug='offer-ponuda',
            cijena=Decimal('10.00'),
            aktivan=True,
            na_stanju=True,
        )
        self.Akcija = Akcija

    def test_offer_with_discount(self):
        from .gratis import (
            build_gratis_offer_response,
            get_active_gratis_akcija_for_product,
        )
        akcija = self.Akcija.objects.create(
            naziv='Test ponuda',
            tip=self.Akcija.Tip.PONUDA,
            artikal=self.trigger,
            gratis_artikal=self.offer,
            popust_postotak=Decimal('20'),
            aktivan=True,
        )
        found = get_active_gratis_akcija_for_product(self.trigger)
        self.assertEqual(found.pk, akcija.pk)
        payload = build_gratis_offer_response(akcija)
        self.assertIsNotNone(payload)
        self.assertTrue(payload['has_discount'])
        self.assertEqual(payload['pct'], '20')
        self.assertEqual(payload['discounted_price'], '8.00')

    def test_offer_without_discount_regular_price(self):
        from .gratis import build_gratis_offer_response, get_active_gratis_akcija_for_product
        akcija = self.Akcija.objects.create(
            naziv='Test ponuda regular',
            tip=self.Akcija.Tip.PONUDA,
            artikal=self.trigger,
            gratis_artikal=self.offer,
            popust_postotak=None,
            aktivan=True,
        )
        found = get_active_gratis_akcija_for_product(self.trigger)
        self.assertEqual(found.pk, akcija.pk)
        payload = build_gratis_offer_response(akcija)
        self.assertIsNotNone(payload)
        self.assertFalse(payload['has_discount'])
        self.assertEqual(payload['original_price'], '10.00')
        self.assertEqual(payload['discounted_price'], '10.00')

    def _post_add(self, data):
        from django.contrib.auth.models import AnonymousUser
        from django.contrib.sessions.middleware import SessionMiddleware
        from django.test import RequestFactory
        from .views import add_to_cart

        factory = RequestFactory()
        request = factory.post(f'/artikal/{self.trigger.slug}/dodaj/', data)
        SessionMiddleware(lambda r: None).process_request(request)
        request.session.save()
        request.user = AnonymousUser()
        return add_to_cart(request, self.trigger.slug)

    def test_add_to_cart_requires_choice(self):
        self.Akcija.objects.create(
            naziv='Cart ponuda',
            tip=self.Akcija.Tip.PONUDA,
            artikal=self.trigger,
            gratis_artikal=self.offer,
            popust_postotak=Decimal('15'),
            aktivan=True,
        )
        resp = self._post_add({'quantity': '1', 'stay': '1'})
        self.assertEqual(resp.status_code, 200)
        import json
        data = json.loads(resp.content)
        self.assertTrue(data.get('ok'))
        self.assertTrue(data.get('requires_gratis_choice'))
        self.assertIn('gratis_offer', data)
        self.assertEqual(data['gratis_offer']['gratis_naziv'], 'Ponuda artikal')

    def test_accept_adds_both_products(self):
        akcija = self.Akcija.objects.create(
            naziv='Accept ponuda',
            tip=self.Akcija.Tip.PONUDA,
            artikal=self.trigger,
            gratis_artikal=self.offer,
            popust_postotak=Decimal('50'),
            aktivan=True,
        )
        resp = self._post_add({
            'quantity': '1',
            'stay': '1',
            'gratis_choice': 'yes',
            'gratis_akcija_id': str(akcija.pk),
        })
        self.assertEqual(resp.status_code, 200)
        import json
        data = json.loads(resp.content)
        self.assertTrue(data.get('ok'))
        self.assertEqual(data.get('cart_count'), 2)
        self.assertIn('Ponuda artikal', data.get('message', ''))

    def test_accept_with_offer_quantity(self):
        """gratis_quantity povećava samo količinu ponuđenog artikla."""
        from django.contrib.auth.models import AnonymousUser
        from django.contrib.sessions.middleware import SessionMiddleware
        from django.test import RequestFactory
        from .cart import Cart
        from .views import add_to_cart
        import json

        akcija = self.Akcija.objects.create(
            naziv='Qty ponuda',
            tip=self.Akcija.Tip.PONUDA,
            artikal=self.trigger,
            gratis_artikal=self.offer,
            popust_postotak=Decimal('10'),
            aktivan=True,
        )
        factory = RequestFactory()
        request = factory.post(
            f'/artikal/{self.trigger.slug}/dodaj/',
            {
                'quantity': '1',
                'stay': '1',
                'gratis_choice': 'yes',
                'gratis_akcija_id': str(akcija.pk),
                'gratis_quantity': '3',
            },
        )
        SessionMiddleware(lambda r: None).process_request(request)
        request.session.save()
        request.user = AnonymousUser()
        resp = add_to_cart(request, self.trigger.slug)
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.content)
        self.assertTrue(data.get('ok'))
        cart = Cart(request)
        items = list(cart)
        offer_items = [i for i in items if i.get('product_id') == self.offer.pk]
        self.assertEqual(len(offer_items), 1)
        self.assertEqual(offer_items[0]['quantity'], 3)

    def test_popup_every_add_while_active(self):
        """+ Ponuda iskače pri svakom dodavanju dok je akcija aktivna."""
        import json
        from django.contrib.auth.models import AnonymousUser
        from django.contrib.sessions.middleware import SessionMiddleware
        from django.test import RequestFactory
        from .views import add_to_cart

        akcija = self.Akcija.objects.create(
            naziv='Always on',
            tip=self.Akcija.Tip.PONUDA,
            artikal=self.trigger,
            gratis_artikal=self.offer,
            popust_postotak=Decimal('15'),
            aktivan=True,
        )
        # Prvi put — odbij (samo trigger)
        first = self._post_add({
            'quantity': '1',
            'stay': '1',
            'gratis_choice': 'no',
            'gratis_akcija_id': str(akcija.pk),
        })
        self.assertEqual(first.status_code, 200)
        self.assertTrue(json.loads(first.content).get('ok'))

        factory = RequestFactory()
        # Drugi put — opet traži DA/NE
        request = factory.post(
            f'/artikal/{self.trigger.slug}/dodaj/',
            {'quantity': '1', 'stay': '1'},
        )
        SessionMiddleware(lambda r: None).process_request(request)
        request.session.save()
        request.user = AnonymousUser()
        mid = add_to_cart(request, self.trigger.slug)
        mid_data = json.loads(mid.content)
        self.assertTrue(mid_data.get('ok'))
        self.assertTrue(mid_data.get('requires_gratis_choice'))

        # Treći put DA — dodaje se ponovo
        request2 = factory.post(
            f'/artikal/{self.trigger.slug}/dodaj/',
            {
                'quantity': '1',
                'stay': '1',
                'gratis_choice': 'yes',
                'gratis_akcija_id': str(akcija.pk),
            },
        )
        SessionMiddleware(lambda r: None).process_request(request2)
        request2.session.save()
        request2.user = AnonymousUser()
        ans = add_to_cart(request2, self.trigger.slug)
        self.assertTrue(json.loads(ans.content).get('ok'))
        self.assertEqual(json.loads(ans.content).get('cart_count'), 2)

    def test_cart_mode_payload_label(self):
        from .gratis import build_gratis_offer_response
        akcija = self.Akcija.objects.create(
            naziv='Cart ponuda label',
            tip=self.Akcija.Tip.PONUDA,
            artikal=self.trigger,
            gratis_artikal=self.offer,
            popust_postotak=Decimal('10'),
            aktivan=True,
        )
        payload = build_gratis_offer_response(akcija)
        self.assertEqual(payload['mode'], 'cart')
        self.assertEqual(payload['gratis_slug'], 'offer-ponuda')
        self.assertEqual(payload['label'], 'Dobra kupovina')
        self.assertIn('10', payload['headline'])
