"""Independent WMS records and pages; separate from legacy warehouse tools."""
from django.contrib.auth.decorators import login_required, user_passes_test
from django.forms import modelform_factory
from django.contrib import messages
from django.db import transaction
from django.urls import reverse
from urllib.parse import urlencode
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from django.shortcuts import get_object_or_404, redirect, render
from django.http import Http404, JsonResponse
from django.core.exceptions import PermissionDenied
from .panel_modules import module_locked
from .models import WMSLocation, WMSOrder, Product, ProductWMSStock, WMSSettings, WMSTransfer
from django.db.models import Q, Sum, Count
from django.views.decorators.http import require_POST
from .wms_legacy_import import import_magacin_locations, MagacinImportError
from django.core.paginator import Paginator
from .warehouse_access import warehouse_user_required
from .wms_customer_forms import WMSCustomerForm, customer_with_phone, lock_customer_writes

SECTIONS = {'zalihe': 'Zalihe', 'narudzbe': 'Narudžbe', 'pakovanje': 'Odvajanje robe', 'prenosnica': 'Prenosnica', 'lokacije': 'Lokacije', 'kupci': 'Kupci', 'podesavanje': 'Podešavanje'}


WMS_MODULES = {'zalihe': 'wms_zalihe', 'narudzbe': 'wms_narudzbe', 'pakovanje': 'wms_pakovanje', 'prenosnica': 'wms_prenosnice', 'lokacije': 'wms_zalihe', 'kupci': 'wms_narudzbe'}


def require_wms_section(section):
    if module_locked(WMS_MODULES.get(section)):
        raise PermissionDenied('Ova WMS sekcija je zaključana u Moduli / Dozvole.')


def context(section):
    return {'wms_section': section, 'wms_title': SECTIONS.get(section, 'WMS Magacin' if not section else 'Nova narudžba'), 'wms_sections': SECTIONS.items(), 'wms_pending_picking': WMSOrder.objects.filter(status__in=['nova', 'pakovanje']).count() if not module_locked('wms_pakovanje') else 0, 'wms_pending_transfers': WMSTransfer.objects.filter(completed_at__isnull=True).count() if not module_locked('wms_prenosnice') else 0, 'wms_locations_enabled': not module_locked('wms_zalihe'), 'wms_transfers_enabled': not module_locked('wms_prenosnice'), 'wms_locked_sections': [key for key, field in WMS_MODULES.items() if module_locked(field)]}


