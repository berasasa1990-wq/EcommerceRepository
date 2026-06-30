import logging
import random
import re
import requests
from decimal import Decimal, InvalidOperation
from urllib.parse import urlencode

from django.conf import settings
from .models import SiteSettings
from django.core.paginator import EmptyPage, PageNotAnInteger, Paginator
from django.contrib import messages
from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.db import DatabaseError
from django.db.models import Prefetch, Q
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.html import escape, mark_safe, strip_tags
from django.views.decorators.http import require_POST

from .cart import Cart
from .loyalty import (
    azuriraj_loyalty_nakon_narudzbe,
    kreiraj_loyalty_karticu,
    loyalty_kontekst,
    osiguraj_loyalty_karticu,
    validiraj_kupon,
)
from .pricing import izracunaj_sazetak, sazetak_iz_narudzbe
from .emails import EmailNotConfiguredError, send_order_emails
from .render_sync import sync_korisnik, sync_narudzba
from .utils.images import image_field_dimensions

logger = logging.getLogger(__name__)
from .forms import CheckoutForm, CouponForm, LoginForm, ProfileForm, RegisterForm
from .models import (
    Banner,
    Brand,
    Category,
    HomeFeaturedProduct,
    HomeVlog,
    Order,
    OrderItem,
    Product,
    ProductVariation,
    SiteSettings,
    UpsellOffer,
    UserProfile,
)


def _in_stock_variations_qs():
    return ProductVariation.objects.filter(
        na_stanju=True,
    ).order_by('redoslijed', 'id')


def _prefetch_product_cards(qs):
    return qs.select_related('kategorija', 'brend').prefetch_related(
        Prefetch('varijacije', queryset=_in_stock_variations_qs()),
    )


def _product_queryset():
    return _prefetch_product_cards(
        Product.objects.filter(aktivan=True, na_stanju=True),
    )


def _effective_product_price(product):
    variations = list(product.varijacije.all())
    if variations:
        return min(variation.prikazna_cijena for variation in variations)
    return product.prikazna_cijena


def _product_is_on_sale(product):
    variations = list(product.varijacije.all())
    if variations:
        return any(variation.na_akciji for variation in variations)
    return product.na_akciji


def _parse_decimal(value):
    value = (value or '').strip().replace(',', '.')
    if not value:
        return None
    try:
        return Decimal(value)
    except (InvalidOperation, ValueError):
        return None


def _get_filter_params(request):
    return {
        'q': request.GET.get('q', '').strip(),
        'kategorija': request.GET.get('kategorija', '').strip(),
        'brend': request.GET.get('brend', '').strip(),
        'cijena_od': request.GET.get('cijena_od', '').strip(),
        'cijena_do': request.GET.get('cijena_do', '').strip(),
        'sort': request.GET.get('sort', '').strip(),
        'akcija': request.GET.get('akcija', '').strip(),
    }


def _filters_active(params):
    return any(params.values())


def _filter_categories():
    return Category.objects.filter(aktivan=True).select_related(
        'roditelj', 'roditelj__roditelj',
    ).order_by('redoslijed', 'naziv')


def _showcase_brands():
    return Brand.objects.filter(
        slika__isnull=False,
        artikli__aktivan=True,
        artikli__na_stanju=True,
    ).exclude(slika='').distinct().order_by('naziv')


def _apply_search_filter(products_qs, query):
    if not query:
        return products_qs
    return products_qs.filter(
        Q(naziv__icontains=query)
        | Q(sifra__icontains=query)
        | Q(tagovi__naziv__icontains=query)
        | Q(varijacije__sifra__icontains=query),
    ).distinct()


SEARCH_SUGGEST_LIMIT = 8


def search_suggest(request):
    query = request.GET.get('q', '').strip()
    if not query:
        return JsonResponse({'results': [], 'query': ''})

    products = _apply_search_filter(_product_queryset(), query)[:SEARCH_SUGGEST_LIMIT]
    results = []
    for product in products:
        price = _effective_product_price(product)
        results.append({
            'naziv': product.naziv,
            'url': product.get_absolute_url(),
            'image': product.prikazna_slika.url if product.prikazna_slika else '',
            'price': f'{price:.2f}',
            'on_sale': _product_is_on_sale(product),
        })

    return JsonResponse({'results': results, 'query': query})


