from decimal import Decimal

from django.test import SimpleTestCase, TestCase

from .models import Product, ProductVariation
from .pricing import _loyalty_osnovica_iz_korpe


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


class LoyaltyAdminSearchTests(TestCase):
    def setUp(self):
        from django.contrib.auth.models import User

        from .loyalty import search_loyalty_cards
        from .models import LoyaltyCard, UserProfile

        self.search_loyalty_cards = search_loyalty_cards
        self.user = User.objects.create_user(
            'ana', 'ana@example.com', 'pass', first_name='Ana', last_name='Kovačević',
        )
        UserProfile.objects.create(user=self.user, telefon='065123456')
        self.card = LoyaltyCard.objects.create(
            user=self.user, kod='482731', barkod='L482731',
        )
        self.admin = User.objects.create_superuser('admin', 'admin@example.com', 'pass')

    def test_new_card_code_is_six_digits(self):
        from .loyalty import _generisi_kod

        kod = _generisi_kod()
        self.assertRegex(kod, r'^\d{6}$')

    def test_code_mode_finds_card_and_phone_not_name(self):
        self.assertEqual(self.search_loyalty_cards('482731', mode='code')[0].pk, self.card.pk)
        self.assertEqual(self.search_loyalty_cards('065123456', mode='code')[0].pk, self.card.pk)
        self.assertEqual(self.search_loyalty_cards('Kovačević', mode='code'), [])

    def test_name_mode_finds_name_not_card(self):
        found = self.search_loyalty_cards('Kovacevic', mode='name')
        self.assertEqual(found[0].pk, self.card.pk)
        self.assertEqual(self.search_loyalty_cards('482731', mode='name'), [])

    def test_admin_page_has_name_and_filter_controls(self):
        self.client.force_login(self.admin)
        page = self.client.get('/nalog/loyalty/')
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'Članovi / Kartice')
        self.assertContains(page, 'Admin panel')
        self.assertContains(page, 'Pretraga članova')
        self.assertContains(page, 'Novi član')
        self.assertContains(page, 'Pretraga člana')
        self.assertContains(page, '+387')
        self.assertContains(page, 'Viber')
        self.assertContains(page, 'WhatsApp')
        self.assertContains(page, '65 152 072')
        self.assertContains(page, 'Ukupno članova')
        self.assertContains(page, 'Najveći potrošač')
        self.assertNotContains(page, 'Ukupno bodova')
        self.assertContains(page, 'Rang nivoa')
        self.assertContains(page, 'Zakontaktiraj člana')
        self.assertContains(page, 'Kupovine po godinama')
        self.assertContains(page, 'Brza pomoć')
        named = self.client.get('/nalog/loyalty/', {'q': 'Kovacevic', 'mode': 'name'})
        self.assertContains(named, 'Ana')
        coded = self.client.get('/nalog/loyalty/', {'q': 'Kovacevic', 'mode': 'code'})
        self.assertContains(coded, 'Nema rezultata')
        any_name = self.client.get('/nalog/loyalty/', {'q': 'Kovacevic', 'mode': 'any'})
        self.assertContains(any_name, 'Ana')
        self.assertContains(page, 'Nema internet')
        self.assertContains(page, 'Ime, telefon ili broj kartice')
        self.assertContains(page, 'id="loyOpenPhone"')
        self.assertContains(page, 'Strani državljanin')
        self.assertContains(page, 'name="strani"')
        self.assertContains(page, '061234567')
        self.assertNotContains(page, 'placeholder="65 123 456"')
        self.assertNotContains(page, 'value="65')
        desk = self.client.get('/nalog/loyalty/')
        self.assertContains(desk, '/nalog/loyalty/clan/482731/')
        self.assertContains(desk, 'Broj kartice')
        opened = self.client.post('/nalog/loyalty/', {
            'action': 'open_card', 'channel': 'admin', 'telefon': '065123456',
        })
        self.assertEqual(opened.status_code, 302)
        self.assertIn('/nalog/loyalty/clan/482731/', opened['Location'])
        member = self.client.get('/nalog/loyalty/clan/482731/')
        self.assertEqual(member.status_code, 200)
        self.assertContains(member, 'Ana')
        self.assertContains(member, 'Detalji člana')
        self.assertContains(member, 'Loyalty program')
        self.assertContains(member, 'Brzi pregled')
        self.assertContains(member, 'Evidencija kupovina')
        self.assertContains(member, 'Evidentiraj kupovinu')
        self.assertContains(member, 'Dodaj napomenu')
        self.assertContains(member, 'viber://chat?number=%2B38765123456')
        self.assertContains(member, 'draft=')
        self.assertContains(member, 'data-channel="viber"')
        self.assertContains(member, 'Pošalji na Viber')
        self.assertContains(member, 'href="viber://chat?number=%2B38765123456')
        self.assertNotContains(member, 'data-chat-url=')
        self.assertNotContains(member, 'Bodovne transakcije')
        self.assertNotContains(member, 'Kod — Viber')
        self.assertNotContains(member, 'Admin — bez koda')
        bought = self.client.post('/nalog/loyalty/clan/482731/', {
            'action': 'evidentiraj_kupovinu',
            'iznos': '25.50',
            'placanje': 'gotovina',
        })
        self.assertEqual(bought.status_code, 302)
        from .models import LoyaltyPurchase
        self.assertTrue(LoyaltyPurchase.objects.filter(kartica=self.card, iznos='25.50').exists())
        again = self.client.get('/nalog/loyalty/clan/482731/')
        self.assertContains(again, 'Gotovina')
        self.assertContains(again, 'godina=2026')
        self.assertContains(again, '1 kupovina')
        self.assertContains(again, 'Obriši')
        self.assertContains(again, 'obrisi_kupovinu')
        pur = LoyaltyPurchase.objects.get(kartica=self.card, iznos='25.50')
        deleted = self.client.post('/nalog/loyalty/clan/482731/', {
            'action': 'obrisi_kupovinu',
            'purchase_id': pur.pk,
        })
        self.assertEqual(deleted.status_code, 302)
        self.assertFalse(LoyaltyPurchase.objects.filter(pk=pur.pk).exists())
        self.card.refresh_from_db()
        self.assertEqual(self.card.ukupna_potrosnja, Decimal('0'))
        after_del = self.client.get('/nalog/loyalty/clan/482731/')
        self.assertContains(after_del, 'Nema kupovina')
        bought = self.client.post('/nalog/loyalty/clan/482731/', {
            'action': 'evidentiraj_kupovinu',
            'iznos': '25.50',
            'placanje': 'gotovina',
        })
        self.assertEqual(bought.status_code, 302)
        again = self.client.get('/nalog/loyalty/clan/482731/')
        self.assertContains(again, 'Gotovina')
        self.assertContains(again, 'godina=2026')
        self.assertContains(again, '1 kupovina')
        desk_top = self.client.get('/nalog/loyalty/')
        self.assertContains(desk_top, 'Ana Kovačević')
        self.assertContains(desk_top, '25,50 KM')
        self.assertContains(desk_top, 'Nema internet')
        self.assertContains(desk_top, 'Kupovine po godinama')
        self.assertContains(desk_top, 'godina=2026')
        self.assertContains(desk_top, 'Gotovina')
        from datetime import datetime

        from django.utils import timezone

        from .models import LoyaltyPurchase
        old = LoyaltyPurchase.objects.create(
            kartica=self.card, iznos='10.00', placanje='gotovina',
        )
        LoyaltyPurchase.objects.filter(pk=old.pk).update(
            kreirano=timezone.make_aware(datetime(2025, 3, 1, 12, 0)),
        )
        years = self.client.get('/nalog/loyalty/clan/482731/')
        self.assertContains(years, 'godina=2025')
        y2025 = self.client.get('/nalog/loyalty/clan/482731/', {'godina': '2025'})
        self.assertContains(y2025, '10,00 KM')
        self.assertContains(y2025, '1 kupovina · 10,00 KM u 2025')
        exact = self.client.get('/nalog/loyalty/', {'q': '482731'})
        self.assertEqual(exact.status_code, 302)
        self.assertIn('/nalog/loyalty/clan/482731/', exact['Location'])
        created = self.client.post('/nalog/loyalty/', {
            'action': 'open_card', 'channel': 'admin',
            'telefon': '065999888', 'ime': 'Marko', 'prezime': 'Petrovic',
        })
        self.assertEqual(created.status_code, 302)
        self.assertIn('/nalog/loyalty/clan/', created['Location'])
        new_kod = created['Location'].rstrip('/').split('/')[-1]
        self.assertRegex(new_kod, r'^\d{6}$')
        from .loyalty import izdaj_loyalty_karticu, validiraj_ba_mobilni
        with self.assertRaises(ValueError):
            validiraj_ba_mobilni('061 123 456')
        with self.assertRaises(ValueError):
            validiraj_ba_mobilni('+38765123456')
        with self.assertRaises(ValueError):
            validiraj_ba_mobilni('65123456')
        local, e164 = validiraj_ba_mobilni('065123456')
        self.assertEqual(local, '065123456')
        self.assertEqual(e164, '38765123456')
        from .loyalty import validiraj_strani_mobilni, viber_chat_url
        stored, intl = validiraj_strani_mobilni('00381641234567')
        self.assertEqual(stored, '00381641234567')
        self.assertEqual(intl, '381641234567')
        with self.assertRaises(ValueError):
            validiraj_strani_mobilni('061234567')
        with self.assertRaises(ValueError):
            validiraj_strani_mobilni('0038765123456')
        card, user = izdaj_loyalty_karticu(
            'Srdjan', 'Ilic', '00381641234567', strani=True,
        )
        self.assertEqual(user.profil.telefon, '00381641234567')
        self.assertIn('381641234567', viber_chat_url(user.profil.telefon, 'test'))
        with self.assertRaises(ValueError):
            izdaj_loyalty_karticu('Drugi', 'Kupac', '065123456')
        dup = self.client.post('/nalog/loyalty/', {
            'action': 'open_card', 'channel': 'admin',
            'telefon': '065123456', 'ime': 'Drugi', 'prezime': 'Kupac',
        })
        self.assertEqual(dup.status_code, 302)
        self.assertNotIn('/nalog/loyalty/clan/482731/', dup['Location'])
        follow = self.client.get(dup['Location'])
        self.assertContains(follow, 'već registrovan')

    def test_viber_chat_url_prefills_draft(self):
        from urllib.parse import parse_qs, unquote_plus, urlparse

        from .loyalty import loyalty_card_caption, viber_chat_url

        caption = loyalty_card_caption(self.card)
        self.assertIn('https://g.page/r/CXurB2BnmyVdEBM/review', caption)
        url = viber_chat_url('065123456', caption)
        self.assertTrue(url.startswith('viber://chat?number=%2B38765123456'))
        self.assertIn('draft=', url)
        self.assertNotIn('&text=', url)
        draft_enc = url.split('draft=', 1)[1]
        self.assertNotIn('%', draft_enc)
        parsed = urlparse(url)
        draft_raw = parse_qs(parsed.query).get('draft', [''])[0]
        self.assertNotIn('%', draft_raw)
        draft = unquote_plus(draft_raw)
        self.assertIn('Vasa loyalty kartica', draft)
        self.assertIn('Broj kartice: 482731', draft)
        self.assertIn('g.page/r/CXurB2BnmyVdEBM/review', draft)
        self.assertIn(' posto', draft)
        self.assertNotIn('%', draft)
        otp = viber_chat_url('065123456', 'Vaš kod: 123456')
        self.assertIn('draft=', otp)
        self.assertIn('123456', unquote_plus(otp.split('draft=', 1)[1]))
        self.client.force_login(self.admin)
        resp = self.client.post(
            '/nalog/loyalty/',
            {'action': 'open_card', 'channel': 'viber', 'telefon': '065123456', 'ajax': '1'},
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data['ok'])
        self.assertIn('viber://chat?number=%2B38765123456', data['chat_url'])
        self.assertIn('draft=', data['chat_url'])

    def test_whatsapp_open_returns_chat_url(self):
        from .loyalty import whatsapp_app_url, whatsapp_chat_url

        from .loyalty import open_card_otp_message
        self.assertIn('65 152 072', open_card_otp_message('123456'))
        web = whatsapp_chat_url('065123456', 'kod 123456')
        app = whatsapp_app_url('065123456', 'kod 123456')
        self.assertTrue(web.startswith('https://wa.me/38765123456'))
        self.assertIn('text=', web)
        self.assertTrue(app.startswith('whatsapp://send?phone=38765123456'))
        self.client.force_login(self.admin)
        resp = self.client.post(
            '/nalog/loyalty/',
            {'action': 'open_card', 'channel': 'whatsapp', 'telefon': '065123456', 'ajax': '1'},
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data['ok'])
        self.assertIn('wa.me/38765123456', data['chat_url'])
        self.assertIn('whatsapp://send?phone=38765123456', data['app_url'])
        self.assertIn('step=2', data['redirect'])
        self.assertNotIn('open=whatsapp', data['redirect'])
        panel = self.client.get(data['redirect'])
        self.assertContains(panel, 'Verifikacija koda')
        self.assertContains(panel, 'Potvrdi i otvori karticu')
        self.assertContains(panel, 'https://wa.me/38765123456')
        self.assertContains(panel, 'Nema internet')
        from .loyalty import LOYALTY_OPEN_OTP_SESSION_KEY
        code = self.client.session.get(LOYALTY_OPEN_OTP_SESSION_KEY, {}).get('code')
        self.assertTrue(code)
        verify = self.client.post('/nalog/loyalty/', {
            'action': 'open_card_verify', 'otp_code': code,
        })
        self.assertEqual(verify.status_code, 302)
        self.assertIn('/nalog/loyalty/clan/482731/', verify['Location'])

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