@login_required(login_url='login')
@user_passes_test(warehouse_user_required)
def workspace(request, section='zalihe'):
    if section not in SECTIONS:
        raise Http404
    locked = module_locked(WMS_MODULES.get(section))
    destination = next((key for key in WMS_MODULES if not module_locked(WMS_MODULES[key])), None) if locked else None
    empty_workspace = all(module_locked(field) for field in WMS_MODULES.values())
    target_url = reverse('staff_wms_section', args=[destination]) if destination else reverse('staff_wms')
    if request.method == 'GET' and request.headers.get('X-WMS-Permissions') == '1':
        response = JsonResponse({'redirect': target_url if locked and request.path != target_url else None})
        response['Cache-Control'] = 'no-store'
        return response
    if request.method in ('GET', 'HEAD') and empty_workspace:
        if request.path != reverse('staff_wms'):
            return redirect('staff_wms')
        return render(request, 'staff/wms/empty.html', context(''))
    if request.method in ('GET', 'HEAD') and locked:
        return redirect(target_url)
    require_wms_section(section)
    ctx = context(section)
    if section == 'kupci':
        import json
        from .models import WMSCustomer
        data = json.loads(order_customers(request).content)
        for row in data['customers']:
            if not row['id']:
                with transaction.atomic():
                    lock_customer_writes()
                    customer = customer_with_phone(row['phone'])
                    if customer is None:
                        customer = WMSCustomer.objects.create(ime_prezime=row['name'], telefon=row['phone'], adresa=row['address'], grad=row['city'], postanski_broj=row['postal'])
                row['id'] = None if customer.is_deleted else customer.pk
        data['customers'] = [row for row in data['customers'] if row['id'] is not None]
        return render(request, 'staff/wms/customers.html', {**ctx, **data, 'query': request.GET.get('q', '')})
    if section == 'podesavanje':
        fields = ['vat_rate']
        if not module_locked('wms_prenosnice') and not module_locked('wms_zalihe'):
            fields.append('transfer_location')
        if not module_locked('wms_zalihe'):
            fields.extend(['show_storefront_availability', 'picking_last_location'])
        if not fields:
            if request.method == 'POST':
                raise PermissionDenied
            return render(request, 'staff/wms/podesavanje.html', ctx)
        if request.method == 'POST' and any(field not in fields for field in request.POST if field in ('transfer_location', 'show_storefront_availability', 'picking_last_location', 'vat_rate')):
            raise PermissionDenied
        settings, _ = WMSSettings.objects.get_or_create(pk=1)
        Form = modelform_factory(WMSSettings, fields=fields)
        form = Form(request.POST if request.method == 'POST' else None, instance=settings)
        if request.method == 'POST' and form.is_valid():
            form.save()
            from .views import _invalidate_storefront_product_caches
            transaction.on_commit(_invalidate_storefront_product_caches)
            messages.success(request, 'WMS podešavanje je sačuvano.')
            return redirect('staff_wms_section', section='podesavanje')
        ctx['form'] = form
        if ctx['wms_locations_enabled']:
            from .models import WarehouseLocation, WarehouseStock
            ctx['magacin_import'] = {
                'locations': WarehouseLocation.objects.count(),
                'products': WarehouseStock.objects.values('product_id').distinct().count(),
                'quantity': WarehouseStock.objects.aggregate(total=Sum('kolicina'))['total'] or 0,
            }
        return render(request, 'staff/wms/podesavanje.html', ctx)
    if section == 'prenosnica':
        if request.method == 'POST':
            require_wms_section('zalihe')
            with transaction.atomic():
                transfer_id = request.POST.get('transfer_id')
                product_id = get_object_or_404(WMSTransfer, pk=transfer_id).product_id
                product = get_object_or_404(Product.objects.select_for_update(), pk=product_id)
                transfer = get_object_or_404(WMSTransfer.objects.select_for_update(), pk=transfer_id)
                if transfer.completed_at:
                    messages.info(request, 'Prenosnica je već prenijeta.')
                else:
                    stock = ProductWMSStock.objects.select_for_update().filter(product=product, lokacija_id=transfer.source_id).first()
                    if not stock and not product.wms_zalihe.exists() and product.wms_lokacija_id == transfer.source_id:
                        stock = ProductWMSStock.objects.create(product=product, lokacija_id=transfer.source_id, kolicina=product.stanje)
                    if not stock or stock.kolicina < transfer.quantity:
                        messages.error(request, 'Nema dovoljno količine na izvornoj lokaciji. Prenosnica nije prenijeta.')
                    else:
                        target, _ = ProductWMSStock.objects.get_or_create(product=product, lokacija_id=transfer.destination_id)
                        target.kolicina += transfer.quantity
                        target.save(update_fields=['kolicina'])
                        stock.kolicina -= transfer.quantity
                        if stock.kolicina:
                            stock.save(update_fields=['kolicina'])
                        else:
                            stock.delete()
                        remaining = list(product.wms_zalihe.all())
                        Product.objects.filter(pk=product.pk).update(wms_lokacija_id=remaining[0].lokacija_id if len(remaining) == 1 else None)
                        from django.utils import timezone
                        transfer.completed_at = timezone.now()
                        transfer.save(update_fields=['completed_at'])
                        messages.success(request, 'Prenosnica je prenijeta. Stanje po lokacijama je ažurirano.')
            return redirect('staff_wms_section', section='prenosnica')
        transfer_filter = request.GET.get('status', 'aktivne')
        if transfer_filter not in ('aktivne', 'prenijete'):
            transfer_filter = 'aktivne'
        ctx['transfer_filter'] = transfer_filter
        ctx['transfers'] = WMSTransfer.objects.select_related('product', 'source', 'destination').filter(completed_at__isnull=transfer_filter == 'aktivne')
        return render(request, 'staff/wms/prenosnica.html', ctx)
    if section == 'zalihe':
        query = (request.GET.get('q') or '').strip()
        if request.method == 'POST':
            require_wms_section('prenosnica')
            require_wms_section('lokacije')
            settings = WMSSettings.objects.filter(pk=1).select_related('transfer_location').first()
            destination = settings.transfer_location if settings else None
            try:
                quantity = int(request.POST.get('quantity', ''))
            except (ValueError, TypeError):
                quantity = 0
            with transaction.atomic():
                product = get_object_or_404(Product.objects.select_for_update(), pk=request.POST.get('product_id'))
                stock_id = request.POST.get('stock_id')
                stock = get_object_or_404(ProductWMSStock.objects.select_for_update(), pk=stock_id, product=product) if stock_id else None
                source_id = stock.lokacija_id if stock else product.wms_lokacija_id
                available = stock.kolicina if stock else product.stanje
                expected = request.POST.get('source_location', '')
                if not destination:
                    messages.error(request, 'Prvo izaberite lokaciju za prenos u WMS podešavanju.')
                elif not source_id or str(source_id) != expected or (not stock and product.wms_zalihe.exists()):
                    messages.error(request, 'Raspored je promijenjen. Ponovo otvorite pregled.')
                elif source_id == destination.pk:
                    messages.error(request, 'Artikal je već na odredišnoj lokaciji.')
                elif quantity < 1 or quantity > available:
                    messages.error(request, 'Unesite količinu od 1 do dostupne količine na ovoj lokaciji.')
                else:
                    WMSTransfer.objects.create(product=product, source_id=source_id, destination=destination, quantity=quantity)
                    messages.success(request, 'Prenosnica je kreirana. Roba se premješta tek potvrdom „Prenijeto“.')
            return redirect(reverse('staff_wms_section', args=['zalihe']) + '?' + urlencode({'q': query}))
        search_submitted = 'q' in request.GET
        products = Product.objects.none()
        if search_submitted:
            products = Product.objects.all()
            if query:
                products = products.filter(Q(naziv__icontains=query) | Q(sifra__icontains=query) | Q(barkod__icontains=query))
            products = products.select_related('wms_lokacija').prefetch_related('wms_zalihe__lokacija').order_by('naziv', 'pk')
        ctx.update({'transfer_settings': WMSSettings.objects.filter(pk=1).select_related('transfer_location').first(), 'locations': WMSLocation.objects.all(), 'query': query, 'search_submitted': search_submitted, 'products': Paginator(products, 30).get_page(request.GET.get('page'))})
        return render(request, 'staff/wms/stock_search.html', ctx)
    if section in ('narudzbe', 'pakovanje'):
        from .wms_orders import sync_panel_orders
        sync_panel_orders()
        ctx['wms_pending_picking'] = WMSOrder.objects.filter(status__in=['nova', 'pakovanje']).count() if not module_locked('wms_pakovanje') else 0
        if request.method == 'POST' and section == 'pakovanje':
            order = get_object_or_404(WMSOrder, pk=request.POST.get('order_id'))
            return redirect('staff_wms_pick_order', pk=order.pk)
        ctx['orders'] = WMSOrder.objects.all()
        if section == 'narudzbe':
            order_filter = request.GET.get('status', 'aktivna')
            if order_filter not in ('aktivna', 'zavrsene', 'pakovanje', 'otkazana'):
                order_filter = 'aktivna'
            ctx['order_filter'] = order_filter
            if order_filter == 'aktivna':
                ctx['orders'] = ctx['orders'].filter(status__in=['nova', 'pakovanje'])
            else:
                ctx['orders'] = ctx['orders'].filter(status={'zavrsene': 'zapakovana', 'pakovanje': 'pakovanje', 'otkazana': 'otkazana'}[order_filter])
            if order_filter == 'zavrsene':
                query = (request.GET.get('q') or '').strip()
                ctx['order_query'] = query
                if query:
                    match = Q(kupac__icontains=query) | Q(source_order__ime_prezime__icontains=query)
                    number = query.lstrip('#').strip()
                    if number.isascii() and number.isdigit() and len(number) <= 18:
                        match |= Q(pk=int(number))
                    ctx['orders'] = ctx['orders'].filter(match)
        if section == 'pakovanje':
            ctx['orders'] = ctx['orders'].exclude(status__in=['zapakovana', 'otkazana'])
    else:
        model, fields = WMSLocation, ['naziv']
        Form = modelform_factory(model, fields=fields)
        form = Form(request.POST or None)
        if request.method == 'POST' and form.is_valid():
            form.save()
            return redirect('staff_wms_section', section=section)
        ctx['form'] = form
        ctx['records'] = model.objects.all()
        if section == 'lokacije':
            query = (request.GET.get('q') or '').strip()
            ctx['location_query'] = query
            ctx['records'] = ctx['records'].annotate(article_count=Count('stanje_artikala__product', filter=Q(stanje_artikala__kolicina__gt=0), distinct=True)).order_by('naziv', 'pk')
            if query:
                ctx['records'] = ctx['records'].filter(Q(naziv__icontains=query) | Q(opis__icontains=query))
            ctx['shortages'] = pending_shortages().select_related('item__artikal', 'location', 'order')
    return render(request, 'staff/wms/workspace.html', ctx)


