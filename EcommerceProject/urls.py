"""
URL configuration for EcommerceProject project.

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
import re

from django.conf import settings
from django.contrib import admin
from django.contrib.sitemaps.views import sitemap
from django.urls import include, path, re_path
from django.views.generic.base import TemplateView
from EcommerceApp.sitemaps import sitemaps as app_sitemaps
from EcommerceApp.views_media import serve_media

urlpatterns = [
    path('admin/', admin.site.urls),
    path('sitemap.xml', sitemap, {'sitemaps': app_sitemaps}, name='django.contrib.sitemaps.views.sitemap'),
    path('robots.txt', TemplateView.as_view(template_name='robots.txt', content_type='text/plain')),
] + [
    path('', include('EcommerceApp.urls')),
]

# Lokalni /media/ servis (Render disk / dev). Kad je Cloudflare R2 aktivan,
# MEDIA_URL je https://… i slike idu direktno sa R2/CDN — ne servira Django.
_media_url = getattr(settings, 'MEDIA_URL', '') or ''
if (
    not getattr(settings, 'USE_R2_MEDIA', False)
    and _media_url.startswith('/')
    and '://' not in _media_url
):
    urlpatterns = [
        re_path(
            r'^%s(?P<path>.*)$' % re.escape(_media_url.lstrip('/')),
            serve_media,
        ),
    ] + urlpatterns
