from django.urls import path

from . import views
from . import views_sync

urlpatterns = [
    path('api/sync/korisnik/', views_sync.sync_korisnik_api, name='sync_korisnik_api'),
    path('api/sync/narudzba/', views_sync.sync_narudzba_api, name='sync_narudzba_api'),
    path('api/pretraga/', views.search_suggest, name='search_suggest'),
    path('', views.home, name='home'),
    path('kategorija/<slug:slug>/', views.category_detail, name='category'),
    path('artikal/<slug:slug>/', views.product_detail, name='product_detail'),
    path('artikal/<slug:slug>/dodaj/', views.add_to_cart, name='add_to_cart'),
    path('upsell/<int:offer_id>/<int:product_id>/dodaj/', views.add_upsell_to_cart, name='add_upsell_to_cart'),
    path('korpa/', views.cart_view, name='cart'),
    path('korpa/azuriraj/', views.update_cart, name='update_cart'),
    path('korpa/kupon/', views.apply_coupon, name='apply_coupon'),
    path('korpa/kupon/ukloni/', views.remove_coupon, name='remove_coupon'),
    path('korpa/ukloni/<str:key>/', views.remove_from_cart, name='remove_from_cart'),
    path('narudzba/', views.checkout, name='checkout'),
    path('narudzba/uspjeh/<str:broj>/', views.order_success, name='order_success'),
    path('prijava/', views.login_view, name='login'),
    path('registracija/', views.register, name='register'),
    path('odjava/', views.logout_view, name='logout'),
    path('nalog/', views.account, name='account'),
    path('nalog/narudzba/<str:broj>/', views.account_order_detail, name='account_order_detail'),
]