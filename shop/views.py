from django.shortcuts import render, redirect, get_object_or_404
from django.http import Http404, JsonResponse
from django.views.decorators.http import require_POST
from django.views.decorators.debug import sensitive_post_parameters
from django.urls import reverse
from django.conf import settings
import json
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from .models import Product, Brand, Category, StoreSettings, Banner
from .forms import LogoForm, BrandForm, CategoryForm, ProductForm, BannerForm, SaleForm, StoreSettingsForm, FeaturedProductsForm
from django.db import models
from django.contrib.auth.decorators import user_passes_test
from django.contrib.auth.views import LoginView, LogoutView
from .forms import PanelLoginForm


PANEL_SECTIONS = [
    {'slug': 'narudzbe', 'title': 'Narudžbe', 'description': 'Pregled narudžbi i novih zahtjeva kupaca.'},
    {'slug': 'moduli', 'title': 'Moduli', 'description': 'Licence i zahtjevi za aktivaciju.'},
    {'slug': 'chatbot', 'title': 'ChatBot', 'description': 'AI asistent, poruke i izgled razgovora.', 'icon': 'M4 4h16v12H9l-5 4z M8 9h8 M8 12h5'},
    {'slug': 'backup', 'title': 'Backup', 'description': 'Automatske kopije baze svakih sat vremena i vraćanje podataka.'},
    {'slug': 'loyalty', 'title': 'Loyalty', 'description': 'Kartice kupaca i rasponi loyalty popusta.'},
    {'slug': 'korisnici', 'title': 'Korisnici', 'description': 'Pregled svih registrovanih korisnika.'},
    {'slug': 'podesavanje', 'title': 'Podešavanje', 'description': 'Upravljajte postavkama trgovine.'},
    {'slug': 'artikli', 'title': 'Artikli', 'description': 'Pregled artikala u vašoj trgovini.', 'icon': 'M4 4h16v16H4z M4 9h16 M9 9v11'},
    {'slug': 'brendovi', 'title': 'Brendovi', 'description': 'Brendovi koji čine vašu ponudu.', 'icon': 'm12 3 9 9-9 9-9-9z M9 12h6'},
    {'slug': 'kategorije', 'title': 'Kategorije', 'description': 'Organizacija i pregled kategorija.', 'icon': 'M3 3h7v7H3z M14 3h7v7h-7z M3 14h7v7H3z M14 14h7v7h-7z'},
    {'slug': 'banneri', 'title': 'Banneri', 'description': 'Vizuali i istaknute ponude na početnoj.', 'icon': 'M3 4h18v16H3z M3 17l6-6 4 4 3-3 5 5 M16 8h1'},
    {'slug': 'akcije', 'title': 'Akcije', 'description': 'Pregled proizvoda u akcijskoj ponudi.', 'icon': 'M5 19 19 5 M8 6a2 2 0 1 1-4 0 2 2 0 0 1 4 0 M20 18a2 2 0 1 1-4 0 2 2 0 0 1 4 0'},
    {'slug': 'logo', 'title': 'Logo', 'description': 'Vizuelni identitet vaše trgovine.', 'icon': 'M5 20V4h7a4 4 0 0 1 0 8H5 M12 12a4 4 0 0 1 0 8H5'},
]


def can_manage_panel(user):
    return user.is_active and (user.is_staff or user.is_superuser)


panel_required = user_passes_test(can_manage_panel, login_url='panel_login')


class PanelLoginView(LoginView):
    template_name = 'shop/panel_login.html'
    authentication_form = PanelLoginForm
    next_page = 'panel'


class PanelLogoutView(LogoutView):
    next_page = 'panel_login'


