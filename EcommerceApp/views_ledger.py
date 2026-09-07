import csv
from decimal import Decimal
from uuid import uuid4

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import DecimalField, Exists, F, OuterRef, Q, Subquery, Sum, Value
from django.db.models.functions import Coalesce
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from .magacin import MagacinError, is_ignored_stock_location, order_location_rows
from .models import WarehouseCustomer, WarehousePartner, WarehouseLedgerEntry as Entry, WarehouseLedgerLine as Line, WarehouseLocation
from .warehouse_access import warehouse_user_required
from .warehouse_ledger import post_entry, create_replacement
from .ledger_orders import customer_orders
from .views_magacin import _magacin_context


class PartnerForm(forms.ModelForm):
    class Meta:
        model = WarehousePartner
        fields = ['naziv', 'grad', 'adresa', 'telefon', 'pdv_broj', 'kontakt']


def partners_with_balance():
    active_articles = Line.objects.filter(
        entry__partner_id=OuterRef('pk'), settled_by__isnull=True, voided_by__isnull=True,
    ).annotate(returned=Coalesce(Sum('returns__returned_qty'), 0)).filter(quantity__gt=F('returned'))
    return WarehousePartner.objects.annotate(
        balance=Coalesce(Sum('entries__amount'), Value(Decimal('0')), output_field=DecimalField(max_digits=14, decimal_places=2)),
        has_active_articles=Exists(active_articles),
        latest_entry_kind=Subquery(Entry.objects.filter(partner_id=OuterRef('pk')).order_by('-created_at', '-pk').values('kind')[:1]),
    ).order_by('naziv', 'pk')


def safe_csv(value):
    value = str(value or '')
    return "'" + value if value.lstrip().startswith(('=', '+', '-', '@')) else value


