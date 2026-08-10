from django.contrib.sitemaps import Sitemap
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from .category_visibility import filter_categories_with_products
from .models import Brand, Category, HomeVlog, Product


class StaticViewSitemap(Sitemap):
    """Javne statične stranice visokog prioriteta."""
    priority = 1.0
    changefreq = 'daily'
    protocol = 'https'

    def items(self):
        return ['home', 'about_us', 'payment_methods', 'vlog_list']

    def location(self, item):
        return reverse(item)


class CategorySitemap(Sitemap):
    changefreq = 'daily'
    priority = 0.85
    protocol = 'https'

    def items(self):
        return filter_categories_with_products(Category.objects.filter(aktivan=True))

    def lastmod(self, obj):
        return getattr(obj, 'azuriran', None) or timezone.now()

    def location(self, obj):
        return obj.get_absolute_url()


class VlogSitemap(Sitemap):
    changefreq = 'weekly'
    priority = 0.65
    protocol = 'https'

    def items(self):
        return HomeVlog.objects.filter(aktivan=True).exclude(slug='')

    def location(self, obj):
        return obj.get_absolute_url()


class ProductSitemap(Sitemap):
    """
    Samo aktivni artikli koji su (ili imaju varijaciju) na stanju.
    Out-of-stock stranice imaju noindex — ne treba ih u sitemapu.
    """
    changefreq = 'daily'
    priority = 0.9
    protocol = 'https'
    limit = 50000

    def items(self):
        return (
            Product.objects.filter(aktivan=True)
            .filter(Q(na_stanju=True) | Q(varijacije__na_stanju=True))
            .distinct()
            .only('id', 'slug', 'azuriran')
            .order_by('-azuriran', 'id')
        )

    def lastmod(self, obj):
        return obj.azuriran

    def location(self, obj):
        return obj.get_absolute_url()


class BrandSitemap(Sitemap):
    """
    Brand filter URL-ovi na početnoj (?brend=slug) — samo brendovi s artiklima.
    """
    changefreq = 'weekly'
    priority = 0.6
    protocol = 'https'

    def items(self):
        used = (
            Product.objects.filter(aktivan=True, brend__isnull=False)
            .filter(Q(na_stanju=True) | Q(varijacije__na_stanju=True))
            .values_list('brend_id', flat=True)
            .distinct()
        )
        return Brand.objects.filter(id__in=used).exclude(slug='')

    def location(self, obj):
        return f'/?brend={obj.slug}'


sitemaps = {
    'static': StaticViewSitemap,
    'kategorije': CategorySitemap,
    'artikli': ProductSitemap,
    'vlogovi': VlogSitemap,
    'brendovi': BrandSitemap,
}
