from html.parser import HTMLParser
from django.test import TestCase, override_settings
from .models import Product, Akcija, AkcijaBundleLine


class FormNesting(HTMLParser):
    def __init__(self):
        super().__init__()
        self.depth = 0
        self.nested = False
        self.bundle_forms = 0

    def handle_starttag(self, tag, attrs):
        if tag == 'form':
            self.nested |= self.depth > 0
            self.depth += 1
            if 'pd-bundle__form' in dict(attrs).get('class', ''):
                self.bundle_forms += 1

    def handle_endtag(self, tag):
        if tag == 'form':
            self.depth = max(0, self.depth-1)


@override_settings(STORAGES={'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
                            'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class ProductBundlePlacementTests(TestCase):
    def setUp(self):
        self.product = Product.objects.create(naziv='Stap za set', cijena=100, na_stanju=True, stanje=10)

    def test_no_bundle_leaves_no_trust_or_empty_bundle(self):
        response = self.client.get(self.product.get_absolute_url())
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'class="pd-conversion-trust"')
        self.assertNotContains(response, 'class="pd-inline-bundle pd-inline-bundle--offer"')

    def test_active_bundle_below_gallery_without_nested_forms(self):
        offer = Akcija.objects.create(naziv='Komplet', tip='bundle', popust_postotak=10)
        AkcijaBundleLine.objects.create(akcija=offer, product=self.product, quantity=2)
        response = self.client.get(self.product.get_absolute_url())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'class="pd-inline-bundle pd-inline-bundle--offer"', count=1)
        html = response.content.decode()
        self.assertLess(html.index('id="productDetailInfo"'), html.index('class="pd-inline-bundle pd-inline-bundle--offer"'))
        self.assertLess(html.index('class="pd-inline-bundle pd-inline-bundle--offer"'), html.index('class="pd-bundle-trust"'))
        self.assertLess(html.index('class="pd-bundle-trust"'), html.index('class="product-lower-layout"'))
        self.assertLess(html.index('class="pd-inline-bundle pd-inline-bundle--offer"'), html.index('product-detail-policies') if 'product-detail-policies' in html else len(html))
        parser = FormNesting()
        parser.feed(html)
        self.assertFalse(parser.nested)
        self.assertEqual(parser.bundle_forms, 1)
        offer.aktivan = False
        offer.save()
        self.assertNotContains(self.client.get(self.product.get_absolute_url()), 'class="pd-inline-bundle pd-inline-bundle--offer"')

    def test_quantity_offer_below_gallery_with_separate_forms(self):
        from .models import AkcijaQtyTier
        offer = Akcija.objects.create(naziv='Kupi više', tip='qty_deal', artikal=self.product)
        AkcijaQtyTier.objects.create(akcija=offer, quantity=2, popust_postotak=10)
        response = self.client.get(self.product.get_absolute_url())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'class="pd-inline-qty"', count=1)
        self.assertNotContains(response, 'class="pd-qtydeal"')
        self.assertNotContains(response, 'pd-ponuda-hint--qty')
        html = response.content.decode()
        parser = FormNesting()
        parser.feed(html)
        self.assertFalse(parser.nested)
        self.assertLess(html.index('id="productDetailInfo"'), html.index('class="pd-inline-qty"'))
        if 'class="pd-conversion-ship"' in html:
            self.assertLess(html.index('class="pd-conversion-ship"'), html.index('class="pd-inline-qty"'))
        self.assertLess(html.index('class="pd-inline-qty"'), html.index('class="pd-bundle-trust"'))
        self.assertContains(response, 'data-qty-choice="1"')
        self.assertContains(response, 'data-qty-choice="2"')
        self.assertContains(response, '90.00 KM / kom')
        self.assertContains(response, 'name="tier_id"')
        self.assertNotContains(response, 'KUPI 1 KOM')
        offer.refresh_from_db()
        self.assertEqual([col['quantity'] for col in offer.qty_deal_page_offer()['columns']], [2])

    def test_plus_offer_renders_pair_and_adds_both_at_offer_price(self):
        from decimal import Decimal
        from django.urls import reverse
        offered = Product.objects.create(naziv='Maska', cijena=40, na_stanju=True, stanje=5)
        offer = Akcija.objects.create(
            naziv='Telefon i maska', tip=Akcija.Tip.PONUDA,
            artikal=self.product, gratis_artikal=offered, popust_postotak=20,
        )
        response = self.client.get(self.product.get_absolute_url())
        self.assertContains(response, 'Dodaj oba u korpu')
        self.assertContains(response, 'Preporučujemo')
        self.assertContains(response, 'name="gratis_akcija_id"')
        from .views import _product_page_plus_offer
        self.assertEqual(_product_page_plus_offer(self.product)['pricing']['total_snizena'], Decimal('132'))
        response = self.client.post(reverse('add_to_cart', args=[self.product.slug]), {
            'quantity': '1', 'stay': '1', 'gratis_choice': 'yes',
            'gratis_akcija_id': str(offer.pk), 'gratis_quantity': '1',
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['cart_count'], 2)
        offered.aktivan = False
        offered.save()
        self.assertIsNone(_product_page_plus_offer(self.product))

    def test_selected_quantity_offer_adds_without_reopening_popup(self):
        from .models import AkcijaQtyTier
        from django.urls import reverse
        offer = Akcija.objects.create(naziv='Kupi više', tip='qty_deal', artikal=self.product)
        AkcijaQtyTier.objects.create(akcija=offer, quantity=2, popust_postotak=10)
        url = reverse('add_to_cart', args=[self.product.slug])
        response = self.client.post(url, {'quantity': '2', 'stay': '1', 'akcija_id': str(offer.pk)})
        data = response.json()
        self.assertTrue(data['ok'])
        self.assertNotIn('requires_qty_deal_choice', data)
        self.assertEqual(data['cart_count'], 2)
        self.assertEqual(data['meta_add_to_cart']['value'], 180.0)
        response = self.client.post(url, {'quantity': '1', 'stay': '1', 'akcija_id': str(offer.pk)})
        data = response.json()
        self.assertTrue(data['ok'])
        self.assertNotIn('requires_qty_deal_choice', data)
        self.assertEqual(data['cart_count'], 3)
        self.assertEqual(data['meta_add_to_cart']['value'], 100.0)