class ProductVariationSplitTests(TestCase):
    def test_split_variations_into_standalone_products(self):
        from .product_merge import split_product_variations

        parent = Product.objects.create(
            naziv='Fox masinica',
            slug='fox-masinica-split',
            sifra='FOX-BASE',
            cijena=Decimal('100.00'),
            aktivan=True,
            na_stanju=True,
            stanje=0,
        )
        red = ProductVariation.objects.create(
            artikal=parent, naziv='Crvena', sifra='FOX-R',
            cijena=Decimal('110.00'), stanje=3, na_stanju=True, redoslijed=1,
        )
        blue = ProductVariation.objects.create(
            artikal=parent, naziv='Plava', sifra='FOX-B',
            cijena=Decimal('120.00'), stanje=5, na_stanju=True, redoslijed=2,
        )
        result = split_product_variations(parent)
        parent.refresh_from_db()
        self.assertEqual(parent.varijacije.count(), 0)
        self.assertEqual(result['split_count'], 2)
        self.assertEqual(len(result['created_products']), 1)
        self.assertEqual(parent.naziv, 'Fox masinica — Crvena')
        self.assertEqual(parent.sifra, 'FOX-R')
        self.assertEqual(parent.cijena, Decimal('110.00'))
        self.assertEqual(parent.stanje, 3)
        created = result['created_products'][0]
        created.refresh_from_db()
        self.assertEqual(created.naziv, 'Fox masinica — Plava')
        self.assertEqual(created.sifra, 'FOX-B')
        self.assertEqual(created.cijena, Decimal('120.00'))
        self.assertEqual(created.stanje, 5)
        self.assertFalse(ProductVariation.objects.filter(pk__in=[red.pk, blue.pk]).exists())

    def test_admin_split_action_requires_variations(self):
        from django.contrib.auth.models import User
        from django.urls import reverse

        user = User.objects.create_superuser('admin', 'a@example.com', 'pass')
        product = Product.objects.create(
            naziv='Bez varijacija', slug='bez-var-split', cijena=Decimal('10.00'),
        )
        self.client.force_login(user)
        changelist = reverse('admin:EcommerceApp_product_changelist')
        response = self.client.post(changelist, {
            'action': 'bulk_split_variations',
            '_selected_action': [str(product.pk)],
        })
        self.assertEqual(response.status_code, 302)


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


