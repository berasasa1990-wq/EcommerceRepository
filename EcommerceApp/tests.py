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
        from urllib.parse import parse_qs, unquote, urlparse

        from .loyalty import loyalty_card_caption, viber_chat_url

        caption = loyalty_card_caption(self.card)
        self.assertIn('https://g.page/r/CXurB2BnmyVdEBM/review', caption)
        url = viber_chat_url('065123456', caption)
        self.assertTrue(url.startswith('viber://chat?number=%2B38765123456'))
        self.assertIn('draft=', url)
        self.assertNotIn('&text=', url)
        parsed = urlparse(url)
        draft = unquote(parse_qs(parsed.query).get('draft', [''])[0])
        self.assertIn('Vasa loyalty kartica', draft)
        self.assertIn('Broj kartice: 482731', draft)
        self.assertIn('g.page/r/CXurB2BnmyVdEBM/review', draft)
        self.assertIn(' posto', draft)
        self.assertNotIn('%', draft)
        otp = viber_chat_url('065123456', 'Vaš kod: 123456')
        self.assertIn('draft=', otp)
        self.assertIn('123456', unquote(otp.split('draft=', 1)[1]))
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
