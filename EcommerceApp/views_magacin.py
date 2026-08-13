"""Staff Magacin — lager artikala po lokacijama."""

import json
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, F, Q, Sum
from django.db.models.functions import TruncDate, TruncMonth, TruncYear
from django.http import HttpResponseRedirect, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from .magacin import (
    MAGACIN_SYNC_SESSION_KEY,
    MagacinError,
    apply_movement,
    reserve_for_order,
    is_ignored_stock_location,
    last_sync,
    location_rows,
    countable_stock_qs,
    magacin_in_stock_q,
    magacin_products_qs,
    usable_locations,
    run_sync_chunk,
    search_products,
    seed_default_locations,
    start_full_sync,
    stock_totals,
)
from .models import (
    BARKOD_MAX_LENGTH,
    SIFRA_MAX_LENGTH,
    Brand,
    Category,
    Order,
    OrderItem,
    Product,
    ProductImage,
    ProductVariation,
    ProductWarehouseMeta,
    SiteSettings,
    StaffSiteEvent,
    Tag,
    WarehouseLocation,
    WarehouseMovement,
    WarehouseStock,
    WarehouseSupplier,
    WarehouseSyncLog,
)
from .odoo_client import odoo_je_konfigurisan
from .views import _base_context, _superuser_required


def _user_display(user):
    first = (getattr(user, 'first_name', '') or '').strip()
    last = (getattr(user, 'last_name', '') or '').strip()
    if first and last:
        return f'{first} {last[0]}.'
    if first:
        return first
    if last:
        return last
    email = (getattr(user, 'email', '') or '').strip()
    if email:
        return email.split('@')[0]
    return user.get_username()


def _ensure_magacin_locations():
    if WarehouseLocation.objects.exists():
        return
    if not odoo_je_konfigurisan():
        seed_default_locations()


def _sync_job_view(job):
    if not job:
        return None
    template_ids = job.get('template_ids') or []
    stock_ids = job.get('stock_ids') or []
    phase = job.get('phase') or 'catalog'
    if phase == 'catalog':
        total = max(1, len(template_ids))
        current = int(job.get('position') or 0)
        label = f'Katalog {current} / {len(template_ids)} — cijene, šifre, barkodovi, slike'
    elif phase == 'locations':
        total = 1
        current = 1
        label = 'Lokacije iz Odoo'
    elif phase == 'stock':
        total = max(1, len(stock_ids))
        current = int(job.get('stock_position') or 0)
        label = f'Zalihe {current} / {len(stock_ids)}'
    else:
        total = 1
        current = 1
        label = 'Završavam…'
    percent = int((current / total) * 100) if total else 100
    return {
        'phase': phase,
        'label': label,
        'percent': min(100, percent),
        'artikala': job.get('artikala') or 0,
    }


def _magacin_search_query(request):
    """Magacin pretraga je odvojena od sajt pretrage (`q` / search_query)."""
    return (
        request.GET.get('pretraga')
        or request.POST.get('pretraga')
        or request.GET.get('q')
        or request.POST.get('q')
        or ''
    ).strip()


def _magacin_context(request, *, section='artikli', page_title='Magacin'):
    sync = last_sync()
    notify_count = StaffSiteEvent.objects.filter(
        kreirano__gte=timezone.now() - timedelta(hours=24),
    ).count()
    site_settings = SiteSettings.load()
    return {
        **_base_context(),
        'site_settings': site_settings,
        'magacin_section': section,
        'page_title': page_title,
        'last_sync': sync,
        'odoo_configured': odoo_je_konfigurisan(),
        'notify_count': notify_count,
        'staff_display_name': _user_display(request.user),
        'staff_role': 'Admin' if request.user.is_superuser else 'Staff',
        'search_query': '',
        'magacin_search': _magacin_search_query(request),
        'include_zero': (request.GET.get('bez_zalihe') or '') == '1',
        'sync_job': _sync_job_view(request.session.get(MAGACIN_SYNC_SESSION_KEY)),
        'new_magacin_orders_count': Order.objects.filter(status=Order.Status.NOVA).count(),
    }


def _parse_qty(raw):
    text = str(raw or '').strip().replace(',', '.')
    if not text:
        raise MagacinError('Unesi količinu.')
    try:
        return int(Decimal(text))
    except (InvalidOperation, ValueError):
        raise MagacinError('Količina nije validan broj.')


def _parse_money(raw):
    text = str(raw or '').strip().replace(',', '.')
    if not text:
        return None
    try:
        return Decimal(text)
    except (InvalidOperation, ValueError):
        raise MagacinError('Cijena nije validan broj.')


@login_required(login_url='login')
@user_passes_test(_superuser_required)
def magacin_home(request):
    return redirect('staff_magacin_artikli')


@login_required(login_url='login')
@user_passes_test(_superuser_required)
def magacin_artikli(request):
    _ensure_magacin_locations()
    query = _magacin_search_query(request)
    include_zero = (request.GET.get('bez_zalihe') or '') == '1'
    searched = bool(query)
    recent_movements = (
        WarehouseMovement.objects.filter(
            tip__in=[
                WarehouseMovement.Tip.PRIJEM,
                WarehouseMovement.Tip.PRODAJA,
                WarehouseMovement.Tip.TRANSFER,
                WarehouseMovement.Tip.KOREKCIJA,
            ],
        )
        .select_related('product', 'variation', 'location', 'to_location', 'korisnik')
        .order_by('-kreiran', '-id')[:10]
    )
    context = _magacin_context(request, section='artikli', page_title='Artikli — Magacin')
    context.update({
        'searched': searched,
        'recent_movements': recent_movements,
        'page': None,
        'result_count': 0,
    })
    if not searched:
        return render(request, 'staff/magacin/artikli.html', context)

    qs = magacin_products_qs().select_related('kategorija', 'brend').order_by('naziv')
    if not include_zero:
        qs = qs.filter(magacin_in_stock_q()).distinct()
    products, exact = search_products(query, limit=200, include_zero=include_zero)
    if exact:
        url = reverse('staff_magacin_artikal', args=[exact.pk])
        params = {'pretraga': query}
        if include_zero:
            params['bez_zalihe'] = '1'
        return redirect(f'{url}?{urlencode(params)}')
    qs = qs.filter(pk__in=[p.pk for p in products])

    paginator = Paginator(qs, 40)
    page = paginator.get_page(request.GET.get('page') or 1)
    product_ids = [item.pk for item in page.object_list]
    totals_by_product = {}
    if product_ids:
        for row in (
            countable_stock_qs(WarehouseStock.objects.filter(product_id__in=product_ids))
            .values('product_id')
            .annotate(na_stanju=Sum('kolicina'), rezervisano=Sum('rezervisano'))
        ):
            na_stanju = int(row['na_stanju'] or 0)
            rezervisano = max(0, int(row['rezervisano'] or 0))
            totals_by_product[row['product_id']] = {
                'na_stanju': na_stanju,
                'rezervisano': rezervisano,
                'dostupno': max(0, na_stanju - rezervisano),
            }

    rows = []
    for product in page.object_list:
        totals = totals_by_product.get(product.pk) or {
            'na_stanju': 0,
            'rezervisano': 0,
            'dostupno': 0,
        }
        rows.append({'product': product, **totals})
    page.object_list = rows
    context.update({
        'page': page,
        'result_count': paginator.count,
    })
    return render(request, 'staff/magacin/artikli.html', context)