def _apply_product_filters(products_qs, request, *, allowed_category_ids=None):
    params = _get_filter_params(request)
    products_qs = _apply_search_filter(products_qs, params['q'])
    products = list(products_qs)

    if allowed_category_ids is not None:
        allowed = set(allowed_category_ids)
        products = [product for product in products if product.kategorija_id in allowed]

    if params['kategorija']:
        category = Category.objects.filter(slug=params['kategorija'], aktivan=True).first()
        if category:
            category_ids = set(category.get_descendant_ids())
            if allowed_category_ids is not None:
                category_ids &= set(allowed_category_ids)
            products = [product for product in products if product.kategorija_id in category_ids]

    if params['brend']:
        brand = Brand.objects.filter(slug=params['brend']).first()
        if brand:
            products = [product for product in products if product.brend_id == brand.pk]

    price_min = _parse_decimal(params['cijena_od'])
    price_max = _parse_decimal(params['cijena_do'])
    if price_min is not None:
        products = [product for product in products if _effective_product_price(product) >= price_min]
    if price_max is not None:
        products = [product for product in products if _effective_product_price(product) <= price_max]

    if params['akcija']:
        products = [product for product in products if _product_is_on_sale(product)]

    if params['sort'] == 'rastuca':
        products.sort(key=_effective_product_price)
    elif params['sort'] == 'opadajuca':
        products.sort(key=_effective_product_price, reverse=True)

    return products, params


HOME_PRODUCTS_PER_PAGE = 16
HOME_PRODUCT_ORDER_KEY = 'home_product_ids'
HOME_FILTER_KEY = 'home_filter_key'


def _catalog_query_string(filter_params, page=None):
    params = {key: value for key, value in filter_params.items() if value}
    if page and page > 1:
        params['page'] = page
    return urlencode(params)


def _paginate_home_products(request, products, filter_params):
    page_number = request.GET.get('page', '1')
    filters_active = _filters_active(filter_params)
    filter_signature = _catalog_query_string(filter_params)

    if filters_active:
        if request.session.get(HOME_FILTER_KEY) != filter_signature:
            request.session.pop(HOME_PRODUCT_ORDER_KEY, None)
            request.session[HOME_FILTER_KEY] = filter_signature
            request.session.modified = True
    else:
        request.session.pop(HOME_FILTER_KEY, None)
        fresh_visit = 'page' not in request.GET
        if fresh_visit:
            random.shuffle(products)
            request.session[HOME_PRODUCT_ORDER_KEY] = [product.pk for product in products]
            request.session.modified = True
        else:
            stored_ids = request.session.get(HOME_PRODUCT_ORDER_KEY, [])
            if stored_ids:
                by_id = {product.pk: product for product in products}
                ordered = [by_id[pk] for pk in stored_ids if pk in by_id]
                seen = {product.pk for product in ordered}
                for product in products:
                    if product.pk not in seen:
                        ordered.append(product)
                products = ordered

    paginator = Paginator(products, HOME_PRODUCTS_PER_PAGE)
    try:
        page_obj = paginator.page(page_number)
    except PageNotAnInteger:
        page_obj = paginator.page(1)
    except EmptyPage:
        page_obj = paginator.page(paginator.num_pages or 1)
    return page_obj


def _base_context():
    return {}


def _banner_secondary_href(link):
    if not link:
        return None
    if link.startswith(('http://', 'https://', '/')):
        return link
    return f'/{link.strip("/")}/'


def _banner_actions(banner):
    actions = []
    if banner.tekst_dugmeta:
        actions.append({
            'label': banner.tekst_dugmeta,
            'url': banner.get_link_href() or '#',
            'primary': True,
        })
    if banner.sekundarno_dugme:
        actions.append({
            'label': banner.sekundarno_dugme,
            'url': _banner_secondary_href(banner.sekundarni_link) or '#',
            'primary': False,
        })
    return actions


def _banner_to_hero_slide(banner):
    image_width, image_height = image_field_dimensions(banner.slika, default=(1920, 1080))
    return {
        'title': banner.naslov,
        'subtitle': banner.podnaslov,
        'image': banner.slika.url,
        'image_width': image_width,
        'image_height': image_height,
        'url': banner.get_link_href(),
        'actions': _banner_actions(banner),
    }


def _banner_to_card(banner):
    default_dims = (420, 420) if banner.tip == Banner.BannerType.GRID else (1200, 1200)
    image_width, image_height = image_field_dimensions(banner.slika, default=default_dims)
    return {
        'title': banner.naslov,
        'subtitle': banner.podnaslov,
        'image': banner.slika.url,
        'image_width': image_width,
        'image_height': image_height,
        'url': banner.get_link_href(),
        'actions': _banner_actions(banner),
        'wide': banner.siroka_kartica,
    }


def _banners_with_image(qs):
    return qs.exclude(slika__isnull=True).exclude(slika='')


HOME_SECTION_PRODUCT_LIMIT = 4
HOME_VLOG_LIMIT = 3


def _home_latest_products():
    return list(
        _product_queryset().order_by('-kreiran')[:HOME_SECTION_PRODUCT_LIMIT],
    )


def _home_featured_products():
    entries = HomeFeaturedProduct.objects.filter(
        aktivan=True,
        artikal__aktivan=True,
        artikal__na_stanju=True,
    ).select_related(
        'artikal', 'artikal__kategorija', 'artikal__brend',
    ).prefetch_related(
        Prefetch('artikal__varijacije', queryset=_in_stock_variations_qs()),
    )[:HOME_SECTION_PRODUCT_LIMIT]
    return [entry.artikal for entry in entries]