class SiteVersionTests(TestCase):
    def test_version_file_is_in_footer_label(self):
        from .site_version import build_site_version

        build_site_version.cache_clear()
        info = build_site_version()
        self.assertTrue(info['site_version'])
        self.assertIn(info['site_version'], info['site_version_label'])
        self.assertTrue(info['site_version_label'].startswith('v'))

    def test_footer_shows_version(self):
        from django.urls import reverse

        from .site_version import build_site_version

        page = self.client.get(reverse('home'))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'Verzija')
        self.assertContains(page, build_site_version()['site_version_label'])

    def test_footer_copies_carpologija_text(self):
        from django.urls import reverse

        page = self.client.get(reverse('home'))
        self.assertContains(page, 'Kontaktirajte nas:')
        self.assertContains(page, 'Raje Banjičića 76, Bijeljina, BiH')
        self.assertContains(page, '(387) 65 838-653')
        self.assertContains(page, 'carpologijabh@gmail.com')
        self.assertContains(page, 'www.carpologijabh.ba')
        self.assertContains(page, 'O nama')
        self.assertContains(page, 'Način plaćanja')
        self.assertContains(page, 'Sigurnost plaćanja')
        self.assertContains(page, 'Izjava o privatnosti')
        self.assertContains(page, 'Uslovi kupovine')
        self.assertContains(page, 'Ponedjeljak – Petak : 9:00-17:00')
        self.assertContains(page, '© Copyright Carpologija BH 2015-')
        self.assertContains(page, 'opremazaribolov.ba je sajt CarpologijaBH.')
        self.assertContains(page, 'Pridruži se preko 20 000 sabskrajbera')