@login_required(login_url='login')
@user_passes_test(_superuser_required)
def magacin_artikal(request, pk):
    _ensure_magacin_locations()
    product = get_object_or_404(
        magacin_products_qs().select_related('kategorija', 'brend', 'magacin_meta__dobavljac'),
        pk=pk,
    )
    variations = list(product.varijacije.all())
    variation = None
    variation_id = request.GET.get('varijacija') or request.POST.get('variation_id')
    if variation_id:
        variation = next((v for v in variations if str(v.pk) == str(variation_id)), None)
        if variation is None:
            messages.error(request, 'Varijacija nije pronađena.')
            return redirect('staff_magacin_artikal', pk=product.pk)

    if request.method == 'POST':
        action = (request.POST.get('action') or '').strip()
        try:
            if action == 'kretanje':
                mode = (request.POST.get('mode') or 'update').strip()
                loc_raw = request.POST.get('add_location_id') if mode == 'add' else request.POST.get('location_id')
                loc_id = int(loc_raw or 0)
                location = WarehouseLocation.objects.get(pk=loc_id)
                if is_ignored_stock_location(location):
                    raise MagacinError('Lokacija Prenos u MP se ne evidentira.')
                stocked_ids = {row['location'].pk for row in location_rows(product, variation)[0]}
                if mode == 'add':
                    apply_movement(
                        product=product,
                        variation=variation,
                        location=location,
                        tip='prijem',
                        kolicina=_parse_qty(request.POST.get('kolicina')),
                        napomena=request.POST.get('napomena') or 'Dodano na lokaciju',
                        user=request.user,
                    )
                    messages.success(request, 'Artikal je dodat na lokaciju.')
                else:
                    if loc_id not in stocked_ids:
                        raise MagacinError('Odaberi postojeću lokaciju ovog artikla.')
                    apply_movement(
                        product=product,
                        variation=variation,
                        location=location,
                        tip='korekcija',
                        kolicina=_parse_qty(request.POST.get('kolicina')),
                        napomena=request.POST.get('napomena') or 'Izmjena lokacije',
                        user=request.user,
                    )
                    messages.success(request, 'Lokacija je ažurirana.')
            elif action == 'meta':
                _save_product_meta(request, product)
                messages.success(request, 'Osnovne informacije su sačuvane.')
            else:
                raise MagacinError('Nepoznata akcija.')
        except (MagacinError, WarehouseLocation.DoesNotExist, ValueError) as exc:
            messages.error(request, str(exc) if str(exc) else 'Greška pri spremanju.')
        url = reverse('staff_magacin_artikal', args=[product.pk])
        params = []
        q = _magacin_search_query(request)
        if q:
            params.append(urlencode({'pretraga': q}))
        if variation:
            params.append(f'varijacija={variation.pk}')
        if params:
            url = f'{url}?{"&".join(params)}'
        return redirect(url)

    locations = list(usable_locations())
    rows, totals = location_rows(product, variation, locations=locations)
    stocked_ids = {row['location'].pk for row in rows}
    add_locations = [loc for loc in locations if loc.pk not in stocked_ids]
    meta = getattr(product, 'magacin_meta', None)
    tags = list(product.tagovi.all())
    movements = (
        WarehouseMovement.objects.filter(product=product)
        .select_related('location', 'to_location', 'variation', 'korisnik')
        .order_by('-kreiran', '-id')
    )
    if variation:
        movements = movements.filter(variation=variation)
    movements = list(movements[:10])

    variant_rows = []
    for var in variations:
        v_totals = stock_totals(product, var)
        variant_rows.append({
            'variation': var,
            'na_stanju': v_totals['na_stanju'],
            'cijena': var.prikazna_cijena,
        })

    context = _magacin_context(request, section='artikli', page_title=f'{product.naziv} — Magacin')
    context.update({
        'product': product,
        'meta': meta,
        'tags': tags,
        'location_rows': rows,
        'totals': totals,
        'locations': locations,
        'add_locations': add_locations,
        'movements': movements,
        'variations': variations,
        'variation': variation,
        'variant_rows': variant_rows,
        'dobavljaci': WarehouseSupplier.objects.filter(aktivan=True),
        'from_search': bool(_magacin_search_query(request)),
        'edit_url': reverse('staff_magacin_artikal_izmjena', args=[product.pk]),
    })
    return render(request, 'staff/magacin/artikal.html', context)


def _save_product_meta(request, product):
    meta, _ = ProductWarehouseMeta.objects.get_or_create(product=product)
    meta.tezina = (request.POST.get('tezina') or '').strip()[:40]
    meta.jedinica_mjere = (request.POST.get('jedinica_mjere') or 'kom').strip()[:20] or 'kom'
    try:
        meta.min_zaliha = max(0, int(request.POST.get('min_zaliha') or 0))
    except (TypeError, ValueError):
        raise MagacinError('Min. zaliha nije validan broj.')
    meta.veleprodajna_cijena = _parse_money(request.POST.get('veleprodajna_cijena'))
    dobavljac_id = request.POST.get('dobavljac_id') or ''
    if dobavljac_id:
        meta.dobavljac = get_object_or_404(WarehouseSupplier, pk=int(dobavljac_id))
    else:
        meta.dobavljac = None
    meta.save()


def _parse_optional_date(raw):
    text = (raw or '').strip()
    if not text:
        return None
    from datetime import date
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise MagacinError('Datum akcije nije validan.')


