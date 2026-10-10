from django.test import TestCase
from django.contrib.auth import get_user_model
from django.urls import reverse
from .models import Order, WMSOrder


class IndependentWMSTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser('wms-test', 'wms@example.invalid', 'test-pass')
        self.client.force_login(self.user)

    def test_legacy_orders_not_listed_in_wms(self):
        Order.objects.create(ime_prezime='LEGACY-CUSTOMER', email='old@example.invalid', ukupno=0, izvor=Order.Izvor.MAGACIN)
        for section in ('zalihe', 'narudzbe', 'pakovanje', 'lokacije'):
            response = self.client.get(reverse('staff_wms_section', args=[section]))
            self.assertEqual(response.status_code, 200)
            self.assertNotContains(response, 'LEGACY-CUSTOMER')
            workspace = response.content.decode().split('<div class="wms-layout', 1)[1].split('</main>', 1)[0]
            self.assertNotIn('/nalog/magacin/', workspace)

    def test_new_order_and_packaging_use_only_wms_records(self):
        before = Order.objects.count()
        from .models import Product
        product = Product.objects.create(naziv='Unos test', cijena=10)
        import json
        response = self.client.post(reverse('staff_wms_create_order', args=['online']), {'kupac': 'Novi WMS kupac', 'items': json.dumps([{'id': product.pk, 'quantity': 2}])})
        self.assertEqual(response.status_code, 302)
        order = WMSOrder.objects.get()
        self.assertEqual(order.tip, 'online')
        self.assertEqual(Order.objects.count(), before)
        self.client.post(reverse('staff_wms_section', args=['pakovanje']), {'order_id': order.pk})
        order.refresh_from_db()
        self.assertEqual(order.status, 'nova')
        self.assertContains(self.client.get(reverse('staff_wms_section', args=['pakovanje'])), 'Novi WMS kupac')

    def test_stock_search_by_name_code_and_barcode(self):
        from .models import Product
        product = Product.objects.create(naziv='WMS test rola', sifra='WMS-SEARCH-123', barkod='9876543210123', cijena=10, stanje=7)
        url = reverse('staff_wms_section', args=['zalihe'])
        self.assertNotContains(self.client.get(url), product.naziv)
        for query in ('test rola', 'WMS-SEARCH-123', '9876543210123'):
            response = self.client.get(url, {'q': query})
            self.assertContains(response, product.naziv)
            self.assertContains(response, product.barkod)
        self.assertContains(self.client.get(url, {'q': 'NO-MATCH-XYZ'}), 'Nema artikala')

    def test_quantity_requires_location_and_search_shows_it(self):
        from .models import Product, WMSLocation
        from .admin_forms import ProductTagsAdminForm
        location = WMSLocation.objects.create(naziv='Polica A1')
        product = Product.objects.create(naziv='Lokacijski artikal', cijena=10, stanje=8, wms_lokacija=location)
        response = self.client.get(reverse('staff_wms_section', args=['zalihe']), {'q': 'Lokacijski'})
        self.assertContains(response, 'Polica A1')
        self.assertContains(response, 'data-stock-location=')
        form = ProductTagsAdminForm(data={'naziv': 'Test', 'cijena': 10, 'stanje': 5, 'tagovi': ''})
        self.assertFalse(form.is_valid())
        self.assertIn('wms_lokacija', form.errors)

    def test_transfer_changes_only_wms_location_and_preserves_quantity(self):
        from .models import Product, WMSLocation, WMSSettings
        source = WMSLocation.objects.create(naziv='Izvor')
        destination = WMSLocation.objects.create(naziv='Odredište')
        WMSSettings.objects.create(pk=1, transfer_location=destination)
        product = Product.objects.create(naziv='Prenos test', cijena=10, stanje=9, wms_lokacija=source)
        url = reverse('staff_wms_section', args=['zalihe'])
        response = self.client.post(url, {'product_id': product.pk, 'quantity': 9, 'source_location': source.pk})
        self.assertEqual(response.status_code, 302)
        self._confirm_latest_transfer()
        product.refresh_from_db()
        self.assertEqual(product.wms_lokacija, destination)
        self.assertEqual(product.stanje, 9)
        self.client.post(url, {'product_id': product.pk, 'destination': source.pk, 'source_location': source.pk})
        product.refresh_from_db()
        self.assertEqual(product.wms_lokacija, destination)

    def test_product_can_be_distributed_between_locations(self):
        import json
        from .models import Product, WMSLocation, ProductWMSStock, WMSSettings
        from .admin_forms import ProductTagsAdminForm
        class AllocationForm(ProductTagsAdminForm):
            class Meta(ProductTagsAdminForm.Meta):
                fields = ['naziv', 'cijena', 'stanje', 'wms_lokacija']
        a = WMSLocation.objects.create(naziv='Polica A')
        b = WMSLocation.objects.create(naziv='Polica B')
        data = {'naziv': 'Raspored test', 'cijena': 10, 'stanje': 12, 'tagovi': '', 'wms_raspored': json.dumps([{'lokacija_id': a.pk, 'kolicina': 5}, {'lokacija_id': b.pk, 'kolicina': 7}])}
        form = AllocationForm(data=data)
        self.assertTrue(form.is_valid(), form.errors)
        product = form.save()
        self.assertEqual(product.wms_zalihe.count(), 2)
        self.assertEqual(sum(product.wms_zalihe.values_list('kolicina', flat=True)), product.stanje)
        url = reverse('staff_wms_section', args=['zalihe'])
        response = self.client.get(url, {'q': product.naziv})
        self.assertContains(response, 'Polica A')
        self.assertContains(response, 'Polica B')
        WMSSettings.objects.create(pk=1, transfer_location=b)
        stock = product.wms_zalihe.get(lokacija=a)
        self.client.post(url, {'product_id': product.pk, 'stock_id': stock.pk, 'source_location': a.pk, 'quantity': 5})
        self._confirm_latest_transfer()
        self.assertEqual(product.wms_zalihe.get(lokacija=b).kolicina, 12)
        self.assertEqual(product.wms_zalihe.count(), 1)

    def test_settings_and_partial_transfer(self):
        from .models import Product, WMSLocation, WMSSettings, ProductWMSStock
        source = WMSLocation.objects.create(naziv='Izvor djelimično')
        destination = WMSLocation.objects.create(naziv='Odredište djelimično')
        settings_url = reverse('staff_wms_section', args=['podesavanje'])
        self.client.post(settings_url, {'transfer_location': destination.pk})
        self.assertEqual(WMSSettings.objects.get(pk=1).transfer_location, destination)
        product = Product.objects.create(naziv='Djelimični prenos', cijena=10, stanje=9, wms_lokacija=source)
        url = reverse('staff_wms_section', args=['zalihe'])
        self.client.post(url, {'product_id': product.pk, 'source_location': source.pk, 'quantity': 3, 'destination': source.pk})
        self._confirm_latest_transfer()
        self.assertEqual(product.wms_zalihe.get(lokacija=source).kolicina, 6)
        self.assertEqual(product.wms_zalihe.get(lokacija=destination).kolicina, 3)
        stock = product.wms_zalihe.get(lokacija=source)
        for quantity in (0, -1, 7, 'bad'):
            self.client.post(url, {'product_id': product.pk, 'stock_id': stock.pk, 'source_location': source.pk, 'quantity': quantity})
        stock.refresh_from_db()
        self.assertEqual(stock.kolicina, 6)
        product.refresh_from_db()
        self.assertEqual(product.stanje, 9)
        self.assertEqual(sum(product.wms_zalihe.values_list('kolicina', flat=True)), 9)
        response = self.client.get(url, {'q': product.naziv})
        self.assertContains(response, 'name="quantity"')
        self.assertNotContains(response, 'name="destination"')

    def test_module_sections_lock_direct_urls_and_show_locks(self):
        from .models import ModulePermissions
        from .views_wms import WMS_MODULES
        ModulePermissions.objects.bulk_create([ModulePermissions(pk=1)])
        for section, field in WMS_MODULES.items():
            ModulePermissions.objects.filter(pk=1).update(**{field: False})
            self.assertEqual(self.client.get(reverse('staff_wms_section', args=[section])).status_code, 302)
            response = self.client.get(reverse('staff_wms_section', args=['podesavanje']))
            self.assertContains(response, 'aria-label="Zaključano"')
            ModulePermissions.objects.filter(pk=1).update(**{field: True})
            self.assertEqual(self.client.get(reverse('staff_wms_section', args=[section])).status_code, 200)
        ModulePermissions.objects.filter(pk=1).update(wms_narudzbe=False)
        self.assertEqual(self.client.get(reverse('staff_wms_create_order', args=['online'])).status_code, 403)

    def test_module_admin_only_displays_wms_options(self):
        from .models import ModulePermissions
        ModulePermissions.objects.bulk_create([ModulePermissions(pk=1)])
        response = self.client.get(reverse('admin:EcommerceApp_modulepermissions_change', args=[1]))
        self.assertContains(response, 'data-module-open="wmsModuleDialog"')
        for field in ('wms_zalihe', 'wms_narudzbe', 'wms_pakovanje', 'wms_prenosnice'):
            self.assertContains(response, f'name="{field}"')
        self.assertNotContains(response, 'name="loyalty"')
        self.assertNotContains(response, 'name="pracenje_lagera"')

    def test_locked_modules_hide_related_controls_and_block_posts(self):
        from .models import ModulePermissions, WMSLocation, WMSSettings, Product, ProductWMSStock
        ModulePermissions.objects.bulk_create([ModulePermissions(pk=1, wms_prenosnice=False)])
        source = WMSLocation.objects.create(naziv='Zaključani izvor')
        target = WMSLocation.objects.create(naziv='Zaključano odredište')
        WMSSettings.objects.create(pk=1, transfer_location=target)
        product = Product.objects.create(naziv='Zaključani test', cijena=10, stanje=5)
        stock = ProductWMSStock.objects.create(product=product, lokacija=source, kolicina=5)
        url = reverse('staff_wms_section', args=['zalihe'])
        response = self.client.get(url, {'q': product.naziv})
        self.assertNotContains(response, 'class="wms-transfer-toggle"')
        self.assertNotContains(response, 'name="quantity"')
        data = {'product_id': product.pk, 'stock_id': stock.pk, 'source_location': source.pk, 'quantity': 2}
        self.assertEqual(self.client.post(url, data).status_code, 403)
        settings_url = reverse('staff_wms_section', args=['podesavanje'])
        self.assertNotContains(self.client.get(settings_url), 'name="transfer_location"')
        self.assertEqual(self.client.post(settings_url, {'transfer_location': target.pk}).status_code, 403)
        ModulePermissions.objects.filter(pk=1).update(wms_prenosnice=True, wms_zalihe=False)
        response = self.client.get(url, {'q': product.naziv}, follow=True)
        self.assertNotContains(response, f'data-stock-location="wmsStockLocation{product.pk}"')
        self.assertNotContains(response, source.naziv)
        self.assertEqual(self.client.post(url, data).status_code, 403)
        stock.refresh_from_db()
        self.assertEqual(stock.kolicina, 5)
        ModulePermissions.objects.filter(pk=1).update(wms_zalihe=True)
        self.assertContains(self.client.get(url, {'q': product.naziv}), 'class="wms-transfer-toggle"')
        self.assertEqual(self.client.post(url, data).status_code, 302)
        self._confirm_latest_transfer()
        stock.refresh_from_db()
        self.assertEqual(stock.kolicina, 3)


    def test_locked_current_section_redirects_to_first_available(self):
        from .models import ModulePermissions
        ModulePermissions.objects.bulk_create([ModulePermissions(pk=1, wms_zalihe=False)])
        response = self.client.get(reverse('staff_wms_section', args=['zalihe']))
        self.assertRedirects(response, reverse('staff_wms_section', args=['narudzbe']))
        ModulePermissions.objects.filter(pk=1).update(wms_narudzbe=False)
        response = self.client.get(reverse('staff_wms_section', args=['zalihe']))
        self.assertRedirects(response, reverse('staff_wms_section', args=['pakovanje']))
        ModulePermissions.objects.filter(pk=1).update(wms_pakovanje=False, wms_prenosnice=False, wms_zalihe=False)
        response = self.client.get(reverse('staff_wms_section', args=['zalihe']))
        self.assertRedirects(response, reverse('staff_wms'))
        self.assertEqual(self.client.post(reverse('staff_wms_section', args=['zalihe']), {}).status_code, 403)


    def test_open_page_permission_check_returns_redirect_without_error(self):
        from .models import ModulePermissions
        ModulePermissions.objects.bulk_create([ModulePermissions(pk=1, wms_zalihe=False)])
        response = self.client.get(reverse('staff_wms_section', args=['zalihe']), HTTP_X_WMS_PERMISSIONS='1')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['redirect'], reverse('staff_wms_section', args=['narudzbe']))
        self.assertEqual(response['Cache-Control'], 'no-store')
        ModulePermissions.objects.filter(pk=1).update(wms_zalihe=True)
        response = self.client.get(reverse('staff_wms_section', args=['zalihe']), HTTP_X_WMS_PERMISSIONS='1')
        self.assertIsNone(response.json()['redirect'])


    def test_all_locked_stays_on_wms_without_open_section(self):
        from .models import ModulePermissions
        ModulePermissions.objects.bulk_create([ModulePermissions(pk=1, wms_zalihe=False, wms_narudzbe=False, wms_pakovanje=False, wms_prenosnice=False)])
        response = self.client.get(reverse('staff_wms'))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'staff/wms/empty.html')
        self.assertEqual(response.context['wms_section'], '')
        self.assertNotContains(response, 'name="transfer_location"')
        response = self.client.get(reverse('staff_wms'), HTTP_X_WMS_PERMISSIONS='1')
        self.assertIsNone(response.json()['redirect'])
        response = self.client.get(reverse('staff_wms_section', args=['zalihe']), HTTP_X_WMS_PERMISSIONS='1')
        self.assertEqual(response.json()['redirect'], reverse('staff_wms'))

    def test_storefront_availability_setting_and_stock_module(self):
        from .models import WMSSettings, ModulePermissions, Product
        from django.template import Template, Context
        product = Product.objects.create(naziv='Prikaz dostupnosti', cijena=10, stanje=8, na_stanju=True)
        settings_url = reverse('staff_wms_section', args=['podesavanje'])
        self.assertContains(self.client.get(settings_url), 'name="show_storefront_availability"')
        self.client.post(settings_url, {'show_storefront_availability': 'on'})
        self.assertTrue(WMSSettings.objects.get(pk=1).show_storefront_availability)
        template = Template('{% include "partials/product_stock_label.html" %}')
        self.assertIn('product-stock-label', template.render(Context({'product': product})))
        self.client.post(settings_url, {})
        self.assertFalse(WMSSettings.objects.get(pk=1).show_storefront_availability)
        self.assertNotIn('product-stock-label', template.render(Context({'product': product})))
        ModulePermissions.objects.bulk_create([ModulePermissions(pk=1, wms_zalihe=False)])
        self.assertNotContains(self.client.get(settings_url), 'name="show_storefront_availability"')
        self.assertEqual(self.client.post(settings_url, {'show_storefront_availability': 'on'}).status_code, 403)


    def test_stock_and_locations_share_one_module_switch(self):
        from .models import ModulePermissions
        ModulePermissions.objects.bulk_create([ModulePermissions(pk=1, wms_zalihe=False)])
        for section in ('zalihe', 'lokacije'):
            response = self.client.get(reverse('staff_wms_section', args=[section]))
            self.assertEqual(response.status_code, 302)
            self.assertEqual(self.client.post(reverse('staff_wms_section', args=[section]), {}).status_code, 403)
        ModulePermissions.objects.filter(pk=1).update(wms_zalihe=True)
        for section in ('zalihe', 'lokacije'):
            self.assertEqual(self.client.get(reverse('staff_wms_section', args=[section])).status_code, 200)
        response = self.client.get(reverse('admin:EcommerceApp_modulepermissions_change', args=[1]))
        self.assertContains(response, 'Zalihe i Lokacije')
        self.assertNotContains(response, 'name="wms_lokacije"')

    def test_enabled_stock_module_caps_cart_and_shows_exact_quantity(self):
        from .models import ModulePermissions, Product, WMSSettings
        from .cart import Cart, stock_on_hand
        from django.test import RequestFactory
        from django.contrib.sessions.backends.db import SessionStore
        from django.template import Template, Context
        ModulePermissions.objects.create(pk=1, wms_zalihe=True, pracenje_lagera=False)
        product = Product.objects.create(naziv='Limit tri komada', cijena=10, stanje=3)
        self.assertTrue(product.pracenje_zaliha)
        self.assertEqual(stock_on_hand(product), 3)
        request = RequestFactory().get('/')
        request.session = SessionStore()
        cart = Cart(request)
        self.assertEqual(cart.add(product, quantity=5), 3)
        self.assertEqual(cart.add(product, quantity=1), 0)
        product.stanje = 2
        product.save()
        changed, _ = cart.clamp_to_stock()
        self.assertTrue(changed)
        self.assertEqual(cart.qty_for_sku(product.pk), 2)
        WMSSettings.objects.create(pk=1, show_storefront_availability=True)
        html = Template('{% include "partials/product_stock_label.html" %}').render(Context({'product': product}))
        self.assertIn('Na stanju <span class="storefront-stock-quantity">2 kom</span>', html)
        settings = ModulePermissions.objects.get(pk=1)
        settings.wms_zalihe = False
        settings.save()
        product.refresh_from_db()
        self.assertFalse(product.pracenje_zaliha)

    def test_actions_module_controls_panel_and_active_offers(self):
        from .models import ModulePermissions, Akcija
        config = ModulePermissions.objects.create(pk=1, akcije=False)
        offer = Akcija.objects.create(aktivan=True, tip=Akcija.Tip.PONUDA)
        self.assertFalse(offer.jos_traje())
        editor = '/panel/podesavanja/sekcije/EcommerceApp/akcija/add/'
        self.assertEqual(self.client.get(editor).status_code, 403)
        before = Akcija.objects.count()
        self.assertEqual(self.client.post(editor, {'aktivan': 'on'}).status_code, 403)
        self.assertEqual(Akcija.objects.count(), before)
        config.akcije = True
        config.save()
        self.assertTrue(offer.jos_traje())
        self.assertEqual(self.client.get(editor).status_code, 200)
        response = self.client.get(reverse('admin:EcommerceApp_modulepermissions_change', args=[1]))
        self.assertContains(response, 'name="akcije"')


    def test_empty_submitted_search_lists_all_products(self):
        from .models import Product
        a = Product.objects.create(naziv='Sve zalihe A', cijena=10)
        b = Product.objects.create(naziv='Sve zalihe B', cijena=20)
        url = reverse('staff_wms_section', args=['zalihe'])
        self.assertNotContains(self.client.get(url), a.naziv)
        response = self.client.get(url, {'q': ''})
        self.assertContains(response, a.naziv)
        self.assertContains(response, b.naziv)
        self.assertContains(response, 'Svi artikli')
        self.assertContains(self.client.get(url, {'q': '   '}), a.naziv)

    def test_zero_stock_is_visible_with_notify_only_and_cannot_be_bought(self):
        from .models import ModulePermissions, Product
        from .cart import Cart
        from django.test import RequestFactory
        from django.contrib.sessions.backends.db import SessionStore
        from .views import _product_queryset, _home_product_queryset
        ModulePermissions.objects.create(pk=1, wms_zalihe=True)
        product = Product.objects.create(naziv='Rasprodat vidljiv', cijena=10, stanje=0)
        self.assertFalse(product.na_stanju)
        self.assertFalse(product.modul_sakriven)
        self.assertTrue(_product_queryset().filter(pk=product.pk).exists())
        self.assertTrue(_home_product_queryset().filter(pk=product.pk).exists())
        response = self.client.get(reverse('product_detail', args=[product.slug]))
        self.assertContains(response, 'Obavijesti pri dolasku')
        self.assertNotContains(response, 'id="mainAddToCartForm"')
        request = RequestFactory().get('/')
        request.session = SessionStore()
        self.assertEqual(Cart(request).add(product, quantity=1), 0)


    def _confirm_latest_transfer(self):
        from .models import WMSTransfer
        return self.client.post(reverse('staff_wms_section', args=['prenosnica']), {'transfer_id': WMSTransfer.objects.first().pk})

    def test_transfer_document_moves_stock_only_when_confirmed_once(self):
        from .models import Product, WMSLocation, ProductWMSStock, WMSSettings, WMSTransfer
        source = WMSLocation.objects.create(naziv='Dokument izvor')
        destination = WMSLocation.objects.create(naziv='Dokument odredište')
        changed_destination = WMSLocation.objects.create(naziv='Kasnije odredište')
        WMSSettings.objects.create(pk=1, transfer_location=destination)
        product = Product.objects.create(naziv='Artikal na prenosnici', sifra='DOC-123', cijena=10, stanje=8)
        stock = ProductWMSStock.objects.create(product=product, lokacija=source, kolicina=8)
        url = reverse('staff_wms_section', args=['zalihe'])
        self.client.post(url, {'product_id': product.pk, 'source_location': source.pk, 'stock_id': stock.pk, 'quantity': 3})
        transfer = WMSTransfer.objects.get()
        stock.refresh_from_db()
        self.assertEqual(stock.kolicina, 8)
        self.assertFalse(product.wms_zalihe.filter(lokacija=destination).exists())
        page = self.client.get(reverse('staff_wms_section', args=['prenosnica']))
        self.assertContains(page, product.naziv)
        self.assertContains(page, 'DOC-123')
        self.assertContains(page, source.naziv)
        self.assertContains(page, 'Prenijeto')
        WMSSettings.objects.filter(pk=1).update(transfer_location=changed_destination)
        self._confirm_latest_transfer()
        self._confirm_latest_transfer()
        stock.refresh_from_db()
        self.assertEqual(stock.kolicina, 5)
        self.assertEqual(product.wms_zalihe.get(lokacija=destination).kolicina, 3)
        self.assertFalse(product.wms_zalihe.filter(lokacija=changed_destination).exists())
        transfer.refresh_from_db()
        self.assertIsNotNone(transfer.completed_at)
        product.refresh_from_db()
        self.assertEqual(product.stanje, 8)

    def test_transfer_confirmation_rechecks_available_stock(self):
        from .models import Product, WMSLocation, ProductWMSStock, WMSTransfer
        source = WMSLocation.objects.create(naziv='Nedovoljan izvor')
        destination = WMSLocation.objects.create(naziv='Nedovoljno odredište')
        product = Product.objects.create(naziv='Nedovoljno stanje', cijena=10, stanje=2)
        ProductWMSStock.objects.create(product=product, lokacija=source, kolicina=2)
        transfer = WMSTransfer.objects.create(product=product, source=source, destination=destination, quantity=3)
        self._confirm_latest_transfer()
        transfer.refresh_from_db()
        self.assertIsNone(transfer.completed_at)
        self.assertFalse(product.wms_zalihe.filter(lokacija=destination).exists())
        self.assertEqual(product.wms_zalihe.get(lokacija=source).kolicina, 2)

    def test_transfer_filters_default_to_active(self):
        from .models import Product, WMSLocation, WMSTransfer
        from django.utils import timezone
        source = WMSLocation.objects.create(naziv='Filter izvor')
        destination = WMSLocation.objects.create(naziv='Filter odredište')
        active_product = Product.objects.create(naziv='Aktivni dokument test', cijena=10)
        completed_product = Product.objects.create(naziv='Prenijeti dokument test', cijena=10)
        WMSTransfer.objects.create(product=active_product, source=source, destination=destination, quantity=1)
        WMSTransfer.objects.create(product=completed_product, source=source, destination=destination, quantity=1, completed_at=timezone.now())
        url = reverse('staff_wms_section', args=['prenosnica'])
        response = self.client.get(url)
        self.assertEqual(response.context['transfer_filter'], 'aktivne')
        self.assertContains(response, active_product.naziv)
        self.assertNotContains(response, completed_product.naziv)
        response = self.client.get(url, {'status': 'prenijete'})
        self.assertContains(response, completed_product.naziv)
        self.assertNotContains(response, active_product.naziv)

    def test_order_number_icons_edit_and_pack_actions(self):
        from .models import WMSOrder, ModulePermissions
        order = WMSOrder.objects.create(kupac='Ikonice kupac', tip='online')
        self.assertEqual(order.display_number, f'{order.pk:04d}')
        page = self.client.get(reverse('staff_wms_section', args=['narudzbe']))
        self.assertContains(page, '#' + order.display_number)
        self.assertContains(page, 'aria-label="Pogledaj narudžbu"')
        self.assertContains(page, 'aria-label="Pošalji na odvajanje robe"')
        self.assertContains(page, 'aria-label="Izmijeni narudžbu"')
        self.client.post(reverse('staff_wms_edit_order', args=[order.pk]), {'kupac': 'Izmijenjen kupac'})
        order.refresh_from_db()
        self.assertEqual(order.kupac, 'Izmijenjen kupac')
        packing = reverse('staff_wms_pack_order', args=[order.pk])
        self.assertEqual(self.client.get(packing).status_code, 405)
        self.client.post(packing)
        order.refresh_from_db()
        self.assertEqual(order.status, 'pakovanje')
        ModulePermissions.objects.bulk_create([ModulePermissions(pk=1, wms_pakovanje=False)])
        self.assertEqual(self.client.post(packing).status_code, 403)

    def test_cancel_icon_preserves_order_and_removes_from_packaging(self):
        order = WMSOrder.objects.create(kupac='Kupac otkazivanje', tip='online', status='pakovanje')
        url = reverse('staff_wms_cancel_order', args=[order.pk])
        self.assertEqual(self.client.get(url).status_code, 405)
        self.assertContains(self.client.get(reverse('staff_wms_section', args=['narudzbe']), {'status': 'pakovanje'}), 'aria-label="Otkaži narudžbu"')
        self.assertEqual(self.client.post(url, {'reason': 'Kupac je odustao'}).status_code, 302)
        order.refresh_from_db()
        self.assertEqual(order.status, 'otkazana')
        self.assertNotContains(self.client.get(reverse('staff_wms_section', args=['pakovanje'])), order.kupac)
        self.client.post(reverse('staff_wms_pack_order', args=[order.pk]))
        order.refresh_from_db()
        self.assertEqual(order.status, 'otkazana')
    def test_cancellation_requires_reason_and_preserves_original_reason(self):
        order = WMSOrder.objects.create(kupac='Razlog kupac', tip='online')
        url = reverse('staff_wms_cancel_order', args=[order.pk])
        for reason in ('', '   ', 'x' * 2001):
            self.client.post(url, {'reason': reason})
            order.refresh_from_db()
            self.assertEqual(order.status, 'nova')
        self.client.post(url, {'reason': '  Kupac je odustao  '})
        order.refresh_from_db()
        self.assertEqual(order.cancellation_reason, 'Kupac je odustao')
        self.client.post(url, {'reason': 'Drugi razlog'})
        order.refresh_from_db()
        self.assertEqual(order.cancellation_reason, 'Kupac je odustao')
        self.assertContains(self.client.get(reverse('staff_wms_order', args=[order.pk])), 'Kupac je odustao')

    def test_order_status_filters(self):
        orders = {status: WMSOrder.objects.create(kupac='Kupac ' + status, tip='online', status=status)
                  for status in ('nova', 'pakovanje', 'otkazana', 'zapakovana')}
        url = reverse('staff_wms_section', args=['narudzbe'])
        for params, expected in (({}, 'nova'), ({'status': 'aktivna'}, 'nova'),
                                 ({'status': 'pakovanje'}, 'pakovanje'),
                                 ({'status': 'otkazana'}, 'otkazana'),
                                 ({'status': 'zavrsene'}, 'zapakovana'),
                                 ({'status': 'invalid'}, 'nova')):
            page = self.client.get(url, params)
            self.assertContains(page, orders[expected].kupac)
            for status, order in orders.items():
                if status != expected:
                    self.assertNotContains(page, order.kupac)

    def test_panel_orders_shared_with_wms_and_removed_when_not_active(self):
        from .wms_orders import panel_active_orders
        for action in ('pack', 'cancel'):
            source = Order.objects.create(ime_prezime='Web kupac ' + action,
                                          email='web@example.invalid', ukupno=25)
            self.assertTrue(panel_active_orders().filter(pk=source.pk).exists())
            page = self.client.get(reverse('staff_wms_section', args=['narudzbe']))
            self.assertContains(page, source.ime_prezime)
            linked = WMSOrder.objects.get(source_order=source)
            self.client.get(reverse('staff_wms_section', args=['narudzbe']))
            self.assertEqual(WMSOrder.objects.filter(source_order=source).count(), 1)
            preview = self.client.get(reverse('staff_wms_order', args=[linked.pk]))
            self.assertEqual(preview.url, reverse('staff_order_detail', args=[source.broj]) + '?preview=email')
            if action == 'pack':
                self.client.post(reverse('staff_wms_pack_order', args=[linked.pk]))
            else:
                self.client.post(reverse('staff_wms_cancel_order', args=[linked.pk]), {'reason': 'Kupac odustao'})
            self.assertFalse(panel_active_orders().filter(pk=source.pk).exists())
            source.refresh_from_db()
            self.assertEqual(source.status, Order.Status.NOVA)

    def test_restore_cancelled_order_to_active_and_panel(self):
        from .wms_orders import panel_active_orders
        from .models import ModulePermissions
        source = Order.objects.create(ime_prezime='Povrat kupca', email='return@example.invalid', ukupno=10)
        order = WMSOrder.objects.create(source_order=source, kupac=source.ime_prezime, tip='online', status='otkazana', cancellation_reason='Odustao')
        page = self.client.get(reverse('staff_wms_section', args=['narudzbe']), {'status': 'otkazana'})
        self.assertContains(page, 'aria-label="Vrati narudžbu u aktivne"')
        self.assertNotContains(page, 'aria-label="Izmijeni narudžbu"')
        self.assertFalse(panel_active_orders().filter(pk=source.pk).exists())
        url = reverse('staff_wms_restore_order', args=[order.pk])
        self.assertEqual(self.client.get(url).status_code, 405)
        response = self.client.post(url)
        self.assertEqual(response.url, reverse('staff_wms_section', args=['narudzbe']) + '?status=aktivna')
        order.refresh_from_db()
        self.assertEqual(order.status, 'nova')
        self.assertTrue(panel_active_orders().filter(pk=source.pk).exists())
        self.assertContains(self.client.get(response.url), order.kupac)
        order.status = 'pakovanje'
        order.save(update_fields=['status'])
        self.client.post(url)
        order.refresh_from_db()
        self.assertEqual(order.status, 'pakovanje')
        ModulePermissions.objects.bulk_create([ModulePermissions(pk=1, wms_narudzbe=False)])
        self.assertEqual(self.client.post(url).status_code, 403)

    def test_cancelled_orders_show_notes(self):
        order = WMSOrder.objects.create(kupac='Kupac s napomenom', tip='online', status='otkazana',
                                        cancellation_reason='Nema odgovora', napomena='Pozvati sutra')
        page = self.client.get(reverse('staff_wms_section', args=['narudzbe']), {'status': 'otkazana'})
        self.assertContains(page, 'Nema odgovora')
        self.assertContains(page, 'Pozvati sutra')
        self.assertContains(page, '?status=zavrsene')

    def test_start_picking_shows_location_quantities_without_moving_stock(self):
        from .models import Product, OrderItem, WMSLocation, ProductWMSStock
        product = Product.objects.create(naziv='Rola za odvajanje', cijena=10, stanje=5)
        first = WMSLocation.objects.create(naziv='A1')
        second = WMSLocation.objects.create(naziv='B2')
        ProductWMSStock.objects.create(product=product, lokacija=first, kolicina=2)
        ProductWMSStock.objects.create(product=product, lokacija=second, kolicina=3)
        source = Order.objects.create(ime_prezime='Kupac odvajanje', email='pick@example.invalid', ukupno=40)
        OrderItem.objects.create(narudzba=source, artikal=product, naziv=product.naziv, cijena=10, kolicina=4)
        order = WMSOrder.objects.create(source_order=source, kupac=source.ime_prezime, tip='online')
        listing = self.client.get(reverse('staff_wms_section', args=['pakovanje']))
        self.assertContains(listing, 'Započni')
        self.assertNotContains(listing, 'aria-label="Izmijeni narudžbu"')
        url = reverse('staff_wms_pick_order', args=[order.pk])
        self.client.post(url)
        page = self.client.get(url)
        self.assertContains(page, source.ime_prezime)
        self.assertContains(page, source.broj)
        self.assertEqual(page.context['rows'][0]['locations'], [{'name': 'A1', 'quantity': 2, 'id': first.pk}, {'name': 'B2', 'quantity': 2, 'id': second.pk}])
        product.refresh_from_db()
        self.assertEqual(product.stanje, 5)
        order.refresh_from_db()
        self.assertEqual(order.status, 'pakovanje')

    def test_picking_requires_all_lines_and_persists_progress(self):
        import io
        import tempfile
        from PIL import Image
        from django.core.files.uploadedfile import SimpleUploadedFile
        from django.test import override_settings
        from .models import Product, OrderItem, WMSLocation, ProductWMSStock
        with tempfile.TemporaryDirectory() as media, override_settings(MEDIA_ROOT=media):
            product = Product.objects.create(naziv='Fotografisani artikal', cijena=10, stanje=3)
            location = WMSLocation.objects.create(naziv='Foto A1')
            ProductWMSStock.objects.create(product=product, lokacija=location, kolicina=3)
            source = Order.objects.create(ime_prezime='Foto kupac', email='photo@example.invalid', ukupno=20)
            OrderItem.objects.create(narudzba=source, artikal=product, naziv=product.naziv, cijena=10, kolicina=2)
            order = WMSOrder.objects.create(source_order=source, kupac=source.ime_prezime, tip='online')
            url = reverse('staff_wms_pick_order', args=[order.pk])
            started = self.client.post(url, {'action': 'start'})
            self.assertIn('?line=', started.url)
            self.assertNotContains(self.client.get(started.url), 'Fotografija (obavezno)')
            self.assertNotContains(self.client.get(started.url), 'Potvrdi i nastavi')
            self.assertContains(self.client.get(started.url), 'Potvrdi količinu')
            self.client.post(url, {'action': 'start'})
            self.assertEqual(order.pick_lines.count(), 1)
            line = order.pick_lines.get()
            self.client.post(url, {'action': 'finish'})
            order.refresh_from_db()
            self.assertEqual(order.status, 'pakovanje')
            self.client.post(url, {'action': 'confirm', 'line_id': line.pk, 'quantity': 3})
            line.refresh_from_db()
            self.assertIsNone(line.confirmed_at)
            self.client.post(url, {'action': 'confirm', 'line_id': line.pk, 'quantity': 1,
                                   'photo': SimpleUploadedFile('invalid.jpg', b'not an image', content_type='image/jpeg')})
            line.refresh_from_db()
            self.assertIsNone(line.confirmed_at)
            image = io.BytesIO()
            Image.new('RGB', (20, 20), 'blue').save(image, format='JPEG')
            response = self.client.post(url, {'action': 'confirm', 'line_id': line.pk, 'quantity': 2,
                                   })
            line.refresh_from_db()
            self.assertEqual(line.confirmed_quantity, 2)
            self.assertFalse(line.photo)
            self.assertIsNotNone(line.confirmed_at)
            self.assertNotContains(self.client.get(response.url), 'Količina potvrđena')
            self.assertContains(self.client.get(response.url), 'Završi odvajanje')
            self.assertTrue(self.client.get(url).context['can_finish'])
            product.refresh_from_db()
            self.assertEqual(product.stanje, 3)
            self.assertEqual(ProductWMSStock.objects.get(product=product, lokacija=location).kolicina, 3)
            self.client.post(url, {'action': 'finish'})
            order.refresh_from_db()
            self.assertEqual(order.status, 'zapakovana')
            product.refresh_from_db()
            self.assertEqual(product.stanje, 1)
            stock = ProductWMSStock.objects.get(product=product, lokacija=location)
            self.assertEqual(stock.kolicina, 1)
            self.client.post(url, {'action': 'finish'})
            product.refresh_from_db()
            self.assertEqual(product.stanje, 1)

    def test_finish_insufficient_location_stock_changes_nothing(self):
        from .models import Product, OrderItem, WMSLocation, ProductWMSStock, WMSPickLine
        from django.utils import timezone
        product = Product.objects.create(naziv='Nedovoljna zaliha', cijena=10, stanje=4)
        source = Order.objects.create(ime_prezime='Kupac zaliha', email='stock@example.invalid', ukupno=40)
        item = OrderItem.objects.create(narudzba=source, artikal=product, naziv=product.naziv, cijena=10, kolicina=4)
        order = WMSOrder.objects.create(source_order=source, kupac=source.ime_prezime, tip='online', status='pakovanje')
        for name, available in [('A', 2), ('B', 1)]:
            location = WMSLocation.objects.create(naziv=name)
            ProductWMSStock.objects.create(product=product, lokacija=location, kolicina=available)
            WMSPickLine.objects.create(order=order, item=item, location=location, quantity=2, confirmed_quantity=2, confirmed_at=timezone.now())
        self.client.post(reverse('staff_wms_pick_order', args=[order.pk]), {'action': 'finish'})
        order.refresh_from_db()
        product.refresh_from_db()
        self.assertEqual(order.status, 'pakovanje')
        self.assertEqual(product.stanje, 4)
        self.assertEqual(list(product.wms_zalihe.order_by('lokacija__naziv').values_list('kolicina', flat=True)), [2, 1])

    def test_picking_cycles_only_unconfirmed_lines(self):
        from .models import Product, OrderItem, WMSLocation, WMSPickLine
        from django.utils import timezone
        source = Order.objects.create(ime_prezime='Kruzenje kupac', email='cycle@example.invalid', ukupno=30)
        order = WMSOrder.objects.create(source_order=source, kupac=source.ime_prezime, tip='online', status='pakovanje')
        location = WMSLocation.objects.create(naziv='Krug A')
        lines = []
        for index in range(3):
            product = Product.objects.create(naziv='Krug ' + str(index), cijena=10, stanje=1)
            item = OrderItem.objects.create(narudzba=source, artikal=product, naziv=product.naziv, cijena=10, kolicina=1)
            lines.append(WMSPickLine.objects.create(order=order, item=item, location=location, quantity=1))
        lines[1].confirmed_at = timezone.now()
        lines[1].confirmed_quantity = 1
        lines[1].save()
        url = reverse('staff_wms_pick_order', args=[order.pk])
        first = self.client.get(url, {'line': lines[0].pk})
        self.assertEqual(first.context['cycle_next'].pk, lines[2].pk)
        self.assertEqual([line.pk for line in first.context['pending_lines']], [lines[0].pk, lines[2].pk])
        self.assertEqual([line.pk for line in first.context['confirmed_lines']], [lines[1].pk])
        self.assertContains(first, 'Za odvajanje (2)')
        self.assertContains(first, 'Odvojeni (1)')
        last = self.client.get(url, {'line': lines[2].pk})
        self.assertEqual(last.context['cycle_next'].pk, lines[0].pk)
        lines[0].refresh_from_db()
        self.assertIsNone(lines[0].confirmed_at)
        lines[2].confirmed_at = timezone.now()
        lines[2].save()
        self.assertIsNone(self.client.get(url, {'line': lines[0].pk}).context['cycle_next'])

    def test_add_stock_from_product_list(self):
        from .models import Product, WMSLocation, ProductWMSStock, ModulePermissions
        product = Product.objects.create(naziv='Lista dodavanje', cijena=10, stanje=0)
        location = WMSLocation.objects.create(naziv='Lista A1')
        url = reverse('panel_admin:EcommerceApp_product_add_stock', args=[product.pk])
        self.assertEqual(self.client.get(url).json()['locations'], [{'id': location.pk, 'naziv': location.naziv}])
        self.assertEqual(self.client.post(url, {'quantity': 0, 'location': location.pk}).status_code, 400)
        self.assertEqual(self.client.post(url, {'quantity': 3, 'location': location.pk}).status_code, 200)
        self.assertEqual(self.client.post(url, {'quantity': 2, 'location': location.pk}).status_code, 200)
        product.refresh_from_db()
        self.assertEqual(product.stanje, 5)
        self.assertEqual(ProductWMSStock.objects.get(product=product, lokacija=location).kolicina, 5)
        ModulePermissions.objects.bulk_create([ModulePermissions(pk=1, wms_zalihe=False)])
        self.assertEqual(self.client.post(url, {'quantity': 1, 'location': location.pk}).status_code, 403)

    def test_shortage_clear_or_keep_after_partial_picking(self):
        from .models import Product, OrderItem, WMSLocation, ProductWMSStock, WMSPickLine
        from .views_wms import pending_shortages
        for picked, decision in ((0, 'keep'), (1, 'clear')):
            product = Product.objects.create(naziv='Nije pronadjeno ' + decision, cijena=10, stanje=5)
            location = WMSLocation.objects.create(naziv='Nedostaje ' + decision)
            stock = ProductWMSStock.objects.create(product=product, lokacija=location, kolicina=5)
            source = Order.objects.create(ime_prezime='Kupac ' + decision, email='missing@example.invalid', ukupno=20)
            item = OrderItem.objects.create(narudzba=source, artikal=product, naziv=product.naziv, cijena=10, kolicina=2)
            order = WMSOrder.objects.create(source_order=source, kupac=source.ime_prezime, tip='online', status='pakovanje')
            line = WMSPickLine.objects.create(order=order, item=item, location=location, quantity=2)
            pick_url = reverse('staff_wms_pick_order', args=[order.pk])
            self.client.post(pick_url, {'action': 'confirm', 'line_id': line.pk, 'quantity': picked})
            line.refresh_from_db()
            self.assertIsNotNone(line.confirmed_at)
            product.refresh_from_db()
            self.assertEqual(product.stanje, 5)
            self.assertFalse(pending_shortages().filter(pk=line.pk).exists())
            self.client.post(pick_url, {'action': 'finish'})
            product.refresh_from_db()
            self.assertEqual(product.stanje, 5 - picked)
            self.assertTrue(pending_shortages().filter(pk=line.pk).exists())
            page = self.client.get(reverse('staff_wms_section', args=['lokacije']))
            self.assertContains(page, 'Nema u lokaciji')
            self.assertContains(page, product.naziv)
            url = reverse('staff_wms_resolve_shortage', args=[line.pk])
            self.client.post(url, {'decision': decision})
            expected = 0 if decision == 'clear' else 5 - picked
            product.refresh_from_db()
            stock.refresh_from_db()
            self.assertEqual(product.stanje, expected)
            self.assertEqual(stock.kolicina, expected)
            self.assertFalse(pending_shortages().filter(pk=line.pk).exists())
            self.client.post(url, {'decision': 'clear'})
            product.refresh_from_db()
            self.assertEqual(product.stanje, expected)

    def test_partial_finish_uses_location_stock_when_site_total_is_stale(self):
        from .models import Product, OrderItem, WMSLocation, ProductWMSStock, WMSPickLine
        from django.utils import timezone
        product = Product.objects.create(naziv='Neuskladjeno stanje', cijena=10, stanje=1)
        source = Order.objects.create(ime_prezime='Kupac nesklad', email='mismatch@example.invalid', ukupno=50)
        item = OrderItem.objects.create(narudzba=source, artikal=product, naziv=product.naziv, cijena=10, kolicina=5)
        order = WMSOrder.objects.create(source_order=source, kupac=source.ime_prezime, tip='online', status='pakovanje')
        for name, stock_qty, needed, picked in [('A', 4, 4, 4), ('B', 2, 1, 0)]:
            location = WMSLocation.objects.create(naziv='Nesklad ' + name)
            ProductWMSStock.objects.create(product=product, lokacija=location, kolicina=stock_qty)
            WMSPickLine.objects.create(order=order, item=item, location=location, quantity=needed,
                                      confirmed_quantity=picked, confirmed_at=timezone.now())
        url = reverse('staff_wms_pick_order', args=[order.pk])
        self.client.post(url, {'action': 'finish'})
        order.refresh_from_db()
        product.refresh_from_db()
        self.assertEqual(order.status, 'zapakovana')
        self.assertEqual(product.stanje, 2)
        self.assertEqual(list(product.wms_zalihe.order_by('lokacija__naziv').values_list('kolicina', flat=True)), [0, 2])
        self.client.post(url, {'action': 'finish'})
        product.refresh_from_db()
        self.assertEqual(product.stanje, 2)

    def test_print_finished_orders_uses_picked_quantities_only(self):
        from .models import Product, OrderItem, WMSLocation, WMSPickLine, ModulePermissions
        from django.utils import timezone
        product = Product.objects.create(naziv='Stampa artikal', cijena=10)
        source = Order.objects.create(ime_prezime='Stampa kupac', email='print@example.invalid', ukupno=50, medjuzbir=50)
        item = OrderItem.objects.create(narudzba=source, artikal=product, naziv=product.naziv, cijena=10, kolicina=5)
        order = WMSOrder.objects.create(source_order=source, kupac=source.ime_prezime, tip='online', status='zapakovana')
        location = WMSLocation.objects.create(naziv='Stampa A1')
        WMSPickLine.objects.create(order=order, item=item, location=location, quantity=5, confirmed_quantity=3, confirmed_at=timezone.now())
        WMSOrder.objects.create(kupac='Aktivni kupac bez stampe', tip='online')
        for kind in ('racun', 'faktura', 'najava'):
            order.tip = 'vp' if kind == 'faktura' else 'online'
            order.save(update_fields=['tip'])
            url = reverse('staff_wms_print_orders', args=[kind])
            page = self.client.get(url, {'stage': 'print', 'orders': [order.pk]} if kind in ('racun', 'faktura') else {})
            self.assertEqual(page.status_code, 200)
            self.assertContains(page, '3 kom')
            self.assertNotContains(page, 'Aktivni kupac bez stampe')
            if kind == 'najava':
                self.assertContains(page, 'Stampa A1')
                self.assertContains(page, source.ime_prezime)
                self.assertContains(page, 'Narudžba #' + source.broj)
                self.assertContains(page, '<th>Naziv artikla</th><th>Šifra</th>')
                self.assertEqual(page.context['jobs'][0]['items'][0]['locations'], [{'name': 'Stampa A1', 'quantity': 3}])
                self.assertNotContains(self.client.get(url, {'date': '2000-01-01'}), 'Stampa artikal')
            else:
                self.assertEqual(page.context['jobs'][0]['subtotal'], 30)
        ModulePermissions.objects.bulk_create([ModulePermissions(pk=1, wms_narudzbe=False)])
        self.assertEqual(self.client.get(url).status_code, 403)

    def test_invoice_date_selection_prints_only_checked_orders(self):
        from django.utils import timezone
        from datetime import timedelta
        first = WMSOrder.objects.create(kupac='Odabrani kupac', tip='online')
        second = WMSOrder.objects.create(kupac='Neodabrani kupac', tip='online', status='otkazana')
        old = WMSOrder.objects.create(kupac='Stari kupac', tip='online')
        WMSOrder.objects.filter(pk=old.pk).update(kreirana=timezone.now() - timedelta(days=5))
        url = reverse('staff_wms_print_orders', args=['racun'])
        page = self.client.get(url)
        self.assertContains(page, first.kupac)
        self.assertNotContains(page, second.kupac)
        self.assertNotContains(page, old.kupac)
        printed = self.client.get(url, {'stage': 'print', 'orders': [first.pk, second.pk, old.pk]})
        self.assertContains(printed, first.kupac)
        self.assertNotContains(printed, second.kupac)
        self.assertNotContains(printed, old.kupac)
        self.assertEqual(len(printed.context['jobs']), 1)
        empty = self.client.get(url, {'stage': 'print'})
        self.assertEqual(len(empty.context['jobs']), 0)

    def test_invoice_quantities_include_wholesale_only(self):
        online = WMSOrder.objects.create(kupac='Online iskljuceno', tip='online', status='zapakovana')
        wholesale = WMSOrder.objects.create(kupac='Veleprodaja ukljucena', tip='vp', status='zapakovana')
        active = WMSOrder.objects.create(kupac='Aktivna veleprodaja', tip='vp')
        page = self.client.get(reverse('staff_wms_print_orders', args=['faktura']))
        self.assertContains(page, wholesale.kupac)
        self.assertNotContains(page, online.kupac)
        self.assertNotContains(page, active.kupac)
        other = WMSOrder.objects.create(kupac='Druga veleprodaja', tip='vp', status='zapakovana')
        printed = self.client.get(reverse('staff_wms_print_orders', args=['faktura']), {'stage': 'print', 'orders': [wholesale.pk, online.pk, active.pk]})
        self.assertContains(printed, wholesale.kupac)
        self.assertNotContains(printed, online.kupac)
        self.assertNotContains(printed, active.kupac)
        self.assertNotContains(printed, other.kupac)
        self.assertContains(page, 'Štampaj odabrane količine')

    def test_online_order_items_search_and_picking(self):
        import json
        from .models import Product, WMSLocation, ProductWMSStock
        product = Product.objects.create(naziv='Brzi unos rola', sifra='FAST123', cijena=10, stanje=5)
        location = WMSLocation.objects.create(naziv='Brzi A1')
        ProductWMSStock.objects.create(product=product, lokacija=location, kolicina=5)
        search_url = reverse('staff_wms_order_products')
        for query in ('Brzi unos', 'FAST123'):
            self.assertEqual(self.client.get(search_url, {'q': query}).json()['results'][0]['id'], product.pk)
        create_url = reverse('staff_wms_create_order', args=['online'])
        self.assertContains(self.client.get(create_url), 'Kataloški unos')
        self.client.post(create_url, {'kupac': 'Brzi kupac', 'items': '[]'})
        self.assertFalse(WMSOrder.objects.exists())
        before = Order.objects.count()
        response = self.client.post(create_url, {'kupac': 'Brzi kupac', 'items': json.dumps([{'id': product.pk, 'quantity': 2}, {'id': product.pk, 'quantity': 1}])})
        self.assertEqual(response.status_code, 302)
        order = WMSOrder.objects.get()
        self.assertEqual(Order.objects.count(), before)
        self.assertEqual(order.items.get().kolicina, 3)
        self.assertEqual(response.url, reverse('staff_wms_section', args=['narudzbe']))
        self.assertContains(self.client.get(reverse('staff_wms_order', args=[order.pk])), product.naziv)
        url = reverse('staff_wms_pick_order', args=[order.pk])
        self.client.post(url, {'action': 'start'})
        line = order.pick_lines.get()
        self.assertEqual(line.wms_item, order.items.get())
        self.client.post(url, {'action': 'confirm', 'line_id': line.pk, 'quantity': 3})
        self.client.post(url, {'action': 'finish'})
        product.refresh_from_db()
        self.assertEqual(product.stanje, 2)
        printed = self.client.get(reverse('staff_wms_print_orders', args=['racun']), {'stage': 'print', 'orders': [order.pk]})
        self.assertContains(printed, product.naziv)

    def test_customer_search_create_and_online_order_selection(self):
        import json
        from .models import Product, WMSCustomer
        historical = Order.objects.create(ime_prezime='Istorijski kupac', email='history@example.invalid', telefon='12345', adresa='Ulica 1', grad='Sarajevo', postanski_broj='71000', ukupno=10)
        url = reverse('staff_wms_order_customers')
        self.assertEqual(self.client.get(url, {'q': 'Istorijski'}).json()['customers'][0]['name'], historical.ime_prezime)
        self.assertEqual(self.client.get(url, {'q': 'NEPOSTOJECI'}).json()['customers'], [])
        self.assertEqual(self.client.post(url, {'ime_prezime': 'Novi'}).status_code, 400)
        data = {'ime_prezime': 'Novi kupac', 'telefon': '0612345', 'adresa': 'Nova 2', 'grad': 'Bijeljina', 'postanski_broj': '76300'}
        created = self.client.post(url, data).json()['customer']
        self.assertEqual(WMSCustomer.objects.get(pk=created['id']).grad, 'Bijeljina')
        self.assertEqual(self.client.get(url, {'q': '0612345'}).json()['customers'][0]['id'], created['id'])
        product = Product.objects.create(naziv='Kupac artikal', cijena=10)
        response = self.client.post(reverse('staff_wms_create_order', args=['online']), {'kupac': data['ime_prezime'], 'telefon': data['telefon'], 'adresa': data['adresa'], 'grad': data['grad'], 'postanski_broj': data['postanski_broj'], 'customer_id': created['id'], 'items': json.dumps([{'id': product.pk, 'quantity': 1}])})
        self.assertEqual(response.status_code, 302)
        order = WMSOrder.objects.get()
        self.assertEqual(order.customer_id, created['id'])
        self.assertEqual(order.grad, 'Bijeljina')
        self.assertEqual(order.postanski_broj, '76300')

    def test_wms_customer_page_lists_and_searches_customers(self):
        from .models import WMSCustomer
        WMSCustomer.objects.create(ime_prezime='WMS Lista kupac', telefon='123', adresa='Ulica', grad='Bijeljina', postanski_broj='76300')
        url = reverse('staff_wms_section', args=['kupci'])
        page = self.client.get(url)
        self.assertContains(page, 'WMS Lista kupac')
        self.assertContains(page, '76300')
        self.assertContains(self.client.get(url, {'q': 'Bijeljina'}), 'WMS Lista kupac')
        self.assertNotContains(self.client.get(url, {'q': 'NEMA REZULTATA'}), 'WMS Lista kupac')

    def test_online_item_discount_is_validated_and_saved(self):
        import json
        from decimal import Decimal
        from .models import Product
        product = Product.objects.create(naziv='Popust test', cijena=100)
        url = reverse('staff_wms_create_order', args=['online'])
        for discount in ('-1', '101', 'NaN'):
            self.client.post(url, {'kupac': 'Kupac', 'items': json.dumps([{'id': product.pk, 'quantity': 2, 'discount': discount}])})
            self.assertFalse(WMSOrder.objects.exists())
        response = self.client.post(url, {'kupac': 'Kupac', 'items': json.dumps([{'id': product.pk, 'quantity': 2, 'discount': '12.5', 'price': '1'}])})
        self.assertEqual(response.status_code, 302)
        item = WMSOrder.objects.get().items.get()
        self.assertEqual(item.popust, Decimal('12.50'))
        self.assertEqual(item.cijena, Decimal('87.50'))
        self.assertEqual(item.cijena * item.kolicina, Decimal('175.00'))

    def test_customer_edit_and_delete_preserves_order(self):
        from .models import WMSCustomer
        customer = WMSCustomer.objects.create(ime_prezime='Kupac', telefon='123', adresa='Ulica 1', grad='Grad', postanski_broj='71000')
        order = WMSOrder.objects.create(kupac='Kupac', telefon='123', customer=customer, tip='online')
        edit = reverse('staff_wms_manage_customer', args=[customer.pk, 'izmjena'])
        response = self.client.post(edit, {'ime_prezime': 'Novo ime', 'telefon': '123', 'adresa': 'Ulica 2', 'grad': 'Grad', 'postanski_broj': '71000'})
        self.assertEqual(response.status_code, 302)
        customer.refresh_from_db()
        self.assertEqual(customer.ime_prezime, 'Novo ime')
        delete = reverse('staff_wms_manage_customer', args=[customer.pk, 'obrisi'])
        self.client.get(delete)
        customer.refresh_from_db()
        self.assertFalse(customer.is_deleted)
        self.assertEqual(self.client.post(delete).status_code, 302)
        customer.refresh_from_db()
        self.assertTrue(customer.is_deleted)
        self.assertTrue(WMSOrder.objects.filter(pk=order.pk).exists())
        self.assertNotContains(self.client.get(reverse('staff_wms_section', args=['kupci'])), 'Novo ime')

    def test_add_stock_adds_exact_amount_without_unassigned_stock(self):
        from .models import Product, WMSLocation, ProductWMSStock
        product = Product.objects.create(naziv='Tačan unos', cijena=10, stanje=1)
        location = WMSLocation.objects.create(naziv='Tačan A1')
        other = WMSLocation.objects.create(naziv='Tačan B1')
        url = reverse('panel_admin:EcommerceApp_product_add_stock', args=[product.pk])
        for qty, loc in ((12, location), (8, location), (4, other)):
            self.assertEqual(self.client.post(url, {'quantity': qty, 'location': loc.pk}).status_code, 200)
        self.assertEqual(ProductWMSStock.objects.get(product=product, lokacija=location).kolicina, 20)
        self.assertEqual(ProductWMSStock.objects.get(product=product, lokacija=other).kolicina, 4)
        product.refresh_from_db()
        self.assertEqual(product.stanje, 24)

    def test_partial_pick_offers_alternative_without_deducting_stock(self):
        from .models import Product, WMSLocation, ProductWMSStock, WMSOrderItem, WMSPickLine
        product = Product.objects.create(naziv='Zamjena lokacije', cijena=10, stanje=8)
        a = WMSLocation.objects.create(naziv='Alternativa A')
        b = WMSLocation.objects.create(naziv='Alternativa B')
        for loc in (a, b):
            ProductWMSStock.objects.create(product=product, lokacija=loc, kolicina=4)
        order = WMSOrder.objects.create(kupac='Kupac', tip='online', status='pakovanje')
        item = WMSOrderItem.objects.create(order=order, artikal=product, naziv=product.naziv, kolicina=3, cijena=10)
        line = WMSPickLine.objects.create(order=order, wms_item=item, location=a, quantity=3)
        url = reverse('staff_wms_pick_order', args=[order.pk])
        response = self.client.post(url, {'action': 'confirm', 'line_id': line.pk, 'quantity': 1})
        self.assertIn('?line=', response.url)
        self.assertContains(self.client.get(response.url), b.naziv)
        response = self.client.post(url, {'action': 'alternative', 'line_id': line.pk, 'location_id': b.pk})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(order.pick_lines.get(location=b).quantity, 2)
        self.client.post(url, {'action': 'alternative', 'line_id': line.pk, 'location_id': b.pk})
        self.assertEqual(order.pick_lines.count(), 2)
        product.refresh_from_db()
        self.assertEqual(product.stanje, 8)

    def test_picking_last_location_setting_and_retail_fallback(self):
        from .models import Product, WMSLocation, ProductWMSStock, WMSSettings
        from .views_wms import ordered_picking_stocks
        product = Product.objects.create(naziv='Redoslijed', cijena=10)
        retail = WMSLocation.objects.create(naziv='Maloprodaja')
        warehouse = WMSLocation.objects.create(naziv='Z magacin')
        for loc in (retail, warehouse):
            ProductWMSStock.objects.create(product=product, lokacija=loc, kolicina=3)
        stocks = product.wms_zalihe.select_related('lokacija')
        self.assertEqual([s.lokacija_id for s in ordered_picking_stocks(stocks)], [warehouse.pk, retail.pk])
        WMSSettings.objects.create(pk=1, picking_last_location=warehouse)
        self.assertEqual([s.lokacija_id for s in ordered_picking_stocks(stocks)], [retail.pk, warehouse.pk])

    def test_product_list_minus_deducts_selected_location_only(self):
        from .models import Product, WMSLocation, ProductWMSStock
        product = Product.objects.create(naziv='Minus test', cijena=10, stanje=7)
        a = WMSLocation.objects.create(naziv='Minus A')
        b = WMSLocation.objects.create(naziv='Minus B')
        ProductWMSStock.objects.create(product=product, lokacija=a, kolicina=3)
        ProductWMSStock.objects.create(product=product, lokacija=b, kolicina=4)
        url = reverse('panel_admin:EcommerceApp_product_add_stock', args=[product.pk])
        self.assertEqual(self.client.post(url, {'action': 'remove', 'quantity': 4, 'location': a.pk}).status_code, 400)
        self.assertEqual(self.client.post(url, {'action': 'remove', 'quantity': 2, 'location': a.pk}).status_code, 200)
        self.assertEqual(ProductWMSStock.objects.get(product=product, lokacija=a).kolicina, 1)
        self.assertEqual(ProductWMSStock.objects.get(product=product, lokacija=b).kolicina, 4)
        product.refresh_from_db()
        self.assertEqual(product.stanje, 5)

    def test_order_detail_regular_price_and_configurable_vat(self):
        from decimal import Decimal
        from .models import WMSOrderItem, WMSSettings
        order = WMSOrder.objects.create(kupac='PDV kupac', tip='online')
        WMSOrderItem.objects.create(order=order, naziv='Snižen artikal', kolicina=1, cijena=117, bazna_cijena=130, popust=10)
        url = reverse('staff_wms_order', args=[order.pk])
        response = self.client.get(url)
        self.assertEqual(response.context['net_total'], Decimal('100.00'))
        self.assertEqual(response.context['vat_total'], Decimal('17.00'))
        self.assertEqual(response.context['items'][0].regular_price, Decimal('130'))
        self.assertContains(response, '<del>')
        WMSSettings.objects.create(pk=1, vat_rate=0)
        response = self.client.get(url)
        self.assertEqual(response.context['net_total'], Decimal('117.00'))
        self.assertEqual(response.context['vat_total'], Decimal('0.00'))

    def test_finished_order_search_name_and_number(self):
        finished = WMSOrder.objects.create(kupac='Adnan Hodžić', tip='online', status='zapakovana')
        WMSOrder.objects.create(kupac='Drugi kupac', tip='online', status='zapakovana')
        WMSOrder.objects.create(kupac='Adnan aktivni', tip='online', status='nova')
        url = reverse('staff_wms_section', args=['narudzbe'])
        for query in ('Adnan', '#' + finished.display_number, str(finished.pk)):
            response = self.client.get(url, {'status': 'zavrsene', 'q': query})
            self.assertEqual(list(response.context['orders']), [finished])
        response = self.client.get(url, {'status': 'zavrsene', 'q': 'Nema kupca'})
        self.assertFalse(response.context['orders'].exists())

    def test_wholesale_entry_uses_server_price_divided_by_138(self):
        import json
        from decimal import Decimal
        from .models import Product
        product = Product.objects.create(naziv='Veleprodaja test', cijena=138)
        url = reverse('staff_wms_create_order', args=['vp'])
        self.assertContains(self.client.get(url), 'Kataloški unos')
        result = self.client.get(reverse('staff_wms_order_products'), {'tip': 'vp', 'q': product.naziv}).json()
        self.assertEqual(Decimal(result['results'][0]['price']), Decimal('100'))
        response = self.client.post(url, {'kupac': 'VP kupac', 'items': json.dumps([{'id': product.pk, 'quantity': 2, 'discount': 10, 'price': 1}])})
        self.assertEqual(response.status_code, 302)
        order = WMSOrder.objects.get()
        self.assertEqual(order.tip, 'vp')
        self.assertEqual(order.items.get().cijena, Decimal('90'))
        self.assertEqual(order.items.get().bazna_cijena, Decimal('100'))

    def test_location_transfer_waits_for_transfer_document_confirmation(self):
        from .models import Product, WMSLocation, ProductWMSStock, WMSTransfer
        product = Product.objects.create(naziv='Apple Tag test', cijena=10, stanje=3)
        a = WMSLocation.objects.create(naziv='A-10 test')
        b = WMSLocation.objects.create(naziv='A-12 test')
        stock = ProductWMSStock.objects.create(product=product, lokacija=a, kolicina=3)
        url = reverse('staff_wms_location', args=[a.pk])
        self.assertContains(self.client.get(url), product.naziv)
        response = self.client.post(url, {'stock_id': stock.pk, 'destination': b.pk, 'quantity': 2})
        self.assertEqual(response.status_code, 302)
        transfer = WMSTransfer.objects.get()
        stock.refresh_from_db()
        self.assertEqual(stock.kolicina, 3)
        self.assertFalse(ProductWMSStock.objects.filter(product=product, lokacija=b).exists())
        self.client.post(reverse('staff_wms_section', args=['prenosnica']), {'transfer_id': transfer.pk})
        stock.refresh_from_db()
        self.assertEqual(stock.kolicina, 1)
        self.assertEqual(ProductWMSStock.objects.get(product=product, lokacija=b).kolicina, 2)

    def test_excel_import_template_locations_and_atomic_validation(self):
        from io import BytesIO
        from openpyxl import load_workbook
        from .wms_excel import template_bytes, import_products
        from .models import Product
        data = template_bytes()
        self.assertEqual(import_products(BytesIO(data)), 2)
        product = Product.objects.get(sifra='TEST-001')
        self.assertEqual(product.opis, 'Detaljan opis testnog artikla.')
        self.assertEqual(product.kategorija.naziv, 'Dodaci')
        self.assertEqual(product.kategorija.roditelj.naziv, 'Oprema')
        self.assertEqual(product.brend.naziv, 'Testni brend')
        self.assertEqual(product.tagovi.count(), 2)
        self.assertEqual(product.stanje, 7)
        self.assertEqual(product.wms_zalihe.count(), 2)
        self.assertEqual(import_products(BytesIO(data)), 2)
        product.refresh_from_db()
        self.assertEqual(product.stanje, 7)
        book = load_workbook(BytesIO(data))
        book['Artikli']['C2'] = 999
        book['Artikli']['C3'] = 999
        book['Artikli']['G3'] = -2
        stream = BytesIO()
        book.save(stream)
        stream.seek(0)
        with self.assertRaises(ValueError):
            import_products(stream)
        product.refresh_from_db()
        self.assertEqual(product.cijena, 100)
        url = reverse('staff_wms_excel_import')
        self.assertEqual(self.client.get(url, {'template': 1}).status_code, 200)
