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
        self.assertNotContains(response, 'class="pd-inline-bundle"')

    def test_active_bundle_once_inside_product_info_without_nested_forms(self):
        offer = Akcija.objects.create(naziv='Komplet', tip='bundle', popust_postotak=10)
        AkcijaBundleLine.objects.create(akcija=offer, product=self.product, quantity=2)
        response = self.client.get(self.product.get_absolute_url())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'class="pd-inline-bundle"', count=1)
        html = response.content.decode()
        self.assertLess(html.index('id="productDetailInfo"'), html.index('class="pd-inline-bundle"'))
        self.assertLess(html.index('class="pd-inline-bundle"'), html.index('product-detail-policies') if 'product-detail-policies' in html else len(html))
        parser = FormNesting()
        parser.feed(html)
        self.assertFalse(parser.nested)
        self.assertEqual(parser.bundle_forms, 1)
        offer.aktivan = False
        offer.save()
        self.assertNotContains(self.client.get(self.product.get_absolute_url()), 'class="pd-inline-bundle"')

    def test_quantity_offer_in_purchase_area_with_separate_forms(self):
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
        self.assertContains(response, 'name="tier_id"')
        self.assertNotContains(response, 'KUPI 1 KOM')
        offer.refresh_from_db()
        self.assertEqual([col['quantity'] for col in offer.qty_deal_page_offer()['columns']], [2])