class ProductNameOptionsTests(TestCase):
    def setUp(self):
        from .models import Category

        self.category = Category.objects.create(naziv='Majice')
        self.red = Product.objects.create(
            naziv='Majica crvena XL',
            cijena=Decimal('19.90'),
            na_stanju=True,
            stanje=5,
            kategorija=self.category,
        )
        self.blue = Product.objects.create(
            naziv='Majica plava S',
            cijena=Decimal('19.90'),
            na_stanju=True,
            stanje=3,
            kategorija=self.category,
        )
        self.unrelated = Product.objects.create(
            naziv='Shimano Stella 2500',
            cijena=Decimal('99.00'),
            na_stanju=True,
            stanje=2,
            kategorija=self.category,
        )

    def test_color_size_names_count_as_similar(self):
        from .product_options import names_are_similar, name_similarity

        self.assertTrue(names_are_similar('Majica crvena XL', 'Majica plava S'))
        self.assertTrue(names_are_similar(
            'Shimano Stella 2500 crvena',
            'Shimano Stella 2500 plava',
        ))
        self.assertFalse(names_are_similar('Majica crvena XL', 'Shimano Stella 2500'))
        # Ispod 90% istog teksta (i bez iste jezgre boja/veličina) se ne prikazuje
        self.assertLess(
            name_similarity('Shimano Catana 2500', 'Shimano Nasci 2500'),
            0.90,
        )
        self.assertFalse(names_are_similar(
            'Shimano Catana 2500',
            'Shimano Nasci 2500',
        ))
        # Šifra MT+broj se zanemaruje — ostatak naziva se podudara
        self.assertTrue(names_are_similar(
            'Fox Edges Armapoint MT12345 Curve',
            'Fox Edges Armapoint MT998877 Curve',
        ))
        self.assertGreaterEqual(
            name_similarity(
                'Fox Edges Armapoint MT12345 Curve',
                'Fox Edges Armapoint MT998877 Curve',
            ),
            0.90,
        )
        self.assertFalse(names_are_similar(
            'Fox Edges Armapoint MT111 Hook',
            'Korda Krank MT222 Hook',
        ))
        # Brojevi, cm, g i navodnici se zanemaruju
        self.assertTrue(names_are_similar('Fox Edges 12cm', 'Fox Edges 18cm'))
        self.assertTrue(names_are_similar('Korda Dark Matter 15g', 'Korda Dark Matter 25g'))
        self.assertTrue(names_are_similar('Carp hook 6"', "Carp hook 8'"))
        self.assertGreaterEqual(name_similarity('Fox Edges 12cm MT9', 'Fox Edges 18cm MT88'), 0.90)

    def test_finds_similar_sku_in_same_category(self):
        from .product_options import find_similar_name_products

        found = find_similar_name_products(self.red, Product.objects.filter(aktivan=True))
        self.assertEqual([item.pk for item in found], [self.blue.pk])

    def test_product_page_shows_button_and_popup(self):
        page = self.client.get(self.red.get_absolute_url())
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'Druge opcije artikla')
        self.assertContains(page, 'productOtherOptionsModal')
        self.assertContains(page, self.blue.naziv)
        self.assertContains(page, f'/artikal/{self.blue.slug}/dodaj/')
        similar = list(page.context['similar_name_products'])
        self.assertEqual([item.pk for item in similar], [self.blue.pk])

    def test_product_page_hides_button_without_similar(self):
        lonely = Product.objects.create(
            naziv='Unikatni feeder štap 3.6m',
            cijena=Decimal('45.00'),
            na_stanju=True,
            stanje=1,
            kategorija=self.category,
        )
        page = self.client.get(lonely.get_absolute_url())
        self.assertEqual(page.status_code, 200)
        self.assertNotContains(page, 'productOtherOptionsModal')
        self.assertNotContains(page, 'id="productOtherOptionsBtn"')

    def test_popup_option_can_be_added_to_cart(self):
        added = self.client.post(
            f'/artikal/{self.blue.slug}/dodaj/',
            {'quantity': '1', 'stay': '1'},
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )
        self.assertEqual(added.status_code, 200)
        payload = added.json()
        self.assertTrue(payload.get('ok'))
        self.assertGreaterEqual(payload.get('cart_count') or 0, 1)


