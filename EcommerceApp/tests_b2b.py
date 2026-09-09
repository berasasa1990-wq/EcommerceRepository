from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import Client, TestCase, override_settings

from .admin import B2BAccountForm
from .models import B2BAccount, Category, Product, ProductVariation, WarehouseLocation, WarehouseStock
from .views_b2b import netto


@override_settings(ALLOWED_HOSTS=['testserver'], SITE_PREP_ENABLED=False,
    STORAGES={'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
              'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class B2BTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.account = B2BAccount(username='partner', company='Partner firma')
        cls.account.set_password('Partner-secret-392!')
        cls.account.save()
        cls.root = Category.objects.create(naziv='Štapovi')
        cls.child = Category.objects.create(naziv='Feeder', roditelj=cls.root)
        cls.product = Product.objects.create(naziv='B2B tajni artikal', cijena=Decimal('161.46'), kategorija=cls.child)
        cls.location = WarehouseLocation.objects.create(sifra='B2B-A', naziv='Glavni magacin')
        WarehouseStock.objects.create(product=cls.product, location=cls.location, kolicina=12, rezervisano=3)

    def setUp(self):
        cache.clear()

    def login(self):
        return self.client.post('/veleprodaja', {'username': 'partner', 'password': 'Partner-secret-392!'})

    def test_access_is_separate_from_retail_and_admin(self):
        user = get_user_model().objects.create_user(username='retail', password='test', is_staff=True)
        self.client.force_login(user)
        for path in ['/veleprodaja', '/veleprodaja/']:
            response = self.client.get(path)
            self.assertContains(response, 'Prijavi se')
            self.assertNotContains(response, self.product.naziv)
            self.assertIn('no-store', response['Cache-Control'])
        self.assertNotContains(self.client.post('/veleprodaja', {'username': 'retail', 'password': 'test'}), self.product.naziv)

    def test_catalog_price_stock_and_descendant_filter(self):
        self.assertEqual(self.login().status_code, 302)
        response = self.client.get('/veleprodaja', {'kategorija': self.root.slug})
        self.assertContains(response, self.product.naziv)
        row = response.context['rows'][0]
        self.assertEqual(row['netto'], Decimal('100.00'))
        quantity = next(q for q in row['quantities'] if q['location'] == self.location)
        self.assertEqual((quantity['quantity'], quantity['available']), (12, 9))
        self.assertNotContains(self.client.get('/veleprodaja', {'q': 'nepostojece'}), self.product.naziv)
        self.assertEqual(netto(Decimal('10')), Decimal('6.19'))

    def test_variations_use_own_price_and_stock(self):
        variation = ProductVariation.objects.create(artikal=self.product, naziv='3.6 m', sifra='VAR-B2B', cijena=Decimal('322.92'))
        WarehouseStock.objects.create(product=self.product, variation=variation, location=self.location, kolicina=5)
        self.login()
        response = self.client.get('/veleprodaja', {'q': 'VAR-B2B'})
        row = response.context['rows'][0]
        self.assertEqual(row['netto'], Decimal('200.00'))
        self.assertEqual(next(q['quantity'] for q in row['quantities'] if q['location'] == self.location), 5)

    def test_disable_and_password_change_revoke_access(self):
        self.login()
        self.account.is_active = False
        self.account.save()
        self.assertNotContains(self.client.get('/veleprodaja'), self.product.naziv)
        self.account.is_active = True
        self.account.save()
        self.login()
        self.account.set_password('Changed-secret-492!')
        self.account.save()
        self.assertNotContains(self.client.get('/veleprodaja'), self.product.naziv)

    def test_logout_requires_post_and_revokes_access(self):
        self.login()
        self.assertEqual(self.client.get('/veleprodaja/odjava/').status_code, 405)
        self.client.post('/veleprodaja/odjava/')
        self.assertNotContains(self.client.get('/veleprodaja'), self.product.naziv)

    def test_csrf_and_throttle(self):
        client = Client(enforce_csrf_checks=True)
        self.assertEqual(client.post('/veleprodaja', {'username': 'partner', 'password': 'test'}).status_code, 403)
        for _ in range(10):
            self.client.post('/veleprodaja', {'username': 'partner', 'password': 'wrong'})
        self.assertContains(self.login(), 'Previše pokušaja')

    def test_admin_requires_password_and_stores_hash(self):
        data = {'username': 'new', 'company': 'Nova firma', 'is_active': True}
        self.assertFalse(B2BAccountForm(data=data).is_valid())
        form = B2BAccountForm(data={**data, 'new_password': 'Different-secret-392!'})
        self.assertTrue(form.is_valid(), form.errors)
        account = form.save()
        self.assertNotEqual(account.password, 'Different-secret-392!')
        from django.contrib.auth.hashers import check_password
        self.assertTrue(check_password('Different-secret-392!', account.password))

    def test_b2b_cart_add_update_remove_and_netto_total(self):
        self.login()
        url = f'/veleprodaja/korpa/{self.product.pk}/'
        self.client.post(url, {'quantity': 2, 'price': '0.01'})
        response = self.client.get('/veleprodaja/korpa/')
        self.assertEqual(response.context['total'], Decimal('200.00'))
        self.assertEqual(response.context['rows'][0]['quantity'], 2)
        self.assertNotIn('cart', self.client.session)
        self.client.post(url, {'action': 'update', 'quantity': 3})
        self.assertEqual(self.client.get('/veleprodaja/korpa/').context['total'], Decimal('300.00'))
        self.client.post(url, {'action': 'remove'})
        self.assertEqual(self.client.get('/veleprodaja/korpa/').context['rows'], [])

    def test_cart_stock_auth_and_input_validation(self):
        url = f'/veleprodaja/korpa/{self.product.pk}/'
        self.assertEqual(self.client.post(url).status_code, 302)
        self.assertNotIn('b2b_cart', self.client.session)
        self.assertEqual(self.client.get('/veleprodaja/korpa/').status_code, 302)
        self.login()
        self.assertEqual(self.client.get(url).status_code, 405)
        for quantity in ['abc', '-1', '0']:
            self.assertEqual(self.client.post(url, {'quantity': quantity}).status_code, 400)
        self.client.post(url, {'quantity': 9})
        self.client.post(url, {'quantity': 1})
        self.assertEqual(self.client.get('/veleprodaja/korpa/').context['rows'][0]['quantity'], 9)
        WarehouseStock.objects.filter(product=self.product).update(kolicina=4)
        self.assertTrue(self.client.get('/veleprodaja/korpa/').context['rows'][0]['insufficient'])
        self.client.post('/veleprodaja/odjava/')
        self.assertNotIn('b2b_cart', self.client.session)

    def test_cart_variation_and_current_price(self):
        variation = ProductVariation.objects.create(artikal=self.product, naziv='Veliki', cijena=Decimal('322.92'))
        WarehouseStock.objects.create(product=self.product, variation=variation, location=self.location, kolicina=2)
        self.login()
        url = f'/veleprodaja/korpa/{self.product.pk}/'
        self.assertEqual(self.client.post(url).status_code, 400)
        self.assertEqual(self.client.post(url, {'variation_id': 999999}).status_code, 404)
        self.client.post(url, {'variation_id': variation.pk})
        self.assertEqual(self.client.get('/veleprodaja/korpa/').context['total'], Decimal('200.00'))
        ProductVariation.objects.filter(pk=variation.pk).update(cijena=Decimal('161.46'))
        self.assertEqual(self.client.get('/veleprodaja/korpa/').context['total'], Decimal('100.00'))

    def test_catalog_displays_only_available_and_netto(self):
        self.login()
        response = self.client.get('/veleprodaja')
        self.assertEqual(response.context['rows'][0]['available'], 9)
        self.assertContains(response, 'Dodaj u korpu')
        self.assertNotContains(response, 'MPC')
        self.assertContains(response, 'Šifra:')
        self.assertNotContains(response, self.location.label)

    def test_reference_layout_filters_and_cart_summary(self):
        from .models import Brand
        brand = Brand.objects.create(naziv='B2B Test Brand')
        Product.objects.filter(pk=self.product.pk).update(brend=brand)
        other = Product.objects.create(naziv='Drugi proizvod', cijena=Decimal('20'), kategorija=self.child)
        self.login()
        response = self.client.get('/veleprodaja', {'brend': brand.slug, 'limit': '25'})
        self.assertEqual([r['product'].pk for r in response.context['rows']], [self.product.pk])
        self.assertEqual(response.context['page'].paginator.per_page, 25)
        self.assertContains(response, 'product-table')
        self.assertContains(response, 'b2b-quantity-dialog')
        self.assertNotContains(response, '<th>Količina</th>')
        response = self.client.get('/veleprodaja', {'q': brand.naziv})
        self.assertEqual(len(response.context['rows']), 1)
        response = self.client.get('/veleprodaja', {'stanje': '1'})
        self.assertNotContains(response, other.naziv)
        response = self.client.get('/veleprodaja', {'sort': 'price'})
        self.assertEqual(response.context['rows'][0]['product'], self.product)
        self.assertEqual(response.context['rows'][-1]['product'], other)
        self.client.post(f'/veleprodaja/korpa/{self.product.pk}/', {'quantity': 2, 'brend': brand.slug, 'stanje': '1'})
        response = self.client.get('/veleprodaja')
        self.assertEqual(response.context['b2b_cart_count'], 2)
        self.assertEqual(response.context['b2b_cart_total'], Decimal('200.00'))

    def test_stock_filter_excludes_parent_stock_for_variations(self):
        ProductVariation.objects.create(artikal=self.product, naziv='Varijacija bez zaliha')
        self.login()
        response = self.client.get('/veleprodaja', {'stanje': '1'})
        self.assertEqual(response.context['rows'], [])

    def test_admin_banner_upload_and_custom_category_icon(self):
        import tempfile
        from io import BytesIO
        from PIL import Image
        from django.core.files.uploadedfile import SimpleUploadedFile
        from .models import B2BSettings, B2BCategoryIcon
        buffer = BytesIO()
        Image.new('RGB', (90, 30), '#ff5900').save(buffer, format='PNG')
        admin_user = get_user_model().objects.create_superuser('b2b-admin', 'admin@example.com', 'Admin-secret-723!')
        self.client.force_login(admin_user)
        with tempfile.TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=media_root):
            response = self.client.post('/admin/EcommerceApp/b2bsettings/add/', {
                'banner': SimpleUploadedFile('banner.png', buffer.getvalue(), content_type='image/png'),
                'banner_alt': 'Naš B2B banner',
                'banners-TOTAL_FORMS': '0', 'banners-INITIAL_FORMS': '0',
                'banners-MIN_NUM_FORMS': '0', 'banners-MAX_NUM_FORMS': '1000',
                'category_icons-TOTAL_FORMS': '1', 'category_icons-INITIAL_FORMS': '0',
                'category_icons-MIN_NUM_FORMS': '0', 'category_icons-MAX_NUM_FORMS': '1000',
                'category_icons-0-category': self.root.pk,
                'category_icons-0-image': SimpleUploadedFile('icon.png', buffer.getvalue(), content_type='image/png'),
                '_save': 'Sačuvaj',
            })
            self.assertEqual(response.status_code, 302)
            settings = B2BSettings.objects.get()
            icon = B2BCategoryIcon.objects.get()
            self.login()
            response = self.client.get('/veleprodaja')
            self.assertContains(response, settings.banner.url)
            self.assertContains(response, 'Naš B2B banner')
            self.assertNotContains(response, icon.image.url)
            self.assertNotContains(response, '<br>BIZNIS</strong>')
            settings.banner = ''
            settings.save()
            self.assertNotContains(self.client.get('/veleprodaja'), 'data-b2b-slider')

    def test_default_category_icons(self):
        from .b2b_icons import category_icon_name
        self.assertEqual(category_icon_name('Štapovi'), 'rod')
        self.assertEqual(category_icon_name('Mašinice'), 'reel')
        self.assertEqual(category_icon_name('Odjeća i obuća'), 'clothing')
        self.login()
        response = self.client.get('/veleprodaja')
        self.assertNotContains(response, 'img/b2b-icons/rod.svg')
        self.assertNotContains(response, 'img/b2b-icons/feeder.svg')
        self.assertContains(response, 'img/b2b-icons/gear.svg')

    def checkout_order(self, quantity=2, payment='ziralno'):
        self.login()
        self.client.post(f'/veleprodaja/korpa/{self.product.pk}/', {'quantity': quantity})
        response = self.client.get('/veleprodaja/zavrsi/')
        for field in ('telefon', 'email', 'adresa', 'grad'):
            self.assertNotContains(response, f'name="{field}"')
        self.assertNotContains(response, '<select')
        data = {'token': str(response.context['form']['token'].value()), 'payment': payment,
                'napomena': 'B2B napomena'}
        response = self.client.post('/veleprodaja/zavrsi/', data)
        self.assertEqual(response.status_code, 302)
        from .models import B2BSubmission
        return B2BSubmission.objects.get(), data

    def test_checkout_reserves_and_queues_once(self):
        from .models import Order, B2BSubmission
        from .views_magacin import collect_pick_jobs
        submission, data = self.checkout_order()
        stock = WarehouseStock.objects.get(product=self.product, location=self.location)
        self.assertEqual((stock.kolicina, stock.rezervisano), (12, 5))
        self.assertEqual(submission.order.ukupno, Decimal('234.00'))
        self.assertEqual(submission.netto_total, Decimal('200.00'))
        self.assertEqual(submission.order.lager_status, Order.LagerStatus.REZERVISANO)
        self.assertIn(submission.order.pk, [o.pk for o in collect_pick_jobs()])
        self.client.post('/veleprodaja/zavrsi/', data)
        self.assertEqual(B2BSubmission.objects.count(), 1)
        self.assertNotIn('b2b_cart', self.client.session)
        self.assertContains(self.client.get(f'/veleprodaja/narudzba/{submission.pk}/'), 'Žiralno')

    def test_picking_deducts_exact_locations_only_once(self):
        from .models import Order, WarehouseMovement
        from .magacin import validate_order_stock
        from .views_magacin import apply_order_pick
        submission, _ = self.checkout_order(payment='gotovina')
        order = submission.order
        item = order.stavke.get()
        second = WarehouseLocation.objects.create(sifra='B2B-B', naziv='Drugi magacin')
        WarehouseStock.objects.create(product=self.product, location=second, kolicina=10)
        lines = [{'key': f'{item.pk}:B', 'item_id': item.pk, 'loc': second.sifra, 'got': 2, 'need': 2, 'done': True}]
        apply_order_pick(order, lines, finalize=False)
        self.assertEqual(WarehouseStock.objects.get(product=self.product, location=second).kolicina, 10)
        apply_order_pick(order, lines, finalize=True)
        validate_order_stock(order)
        self.assertEqual(WarehouseStock.objects.get(product=self.product, location=second).kolicina, 8)
        original = WarehouseStock.objects.get(product=self.product, location=self.location)
        self.assertEqual((original.kolicina, original.rezervisano), (12, 3))
        self.assertEqual(order.lager_status, Order.LagerStatus.VALIDIRANO)
        validate_order_stock(order)
        self.assertEqual(WarehouseStock.objects.filter(product=self.product, location=second).get().kolicina, 8)
        self.assertEqual(WarehouseMovement.objects.filter(product=self.product, tip='prodaja').count(), 1)

    def test_picking_requires_confirmation_and_rolls_back_short_stock(self):
        from .magacin import validate_order_stock, MagacinError
        from .views_magacin import apply_order_pick
        submission, _ = self.checkout_order()
        order = submission.order
        with self.assertRaises(MagacinError):
            validate_order_stock(order)
        item = order.stavke.get()
        second = WarehouseLocation.objects.create(sifra='B2B-C', naziv='Prazan')
        WarehouseStock.objects.create(product=self.product, location=second, kolicina=0)
        apply_order_pick(order, [{'key': 'x', 'item_id': item.pk, 'loc': second.sifra, 'got': 2, 'done': True}], finalize=True)
        with self.assertRaises(MagacinError):
            validate_order_stock(order)
        stock = WarehouseStock.objects.get(product=self.product, location=self.location)
        self.assertEqual((stock.kolicina, stock.rezervisano), (12, 5))
        order.refresh_from_db()
        self.assertFalse(order.zapakovana)

    def test_partial_picking_charges_only_picked_quantity(self):
        from .magacin import validate_order_stock
        from .views_magacin import apply_order_pick
        submission, _ = self.checkout_order()
        order = submission.order
        item = order.stavke.get()
        apply_order_pick(order, [{'key': 'x', 'item_id': item.pk, 'loc': self.location.sifra, 'got': 1, 'done': True}], finalize=True)
        validate_order_stock(order)
        stock = WarehouseStock.objects.get(product=self.product, location=self.location)
        self.assertEqual((stock.kolicina, stock.rezervisano), (11, 3))
        self.assertEqual(order.ukupno, Decimal('117.00'))

    def test_checkout_requires_payment_and_current_stock(self):
        from .models import B2BSubmission
        self.login()
        self.client.post(f'/veleprodaja/korpa/{self.product.pk}/', {'quantity': 2})
        response = self.client.get('/veleprodaja/zavrsi/')
        data = {'token': str(response.context['form']['token'].value()), 'payment': '',
                'napomena': 'B2B napomena'}
        self.assertEqual(self.client.post('/veleprodaja/zavrsi/', data).status_code, 200)
        self.assertFalse(B2BSubmission.objects.exists())
        WarehouseStock.objects.filter(product=self.product).update(kolicina=3)
        data['payment'] = 'gotovina'
        response = self.client.post('/veleprodaja/zavrsi/', data)
        self.assertContains(response, 'Nedovoljno dostupne količine')
        self.assertFalse(B2BSubmission.objects.exists())

    def test_short_pick_confirmation_does_not_deduct_before_finish(self):
        from .views_magacin import confirm_short_pick
        from .magacin import validate_order_stock
        submission, _ = self.checkout_order()
        order = submission.order
        item = order.stavke.get()
        confirm_short_pick(order, item_id=item.pk, loc=self.location.sifra, got=1, user=None)
        stock = WarehouseStock.objects.get(product=self.product, location=self.location)
        self.assertEqual((stock.kolicina, stock.rezervisano), (12, 5))
        validate_order_stock(order)
        stock.refresh_from_db()
        self.assertEqual((stock.kolicina, stock.rezervisano), (11, 3))

    def test_confirmation_is_private_and_checkout_requires_login(self):
        submission, _ = self.checkout_order()
        self.client.post('/veleprodaja/odjava/')
        self.assertEqual(self.client.post('/veleprodaja/zavrsi/', {}).status_code, 302)
        second = B2BAccount(username='another-partner', company='Druga firma')
        second.set_password('Another-secret-723!')
        second.save()
        self.client.post('/veleprodaja', {'username': second.username, 'password': 'Another-secret-723!'})
        self.assertEqual(self.client.get(f'/veleprodaja/narudzba/{submission.pk}/').status_code, 404)

    def test_banner_slider_order_visibility_and_full_width(self):
        from .models import B2BSettings, B2BBanner
        settings = B2BSettings.objects.create(banner='b2b/banners/primary.png', banner_alt='Prvi banner', banner_link='/veleprodaja?kategorija=stapovi')
        B2BBanner.objects.create(settings=settings, image='b2b/banners/third.png', position=20)
        B2BBanner.objects.create(settings=settings, image='b2b/banners/second.png', position=10, link='https://example.com/ponuda')
        B2BBanner.objects.create(settings=settings, image='b2b/banners/hidden.png', active=False)
        self.login()
        response = self.client.get('/veleprodaja')
        self.assertEqual([b['image'].name for b in response.context['banners']],
                         ['b2b/banners/primary.png', 'b2b/banners/second.png', 'b2b/banners/third.png'])
        self.assertContains(response, 'data-b2b-slider')
        self.assertContains(response, 'href="/veleprodaja?kategorija=stapovi"')
        self.assertContains(response, 'href="https://example.com/ponuda"')
        self.assertContains(response, 'data-pause')
        self.assertNotContains(response, 'b2b/banners/hidden.png')
        self.assertNotContains(response, 'class="category-hero"')
        self.assertNotContains(response, 'Oprema za ribolov po veleprodajnim cijenama.')
        B2BBanner.objects.all().delete()
        self.assertNotContains(self.client.get('/veleprodaja'), 'data-pause')

    def test_ajax_cart_returns_totals_without_redirect_or_queued_messages(self):
        self.login()
        url = f'/veleprodaja/korpa/{self.product.pk}/'
        for count in [1, 2]:
            response = self.client.post(url, {'quantity': 1}, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.json()['ok'])
            self.assertEqual(response.json()['b2b_cart_count'], count)
            self.assertEqual(Decimal(response.json()['b2b_cart_total']), Decimal('100.00') * count)
            self.assertNotIn('Location', response)
        self.assertNotContains(self.client.get('/veleprodaja'), 'Artikal je dodat u korpu.')
        response = self.client.post(url, {'quantity': 100}, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(response.status_code, 409)
        self.assertFalse(response.json()['ok'])
        self.assertEqual(response.json()['b2b_cart_count'], 2)
        self.client.post('/veleprodaja/odjava/')
        response = self.client.post(url, {'quantity': 1}, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(response.status_code, 401)
        self.assertFalse(response.json()['ok'])

    def test_category_tree_expands_selected_ancestors_without_icons(self):
        self.login()
        response = self.client.get('/veleprodaja')
        root = next(n for n in response.context['navigation'] if n['category'] == self.root)
        self.assertFalse(root['expanded'])
        self.assertEqual(root['children'][0]['category'], self.child)
        self.assertContains(response, '<details class="category-branch"')
        self.assertContains(response, 'Svi artikli iz kategorije')
        self.assertContains(response, 'class="category-icon"', count=1)
        response = self.client.get('/veleprodaja', {'kategorija': self.child.slug})
        root = next(n for n in response.context['navigation'] if n['category'] == self.root)
        self.assertTrue(root['expanded'])
        self.assertTrue(root['children'][0]['current'])

    def test_stock_priority_in_all_sorts_and_across_variant_pages(self):
        sold_out = Product.objects.create(naziv='AAA nema zaliha', cijena=Decimal('1'), kategorija=self.child)
        variant_product = Product.objects.create(naziv='ZZZ varijacije', cijena=Decimal('2'), kategorija=self.child)
        empty_variant = ProductVariation.objects.create(artikal=variant_product, naziv='Bez zaliha')
        for index in range(26):
            variation = ProductVariation.objects.create(artikal=variant_product, naziv=f'Dostupna {index}')
            WarehouseStock.objects.create(product=variant_product, variation=variation, location=self.location, kolicina=1)
        self.login()
        for sort in ['newest', 'name', 'price', 'price_desc']:
            response = self.client.get('/veleprodaja', {'sort': sort, 'limit': '25'})
            self.assertEqual(response.context['page'].paginator.count, 29)
            self.assertEqual(len(response.context['rows']), 25)
            self.assertTrue(all(row['available'] > 0 for row in response.context['rows']))
            response = self.client.get('/veleprodaja', {'sort': sort, 'limit': '25', 'page': 2})
            rows = response.context['rows']
            self.assertEqual([bool(row['available']) for row in rows], [True, True, False, False])
            self.assertContains(response, 'Nije na stanju', count=2)
        response = self.client.get('/veleprodaja', {'stanje': '1', 'limit': '100'})
        self.assertEqual(response.context['page'].paginator.count, 27)
        self.assertTrue(all(row['available'] for row in response.context['rows']))

    @override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
    def test_access_request_emails_all_fields_without_creating_account(self):
        from django.core import mail
        data = {'company': 'Nova Firma', 'address': 'Ulica 12', 'city': 'Sarajevo',
                'jib': '1234567890123', 'phone': '061123456', 'contact_name': 'Kontakt Osoba'}
        before = B2BAccount.objects.count()
        response = self.client.get('/veleprodaja')
        self.assertContains(response, 'Zatraži pristup')
        self.assertContains(self.client.get('/veleprodaja/zatrazi-pristup/'), 'Kontakt ime')
        response = self.client.post('/veleprodaja/zatrazi-pristup/', data, follow=True)
        self.assertContains(response, 'Zahtjev je poslan')
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ['narudzbe@opremazaribolov.ba'])
        for value in data.values():
            self.assertIn(value, mail.outbox[0].body)
        self.assertEqual(B2BAccount.objects.count(), before)
        self.assertNotIn('b2b_account_id', self.client.session)
        self.client.post('/veleprodaja/zatrazi-pristup/', data)
        self.assertEqual(len(mail.outbox), 1)

    @override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
    def test_access_request_requires_fields_csrf_and_reports_email_failure(self):
        from unittest.mock import patch
        from django.core import mail
        url = '/veleprodaja/zatrazi-pristup/'
        self.assertEqual(Client(enforce_csrf_checks=True).post(url, {}).status_code, 403)
        response = self.client.post(url, {})
        self.assertEqual(set(response.context['form'].errors), {'company', 'address', 'city', 'jib', 'phone', 'contact_name'})
        self.assertEqual(len(mail.outbox), 0)
        data = {'company': 'Firma', 'address': 'Ulica 1', 'city': 'Mostar', 'jib': '1234567890123',
                'phone': '061123456', 'contact_name': 'Osoba'}
        with patch('django.core.mail.send_mail', side_effect=OSError('SMTP unavailable')):
            response = self.client.post(url, data)
        self.assertContains(response, 'Zahtjev trenutno nije moguće poslati')
        self.assertFalse(response.context['sent'])
        self.assertEqual(response.context['form']['company'].value(), 'Firma')
        self.client.post(url, data)
        self.assertEqual(len(mail.outbox), 1)

    def test_b2b_collections_filter_and_can_share_products(self):
        from .models import B2BSettings
        settings = B2BSettings.objects.create()
        second = Product.objects.create(naziv='Samo akcija', cijena=Decimal('20'), kategorija=self.child)
        settings.noviteti.add(self.product)
        settings.akcijska_ponuda.add(self.product, second)
        self.login()
        response = self.client.get('/veleprodaja', {'ponuda': 'noviteti'})
        self.assertEqual([r['product'].pk for r in response.context['rows']], [self.product.pk])
        self.assertContains(response, 'NOVITETI')
        self.assertContains(response, 'b2b-sale-button')
        self.assertContains(response, 'name="ponuda" value="noviteti"')
        response = self.client.get('/veleprodaja', {'ponuda': 'akcijska'})
        self.assertEqual({r['product'].pk for r in response.context['rows']}, {self.product.pk, second.pk})
        response = self.client.get('/veleprodaja', {'ponuda': 'akcijska', 'q': second.naziv})
        self.assertEqual([r['product'].pk for r in response.context['rows']], [second.pk])
        response = self.client.post(f'/veleprodaja/korpa/{self.product.pk}/', {'quantity': 1, 'ponuda': 'noviteti'})
        self.assertIn('ponuda=noviteti', response.url)
        settings.noviteti.clear()
        self.assertEqual(self.client.get('/veleprodaja', {'ponuda': 'noviteti'}).context['rows'], [])

    def test_b2b_admin_collections_use_search_and_save_multiple_products(self):
        from django.contrib import admin
        from .models import B2BSettings
        user = get_user_model().objects.create_superuser('collections-admin', 'a@example.com', 'Admin-secret-723!')
        self.client.force_login(user)
        second = Product.objects.create(naziv='Drugi novitet', cijena=Decimal('20'))
        response = self.client.post('/admin/EcommerceApp/b2bsettings/add/', {
            'noviteti': [self.product.pk, second.pk], 'akcijska_ponuda': [second.pk],
            'banners-TOTAL_FORMS': '0', 'banners-INITIAL_FORMS': '0',
            'category_icons-TOTAL_FORMS': '0', 'category_icons-INITIAL_FORMS': '0', '_save': 'Sačuvaj',
        })
        self.assertEqual(response.status_code, 302)
        settings = B2BSettings.objects.get()
        self.assertEqual(settings.noviteti.count(), 2)
        self.assertEqual(list(settings.akcijska_ponuda.all()), [second])
        self.assertEqual(admin.site._registry[B2BSettings].autocomplete_fields, ['noviteti', 'akcijska_ponuda'])