@sensitive_post_parameters('api_key')
@panel_required
def panel(request, section=None):
    selected = next((item for item in PANEL_SECTIONS if item['slug'] == section), None)
    if section and not selected:
        raise Http404('Nepostojeća sekcija')
    store_settings = StoreSettings.objects.filter(pk=1).first()
    if section == 'narudzbe':
        if request.method == 'POST':
            order = get_object_or_404(Order, pk=request.POST.get('order_id') if request.POST.get('order_id', '').isdigit() else None)
            if request.POST.get('action') == 'pack':
                with transaction.atomic():
                    order = Order.objects.select_for_update().get(pk=order.pk)
                    if order.status == 'placed':
                        order.status = 'shipped'
                        order.save(update_fields=['status'])
            return redirect('panel_section', section='narudzbe')
        if 'preview' in request.GET:
            order_id = request.GET.get('preview', '')
            order = get_object_or_404(Order.objects.exclude(status='draft'), pk=order_id if order_id.isdigit() else None)
            items = list(order.items.select_related('product'))
            for item in items:
                item.line_total = item.unit_price * item.quantity
            response = render(request, 'shop/partials/panel_order_preview.html', {
                'order': order, 'items': items, 'customer_only': request.GET.get('customer_only') == '1',
                'subtotal': sum((item.line_total for item in items), Decimal('0')),
            })
            response['Cache-Control'] = 'no-store'
            return response
        order_filter = request.GET.get('status', 'nove')
        if order_filter not in ('nove', 'poslate', 'sve'):
            order_filter = 'nove'
        orders = Order.objects.exclude(status='draft')
        if order_filter != 'sve':
            orders = orders.filter(status='placed' if order_filter == 'nove' else 'shipped')
        orders = orders.prefetch_related('items__product').order_by('-created_at', '-pk')
        return render(request, 'shop/panel.html', {
            'sections': PANEL_SECTIONS, 'selected': selected, 'site_settings': store_settings,
            'orders_section': True, 'orders': orders, 'order_filter': order_filter,
        })
    if section == 'chatbot':
        from .forms import ChatBotSettingsForm
        from .models import ChatBotSettings
        from .chatbot import api_key
        config = ChatBotSettings.objects.filter(pk=1).first() or ChatBotSettings(pk=1)
        chatbot_form = ChatBotSettingsForm(request.POST if request.method == 'POST' else None, instance=config)
        if request.method == 'POST' and chatbot_form.is_valid():
            chatbot_form.save()
            from django.contrib import messages
            messages.success(request, 'ChatBot postavke su spremljene.')
            return redirect('panel_section', section='chatbot')
        return render(request, 'shop/panel.html', {'sections': PANEL_SECTIONS, 'selected': selected, 'site_settings': store_settings, 'edit_form': chatbot_form, 'editing': True, 'chatbot_section': True, 'chatbot_connected': bool(api_key(config)), 'chatbot_guided': config.guided_enabled})
    if section == 'moduli':
        return render(request, 'shop/panel.html', {'sections': PANEL_SECTIONS, 'selected': selected, 'site_settings': store_settings, 'modules_section': True})
    if section == 'backup':
        from .backups import list_backups, prune_backups
        from django.core.paginator import Paginator
        from django.contrib import messages
        try:
            prune_backups()
            backups = list_backups()
        except OSError:
            backups = []
            messages.error(request, 'Nije moguće pročitati backup kopije.')
        backup_page = Paginator(backups, 10).get_page(request.GET.get('page'))
        return render(request, 'shop/panel.html', {
            'sections': PANEL_SECTIONS, 'selected': selected,
            'site_settings': store_settings, 'backup_section': True, 'backups': backup_page,
        })
    logo_form = None
    if section == 'loyalty':
        from .modules import loyalty_enabled
        if not loyalty_enabled():
            raise Http404('Loyalty modul nije aktivan.')
        from .forms import LoyaltySettingsFormSet, LoyaltyFeaturesForm, LoyaltyPointsSettingsForm
        from .loyalty import rules
        from .models import LoyaltyCard, LoyaltyRules
        if 'card' in request.GET:
            card_id = request.GET.get('card', '')
            card = get_object_or_404(LoyaltyCard.objects.select_related('customer'), pk=card_id if card_id.isdigit() else None)
            purchases = Order.objects.filter(customer_id=card.customer_id).exclude(status='draft').order_by('-created_at', '-pk')
            spent = purchases.filter(status__in=['placed', 'shipped', 'completed']).aggregate(
                value=models.Sum(models.F('total') - models.F('delivery_price')),
            )['value'] or Decimal('0')
            return render(request, 'shop/partials/loyalty_purchases.html', {
                'card': card, 'purchases': purchases.prefetch_related('items__product'), 'spent': spent,
            })
        loyalty_tab = 'settings' if request.GET.get('tab') == 'settings' or request.method == 'POST' else 'search'
        feature_post = request.method == 'POST' and request.POST.get('settings_section') == 'features'
        points_post = request.method == 'POST' and request.POST.get('settings_section') == 'points'
        points_form = LoyaltyPointsSettingsForm(request.POST if points_post else None, instance=LoyaltyRules.objects.filter(pk=1).first() or LoyaltyRules())
        if points_post and points_form.is_valid():
            points_form.save()
            from django.contrib import messages
            messages.success(request, 'Pravila bodova su spremljena.')
            return redirect(reverse('panel_section', args=['loyalty']) + '?tab=settings')
        features_form = LoyaltyFeaturesForm(request.POST if feature_post else None, instance=rules())
        if feature_post and features_form.is_valid():
            features_form.save()
            from django.contrib import messages
            messages.success(request, 'Loyalty opcije su spremljene.')
            return redirect(reverse('panel_section', args=['loyalty']) + '?tab=settings')
        formset = LoyaltySettingsFormSet(request.POST if request.method == 'POST' and not feature_post and not points_post else None)
        if request.method == 'POST' and not feature_post and not points_post and formset.is_valid():
            formset.save()
            from django.contrib import messages
            messages.success(request, 'Loyalty postavke su spremljene.')
            return redirect(reverse('panel_section', args=['loyalty']) + '?tab=settings')
        card_search = request.GET.get('q', '').strip()[:150]
        cards = LoyaltyCard.objects.select_related('customer').order_by('-created_at', '-pk')
        for term in card_search.split():
            cards = cards.filter(
                models.Q(customer__username__icontains=term) |
                models.Q(customer__first_name__icontains=term) |
                models.Q(customer__last_name__icontains=term) |
                models.Q(number__icontains=term) |
                models.Q(customer__shop_contact__phone__icontains=term) |
                models.Q(customer_id__in=Order.objects.filter(phone__icontains=term).values('customer_id'))
            )
        return render(request, 'shop/panel.html', {'sections': PANEL_SECTIONS, 'selected': selected, 'site_settings': store_settings, 'loyalty_formset': formset, 'loyalty_cards': cards, 'card_search': card_search, 'loyalty_tab': loyalty_tab, 'loyalty_features_form': features_form, 'loyalty_points_form': points_form})
    if section == 'podesavanje':
        from .forms import SEOSettingsForm, SEODescriptionForm
        from .models import SEOSettings
        is_description_form = request.method == 'POST' and request.POST.get('settings_section') == 'meta_description'
        description_form = SEODescriptionForm(request.POST if is_description_form else None, prefix='meta', instance=SEOSettings.objects.filter(pk=1).first() or SEOSettings(pk=1))
        is_seo_form = request.method == 'POST' and request.POST.get('settings_section') == 'seo'
        seo_form = SEOSettingsForm(request.POST if is_seo_form else None, request.FILES if is_seo_form else None, instance=SEOSettings.objects.filter(pk=1).first() or SEOSettings(pk=1))
        is_featured_form = request.method == 'POST' and request.POST.get('settings_section') == 'featured'
        form = StoreSettingsForm(request.POST if request.method == 'POST' and not is_featured_form and not is_seo_form and not is_description_form else None, instance=store_settings or StoreSettings(pk=1))
        featured_form = FeaturedProductsForm(request.POST if is_featured_form else None)
        submitted_form = description_form if is_description_form else seo_form if is_seo_form else featured_form if is_featured_form else form
        if request.method == 'POST' and submitted_form.is_valid():
            submitted_form.save()
            from django.contrib import messages
            messages.success(request, 'Podešavanje je spremljeno.')
            return redirect('panel_section', section=section)
        return render(request, 'shop/panel.html', {'sections': PANEL_SECTIONS, 'selected': selected, 'site_settings': store_settings, 'edit_form': form, 'editing': True, 'delivery_settings': True, 'featured_form': featured_form, 'seo_form': seo_form, 'description_form': description_form})
    if section == 'logo':
        if request.method == 'POST':
            logo_form = LogoForm(request.POST, request.FILES, instance=store_settings or StoreSettings(pk=1))
            if logo_form.is_valid():
                logo_form.save()
                return redirect('panel_section', section='logo')
        else:
            logo_form = LogoForm(instance=store_settings)
    rows = []
    if section == 'artikli':
        rows = [{
            'id': p.pk,
            'name': p.name,
            'detail': f'{p.price:.2f} KM',
            'original_price': f'{p.original_price:.2f} KM' if p.original_price and p.original_price > p.price else '',
            'image_url': p.image.url if p.image else '',
        } for p in Product.objects.all()]
    elif section == 'kategorije':
        rows = [{'id': c.pk, 'name': c.name, 'image_url': c.image.url if c.image else ''} for c in Category.objects.all()]
    elif section == 'brendovi':
        rows = [{'id': b.pk, 'name': b.name, 'image_url': b.logo.url if b.logo else ''} for b in Brand.objects.all()]
    elif section == 'banneri':
        rows = [{'id': b.pk, 'name': b.title, 'detail': b.get_position_display(), 'image_url': b.image.url if b.image else ''} for b in Banner.objects.all()]
    elif section == 'akcije':
        rows = [{'id': p.pk, 'name': p.name, 'detail': f'{p.price:.2f} KM', 'image_url': p.image.url if p.image else ''} for p in Product.objects.filter(is_active=True).on_sale()]
    elif section == 'korisnici':
        from django.contrib.auth import get_user_model
        rows = list(get_user_model().objects.order_by('-date_joined', '-pk'))
    elif section == 'logo':
        rows = [{'name': 'Logo trgovine', 'image_url': store_settings.logo.url}] if store_settings and store_settings.logo else []
    return render(request, 'shop/panel.html', {'logo_form': logo_form, 'site_settings': store_settings, 'sections': PANEL_SECTIONS, 'selected': selected, 'rows': rows, 'hero_editor': Banner.objects.filter(position='hero').first() if section == 'banneri' else None, 'can_add': section in PANEL_FORMS})