@login_required(login_url='login')
@user_passes_test(warehouse_user_required)
def create_order(request, tip):
    require_wms_section('narudzbe')
    if tip not in ('vp', 'online'):
        raise Http404
    Form = modelform_factory(WMSOrder, fields=['kupac', 'email', 'telefon', 'adresa', 'grad', 'postanski_broj', 'napomena'])
    form = Form(request.POST or None)
    return create_online_order(request, form, tip)



@login_required(login_url='login')
@user_passes_test(warehouse_user_required)
def order_detail(request, pk):
    require_wms_section('narudzbe')
    order = get_object_or_404(WMSOrder, pk=pk)
    source = order.source_order if order.source_order_id else None
    items = list(source.stavke.select_related('artikal') if source else order.items.select_related('artikal'))
    for item in items:
        item.line_total = item.cijena * item.kolicina
        regular = item.bazna_cijena
        if regular is None and not source and item.popust and item.popust < 100:
            regular = (item.cijena / (1 - item.popust / 100)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        if regular is None and item.artikal_id:
            regular = item.artikal.cijena
        item.regular_price = regular if regular is not None and regular > item.cijena else None
    subtotal = sum((item.line_total for item in items), Decimal('0'))
    discount = source.popust if source else Decimal('0')
    delivery = source.dostava if source else Decimal('0')
    if source:
        order.kupac = source.ime_prezime
        for field in ('email', 'telefon', 'adresa', 'grad', 'postanski_broj', 'napomena', 'kreirana'):
            setattr(order, field, getattr(source, field))
    total = source.ukupno if source else subtotal
    settings = WMSSettings.objects.filter(pk=1).first()
    vat_rate = settings.vat_rate if settings else Decimal('17.00')
    net_total = (total / (1 + vat_rate / 100)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    vat_total = total - net_total
    return render(request, 'staff/wms/order_detail.html', {**context('narudzbe'), 'order': order, 'items': items, 'order_total': total, 'net_total': net_total, 'vat_total': vat_total, 'vat_rate': vat_rate, 'subtotal': subtotal, 'discounted_subtotal': subtotal - discount, 'discount': discount, 'delivery': delivery, 'webshop_order': bool(source)})



@login_required(login_url='login')
@user_passes_test(warehouse_user_required)
def edit_order(request, pk):
    require_wms_section('narudzbe')
    order = get_object_or_404(WMSOrder, pk=pk)
    Form = modelform_factory(WMSOrder, fields=['kupac', 'email', 'telefon', 'adresa', 'grad', 'postanski_broj', 'napomena'])
    form = Form(request.POST if request.method == 'POST' else None, instance=order)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'Narudžba je izmijenjena.')
        return redirect('staff_wms_section', section='narudzbe')
    return render(request, 'staff/wms/order_form.html', {**context('narudzbe'), 'form': form, 'order': order, 'editing': True, 'order_type': order.get_tip_display()})


@login_required(login_url='login')
@user_passes_test(warehouse_user_required)
def pack_order(request, pk):
    require_wms_section('narudzbe')
    require_wms_section('pakovanje')
    if request.method != 'POST':
        from django.http import HttpResponseNotAllowed
        return HttpResponseNotAllowed(['POST'])
    WMSOrder.objects.filter(pk=get_object_or_404(WMSOrder, pk=pk).pk, status='nova').update(status='pakovanje')
    return redirect('staff_wms_section', section='pakovanje')


@login_required(login_url='login')
@user_passes_test(warehouse_user_required)
def cancel_order(request, pk):
    require_wms_section('narudzbe')
    if request.method != 'POST':
        from django.http import HttpResponseNotAllowed
        return HttpResponseNotAllowed(['POST'])
    order = get_object_or_404(WMSOrder, pk=pk)
    reason = (request.POST.get('reason') or '').strip()
    if not reason:
        messages.error(request, 'Unesite razlog otkazivanja.')
        return redirect('staff_wms_section', section='narudzbe')
    if len(reason) > 2000:
        messages.error(request, 'Razlog može imati najviše 2000 znakova.')
        return redirect('staff_wms_section', section='narudzbe')
    WMSOrder.objects.filter(pk=order.pk).exclude(status='otkazana').update(status='otkazana', cancellation_reason=reason)
    messages.success(request, 'Narudžba je otkazana.')
    return redirect('staff_wms_section', section='narudzbe')


@login_required(login_url='login')
@user_passes_test(warehouse_user_required)
def restore_order(request, pk):
    require_wms_section('narudzbe')
    if request.method != 'POST':
        from django.http import HttpResponseNotAllowed
        return HttpResponseNotAllowed(['POST'])
    order = get_object_or_404(WMSOrder, pk=pk)
    restored = WMSOrder.objects.filter(pk=order.pk, status='otkazana').update(status='nova')
    if restored:
        messages.success(request, 'Narudžba je vraćena među aktivne.')
    return redirect(reverse('staff_wms_section', args=['narudzbe']) + '?status=aktivna')


@login_required(login_url='login')
@user_passes_test(warehouse_user_required)
def pick_order(request, pk):
    require_wms_section('pakovanje')
    order = get_object_or_404(WMSOrder.objects.select_related('source_order'), pk=pk)
    if order.status not in ('nova', 'pakovanje'):
        return redirect('staff_wms_section', section='pakovanje')
    rows = []
    allocated = {}
    order_items = order.source_order.stavke.select_related('artikal').exclude(is_set_parent=True) if order.source_order_id else order.items.select_related('artikal')
    if order_items:
        for item in order_items:
            remaining = item.kolicina
            locations = []
            if item.artikal_id:
                stocks = ordered_picking_stocks(item.artikal.wms_zalihe.select_related('lokacija'))
                for stock in stocks:
                    available = max(0, stock.kolicina - allocated.get(stock.pk, 0))
                    take = min(remaining, available)
                    if take:
                        locations.append({'name': stock.lokacija.naziv, 'quantity': take, 'id': stock.lokacija_id})
                        allocated[stock.pk] = allocated.get(stock.pk, 0) + take
                        remaining -= take
                    if not remaining:
                        break
                if not stocks and item.artikal.wms_lokacija_id:
                    key = ('product', item.artikal_id)
                    take = min(remaining, max(0, item.artikal.stanje - allocated.get(key, 0)))
                    if take:
                        locations.append({'name': item.artikal.wms_lokacija.naziv, 'quantity': take, 'id': item.artikal.wms_lokacija_id})
                        allocated[key] = allocated.get(key, 0) + take
                        remaining -= take
            rows.append({'item': item, 'locations': locations, 'missing': remaining})
    from .models import WMSPickLine
    from django.utils import timezone
    from django import forms
    lines = order.pick_lines.select_related('item__artikal', 'location')
    if request.method == 'POST':
        action = request.POST.get('action', 'start')
        with transaction.atomic():
            locked = WMSOrder.objects.select_for_update().get(pk=pk)
            if locked.status not in ('nova', 'pakovanje'):
                return redirect('staff_wms_section', section='pakovanje')
            if action == 'start' and not locked.pick_lines.exists():
                if not rows or any(row['missing'] for row in rows):
                    messages.error(request, 'Dodijelite dovoljne količine na WMS lokacije prije odvajanja.')
                else:
                    for row in rows:
                        for location in row['locations']:
                            WMSPickLine.objects.create(order=locked, **({'item': row['item']} if order.source_order_id else {'wms_item': row['item']}), location_id=location['id'], quantity=location['quantity'])
                    locked.status = 'pakovanje'
                    locked.save(update_fields=['status'])
            elif action == 'alternative':
                origin = get_object_or_404(WMSPickLine, order=locked, pk=request.POST.get('line_id'), confirmed_at__isnull=False)
                offers = picking_alternatives(locked, origin)
                offer = next((o for o in offers if str(o['location'].pk) == request.POST.get('location_id')), None)
                if offer:
                    replacement = WMSPickLine.objects.create(order=locked, item=origin.item, wms_item=origin.wms_item, location=offer['location'], quantity=offer['quantity'])
                    return redirect(reverse('staff_wms_pick_order', args=[pk]) + '?line=' + str(replacement.pk))
                messages.error(request, 'Lokacija više nema slobodnu količinu za ovaj artikal.')
                return redirect('staff_wms_pick_order', pk=pk)
            elif action == 'confirm':
                line = get_object_or_404(WMSPickLine.objects.select_for_update(), order=locked, pk=request.POST.get('line_id'))
                class ConfirmationForm(forms.Form):
                    quantity = forms.IntegerField(min_value=0, max_value=line.quantity)
                    photo = forms.ImageField(required=False)
                    def clean_photo(self):
                        photo = self.cleaned_data['photo']
                        if photo and photo.size > 10 * 1024 * 1024:
                            raise forms.ValidationError('Fotografija može imati najviše 10 MB.')
                        return photo
                form = ConfirmationForm(request.POST, request.FILES)
                if line.confirmed_at:
                    next_pending = locked.pick_lines.filter(confirmed_at__isnull=True).first()
                    if next_pending:
                        return redirect(reverse('staff_wms_pick_order', args=[pk]) + '?line=' + str(next_pending.pk))
                    return redirect('staff_wms_pick_order', pk=pk)
                if form.is_valid():
                    line.confirmed_quantity = form.cleaned_data['quantity']
                    if form.cleaned_data['photo']:
                        line.photo = form.cleaned_data['photo']
                    line.confirmed_at = timezone.now()
                    line.save(update_fields=['confirmed_quantity', 'photo', 'confirmed_at'])
                    offers = picking_alternatives(locked, line)
                    if offers:
                        offer = offers[0]
                        replacement = WMSPickLine.objects.create(order=locked, item=line.item, wms_item=line.wms_item, location=offer['location'], quantity=offer['quantity'])
                        return redirect(reverse('staff_wms_pick_order', args=[pk]) + '?line=' + str(replacement.pk))
                    next_pending = locked.pick_lines.filter(confirmed_at__isnull=True).first()
                    if next_pending:
                        return redirect(reverse('staff_wms_pick_order', args=[pk]) + '?line=' + str(next_pending.pk))
                    return redirect('staff_wms_pick_order', pk=pk)
                messages.error(request, 'Unesite pokupljenu količinu od 0 do tražene količine.')
                return redirect(reverse('staff_wms_pick_order', args=[pk]) + '?line=' + str(line.pk))
            elif action == 'finish':
                if locked.pick_lines.exists() and not locked.pick_lines.filter(confirmed_at__isnull=True).exists():
                    # Validate every location before changing any stock.
                    totals = {}
                    for line in locked.pick_lines.select_related('item'):
                        product_id = line.picking_item.artikal_id
                        if not product_id or line.confirmed_quantity > line.quantity:
                            messages.error(request, 'Artikal ili potvrđena količina više nisu ispravni.')
                            return redirect('staff_wms_pick_order', pk=pk)
                        key = (product_id, line.location_id)
                        totals[key] = totals.get(key, 0) + line.confirmed_quantity
                    products = {product.pk: product for product in Product.objects.select_for_update().filter(
                        pk__in={key[0] for key in totals}).order_by('pk')}
                    stocks = {(stock.product_id, stock.lokacija_id): stock for stock in
                              ProductWMSStock.objects.select_for_update().filter(product_id__in=products).order_by('pk')}
                    product_totals = {}
                    for (product_id, location_id), quantity in totals.items():
                        product = products[product_id]
                        stock = stocks.get((product_id, location_id))
                        has_rows = any(key[0] == product_id for key in stocks)
                        available = stock.kolicina if stock else (
                            product.stanje if not has_rows and product.wms_lokacija_id == location_id else 0)
                        product_totals[product_id] = product_totals.get(product_id, 0) + quantity
                        if available < quantity:
                            messages.error(request, 'Nema dovoljno robe na lokaciji. Stanje nije promijenjeno.')
                            return redirect('staff_wms_pick_order', pk=pk)
                    # Location balances are authoritative when allocated stock exists.
                    starting_totals = {}
                    for product_id in product_totals:
                        allocated_stocks = [stock for key, stock in stocks.items() if key[0] == product_id]
                        starting_totals[product_id] = (sum(stock.kolicina for stock in allocated_stocks)
                                                       if allocated_stocks else products[product_id].stanje)
                    for key, quantity in totals.items():
                        stock = stocks.get(key)
                        if stock:
                            stock.kolicina -= quantity
                            stock.save(update_fields=['kolicina'])
                    for product_id, quantity in product_totals.items():
                        product = products[product_id]
                        product.stanje = starting_totals[product_id] - quantity
                        product.save(update_fields=['stanje', 'na_stanju', 'pracenje_zaliha', 'modul_sakriven'])
                    locked.status = 'zapakovana'
                    locked.completed_at = timezone.now()
                    locked.save(update_fields=['status', 'completed_at'])
                    messages.success(request, 'Odvajanje robe je završeno.')
                    return redirect(reverse('staff_wms_section', args=['narudzbe']) + '?status=zavrsene')
                messages.error(request, 'Svi artikli moraju biti odvojeni.')
        if action == 'start':
            first = order.pick_lines.filter(confirmed_at__isnull=True).first()
            if first:
                return redirect(reverse('staff_wms_pick_order', args=[pk]) + '?line=' + str(first.pk))
        return redirect('staff_wms_pick_order', pk=pk)
    lines = list(lines)
    origin = next((line for line in lines if str(line.pk) == request.GET.get('alternative') and line.confirmed_at), None)
    offers = picking_alternatives(order, origin) if origin else []
    current = next((line for line in lines if str(line.pk) == request.GET.get('line')), None)
    next_line = next((line for line in lines if not line.confirmed_at), None)
    pending_lines = [line for line in lines if not line.confirmed_at]
    cycle_next = None
    if current and current in pending_lines and len(pending_lines) > 1:
        cycle_next = pending_lines[(pending_lines.index(current) + 1) % len(pending_lines)]
    completed = sum(bool(line.confirmed_at) for line in lines)
    return render(request, 'staff/wms/picking.html', {
        **context('pakovanje'), 'order': order, 'rows': rows, 'lines': lines,
        'requested_quantity': sum(row['item'].kolicina for row in rows),
        'current': current, 'next_line': next_line, 'completed': completed, 'alternative_origin': origin, 'alternative_offers': offers,
        'can_finish': bool(lines) and completed == len(lines),
        'progress': round(completed * 100 / len(lines)) if lines else 0,
        'current_index': lines.index(current) + 1 if current else 0, 'cycle_next': cycle_next,
        'pending_lines': pending_lines, 'confirmed_lines': [line for line in lines if line.confirmed_at and line.confirmed_quantity > 0],
        'picked_lines': [line for line in lines if line.confirmed_at and line.confirmed_quantity > 0],
    })


def pending_shortages():
    from .models import WMSPickLine
    from django.db.models import F
    return WMSPickLine.objects.filter(order__status='zapakovana', confirmed_at__isnull=False,
        confirmed_quantity__lt=F('quantity'), shortage_resolved_at__isnull=True)


@login_required(login_url='login')
@user_passes_test(warehouse_user_required)
def resolve_shortage(request, pk):
    from django.http import HttpResponseNotAllowed
    from django.utils import timezone
    require_wms_section('lokacije')
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])
    decision = request.POST.get('decision')
    if decision not in ('clear', 'keep'):
        return JsonResponse({'error': 'Izaberite Očisti ili Ostavi.'}, status=400)
    from .models import WMSPickLine
    line = get_object_or_404(WMSPickLine.objects.select_related('item'), pk=pk)
    if not line.picking_item.artikal_id:
        messages.error(request, 'Artikal više nije dostupan.')
        return redirect('staff_wms_section', section='lokacije')
    with transaction.atomic():
        product = Product.objects.select_for_update().get(pk=line.picking_item.artikal_id)
        unresolved = pending_shortages().select_for_update().filter(Q(item__artikal=product) | Q(wms_item__artikal=product), location_id=line.location_id)
        if not unresolved.filter(pk=pk).exists():
            return redirect('staff_wms_section', section='lokacije')
        if decision == 'clear':
            stock = ProductWMSStock.objects.select_for_update().filter(product=product, lokacija_id=line.location_id).first()
            quantity = stock.kolicina if stock else (product.stanje if not product.wms_zalihe.exists() and product.wms_lokacija_id == line.location_id else 0)
            if quantity > product.stanje:
                messages.error(request, 'Stanje artikla i lokacije nije usklađeno. Količina nije promijenjena.')
                return redirect('staff_wms_section', section='lokacije')
            if stock:
                stock.kolicina = 0
                stock.save(update_fields=['kolicina'])
            product.stanje -= quantity
            product.save(update_fields=['stanje'])
        unresolved.update(shortage_resolution=decision, shortage_resolved_at=timezone.now())
    messages.success(request, 'Lokacija je očišćena.' if decision == 'clear' else 'Količina je ostavljena na lokaciji.')
    return redirect('staff_wms_section', section='lokacije')