class ProductAdminVariationSortTests(TestCase):
    def setUp(self):
        from django.contrib.auth.models import User

        self.admin = User.objects.create_superuser('prodadmin', 'prod@example.com', 'pass')
        self.plain = Product.objects.create(
            naziv='Majica bez varijacije',
            cijena=Decimal('10.00'),
            na_stanju=True,
            stanje=2,
        )
        self.with_vars = Product.objects.create(
            naziv='Majica sa varijacijama',
            cijena=Decimal('12.00'),
            na_stanju=True,
            stanje=3,
        )
        ProductVariation.objects.create(
            artikal=self.with_vars, naziv='XL', na_stanju=True, stanje=2,
        )
        ProductVariation.objects.create(
            artikal=self.with_vars, naziv='S', na_stanju=True, stanje=1,
        )

    def test_filter_and_sort_by_variations(self):
        from django.contrib.admin.sites import site
        from django.test import RequestFactory

        from .admin import ImaVarijacijeFilter, ProductAdmin

        self.assertIn('varijacije_broj', ProductAdmin.list_display)
        self.assertIn(ImaVarijacijeFilter, ProductAdmin.list_filter)
        request = RequestFactory().get('/admin/EcommerceApp/product/')
        request.user = self.admin
        model_admin = ProductAdmin(Product, site)
        qs = model_admin.get_queryset(request)
        self.assertIn('varijacije_broj', qs.query.annotations)
        with_vars = ImaVarijacijeFilter(
            request, {'ima_varijacije': ['da']}, Product, model_admin,
        ).queryset(request, qs)
        self.assertEqual(list(with_vars.values_list('pk', flat=True)), [self.with_vars.pk])
        without = ImaVarijacijeFilter(
            request, {'ima_varijacije': ['ne']}, Product, model_admin,
        ).queryset(request, qs)
        self.assertEqual(list(without.values_list('pk', flat=True)), [self.plain.pk])
        ordered = list(qs.order_by('-varijacije_broj', 'pk').values_list('pk', flat=True))
        self.assertEqual(ordered[0], self.with_vars.pk)

    def test_filter_same_mt_code_in_name(self):
        from django.contrib.admin.sites import site
        from django.test import RequestFactory

        from .admin import IstaMtSifraUNazivuFilter, ProductAdmin
        from .product_options import duplicate_mt_name_product_ids, extract_mt_codes

        first = Product.objects.create(
            naziv='MT12122 MATE Old School Stick 274cm 100-180g',
            slug='mt12122-a',
            sifra='MT12122-A',
            cijena=Decimal('10.00'),
            na_stanju=False,
        )
        second = Product.objects.create(
            naziv='MT-12122 MATE Old School Stick 274cm',
            slug='mt12122-b',
            sifra='OTHER',
            cijena=Decimal('11.00'),
            na_stanju=True,
        )
        unique = Product.objects.create(
            naziv='MT99999 Unique rod',
            slug='mt99999-u',
            sifra='MT99999',
            cijena=Decimal('12.00'),
            na_stanju=True,
        )
        self.assertEqual(extract_mt_codes(first.naziv), ['MT12122'])
        self.assertEqual(extract_mt_codes(second.naziv), ['MT12122'])
        ids = duplicate_mt_name_product_ids()
        self.assertEqual(ids, {first.pk, second.pk})
        self.assertNotIn(unique.pk, ids)

        self.assertIn(IstaMtSifraUNazivuFilter, ProductAdmin.list_filter)
        request = RequestFactory().get(
            '/admin/EcommerceApp/product/', {'ista_mt_sifra': 'da'},
        )
        request.user = self.admin
        model_admin = ProductAdmin(Product, site)
        qs = model_admin.get_queryset(request)
        filtered = IstaMtSifraUNazivuFilter(
            request, {'ista_mt_sifra': ['da']}, Product, model_admin,
        ).queryset(request, qs)
        self.assertEqual(set(filtered.values_list('pk', flat=True)), {first.pk, second.pk})
        self.assertEqual(model_admin.mt_sifra_u_nazivu(first), 'MT12122')
        self.assertEqual(model_admin.mt_sifra_u_nazivu(unique), 'MT99999')