PANEL_FORMS = {
    'artikli': (Product, ProductForm),
    'brendovi': (Brand, BrandForm),
    'kategorije': (Category, CategoryForm),
    'banneri': (Banner, BannerForm),
    'akcije': (Product, SaleForm),
}


@panel_required
def panel_edit(request, section, pk=None):
    if section not in PANEL_FORMS:
        raise Http404('Nepostojeća sekcija')
    model, form_class = PANEL_FORMS[section]
    instance = get_object_or_404(model, pk=pk) if pk is not None else None
    if section == 'akcije' and request.method == 'POST' and instance is None:
        product_id = request.POST.get('product', '')
        if product_id.isdigit():
            instance = Product.objects.filter(pk=product_id).first()
    form = form_class(request.POST if request.method == 'POST' else None,
                      request.FILES if request.method == 'POST' else None, instance=instance)
    if section == 'akcije':
        if instance:
            form.fields['product'].initial = instance.pk
        if pk is not None:
            form.fields['product'].disabled = True
    if request.method == 'POST' and form.is_valid():
        if section == 'akcije':
            if form.cleaned_data['sale_type'] != 'regular':
                from bera_licensing.services import require_module
                require_module(form.cleaned_data['sale_type'])
            target = form.cleaned_data['product']
            target.plus_product = form.cleaned_data['plus_product']
            target.plus_percent = form.cleaned_data['plus_percent']
            for field in ['quantity_percent_2', 'quantity_percent_3', 'quantity_percent_over_3']:
                setattr(target, field, form.cleaned_data.get(field) if form.cleaned_data['sale_type'] == 'quantity' else None)
            target.bundle_percent = form.cleaned_data.get('bundle_percent') if form.cleaned_data['sale_type'] == 'bundle' else None
            target.sale_type = form.cleaned_data['sale_type']
            target.sale_ends_at = form.cleaned_data.get('sale_ends_at')
            target.price = form.cleaned_data['price']
            target.original_price = form.cleaned_data['original_price']
            with transaction.atomic():
                target.save(update_fields=['sale_ends_at', 'sale_type', 'plus_product', 'plus_percent', 'quantity_percent_2', 'quantity_percent_3', 'quantity_percent_over_3', 'bundle_percent', 'price', 'original_price'])
                target.bundle_products.set([member for member in form.cleaned_data['bundle_products'] if member.pk != target.pk] if target.sale_type == 'bundle' else [])
                if target.sale_type == 'bundle' and instance and instance.pk != target.pk:
                    Product.objects.filter(pk=instance.pk).update(sale_type='regular', bundle_percent=None, sale_ends_at=None)
                    instance.bundle_products.clear()
        else:
            form.save()
        from django.contrib import messages
        messages.success(request, 'Izmjene su spremljene.')
        return redirect('panel_section', section=section)
    return render(request, 'shop/panel.html', {
        'sections': PANEL_SECTIONS,
        'selected': next(item for item in PANEL_SECTIONS if item['slug'] == section),
        'site_settings': StoreSettings.objects.filter(pk=1).first(),
        'edit_form': form, 'editing': pk is not None,
    })