@login_required(login_url='login')
@user_passes_test(warehouse_user_required)
def print_orders(request, kind):
    from decimal import Decimal
    require_wms_section('narudzbe')
    if kind not in ('racun', 'faktura', 'najava'):
        raise Http404
    orders = WMSOrder.objects.filter(status='zapakovana').select_related('source_order').prefetch_related('pick_lines__item', 'pick_lines__location')
    if kind == 'faktura':
        orders = orders.filter(tip='vp')
    from datetime import date
    from django.utils import timezone
    print_date = timezone.localdate()
    if kind in ('najava', 'racun', 'faktura'):
        try:
            print_date = date.fromisoformat(request.GET.get('date', '')) if request.GET.get('date') else print_date
        except ValueError:
            return JsonResponse({'error': 'Unesite ispravan datum.'}, status=400)
        if kind in ('najava', 'faktura'):
            orders = orders.filter(Q(completed_at__date=print_date) | Q(completed_at__isnull=True, kreirana__date=print_date))
        else:
            from .wms_orders import sync_panel_orders
            sync_panel_orders()
            orders = WMSOrder.objects.exclude(status='otkazana').exclude(source_order__status='otkazana').select_related('source_order').prefetch_related('pick_lines__item', 'pick_lines__location').filter(
                Q(source_order__kreirana__date=print_date) | Q(source_order__isnull=True, kreirana__date=print_date))
        if kind in ('racun', 'faktura'):
            if request.GET.get('stage') != 'print':
                return render(request, 'staff/wms/invoice_selection.html', {
                    **context('narudzbe'), 'orders': orders, 'print_date': print_date,
                    'print_kind': kind, 'selection_title': 'Količine za fakturu' if kind == 'faktura' else 'Štampanje računa'})
            selected = [int(value) for value in request.GET.getlist('orders') if value.isdigit() and len(value) < 18]
            orders = orders.filter(pk__in=selected)

    jobs = []
    for order in orders:
        quantities = {}
        locations = {}
        for line in order.pick_lines.all():
            quantities[line.picking_item.pk] = quantities.get(line.picking_item.pk, 0) + line.confirmed_quantity
            if line.confirmed_quantity:
                locations.setdefault(line.picking_item.pk, []).append({'name': line.location.naziv, 'quantity': line.confirmed_quantity})
        items = []
        subtotal = Decimal('0.00')
        order_items = order.source_order.stavke.all() if order.source_order_id else order.items.all()
        if order_items:
            for item in order_items:
                quantity = quantities.get(item.pk, 0) if order.status == 'zapakovana' else item.kolicina
                if quantity:
                    total = item.cijena * quantity
                    subtotal += total
                    items.append({'name': item.naziv, 'code': item.sifra, 'quantity': quantity, 'price': item.cijena, 'total': total, 'locations': locations.get(item.pk, [])})
        source = order.source_order
        discount = min(subtotal, (source.popust * subtotal / source.medjuzbir).quantize(Decimal('0.01'))) if source and source.medjuzbir > 0 else Decimal('0.00')
        delivery = source.dostava if source else Decimal('0.00')
        jobs.append({'order': order, 'items': items, 'subtotal': subtotal, 'discount': discount,
                     'delivery': delivery, 'total': subtotal - discount + delivery})
    if kind == 'najava':
        return render(request, 'staff/wms/announcement_print.html', {'jobs': jobs, 'print_date': print_date, 'order_count': len(jobs)})
    return render(request, 'staff/wms/orders_print.html', {'jobs': jobs, 'kind': kind,
        'title': {'racun': 'Štampanje računa', 'faktura': 'Količine za fakturu', 'najava': 'Najava slanja'}[kind]})