def _vlog_cards(limit=None):
    try:
        entries_qs = HomeVlog.objects.filter(
            aktivan=True,
        ).exclude(
            slika='',
        ).exclude(
            slug='',
        ).order_by('redoslijed', '-id')
        if limit is not None:
            entries = list(entries_qs[:limit])
        else:
            entries = list(entries_qs)
    except DatabaseError:
        logger.exception(
            'HomeVlog tabela nije dostupna — pokreni: python manage.py migrate',
        )
        return []

    vlogs = []
    for vlog in entries:
        if not vlog.slug:
            continue
        width, height = image_field_dimensions(vlog.slika, default=(400, 300))
        vlogs.append({
            'id': vlog.pk,
            'slug': vlog.slug,
            'naslov': vlog.naslov,
            'slika_url': vlog.slika.url,
            'image_width': width,
            'image_height': height,
        })
    return vlogs


def _home_vlogs():
    return _vlog_cards(HOME_VLOG_LIMIT)


def _vlog_seo_description(sadrzaj, max_len=160):
    text = strip_tags(sadrzaj).strip()
    if len(text) <= max_len:
        return text
    trimmed = text[:max_len - 1]
    if ' ' in trimmed:
        trimmed = trimmed.rsplit(' ', 1)[0]
    return f'{trimmed}…'


def home(request):
    hero_banners = _banners_with_image(Banner.objects.filter(
        tip=Banner.BannerType.HERO, aktivan=True,
    ).order_by('redoslijed', '-id'))
    grid_banners = _banners_with_image(Banner.objects.filter(
        tip=Banner.BannerType.GRID, aktivan=True,
    ).order_by('redoslijed', '-id'))[:3]
    featured_banners = _banners_with_image(Banner.objects.filter(
        tip=Banner.BannerType.FEATURED, aktivan=True,
    ).order_by('redoslijed', '-id'))
    spotlight_banner = _banners_with_image(Banner.objects.filter(
        tip=Banner.BannerType.SPOTLIGHT, aktivan=True,
    ).order_by('redoslijed', '-id')).first()

    filter_params = _get_filter_params(request)
    filters_active = _filters_active(filter_params)

    latest_products = []
    featured_products = []
    home_vlogs = []
    page_obj = None
    search_products = []

    if filters_active:
        products, filter_params = _apply_product_filters(_product_queryset(), request)
        page_obj = _paginate_home_products(request, products, filter_params)
        search_products = page_obj.object_list
    else:
        latest_products = _home_latest_products()
        featured_products = _home_featured_products()
        home_vlogs = _home_vlogs()

    first_hero = hero_banners.first()
    first_grid_banner = grid_banners.first()
    lcp_image_url = None
    if not filters_active:
        if first_grid_banner and first_grid_banner.slika:
            lcp_image_url = request.build_absolute_uri(first_grid_banner.slika.url)
        elif first_hero and first_hero.slika:
            lcp_image_url = request.build_absolute_uri(first_hero.slika.url)

    spotlight = None
    if spotlight_banner:
        spotlight_width, spotlight_height = image_field_dimensions(
            spotlight_banner.slika, default=(1200, 800),
        )
        spotlight = {
            'title': spotlight_banner.naslov,
            'description': spotlight_banner.podnaslov,
            'image': spotlight_banner.slika.url,
            'image_width': spotlight_width,
            'image_height': spotlight_height,
            'cta': spotlight_banner.tekst_dugmeta,
            'url': spotlight_banner.get_link_href(),
        }

    context = {
        **_base_context(),
        'lcp_image_url': lcp_image_url,
        'hero_slides': [_banner_to_hero_slide(b) for b in hero_banners],
        'grid_banners': [_banner_to_card(b) for b in grid_banners],
        'featured_cards': [_banner_to_card(b) for b in featured_banners],
        'spotlight': spotlight,
        'latest_products': latest_products,
        'featured_products': featured_products,
        'home_vlogs': home_vlogs,
        'showcase_brands': _showcase_brands() if not filters_active else [],
        'search_products': search_products,
        'page_obj': page_obj,
        'filter_params': filter_params,
        'catalog_query': _catalog_query_string(filter_params) if filters_active else '',
        'elided_page_range': (
            page_obj.paginator.get_elided_page_range(page_obj.number) if page_obj else []
        ),
        'selected_brand': Brand.objects.filter(slug=filter_params['brend']).first() if filter_params.get('brend') else None,
        'canonical_url': settings.SITE_URL.rstrip('/') + '/',
    }
    return render(request, 'home.html', context)


