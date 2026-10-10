from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from .models import Product, Category


@override_settings(STORAGES={
    'default': {'BACKEND': 'django.core.files.storage.InMemoryStorage'},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
})
class WarehouseNewProductTests(TestCase):
    def setUp(self):
        cache.clear()
        self.owner = get_user_model().objects.create_superuser('new-product-owner', 'owner@example.invalid', 'password')
        self.client.force_login(self.owner)
        self.url = reverse('staff_magacin_brzi_unos_novi')

    def test_create_saves_extended_fields_and_tags(self):
        self.assertEqual(self.client.get('/nalog/magacin/artikli/brzi-unos/').status_code, 404)
        self.assertEqual(self.url, '/nalog/magacin/artikli/novi/')
        data = {'rezim_zaliha': 'tracked', 'naziv': 'Demo proizvod', 'cijena': '20', 'sifra': 'NEW-EXTENDED', 'barkod': 'NEW-BARCODE',
                'opis': 'Detaljan opis', 'pakovanje_komada': '9', 'akcijska_cijena': '15,50',
                'tagovi_unos': 'feeder, ribolov, feeder', 'tip': '3',
                'meta_title': 'SEO naslov', 'meta_description': 'SEO opis', 'h1_naslov': 'SEO H1',
                'seo_tekst_iznad': 'Iznad', 'seo_tekst_ispod': 'Ispod'}
        response = self.client.post(self.url, data)
        self.assertRedirects(response, reverse('staff_magacin_artikli'))
        product = Product.objects.get(sifra='NEW-EXTENDED')
        self.assertEqual(product.barkod, 'NEW-BARCODE')
        self.assertEqual(product.pakovanje_komada, 9)
        self.assertEqual(float(product.akcijska_cijena), 15.5)
        self.assertEqual(product.prioritet_lagera, 3)
        self.assertEqual(product.meta_title, 'SEO naslov')
        self.assertEqual(product.opis, 'Detaljan opis')
        self.assertEqual(set(product.tagovi.values_list('naziv', flat=True)), {'feeder', 'ribolov'})
        self.assertContains(self.client.get(self.url), 'data-mg-scan-target="id_barkod"')

    def test_invalid_price_packaging_and_duplicate_barcode_do_not_create(self):
        Product.objects.create(naziv='Postojeći', barkod='USED-BARCODE', cijena=10)
        before = Product.objects.count()
        for extra in ({'pakovanje_komada': '0'}, {'akcijska_cijena': '30'}, {'barkod': 'USED-BARCODE'}, {'tip': '99'}):
            with self.subTest(extra=extra):
                response = self.client.post(self.url, {'rezim_zaliha': 'tracked', 'naziv': 'Ne kreiraj', 'cijena': '20', **extra})
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.context['form_errors'])
                self.assertEqual(Product.objects.count(), before)

    def test_duplicate_name_and_code_are_rejected_case_and_space_insensitive(self):
        from .models import ProductVariation
        existing = Product.objects.create(naziv='Demo   Artikal', sifra='CODE-ABC', cijena=10)
        ProductVariation.objects.create(artikal=existing, naziv='Varijacija', sifra='VAR-CODE', cijena=10)
        before = Product.objects.count()
        for fields in (
            {'naziv': ' demo artikal ', 'sifra': 'NEW-CODE'},
            {'naziv': 'DEMO   ARTIKAL', 'sifra': 'NEW-CODE'},
            {'naziv': 'Drugi naziv', 'sifra': ' code-abc '},
            {'naziv': 'Drugi naziv', 'sifra': 'var-code'},
        ):
            with self.subTest(fields=fields):
                response = self.client.post(self.url, {'rezim_zaliha': 'tracked', 'cijena': '20', **fields})
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.context['form_errors'])
                self.assertEqual(Product.objects.count(), before)

    def test_model_validation_rejects_new_duplicate_for_panel_editor(self):
        from django.core.exceptions import ValidationError
        Product.objects.create(naziv='Postojeći naziv', sifra='UNIQUE-CODE', cijena=10)
        for product, field in (
            (Product(naziv='postojeći naziv', cijena=20), 'naziv'),
            (Product(naziv='Novi naziv', sifra='unique-code', cijena=20), 'sifra'),
        ):
            with self.subTest(field=field), self.assertRaises(ValidationError) as error:
                product.clean()
            self.assertIn(field, error.exception.message_dict)

    def test_panel_creation_and_warehouse_edit_use_same_record(self):
        from django import forms
        from .models import Brand
        brand = Brand.objects.create(naziv='Zajednički brend')
        add_url = reverse('panel_admin:EcommerceApp_product_add')
        page = self.client.get(add_url)
        data = {}
        for field in page.context['adminform'].form:
            value = field.value()
            if isinstance(field.field, forms.FileField):
                continue
            if isinstance(field.field, forms.BooleanField):
                if value:
                    data[field.html_name] = 'on'
            elif isinstance(field.field, forms.ModelMultipleChoiceField):
                data[field.html_name] = value or []
            else:
                data[field.html_name] = '' if value is None else str(value)
        for inline in page.context['inline_admin_formsets']:
            for field in inline.formset.management_form:
                data[field.html_name] = str(field.value())
            data[inline.formset.prefix + '-TOTAL_FORMS'] = '0'
        data.update(naziv='Panel kreirani artikal', sifra='PANEL-SHARED', cijena='25', brend=brand.pk, _save='Save')
        saved = self.client.post(add_url, data)
        self.assertEqual(saved.status_code, 302)
        product = Product.objects.get(sifra='PANEL-SHARED')
        self.assertContains(self.client.get(reverse('staff_magacin_artikli')), 'Panel kreirani artikal')
        edit = self.client.post(reverse('staff_magacin_artikal_izmjena', args=[product.pk]), {
            'naziv': 'Magacin izmijenjen naziv', 'sifra': 'PANEL-SHARED', 'cijena': '35', 'brend_id': brand.pk,
        })
        self.assertEqual(edit.status_code, 302)
        product.refresh_from_db()
        self.assertEqual(product.naziv, 'Magacin izmijenjen naziv')
        self.assertEqual(float(product.cijena), 35)
        self.assertEqual(Product.objects.filter(sifra='PANEL-SHARED').count(), 1)
        self.assertContains(self.client.get(reverse('panel_admin:EcommerceApp_product_changelist')), product.naziv)

    def test_warehouse_creation_shows_in_panel_and_default_warehouse_list(self):
        self.client.post(self.url, {'rezim_zaliha': 'tracked', 'naziv': 'Magacin zajednički artikal', 'sifra': 'WH-SHARED', 'cijena': '20'})
        product = Product.objects.get(sifra='WH-SHARED')
        self.assertContains(self.client.get(reverse('staff_magacin_artikli')), product.naziv)
        self.assertContains(self.client.get(reverse('panel_admin:EcommerceApp_product_changelist')), product.naziv)

    def test_forced_matching_product_first_in_search_category_and_suggestions(self):
        category = Category.objects.create(naziv='Demo kategorija')
        normal = Product.objects.create(naziv='Feeder', cijena=10, na_stanju=True, stanje=1, kategorija=category, je_novitet=True)
        forced = Product.objects.create(naziv='Feeder izbor', cijena=50, na_stanju=True, stanje=1, kategorija=category, prioritet_lagera=3)
        unrelated = Product.objects.create(naziv='Drugi proizvod', cijena=1, na_stanju=True, stanje=1, kategorija=category, prioritet_lagera=3)
        from .views import _apply_product_filters
        from django.test import RequestFactory
        for params in ({'q': 'feeder'}, {'q': 'feeder', 'kategorija': category.slug}, {'q': 'feeder', 'sort': 'rastuca'}):
            request = RequestFactory().get('/', params)
            request.session = self.client.session
            products, _ = _apply_product_filters(Product.objects.all(), request)
            self.assertEqual(products[0].pk, forced.pk)
            self.assertIn(normal.pk, [product.pk for product in products])
            self.assertNotIn(unrelated.pk, [product.pk for product in products])
        response = self.client.get(reverse('search_suggest'), {'q': 'feeder'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['results'][0]['slug'], forced.slug)

    def test_manual_availability_without_quantity_displays_slash_without_creating_stock(self):
        from .models import WarehouseStock, WarehouseMovement
        product = Product.objects.create(naziv='Nepoznata količina', cijena=10, na_stanju=True, stanje=0)
        stocks_before = WarehouseStock.objects.count()
        movements_before = WarehouseMovement.objects.count()
        listing = self.client.get(reverse('staff_magacin_artikli'))
        row = next(row for row in listing.context['page'].object_list if row['product'].pk == product.pk)
        self.assertTrue(row['quantity_unknown'])
        self.assertContains(listing, 'Na stanju · /')
        detail = self.client.get(reverse('staff_magacin_artikal', args=[product.pk]))
        self.assertTrue(detail.context['quantity_unknown'])
        self.assertEqual(detail.context['totals']['na_stanju'], 0)
        self.assertContains(detail, 'Na stanju — količina i lokacija nisu evidentirane.')
        self.assertEqual(WarehouseStock.objects.count(), stocks_before)
        self.assertEqual(WarehouseMovement.objects.count(), movements_before)
        product.refresh_from_db()
        self.assertTrue(product.na_stanju)
        self.assertEqual(product.stanje, 0)
        product.na_stanju = False
        product.save(update_fields=['na_stanju'])
        detail = self.client.get(reverse('staff_magacin_artikal', args=[product.pk]))
        self.assertFalse(detail.context['quantity_unknown'])
        self.assertContains(detail, 'Nema zalihe na nijednoj lokaciji.')

    def test_known_stock_quantity_is_not_unknown(self):
        from .magacin import stock_quantity_unknown
        product = Product(naziv='Poznata količina', cijena=10, na_stanju=True, stanje=0)
        self.assertFalse(stock_quantity_unknown(product, {'na_stanju': 5, 'rezervisano': 0}))
        product.stanje = 5
        self.assertFalse(stock_quantity_unknown(product, {'na_stanju': 0, 'rezervisano': 0}))