def create_online_order(request, form, tip='online'):
    import json
    from .models import WMSOrderItem
    lines = []
    if request.method == 'POST':
        try:
            raw = json.loads(request.POST.get('items', '[]'))
            if not isinstance(raw, list) or not raw or len(raw) > 500:
                raise ValueError
            quantities = {}
            discounts = {}
            for row in raw:
                product_id, quantity = int(row['id']), int(row['quantity'])
                if quantity < 1 or quantity > 100000:
                    raise ValueError
                discount = Decimal(str(row.get('discount', 0)))
                if not discount.is_finite() or discount < 0 or discount > 100:
                    raise ValueError
                discount = discount.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
                if product_id in discounts and discounts[product_id] != discount:
                    raise ValueError
                discounts[product_id] = discount
                quantities[product_id] = quantities.get(product_id, 0) + quantity
            products = {product.pk: product for product in Product.objects.filter(pk__in=quantities)}
            if len(products) != len(quantities):
                raise ValueError
            lines = [{'id': pk, 'name': products[pk].naziv, 'code': products[pk].sifra, 'price': str(wms_entry_price(products[pk], tip)), 'quantity': qty, 'discount': str(discounts[pk])} for pk, qty in quantities.items()]
            if form.is_valid():
                with transaction.atomic():
                    order = form.save(commit=False)
                    order.tip = tip
                    from .models import WMSCustomer
                    customer_id = request.POST.get('customer_id', '')
                    if customer_id.isdigit():
                        order.customer = WMSCustomer.objects.filter(pk=customer_id).first()
                    order.save()
                    for pk, qty in quantities.items():
                        product = products[pk]
                        WMSOrderItem.objects.create(order=order, artikal=product, naziv=product.naziv,
                            sifra=product.sifra or '', kolicina=qty, popust=discounts[pk], bazna_cijena=wms_entry_price(product, tip, regular=True),
                            cijena=(wms_entry_price(product, tip) * (Decimal('1') - discounts[pk] / 100)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP))
                return redirect('staff_wms_section', section='narudzbe')
        except (ValueError, TypeError, KeyError, InvalidOperation):
            messages.error(request, 'Dodajte artikle i unesite ispravne količine.')
    return render(request, 'staff/wms/online_order_form.html', {**context('narudzbe'), 'form': form, 'initial_items': lines, 'order_type': tip})


