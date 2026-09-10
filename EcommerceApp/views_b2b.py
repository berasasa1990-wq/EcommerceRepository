from decimal import Decimal, ROUND_HALF_UP

from django import forms
from django.contrib import messages
from django.http import HttpResponseBadRequest, JsonResponse
from django.urls import reverse
from urllib.parse import urlencode
from django.shortcuts import get_object_or_404
from django.contrib.auth.hashers import check_password, make_password
from django.core.cache import cache
from django.core.paginator import Paginator
from django.db.models import Prefetch, Q, Exists, OuterRef, F, Value, FloatField, Case, When
from django.db.models.functions import Coalesce, Cast
from django.shortcuts import redirect, render
from django.utils.crypto import constant_time_compare, salted_hmac
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods, require_POST

from .b2b_pricing import discounts_for, net_price, brand_divisors, base_net
from .magacin import ignored_location_q
from .models import B2BAccount, B2BSettings, Brand, Category, Product, WarehouseLocation, WarehouseStock


class B2BLoginForm(forms.Form):
    username = forms.CharField(label='Korisničko ime', max_length=150,
                               widget=forms.TextInput(attrs={'autocomplete': 'username'}))
    password = forms.CharField(label='Šifra', strip=False,
                              widget=forms.PasswordInput(attrs={'autocomplete': 'current-password'}))