@login_required(login_url='login')
@user_passes_test(warehouse_user_required)
@require_http_methods(['GET', 'POST'])
def ledger(request):
    partner_id = request.POST.get('partner_id') if request.method == 'POST' else request.GET.get('partner')
    partner = get_object_or_404(partners_with_balance(), pk=partner_id) if str(partner_id or '').isdigit() else None
    if request.method == 'GET' and request.GET.get('orders_lookup') == '1':
        if not partner:
            return JsonResponse({'error': 'Odaberi kupca.'}, status=400)
        qs = customer_orders(partner)
        if request.GET.get('order_id'):
            if not request.GET['order_id'].isdigit():
                return JsonResponse({'error': 'Narudžba nije pronađena.'}, status=404)
            order = get_object_or_404(qs, pk=request.GET['order_id'])
            previous = dict(Line.objects.filter(voided_by__isnull=True, order_item__narudzba=order, entry__kind__in=[Entry.Kind.MISSING, Entry.Kind.DAMAGED])
                            .values('order_item_id').annotate(qty=Sum('quantity')).values_list('order_item_id', 'qty'))
            return JsonResponse({'items': [{'id': item.pk, 'name': item.puni_naziv, 'code': item.sifra,
                                           'quantity': item.kolicina, 'remaining': max(0, item.kolicina - previous.get(item.pk, 0)),
                                           'price': str(item.cijena)} for item in order.stavke.all()],
                                 'number': order.broj, 'cancelled': order.status == 'otkazana' or order.lager_status == 'otkazano'})
        query = request.GET.get('q', '').strip().lstrip('#')[:100]
        if query:
            qs = qs.filter(broj__icontains=query)
        return JsonResponse({'orders': [{'id': order.pk, 'number': order.broj, 'status': order.get_status_display(),
                                         'date': order.kreirana.strftime('%d.%m.%Y.'), 'amount': str(order.ukupno)} for order in qs[:50]]})
    form = PartnerForm()
    error = ''
    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'partner':
            form = PartnerForm(request.POST)
            if form.is_valid():
                with transaction.atomic():
                    values = form.cleaned_data
                    customer = WarehouseCustomer.objects.create(
                        ime_prezime=values['naziv'], telefon=values['telefon'],
                        adresa=values['adresa'], grad=values['grad'],
                    )
                    partner = customer.ledger_partner
                    partner.pdv_broj = values['pdv_broj']
                    partner.kontakt = values['kontakt']
                    partner.save(update_fields=['pdv_broj', 'kontakt'])
                messages.success(request, 'Partner je sačuvan.')
                return redirect(f"{reverse('staff_magacin_duguje')}?partner={partner.pk}")
            error = 'Provjeri podatke novog partnera.'
        elif partner:
            try:
                if action == 'replacement':
                    order = create_replacement(partner_id=partner.pk, line_id=request.POST.get('line_id'), user=request.user)
                    messages.success(request, f'Narudžba #{order.broj} bez otkupa je kreirana i roba rezervisana.')
                    return redirect(f"{reverse('staff_magacin_duguje')}?partner={partner.pk}")
                if action == 'notes':
                    partner.napomena = request.POST.get('notes', '')[:10000]
                    partner.save(update_fields=['napomena'])
                elif action in ('entry', 'return', 'order', 'missing', 'excess', 'damaged', 'delete_line'):
                    post_entry(partner_id=partner.pk, data=request.POST, user=request.user)
                else:
                    raise MagacinError('Nepoznata akcija.')
                messages.success(request, 'Promjene su sačuvane.')
                return redirect(f"{reverse('staff_magacin_duguje')}?partner={partner.pk}")
            except MagacinError as exc:
                error = str(exc)
        else:
            error = 'Prvo odaberi partnera.'
    query = request.GET.get('q', '').strip()[:200]
    partners = partners_with_balance()
    if query:
        partners = partners.filter(Q(naziv__icontains=query) | Q(grad__icontains=query) | Q(telefon__icontains=query) | Q(pdv_broj__icontains=query) | Q(customer__email__icontains=query))
    else:
        partners = partners.filter(latest_entry_kind__isnull=False)
    if request.GET.get('export') == '1':
        response = HttpResponse(content_type='text/csv; charset=utf-8')
        response['Content-Disposition'] = 'attachment; filename="duguje-potrazuje.csv"'
        response.write('\ufeff')
        writer = csv.writer(response, delimiter=';')
        if partner:
            writer.writerow(['Partner', 'Datum', 'Vrsta', 'Opis', 'Promjena salda (KM)', 'Korisnik', 'Narudžba'])
            for entry in partner.entries.select_related('user', 'order').iterator():
                writer.writerow([safe_csv(partner.naziv), entry.created_at.isoformat(), entry.get_kind_display(), safe_csv(entry.description), str(entry.amount), safe_csv(entry.user.get_username() if entry.user else ''), safe_csv(entry.order.broj if entry.order else '')])
        else:
            writer.writerow(['Partner', 'Grad', 'Saldo (KM): plus = duguje, minus = potražuje'])
            for row in partners.iterator():
                writer.writerow([safe_csv(row.naziv), safe_csv(row.grad), str(row.balance)])
        return response
    partner_page = Paginator(partners, 30).get_page(request.GET.get('page'))
    if partner is None and request.method == 'GET':
        partner = next(iter(partner_page.object_list), None)
    lines = Line.objects.none()
    entries = Entry.objects.none()
    if partner:
        partner.balance_abs = abs(partner.balance)
        partner.resolved_by_payment = partner.balance == 0 and partner.latest_entry_kind in (Entry.Kind.PAYMENT, Entry.Kind.RECEIPT)
        lines = Line.objects.filter(entry__partner=partner, settled_by__isnull=True, voided_by__isnull=True).select_related('entry__order', 'entry__source_order', 'replacement_order', 'invoice_item__narudzba', 'product', 'variation').prefetch_related('fulfillment_items__narudzba').annotate(returned=Coalesce(Sum('returns__returned_qty'), 0)).filter(quantity__gt=F('returned'))
        entries = partner.entries.select_related('user', 'order', 'source_line__entry__order')
    line_page = Paginator(lines.order_by('-pk'), 30).get_page(request.GET.get('items_page'))
    stock_cache = {}
    for line in line_page:
        line.fulfillment_order = next((item.narudzba for item in line.fulfillment_items.all() if item.narudzba.lager_status not in ('validirano', 'otkazano') and item.narudzba.status != 'otkazana'), None)
        line.can_send = False
        if line.entry.kind in (Entry.Kind.MISSING, Entry.Kind.DAMAGED) and line.product_id and not line.replacement_order_id:
            key = (line.product_id, line.variation_id)
            if key not in stock_cache:
                rows, _ = order_location_rows(line.product, line.variation)
                stock_cache[key] = sum(max(0, int(row.get("kolicina") or 0) - max(0, int(row.get("rezervisano") or 0))) for row in rows if row["location"].aktivan)
            line.can_send = stock_cache[key] >= line.quantity
        line.remaining = line.quantity - line.returned
    for item in partner_page:
        item.balance_abs = abs(item.balance)
        item.resolved_by_payment = item.balance == 0 and item.latest_entry_kind in (Entry.Kind.PAYMENT, Entry.Kind.RECEIPT)
    context = _magacin_context(request, section='duguje', page_title='Duguje / Potražuje', hide_top_search=True)
    context.update(partner=partner, partners=partner_page, lines=line_page,
                   orders=Paginator(customer_orders(partner), 20).get_page(request.GET.get('orders_page')),
                   displayed_total=sum((line.amount for line in line_page), Decimal("0")),
                   entries=Paginator(entries, 20).get_page(request.GET.get('history_page')),
                   partner_form=form, ledger_error=error, submitted=request.POST.dict() if error else {}, query=query,
                   token=request.POST.get('token') or str(uuid4()),
                   locations=[loc for loc in WarehouseLocation.objects.filter(aktivan=True).order_by('sifra') if not is_ignored_stock_location(loc)])
    return render(request, 'staff/magacin/duguje.html', context)