@panel_required
@require_POST
def panel_banner_delete(request, pk):
    banner = get_object_or_404(Banner, pk=pk)
    banner.delete()
    from django.contrib import messages
    messages.success(request, 'Banner je uklonjen.')
    return redirect('panel_section', section='banneri')


def home(request, category_pk=None, sale_only=False, new_only=False, bestselling_only=False, brand_pk=None):
    data = json.loads((Path(settings.BASE_DIR) / 'shop/data/home.json').read_text())
    query = request.GET.get('q', '').strip()
    products = Product.objects.filter(is_active=True).select_related('brand', 'category', 'plus_product')
    if bestselling_only:
        products = products.bestsellers()
    if sale_only:
        products = products.on_sale()
    if query:
        products = products.filter(name__icontains=query)
    selected_brand = get_object_or_404(Brand, pk=brand_pk) if brand_pk is not None else None
    if selected_brand:
        products = products.filter(brand=selected_brand)
    selected_category = None
    if category_pk is not None:
        selected_category = get_object_or_404(Category, pk=category_pk)
        products = products.filter(category=selected_category)
    else:
        category_id = request.GET.get('kategorija', '')
        if category_id.isdigit():
            category = get_object_or_404(Category, pk=int(category_id))
            from urllib.parse import urlencode
            target = category.get_absolute_url()
            if query:
                target += '?' + urlencode({'q': query})
            return redirect(target)

    from .chatbot import public_config
    banners = {b.position: b for b in Banner.objects.filter(is_active=True)}
    return render(request, 'shop/home.html', {
        'storefront_version': max((settings.BASE_DIR / 'shop/static' / name).stat().st_mtime_ns for name in ['css/home.css', 'css/bera-home.css', 'home.js']),
        'site_settings': StoreSettings.objects.filter(pk=1).first(),
        'banner': data.get('banner') if not Banner.objects.filter(position='hero').exists() else None,
        'chatbot_settings': public_config(),
        'chatbot_version': max((settings.BASE_DIR / 'shop/static' / name).stat().st_mtime_ns for name in ['chatbot.js', 'css/chatbot.css']),
        'hero_banners': Banner.objects.filter(position='hero', is_active=True).order_by('pk'),
        'offer_left': banners.get('offer_left'),
        'offer_right': banners.get('offer_right'),
        'footer_promo_card': banners.get('footer'),
        'middle_banner': banners.get('middle'),
        'categories': Category.objects.all(),
        'showcase_brands': Brand.objects.filter(product__is_active=True).distinct(),
        'featured_products': products.filter(is_featured=True),
        'bestselling_products': products.bestsellers()[:6],
        'new_products': products[:6],
        'sale_products': products.on_sale(),
        'products': products,
        'bestselling_only': bestselling_only,
        'new_only': new_only,
        'sale_only': sale_only,
        'search_query': query,
        'category_filter': selected_category.pk if selected_category else '',
        'selected_category': selected_category,
        'selected_brand': selected_brand,
        'shop_name': 'BERA',
    })