@login_required(login_url='login')
@user_passes_test(warehouse_user_required)
def order_products(request):
    require_wms_section('narudzbe')
    query = (request.GET.get('q') or '').strip()
    products = Product.objects.filter(aktivan=True)
    if query:
        products = products.filter(Q(naziv__icontains=query) | Q(sifra__icontains=query))
    page = Paginator(products.order_by('naziv', 'pk'), 24).get_page(request.GET.get('page'))
    return JsonResponse({'pages': page.paginator.num_pages, 'page': page.number, 'results': [
        {'id': product.pk, 'name': product.naziv, 'code': product.sifra, 'price': str(wms_entry_price(product, request.GET.get('tip', 'online'))),
         'image': product.prikazna_slika.url if product.prikazna_slika else ''} for product in page]})


@login_required(login_url='login')
@user_passes_test(warehouse_user_required)
@transaction.atomic
def order_customers(request):
    from .models import WMSCustomer, Order
    from django.forms import modelform_factory
    require_wms_section('narudzbe')
    if request.method == 'POST':
        lock_customer_writes()
        form = WMSCustomerForm(request.POST)
        if not form.is_valid():
            return JsonResponse({'errors': form.errors.get_json_data()}, status=400)
        customer = form.save()
        return JsonResponse({'customer': {'id': customer.pk, 'name': customer.ime_prezime,
            'phone': customer.telefon, 'address': customer.adresa, 'city': customer.grad, 'postal': customer.postanski_broj}})
    if request.method != 'GET':
        return JsonResponse({'error': 'Nedozvoljen zahtjev.'}, status=405)
    query = (request.GET.get('q') or '').strip()
    customers = WMSCustomer.objects.filter(is_deleted=False)
    history = Order.objects.all()
    manual = WMSOrder.objects.filter(source_order__isnull=True, customer__isnull=True)
    if query:
        customers = customers.filter(Q(ime_prezime__icontains=query) | Q(telefon__icontains=query) | Q(grad__icontains=query))
        history = history.filter(Q(ime_prezime__icontains=query) | Q(telefon__icontains=query) | Q(grad__icontains=query))
        manual = manual.filter(Q(kupac__icontains=query) | Q(telefon__icontains=query) | Q(grad__icontains=query))
    rows = [{'id': c.pk, 'name': c.ime_prezime, 'phone': c.telefon, 'address': c.adresa, 'city': c.grad, 'postal': c.postanski_broj} for c in customers]
    rows += [{'id': '', 'name': o.ime_prezime, 'phone': o.telefon, 'address': o.adresa, 'city': o.grad, 'postal': o.postanski_broj, 'email': o.email} for o in history.order_by('-kreirana')]
    rows += [{'id': '', 'name': o.kupac, 'phone': o.telefon, 'address': o.adresa, 'city': o.grad, 'postal': o.postanski_broj, 'email': o.email} for o in manual.order_by('-kreirana')]
    deleted = {(c.ime_prezime.strip().lower(), c.telefon.strip()) for c in WMSCustomer.objects.filter(is_deleted=True)}
    unique = {}
    for row in rows:
        if (row['name'].strip().lower(), row['phone'].strip()) in deleted:
            continue
        unique.setdefault((row['name'].strip().lower(), row['phone'].strip()), row)
    rows = sorted(unique.values(), key=lambda row: row['name'].lower())
    page = Paginator(rows, 30).get_page(request.GET.get('page'))
    return JsonResponse({'customers': list(page), 'page': page.number, 'pages': page.paginator.num_pages})


