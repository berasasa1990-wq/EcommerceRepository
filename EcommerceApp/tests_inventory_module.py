from django.contrib import admin
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from .models import ModulePermissions, Product
from .views import _product_queryset


class InventoryModuleTests(TestCase):
    def test_home_renders_with_inventory_visibility_filters(self):
        response = self.client.get(reverse('home'))
        self.assertEqual(response.status_code, 200)

    def test_quantity_and_manual_modes(self):
        product = Product.objects.create(naziv='Module test', cijena=10, stanje=2)
        config = ModulePermissions.objects.create(pracenje_lagera=True)
        product.refresh_from_db()
        self.assertTrue(product.pracenje_zaliha)
        product.stanje = 0
        product.save(update_fields=['stanje'])
        self.assertFalse(product.na_stanju)
        self.assertNotIn(product, _product_queryset())
        product.stanje = 3
        product.save(update_fields=['stanje'])
        self.assertTrue(product.na_stanju)
        self.assertIn(product, _product_queryset())
        config.pracenje_lagera = False
        config.save()
        product.refresh_from_db()
        self.assertFalse(product.pracenje_zaliha)
        product.stanje = 0
        product.save()
        self.assertTrue(product.na_stanju)
        product.na_stanju = False
        product.save(update_fields=['na_stanju'])
        product.refresh_from_db()
        self.assertFalse(product.na_stanju)
        product.na_stanju = True
        product.save(update_fields=['na_stanju'])
        self.assertTrue(product.na_stanju)

    def test_modules_only_registered_in_superuser_admin(self):
        from .panel_admin import panel_admin_site
        self.assertIn(ModulePermissions, admin.site._registry)
        self.assertNotIn(ModulePermissions, panel_admin_site._registry)
        user = get_user_model().objects.create_user('module-staff', is_staff=True)
        self.client.force_login(user)
        response = self.client.get(reverse('admin:EcommerceApp_modulepermissions_changelist'))
        self.assertEqual(response.status_code, 302)

    def test_catalogue_quantity_shown_in_warehouse_without_locations(self):
        from .magacin import display_stock_totals
        from .models import WarehouseLocation, WarehouseStock
        product = Product.objects.create(naziv='Panel quantity', cijena=10, stanje=7)
        self.assertEqual(display_stock_totals(product)['dostupno'], 7)
        product.stanje = 11
        product.save(update_fields=['stanje'])
        self.assertEqual(display_stock_totals(product)['na_stanju'], 11)
        location = WarehouseLocation.objects.create(sifra='ZERO', naziv='Zero')
        WarehouseStock.objects.create(product=product, location=location, kolicina=0)
        self.assertEqual(display_stock_totals(product)['dostupno'], 0)

    def test_disabled_module_blocks_warehouse_tools_including_posts(self):
        from .warehouse_access import can_access_route
        owner = get_user_model().objects.create_superuser('module-owner', 'owner@example.invalid', 'password')
        self.client.force_login(owner)
        config = ModulePermissions.objects.create(pracenje_lagera=False)
        for name in ('staff_magacin_artikli', 'staff_magacin_narudzbe', 'staff_magacin_pakuj', 'staff_magacin_kupci'):
            self.assertFalse(can_access_route(owner, name))
            self.assertEqual(self.client.get(reverse(name)).status_code, 403)
            self.assertEqual(self.client.post(reverse(name), {}).status_code, 403)
        config.pracenje_lagera = True
        config.save()
        for name in ('staff_magacin_artikli', 'staff_magacin_narudzbe', 'staff_magacin_pakuj', 'staff_magacin_kupci'):
            self.assertTrue(can_access_route(owner, name))

    def test_locked_tools_remain_visible_in_warehouse_menu(self):
        from django.template.loader import render_to_string
        from django.test import RequestFactory
        ModulePermissions.objects.create(pracenje_lagera=False)
        owner = get_user_model().objects.create_superuser('lock-owner', 'lock@example.invalid', 'password')
        request = RequestFactory().get('/nalog/magacin/pregled/')
        request.user = owner
        html = render_to_string('staff/magacin/base.html', {}, request=request)
        self.assertEqual(html.count('class="mg-tool-lock-icon"'), 4)
        for label in ('Artikli', 'Narudžbe', 'Picking', 'Kupci'):
            self.assertIn(label, html)

    def test_sets_module_is_independent_and_blocks_direct_access(self):
        from .warehouse_access import can_access_route
        owner = get_user_model().objects.create_superuser('sets-owner', 'sets@example.invalid', 'password')
        self.client.force_login(owner)
        config = ModulePermissions.objects.create(pracenje_lagera=False, artikli_u_setu=False)
        url = reverse('staff_magacin_setovi')
        self.assertFalse(can_access_route(owner, 'staff_magacin_setovi'))
        self.assertEqual(self.client.get(url).status_code, 403)
        self.assertEqual(self.client.post(url, {}).status_code, 403)
        config.artikli_u_setu = True
        config.save()
        self.assertTrue(can_access_route(owner, 'staff_magacin_setovi'))
        self.assertFalse(can_access_route(owner, 'staff_magacin_artikli'))

    def test_each_warehouse_module_locks_only_its_own_tool(self):
        from .warehouse_access import can_access_route, warehouse_module_field
        from django.template.loader import render_to_string
        from django.test import RequestFactory
        owner = get_user_model().objects.create_superuser('tools-owner', 'tools@example.invalid', 'password')
        self.client.force_login(owner)
        config = ModulePermissions.objects.create(pracenje_lagera=True)
        routes = (
            'staff_magacin_stampa_cijena', 'staff_magacin_stampa_deklaracije',
            'staff_magacin_mp_dnevno', 'staff_magacin_dupli_barkodovi',
            'staff_magacin_duguje', 'staff_magacin_popis_test',
            'staff_magacin_fali_na_sajtu', 'staff_magacin_nivelacije',
            'staff_magacin_ponude', 'staff_magacin_izvjestaji', 'staff_magacin_rezervni_dijelovi', 'staff_magacin_provjera_lagera', 'staff_magacin_uvoz',
        )
        for route in routes:
            field = warehouse_module_field(route)
            setattr(config, field, False)
            config.save()
            self.assertEqual(self.client.get(reverse(route)).status_code, 403)
            self.assertEqual(self.client.post(reverse(route), {}).status_code, 403)
            self.assertTrue(can_access_route(owner, 'staff_magacin_artikli'))
            setattr(config, field, True)
            config.save()
            self.assertTrue(can_access_route(owner, route))
        for route in routes:
            setattr(config, warehouse_module_field(route), False)
        config.save()
        self.assertFalse(can_access_route(owner, 'staff_magacin_dupli_barkod_obrisi'))
        self.assertFalse(can_access_route(owner, 'staff_magacin_ponuda_prihvati'))
        self.assertFalse(can_access_route(owner, 'staff_magacin_popis_stampa'))
        self.assertFalse(can_access_route(owner, 'staff_magacin_uvoz_detail'))
        self.assertFalse(can_access_route(owner, 'staff_magacin_uvoz_novi'))
        self.assertFalse(can_access_route(owner, 'staff_magacin_provjera_lagera_stampa'))
        request = RequestFactory().get('/nalog/magacin/pregled/')
        request.user = owner
        html = render_to_string('staff/magacin/base.html', {}, request=request)
        self.assertEqual(html.count('class="mg-tool-lock-icon"'), 16)

    def test_stock_check_module_controls_locations_stock_and_transfers(self):
        from .warehouse_access import can_access_route
        owner = get_user_model().objects.create_superuser('stock-tools-owner', 'stock@example.invalid', 'password')
        self.client.force_login(owner)
        config = ModulePermissions.objects.create(provjera_lagera=False)
        routes = ('staff_magacin_lokacije', 'staff_magacin_zalihe', 'staff_magacin_transferi')
        for route in routes:
            self.assertEqual(self.client.get(reverse(route)).status_code, 403)
            self.assertEqual(self.client.post(reverse(route), {}).status_code, 403)
        self.assertFalse(can_access_route(owner, 'staff_magacin_lokacija_stampa'))
        self.assertFalse(can_access_route(owner, 'staff_magacin_lokacije_lookup'))
        config.provjera_lagera = True
        config.save()
        for route in routes:
            self.assertTrue(can_access_route(owner, route))

    def test_panel_modules_block_workspaces_and_model_editors(self):
        from .panel_modules import panel_route_module, module_locked
        owner = get_user_model().objects.create_superuser('panel-tools-owner', 'panel@example.invalid', 'password')
        self.client.force_login(owner)
        config = ModulePermissions.objects.create()
        modules = {
            'b2b': ('staff_b2b_workspace', None),
            'posjetioci_uzivo': ('staff_model_workspace', 'livevisitor'),
            'sretni_greb_greb': ('staff_scratch_workspace', None),
            'stavke_aktivnih_korpi': ('staff_model_workspace', 'activecartitem'),
            'loyalty': ('staff_loyalty_workspace', None),
            'poklon_vaucer': ('staff_gift_voucher', None),
            'live_centar': ('staff_live_analytics', None),
        }
        for field, (route, section) in modules.items():
            setattr(config, field, False)
            config.save()
            url = reverse(route, args=[section] if section else None)
            self.assertEqual(self.client.get(url).status_code, 403)
            self.assertEqual(self.client.post(url, {}).status_code, 403)
            setattr(config, field, True)
            config.save()
            self.assertFalse(module_locked(panel_route_module(route, section)))
        config.posjetioci_uzivo = False
        config.save()
        self.assertEqual(self.client.get(reverse('panel_admin:EcommerceApp_livevisitor_changelist')).status_code, 403)
        response = self.client.get(reverse('staff_panel'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '🔒')

    def test_disabled_loyalty_hides_cards_but_keeps_regular_coupons(self):
        from django.test import RequestFactory
        from .models import Coupon
        from .views import _coupon_choices, _loyalty_za_kupon
        from .panel_admin import panel_admin_site
        owner = get_user_model().objects.create_superuser('coupon-owner', 'coupon@example.invalid', 'password')
        ModulePermissions.objects.create(loyalty=False)
        regular = Coupon.objects.create(kod='REGULAR', naziv='Kupon', postotak=5, vlasnik=owner)
        automatic = Coupon.objects.create(kod='LOYALTY', naziv='Kartica', postotak=3, vlasnik=owner, automatski=True)
        request = RequestFactory().get('/panel')
        request.user = owner
        self.assertIsNone(_loyalty_za_kupon(request))
        self.assertIn(regular, _coupon_choices(request))
        self.assertNotIn(automatic, _coupon_choices(request))
        queryset = panel_admin_site._registry[Coupon].get_queryset(request)
        self.assertIn(regular, queryset)
        self.assertNotIn(automatic, queryset)

    def test_disabled_loyalty_blocks_customer_codes_and_card_creation(self):
        from .loyalty import osiguraj_loyalty_karticu, validiraj_kupon, loyalty_kontekst
        from .models import Coupon, LoyaltyCard
        owner = get_user_model().objects.create_user('loyalty-customer', 'customer@example.invalid', 'password')
        card = osiguraj_loyalty_karticu(owner)
        config = ModulePermissions.objects.create(loyalty=False)
        regular = Coupon.objects.create(kod='NORMAL', naziv='Normalni kupon', postotak=5, vlasnik=owner)
        self.assertIsNone(validiraj_kupon(card.kod, owner)[0])
        self.assertIsNone(validiraj_kupon(card.kod)[0])
        self.assertEqual(validiraj_kupon(regular.kod, owner)[0], regular)
        self.assertEqual(loyalty_kontekst(card), {})
        other = get_user_model().objects.create_user('no-card')
        self.assertIsNone(osiguraj_loyalty_karticu(other))
        self.assertFalse(LoyaltyCard.objects.filter(user=other).exists())
        self.client.force_login(owner)
        response = self.client.get(reverse('account'))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'Loyalty potrošnja')
        self.assertNotContains(response, card.kod)
        self.assertEqual(self.client.get(reverse('public_loyalty_card_image', args=[card.pk, 'invalid'])).status_code, 403)
        config.loyalty = True
        config.save()
        self.assertIsNotNone(validiraj_kupon(card.kod, owner)[0])

    def test_banner_destination_fields_locked_but_link_editable(self):
        from .forms import BannerAdminForm
        from .models import Banner
        config = ModulePermissions.objects.create(banner_odrediste_filter=False)
        banner = Banner(slika='banners/test.jpg', filter_cijena_od=10, link='/old/')
        form = BannerAdminForm(data={'link': '/new/', 'filter_cijena_od': '999'}, instance=banner)
        for name in form.MODULE_FIELDS:
            self.assertTrue(form.fields[name].disabled)
        self.assertFalse(form.fields['link'].disabled)
        form.full_clean()
        self.assertEqual(form.cleaned_data['filter_cijena_od'], 10)
        self.assertEqual(form.cleaned_data['link'], '/new/')
        config.banner_odrediste_filter = True
        config.save()
        unlocked = BannerAdminForm(instance=banner)
        for name in form.MODULE_FIELDS:
            self.assertFalse(unlocked.fields[name].disabled)

    def test_actions_module_locks_panel_and_direct_editor(self):
        owner = get_user_model().objects.create_superuser('actions-owner', 'actions@example.invalid', 'password')
        self.client.force_login(owner)
        config = ModulePermissions.objects.create(akcije=False)
        urls = [reverse('staff_model_workspace', args=['akcija']),
                reverse('panel_admin:EcommerceApp_akcija_changelist'),
                reverse('panel_admin:EcommerceApp_akcija_add')]
        for url in urls:
            self.assertEqual(self.client.get(url).status_code, 403)
            self.assertEqual(self.client.post(url, {}).status_code, 403)
        config.akcije = True
        config.save()
        for url in urls:
            self.assertEqual(self.client.get(url).status_code, 200)

    def test_disabled_scratch_hidden_and_endpoints_locked(self):
        config = ModulePermissions.objects.create(sretni_greb_greb=False)
        response = self.client.get(reverse('home'))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'id="scratchGame"')
        self.assertNotContains(response, 'js/scratch-game.js')
        for route in ('scratch_status', 'scratch_claim', 'scratch_add_product', 'scratch_add_product_to_order', 'scratch_event'):
            self.assertEqual(self.client.post(reverse(route), {}).status_code, 403)
        config.sretni_greb_greb = True
        config.save()
        self.assertContains(self.client.get(reverse('home')), 'id="scratchGame"')