def product_detail(request, pk):
    product = get_object_or_404(Product.objects.select_related('brand', 'category', 'plus_product'), pk=pk, is_active=True)
    related = Product.objects.select_related('brand', 'category', 'plus_product').filter(is_active=True, category=product.category).exclude(pk=pk)[:6] if product.category_id else []
    bundle_sets = []
    bundles = Product.objects.filter(sale_type='bundle', is_active=True, bundle_percent__gt=0).filter(models.Q(pk=product.pk) | models.Q(bundle_products=product)).prefetch_related('bundle_products').distinct()
    from bera_licensing.services import module_enabled
    if not module_enabled('bundle'):
        bundles = []
    for bundle in bundles:
        members = [bundle, *bundle.bundle_products.all()]
        if len(members) < 2:
            continue
        items = [{
            'product': member,
            'price': (member.price * (1 - bundle.bundle_percent / Decimal('100'))).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP),
        } for member in members]
        bundle_sets.append({
            'bundle': bundle, 'items': items,
            'old_total': sum((member.price for member in members), Decimal('0.00')),
            'new_total': sum((item['price'] for item in items), Decimal('0.00')),
        })
    return render(request, 'shop/product_detail.html', {
        'product': product,
        'bundle_sets': bundle_sets,
        'plus_preview_price': (product.plus_product.price * (1 - product.plus_percent / Decimal('100'))).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP) if product.has_article_plus else None,
        'related_products': related,
        'categories': Category.objects.all(),
        'site_settings': StoreSettings.objects.filter(pk=1).first(),
        'shop_name': 'BERA',
        'storefront_version': max((settings.BASE_DIR / 'shop/static' / name).stat().st_mtime_ns for name in ['css/home.css', 'css/bera-home.css', 'home.js']),
    })