def _save_product_edit(request, product):
    from .odoo_import import _sifra_zauzeta

    naziv = (request.POST.get('naziv') or '').strip()[:200]
    if not naziv:
        raise MagacinError('Naziv je obavezan.')
    sifra = (request.POST.get('sifra') or '').strip()[:SIFRA_MAX_LENGTH] or None
    if sifra and _sifra_zauzeta(sifra, product_pk=product.pk):
        raise MagacinError(f'Šifra {sifra} je već zauzeta.')
    barkod = (request.POST.get('barkod') or '').strip()[:BARKOD_MAX_LENGTH]
    cijena = _parse_money(request.POST.get('cijena'))
    if cijena is None:
        raise MagacinError('Cijena je obavezna.')
    product.naziv = naziv
    product.sifra = sifra
    product.barkod = barkod
    product.opis = request.POST.get('opis') or ''
    product.cijena = cijena
    product.akcijska_cijena = _parse_money(request.POST.get('akcijska_cijena'))
    product.akcija_postotak = _parse_money(request.POST.get('akcija_postotak'))
    product.akcija_do = _parse_optional_date(request.POST.get('akcija_do'))
    try:
        pakovanje = (request.POST.get('pakovanje_komada') or '').strip()
        product.pakovanje_komada = int(pakovanje) if pakovanje else None
        if product.pakovanje_komada is not None and product.pakovanje_komada < 1:
            product.pakovanje_komada = None
    except (TypeError, ValueError):
        raise MagacinError('Pakovanje nije validan broj.')
    kat_id = (request.POST.get('kategorija_id') or '').strip()
    product.kategorija = Category.objects.filter(pk=kat_id).first() if kat_id else None
    brend_id = (request.POST.get('brend_id') or '').strip()
    product.brend = Brand.objects.filter(pk=brend_id).first() if brend_id else None
    product.prikazi_na_pocetnoj = request.POST.get('prikazi_na_pocetnoj') == '1'
    product.je_novitet = request.POST.get('je_novitet') == '1'
    product.je_hit = request.POST.get('je_hit') == '1'
    product.proizvedeno_u_japanu = request.POST.get('proizvedeno_u_japanu') == '1'
    product.aktivan = request.POST.get('aktivan') == '1'
    try:
        product.prioritet_lagera = int(request.POST.get('prioritet_lagera') or 0)
    except (TypeError, ValueError):
        product.prioritet_lagera = 0
    product.meta_title = (request.POST.get('meta_title') or '').strip()[:70]
    product.meta_description = (request.POST.get('meta_description') or '').strip()[:160]
    product.h1_naslov = (request.POST.get('h1_naslov') or '').strip()[:200]
    product.seo_tekst_iznad = request.POST.get('seo_tekst_iznad') or ''
    product.seo_tekst_ispod = request.POST.get('seo_tekst_ispod') or ''
    product.naziv_normalized = (naziv or '').casefold()[:220]
    product.sifra_normalized = (sifra or '').casefold()[:80]
    product.barkod_normalized = (barkod or '').casefold()[:80]
    if request.POST.get('ukloni_sliku') == '1' and product.slika:
        product.slika.delete(save=False)
        product.slika = None
    uploaded = request.FILES.get('slika')
    if uploaded:
        product.slika = uploaded
    product.save()

    tag_ids = [int(tid) for tid in request.POST.getlist('tagovi') if str(tid).isdigit()]
    product.tagovi.set(Tag.objects.filter(pk__in=tag_ids))

    _save_product_meta(request, product)

    for img_id in request.POST.getlist('obrisi_sliku'):
        if str(img_id).isdigit():
            ProductImage.objects.filter(pk=int(img_id), product=product).delete()
    for extra in request.FILES.getlist('dodatne_slike'):
        ProductImage.objects.create(product=product, slika=extra)

    var_ids = request.POST.getlist('variation_id')
    var_nazivi = request.POST.getlist('var_naziv')
    var_sifre = request.POST.getlist('var_sifra')
    var_cijene = request.POST.getlist('var_cijena')
    var_akcijske = request.POST.getlist('var_akcijska')
    var_pakovanja = request.POST.getlist('var_pakovanje')
    for index, raw_id in enumerate(var_ids):
        naziv_var = (var_nazivi[index] if index < len(var_nazivi) else '').strip()[:100]
        sifra_var = (var_sifre[index] if index < len(var_sifre) else '').strip()[:SIFRA_MAX_LENGTH] or None
        cijena_var = _parse_money(var_cijene[index] if index < len(var_cijene) else '')
        akcijska_var = _parse_money(var_akcijske[index] if index < len(var_akcijske) else '')
        pak_raw = (var_pakovanja[index] if index < len(var_pakovanja) else '').strip()
        try:
            pak_var = int(pak_raw) if pak_raw else None
        except (TypeError, ValueError):
            raise MagacinError('Pakovanje varijacije nije validan broj.')
        if raw_id and str(raw_id).isdigit():
            variation = get_object_or_404(ProductVariation, pk=int(raw_id), artikal=product)
        else:
            if not naziv_var:
                continue
            variation = ProductVariation(artikal=product)
        if sifra_var and _sifra_zauzeta(sifra_var, product_pk=product.pk, variation_pk=variation.pk):
            raise MagacinError(f'Šifra varijacije {sifra_var} je već zauzeta.')
        if not naziv_var:
            raise MagacinError('Naziv varijacije je obavezan.')
        variation.naziv = naziv_var
        variation.sifra = sifra_var
        variation.cijena = cijena_var
        variation.akcijska_cijena = akcijska_var
        variation.pakovanje_komada = pak_var
        variation.save()


@login_required(login_url='login')
@user_passes_test(_superuser_required)
def magacin_artikal_izmjena(request, pk):
    product = get_object_or_404(
        magacin_products_qs().select_related('kategorija', 'brend', 'magacin_meta'),
        pk=pk,
    )
    if request.method == 'POST':
        try:
            with transaction.atomic():
                _save_product_edit(request, product)
            messages.success(request, 'Artikal je ažuriran.')
            url = reverse('staff_magacin_artikal', args=[product.pk])
            q = _magacin_search_query(request)
            if q:
                url = f'{url}?{urlencode({"pretraga": q})}'
            return redirect(url)
        except MagacinError as exc:
            messages.error(request, str(exc))
    meta = getattr(product, 'magacin_meta', None)
    context = _magacin_context(request, section='artikli', page_title=f'Izmjena — {product.naziv}')
    context.update({
        'product': product,
        'meta': meta,
        'variations': list(product.varijacije.all()),
        'tagovi': list(product.tagovi.all()),
        'tag_ids': set(product.tagovi.values_list('pk', flat=True)),
        'kategorije': Category.objects.filter(aktivan=True).order_by('naziv'),
        'brendovi': Brand.objects.order_by('naziv'),
        'svi_tagovi': Tag.objects.order_by('naziv'),
        'dobavljaci': WarehouseSupplier.objects.filter(aktivan=True),
        'dodatne_slike': list(product.dodatne_slike.all()),
        'prioriteti': Product.PrioritetLagera.choices,
    })
    return render(request, 'staff/magacin/artikal_izmjena.html', context)


@login_required(login_url='login')
@user_passes_test(_superuser_required)
def magacin_istorija(request, pk):
    product = get_object_or_404(Product, pk=pk)
    qs = (
        WarehouseMovement.objects.filter(product=product)
        .select_related('location', 'to_location', 'variation', 'korisnik')
    )
    variation_id = request.GET.get('varijacija')
    if variation_id:
        qs = qs.filter(variation_id=variation_id)
    paginator = Paginator(qs, 50)
    page = paginator.get_page(request.GET.get('page') or 1)
    context = _magacin_context(request, section='artikli', page_title=f'Istorija — {product.naziv}')
    context.update({'product': product, 'page': page})
    return render(request, 'staff/magacin/istorija.html', context)