def netto(mpc):
    return (mpc / Decimal('1.38') / Decimal('1.17')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


def current_account(request):
    account = B2BAccount.objects.filter(pk=request.session.get('b2b_account_id'), is_active=True).first()
    if account and constant_time_compare(request.session.get('b2b_hash', ''), account.session_hash()):
        return account
    return None


@never_cache
@require_http_methods(['GET', 'POST'])
def catalog(request):
    account = current_account(request)
    if not account:
        form = B2BLoginForm(request.POST or None)
        if request.method == 'POST' and form.is_valid():
            username = form.cleaned_data['username']
            key = 'b2b-login:' + salted_hmac('b2b.throttle', username).hexdigest()
            attempts = cache.get(key, 0)
            if attempts >= 10:
                form.add_error(None, 'Previše pokušaja. Pokušajte ponovo za 15 minuta.')
            else:
                candidate = B2BAccount.objects.filter(username=username, is_active=True).first()
                valid = check_password(form.cleaned_data['password'], candidate.password) if candidate else False
                if not candidate:
                    make_password(form.cleaned_data['password'])
                if valid:
                    cache.delete(key)
                    request.session.cycle_key()
                    request.session.pop('b2b_cart', None)
                    request.session['b2b_account_id'] = candidate.pk
                    request.session['b2b_hash'] = candidate.session_hash()
                    return redirect('b2b_catalog')
                cache.set(key, attempts + 1, 900)
                form.add_error(None, 'Pogrešno korisničko ime ili šifra.')
        response = render(request, 'b2b/login.html', {'form': form})
    else:
        b2b_settings = B2BSettings.objects.prefetch_related('banners').first()
        banners = []
        if b2b_settings:
            if b2b_settings.banner:
                banners.append({'image': b2b_settings.banner, 'alt': b2b_settings.banner_alt, 'link': b2b_settings.banner_link})
            banners.extend({'image': b.image, 'alt': b.alt, 'link': b.link} for b in b2b_settings.banners.all() if b.active and b.image)
        categories = list(Category.objects.filter(aktivan=True))
        children = {}
        ids = {c.pk for c in categories}
        for category in categories:
            parent = category.roditelj_id if category.roditelj_id in ids else None
            children.setdefault(parent, []).append(category)
        selected = next((c for c in categories if c.slug == request.GET.get('kategorija')), None)
        def build_navigation(parent):
            nodes = []
            for category in children.get(parent, []):
                descendants = build_navigation(category.pk)
                current = bool(selected and selected.pk == category.pk)
                nodes.append({'category': category, 'children': descendants, 'current': current,
                              'expanded': current or any(node['expanded'] for node in descendants)})
            return nodes
        navigation = build_navigation(None)
        products = Product.objects.filter(aktivan=True, sakriven_do_stanja=False).filter(
            Q(kategorija_id__in=ids) | Q(kategorija__isnull=True))
        collection = request.GET.get('ponuda', '')
        collection_labels = {'noviteti': 'NOVITETI', 'akcijska': 'AKCIJSKA PONUDA'}
        if collection not in collection_labels:
            collection = ''
        if collection:
            if b2b_settings:
                members = b2b_settings.noviteti if collection == 'noviteti' else b2b_settings.akcijska_ponuda
                products = products.filter(pk__in=members.values('pk'))
            else:
                products = products.none()
        if selected:
            selected_ids = {selected.pk}
            pending = [selected.pk]
            while pending:
                for child in children.get(pending.pop(), []):
                    if child.pk not in selected_ids:
                        selected_ids.add(child.pk)
                        pending.append(child.pk)
            products = products.filter(kategorija_id__in=selected_ids)
        query = request.GET.get('q', '').strip()[:200]
        if query:
            products = products.filter(Q(naziv__icontains=query) | Q(sifra__icontains=query)
                                       | Q(varijacije__naziv__icontains=query) | Q(varijacije__sifra__icontains=query) | Q(brend__naziv__icontains=query)).distinct()
        brands = Brand.objects.filter(artikli__in=products).distinct().order_by('naziv')
        brand_slug = request.GET.get('brend', '')
        if brand_slug:
            products = products.filter(brend__slug=brand_slug)
        in_stock = request.GET.get('stanje') == '1'
        sort = request.GET.get('sort', 'newest')
        ordering = {'newest': ('-pk', 'b2b_variant_id'), 'name': ('naziv', 'pk', 'b2b_variant_id'),
                    'price': ('b2b_price', 'pk', 'b2b_variant_id'),
                    'price_desc': ('-b2b_price', 'pk', 'b2b_variant_id')}
        if sort not in ordering:
            sort = 'newest'
        per_page = request.GET.get('limit', '50')
        if per_page not in ('25', '50', '100'):
            per_page = '50'
        locations = list(WarehouseLocation.objects.filter(aktivan=True).exclude(ignored_location_q()))
        # Paginate actual displayed SKU rows, so sold-out variations cannot precede
        # available articles on a later page. Availability always outranks user sorting.
        divisors = brand_divisors()
        price_divisor = Case(*[When(brend_id=pk, then=Value(float(value))) for pk, value in divisors.items()],
                             default=Value(1.38), output_field=FloatField())
        catalog_rows = products.annotate(b2b_variant_id=F('varijacije__pk'),
            b2b_price=Cast(Coalesce('varijacije__cijena', 'cijena'), FloatField()) / price_divisor / Value(1.17) *
            (Value(100.0) - Cast(Coalesce('b2b_offer_items__discount_percent', Decimal('0')), FloatField())) / Value(100.0))
        stocks = WarehouseStock.objects.filter(product_id=OuterRef('pk'),
            variation_key=Coalesce(OuterRef('b2b_variant_id'), Value(0)),
            location__in=locations, kolicina__gt=0).filter(kolicina__gt=F('rezervisano'))
        catalog_rows = catalog_rows.annotate(b2b_available=Exists(stocks))
        if in_stock:
            catalog_rows = catalog_rows.filter(b2b_available=True)
        catalog_rows = catalog_rows.order_by('-b2b_available', *ordering[sort]).values(
            'pk', 'b2b_variant_id', 'b2b_price', 'b2b_available')
        page = Paginator(catalog_rows, int(per_page)).get_page(request.GET.get('page'))
        page_rows = list(page.object_list)
        page_products = Product.objects.filter(pk__in=[r['pk'] for r in page_rows]).select_related(
            'kategorija', 'brend').prefetch_related('varijacije', Prefetch(
                'magacin_zalihe', queryset=WarehouseStock.objects.filter(location__in=locations)))
        product_map = {p.pk: p for p in page_products}
        discounts = discounts_for(product_map)
        rows = []
        for catalog_row in page_rows:
            product = product_map[catalog_row['pk']]
            variation = next((v for v in product.varijacije.all() if v.pk == catalog_row['b2b_variant_id']), None)
            stock = {(s.variation_id, s.location_id): s for s in product.magacin_zalihe.all()}
            mpc = variation.bazna_cijena if variation else product.cijena
            quantities = []
            for location in locations:
                entry = stock.get((variation.pk if variation else None, location.pk))
                quantities.append({'location': location, 'quantity': entry.kolicina if entry else 0,
                                   'available': entry.dostupno if entry else 0})
            rows.append({'product': product, 'variation': variation,
                         'sku': variation.sifra if variation else product.sifra,
                         'image': (variation.slika if variation and variation.slika else product.prikazna_slika),
                         'mpc': mpc, 'original_netto': base_net(mpc, divisors.get(product.brend_id, Decimal('1.38'))), 'netto': net_price(product, variation, discounts, divisors),
                         'discount_percent': discounts.get(product.pk, 0), 'quantities': quantities,
                         'available': sum(q['available'] for q in quantities)})
        params = request.GET.copy()
        params.pop('page', None)
        response = render(request, 'b2b/catalog.html', {'account': account, 'navigation': navigation, 'b2b_settings': b2b_settings, 'banners': banners,
            'collection': collection, 'collection_label': collection_labels.get(collection, ''),
            'brands': brands, 'brand_slug': brand_slug, 'sort': sort, 'per_page': per_page, 'in_stock': in_stock,
            'page_links': page.paginator.get_elided_page_range(page.number, on_each_side=2, on_ends=1),
            **cart_summary(request), 'selected': selected, 'q': query, 'page': page, 'rows': rows, 'params': params.urlencode()})
    response['X-Robots-Tag'] = 'noindex, nofollow'
    return response


@never_cache
@require_POST
def logout(request):
    request.session.pop('b2b_cart', None)
    request.session.pop('b2b_account_id', None)
    request.session.pop('b2b_hash', None)
    request.session.cycle_key()
    return redirect('b2b_catalog')


def available_stock(product, variation):
    stocks = WarehouseStock.objects.filter(
        product=product, variation=variation, location__aktivan=True,
    ).exclude(ignored_location_q('location'))
    return sum(stock.dostupno for stock in stocks)


def cart_product(product_id):
    return get_object_or_404(Product.objects.filter(aktivan=True, sakriven_do_stanja=False).filter(
        Q(kategorija__aktivan=True) | Q(kategorija__isnull=True)), pk=product_id)


@never_cache
@require_POST
def cart_change(request, product_id):
    ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'
    def error(message, status=400):
        return JsonResponse({'ok': False, 'message': message}, status=status) if ajax else HttpResponseBadRequest(message)
    if not current_account(request):
        return error('Sesija je istekla. Ponovo se prijavite u B2B.', 401) if ajax else redirect('b2b_catalog')
    product = cart_product(product_id)
    try:
        variation_id = int(request.POST.get('variation_id') or 0)
        quantity = int(request.POST.get('quantity', '1'))
    except (ValueError, TypeError):
        return error('Neispravna količina ili varijacija.')
    variation = get_object_or_404(product.varijacije, pk=variation_id) if variation_id else None
    if not variation and product.varijacije.exists():
        return error('Odaberite varijaciju.')
    action = request.POST.get('action', 'add')
    if action not in ('add', 'update', 'remove') or quantity < 0 or (action == 'add' and quantity == 0):
        return error('Neispravna količina ili radnja.')
    cart = request.session.get('b2b_cart', {})
    key = f'{product.pk}:{variation_id}'
    desired = cart.get(key, 0) + quantity if action == 'add' else quantity
    ok = True
    if action == 'remove' or desired == 0:
        cart.pop(key, None)
        message = 'Artikal je uklonjen iz korpe.'
    elif desired > available_stock(product, variation):
        ok = False
        message = 'Tražena količina nije dostupna.'
    else:
        cart[key] = desired
        message = 'Artikal je dodat u korpu.' if action == 'add' else 'Količina je ažurirana.'
    if ok:
        request.session.pop('b2b_checkout_token', None)
        request.session['b2b_cart'] = cart
    if ajax:
        return JsonResponse({'ok': ok, 'message': message, **cart_summary(request)}, status=200 if ok else 409)
    if ok:
        messages.success(request, message)
    else:
        messages.error(request, message)
    if action != 'add':
        return redirect('b2b_cart')
    params = urlencode({k: request.POST[k] for k in ('q', 'kategorija', 'page', 'brend', 'sort', 'limit', 'stanje', 'ponuda') if request.POST.get(k)})
    return redirect(reverse('b2b_catalog') + ('?' + params if params else ''))


@never_cache
@require_http_methods(['GET'])
def cart_view(request):
    account = current_account(request)
    if not account:
        return redirect('b2b_catalog')
    cart = request.session.get('b2b_cart', {})
    rows = []
    total = Decimal('0.00')
    stale = []
    products = Product.objects.filter(pk__in=[key.split(':')[0] for key in cart],
        aktivan=True, sakriven_do_stanja=False).filter(
        Q(kategorija__aktivan=True) | Q(kategorija__isnull=True)).prefetch_related('varijacije')
    product_map = {product.pk: product for product in products}
    discounts = discounts_for(product_map)
    divisors = brand_divisors()
    for key, quantity in cart.items():
        product_id, variation_id = map(int, key.split(':'))
        product = product_map.get(product_id)
        variations = {v.pk: v for v in product.varijacije.all()} if product else {}
        variation = variations.get(variation_id)
        if not product or (variation_id and not variation) or (not variation_id and variations):
            stale.append(key)
            continue
        price = net_price(product, variation, discounts, divisors)
        subtotal = price * quantity
        total += subtotal
        available = available_stock(product, variation)
        rows.append({'product': product, 'variation': variation,
            'image': variation.slika if variation and variation.slika else product.prikazna_slika,
            'quantity': quantity, 'available': available, 'netto': price, 'subtotal': subtotal,
            'insufficient': quantity > available})
    if stale:
        request.session['b2b_cart'] = {k: v for k, v in cart.items() if k not in stale}
        messages.info(request, 'Artikli koji više nisu u ponudi uklonjeni su iz korpe.')
    from .b2b_orders import gross
    gross_total = sum((gross(row['netto']) * row['quantity'] for row in rows), Decimal('0.00'))
    response = render(request, 'b2b/cart.html', {'account': account, 'rows': rows, 'total': total,
        'gross_total': gross_total, 'tax_total': gross_total - total, **cart_summary(request)})
    response['X-Robots-Tag'] = 'noindex, nofollow'
    return response



def cart_summary(request):
    cart = request.session.get('b2b_cart', {})
    count = 0
    total = Decimal('0.00')
    products = Product.objects.filter(pk__in=[k.split(':')[0] for k in cart], aktivan=True,
        sakriven_do_stanja=False).filter(Q(kategorija__aktivan=True) | Q(kategorija__isnull=True)).prefetch_related('varijacije')
    discounts = discounts_for(p.pk for p in products)
    divisors = brand_divisors()
    for product in products:
        variations = list(product.varijacije.all())
        for variation in variations or [None]:
            quantity = cart.get(f'{product.pk}:{variation.pk if variation else 0}', 0)
            count += quantity
            total += net_price(product, variation, discounts, divisors) * quantity
    return {'b2b_cart_count': count, 'b2b_cart_total': total}


class B2BCheckoutForm(forms.Form):
    payment = forms.ChoiceField(label='Način plaćanja', choices=[('', 'Izaberite način plaćanja'),
        ('ziralno', 'Žiralno'), ('gotovina', 'Gotovinski')])
    napomena = forms.CharField(label='Napomena', required=False, max_length=2000, widget=forms.Textarea(attrs={'rows': 3}))
    token = forms.UUIDField(widget=forms.HiddenInput)


@never_cache
@require_http_methods(['GET', 'POST'])
def checkout(request):
    import uuid
    from .models import B2BSubmission
    from .b2b_orders import submit_order
    from .magacin import MagacinError
    account = current_account(request)
    if not account:
        return redirect('b2b_catalog')
    token = request.session.get('b2b_checkout_token')
    if not token:
        token = str(uuid.uuid4())
        request.session['b2b_checkout_token'] = token
    form = B2BCheckoutForm(request.POST if request.method == 'POST' else None, initial={'token': token})
    if request.method == 'POST' and form.is_valid():
        existing = B2BSubmission.objects.filter(account=account, token=form.cleaned_data['token']).first()
        if existing:
            return redirect('b2b_order', pk=existing.pk)
        if str(form.cleaned_data['token']) != token:
            form.add_error(None, 'Korpa je izmijenjena. Vratite se u korpu i ponovo završite narudžbu.')
        else:
            try:
                submission = submit_order(account, request.session.get('b2b_cart', {}),
                    form.cleaned_data['token'], form.cleaned_data)
            except MagacinError as exc:
                form.add_error(None, str(exc))
            else:
                request.session.pop('b2b_cart', None)
                request.session.pop('b2b_checkout_token', None)
                return redirect('b2b_order', pk=submission.pk)
    summary = cart_summary(request)
    if not summary['b2b_cart_count'] and request.method == 'GET':
        return redirect('b2b_cart')
    # Warehouse order lines store gross prices, following the existing invoice convention.
    from .b2b_orders import gross
    cart = request.session.get('b2b_cart', {})
    total = Decimal('0.00')
    discounts = discounts_for(k.split(':')[0] for k in cart)
    divisors = brand_divisors()
    for product in Product.objects.filter(pk__in=[k.split(':')[0] for k in cart]).prefetch_related('varijacije'):
        for variation in list(product.varijacije.all()) or [None]:
            qty = cart.get(f'{product.pk}:{variation.pk if variation else 0}', 0)
            total += gross(net_price(product, variation, discounts, divisors)) * qty
    response = render(request, 'b2b/checkout.html', {'account': account, 'form': form,
        **summary, 'gross_total': total, 'tax_total': total - summary['b2b_cart_total']})
    response['X-Robots-Tag'] = 'noindex, nofollow'
    return response


@never_cache
@require_http_methods(['GET'])
def order_confirmation(request, pk):
    from .models import B2BSubmission
    account = current_account(request)
    if not account:
        return redirect('b2b_catalog')
    submission = get_object_or_404(B2BSubmission.objects.select_related('order'), pk=pk, account=account)
    response = render(request, 'b2b/order.html', {'account': account, 'submission': submission,
        **cart_summary(request)})
    response['X-Robots-Tag'] = 'noindex, nofollow'
    return response


class B2BAccessRequestForm(forms.Form):
    company = forms.CharField(label='Naziv firme', max_length=200,
        widget=forms.TextInput(attrs={'autocomplete': 'organization'}))
    address = forms.CharField(label='Adresa', max_length=300,
        widget=forms.TextInput(attrs={'autocomplete': 'street-address'}))
    city = forms.CharField(label='Grad', max_length=100,
        widget=forms.TextInput(attrs={'autocomplete': 'address-level2'}))
    jib = forms.RegexField(label='JIB', regex=r'^\d{13}$', max_length=13,
        error_messages={'invalid': 'JIB mora sadržavati 13 cifara.'},
        widget=forms.TextInput(attrs={'inputmode': 'numeric', 'pattern': '[0-9]{13}'}))
    phone = forms.CharField(label='Telefon', max_length=30,
        widget=forms.TextInput(attrs={'autocomplete': 'tel', 'type': 'tel'}))
    contact_name = forms.CharField(label='Kontakt ime', max_length=200,
        widget=forms.TextInput(attrs={'autocomplete': 'name'}))


@never_cache
@require_http_methods(['GET', 'POST'])
def request_access(request):
    from django.conf import settings
    from django.core.mail import send_mail
    import logging
    form = B2BAccessRequestForm(request.POST if request.method == 'POST' else None)
    sent = request.session.pop('b2b_access_request_sent', False) if request.method == 'GET' else False
    if request.method == 'POST' and form.is_valid():
        # Short-lived limits apply only to this public application form.
        ip_key = 'b2b-access-ip:' + salted_hmac('b2b-access-ip', request.META.get('REMOTE_ADDR', '')).hexdigest()
        signature = '\n'.join(form.cleaned_data.values())
        dedup_key = 'b2b-access-sent:' + salted_hmac('b2b-access-sent', signature).hexdigest()
        if cache.get(dedup_key) == 'sent':
            request.session['b2b_access_request_sent'] = True
            return redirect('b2b_request_access')
        cache.add(ip_key, 0, 900)
        if cache.incr(ip_key) > 5:
            form.add_error(None, 'Previše pokušaja. Pokušajte ponovo za 15 minuta.')
        elif not cache.add(dedup_key, 'sending', 120):
            form.add_error(None, 'Zahtjev se već šalje. Sačekajte trenutak.')
        else:
            data = form.cleaned_data
            body = '\n'.join([
                'Novi zahtjev za B2B pristup', '',
                f'Naziv firme: {data["company"]}',
                f'Adresa: {data["address"]}', f'Grad: {data["city"]}',
                f'JIB: {data["jib"]}', f'Telefon: {data["phone"]}',
                f'Kontakt ime: {data["contact_name"]}', '',
                'Zahtjev nije automatska registracija. B2B nalog kreira administrator nakon odobrenja.',
            ])
            try:
                delivered = send_mail('Novi zahtjev za B2B pristup', body,
                    settings.DEFAULT_FROM_EMAIL, ['narudzbe@opremazaribolov.ba'], fail_silently=False)
                if not delivered:
                    raise RuntimeError('Email backend did not accept the message')
            except Exception:
                cache.delete(dedup_key)
                logging.getLogger(__name__).error('B2B access request email delivery failed.')
                form.add_error(None, 'Zahtjev trenutno nije moguće poslati. Pokušajte ponovo; uneseni podaci su sačuvani u formi.')
            else:
                cache.set(dedup_key, 'sent', 900)
                request.session['b2b_access_request_sent'] = True
                return redirect('b2b_request_access')
    response = render(request, 'b2b/request_access.html', {'form': form, 'sent': sent})
    response['X-Robots-Tag'] = 'noindex, nofollow'
    return response
