from django.contrib.sitemaps import Sitemap
from django.urls import reverse
from django.utils import timezone

from .models import Category, Product


class StaticViewSitemap(Sitemap):
    priority = 1.0
    changefreq = 'daily'

    def items(self):
        return ['home']

    def location(self, item):
        return reverse(item)


class CategorySitemap(Sitemap):
    changefreq = 'daily'
    priority = 0.8

    def items(self):
        return Category.objects.filter(aktivan=True)

    def lastmod(self, obj):
        return getattr(obj, 'azuriran', None) or timezone.now()

    def location(self, obj):
        return obj.get_absolute_url()


class ProductSitemap(Sitemap):
    changefreq = 'daily'
    priority = 0.9

    def items(self):
        return Product.objects.filter(aktivan=True)

    def lastmod(self, obj):
        return obj.azuriran

    def location(self, obj):
        return obj.get_absolute_url()


sitemaps = {
    'static': StaticViewSitemap,
    'kategorije': CategorySitemap,
    'artikli': ProductSitemap,
}