def _parse_iso_date(raw):
    text = (raw or '').strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _pregled_period(request):
    today = timezone.localdate()
    period = (request.GET.get('period') or 'day').strip().lower()
    date_from = _parse_iso_date(request.GET.get('from'))
    date_to = _parse_iso_date(request.GET.get('to'))
    if period == 'range' and date_from and date_to:
        if date_from > date_to:
            date_from, date_to = date_to, date_from
        label = f'{date_from.strftime("%d.%m.%Y.")} – {date_to.strftime("%d.%m.%Y.")}'
        return period, date_from, date_to, label
    if period == 'month':
        start = today.replace(day=1)
        return 'month', start, today, 'Ovaj mjesec'
    if period == 'year':
        start = today.replace(month=1, day=1)
        return 'year', start, today, 'Ova godina'
    return 'day', today, today, 'Danas'


def _pregled_order_stats(start, end):
    orders = Order.objects.exclude(status=Order.Status.OTKAZANA).filter(
        kreirana__date__gte=start,
        kreirana__date__lte=end,
    )
    broj = orders.count()
    iznos = orders.aggregate(s=Sum('ukupno'))['s'] or Decimal('0.00')
    items = OrderItem.objects.filter(narudzba__in=orders)
    komada = int(items.aggregate(s=Sum('kolicina'))['s'] or 0)
    linije = items.aggregate(s=Sum(F('cijena') * F('kolicina')))['s'] or Decimal('0.00')
    ids = set(items.exclude(artikal_id=None).values_list('artikal_id', flat=True))
    names = set(items.filter(artikal_id=None).values_list('naziv', flat=True))
    razlicitih = len(ids) + len(names)
    prosjek = (linije / komada).quantize(Decimal('0.01')) if komada else Decimal('0.00')
    lista = list(orders.order_by('-kreirana')[:20])
    return {
        'orders_count': broj,
        'orders_iznos': iznos,
        'orders_artikala': razlicitih,
        'orders_prosjek': prosjek,
        'orders_komada': komada,
        'period_orders': lista,
    }


def _pregled_chart(group, end):
    group = group if group in {'dani', 'mjeseci', 'godine'} else 'dani'
    labels = []
    starts = []
    if group == 'godine':
        years = list(range(end.year - 4, end.year + 1))
        for year in years:
            labels.append(str(year))
            starts.append(date(year, 1, 1))
        trunc = TruncYear('kreirana')
    elif group == 'mjeseci':
        cursor = date(end.year, end.month, 1)
        months = []
        for _ in range(12):
            months.append(cursor)
            month = cursor.month - 1 or 12
            year = cursor.year if cursor.month > 1 else cursor.year - 1
            cursor = date(year, month, 1)
        months.reverse()
        for item in months:
            labels.append(item.strftime('%m.%Y.'))
            starts.append(item)
        trunc = TruncMonth('kreirana')
    else:
        days = [end - timedelta(days=offset) for offset in range(13, -1, -1)]
        for item in days:
            labels.append(item.strftime('%d.%m.'))
            starts.append(item)
        trunc = TruncDate('kreirana')

    start = starts[0]
    rows = {
        (bucket.date() if hasattr(bucket, 'date') else bucket): row
        for bucket, row in (
            (
                item['bucket'],
                item,
            )
            for item in (
                Order.objects.exclude(status=Order.Status.OTKAZANA)
                .filter(kreirana__date__gte=start, kreirana__date__lte=end)
                .annotate(bucket=trunc)
                .values('bucket')
                .annotate(broj=Count('id'), iznos=Sum('ukupno'))
            )
            if item['bucket']
        )
    }
    series_orders = []
    series_iznos = []
    for item in starts:
        match = None
        for bucket, row in rows.items():
            if group == 'godine' and bucket.year == item.year:
                match = row
                break
            if group == 'mjeseci' and bucket.year == item.year and bucket.month == item.month:
                match = row
                break
            if group == 'dani' and bucket == item:
                match = row
                break
        series_orders.append(int(match['broj']) if match else 0)
        series_iznos.append(float(match['iznos'] or 0) if match else 0)
    return {
        'group': group,
        'labels': labels,
        'orders': series_orders,
        'iznos': series_iznos,
    }


@login_required(login_url='login')
@user_passes_test(_superuser_required)
def magacin_pregled(request):
    _ensure_magacin_locations()
    period, start, end, period_label = _pregled_period(request)
    graf = (request.GET.get('graf') or '').strip().lower()
    if graf not in {'dani', 'mjeseci', 'godine'}:
        if period == 'year':
            graf = 'mjeseci'
        elif period == 'month':
            graf = 'dani'
        else:
            graf = 'dani'
    order_stats = _pregled_order_stats(start, end)
    chart = _pregled_chart(graf, end)

    loc_count = usable_locations().count()
    stock_agg = countable_stock_qs().aggregate(
        na_stanju=Sum('kolicina'),
        rezervisano=Sum('rezervisano'),
        redova=Count('id'),
    )
    na_stanju = int(stock_agg['na_stanju'] or 0)
    rezervisano = max(0, int(stock_agg['rezervisano'] or 0))
    low_stock = []
    metas = ProductWarehouseMeta.objects.select_related('product').filter(min_zaliha__gt=0)
    totals_map = {
        row['product_id']: int(row['na_stanju'] or 0)
        for row in countable_stock_qs().values('product_id').annotate(na_stanju=Sum('kolicina'))
    }
    for meta in metas:
        qty = totals_map.get(meta.product_id, 0)
        if qty <= meta.min_zaliha:
            low_stock.append({'product': meta.product, 'na_stanju': qty, 'min_zaliha': meta.min_zaliha})
    low_stock.sort(key=lambda row: row['na_stanju'])

    recent = (
        WarehouseMovement.objects.filter(
            tip__in=[
                WarehouseMovement.Tip.PRIJEM,
                WarehouseMovement.Tip.PRODAJA,
                WarehouseMovement.Tip.TRANSFER,
                WarehouseMovement.Tip.KOREKCIJA,
            ],
        )
        .select_related('product', 'location', 'to_location', 'variation')
        .order_by('-kreiran', '-id')[:10]
    )

    context = _magacin_context(request, section='pregled', page_title='Pregled — Magacin')
    context.update({
        'stat_artikala': magacin_products_qs().count(),
        'stat_lokacija': loc_count,
        'stat_na_stanju': na_stanju,
        'stat_rezervisano': rezervisano,
        'stat_dostupno': max(0, na_stanju - rezervisano),
        'low_stock': low_stock[:20],
        'recent_movements': recent,
        'period': period,
        'period_label': period_label,
        'date_from': start.isoformat(),
        'date_to': end.isoformat(),
        'graf': graf,
        'chart_json': json.dumps(chart, ensure_ascii=False),
        **order_stats,
    })
    return render(request, 'staff/magacin/pregled.html', context)