def vlog_detail(request, slug):
    try:
        vlog = get_object_or_404(HomeVlog, slug=slug, aktivan=True)
    except DatabaseError:
        logger.exception(
            'HomeVlog tabela nije dostupna — pokreni: python manage.py migrate',
        )
        raise Http404 from None
    image_width, image_height = image_field_dimensions(vlog.slika, default=(1200, 800))
    other_vlogs = []
    for other in HomeVlog.objects.filter(aktivan=True).exclude(slika='').exclude(pk=vlog.pk).order_by(
        'redoslijed', '-id',
    )[:3]:
        width, height = image_field_dimensions(other.slika, default=(400, 300))
        other_vlogs.append({
            'slug': other.slug,
            'naslov': other.naslov,
            'slika_url': other.slika.url,
            'image_width': width,
            'image_height': height,
        })

    lcp_image_url = request.build_absolute_uri(vlog.slika.url)
    seo_description = _vlog_seo_description(vlog.sadrzaj)

    context = {
        **_base_context(),
        'vlog': vlog,
        'other_vlogs': other_vlogs,
        'lcp_image_url': lcp_image_url,
        'image_width': image_width,
        'image_height': image_height,
        'seo_title': f'{vlog.naslov} | Vlog — opremazaribolov.ba',
        'seo_description': seo_description,
        'canonical_url': settings.SITE_URL.rstrip('/') + vlog.get_absolute_url(),
        'og_image': request.build_absolute_uri(vlog.slika.url),
    }
    return render(request, 'vlog_detail.html', context)


def about_us(request):
    context = {
        **_base_context(),
        'seo_title': 'O nama — opremazaribolov.ba',
        'seo_description': (
            'Saznajte više o opremazaribolov.ba — dugogodišnje iskustvo u ribolovu '
            'i opremi, sada u online prodaji za ribare u Bosni i Hercegovini.'
        ),
        'canonical_url': settings.SITE_URL.rstrip('/') + reverse('about_us'),
    }
    return render(request, 'pages/about.html', context)


def payment_methods(request):
    context = {
        **_base_context(),
        'seo_title': 'Način plaćanja — opremazaribolov.ba',
        'seo_description': (
            'Plaćanje prilikom preuzimanja, dostava brzom poštom u roku 48h i sigurno slanje pošiljki.'
        ),
        'canonical_url': settings.SITE_URL.rstrip('/') + reverse('payment_methods'),
    }
    return render(request, 'pages/payment.html', context)


def vlog_list(request):
    context = {
        **_base_context(),
        'vlogs': _vlog_cards(),
        'seo_title': 'Blog — opremazaribolov.ba',
        'seo_description': (
            'Blog i vlog opremazaribolov.ba — savjeti, priče i novosti iz svijeta ribolova.'
        ),
        'canonical_url': settings.SITE_URL.rstrip('/') + reverse('vlog_list'),
    }
    return render(request, 'vlog_list.html', context)


def category_detail(request, slug):
    category = get_object_or_404(
        Category.objects.prefetch_related('podkategorije__podkategorije'),
        slug=slug, aktivan=True,
    )
    category_ids = category.get_descendant_ids()
    products_qs = _product_queryset().filter(kategorija_id__in=category_ids)
    products, filter_params = _apply_product_filters(
        products_qs,
        request,
        allowed_category_ids=category_ids,
    )

    context = {
        **_base_context(),
        'category': category,
        'products': products,
        'filter_categories': _filter_categories(),
        'filter_params': filter_params,
        # SEO
        'seo_title': category.meta_title or f"{category.naziv} | Oprema za ribolov",
        'seo_description': category.meta_description or f"{category.naziv} — kvalitetna oprema za ribolov po povoljnim cijenama. Brza dostava širom Bosne i Hercegovine.",
        'canonical_url': settings.SITE_URL.rstrip('/') + category.get_absolute_url(),
    }
    return render(request, 'category.html', context)


def product_detail(request, slug):
    # Allow sold-out products (na_stanju=False) to be shown on product page
    # but only active ones. We prefetch ALL variations (not just in-stock) so we
    # can display "Rasprodato" for out-of-stock variations.
    product = get_object_or_404(
        Product.objects.filter(aktivan=True)
        .select_related('kategorija', 'brend')
        .prefetch_related(
            Prefetch('varijacije', queryset=ProductVariation.objects.order_by('redoslijed', 'id')),
        ),
        slug=slug,
    )
    lcp_image_url = None
    product_image_width, product_image_height = 800, 800
    if product.prikazna_slika:
        product_image_width, product_image_height = image_field_dimensions(
            product.prikazna_slika, default=(800, 800),
        )
        lcp_image_url = request.build_absolute_uri(product.prikazna_slika.url)

    context = {
        **_base_context(),
        'product': product,
        'ima_varijacije': product.varijacije.count() > 0,
        'lcp_image_url': lcp_image_url,
        'product_image_width': product_image_width,
        'product_image_height': product_image_height,
        # SEO
        'seo_title': product.seo_title,
        'seo_description': product.seo_description,
        'canonical_url': settings.SITE_URL.rstrip('/') + product.get_absolute_url(),
        'og_image': (
            request.build_absolute_uri(product.prikazna_slika.url)
            if product.prikazna_slika else None
        ),
    }
    return render(request, 'product_detail.html', context)