def search_suggestions(request):
    query = request.GET.get('q', '').strip()[:150]
    if not query:
        return JsonResponse({'products': []})
    products = Product.objects.filter(is_active=True, name__icontains=query)
    category_id = request.GET.get('category', '')
    if category_id.isdigit():
        products = products.filter(category_id=int(category_id))
    brand_id = request.GET.get('brand', '')
    if brand_id.isdigit():
        products = products.filter(brand_id=int(brand_id))
    if request.GET.get('sale') == '1':
        products = products.on_sale()
    if request.GET.get('bestselling') == '1':
        products = products.bestsellers()
    return JsonResponse({'products': [
        {'name': p.name, 'url': p.get_absolute_url(),
         'image': p.image.url if p.image else '', 'price': f'{p.price:.2f} KM'}
        for p in products[:8]
    ]})


from django.views.decorators.http import require_POST
from django.views.decorators.csrf import ensure_csrf_cookie
from .cart import cart_context


@ensure_csrf_cookie
def cart(request):
    return render(request, 'shop/cart.html', {
        **cart_context(request),
        'categories': Category.objects.all(),
        'site_settings': StoreSettings.objects.filter(pk=1).first(),
        'shop_name': 'BERA',
        'storefront_version': max((settings.BASE_DIR / 'shop/static' / name).stat().st_mtime_ns for name in ['css/home.css', 'css/bera-home.css', 'home.js']),
    })


@require_POST
def cart_add_bundle(request, pk):
    from bera_licensing.services import require_module
    require_module('bundle')
    bundle = get_object_or_404(Product.objects.prefetch_related('bundle_products'), pk=pk, is_active=True, sale_type='bundle', bundle_percent__gt=0)
    members = [bundle, *bundle.bundle_products.all()]
    if len(members) < 2:
        return JsonResponse({'error': 'Set trenutno nije dostupan.'}, status=400)
    saved = request.session.get('cart', {}).copy()
    for member in members:
        quantity = saved.get(str(member.pk), 0) + 1
        limit = min(member.stock if member.stock else 99, 99)
        if not member.is_active or member.is_out_of_stock or quantity > limit:
            return JsonResponse({'error': f'Artikal „{member.name}“ nije dostupan u traženoj količini.'}, status=400)
        saved[str(member.pk)] = quantity
    request.session['cart'] = saved
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return JsonResponse({'count': cart_context(request)['cart_count'], 'message': 'Set je dodan u korpu.'})
    return redirect('cart')


@require_POST
def cart_update(request, pk):
    product = get_object_or_404(Product.objects.select_related('plus_product'), pk=pk, is_active=True)
    saved = request.session.get('cart', {}).copy()
    try:
        quantity = int(request.POST.get('quantity', 1))
    except (ValueError, TypeError):
        return JsonResponse({'error': 'Unesite ispravnu količinu.'}, status=400)
    if request.POST.get('action') == 'add':
        quantity += saved.get(str(pk), 0)
    limit = min(product.stock if product.stock else 99, 99)
    if quantity < 0 or quantity > limit or (quantity and product.is_out_of_stock):
        return JsonResponse({'error': 'Odabrana količina nije dostupna.'}, status=400)
    if quantity:
        saved[str(pk)] = quantity
    else:
        saved.pop(str(pk), None)
    request.session['cart'] = saved
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        from .cart import article_plus_offer
        offer = article_plus_offer(product, saved) if request.POST.get('action') == 'add' and quantity else None
        return JsonResponse({'count': cart_context(request)['cart_count'], 'offer': offer})
    return redirect('cart')


from django.db import transaction
from .forms import CheckoutForm
from .models import Order, OrderItem