@login_required(login_url='login')
@user_passes_test(_superuser_required)
def magacin_lokacije(request):
    _ensure_magacin_locations()
    if request.method == 'POST':
        action = (request.POST.get('action') or 'save').strip()
        try:
            if action == 'delete':
                loc = get_object_or_404(WarehouseLocation, pk=request.POST.get('location_id'))
                if loc.zalihe.exclude(kolicina=0).exists():
                    raise MagacinError('Lokacija ima zalihe — prvo ih premjesti ili nuliraj.')
                loc.zalihe.all().delete()
                loc.delete()
                messages.success(request, 'Lokacija je obrisana.')
            else:
                loc_id = request.POST.get('location_id')
                sifra = (request.POST.get('sifra') or '').strip().upper()
                naziv = (request.POST.get('naziv') or '').strip()
                if not sifra or not naziv:
                    raise MagacinError('Šifra i naziv su obavezni.')
                if is_ignored_stock_location(sifra=sifra, name=naziv):
                    raise MagacinError('Lokacija Prenos u MP se ne evidentira u Magacinu.')
                if loc_id:
                    loc = get_object_or_404(WarehouseLocation, pk=loc_id)
                else:
                    loc = WarehouseLocation()
                if WarehouseLocation.objects.filter(sifra=sifra).exclude(pk=loc.pk or 0).exists():
                    raise MagacinError('Šifra lokacije već postoji.')
                loc.sifra = sifra[:20]
                loc.naziv = naziv[:120]
                loc.opis = (request.POST.get('opis') or '').strip()[:300]
                loc.aktivan = request.POST.get('aktivan') == '1'
                try:
                    loc.redoslijed = int(request.POST.get('redoslijed') or 0)
                except (TypeError, ValueError):
                    loc.redoslijed = 0
                loc.save()
                messages.success(request, 'Lokacija je sačuvana.')
        except MagacinError as exc:
            messages.error(request, str(exc))
        return redirect('staff_magacin_lokacije')

    locations = []
    for loc in WarehouseLocation.objects.exclude(
        sifra__icontains='prenos',
    ).exclude(
        naziv__icontains='prenos',
    ).exclude(
        odoo_location_path__icontains='prenos',
    ):
        agg = loc.zalihe.aggregate(na_stanju=Sum('kolicina'), artikala=Count('product', distinct=True))
        locations.append({
            'location': loc,
            'na_stanju': int(agg['na_stanju'] or 0),
            'artikala': int(agg['artikala'] or 0),
        })
    context = _magacin_context(request, section='lokacije', page_title='Lokacije — Magacin')
    context.update({'locations': locations})
    return render(request, 'staff/magacin/lokacije.html', context)


@login_required(login_url='login')
@user_passes_test(_superuser_required)
def magacin_zalihe(request):
    _ensure_magacin_locations()
    location_id = request.GET.get('lokacija') or ''
    query = _magacin_search_query(request)
    only_low = request.GET.get('nisko') == '1'

    qs = countable_stock_qs(
        WarehouseStock.objects.select_related(
            'product', 'product__kategorija', 'variation', 'location',
        )
    )
    if location_id:
        qs = qs.filter(location_id=location_id)
    if query:
        qs = qs.filter(
            Q(product__naziv__icontains=query)
            | Q(product__sifra__icontains=query)
            | Q(product__barkod__icontains=query)
            | Q(variation__sifra__icontains=query)
            | Q(variation__naziv__icontains=query)
        )
    if only_low:
        qs = qs.filter(kolicina__lte=5)
    qs = qs.order_by('location__redoslijed', 'location__sifra', 'product__naziv')

    paginator = Paginator(qs, 60)
    page = paginator.get_page(request.GET.get('page') or 1)
    context = _magacin_context(request, section='zalihe', page_title='Zalihe — Magacin')
    context.update({
        'page': page,
        'locations': usable_locations(),
        'selected_location': location_id,
        'only_low': only_low,
        'magacin_search': query,
    })
    return render(request, 'staff/magacin/zalihe.html', context)


def _resolve_transfer_product(request, *, product_id=None, variation_id=None):
    raw = str(product_id or '').strip()
    if not raw:
        raise MagacinError('Odaberi artikal.')
    product = magacin_products_qs().filter(pk=raw).first() if raw.isdigit() else None
    if product is None:
        raise MagacinError('Artikal nije pronađen.')
    variation = None
    vid = str(variation_id or '').strip()
    if vid:
        variation = get_object_or_404(ProductVariation, pk=vid, artikal=product)
    return product, variation


@login_required(login_url='login')
@user_passes_test(_superuser_required)
def magacin_lokacije_lookup(request):
    query = (request.GET.get('q') or '').strip()
    only_stock = (request.GET.get('sa_zalihom') or '') == '1'
    product_id = (request.GET.get('product_id') or '').strip()
    variation_id = (request.GET.get('variation_id') or '').strip()
    locations = list(usable_locations())
    stock_map = {}
    if product_id:
        product = get_object_or_404(magacin_products_qs(), pk=product_id)
        variation = None
        if variation_id:
            variation = get_object_or_404(ProductVariation, pk=variation_id, artikal=product)
        rows, _ = location_rows(product, variation)
        stock_map = {row['location'].pk: row for row in rows}
        if only_stock:
            locations = [
                loc for loc in locations
                if stock_map.get(loc.pk) and stock_map[loc.pk]['kolicina'] > 0
            ]
    if query:
        needle = query.casefold()
        locations = [
            loc for loc in locations
            if needle in loc.label.casefold()
            or needle in (loc.sifra or '').casefold()
            or needle in (loc.naziv or '').casefold()
            or needle in (loc.odoo_location_path or '').casefold()
        ]
    results = []
    limit = 200 if only_stock and not query else 40
    for loc in locations[:limit]:
        row = stock_map.get(loc.pk)
        results.append({
            'id': loc.pk,
            'sifra': loc.sifra,
            'naziv': loc.naziv or '',
            'label': loc.label,
            'kolicina': int(row['kolicina']) if row else 0,
            'dostupno': int(row['dostupno']) if row else 0,
        })
    return JsonResponse({'results': results, 'query': query})