@require_POST
def add_to_cart(request, slug):
    # Fetch product allowing sold-out (we validate stock below)
    product = get_object_or_404(Product.objects.filter(aktivan=True).select_related('kategorija'), slug=slug)
    cart = Cart(request)
    variation = None
    variation_id = request.POST.get('variation_id', '').strip()
    quantity = max(1, int(request.POST.get('quantity', 1) or 1))

    stay_on_page = request.POST.get('stay') == '1'

    if not product.na_stanju and not product.varijacije.exists():
        msg = 'Artikal je rasprodan.'
        if stay_on_page:
            return JsonResponse({'ok': False, 'message': msg}, status=400)
        messages.error(request, msg)
        return redirect('product_detail', slug=slug)

    if product.varijacije.count() > 0:
        if not variation_id:
            if stay_on_page:
                return JsonResponse({'ok': False, 'message': 'Odaberite varijantu.'}, status=400)
            messages.error(request, 'Odaberite varijantu prije dodavanja u korpu.')
            return redirect('product_detail', slug=slug)
        variation = get_object_or_404(
            ProductVariation, pk=variation_id, artikal=product, na_stanju=True,
        )
    elif variation_id:
        variation = get_object_or_404(
            ProductVariation, pk=variation_id, artikal=product, na_stanju=True,
        )

    # Double-check stock on the chosen item
    if variation and not variation.na_stanju:
        msg = 'Varijanta je rasprodana.'
        if stay_on_page:
            return JsonResponse({'ok': False, 'message': msg}, status=400)
        messages.error(request, msg)
        return redirect('product_detail', slug=slug)
    if not variation and not product.na_stanju:
        msg = 'Artikal je rasprodan.'
        if stay_on_page:
            return JsonResponse({'ok': False, 'message': msg}, status=400)
        messages.error(request, msg)
        return redirect('product_detail', slug=slug)

    cart.add(product, variation=variation, quantity=quantity)
    cart.clear_coupon()
    label = variation.naziv if variation else product.naziv
    message = f'"{label}" je dodano u korpu.'

    # Trigger upsell check
    _check_and_set_pending_upsell(request, product)

    if stay_on_page:
        return JsonResponse({
            'ok': True,
            'message': message,
            'cart_count': len(cart),
        })
    messages.success(request, message)
    if request.POST.get('redirect_to') == 'cart':
        return redirect('cart')
    return redirect('product_detail', slug=slug)


@require_POST
def add_upsell_to_cart(request, offer_id, product_id):
    offer = get_object_or_404(UpsellOffer, pk=offer_id, aktivan=True)
    product = get_object_or_404(Product.objects.filter(aktivan=True, na_stanju=True), pk=product_id)

    # Validate product is in the offer
    if not offer.ponuda_artikli.filter(pk=product.pk).exists():
        messages.error(request, 'Ovaj artikal nije dio ponude.')
        return redirect('checkout' if request.POST.get('next') == 'checkout' else 'cart')

    variation = None
    var_id = request.POST.get('variation_id')
    if var_id:
        try:
            variation = ProductVariation.objects.get(
                pk=int(var_id), artikal=product, na_stanju=True
            )
        except (ProductVariation.DoesNotExist, ValueError):
            messages.error(request, 'Nevažeća varijacija.')
            return redirect('checkout' if request.POST.get('next') == 'checkout' else 'cart')

    # Compute price with discount
    base_price = variation.prikazna_cijena if variation else product.prikazna_cijena
    final_price = base_price
    if offer.popust_postotak:
        final_price = (base_price * (Decimal('1') - offer.popust_postotak / Decimal('100'))).quantize(Decimal('0.01'))
    if offer.popust_km:
        final_price = max(Decimal('0'), final_price - offer.popust_km).quantize(Decimal('0.01'))

    cart = Cart(request)
    cart.add(product, variation=variation, quantity=1, custom_price=final_price)

    if offer.prikaz == UpsellOffer.PrikazTip.POPUP:
        from .upsell import clear_upsell_offer_session
        clear_upsell_offer_session(request)

    label = variation.naziv if variation else product.naziv
    messages.success(request, f'"{product.naziv} - {label}" je dodato u korpu sa specijalnom ponudom!')
    if request.POST.get('next') == 'checkout':
        return redirect('checkout')
    return redirect('cart')


