from django.conf import settings
from django.db.models import Prefetch

from .cart import Cart
from .models import Category, Popup, SiteSettings
from .upsell import get_active_upsell_offer


def nav_categories(request):
    sub_subcategories = Category.objects.filter(
        aktivan=True, prikazi_u_meniju=True,
    ).order_by('redoslijed', 'naziv')

    subcategories = Category.objects.filter(
        aktivan=True, prikazi_u_meniju=True,
    ).order_by('redoslijed', 'naziv').prefetch_related(
        Prefetch('podkategorije', queryset=sub_subcategories),
    )

    categories = Category.objects.filter(
        roditelj__isnull=True, aktivan=True, prikazi_u_meniju=True,
    ).order_by('redoslijed', 'naziv').prefetch_related(
        Prefetch('podkategorije', queryset=subcategories),
    )

    cart = Cart(request)
    active_popup = None
    for popup in Popup.objects.filter(aktivan=True).order_by('redoslijed', '-id'):
        if popup.prikazi_korisniku(request.user):
            active_popup = popup
            break

    return {
        'site_url': settings.SITE_URL,
        'nav_categories': categories,
        'site_settings': SiteSettings.load(),
        'cart_count': len(cart),
        'active_popup': active_popup,
        'active_upsell_offer': get_active_upsell_offer(request),
        'search_query': request.GET.get('q', '').strip(),
    }