@login_required(login_url='login')
@user_passes_test(warehouse_user_required)
@transaction.atomic
def manage_customer(request, pk, action):
    from .models import WMSCustomer
    require_wms_section('kupci')
    customer = get_object_or_404(WMSCustomer, pk=pk, is_deleted=False)
    if action not in ('izmjena', 'obrisi'):
        raise Http404
    if request.method == 'POST':
        lock_customer_writes()
    form = WMSCustomerForm(request.POST or None, instance=customer)
    if request.method == 'POST':
        if action == 'obrisi':
            customer.is_deleted = True
            customer.save(update_fields=['is_deleted'])
            return redirect('staff_wms_section', section='kupci')
        if form.is_valid():
            form.save()
            return redirect('staff_wms_section', section='kupci')
    return render(request, 'staff/wms/customer_form.html', {**context('kupci'), 'form': form, 'customer': customer, 'delete_customer': action == 'obrisi'})


def picking_alternatives(order, origin):
    item = origin.picking_item
    if not item.artikal_id or origin.confirmed_quantity >= origin.quantity:
        return []
    lines = list(order.pick_lines.select_related('item', 'wms_item'))
    same_item = [line for line in lines if line.item_id == origin.item_id and line.wms_item_id == origin.wms_item_id]
    committed = sum(line.confirmed_quantity if line.confirmed_at else line.quantity for line in same_item)
    missing = max(0, item.kolicina - committed)
    visited = {line.location_id for line in same_item}
    offers = []
    for stock in ordered_picking_stocks(ProductWMSStock.objects.filter(product_id=item.artikal_id, kolicina__gt=0).exclude(lokacija_id__in=visited).select_related('lokacija')):
        reserved = sum(line.confirmed_quantity if line.confirmed_at else line.quantity for line in lines if line.location_id == stock.lokacija_id and line.picking_item.artikal_id == item.artikal_id)
        take = min(missing, max(0, stock.kolicina - reserved))
        if take:
            offers.append({'location': stock.lokacija, 'quantity': take})
    return offers