def _check_and_set_pending_upsell(request, added_product):
    """Pokreni popup upsell samo kad se u korpu doda trigger artikal ili artikal iz trigger kategorije."""
    from django.db.models import Q

    from .upsell import set_upsell_offer_session

    try:
        offers = (
            UpsellOffer.objects.filter(
                aktivan=True,
                prikaz=UpsellOffer.PrikazTip.POPUP,
            )
            .filter(Q(trigger_artikal__isnull=False) | Q(trigger_kategorija__isnull=False))
            .order_by('redoslijed', 'id')
            .select_related('trigger_artikal', 'trigger_kategorija')
        )
        for offer in offers:
            triggered = False
            if offer.trigger_artikal_id == added_product.pk:
                triggered = True
            elif offer.trigger_kategorija_id and added_product.kategorija_id:
                trigger_cat = offer.trigger_kategorija
                if added_product.kategorija_id in trigger_cat.get_descendant_ids():
                    triggered = True
            if triggered:
                set_upsell_offer_session(request, offer.pk)
                break
    except Exception:
        pass


def _loyalty_za_kupon(request):
    if not request.user.is_authenticated:
        return None
    card = getattr(request.user, 'loyalty_kartica', None)
    if card is None:
        card = osiguraj_loyalty_karticu(request.user)
    return card


def _cart_context(request, cart):
    loyalty_card = _loyalty_za_kupon(request)
    applied_code = cart.get_coupon_code()
    summary = izracunaj_sazetak(
        cart.ukupno,
        user=request.user,
        coupon_code=applied_code,
    )
    cart_items = list(cart)
    if cart_items:
        slug_map = dict(
            Product.objects.filter(
                pk__in={item['product_id'] for item in cart_items},
            ).values_list('pk', 'slug'),
        )
        for item in cart_items:
            item['slug'] = item.get('slug') or slug_map.get(item['product_id'], '')
    return {
        'cart': cart,
        'cart_items': cart_items,
        'cart_total': summary['ukupno'],
        'summary': summary,
        'pricing': summary['pdv'],
        'coupon_form': CouponForm(initial={'kod': ''}),
        'applied_coupon_code': applied_code if cart.is_coupon_applied() else '',
        'loyalty_card': loyalty_card,
    }


def cart_view(request):
    from .upsell import get_cart_banner_upsell_offers

    cart = Cart(request)
    if not cart.should_keep_coupon_on_cart_view():
        cart.clear_coupon()
    elif not cart.is_coupon_applied() and cart.request.session.get(Cart.COUPON_KEY):
        cart.clear_coupon()
    context = {
        **_base_context(),
        **_cart_context(request, cart),
        'upsell_banners_above': get_cart_banner_upsell_offers(UpsellOffer.PrikazTip.BANNER_IZNAD),
        'upsell_banners_below': get_cart_banner_upsell_offers(UpsellOffer.PrikazTip.BANNER_ISPOD),
    }
    return render(request, 'cart.html', context)


@require_POST
def update_cart(request):
    cart = Cart(request)
    for key in list(cart.cart.keys()):
        qty = request.POST.get(f'quantity_{key}')
        if qty is not None:
            try:
                cart.set_quantity(key, int(qty))
            except (TypeError, ValueError):
                pass
    cart.clear_coupon()
    return redirect('cart')


@require_POST
def apply_coupon(request):
    cart = Cart(request)
    form = CouponForm(request.POST)
    if form.is_valid():
        kod = form.cleaned_data['kod']
        coupon, error = validiraj_kupon(kod, request.user)
        if error:
            messages.error(request, error)
        else:
            cart.set_coupon_code(coupon.kod)
            cart.mark_coupon_keep_after_apply()
            messages.success(request, f'Kupon {coupon.kod} primijenjen — popust {coupon.postotak}%.')
    else:
        for error in form.errors.get('kod', []):
            messages.error(request, error)
    redirect_to = request.POST.get('next', 'cart')
    if redirect_to == 'checkout':
        return redirect('checkout')
    return redirect('cart')


@require_POST
def remove_coupon(request):
    cart = Cart(request)
    cart.clear_coupon()
    messages.info(request, 'Kupon je uklonjen.')
    redirect_to = request.POST.get('next', 'cart')
    if redirect_to == 'checkout':
        return redirect('checkout')
    return redirect('cart')


@require_POST
def remove_from_cart(request, key):
    cart = Cart(request)
    cart.remove(key)
    cart.clear_coupon()
    messages.info(request, 'Artikal je uklonjen iz korpe.')
    return redirect('cart')


def _checkout_initial(request):
    if not request.user.is_authenticated:
        return {}
    profil = getattr(request.user, 'profil', None)
    return {
        'ime_prezime': request.user.get_full_name() or request.user.email,
        'email': request.user.email,
        'telefon': profil.telefon if profil else '',
        'adresa': profil.adresa if profil else '',
        'grad': profil.grad if profil else '',
        'postanski_broj': profil.postanski_broj if profil else '',
    }