@login_required(login_url='login')
@user_passes_test(_superuser_required)
def magacin_transferi(request):
    _ensure_magacin_locations()
    tab = (request.GET.get('tab') or request.POST.get('tab') or 'ubaci').strip()
    if tab not in {'ubaci', 'prenos'}:
        tab = 'ubaci'
    if request.method == 'POST':
        action = (request.POST.get('action') or tab or 'transfer').strip()
        try:
            if action == 'ubaci':
                location = get_object_or_404(
                    usable_locations(), pk=int(request.POST.get('location_id') or 0),
                )
                product_ids = request.POST.getlist('product_id')
                variation_ids = request.POST.getlist('variation_id')
                kolicine = request.POST.getlist('kolicina')
                if not product_ids:
                    raise MagacinError('Dodaj barem jedan artikal.')
                count = 0
                with transaction.atomic():
                    for index, raw_pid in enumerate(product_ids):
                        product, variation = _resolve_transfer_product(
                            request,
                            product_id=raw_pid,
                            variation_id=variation_ids[index] if index < len(variation_ids) else '',
                        )
                        qty = _parse_qty(kolicine[index] if index < len(kolicine) else '')
                        apply_movement(
                            product=product,
                            variation=variation,
                            location=location,
                            tip='prijem',
                            kolicina=qty,
                            napomena=request.POST.get('napomena') or 'Ubaci u lokaciju',
                            user=request.user,
                        )
                        count += 1
                messages.success(request, f'Ubačeno {count} artikala na {location.label}.')
                return redirect(f"{reverse('staff_magacin_transferi')}?tab=ubaci")
            product, variation = _resolve_transfer_product(
                request,
                product_id=request.POST.get('product_id') or request.POST.get('product_ref'),
                variation_id=request.POST.get('variation_id'),
            )
            apply_movement(
                product=product,
                variation=variation,
                location=int(request.POST.get('location_id') or 0),
                to_location=int(request.POST.get('to_location_id') or 0),
                tip=WarehouseMovement.Tip.TRANSFER,
                kolicina=_parse_qty(request.POST.get('kolicina')),
                napomena=request.POST.get('napomena') or '',
                user=request.user,
            )
            messages.success(request, 'Transfer je evidentiran.')
            return redirect(f"{reverse('staff_magacin_transferi')}?tab=prenos")
        except (MagacinError, WarehouseLocation.DoesNotExist, ValueError) as exc:
            messages.error(request, str(exc) if str(exc) else 'Greška pri spremanju.')
        return redirect(f"{reverse('staff_magacin_transferi')}?tab={tab}")

    transfers = (
        WarehouseMovement.objects.filter(
            Q(tip=WarehouseMovement.Tip.TRANSFER)
            | Q(tip=WarehouseMovement.Tip.PRIJEM, napomena__icontains='Ubaci u lokaciju')
        )
        .select_related('product', 'variation', 'location', 'to_location', 'korisnik')
        .order_by('-kreiran', '-id')[:40]
    )
    context = _magacin_context(request, section='transferi', page_title='Transferi — Magacin')
    context.update({
        'transfers': transfers,
        'tab': tab,
        'lookup_url': reverse('staff_magacin_artikli_lookup'),
        'loc_lookup_url': reverse('staff_magacin_lokacije_lookup'),
    })
    return render(request, 'staff/magacin/transferi.html', context)


@login_required(login_url='login')
@user_passes_test(_superuser_required)
def magacin_narudzbe(request):
    izvor = (request.GET.get('izvor') or 'sve').strip()
    show_validated = (request.GET.get('validirane') or '') == '1'
    orders = Order.objects.exclude(status=Order.Status.OTKAZANA).order_by('-kreirana')
    if izvor == 'magacin':
        orders = orders.filter(izvor=Order.Izvor.MAGACIN)
    elif izvor == 'webshop':
        orders = orders.filter(izvor=Order.Izvor.WEBSHOP)
    if show_validated:
        orders = orders.filter(lager_status=Order.LagerStatus.VALIDIRANO)
    else:
        orders = orders.exclude(lager_status=Order.LagerStatus.VALIDIRANO)
    base_qs = Order.objects.exclude(status=Order.Status.OTKAZANA)
    context = _magacin_context(request, section='narudzbe', page_title='Narudžbe — Magacin')
    context.update({
        'orders': orders[:80],
        'izvor_filter': izvor,
        'show_validated': show_validated,
        'rucne_count': base_qs.filter(
            izvor=Order.Izvor.MAGACIN,
        ).exclude(lager_status=Order.LagerStatus.VALIDIRANO).count(),
        'validated_count': base_qs.filter(lager_status=Order.LagerStatus.VALIDIRANO).count(),
    })
    return render(request, 'staff/magacin/narudzbe.html', context)


@login_required(login_url='login')
@user_passes_test(_superuser_required)
def magacin_narudzbe_stampa(request):
    from .views import _order_print_job

    brojevi = [b.strip() for b in request.GET.getlist('b') if (b or '').strip()]
    brojevi = list(dict.fromkeys(brojevi))[:30]
    if not brojevi:
        messages.error(request, 'Odaberi barem jednu narudžbu za štampu.')
        return redirect('staff_magacin_narudzbe')
    orders = list(
        Order.objects.filter(broj__in=brojevi)
        .exclude(status=Order.Status.OTKAZANA)
        .prefetch_related('stavke')
    )
    by_broj = {order.broj: order for order in orders}
    ordered = [by_broj[broj] for broj in brojevi if broj in by_broj]
    if not ordered:
        messages.error(request, 'Odabrane narudžbe nisu pronađene.')
        return redirect('staff_magacin_narudzbe')
    print_jobs = [_order_print_job(order) for order in ordered]
    context = {
        **print_jobs[0],
        'print_jobs': print_jobs,
        'print_brojevi': [order.broj for order in ordered],
        'requires_mp_check': any(job['requires_mp_check'] for job in print_jobs),
        'mark_printed_url': reverse('staff_magacin_narudzbe_mark_printed'),
    }
    return render(request, 'staff/order_print.html', context)


@login_required(login_url='login')
@user_passes_test(_superuser_required)
@require_POST
def magacin_narudzbe_mark_printed(request):
    brojevi = [b.strip() for b in request.POST.getlist('b') if (b or '').strip()]
    now = timezone.now()
    qs = Order.objects.filter(broj__in=brojevi, odstampana=False)
    updated = qs.update(odstampana=True, odstampana_at=now)
    return JsonResponse({
        'ok': True,
        'updated': updated,
        'brojevi': brojevi,
        'odstampana': True,
    })


@login_required(login_url='login')
@user_passes_test(_superuser_required)
def magacin_artikli_lookup(request):
    query = (request.GET.get('q') or '').strip()
    include_zero = (request.GET.get('bez_zalihe') or '') == '1'
    products, exact = search_products(query, limit=30, include_zero=include_zero)
    if exact:
        products = [exact]
    results = []
    for product in products:
        totals = stock_totals(product)
        results.append({
            'id': product.pk,
            'naziv': product.naziv,
            'sifra': product.sifra or '',
            'cijena': str(product.prikazna_cijena),
            'na_stanju': totals['na_stanju'],
            'dostupno': totals['dostupno'],
            'varijacije': [
                {
                    'id': var.pk,
                    'naziv': var.naziv,
                    'sifra': var.sifra or '',
                    'cijena': str(var.prikazna_cijena),
                    'na_stanju': stock_totals(product, var)['dostupno'],
                }
                for var in product.varijacije.all()
            ],
        })
    return JsonResponse({'results': results, 'query': query, 'include_zero': include_zero})