class StaffStorefrontEditModeTests(TestCase):
    def setUp(self):
        from django.contrib.auth.models import User

        from .models import Category

        self.admin = User.objects.create_superuser('editadmin', 'edit@example.com', 'pass')
        self.guest = User.objects.create_user('kupac', 'kupac@example.com', 'pass')
        self.category = Category.objects.create(naziv='Edit kat', slug='edit-kat')
        self.incomplete = Product.objects.create(
            naziv='Nepotpun artikal',
            sifra='EDIT-MISS',
            cijena=Decimal('0.00'),
            na_stanju=True,
            aktivan=True,
            prikazi_na_pocetnoj=True,
        )
        self.complete = Product.objects.create(
            naziv='Kompletan artikal',
            sifra='EDIT-OK',
            cijena=Decimal('12.50'),
            opis='Ima opis.',
            kategorija=self.category,
            na_stanju=True,
            aktivan=True,
            slika='products/ok.jpg',
        )

    def test_catalog_pages_hide_filters(self):
        from django.urls import reverse

        category = self.client.get(reverse('category', args=[self.category.slug]))
        self.assertEqual(category.status_code, 200)
        self.assertContains(category, 'catalog-layout--full')
        self.assertContains(category, 'product-grid--catalog')
        self.assertNotContains(category, 'catalog-sidebar')
        self.assertNotContains(category, 'catalog-filter-title')
        self.assertNotContains(category, 'Filter i sortiranje')
        search = self.client.get(reverse('home'), {'q': 'EDIT-OK'})
        self.assertEqual(search.status_code, 200)
        self.assertContains(search, 'catalog-layout--full')
        self.assertNotContains(search, 'catalog-sidebar')
        self.assertNotContains(search, 'Filter i sortiranje')

    def test_missing_storefront_fields(self):
        self.assertEqual(
            self.incomplete.missing_storefront_fields(),
            ['kategorija', 'cijena', 'slika', 'opis'],
        )
        self.assertEqual(self.complete.missing_storefront_fields(), [])

    def test_edit_checkbox_only_for_superuser(self):
        from django.urls import reverse

        home = self.client.get(reverse('home'))
        self.assertNotContains(home, 'staff-edit-toggle')
        self.client.force_login(self.guest)
        home = self.client.get(reverse('home'))
        self.assertNotContains(home, 'staff-edit-toggle')
        self.client.force_login(self.admin)
        home = self.client.get(reverse('home'))
        self.assertContains(home, 'staff-edit-toggle')
        self.assertContains(home, 'Edit')
        self.assertContains(home, reverse('staff_toggle_edit_mode'))

    def test_edit_mode_shows_missing_and_opens_brzi_unos(self):
        from django.urls import reverse

        self.client.force_login(self.admin)
        toggled = self.client.post(reverse('staff_toggle_edit_mode'), {
            'enabled': '1',
            'next': reverse('category', args=[self.category.slug]),
        })
        self.assertEqual(toggled.status_code, 302)
        home = self.client.get(reverse('home'), {'q': 'EDIT-MISS'})
        self.assertContains(home, 'Fali kategorija')
        self.assertContains(home, 'Fali cijena')
        self.assertContains(home, 'Fali slika')
        self.assertContains(home, 'Fali opis')
        brzi = reverse('staff_magacin_brzi_unos_aktivacija', args=[self.incomplete.pk])
        self.assertContains(home, brzi)
        self.assertContains(home, f'href="{brzi}"')
        self.assertContains(home, 'target="_blank"')
        self.assertContains(home, 'rel="noopener"')
        off = self.client.post(reverse('staff_toggle_edit_mode'), {
            'enabled': '0',
            'next': reverse('home'),
        })
        self.assertEqual(off.status_code, 302)
        after = self.client.get(reverse('home'))
        self.assertNotContains(after, 'Fali kategorija')
        self.assertNotContains(
            after,
            reverse('staff_magacin_brzi_unos_aktivacija', args=[self.incomplete.pk]),
        )

    def test_edit_mode_bulk_applies_only_filled_fields(self):
        import json
        from django.urls import reverse

        from .models import Brand

        brand = Brand.objects.create(naziv='Fox Bulk', slug='fox-bulk')
        self.client.force_login(self.admin)
        blocked = self.client.post(
            reverse('staff_product_bulk_edit'),
            data=json.dumps({'product_ids': [self.complete.pk], 'je_hit': '1'}),
            content_type='application/json',
        )
        self.assertEqual(blocked.status_code, 403)
        self.client.post(reverse('staff_toggle_edit_mode'), {'enabled': '1'})
        home = self.client.get(reverse('home'))
        self.assertContains(home, 'id="staffBulkPanel"')
        self.assertContains(home, reverse('staff_product_bulk_edit'))
        empty = self.client.post(
            reverse('staff_product_bulk_edit'),
            data=json.dumps({'product_ids': [self.complete.pk, self.incomplete.pk]}),
            content_type='application/json',
        )
        self.assertEqual(empty.status_code, 400)
        applied = self.client.post(
            reverse('staff_product_bulk_edit'),
            data=json.dumps({
                'product_ids': [self.complete.pk, self.incomplete.pk],
                'brend_id': str(brand.pk),
                'akcija_postotak': '20',
                'je_hit': '1',
            }),
            content_type='application/json',
        )
        self.assertEqual(applied.status_code, 200)
        payload = applied.json()
        self.assertTrue(payload['ok'])
        self.assertEqual(payload['count'], 2)
        self.complete.refresh_from_db()
        self.incomplete.refresh_from_db()
        self.assertEqual(self.complete.brend_id, brand.pk)
        self.assertEqual(self.incomplete.brend_id, brand.pk)
        self.assertEqual(self.complete.opis, 'Ima opis.')
        self.assertEqual(self.incomplete.kategorija_id, None)
        self.assertTrue(self.complete.je_hit)
        self.assertTrue(self.incomplete.je_hit)
        self.assertEqual(self.complete.akcija_postotak, Decimal('20.00'))
        self.assertEqual(self.incomplete.akcija_postotak, Decimal('20.00'))
        per_item = self.client.post(
            reverse('staff_product_bulk_edit'),
            {
                'product_ids': str(self.incomplete.pk),
                f'opis_{self.incomplete.pk}': 'Veći opis s ChatGPT-a.',
            },
        )
        self.assertEqual(per_item.status_code, 200)
        self.assertTrue(per_item.json()['ok'])
        self.incomplete.refresh_from_db()
        self.assertEqual(self.incomplete.opis, 'Veći opis s ChatGPT-a.')

    def test_edit_mode_marks_out_of_stock_on_catalog(self):
        from django.urls import reverse

        oos = Product.objects.create(
            naziv='Nema ga na lageru',
            sifra='EDIT-OOS',
            cijena=Decimal('9.90'),
            opis='Ima opis.',
            kategorija=self.category,
            na_stanju=False,
            aktivan=True,
            slika='products/oos.jpg',
        )
        self.client.force_login(self.admin)
        hidden = self.client.get(reverse('category', args=[self.category.slug]))
        self.assertNotContains(hidden, 'Nema ga na lageru')
        self.client.post(reverse('staff_toggle_edit_mode'), {
            'enabled': '1',
            'next': reverse('category', args=[self.category.slug]),
        })
        shown = self.client.get(reverse('category', args=[self.category.slug]))
        self.assertContains(shown, 'Nema ga na lageru')
        self.assertContains(shown, 'staff-stock-badge--photo')
        self.assertContains(shown, 'Nije na stanju')
        self.assertContains(shown, 'staff-edit-mode-on')