def _save_profile_from_checkout(user, cleaned_data):
    profil, _ = UserProfile.objects.get_or_create(user=user)
    profil.telefon = cleaned_data['telefon']
    profil.adresa = cleaned_data['adresa']
    profil.grad = cleaned_data['grad']
    profil.postanski_broj = cleaned_data.get('postanski_broj', '')
    profil.save(update_fields=['telefon', 'adresa', 'grad', 'postanski_broj'])
    user.first_name = cleaned_data['ime_prezime']
    user.email = cleaned_data['email']
    user.save(update_fields=['first_name', 'email'])


def checkout(request):
    cart = Cart(request)
    if not cart.item_count:
        messages.warning(request, 'Korpa je prazna.')
        return redirect('home')

    form = CheckoutForm(initial=_checkout_initial(request))
    if request.method == 'POST':
        form = CheckoutForm(request.POST)
        if form.is_valid():
            summary = cart.sazetak(user=request.user)
            order = Order.objects.create(
                korisnik=request.user if request.user.is_authenticated else None,
                ime_prezime=form.cleaned_data['ime_prezime'],
                email=form.cleaned_data['email'],
                telefon=form.cleaned_data['telefon'],
                adresa=form.cleaned_data['adresa'],
                grad=form.cleaned_data['grad'],
                postanski_broj=form.cleaned_data.get('postanski_broj', ''),
                napomena=form.cleaned_data.get('napomena', ''),
                medjuzbir=summary['medjuzbir'],
                dostava=summary['dostava'],
                popust=summary['popust'],
                kupon_kod=summary.get('kupon_kod', ''),
                ukupno=summary['ukupno'],
            )
            if request.user.is_authenticated:
                _save_profile_from_checkout(request.user, form.cleaned_data)
            for item in cart:
                product, variation = cart.get_product_and_variation(item)
                if not product:
                    messages.error(request, 'Neki artikli više nisu dostupni. Osvježite korpu.')
                    order.delete()
                    return redirect('cart')
                OrderItem.objects.create(
                    narudzba=order,
                    artikal=product,
                    varijacija=variation,
                    naziv=item['product_naziv'],
                    product_naziv=item['product_naziv'],
                    varijacija_naziv=item.get('varijacija_naziv', ''),
                    sifra=item['sifra'],
                    cijena=item['cijena_decimal'],
                    kolicina=item['quantity'],
                )

            # Uvijek sinkronizuj narudžbu na loyalty program (i ažuriraj loyalty bodove)
            # Email je sekundaran — ne smije blokirati sync
            logger.info("Checkout završen, pripremam sync za narudžbu #%s", order.broj)
            if request.user.is_authenticated:
                azuriraj_loyalty_nakon_narudzbe(order)
                # Cim kupac sa karticom poruci, automatski evidentiraj karticu preko korisnik API
                card = getattr(request.user, 'loyalty_kartica', None)
                if card:
                    logger.info("Automatski sync korisnik (kartica) za kupca %s prije narudžbe", request.user.email)
                    sync_korisnik(request.user)
            result = sync_narudzba(order)
            if result is None:
                logger.warning("sync_narudzba vratio None (vjerovatno SYNC nije aktivan)")
            elif isinstance(result, dict) and not result.get('ok', True):
                logger.error("sync_narudzba nije uspio: %s", result)
            cart.clear()

            try:
                send_order_emails(order)
            except EmailNotConfiguredError:
                messages.warning(
                    request,
                    'Narudžba je sačuvana, ali email nije poslan. '
                    'Dodajte EMAIL_APP_PASSWORD u .env datoteku i restartujte server.',
                )
            except Exception:
                logger.exception('Slanje emaila za narudžbu #%s nije uspjelo.', order.broj)
                if settings.DEBUG:
                    messages.warning(
                        request,
                        'Narudžba je sačuvana, ali email nije poslan. '
                        'Provjerite EMAIL_APP_PASSWORD u .env i restartujte Django server.',
                    )
                else:
                    messages.warning(
                        request,
                        'Narudžba je sačuvana, ali potvrda emailom nije poslana. Kontaktirajte nas.',
                    )

            messages.success(request, 'Narudžba je uspješno poslana!')
            return redirect('order_success', broj=order.broj)

    from .upsell import get_checkout_upsell_offers

    context = {
        **_base_context(),
        **_cart_context(request, cart),
        'form': form,
        'upsell_checkout_offers': get_checkout_upsell_offers(cart),
    }
    return render(request, 'checkout.html', context)


def order_success(request, broj):
    order = get_object_or_404(Order, broj=broj)
    context = {
        **_base_context(),
        'order': order,
    }
    return render(request, 'order_success.html', context)


def verify_turnstile(token, request):
    secret = getattr(settings, 'TURNSTILE_SECRET_KEY', '')
    if not secret or not token:
        return False
    try:
        response = requests.post(
            'https://challenges.cloudflare.com/turnstile/v0/siteverify',
            data={
                'secret': secret,
                'response': token,
                'remoteip': request.META.get('REMOTE_ADDR', ''),
            },
            timeout=10,
        )
        result = response.json()
        return result.get('success', False)
    except Exception:
        return False


