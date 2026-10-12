from . import views_monri
from . import views_partner_stock
from django.urls import path
from django.contrib.auth import views as auth_views
from django.urls import reverse_lazy
from django.views.decorators.cache import never_cache

from . import views
from . import bera_panel
from . import module_requests
from . import bera_panel_settings
from . import wms_print_status
from . import wms_price_print
from . import views_b2b
from . import views_catalog_api
from . import views_feed
from . import views_magacin
from . import views_wms
from . import views_site_prep
from . import views_sync

def retired_live_feature(request, *args, **kwargs):
    from django.http import HttpResponseGone
    response = HttpResponseGone('Ova opcija je uklonjena.')
    response['Cache-Control'] = 'no-store'
    return response


urlpatterns = [
    path('panel/stara-dugmad/', bera_panel.staff_old_buttons, name='staff_old_buttons'),
    path('panel/zahtjev-otkljucavanje/', module_requests.request_unlock, name='staff_request_unlock'),
    path('panel/podesavanje-novo/', bera_panel_settings.staff_site_settings, name='staff_settings_new'),
    path('panel/podesavanje-novo/boja-dugmadi/', bera_panel_settings.staff_button_color, name='staff_button_color'),
    path('panel/narudzbe/', views.staff_orders_validation, name='staff_panel_orders'),
    path('panel/narudzbe/online/', views.staff_online_orders, name='staff_panel_online_orders'),
    path('wms/stampa-cijena/', wms_price_print.price_print, name='staff_wms_stampa_cijena'),
    path('wms/stampa-cijena/ista/', wms_price_print.price_print, {'mode': 'ista'}, name='staff_wms_stampa_cijena_ista'),
    path('wms/stampa-cijena/razlicite/', wms_price_print.price_print, {'mode': 'razlicite'}, name='staff_wms_stampa_cijena_razlicite'),
    path('wms/stampa-cijena/print/', wms_price_print.price_print, {'mode': 'print'}, name='staff_wms_stampa_cijena_print'),
    path('wms/zalihe/artikal/<int:pk>/istorija/', views_wms.product_history, name='staff_wms_product_history'),
    path('wms/narudzbe/kreiraj/<str:tip>/', views_wms.create_order, name='staff_wms_create_order'),
    path('wms/narudzbe/pregled/<int:pk>/', views_wms.order_detail, name='staff_wms_order'),
    path('wms/narudzbe/<int:pk>/izmjena/', views_wms.edit_order, name='staff_wms_edit_order'),
    path('wms/narudzbe/<int:pk>/pakovanje/', views_wms.pack_order, name='staff_wms_pack_order'),
    path('wms/narudzbe/<int:pk>/odvajanje/', views_wms.pick_order, name='staff_wms_pick_order'),
    path('wms/odvajanje/istorija/', views_wms.picking_history, name='staff_wms_picking_history'),
    path('wms/narudzbe/<int:pk>/vrati/', views_wms.restore_order, name='staff_wms_restore_order'),
    path('wms/narudzbe/<int:pk>/otkazi/', views_wms.cancel_order, name='staff_wms_cancel_order'),
    path('wms/lokacije/nedostaje/<int:pk>/', views_wms.resolve_shortage, name='staff_wms_resolve_shortage'),
    path('wms/narudzbe/stampa/<str:kind>/potvrdi/', wms_print_status.acknowledge, name='staff_wms_print_acknowledge'),
    path('wms/narudzbe/stampa/<str:kind>/', views_wms.print_orders, name='staff_wms_print_orders'),
    path('wms/narudzbe/artikli/', views_wms.order_products, name='staff_wms_order_products'),
    path('wms/narudzbe/kupci/', views_wms.order_customers, name='staff_wms_order_customers'),
    path('wms/kupci/<int:pk>/<str:action>/', views_wms.manage_customer, name='staff_wms_manage_customer'),
    path('wms/lokacije/<int:pk>/barkod/', views_wms.location_barcode_print, name='staff_wms_location_barcode'),
    path('wms/lokacije/<int:pk>/', views_wms.location_detail, name='staff_wms_location'),
    path('wms/podesavanje/excel-import/', views_wms.excel_import, name='staff_wms_excel_import'),
    path('wms/', views_wms.workspace, name='staff_wms'),
    path('wms/<str:section>/', views_wms.workspace, name='staff_wms_section'),
    path('panel/podesavanja/logo/', bera_panel_settings.staff_settings_logo, name='staff_settings_logo'),
    path('panel/podesavanja/sajt/', bera_panel_settings.staff_site_settings, name='staff_site_settings'),
    path('panel/podesavanja', bera_panel_settings.staff_settings, name='staff_settings'),
    path('panel/podesavanja/', bera_panel_settings.staff_settings),
    path('panel/podesavanja/pretraga/', bera_panel_settings.staff_settings_lookup, name='staff_settings_lookup'),
    path('panel/b2b', bera_panel_settings.staff_b2b_workspace, name='staff_b2b_workspace'),
    path('panel/b2b/', bera_panel_settings.staff_b2b_workspace),
    path('panel/loyalty', bera_panel_settings.staff_loyalty_workspace, name='staff_loyalty_workspace'),
    path('panel/loyalty/', bera_panel_settings.staff_loyalty_workspace),
    path('panel/wms', views_wms.workspace, name='staff_wms_workspace'),
    path('panel/wms/', views_wms.workspace),
    path('panel/wms/sekcije/', bera_panel_settings.staff_wms_workspace, name='staff_wms_model_workspace'),
    path('panel/greb-greb', bera_panel_settings.staff_scratch_workspace, name='staff_scratch_workspace'),
    path('panel/greb-greb/', bera_panel_settings.staff_scratch_workspace),
    path('panel/sekcija/<slug:section>/', bera_panel_settings.staff_model_workspace, name='staff_model_workspace'),
    path('wms', views_wms.workspace),

    path('panel', bera_panel.staff_panel, name='staff_panel'),
    path('panel/', bera_panel.staff_panel),
    path('placanje/monri/potvrda/', views_monri.callback, name='monri_callback'),
    path('placanje/monri/<uuid:token>/', views_monri.start, name='monri_start'),
    path('placanje/monri/<uuid:token>/povratak/', views_monri.payment_return, name='monri_return'),
    path('placanje/monri/<uuid:token>/status/', views_monri.payment_status, name='monri_status'),
    path('placanje/monri/<uuid:token>/odustajanje/', views_monri.cancel, name='monri_cancel'),
    path('manifest.webmanifest', views.pwa_manifest, name='pwa_manifest'),
    path('service-worker.js', views.pwa_service_worker, name='pwa_service_worker'),
    path('nagrade/greb-greb/dogadjaj/', views.scratch_event, name='scratch_event'),
    path('nagrade/greb-greb/status/', views.scratch_status, name='scratch_status'),
    path('nagrade/greb-greb/otkrij/', views.scratch_claim, name='scratch_claim'),
    path('nagrade/greb-greb/dodaj-artikal/', views.scratch_add_product, name='scratch_add_product'),
    path('nagrade/greb-greb/dodaj-na-narudzbu/', views.scratch_add_product_to_order, name='scratch_add_product_to_order'),
    path('lozinka/reset/', never_cache(auth_views.PasswordResetView.as_view(
        template_name='auth/password_reset_form.html',
        email_template_name='auth/password_reset_email.txt',
        subject_template_name='auth/password_reset_subject.txt',
        success_url=reverse_lazy('password_reset_done'),
    )), name='password_reset'),
    path('lozinka/poslano/', never_cache(auth_views.PasswordResetDoneView.as_view(
        template_name='auth/password_reset_done.html',
    )), name='password_reset_done'),
    path('lozinka/potvrdi/<uidb64>/<token>/', never_cache(auth_views.PasswordResetConfirmView.as_view(
        template_name='auth/password_reset_confirm.html',
        success_url='/lozinka/gotovo/',
    )), name='password_reset_confirm'),
    path('lozinka/gotovo/', never_cache(auth_views.PasswordResetCompleteView.as_view(
        template_name='auth/password_reset_complete.html',
    )), name='password_reset_complete'),
    path("veleprodaja/zatrazi-pristup/", views_b2b.request_access, name="b2b_request_access"),
    path("veleprodaja/zavrsi/", views_b2b.checkout, name="b2b_checkout"),
    path("veleprodaja/narudzba/<int:pk>/", views_b2b.order_confirmation, name="b2b_order"),
    path("veleprodaja/korpa/", views_b2b.cart_view, name="b2b_cart"),
    path("veleprodaja/korpa/<int:product_id>/", views_b2b.cart_change, name="b2b_cart_change"),
    path("veleprodaja/uzivo/", views_b2b.presence, name="b2b_presence"),
    path("veleprodaja", views_b2b.catalog, name="b2b_catalog"),
    path("veleprodaja/", views_b2b.catalog),
    path("veleprodaja/odjava/", views_b2b.logout, name="b2b_logout"),
    path('api/sync/korisnik/', views_sync.sync_korisnik_api, name='sync_korisnik_api'),
    path('api/sync/narudzba/', views_sync.sync_narudzba_api, name='sync_narudzba_api'),
    path('api/partner/v1/products/', views_partner_stock.products, name='partner_stock_products'),
    path('api/partner/v1/products/<int:pk>/', views_partner_stock.product_detail, name='partner_stock_product'),
    path('api/pretraga/', views.search_suggest, name='search_suggest'),


    # Catalog Sync API — partner vuče katalog (read-only, API key)
    path('api/v1/ping/', views_catalog_api.catalog_api_ping, name='catalog_api_ping'),
    path('api/v1/products/', views_catalog_api.catalog_api_products, name='catalog_api_products'),
    path(
        'api/v1/magacin-stanje/',
        views_catalog_api.catalog_api_magacin_stanje,
        name='catalog_api_magacin_stanje',
    ),
    path(
        'api/v1/products/<slug:slug>/',
        views_catalog_api.catalog_api_product_detail,
        name='catalog_api_product_detail',
    ),
    path('api/v1/categories/', views_catalog_api.catalog_api_categories, name='catalog_api_categories'),
    path('api/v1/brands/', views_catalog_api.catalog_api_brands, name='catalog_api_brands'),

    path('facebook-feed.xml', views_feed.facebook_feed, name='facebook_feed'),

    path('priprema-pristup/', views_site_prep.site_prep_unlock, name='site_prep_unlock'),
    path(
        f'{views.FACEBOOK_DOMAIN_VERIFICATION}.html',
        views.facebook_domain_verification,
        name='facebook_domain_verification',
    ),
    path('', views.home, name='home'),
    path('api/newsletter/', views.newsletter_subscribe, name='newsletter_subscribe'),
    path('o-nama/', views.about_us, name='about_us'),
    path('lista-zelja/', views.wishlist, name='wishlist'),
    path('nacin-placanja/', views.payment_methods, name='payment_methods'),
    path('sigurnost-placanja/', views.payment_security, name='payment_security'),
    path('izjava-o-privatnosti/', views.privacy_statement, name='privacy_statement'),
    path('uslovi-kupovine/', views.purchase_terms, name='purchase_terms'),
    path('brendovi/', views.brands_list, name='brands_list'),
    path('vlog/', retired_live_feature, name='vlog_list'),
    path('vlog/<slug:slug>/', retired_live_feature, name='vlog_detail'),
    path('kategorija/<slug:slug>/', views.category_detail, name='category'),
    path('artikal/<slug:slug>/', views.product_detail, name='product_detail'),
    path('artikal/<slug:slug>/brza-izmjena/', views.staff_product_quick_edit, name='staff_product_quick_edit'),
    path('artikal/<slug:slug>/olx-objavi/', views.staff_post_product_olx, name='staff_post_product_olx'),
    path('artikal/<slug:slug>/dodaj/', views.add_to_cart, name='add_to_cart'),
    path('artikal/<slug:slug>/obavijesti/', views.stock_notify, name='stock_notify'),
    path('obavijesti/stanje/<str:token>/', views.stock_back_preview, name='stock_back_preview'),
    path('obavijesti/odjava/<str:token>/', views.stock_notify_unsubscribe, name='stock_notify_unsubscribe'),
    path('upsell/<int:offer_id>/<int:product_id>/dodaj/', views.add_upsell_to_cart, name='add_upsell_to_cart'),
    path('upsell/odbaci/', views.dismiss_upsell_popup, name='dismiss_upsell_popup'),
    path('korpa/', views.cart_view, name='cart'),
    path('korpa/stanje/', views.cart_stock, name='cart_stock'),
    path('korpa/azuriraj/', views.update_cart, name='update_cart'),
    path('korpa/kupon/', views.apply_coupon, name='apply_coupon'),
    path('korpa/kupon/ukloni/', views.remove_coupon, name='remove_coupon'),
    path('korpa/ukloni/<str:key>/', views.remove_from_cart, name='remove_from_cart'),
    path('korpa/podsjetnik/primijeni/', views.cart_recovery_apply, name='cart_recovery_apply'),
    path('korpa/podsjetnik/zatvori/', views.cart_recovery_dismiss, name='cart_recovery_dismiss'),
    path('korpa/exit/zatvori/', views.cart_exit_dismiss, name='cart_exit_dismiss'),
    path('korpa/podsjetnik-exit/zatvori/', views.cart_abandon_exit_dismiss, name='cart_abandon_exit_dismiss'),
    path('kreiraj-set/', views.set_builder_page, name='set_builder'),
    path('kreiraj-set/ulaz/', views.set_builder_unlock, name='set_builder_unlock'),
    path('kreiraj-set/preporuka/', views.set_builder_recommend, name='set_builder_recommend'),
    path('kreiraj-set/zamjena/', views.set_builder_alternatives, name='set_builder_alternatives'),
    path('kreiraj-set/korpa/', views.set_builder_add_cart, name='set_builder_add_cart'),
    path('drustveni-dokaz/', views.social_proof_poll, name='social_proof_poll'),
    path('uzivo/prisutan/', retired_live_feature, name='live_visitor_heartbeat'),
    path('uzivo/odlazak/', retired_live_feature, name='live_visitor_leave'),
    path('uzivo/javno/', retired_live_feature, name='public_online_visitors'),
    path('uzivo/skoro-korpa/', retired_live_feature, name='almost_cart_track'),
    path('narudzba/', views.checkout, name='checkout'),
    path('narudzba/uspjeh/<str:broj>/', views.order_success, name='order_success'),
    path('prijava/', views.login_view, name='login'),
    path('prijava/csrf/', views.auth_csrf_token, name='auth_csrf_token'),
    path('registracija/', views.register, name='register'),
    path('activate/<uidb64>/<token>/', views.activate, name='activate'),
    path('odjava/', views.logout_view, name='logout'),
    path('nalog/', views.account, name='account'),
    path('nalog/narudzba/<str:broj>/', views.account_order_detail, name='account_order_detail'),
    path('nalog/provjera-narudzbi/', views.staff_order_lookup, name='staff_order_lookup'),
    path('nalog/provjera-narudzbi/<str:broj>/', views.staff_order_detail, name='staff_order_detail'),
    path('nalog/provjera-narudzbi/<str:broj>/stampaj/', views.staff_order_print, name='staff_order_print'),
    path(
        'nalog/provjera-narudzbi/<str:broj>/odstampano/',
        views.staff_order_mark_printed,
        name='staff_order_mark_printed',
    ),
    path('nalog/provjera-narudzbi/<str:broj>/pakovanje/', views.staff_order_packing, name='staff_order_packing'),
    path(
        'nalog/provjera-narudzbi/<str:broj>/brza-posta/',
        views.staff_order_brza_posta,
        name='staff_order_brza_posta',
    ),
    path(
        'nalog/provjera-narudzbi/<str:broj>/x-express/',
        views.staff_order_xexpress,
        name='staff_order_xexpress',
    ),


    path('nalog/b2b-live/', views.staff_b2b_live, name='staff_b2b_live'),
    path('nalog/edit-mode/', views.staff_toggle_edit_mode, name='staff_toggle_edit_mode'),
    path('nalog/site-edit/', views.staff_site_edit_save, name='staff_site_edit_save'),
    path('nalog/artikli/bulk-izmjena/', views.staff_product_bulk_edit, name='staff_product_bulk_edit'),
    path('nalog/artikli/ista-slika/', views.staff_same_image_products, name='staff_same_image_products'),
    path('nalog/aktiviraj-korisnika/', views.staff_activate_user, name='staff_activate_user'),
    path('nalog/aktivne-korpe/', retired_live_feature, name='staff_active_carts'),
    path('ponuda/<str:token>/', views_magacin.ponuda_javna, name='ponuda_javna'),
    path('nalog/greb-greb/', views.staff_scratch_analytics, name='staff_scratch_analytics'),
    path('nalog/uzivo-analitika/', retired_live_feature, name='staff_live_analytics'),
    path('nalog/uzivo-analitika/podaci/', retired_live_feature, name='staff_live_analytics_data'),
    path('nalog/uzivo-obavijesti/', retired_live_feature, name='staff_site_events_poll'),
    path('nalog/pretraga-artikala/', views.staff_product_search, name='staff_product_search'),

    path('nalog/loyalty/', views.staff_loyalty_system, name='staff_loyalty_system'),
    path('nalog/loyalty/sync/', views.staff_loyalty_sync, name='staff_loyalty_sync'),
    path('nalog/loyalty/clan/<str:kod>/', views.staff_loyalty_member, name='staff_loyalty_member'),
    path(
        'nalog/loyalty/kartica/<int:card_id>/slika.png',
        views.staff_loyalty_card_image,
        name='staff_loyalty_card_image',
    ),
    # Javni PNG (token) — kupac otvara sliku kartice iz WhatsApp/Viber poruke
    path(
        'loyalty/kartica/<int:card_id>/<str:token>.png',
        views.public_loyalty_card_image,
        name='public_loyalty_card_image',
    ),
    path(
        'nalog/loyalty/kartica/<int:card_id>/qr.png',
        views.staff_loyalty_card_qr,
        name='staff_loyalty_card_qr',
    ),
    path(
        'nalog/loyalty/kartica/<int:card_id>/barkod.png',
        views.staff_loyalty_card_barcode,
        name='staff_loyalty_card_barcode',
    ),
    path('nalog/narudzbe/', views.staff_orders_validation, name='staff_orders_validation'),
    path('nalog/online-narudzbe/', views.staff_online_orders, name='staff_online_orders'),
    path('nalog/poklon-vaucer/', views.staff_gift_voucher, name='staff_gift_voucher'),
    path('nalog/poklon-vaucer/stampa/', views.staff_gift_voucher_print, name='staff_gift_voucher_print'),
    path('nalog/uvoz/', views.staff_uvoz, name='staff_uvoz'),
    path('nalog/uvoz/<int:pk>/', views.staff_uvoz_detail, name='staff_uvoz_detail'),
    path('nalog/pretraga-kategorija/', views.staff_category_search, name='staff_category_search'),
    path('nalog/pretraga-brendova/', views.staff_brand_search, name='staff_brand_search'),
    path('nalog/pretraga-tagova/', views.staff_tag_search, name='staff_tag_search'),
]

# Specific tool routes precede the generic WMS section route.
from .wms_service_urls import urlpatterns as wms_service_patterns
urlpatterns = wms_service_patterns + urlpatterns