class StaffLiveAlertTests(TestCase):
    def test_only_purchase_creates_live_event(self):
        from .models import StaffSiteEvent
        from .staff_alerts import notify_cart_add, notify_purchase, notify_registration

        self.assertIsNone(notify_cart_add(ime='Ana', product_name='Braid'))
        self.assertIsNone(notify_registration(ime='Ana', email='ana@example.com'))
        event = notify_purchase(
            ime='Ana Ribic',
            email='ana@example.com',
            grad='Tuzla',
            order_number='0042',
            total='86.50',
        )
        self.assertIsNotNone(event)
        self.assertEqual(event.tip, StaffSiteEvent.Tip.PURCHASE)
        self.assertIn('ORDER:0042', event.poruka)
        self.assertIn('TOTAL:86,50', event.poruka)
        self.assertEqual(StaffSiteEvent.objects.filter(tip='cart').count(), 0)
        self.assertEqual(StaffSiteEvent.objects.filter(tip='register').count(), 0)


class CatalogNameSearchTests(TestCase):
    def setUp(self):
        self.itana = Product.objects.create(
            naziv='MATE itana tournament spin',
            slug='mate-itana-tournament-spin',
            sifra='ITANA-1',
            cijena=Decimal('10.00'),
            na_stanju=True,
            aktivan=True,
        )
        self.other = Product.objects.create(
            naziv='Fox Warrior S feeder',
            slug='fox-warrior-s-feeder',
            sifra='FOX-1',
            cijena=Decimal('20.00'),
            na_stanju=True,
            aktivan=True,
        )

    def _ids(self, query):
        from .views import _apply_search_filter

        return set(
            _apply_search_filter(Product.objects.all(), query).values_list('pk', flat=True)
        )

    def test_one_word_from_middle_of_name(self):
        self.assertEqual(self._ids('itana'), {self.itana.pk})

    def test_two_words_any_order(self):
        self.assertEqual(self._ids('tournament spin'), {self.itana.pk})
        self.assertEqual(self._ids('spin tournament'), {self.itana.pk})

    def test_non_consecutive_words(self):
        self.assertEqual(self._ids('itana spin'), {self.itana.pk})
        self.assertEqual(self._ids('mate spin'), {self.itana.pk})

    def test_does_not_return_unrelated(self):
        self.assertNotIn(self.other.pk, self._ids('itana spin'))
        self.assertEqual(self._ids('feeder'), {self.other.pk})


class CartIconThemeTests(TestCase):
    def test_theme_css_uses_cart_icon_color(self):
        from .models import SiteSettings

        site = SiteSettings.load()
        site.boja_ikonica_korpa = '#ff5500'
        css = site.get_theme_ui()['css_vars']
        self.assertIn('--cart-icon:#ff5500', css)
        self.assertRegex(css, r'--cart-icon-hover:#[0-9a-fA-F]{6}')
        self.assertNotIn('--cart-icon-hover:#ff5500', css)

    def test_default_cart_icon_is_black(self):
        from .models import SiteSettings

        site = SiteSettings.load()
        css = site.get_theme_ui()['css_vars']
        self.assertIn('--cart-icon:#111111', css)
        self.assertIn('--cart-icon-hover:#222222', css)