def register(request):
    if request.user.is_authenticated:
        return redirect('account')

    form = RegisterForm()
    if request.method == 'POST':
        form = RegisterForm(request.POST)
        if form.is_valid():
            token = form.cleaned_data.get('cf_turnstile_response')
            secret = getattr(settings, 'TURNSTILE_SECRET_KEY', '')
            if secret and not verify_turnstile(token, request):
                form.add_error(None, 'Turnstile provjera nije uspjela. Molimo pokušajte ponovo.')
            else:
                email = form.cleaned_data['email']
                user = User.objects.create_user(
                    username=email,
                    email=email,
                    password=form.cleaned_data['lozinka'],
                    first_name=form.cleaned_data['ime_prezime'],
                )
                UserProfile.objects.create(
                    user=user,
                    telefon=form.cleaned_data.get('telefon', ''),
                )
                Order.objects.filter(email__iexact=email, korisnik__isnull=True).update(korisnik=user)
                kreiraj_loyalty_karticu(user)
                logger.info("Register: sync_korisnik za novog korisnika %s", email)
                sync_korisnik(user)
                login(request, user)
                messages.success(request, 'Dobrodošli! Vaš nalog je kreiran.')
                return redirect('account')

    context = {
        **_base_context(),
        'form': form,
        'turnstile_site_key': getattr(settings, 'TURNSTILE_SITE_KEY', ''),
    }
    return render(request, 'auth/register.html', context)


def login_view(request):
    if request.user.is_authenticated:
        return redirect('account')

    next_url = request.GET.get('next', '') or request.POST.get('next', '')
    form = LoginForm(request=request)
    if request.method == 'POST':
        form = LoginForm(request.POST, request=request)
        if form.is_valid():
            login(request, form.user)
            Order.objects.filter(
                email__iexact=form.user.email,
                korisnik__isnull=True,
            ).update(korisnik=form.user)
            osiguraj_loyalty_karticu(form.user)
            messages.success(request, 'Uspješno ste se prijavili.')
            redirect_to = request.POST.get('next') or next_url
            if redirect_to and redirect_to.startswith('/'):
                return redirect(redirect_to)
            return redirect('account')

    context = {
        **_base_context(),
        'form': form,
        'next_url': next_url,
    }
    return render(request, 'auth/login.html', context)


def logout_view(request):
    logout(request)
    messages.info(request, 'Odjavljeni ste.')
    return redirect('home')


@login_required(login_url='login')
def account(request):
    profil, _ = UserProfile.objects.get_or_create(user=request.user)
    profile_form = ProfileForm(initial={
        'ime_prezime': request.user.get_full_name() or request.user.first_name,
        'email': request.user.email,
        'telefon': profil.telefon,
        'adresa': profil.adresa,
        'grad': profil.grad,
        'postanski_broj': profil.postanski_broj,
    })

    if request.method == 'POST':
        profile_form = ProfileForm(request.POST)
        if profile_form.is_valid():
            email = profile_form.cleaned_data['email'].strip().lower()
            if User.objects.filter(email__iexact=email).exclude(pk=request.user.pk).exists():
                messages.error(request, 'Email je već u upotrebi.')
            elif User.objects.filter(username__iexact=email).exclude(pk=request.user.pk).exists():
                messages.error(request, 'Email je već u upotrebi.')
            else:
                request.user.first_name = profile_form.cleaned_data['ime_prezime']
                request.user.email = email
                request.user.username = email
                request.user.save(update_fields=['first_name', 'email', 'username'])
                profil.telefon = profile_form.cleaned_data.get('telefon', '')
                profil.adresa = profile_form.cleaned_data.get('adresa', '')
                profil.grad = profile_form.cleaned_data.get('grad', '')
                profil.postanski_broj = profile_form.cleaned_data.get('postanski_broj', '')
                profil.save()
                logger.info("Profile update: sync_korisnik za %s", request.user.email)
                sync_korisnik(request.user)
                messages.success(request, 'Podaci naloga su ažurirani.')

    orders = (
        Order.objects.filter(korisnik=request.user)
        .prefetch_related('stavke')
        .order_by('-kreirana')
    )
    loyalty = loyalty_kontekst(osiguraj_loyalty_karticu(request.user))

    context = {
        **_base_context(),
        'profile_form': profile_form,
        'orders': orders,
        'loyalty': loyalty,
    }
    return render(request, 'account/index.html', context)


@login_required(login_url='login')
def account_order_detail(request, broj):
    order = get_object_or_404(
        Order.objects.prefetch_related('stavke'),
        broj=broj,
        korisnik=request.user,
    )
    context = {
        **_base_context(),
        'order': order,
        'summary': sazetak_iz_narudzbe(order),
        'pricing': order.pdv_pregled,
    }
    return render(request, 'account/order_detail.html', context)