from django.urls import path

from . import views
from . import views_feed
from . import views_site_prep
from . import views_sync

urlpatterns = [
    path('api/sync/korisnik/', views_sync.sync_korisnik_api, name='sync_korisnik_api'),
    path('api/sync/narudzba/', views_sync.sync_narudzba_api, name='sync_narudzba_api'),
    path('api/pretraga/', views.search_suggest, name='search_suggest'),
    path('facebook-feed.xml', views_feed.facebook_feed, name='facebook_feed'),

    path('priprema-pristup/', views_site_prep.site_prep_unlock, name='site_prep_unlock'),
    path('', views.home, name='home'),
    path('o-nama/', views.about_us, name='about_us'),
    path('nacin-placanja/', views.payment_methods, name='payment_methods'),
    path('vlog/', views.vlog_list, name='vlog_list'),
    path('vlog/<slug:slug>/', views.vlog_detail, name='vlog_detail'),
    path('kategorija/<slug:slug>/', views.category_detail, name='category'),
    path('artikal/<slug:slug>/', views.product_detail, name='product_detail'),
    path('artikal/<slug:slug>/brza-izmjena/', views.staff_product_quick_edit, name='staff_product_quick_edit'),
    path('artikal/<slug:slug>/olx-objavi/', views.staff_post_product_olx, name='staff_post_product_olx'),
    path('artikal/<slug:slug>/dodaj/', views.add_to_cart, name='add_to_cart'),
    path('upsell/<int:offer_id>/<int:product_id>/dodaj/', views.add_upsell_to_cart, name='add_upsell_to_cart'),
    path('upsell/odbaci/', views.dismiss_upsell_popup, name='dismiss_upsell_popup'),
    path('korpa/', views.cart_view, name='cart'),
    path('korpa/azuriraj/', views.update_cart, name='update_cart'),
    path('korpa/kupon/', views.apply_coupon, name='apply_coupon'),
    path('korpa/kupon/ukloni/', views.remove_coupon, name='remove_coupon'),
    path('korpa/ukloni/<str:key>/', views.remove_from_cart, name='remove_from_cart'),
    path('korpa/podsjetnik/primijeni/', views.cart_recovery_apply, name='cart_recovery_apply'),
    path('korpa/podsjetnik/zatvori/', views.cart_recovery_dismiss, name='cart_recovery_dismiss'),
    path('narudzba/', views.checkout, name='checkout'),
    path('narudzba/uspjeh/<str:broj>/', views.order_success, name='order_success'),
    path('prijava/', views.login_view, name='login'),
    path('registracija/', views.register, name='register'),
    path('activate/<uidb64>/<token>/', views.activate, name='activate'),
    path('odjava/', views.logout_view, name='logout'),
    path('nalog/', views.account, name='account'),
    path('nalog/narudzba/<str:broj>/', views.account_order_detail, name='account_order_detail'),
    path('nalog/provjera-narudzbi/', views.staff_order_lookup, name='staff_order_lookup'),
    path('nalog/provjera-narudzbi/<str:broj>/', views.staff_order_detail, name='staff_order_detail'),
    path('nalog/admin/', views.staff_admin_panel, name='staff_admin_panel'),
    path('nalog/aktivne-korpe/', views.staff_active_carts, name='staff_active_carts'),

    path('nalog/loyalty/', views.staff_loyalty_system, name='staff_loyalty_system'),
    path('nalog/online-narudzbe/', views.staff_online_orders, name='staff_online_orders'),
    path('nalog/olx-poruke/', views.staff_olx_messages, name='staff_olx_messages'),
    path('nalog/pretraga-kategorija/', views.staff_category_search, name='staff_category_search'),
    path('nalog/pretraga-brendova/', views.staff_brand_search, name='staff_brand_search'),
    path('nalog/pretraga-tagova/', views.staff_tag_search, name='staff_tag_search'),
]