def checkout(request):
    completed = None
    if request.GET.get('success') == '1':
        completed = Order.objects.filter(pk=request.session.get('last_order')).first()
    context = cart_context(request)
    if not context['cart_items'] and completed is None:
        return redirect('cart')
    from .cart import checkout_points_context
    if request.method == 'GET' and request.GET.get('points_preview') == '1':
        response = JsonResponse(checkout_points_context(request, context, request.GET.get('points', 0)))
        response['Cache-Control'] = 'no-store'
        return response
    context.update(checkout_points_context(request, context, request.POST.get('loyalty_points', 0) if request.method == 'POST' else 0))
    initial = {}
    if request.user.is_authenticated:
        from .models import CustomerContact
        contact = CustomerContact.objects.filter(customer=request.user).first()
        initial = {
            'customer_name': request.user.get_full_name(),
            'email': request.user.email,
            'phone': contact.phone if contact else '',
        }
    form = CheckoutForm(request.POST if request.method == 'POST' else None, initial=initial)
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            saved = request.session.get('cart', {})
            products = list(Product.objects.select_for_update().filter(pk__in=saved))
            if not saved or len(products) != len(saved) or any(
                not p.is_active or p.is_out_of_stock or not 1 <= saved[str(p.pk)] <= min(p.stock if p.stock else 99, 99)
                for p in products
            ):
                form.add_error(None, 'Dostupnost artikala je promijenjena. Vratite se u korpu i provjerite količine.')
            else:
                order = form.save(commit=False)
                order.customer = request.user if request.user.is_authenticated else None
                store = StoreSettings.objects.filter(pk=1).first()
                from .cart import priced_items
                priced = priced_items(products, saved)
                subtotal = sum(item['subtotal'] for item in priced)
                from .loyalty import applied_loyalty_percent, loyalty_amount
                order.loyalty_percent = applied_loyalty_percent(request)
                order.loyalty_discount = loyalty_amount(subtotal, order.loyalty_percent)
                net = subtotal - order.loyalty_discount
                from .modules import loyalty_enabled
                if request.user.is_authenticated and loyalty_enabled():
                    from .loyalty import redeem_points
                    order.save()
                    points_discount = redeem_points(order, request.user, net, request.POST.get('loyalty_points', 0))
                    order.loyalty_discount += points_discount
                    net -= points_discount
                order.delivery_price = store.delivery_cost(net) if store else 0
                order.total = net + order.delivery_price
                order.save()
                for item in priced:
                    product, quantity = item['product'], item['quantity']
                    for unit_price, count, promotion in item['promotion_groups']:
                        OrderItem.objects.create(order=order, product=product, quantity=count, unit_price=unit_price, promotion=promotion)
                    if product.stock:
                        product.stock -= quantity
                        product.is_out_of_stock = product.stock == 0
                        product.save(update_fields=['stock', 'is_out_of_stock'])
                request.session['cart'] = {}
                request.session.pop('loyalty_customer', None)
                request.session['last_order'] = order.pk
                return redirect(reverse('checkout') + '?success=1')
    from .seo import settings_for
    return render(request, 'shop/checkout.html', {
        **context, 'form': form, 'completed_order': completed,
        'google_review_url': settings_for(request).google_review_url if completed else '',
        'categories': Category.objects.all(),
        'site_settings': StoreSettings.objects.filter(pk=1).first(),
        'shop_name': 'BERA',
        'storefront_version': max((settings.BASE_DIR / 'shop/static' / name).stat().st_mtime_ns for name in ['css/home.css', 'css/bera-home.css', 'home.js']),
    })


from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from .forms import CustomerRegistrationForm, CustomerProfileForm, CustomerLoginForm, CustomerPasswordChangeForm


def account_context():
    from .loyalty import rules
    return {
        'loyalty_rules': rules(),
        'categories': Category.objects.all(),
        'site_settings': StoreSettings.objects.filter(pk=1).first(),
        'shop_name': 'BERA', 'account_page': True,
        'storefront_version': max((settings.BASE_DIR / 'shop/static' / name).stat().st_mtime_ns for name in ['css/home.css', 'css/bera-home.css', 'home.js']),
    }


from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET


@never_cache
@require_GET
def customer_csrf_token(request):
    from django.middleware.csrf import get_token
    return JsonResponse({'csrfToken': get_token(request)})


@never_cache
def customer_login(request):
    if request.user.is_authenticated:
        return redirect('account')
    form = CustomerLoginForm(request, data=request.POST if request.method == 'POST' else None)
    if request.method == 'POST' and form.is_valid():
        login(request, form.get_user())
        return redirect('account')
    return render(request, 'shop/account.html', {**account_context(), 'form': form, 'mode': 'login'})


def save_customer_form(form):
    from django.db import IntegrityError
    try:
        return form.save()
    except IntegrityError:
        form.add_error(None, 'Telefon ili e-mail je već registrovan. Provjerite unesene podatke.')
        return None


@never_cache
def customer_register(request):
    if request.user.is_authenticated:
        return redirect('account')
    form = CustomerRegistrationForm(request.POST if request.method == 'POST' else None)
    if request.method == 'POST' and form.is_valid():
        user = save_customer_form(form)
        if user is not None:
            login(request, user)
            return redirect('account')
    return render(request, 'shop/account.html', {**account_context(), 'form': form, 'mode': 'register'})


from django.views.decorators.cache import never_cache
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_http_methods