@login_required(login_url='login')
@user_passes_test(_superuser_required)
def magacin_narudzba_nova(request):
    context = _magacin_context(request, section='narudzbe', page_title='Nova ručna narudžba')
    if request.method == 'POST':
        try:
            order = _create_manual_order(request)
        except MagacinError as exc:
            messages.error(request, str(exc))
            context['form'] = request.POST
            context['form_lines'] = _posted_display_lines(request)
            return render(request, 'staff/magacin/narudzba_nova.html', context)
        messages.success(request, f'Narudžba #{order.broj} je kreirana.')
        return redirect('staff_magacin_narudzbe')

    context['form'] = {}
    context['form_lines'] = []
    return render(request, 'staff/magacin/narudzba_nova.html', context)


def _posted_display_lines(request):
    lines = []
    product_ids = request.POST.getlist('product_id')
    variation_ids = request.POST.getlist('variation_id')
    kolicine = request.POST.getlist('kolicina')
    mp_flags = request.POST.getlist('mp_ok')
    for index, raw_pid in enumerate(product_ids):
        try:
            product = Product.objects.get(pk=int(raw_pid))
        except (TypeError, ValueError, Product.DoesNotExist):
            continue
        variation = None
        raw_vid = variation_ids[index] if index < len(variation_ids) else ''
        if raw_vid:
            variation = ProductVariation.objects.filter(pk=int(raw_vid), artikal=product).first()
        try:
            qty = _parse_qty(kolicine[index] if index < len(kolicine) else 1)
        except MagacinError:
            qty = 1
        cijena = variation.prikazna_cijena if variation else product.prikazna_cijena
        available = stock_totals(product, variation)['dostupno']
        lines.append({
            'product': product,
            'variation': variation,
            'qty': qty,
            'mp_ok': (mp_flags[index] if index < len(mp_flags) else '') == '1',
            'cijena': cijena,
            'dostupno': available,
        })
    return lines


def _create_manual_order(request):
    ime = (request.POST.get('ime_prezime') or '').strip()
    telefon = (request.POST.get('telefon') or '').strip()
    if not ime:
        raise MagacinError('Ime i prezime su obavezni.')
    if not telefon:
        raise MagacinError('Telefon je obavezan.')
    email = (request.POST.get('email') or '').strip() or 'rucna@opremazaribolov.ba'
    adresa = (request.POST.get('adresa') or '').strip() or 'Ručni unos'
    grad = (request.POST.get('grad') or '').strip() or '—'
    product_ids = request.POST.getlist('product_id')
    variation_ids = request.POST.getlist('variation_id')
    kolicine = request.POST.getlist('kolicina')
    mp_flags = request.POST.getlist('mp_ok')
    if not product_ids:
        raise MagacinError('Dodaj barem jedan artikal.')

    lines = []
    for index, raw_pid in enumerate(product_ids):
        try:
            product = magacin_products_qs().get(pk=int(raw_pid))
        except (TypeError, ValueError, Product.DoesNotExist):
            raise MagacinError('Artikal nije pronađen u Magacinu.')
        variation = None
        raw_vid = variation_ids[index] if index < len(variation_ids) else ''
        if raw_vid:
            variation = ProductVariation.objects.filter(pk=int(raw_vid), artikal=product).first()
            if not variation:
                raise MagacinError(f'Varijacija nije pronađena za „{product.naziv}”.')
        elif product.varijacije.exists():
            raise MagacinError(f'Odaberi varijaciju za „{product.naziv}”.')
        qty = _parse_qty(kolicine[index] if index < len(kolicine) else 1)
        if qty <= 0:
            raise MagacinError('Količina mora biti veća od nule.')
        mp_ok = (mp_flags[index] if index < len(mp_flags) else '') == '1'
        available = stock_totals(product, variation)['dostupno']
        if available < qty and not mp_ok:
            raise MagacinError(
                f'„{product.naziv}” nema dovoljno zalihe u magacinu ({available}). '
                'Provjeri maloprodaju pa klikni Dodaj, ili makni stavku.'
            )
        cijena = variation.prikazna_cijena if variation else product.prikazna_cijena
        bazna = variation.bazna_cijena if variation else product.bazna_cijena
        lines.append({
            'product': product,
            'variation': variation,
            'qty': qty,
            'mp_ok': mp_ok,
            'cijena': cijena,
            'bazna': bazna,
            'shortfall': max(0, qty - available),
        })

    medjuzbir = sum((line['cijena'] * line['qty'] for line in lines), Decimal('0.00'))
    from .pricing import _standardna_dostava
    dostava, _, _, _ = _standardna_dostava(medjuzbir)
    with transaction.atomic():
        order = _save_manual_order(
            request, ime, telefon, email, adresa, grad, medjuzbir, dostava, lines,
        )
    return order


def _save_manual_order(request, ime, telefon, email, adresa, grad, medjuzbir, dostava, lines):
    napomena = (request.POST.get('napomena') or '').strip()
    mp_names = [
        (line['variation'].naziv if line['variation'] else line['product'].naziv)
        for line in lines
        if line['mp_ok'] and line['shortfall'] > 0
    ]
    if mp_names:
        extra = 'Maloprodaja: ' + ', '.join(mp_names)
        napomena = f'{napomena}\n{extra}'.strip() if napomena else extra
    order = Order.objects.create(
        ime_prezime=ime[:200],
        email=email[:254],
        telefon=telefon[:30],
        adresa=adresa[:300],
        grad=grad[:100],
        postanski_broj=(request.POST.get('postanski_broj') or '').strip()[:20],
        napomena=napomena,
        medjuzbir=medjuzbir,
        dostava=dostava,
        popust=Decimal('0.00'),
        ukupno=medjuzbir + dostava,
        status=Order.Status.NOVA,
        izvor=Order.Izvor.MAGACIN,
    )
    for line in lines:
        product = line['product']
        variation = line['variation']
        OrderItem.objects.create(
            narudzba=order,
            artikal=product,
            varijacija=variation,
            naziv=product.naziv[:200],
            product_naziv=product.naziv[:200],
            varijacija_naziv=(variation.naziv[:100] if variation else ''),
            sifra=((variation.sifra if variation and variation.sifra else product.sifra) or '')[:200],
            cijena=line['cijena'],
            bazna_cijena=line['bazna'],
            kolicina=line['qty'],
        )
        leftover = reserve_for_order(
            order,
            product,
            line['qty'] - line['shortfall'],
            variation=variation,
            user=request.user,
            napomena=f'Rezervacija #{order.broj}',
        )
        if leftover and not line['mp_ok']:
            raise MagacinError(f'Nije rezervisana puna količina za {product.naziv}.')
    order.lager_status = Order.LagerStatus.REZERVISANO
    order.save(update_fields=['lager_status'])
    return order


