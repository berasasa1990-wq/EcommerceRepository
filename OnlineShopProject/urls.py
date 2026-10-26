"""
URL configuration for OnlineShopProject project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.0/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import include, path
from shop.module_api import update_loyalty_module
from shop.admin_sso import admin_sso_login
from shop.views import panel_backup_restore, panel_banner_delete, home, panel, panel_edit, PanelLoginView, PanelLogoutView, product_detail, search_suggestions, cart, cart_update, cart_add_bundle, checkout, account, customer_login, customer_register, customer_logout, wishlist_page, cart_loyalty

from bera_licensing.views import request_activation, activate, sync_now, activate_module_code

from shop.views import loyalty_page, customer_csrf_token

from shop.seo import robots, sitemap
from shop.chatbot import chat

urlpatterns = [
    path('api/chatbot/', chat, name='chatbot_message'),
    path('robots.txt', robots, name='seo_robots'),
    path('sitemap.xml', sitemap, name='seo_sitemap'),
    path('sitemap-<slug:section>-<int:page>.xml', sitemap, name='seo_sitemap_section'),
    path('panel/moduli/aktiviraj-kod/', activate_module_code, name='bera_module_code_activate'),
    path('bera-control/', include('bera_control.urls')),
    path('panel/moduli/licenca/aktivacija/', activate, name='bera_license_activate'),
    path('panel/moduli/licenca/provjera/', sync_now, name='bera_license_sync'),
    path('loyalty/', loyalty_page, name='loyalty'),
    path('panel/moduli/aktivacija/', request_activation, name='bera_activation'),
    path('api/modules/loyalty/', update_loyalty_module, name='module_loyalty'),
    path('admin/sso/', admin_sso_login, name='admin_sso_login'),
    path('korpa/loyalty/', cart_loyalty, name='cart_loyalty'),
    path('lista-zelja/', wishlist_page, name='wishlist'),
    path('nalog/csrf/', customer_csrf_token, name='customer_csrf_token'),
    path('nalog/', account, name='account'),
    path('nalog/prijava/', customer_login, name='customer_login'),
    path('nalog/registracija/', customer_register, name='customer_register'),
    path('nalog/odjava/', customer_logout, name='customer_logout'),
    path('checkout/', checkout, name='checkout'),
    path('korpa/', cart, name='cart'),
    path('korpa/set/<int:pk>/', cart_add_bundle, name='cart_add_bundle'),
    path('korpa/<int:pk>/', cart_update, name='cart_update'),
    path('', home, name='home'),
    path('pretraga/prijedlozi/', search_suggestions, name='search_suggestions'),
    path('artikal/<int:pk>/', product_detail, name='product_detail'),
    path('akcije/', home, {'sale_only': True}, name='sales'),
    path('najprodavanije/', home, {'bestselling_only': True}, name='bestsellers'),
    path('noviteti/', home, {'new_only': True}, name='new_arrivals'),
    path('brend/<int:brand_pk>/', home, name='brand'),
    path('kategorija/<int:category_pk>/', home, name='category'),
    path('panel', panel, name='panel'),
    path('panel/', panel),
    path('panel/prijava/', PanelLoginView.as_view(), name='panel_login'),
    path('panel/odjava/', PanelLogoutView.as_view(), name='panel_logout'),
    path('panel/banneri/<int:pk>/ukloni/', panel_banner_delete, name='panel_banner_delete'),
    path('panel/<slug:section>/dodaj/', panel_edit, name='panel_add'),
    path('panel/<slug:section>/<int:pk>/uredi/', panel_edit, name='panel_edit'),
    path('panel/backup/restore/', panel_backup_restore, name='panel_backup_restore'),
    path('panel/<slug:section>/', panel, name='panel_section'),
    path('admin/', admin.site.urls),
]

from django.conf import settings
from django.urls import re_path
from urllib.parse import urlsplit
import re
from shop.media_views import public_image

# Image downloads retain their URL and use the site's SEO name as the filename.
media_prefix = urlsplit(settings.MEDIA_URL).path.lstrip('/')
if media_prefix:
    urlpatterns += [re_path(r'^' + re.escape(media_prefix) + r'(?P<path>.*)$', public_image)]