def ordered_picking_stocks(stocks):
    settings = WMSSettings.objects.filter(pk=1).first()
    last_id = settings.picking_last_location_id if settings else None
    def sort_key(stock):
        name = stock.lokacija.naziv.strip().casefold()
        last = stock.lokacija_id == last_id if last_id else ('maloprodaja' in name or name == 'mp')
        return (last, name, stock.pk)
    return sorted(stocks, key=sort_key)


def wms_entry_price(product, tip, regular=False):
    price = product.cijena if regular else (product.akcijska_cijena or product.cijena)
    if tip == 'vp':
        price = price / Decimal('1.38')
    return price.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


@login_required(login_url='login')
@user_passes_test(warehouse_user_required)
def location_detail(request, pk):
    require_wms_section('lokacije')
    location = get_object_or_404(WMSLocation, pk=pk)
    if request.method == 'POST':
        require_wms_section('prenosnica')
        try:
            quantity = int(request.POST.get('quantity', ''))
            destination_id = int(request.POST.get('destination', ''))
            stock_id = int(request.POST.get('stock_id', ''))
        except (ValueError, TypeError):
            messages.error(request, 'Unesite količinu i odredišnu lokaciju.')
            return redirect('staff_wms_location', pk=pk)
        destination = get_object_or_404(WMSLocation, pk=destination_id)
        with transaction.atomic():
            stock = get_object_or_404(ProductWMSStock.objects.select_for_update(), pk=stock_id, lokacija=location)
            if quantity < 1 or quantity > stock.kolicina or destination.pk == location.pk:
                messages.error(request, 'Provjerite količinu i izaberite drugu lokaciju.')
            else:
                WMSTransfer.objects.create(product=stock.product, source=location, destination=destination, quantity=quantity)
                messages.success(request, 'Prenosnica je kreirana. Potvrdite Prenijeto da prebacite robu.')
                return redirect('staff_wms_section', section='prenosnica')
        return redirect('staff_wms_location', pk=pk)
    stocks = ProductWMSStock.objects.filter(lokacija=location, kolicina__gt=0).select_related('product').order_by('product__naziv')
    return render(request, 'staff/wms/location_detail.html', {**context('lokacije'), 'location': location, 'stocks': stocks, 'destinations': WMSLocation.objects.exclude(pk=pk).order_by('naziv')})


@login_required(login_url='login')
@user_passes_test(warehouse_user_required)
def location_barcode_print(request, pk):
    require_wms_section('lokacije')
    from barcode import Code128
    from barcode.writer import SVGWriter
    import base64
    location = get_object_or_404(WMSLocation, pk=pk)
    svg = Code128(location.barkod, writer=SVGWriter()).render({'module_width': 0.35, 'module_height': 15, 'quiet_zone': 3, 'font_size': 10})
    image = 'data:image/svg+xml;base64,' + base64.b64encode(svg).decode('ascii')
    return render(request, 'staff/wms/location_barcode_print.html', {'location': location, 'barcode_image': image})


@login_required(login_url='login')
@user_passes_test(warehouse_user_required)
def excel_import(request):
    require_wms_section('zalihe')
    from .wms_excel import template_bytes, import_products
    from django.http import HttpResponse
    if request.method == 'GET' and request.GET.get('template') == '1':
        response = HttpResponse(template_bytes(), content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        response['Content-Disposition'] = 'attachment; filename="WMS-import-artikala.xlsx"'
        return response
    if request.method == 'POST':
        upload = request.FILES.get('excel_file')
        if not upload or not upload.name.lower().endswith('.xlsx') or upload.size > 10 * 1024 * 1024:
            messages.error(request, 'Izaberite .xlsx datoteku do 10 MB.')
        else:
            try:
                count = import_products(upload)
                messages.success(request, f'Import uspješan: {count} artikala.')
            except ValueError as exc:
                messages.error(request, str(exc))
            except Exception:
                import logging
                logging.getLogger(__name__).exception('WMS Excel import nije uspio')
                messages.error(request, 'Import nije uspio. Provjerite Excel datoteku; podaci nisu upisani.')
    return redirect('staff_wms_section', section='podesavanje')


@login_required(login_url='login')
@user_passes_test(warehouse_user_required)
@require_POST
def magacin_import(request):
    require_wms_section('zalihe')
    try:
        result = import_magacin_locations()
    except MagacinImportError as exc:
        messages.error(request, str(exc))
    else:
        from .views import _invalidate_storefront_product_caches
        transaction.on_commit(_invalidate_storefront_product_caches)
        messages.success(request, (
            f"Prenos iz magacina je završen: {result['products']} artikala, "
            f"{result['legacy_locations']} lokacija. Dodano zapisa zaliha: "
            f"{result['created_stock_rows']}; dodijeljeno glavnih lokacija: "
            f"{result['assigned_primary_locations']}."
        ))
    return redirect('staff_wms_section', section='podesavanje')