@login_required(login_url='login')
@user_passes_test(_superuser_required)
def magacin_pakovanje(request):
    show_packed = (request.GET.get('zapakovane') or '') == '1'
    orders = (
        Order.objects.exclude(status=Order.Status.OTKAZANA)
        .order_by('-kreirana')
    )
    if show_packed:
        orders = orders.filter(zapakovana=True)
    else:
        orders = orders.filter(zapakovana=False).filter(
            status__in=[Order.Status.NOVA, Order.Status.POTVRDJENA],
        )
    context = _magacin_context(request, section='pakovanje', page_title='Pakovanje — Magacin')
    context.update({
        'orders': orders[:80],
        'show_packed': show_packed,
        'packed_count': Order.objects.filter(zapakovana=True)
        .exclude(status=Order.Status.OTKAZANA)
        .count(),
    })
    return render(request, 'staff/magacin/pakovanje.html', context)


@login_required(login_url='login')
@user_passes_test(_superuser_required)
def magacin_dobavljaci(request):
    if request.method == 'POST':
        action = (request.POST.get('action') or 'save').strip()
        try:
            if action == 'delete':
                supplier = get_object_or_404(WarehouseSupplier, pk=request.POST.get('supplier_id'))
                supplier.delete()
                messages.success(request, 'Dobavljač je obrisan.')
            else:
                sid = request.POST.get('supplier_id')
                naziv = (request.POST.get('naziv') or '').strip()
                if not naziv:
                    raise MagacinError('Naziv dobavljača je obavezan.')
                if sid:
                    supplier = get_object_or_404(WarehouseSupplier, pk=sid)
                else:
                    supplier = WarehouseSupplier()
                if WarehouseSupplier.objects.filter(naziv=naziv).exclude(pk=supplier.pk or 0).exists():
                    raise MagacinError('Dobavljač s tim nazivom već postoji.')
                supplier.naziv = naziv[:160]
                supplier.aktivan = request.POST.get('aktivan') == '1'
                supplier.save()
                messages.success(request, 'Dobavljač je sačuvan.')
        except MagacinError as exc:
            messages.error(request, str(exc))
        return redirect('staff_magacin_dobavljaci')

    suppliers = []
    for supplier in WarehouseSupplier.objects.all():
        suppliers.append({
            'supplier': supplier,
            'artikala': supplier.artikli.count(),
        })
    context = _magacin_context(request, section='dobavljaci', page_title='Dobavljači — Magacin')
    context.update({'suppliers': suppliers})
    return render(request, 'staff/magacin/dobavljaci.html', context)


@login_required(login_url='login')
@user_passes_test(_superuser_required)
def magacin_izvjestaji(request):
    order_query = (request.GET.get('narudzba') or '').strip()
    found_orders = []
    if order_query:
        digits = ''.join(ch for ch in order_query if ch.isdigit())
        filt = (
            Q(broj__icontains=order_query)
            | Q(ime_prezime__icontains=order_query)
            | Q(telefon__icontains=order_query)
        )
        if digits and digits != order_query:
            filt |= Q(broj__icontains=digits) | Q(telefon__icontains=digits)
        found_orders = list(
            Order.objects.filter(filt).order_by('-kreirana')[:50]
        )
    by_location = []
    for loc in usable_locations():
        agg = loc.zalihe.aggregate(
            na_stanju=Sum('kolicina'),
            rezervisano=Sum('rezervisano'),
            artikala=Count('product', distinct=True),
        )
        qty = int(agg['na_stanju'] or 0)
        reserved = max(0, int(agg['rezervisano'] or 0))
        by_location.append({
            'location': loc,
            'na_stanju': qty,
            'rezervisano': reserved,
            'dostupno': max(0, qty - reserved),
            'artikala': int(agg['artikala'] or 0),
        })
    movements_today = WarehouseMovement.objects.filter(
        kreiran__date=timezone.localdate(),
    ).count()
    context = _magacin_context(request, section='izvjestaji', page_title='Izvještaji — Magacin')
    context.update({
        'by_location': by_location,
        'movements_today': movements_today,
        'products_total': magacin_products_qs().count(),
        'products_active': magacin_products_qs().filter(aktivan=True).count(),
        'order_query': order_query,
        'found_orders': found_orders,
    })
    return render(request, 'staff/magacin/izvjestaji.html', context)


@login_required(login_url='login')
@user_passes_test(_superuser_required)
def magacin_podesavanja(request):
    context = _magacin_context(request, section='podesavanja', page_title='Podešavanja — Magacin')
    context.update({
        'odoo_configured': odoo_je_konfigurisan(),
        'location_count': WarehouseLocation.objects.count(),
        'sync_count': WarehouseSyncLog.objects.count(),
    })
    return render(request, 'staff/magacin/podesavanja.html', context)


@login_required(login_url='login')
@user_passes_test(_superuser_required)
def magacin_sync_istorija(request):
    paginator = Paginator(WarehouseSyncLog.objects.select_related('korisnik'), 30)
    page = paginator.get_page(request.GET.get('page') or 1)
    context = _magacin_context(request, section='podesavanja', page_title='Istorija sync-a — Magacin')
    context.update({'page': page})
    return render(request, 'staff/magacin/sync_istorija.html', context)


@login_required(login_url='login')
@user_passes_test(_superuser_required)
@require_POST
def magacin_sync(request):
    next_url = request.POST.get('next') or reverse('staff_magacin_artikli')
    action = (request.POST.get('action') or 'start').strip()
    try:
        if action == 'continue':
            job = request.session.get(MAGACIN_SYNC_SESSION_KEY)
            if not job:
                raise MagacinError('Sync sesija je istekla. Pokreni Sync ponovo.')
            job = run_sync_chunk(job, user=request.user)
        else:
            product = None
            product_id = request.POST.get('product_id')
            if product_id:
                product = get_object_or_404(Product, pk=product_id)
            job = start_full_sync(user=request.user, product=product)
            job = run_sync_chunk(job, user=request.user)

        if job.get('done'):
            request.session.pop(MAGACIN_SYNC_SESSION_KEY, None)
            request.session.modified = True
            if job.get('error'):
                messages.error(request, job['error'])
            else:
                log = last_sync()
                messages.success(request, (log.poruka if log else '') or 'Sinhronizacija je završena.')
            return HttpResponseRedirect(next_url.split('?')[0] if next_url else reverse('staff_magacin_artikli'))

        request.session[MAGACIN_SYNC_SESSION_KEY] = job
        request.session.modified = True
        return redirect(f"{reverse('staff_magacin_artikli')}?sync=1")
    except MagacinError as exc:
        request.session.pop(MAGACIN_SYNC_SESSION_KEY, None)
        request.session.modified = True
        messages.error(request, str(exc))
        return HttpResponseRedirect(next_url)