@never_cache
@sensitive_post_parameters('old_password', 'new_password1', 'new_password2')
@login_required(login_url='customer_login')
@require_http_methods(['GET', 'POST'])
def account(request):
    from django.contrib.auth import update_session_auth_hash
    from django.core.paginator import Paginator
    tab = request.GET.get('tab')
    section = 'security' if tab == 'sigurnost' else 'data' if tab == 'podaci' else 'orders'
    if request.method == 'POST':
        section = 'security' if request.POST.get('action') == 'password' else 'data'
    form = CustomerPasswordChangeForm(request.user, data=request.POST if request.method == 'POST' else None) if section == 'security' else CustomerProfileForm(request.POST if request.method == 'POST' else None, instance=request.user)
    if request.method == 'POST' and form.is_valid():
        user = save_customer_form(form)
        if user is not None:
            if section == 'security':
                update_session_auth_hash(request, user)
                messages.success(request, 'Lozinka je uspješno promijenjena.')
                return redirect(reverse('account') + '?tab=sigurnost')
            messages.success(request, 'Podaci su spremljeni.')
            return redirect(reverse('account') + '?tab=podaci')
    from .loyalty import loyalty_status
    from .models import LoyaltyCard
    from .modules import loyalty_enabled
    card = None
    if loyalty_enabled():
        card, _ = LoyaltyCard.objects.get_or_create(customer=request.user)
    spent, percent = loyalty_status(request.user)
    orders = Order.objects.filter(customer=request.user).order_by('-created_at', '-pk')
    stats = orders.aggregate(count=models.Count('pk'), active=models.Count('pk', filter=models.Q(status__in=['placed', 'shipped'])), completed=models.Count('pk', filter=models.Q(status='completed')))
    page = Paginator(orders.prefetch_related('items__product'), 10).get_page(request.GET.get('page'))
    return render(request, 'shop/account.html', {
        **account_context(), 'loyalty_card': card, 'loyalty_spent': spent, 'loyalty_rate': percent,
        'form': form, 'mode': 'profile', 'account_section': section, 'orders': page, 'orders_page': page,
        'account_stats': stats,
    })


@require_POST
def customer_logout(request):
    logout(request)
    return redirect('customer_login')


def wishlist_page(request):
    ids = [int(value) for value in request.GET.get('ids', '').split(',')[:500] if value.isascii() and value.isdigit() and len(value) < 19]
    products = Product.objects.filter(is_active=True, pk__in=ids).select_related('brand', 'category', 'plus_product')
    return render(request, 'shop/wishlist.html', {**account_context(), 'wishlist_products': products})


@login_required(login_url='customer_login')
@require_POST
def cart_loyalty(request):
    from .modules import loyalty_enabled
    if not loyalty_enabled():
        request.session.pop('loyalty_customer', None)
        return redirect('cart')
    if request.POST.get('action') == 'apply' and cart_context(request)['cart_count']:
        request.session['loyalty_customer'] = request.user.pk
    else:
        request.session.pop('loyalty_customer', None)
    return redirect('cart')


@panel_required
@require_POST
def panel_backup_restore(request):
    from .backups import restore_backup
    from django.contrib import messages
    from django.db import connections
    import logging
    try:
        connections.close_all()
        restore_backup(request.POST.get('backup', ''))
    except ValueError as exc:
        messages.error(request, str(exc))
    except Exception:
        logging.getLogger(__name__).exception('Vraćanje backupa nije uspjelo')
        messages.error(request, 'Vraćanje baze nije uspjelo. Pokušajte ponovo.')
    else:
        messages.success(request, 'Baza je vraćena na odabrani backup. Prethodno stanje je sačuvano kao sigurnosna kopija.')
    return redirect('panel_section', section='backup')


from bera_licensing.decorators import module_required


@module_required('loyalty')
def loyalty_page(request):
    from .models import LoyaltyCard, LoyaltyTier
    from .loyalty import rules, loyalty_status
    card = None
    if request.user.is_authenticated:
        card, _ = LoyaltyCard.objects.get_or_create(customer=request.user)
    spent, percent = loyalty_status(request.user)
    from django.db.models import Q
    tier = LoyaltyTier.objects.filter(minimum__lte=spent).filter(Q(maximum__isnull=True) | Q(maximum__gt=spent)).order_by('-minimum').first()
    return render(request, 'shop/loyalty.html', {**account_context(), 'card': card, 'entries': card.entries.select_related('order')[:100] if card else [], 'tiers': LoyaltyTier.objects.all(), 'tier': tier, 'rules': rules(), 'spent': spent, 'percent': percent})
