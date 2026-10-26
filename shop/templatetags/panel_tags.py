from django import template

from shop.models import Brand, Order

register = template.Library()


@register.simple_tag
def new_orders_count():
    return Order.objects.filter(status='placed').count()


@register.simple_tag
def all_store_brands():
    return Brand.objects.all()


@register.simple_tag
def panel_css_version():
    from django.conf import settings
    return (settings.BASE_DIR / 'shop/static/css/panel.css').stat().st_mtime_ns
