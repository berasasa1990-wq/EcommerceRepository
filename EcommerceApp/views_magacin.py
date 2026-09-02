"""Staff Magacin — lager artikala po lokacijama."""

import base64
import hmac
import json
import logging
import re
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.db.models import Count, Exists, F, OuterRef, Prefetch, Q, Sum
from django.db.models.functions import TruncDate, TruncMonth, TruncYear
from django.http import FileResponse, Http404, HttpResponseRedirect, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from .magacin import (
    DISCOVER_SYNC_BATCH,
    MAGACIN_SYNC_SESSION_KEY,
    MagacinError,
    active_popis,
    add_popis_stavka,
    pause_popis,
    paused_popisi,
    finished_popisi,
    popis_spreman_za_stampu,
    resume_popis,
    active_vp_narudzba,
    add_vp_stavka,
    add_vp_bulk_stavke,
    ponuda_totals,
    accept_ponuda,
    vp_draft_totals,
    apply_movement,
    attach_uvoz_list_metrics,
    create_magacin_uvoz_from_rows,
    leftover_uvoz_stocks,
    move_uvoz_leftovers_to_mp,
    finish_popis,
    mark_popis_odstampan,
    finish_vp_narudzba,
    remove_popis_stavka,
    remove_vp_stavka,
    set_popis_cekirano,
    set_popis_stavka_qty,
    set_vp_customer,
    set_vp_stavka_qty,
    start_popis,
    start_vp_narudzba,
    create_prenos_mp_pick,
    drop_prenos_mp_item,
    trim_prenos_mp_item,
    is_prenos_mp_order,
    is_vp_order,
    add_item_to_order,
    set_order_item_qty,
    remove_item_from_order,
    drop_missing_pick_line,
    clear_pick_location_stock,
    order_is_editable,
    mark_order_packed,
    skini_sa_sajta,
    ubaci_na_sajt,
    reserve_for_order,
    release_holds_for_product,
    cancel_order_stock,
    is_ignored_stock_location,
    is_uncountable_stock_location,
    maloprodaja_locations,
    ignored_location_q,
    last_sync,
    load_running_sync_job,
    persist_sync_job,
    run_sync_until,
    location_rows,
    maloprodaja_location_rows,
    missing_maloprodaja_rows,
    display_stock_totals,
    countable_stock_qs,
    recorded_stock_qs,
    deduct_mp_daily_stock,
    extract_mp_daily_text_from_upload,
    parse_mp_daily_datum,
    parse_mp_daily_text,
    preview_mp_daily_rows,
    compare_lager_document,
    extract_lager_document_rows,
    obrisi_artikal_iz_baze,
    save_mp_daily_skidanje,
    magacin_in_stock_q,
    magacin_products_qs,
    usable_locations,
    location_stock_qty,
    set_location_counted_qty,
    cancel_sync,
    run_sync_chunk,
    validate_order_stock,
    search_products,
    seed_default_locations,
    start_full_sync,
    start_price_sync,
    start_sifra_sync,
    start_stock_sync,
    stock_totals,
    vp_cijena,
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
    Uvoz,
    UvozStavka,
    NivelacijaOznaka,
    MagacinPopis,
    MagacinMpDnevnoSkidanje,
    MagacinPonuda,
    MagacinPonudaStavka,
    MagacinVpNarudzba,
    WarehouseLocation,
    WarehouseMovement,
    MagacinDeklaracijaBrend,
    WarehouseCustomer,
    WarehouseStock,
    WarehouseSupplier,
    WarehouseSyncLog,
    OrderStockHold,
)
from .db_backup import (
    BackupError,
    backup_storage_status,
    create_backup,
    last_backup,
    list_backups,
    resolve_backup_file,
    restore_backup,
    save_uploaded_backup,
)
from .odoo_client import odoo_je_konfigurisan
from .views import _base_context, _superuser_required

logger = logging.getLogger(__name__)


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


_ORDER_STOCK_NOTE_RE = re.compile(
    r'(?i)(?:validacija|rezervacija|otkazivanje|izmjena|prenos u mp)\s*#|#\d+|ru[cč]na narud[zž]ba',
)
_MOVEMENT_ORDER_BROJ_RE = re.compile(r'#(\d+)')


def _movement_from_order(movement):
    note = (getattr(movement, 'napomena', None) or '').strip()
    return bool(note and _ORDER_STOCK_NOTE_RE.search(note))


def _movement_order_broj(movement):
    note = (getattr(movement, 'napomena', None) or '').strip()
    match = _MOVEMENT_ORDER_BROJ_RE.search(note)
    return match.group(1) if match else ''


def _attach_movement_kupci(movements):
    """Na kretanjima iz narudžbe stavi ime kupca (za prikaz u istoriji)."""
    brojevi = []
    seen = set()
    for movement in movements:
        broj = _movement_order_broj(movement)
        movement.kupac_label = ''
        movement.kupac_broj = broj
        if broj and broj not in seen:
            seen.add(broj)
            brojevi.append(broj)
    if not brojevi:
        return movements
    by_broj = {
        order.broj: (order.ime_prezime or '').strip()
        for order in Order.objects.filter(broj__in=brojevi).only('broj', 'ime_prezime')
    }
    for movement in movements:
        movement.kupac_label = by_broj.get(getattr(movement, 'kupac_broj', ''), '')
    return movements


def _movement_korisnik_label(movement):
    if not getattr(movement, 'korisnik_id', None) or _movement_from_order(movement):
        return ''
    return _user_display(movement.korisnik)


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
    if phase == 'discover':
        total = max(1, int(job.get('discover_offset') or 0) + DISCOVER_SYNC_BATCH)
        current = int(job.get('discover_offset') or 0)
        label = f'Čitam Odoo katalog: {len(job.get("discovered_ids") or [])} artikala'
    elif phase == 'prices':
        total = max(1, len(template_ids))
        current = int(job.get('position') or 0)
        label = f'Usklađujem cijene s Odoo: {current} / {len(template_ids)}'
    elif phase == 'sifre':
        total = max(1, len(template_ids))
        current = int(job.get('position') or 0)
        label = f'Ažuriram šifre po nazivu: {current} / {len(template_ids)}'
    elif phase == 'catalog':
        total = max(1, len(template_ids))
        current = int(job.get('position') or 0)
        label = (
            f'Dodajem {current} / {len(template_ids)} artikala kojih nema na sajtu'
        )
    elif phase == 'locations':
        total = 1
        current = 1
        label = 'Lokacije iz Odoo'
    elif phase == 'stock':
        total = max(1, len(stock_ids))
        current = int(job.get('stock_position') or 0)
        label = (
            f'Usklađujem količine s Odoo: {current} / {len(stock_ids)}'
            if job.get('stock_only')
            else f'Zalihe {current} / {len(stock_ids)}'
        )
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


def _prenos_mp_q():
    # Samo ime — JSON lookup pick_state__kind na Postgresu usporava svaku Magacin stranicu.
    return Q(ime_prezime='Prenos u MP')


def _validated_orders_q():
    return Q(lager_status=Order.LagerStatus.VALIDIRANO) | Q(status=Order.Status.ZAVRSENA)


def _unvalidated_orders_qs():
    return (
        Order.objects.exclude(status=Order.Status.OTKAZANA)
        .exclude(status=Order.Status.REZERVACIJA)
        .exclude(_validated_orders_q())
    )


def _completed_pick_qs():
    return (
        Order.objects.filter(_validated_orders_q())
        .exclude(status=Order.Status.OTKAZANA)
        .exclude(_prenos_mp_q())
    )


def _normalize_order_scan(raw):
    code = (raw or '').strip().upper().replace(' ', '').lstrip('#')
    if code.startswith('OZB'):
        code = code[3:]
    return code


def find_order_by_scan(raw, *, qs=None):
    code = _normalize_order_scan(raw)
    if not code:
        return None
    qs = qs if qs is not None else Order.objects.all()
    order = qs.filter(broj__iexact=code).first()
    if order:
        return order
    if code.isdigit():
        padded = {code, f'{int(code):04d}', f'{int(code):05d}'}
        return qs.filter(broj__in=padded).first()
    return None


def _magacin_search_query(request):
    """Magacin pretraga je odvojena od sajt pretrage (`q` / search_query)."""
    return (
        request.GET.get('pretraga')
        or request.POST.get('pretraga')
        or request.GET.get('q')
        or request.POST.get('q')
        or ''
    ).strip()


_MAGACIN_NAV_CACHE_KEY = 'mg_nav_counts_v1'


def invalidate_magacin_nav_counts():
    from django.core.cache import cache

    cache.delete(_MAGACIN_NAV_CACHE_KEY)


def _magacin_nav_counts():
    import sys
    from django.core.cache import cache

    use_cache = 'test' not in sys.argv
    if use_cache:
        data = cache.get(_MAGACIN_NAV_CACHE_KEY)
        if data is not None:
            return data
    locked = pending_mp_brojevi(
        collect_mp_checks(list(_unvalidated_orders_qs().prefetch_related('stavke', 'magacin_holds')))
    )
    pack_qs = _unvalidated_orders_qs()
    if locked:
        pack_qs = pack_qs.exclude(broj__in=locked)
    data = {
        'new_magacin_orders_count': Order.objects.filter(status=Order.Status.NOVA).exclude(_prenos_mp_q()).count(),
        'new_pack_orders_count': pack_qs.count(),
        'notify_count': StaffSiteEvent.objects.filter(
            kreirano__gte=timezone.now() - timedelta(hours=24),
        ).count(),
    }
    if use_cache:
        cache.set(_MAGACIN_NAV_CACHE_KEY, data, 20)
    return data


def _magacin_context(request, *, section='artikli', page_title='Magacin', hide_top_search=False):
    sync = last_sync()
    site_settings = SiteSettings.load()
    counts = _magacin_nav_counts()
    return {
        **_base_context(),
        'site_settings': site_settings,
        'magacin_section': section,
        'page_title': page_title,
        'hide_top_search': hide_top_search,
        'last_sync': sync,
        'odoo_configured': odoo_je_konfigurisan(),
        'notify_count': counts['notify_count'],
        'staff_display_name': _user_display(request.user),
        'staff_role': 'Admin' if request.user.is_superuser else 'Staff',
        'search_query': '',
        'magacin_search': _magacin_search_query(request),
        'include_zero': (request.GET.get('bez_zalihe') or '') == '1',
        'sync_job': _sync_job_view(
            request.session.get(MAGACIN_SYNC_SESSION_KEY) or load_running_sync_job()
        ),
        'last_backup': last_backup(),
        'new_magacin_orders_count': counts['new_magacin_orders_count'],
        'new_pack_orders_count': counts['new_pack_orders_count'],
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
    text = str(raw or '').strip().replace('KM', '').replace('km', '').replace(' ', '').replace(',', '.')
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
def magacin_brzi_unos(request):
    """Korak 1: sken / šifra / barkod / naziv → pronađi postojeći artikal."""
    from .quick_activation import find_products, find_single_product, normalize_scan_code

    query = normalize_scan_code(request.GET.get('q') or request.POST.get('q') or '')
    matches = []
    not_found = False

    if request.method == 'POST' or query:
        if not query:
            messages.warning(request, 'Unesi šifru, barkod ili naziv — ili skeniraj barkod.')
        else:
            product, multi = find_single_product(query)
            if product is not None:
                return redirect('staff_magacin_brzi_unos_aktivacija', product_id=product.pk)
            matches = multi if multi is not None else find_products(query)
            if not matches:
                not_found = True
                messages.error(
                    request,
                    f'Nijedan artikal nije pronađen za „{query}”. '
                    'Traži po šifri, barkodu ili nazivu, ili dodaj novi artikal.',
                )
            elif len(matches) == 1:
                return redirect('staff_magacin_brzi_unos_aktivacija', product_id=matches[0].pk)

    context = _magacin_context(request, section='brzi_unos', page_title='Brzi unos / Aktivacija — Magacin')
    context.update({
        'query': query,
        'matches': matches,
        'not_found': not_found,
    })
    return render(request, 'staff/magacin/brzi_unos.html', context)


@login_required(login_url='login')
@user_passes_test(_superuser_required)
def magacin_brzi_unos_novi(request):
    """Novi artikal iz Brzog unosa. Obavezni su samo naziv i cijena."""
    from .quick_activation import category_choices, create_and_activate_product, parse_price

    brands = Brand.objects.order_by('naziv')
    categories = category_choices()
    query = (request.GET.get('q') or request.POST.get('q') or '').strip()
    looks_like_sifra = bool(re.fullmatch(r'[A-Za-z0-9._/-]{2,}', query or ''))
    form_errors = []
    form_data = {
        'naziv': '' if looks_like_sifra else query,
        'sifra': query if looks_like_sifra else '',
        'cijena': '',
        'brend_id': '',
        'kategorija_id': '',
    }

    if request.method == 'POST':
        form_data['naziv'] = (request.POST.get('naziv') or '').strip()
        form_data['sifra'] = (request.POST.get('sifra') or '').strip()
        form_data['cijena'] = (request.POST.get('cijena') or '').strip()
        form_data['brend_id'] = (request.POST.get('brend_id') or '').strip()
        form_data['kategorija_id'] = (request.POST.get('kategorija_id') or '').strip()

        naziv = form_data['naziv']
        if not naziv:
            form_errors.append('Naziv je obavezan.')

        try:
            cijena = parse_price(form_data['cijena'])
        except (InvalidOperation, ValueError):
            form_errors.append('Unesi ispravnu cijenu (npr. 12.90).')
            cijena = None

        brend = None
        if form_data['brend_id']:
            brend = brands.filter(pk=form_data['brend_id']).first()
            if brend is None:
                form_errors.append('Odabrani brend ne postoji.')

        kategorija = None
        if form_data['kategorija_id']:
            try:
                kategorija = Category.objects.filter(
                    pk=int(form_data['kategorija_id']),
                    aktivan=True,
                ).first()
            except (TypeError, ValueError):
                kategorija = None
            if kategorija is None:
                form_errors.append('Odabrana kategorija ne postoji.')

        if not form_errors and cijena is not None:
            try:
                product = create_and_activate_product(
                    naziv=naziv,
                    cijena=cijena,
                    sifra=form_data['sifra'],
                    brend=brend,
                    kategorija=kategorija,
                )
                extra = []
                if product.sifra:
                    extra.append(product.sifra)
                if brend:
                    extra.append(brend.naziv)
                if kategorija:
                    extra.append(kategorija.naziv)
                note = f' ({", ".join(extra)})' if extra else ''
                product.refresh_from_db(fields=['na_stanju'])
                if product.na_stanju:
                    site_note = 'na sajtu'
                else:
                    site_note = 'sačuvan — na sajt ide kad dobije zalihu na lokaciji'
                messages.success(
                    request,
                    f'✓ Novi artikal „{product.naziv}” je {site_note} ({cijena} KM){note}.',
                )
                return redirect('staff_magacin_brzi_unos')
            except ValueError as exc:
                form_errors.append(str(exc))
            except Exception as exc:
                logger.exception('Magacin brzi unos: kreiranje novog artikla nije uspjelo')
                form_errors.append(f'Artikal nije snimljen: {exc}')

    context = _magacin_context(
        request, section='brzi_unos', page_title='Novi artikal — Brzi unos',
        hide_top_search=True,
    )
    context.update({
        'brands': brands,
        'categories': categories,
        'categories_json': categories,
        'form_data': form_data,
        'form_errors': form_errors,
        'scan_url': reverse('staff_magacin_brzi_unos'),
    })
    return render(request, 'staff/magacin/brzi_unos_novi.html', context)


@login_required(login_url='login')
@user_passes_test(_superuser_required)
def magacin_brzi_unos_aktivacija(request, product_id):
    """Korak 2: cijena, brend, slika, AI opis → Aktiviraj artikal."""
    from urllib.parse import quote_plus

    from .quick_activation import (
        activate_product,
        category_choices,
        parse_price,
        resolve_tags,
        take_off_stock,
    )

    product = (
        Product.objects.select_related('brend', 'kategorija')
        .prefetch_related('tagovi')
        .filter(pk=product_id)
        .first()
    )
    if product is None:
        messages.error(request, 'Artikal nije pronađen.')
        return redirect('staff_magacin_brzi_unos')

    post_action = (request.POST.get('action') or '').strip() if request.method == 'POST' else ''
    if request.method == 'POST' and post_action == 'off_stock':
        try:
            take_off_stock(product)
            messages.success(
                request,
                f'✓ „{product.naziv}” — nije na stanju (sakriven od kupaca).',
            )
        except Exception as exc:
            logger.exception(
                'Magacin brzi unos: skidanje sa stanja nije uspjelo product_id=%s',
                product_id,
            )
            messages.error(request, f'Skidanje sa stanja nije uspjelo: {exc}')
        return redirect('staff_magacin_brzi_unos')

    brands = Brand.objects.order_by('naziv')
    categories = category_choices()
    from django.conf import settings as django_settings

    olx_configured = bool(getattr(django_settings, 'OLX_API_TOKEN', None))
    form_errors = []
    pack_n = product.pakovanje_komada or 0
    form_data = {
        'cijena': str(product.cijena) if product.cijena is not None else '',
        'brend_id': str(product.brend_id or ''),
        'kategorija_id': str(product.kategorija_id or ''),
        'opis': (product.opis or '').strip(),
        'tagovi': '',
        'barkod': (product.barkod or '').strip(),
        'je_pakovanje': '1' if pack_n and pack_n > 1 else '',
        'pakovanje_komada': str(pack_n) if pack_n and pack_n > 1 else '',
        'proizvedeno_u_japanu': '1' if product.proizvedeno_u_japanu else '',
        'objavi_olx': '',
    }

    if request.method == 'POST' and post_action == 'activate':
        form_data['cijena'] = (request.POST.get('cijena') or '').strip()
        form_data['brend_id'] = (request.POST.get('brend_id') or '').strip()
        form_data['kategorija_id'] = (request.POST.get('kategorija_id') or '').strip()
        form_data['opis'] = (request.POST.get('opis') or '').strip()
        form_data['tagovi'] = (request.POST.get('tagovi') or '').strip()
        form_data['barkod'] = (request.POST.get('barkod') or '').strip()
        form_data['je_pakovanje'] = (
            '1'
            if (request.POST.get('je_pakovanje') or '').strip()
            in ('1', 'true', 'on', 'yes')
            else ''
        )
        form_data['pakovanje_komada'] = (request.POST.get('pakovanje_komada') or '').strip()
        form_data['proizvedeno_u_japanu'] = (
            '1'
            if (request.POST.get('proizvedeno_u_japanu') or '').strip()
            in ('1', 'true', 'on', 'yes')
            else ''
        )
        form_data['objavi_olx'] = (
            '1'
            if (request.POST.get('objavi_olx') or '').strip()
            in ('1', 'true', 'on', 'yes')
            else ''
        )
        image_upload = request.FILES.get('slika') or request.FILES.get('slika_kamera')
        keep_image = request.POST.get('keep_existing_image') == '1'
        extra_images = request.FILES.getlist('dodatne_slike')

        brend = None
        if form_data['brend_id']:
            brend = brands.filter(pk=form_data['brend_id']).first()

        kategorija = None
        if form_data['kategorija_id']:
            try:
                kategorija = Category.objects.filter(
                    pk=int(form_data['kategorija_id']),
                    aktivan=True,
                ).first()
            except (TypeError, ValueError):
                kategorija = None

        try:
            cijena = parse_price(form_data['cijena'])
        except (InvalidOperation, ValueError):
            form_errors.append('Unesi ispravnu cijenu (npr. 12.90).')
            cijena = None

        if form_data['brend_id']:
            if brend is None:
                form_errors.append('Odabrani brend ne postoji.')
        else:
            form_errors.append('Izaberi brend.')

        if form_data['kategorija_id']:
            if kategorija is None:
                form_errors.append('Odabrana kategorija ne postoji.')
        else:
            form_errors.append('Izaberi kategoriju.')

        if not image_upload and not (product.slika and product.slika.name):
            form_errors.append('Dodaj sliku artikla (galerija ili kamera).')
        elif not image_upload and product.slika and product.slika.name:
            keep_image = True

        pack_value = None
        if form_data['je_pakovanje']:
            raw_pack = form_data['pakovanje_komada']
            try:
                pack_value = int(raw_pack) if raw_pack else 0
            except (TypeError, ValueError):
                pack_value = 0
                form_errors.append('Pakovanje: unesi cijeli broj komada (npr. 9).')
            if pack_value and pack_value <= 1:
                form_errors.append('Pakovanje: količina mora biti najmanje 2 komada.')
                pack_value = None

        if not form_errors and cijena is not None:
            try:
                tagovi = resolve_tags(form_data['tagovi'])
                activate_product(
                    product,
                    cijena=cijena,
                    brend=brend,
                    kategorija=kategorija,
                    image_upload=image_upload,
                    keep_existing_image=keep_image and not image_upload,
                    opis=form_data['opis'],
                    tagovi=tagovi,
                    barkod=form_data['barkod'],
                    extra_images=extra_images,
                    set_pakovanje=True,
                    pakovanje_komada=pack_value if form_data['je_pakovanje'] else None,
                    proizvedeno_u_japanu=bool(form_data['proizvedeno_u_japanu']),
                    image_manual_fit=(request.POST.get('slika_rucno') or '') == '1',
                )
                tag_note = f', {len(tagovi)} tag(ova)' if tagovi else ''
                cat_note = f', {kategorija.naziv}' if kategorija else ''
                extra_note = f', +{len(extra_images)} slika' if extra_images else ''
                pack_note = ''
                if form_data['je_pakovanje'] and pack_value and pack_value > 1:
                    pack_note = f', pakovanje {pack_value} kom.'
                japan_note = ', Made in Japan' if form_data['proizvedeno_u_japanu'] else ''
                messages.success(
                    request,
                    f'✓ „{product.naziv}” je aktivan na webshopu '
                    f'({cijena} KM'
                    f'{f", {brend.naziv}" if brend else ""}'
                    f'{cat_note}'
                    f'{tag_note}'
                    f'{extra_note}'
                    f'{pack_note}'
                    f'{japan_note}'
                    f', na stanju).',
                )

                if form_data['objavi_olx']:
                    if not olx_configured:
                        messages.warning(
                            request,
                            'OLX nije konfigurisan (OLX_API_TOKEN) — artikal je aktivan, ali nije objavljen.',
                        )
                    else:
                        try:
                            from django.utils import timezone as dj_tz

                            from .olx_api import OlxApiError, publish_product_to_olx

                            olx_result = publish_product_to_olx(product)
                            product.olx_listing_id = olx_result['id']
                            product.olx_listing_slug = olx_result.get('slug', '') or ''
                            product.olx_listing_url = olx_result.get('url', '') or ''
                            product.olx_objavljen = dj_tz.now()
                            product.save(
                                update_fields=[
                                    'olx_listing_id',
                                    'olx_listing_slug',
                                    'olx_listing_url',
                                    'olx_objavljen',
                                ]
                            )
                            olx_url = olx_result.get('url') or ''
                            if olx_result.get('status') == 'active':
                                messages.success(
                                    request,
                                    f'Objavljeno na OLX/Pik. {olx_url}'.strip(),
                                )
                            else:
                                messages.warning(
                                    request,
                                    'OLX oglas poslan, ali nije aktivan — provjeri Neaktivne u Pik/OLX. '
                                    f'{olx_url}'.strip(),
                                )
                        except OlxApiError as olx_exc:
                            messages.error(
                                request,
                                f'Artikal je aktivan, ali OLX objava nije uspjela: {olx_exc}',
                            )
                            logger.warning('Magacin brzi unos OLX %s: %s', product.slug, olx_exc)
                        except Exception as olx_exc:
                            logger.exception(
                                'Magacin brzi unos OLX neočekivano product_id=%s',
                                product_id,
                            )
                            messages.error(
                                request,
                                f'Artikal je aktivan, ali OLX objava nije uspjela: {olx_exc}',
                            )

                return redirect('staff_magacin_brzi_unos')
            except Exception as exc:
                logger.exception(
                    'Magacin brzi unos: aktivacija nije uspjela za product_id=%s',
                    product_id,
                )
                form_errors.append(f'Aktivacija nije uspjela: {exc}')

    product.refresh_from_db()
    current_image_url = ''
    if product.slika and product.slika.name:
        try:
            current_image_url = product.slika.url
        except Exception:
            current_image_url = ''

    existing_extra = []
    for img in product.dodatne_slike.all().order_by('redoslijed', 'id')[:12]:
        try:
            url = img.slika.url if img.slika else ''
        except Exception:
            url = ''
        if url:
            existing_extra.append({'id': img.pk, 'url': url})

    google_query = (product.naziv or '').strip()
    google_images_url = (
        'https://www.google.com/search?tbm=isch&q=' + quote_plus(google_query)
        if google_query else ''
    )
    chatgpt_url = ''
    if google_query:
        chatgpt_prompt = f'{google_query} veci opis za ovaj artikal i tagove'
        chatgpt_url = 'https://chatgpt.com/?q=' + quote_plus(chatgpt_prompt)

    context = _magacin_context(request, section='brzi_unos', page_title=f'Aktivacija: {product.naziv} — Magacin')
    context.update({
        'product': product,
        'brands': brands,
        'categories': categories,
        'categories_json': categories,
        'form_data': form_data,
        'form_errors': form_errors,
        'current_image_url': current_image_url,
        'existing_extra_images': existing_extra,
        'google_images_url': google_images_url,
        'google_query': google_query,
        'chatgpt_url': chatgpt_url,
        'olx_configured': olx_configured,
        'scan_url': reverse('staff_magacin_brzi_unos'),
    })
    return render(request, 'staff/magacin/brzi_unos_aktivacija.html', context)


@login_required(login_url='login')
@user_passes_test(_superuser_required)
def magacin_artikli(request):
    _ensure_magacin_locations()
    query = _magacin_search_query(request)
    include_zero = (request.GET.get('bez_zalihe') or '') == '1'
    searched = bool(query)
    recent_movements = list(
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
    for movement in recent_movements:
        movement.korisnik_label = _movement_korisnik_label(movement)
    context = _magacin_context(request, section='artikli', page_title='Artikli — Magacin')
    context.update({
        'searched': searched,
        'recent_movements': recent_movements,
        'page': None,
        'result_count': 0,
        'magacin_notice': request.session.pop('magacin_page_notice', '') or '',
    })
    if not searched:
        return render(request, 'staff/magacin/artikli.html', context)

    products, exact = search_products(query, limit=None, include_zero=include_zero)
    if not exact and not include_zero:
        zero_products, zero_exact = search_products(query, limit=None, include_zero=True)
        if zero_exact or zero_products:
            include_zero = True
            products, exact = zero_products, zero_exact
            context['include_zero'] = True
    if exact:
        url = reverse('staff_magacin_artikal', args=[exact.pk])
        params = {'pretraga': query}
        if include_zero:
            params['bez_zalihe'] = '1'
        return redirect(f'{url}?{urlencode(params)}')
    qs = products

    paginator = Paginator(qs, 40)
    page = paginator.get_page(request.GET.get('page') or 1)
    product_ids = [item.pk for item in page.object_list]
    totals_by_product = {}
    if product_ids:
        for row in (
            recorded_stock_qs(WarehouseStock.objects.filter(product_id__in=product_ids))
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
        rows.append({'product': product, **totals, 'locations': []})
    locs_by_product = {}
    if product_ids:
        for stock in (
            recorded_stock_qs(WarehouseStock.objects.filter(product_id__in=product_ids, kolicina__gt=0))
            .select_related('location')
            .order_by('location__sifra')
        ):
            dostupno = max(0, int(stock.kolicina or 0) - max(0, int(stock.rezervisano or 0)))
            if dostupno <= 0:
                continue
            locs_by_product.setdefault(stock.product_id, []).append({
                'sifra': stock.location.sifra,
                'naziv': stock.location.naziv,
                'dostupno': dostupno,
            })
    for row in rows:
        row['locations'] = locs_by_product.get(row['product'].pk) or []
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
                stocked_rows = location_rows(product, variation)[0]
                mp_stocked = maloprodaja_location_rows(product, variation)
                stocked_ids = {row['location'].pk for row in stocked_rows} | {
                    row['location'].pk for row in mp_stocked
                }
                if mode == 'mp':
                    loc_id = int(request.POST.get('location_id') or 0)
                    location = next(
                        (row['location'] for row in stocked_rows if row['location'].pk == loc_id),
                        None,
                    )
                    if location is None and stocked_rows:
                        location = stocked_rows[0]['location']
                    if location is None:
                        raise MagacinError('Nema zalihe za prenos u MP.')
                    order = create_prenos_mp_pick(
                        product=product,
                        variation=variation,
                        location=location,
                        qty=_parse_qty(request.POST.get('kolicina') or '1'),
                        user=request.user,
                    )
                    stavki = order.stavke.count()
                    qty = request.POST.get('kolicina')
                    if stavki == 1:
                        qty = order.stavke.first().kolicina
                    messages.success(
                        request,
                        f'Prenos u MP ({qty} kom) je na Pickingu #{order.broj}'
                        f' ({stavki} stavk{"a" if stavki == 1 else "i"}). '
                        'Otvori Picking pa pokupi sve stavke.',
                    )
                else:
                    loc_raw = request.POST.get('add_location_id') if mode == 'add' else request.POST.get('location_id')
                    loc_id = int(loc_raw or 0)
                    location = WarehouseLocation.objects.get(pk=loc_id)
                    if is_ignored_stock_location(location):
                        raise MagacinError('Lokacija Prenos u MP se ne evidentira.')
                    if mode == 'transfer':
                        to_id = int(request.POST.get('to_location_id') or request.POST.get('add_location_id') or 0)
                        to_location = WarehouseLocation.objects.get(pk=to_id)
                        if is_ignored_stock_location(to_location):
                            raise MagacinError('Lokacija Prenos u MP se ne evidentira.')
                        if loc_id not in stocked_ids:
                            raise MagacinError('Odaberi lokaciju s koje prebacuješ.')
                        apply_movement(
                            product=product,
                            variation=variation,
                            location=location,
                            to_location=to_location,
                            tip='transfer',
                            kolicina=_parse_qty(request.POST.get('kolicina')),
                            napomena=request.POST.get('napomena') or 'Transfer',
                            user=request.user,
                        )
                        messages.success(
                            request,
                            f'Prebačeno na {to_location.label}.',
                        )
                    elif mode == 'add':
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
                    elif mode == 'remove':
                        if loc_id not in stocked_ids:
                            raise MagacinError('Odaberi postojeću lokaciju ovog artikla.')
                        qty = _parse_qty(request.POST.get('kolicina') or '0')
                        if qty <= 0:
                            raise MagacinError('Unesi količinu koju skidaš s lokacije.')
                        apply_movement(
                            product=product,
                            variation=variation,
                            location=location,
                            tip='prodaja',
                            kolicina=qty,
                            napomena=request.POST.get('napomena') or f'Skini sa {location.label}',
                            user=request.user,
                        )
                        messages.success(request, f'Skinuto {qty} kom s {location.label}.')
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
            elif action == 'skini':
                skini_sa_sajta(product, user=request.user)
                messages.success(request, 'Artikal je skinut sa stanja i sa sajta.')
            elif action == 'ubaci':
                ubaci_na_sajt(product)
                product.refresh_from_db()
                if product.na_stanju:
                    messages.success(request, 'Artikal je na sajtu (na stanju).')
                else:
                    messages.warning(
                        request,
                        'Artikal nije na sajtu jer je UKUPNO NA STANJU 0. Ubaci količinu na lokaciju.',
                    )
            elif action == 'obrisi':
                naziv = product.naziv or ''
                sifra = product.sifra or ''
                obrisi_artikal_iz_baze(product)
                request.session['magacin_page_notice'] = (
                    f'Artikal „{naziv}”'
                    + (f' ({sifra})' if sifra else '')
                    + ' je obrisan iz baze.'
                )
                request.session.modified = True
                dest = reverse('staff_magacin_artikli')
                q = _magacin_search_query(request)
                if q:
                    dest = f'{dest}?{urlencode({"pretraga": q})}'
                return redirect(dest)
            elif action == 'meta':
                _save_product_meta(request, product)
                messages.success(request, 'Osnovne informacije su sačuvane.')
            else:
                raise MagacinError('Nepoznata akcija.')
        except (MagacinError, WarehouseLocation.DoesNotExist, ValueError) as exc:
            text = str(exc) if str(exc) else 'Greška pri spremanju.'
            messages.error(request, text)
            request.session['magacin_page_error'] = text
            request.session.modified = True
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
    rows, _ = location_rows(product, variation, locations=locations)
    mp_rows = maloprodaja_location_rows(product, variation)
    totals = display_stock_totals(product, variation)
    stocked_ids = {row['location'].pk for row in rows} | {row['location'].pk for row in mp_rows}
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
    _attach_movement_kupci(movements)

    variant_rows = []
    for var in variations:
        v_totals = display_stock_totals(product, var)
        variant_rows.append({
            'variation': var,
            'na_stanju': v_totals['na_stanju'],
            'cijena': var.prikazna_cijena,
        })

    price_history, price_chart = _product_uvoz_price_history(product)
    vpc, mpc = vp_cijena(product, variation)

    context = _magacin_context(request, section='artikli', page_title=f'{product.naziv} — Magacin', hide_top_search=True)
    context.update({
        'product': product,
        'meta': meta,
        'mpc': mpc,
        'vpc': vpc,
        'tags': tags,
        'location_rows': rows,
        'mp_location_rows': mp_rows,
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
        'price_history': price_history,
        'price_chart': price_chart,
        'artikal_error': request.session.pop('magacin_page_error', '') or '',
    })
    return render(request, 'staff/magacin/artikal.html', context)


ETIKETA_A4_COLS = 4
ETIKETA_A4_ROWS = 7
ETIKETA_A4_COUNT = ETIKETA_A4_COLS * ETIKETA_A4_ROWS


def _etiketa_barcode_data_uri(code):
    raw = (code or '').strip()
    if not raw:
        return ''
    import io as _io
    from barcode import Code128
    from barcode.writer import ImageWriter
    from PIL import Image

    try:
        buffer = _io.BytesIO()
        Code128(str(raw), writer=ImageWriter()).write(
            buffer,
            options={
                'module_width': 0.38,
                'module_height': 22.0,
                'quiet_zone': 1.2,
                'font_size': 0,
                'text_distance': 1,
                'write_text': False,
                'background': 'white',
                'foreground': 'black',
            },
        )
        buffer.seek(0)
        img = Image.open(buffer).convert('RGB')
        out = _io.BytesIO()
        img.save(out, format='PNG', optimize=True)
        png = out.getvalue()
    except Exception:
        logger.exception('Barkod etikete nije generisan: %s', raw)
        return ''
    return 'data:image/png;base64,' + base64.b64encode(png).decode('ascii')


def _artikal_etiketa_payload(product, variation=None):
    naziv = (product.naziv or '').strip()
    if variation:
        var_name = (variation.naziv or '').strip()
        if var_name:
            naziv = f'{naziv} — {var_name}' if naziv else var_name
    sifra = ''
    if variation:
        sifra = (variation.sifra or '').strip()
    if not sifra:
        sifra = (product.sifra or '').strip()
    cijena = variation.prikazna_cijena if variation else product.prikazna_cijena
    cijena_label = ''
    if cijena is not None:
        cijena = Decimal(cijena).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        cijena_label = format(cijena, '.2f').replace('.', ',')
    barkod = (product.barkod or '').strip() or sifra
    return {
        'naziv': naziv,
        'sifra': sifra,
        'cijena': cijena,
        'cijena_label': cijena_label,
        'barkod': barkod,
        'barcode_src': _etiketa_barcode_data_uri(barkod),
    }


def _etiketa_sheets(items):
    pages = []
    for i in range(0, len(items), ETIKETA_A4_COUNT):
        pages.append(items[i:i + ETIKETA_A4_COUNT])
    return pages


def _etiketa_copy_count(raw, default=1):
    try:
        n = int(str(raw or '').strip() or default)
    except (TypeError, ValueError):
        n = default
    return max(1, min(n, ETIKETA_A4_COUNT * 10))


def _papir_kind(raw):
    value = (raw or 'a4').strip().casefold()
    if value in ('zebra', 'zebru', 'z'):
        return 'zebra'
    return 'a4'


def _zpl_text(value, max_len=40):
    return re.sub(r'[\^~\\]', '', (value or '').strip())[:max_len]


def _zebra_price_zpl(payload):
    naziv = _zpl_text(payload.get('naziv'), 60)
    sifra = _zpl_text(payload.get('sifra'), 24) or '-'
    barkod = _zpl_text(payload.get('barkod'), 40)
    cijena = _zpl_text(payload.get('cijena_label'), 12) or '-'
    width = int((ZEBRA_BARCODE_WIDTH_IN * ZEBRA_BARCODE_DPI).quantize(Decimal('1')))
    height = int((ZEBRA_BARCODE_HEIGHT_IN * ZEBRA_BARCODE_DPI).quantize(Decimal('1')))
    top = int((ZEBRA_BARCODE_TOP_IN * ZEBRA_BARCODE_DPI).quantize(Decimal('1')))
    left = 42
    text_w = max(200, width - left - 24)
    lines = [
        '^XA',
        '^MNY',
        f'^PW{width}',
        f'^LL{height}',
        f'^LT{top}',
        '^LH0,0',
        '^PON',
        '^FWN',
        f'^FO{left},2^A0N,22,22^FB{text_w},2,0,L^FD{naziv}^FS',
        f'^FO{left},50^A0N,20,20^FDSIFRA: {sifra}^FS',
    ]
    if barkod:
        lines.append(f'^FO{left},74^BY2,2.0,56^BCN,56,N,N,N^FD{barkod}^FS')
        lines.append(f'^FO{left},136^A0N,18,18^FD{barkod}^FS')
    lines.append(f'^FO{left + 380},148^A0N,44,44^FD{cijena}^FS')
    lines.append(f'^FO{left + 580},166^A0N,24,24^FDKM^FS')
    lines.append('^XZ')
    return '\n'.join(lines) + '\n'


def _etiketa_resolve(product_id, variation_id=None):
    try:
        pk = int(str(product_id or '').strip())
    except (TypeError, ValueError):
        return None, None
    product = magacin_products_qs().filter(pk=pk).prefetch_related('varijacije').first()
    if product is None:
        return None, None
    vid = str(variation_id or '').strip()
    if not vid:
        return product, None
    variation = next((row for row in product.varijacije.all() if str(row.pk) == vid), None)
    if variation is None:
        return None, None
    return product, variation


def _etiketa_resolve_token(raw):
    text = str(raw or '').strip()
    if not text:
        return None, None
    if ':' in text:
        pid, vid = text.split(':', 1)
        return _etiketa_resolve(pid, vid)
    return _etiketa_resolve(text, None)


def _stampa_cijena_context(request, *, mode='izbor'):
    context = _magacin_context(
        request,
        section='stampa_cijena',
        page_title='Štampaj cijenu — Magacin',
        hide_top_search=True,
    )
    context.update({
        'mode': mode,
        'lookup_url': reverse('staff_magacin_artikli_lookup'),
        'print_url': reverse('staff_magacin_stampa_cijena_print'),
    })
    return context


def _render_etiketa_print(request, items, papir='a4'):
    if not items:
        messages.error(request, 'Nema artikala za štampu.')
        return redirect('staff_magacin_stampa_cijena')
    kind = _papir_kind(papir)
    if kind == 'zebra':
        return render(request, 'staff/magacin/artikal_etiketa_zebra.html', {
            'items': items,
            'etiketa_count': len(items),
            'zpl': ''.join(_zebra_price_zpl(row) for row in items),
            'label_width': str(ZEBRA_BARCODE_WIDTH_IN),
            'label_height': str(ZEBRA_BARCODE_HEIGHT_IN),
            'label_top': str(ZEBRA_BARCODE_TOP_IN),
        })
    return render(request, 'staff/magacin/artikal_etiketa.html', {
        'sheets': _etiketa_sheets(items),
        'etiketa_count': len(items),
    })


ZEBRA_BARCODE_WIDTH_IN = Decimal('3.559')
ZEBRA_BARCODE_HEIGHT_IN = Decimal('1.224')
ZEBRA_BARCODE_TOP_IN = Decimal('0.100')
ZEBRA_BARCODE_DPI = 203


def _artikal_barkod_value(product, variation=None):
    barkod = (getattr(product, 'barkod', None) or '').strip()
    if barkod:
        return barkod
    if variation:
        sifra = (variation.sifra or '').strip()
        if sifra:
            return sifra
    return (product.sifra or '').strip()


def _zebra_barcode_zpl(code):
    raw = (code or '').strip()
    if not raw:
        return ''
    safe = re.sub(r'[\^~\\]', '', raw)[:40]
    width = int((ZEBRA_BARCODE_WIDTH_IN * ZEBRA_BARCODE_DPI).quantize(Decimal('1')))
    height = int((ZEBRA_BARCODE_HEIGHT_IN * ZEBRA_BARCODE_DPI).quantize(Decimal('1')))
    top = int((ZEBRA_BARCODE_TOP_IN * ZEBRA_BARCODE_DPI).quantize(Decimal('1')))
    bar_h = max(80, height - top - 50)
    return (
        '^XA\n'
        '^MNY\n'
        f'^PW{width}\n'
        f'^LL{height}\n'
        f'^LT{top}\n'
        '^LH0,0\n'
        '^PON\n'
        '^FWN\n'
        f'^FO28,4^BY2,2.4,{bar_h}^BCN,{bar_h},Y,N,N^FD{safe}^FS\n'
        '^XZ\n'
    )


@login_required(login_url='login')
@user_passes_test(_superuser_required)
@require_GET
def magacin_artikal_stampa_barkod(request, pk):
    product = get_object_or_404(magacin_products_qs(), pk=pk)
    variations = list(product.varijacije.all())
    variation = None
    variation_id = (request.GET.get('varijacija') or '').strip()
    if variation_id:
        variation = next((row for row in variations if str(row.pk) == variation_id), None)
        if variation is None:
            messages.error(request, 'Varijacija nije pronađena.')
            return redirect('staff_magacin_artikal', pk=product.pk)
    barkod = _artikal_barkod_value(product, variation)
    return render(request, 'staff/magacin/artikal_barkod_zebra.html', {
        'product': product,
        'variation': variation,
        'barkod': barkod,
        'barcode_src': _etiketa_barcode_data_uri(barkod) if barkod else '',
        'zpl': _zebra_barcode_zpl(barkod) if barkod else '',
        'label_width': str(ZEBRA_BARCODE_WIDTH_IN),
        'label_height': str(ZEBRA_BARCODE_HEIGHT_IN),
        'label_top': str(ZEBRA_BARCODE_TOP_IN),
    })


@login_required(login_url='login')
@user_passes_test(_superuser_required)
@require_GET
def magacin_artikal_stampa(request, pk):
    product = get_object_or_404(magacin_products_qs(), pk=pk)
    variations = list(product.varijacije.all())
    variation = None
    variation_id = (request.GET.get('varijacija') or '').strip()
    if variation_id:
        variation = next((row for row in variations if str(row.pk) == variation_id), None)
        if variation is None:
            messages.error(request, 'Varijacija nije pronađena.')
            return redirect('staff_magacin_artikal', pk=product.pk)
    payload = _artikal_etiketa_payload(product, variation)
    n = _etiketa_copy_count(request.GET.get('n'), default=ETIKETA_A4_COUNT)
    return _render_etiketa_print(request, [payload] * n)


@login_required(login_url='login')
@user_passes_test(_superuser_required)
@require_GET
def magacin_stampa_cijena(request):
    return render(request, 'staff/magacin/stampa_cijena.html', _stampa_cijena_context(request, mode='izbor'))


@login_required(login_url='login')
@user_passes_test(_superuser_required)
@require_GET
def magacin_stampa_cijena_ista(request):
    context = _stampa_cijena_context(request, mode='ista')
    product, variation = _etiketa_resolve(request.GET.get('artikal'), request.GET.get('varijacija'))
    picked = None
    if product:
        payload = _artikal_etiketa_payload(product, variation)
        picked = {
            'product_id': product.pk,
            'variation_id': variation.pk if variation else '',
            'naziv': payload['naziv'],
            'sifra': payload['sifra'],
            'cijena_label': payload['cijena_label'],
        }
    context['picked'] = picked
    return render(request, 'staff/magacin/stampa_cijena.html', context)


@login_required(login_url='login')
@user_passes_test(_superuser_required)
@require_GET
def magacin_stampa_cijena_razlicite(request):
    return render(request, 'staff/magacin/stampa_cijena.html', _stampa_cijena_context(request, mode='razlicite'))


@login_required(login_url='login')
@user_passes_test(_superuser_required)
def magacin_stampa_cijena_print(request):
    data = request.POST if request.method == 'POST' else request.GET
    mod = (data.get('mod') or 'ista').strip()
    if mod == 'razlicite':
        items = []
        seen = set()
        for raw in data.getlist('stavka'):
            product, variation = _etiketa_resolve_token(raw)
            if product is None:
                continue
            key = (product.pk, variation.pk if variation else 0)
            if key in seen:
                continue
            seen.add(key)
            items.append(_artikal_etiketa_payload(product, variation))
        if not items:
            messages.error(request, 'Unesi barem jedan artikal.')
            return redirect('staff_magacin_stampa_cijena_razlicite')
        return _render_etiketa_print(request, items, papir=data.get('papir'))

    product, variation = _etiketa_resolve(data.get('artikal'), data.get('varijacija'))
    if product is None:
        messages.error(request, 'Izaberi artikal.')
        return redirect('staff_magacin_stampa_cijena_ista')
    raw_n = data.get('n')
    if raw_n is None or str(raw_n).strip() == '':
        messages.error(request, 'Unesi koliko cijena da odstampa.')
        url = reverse('staff_magacin_stampa_cijena_ista')
        params = {'artikal': product.pk}
        if variation:
            params['varijacija'] = variation.pk
        return redirect(f'{url}?{urlencode(params)}')
    n = _etiketa_copy_count(raw_n)
    payload = _artikal_etiketa_payload(product, variation)
    return _render_etiketa_print(request, [payload] * n, papir=data.get('papir'))


DEKLARACIJA_A4_COLS = 5
DEKLARACIJA_A4_ROWS = 13
DEKLARACIJA_A4_COUNT = DEKLARACIJA_A4_COLS * DEKLARACIJA_A4_ROWS


def _deklaracija_copy_count(raw, default=DEKLARACIJA_A4_COUNT):
    try:
        n = int(str(raw or '').strip() or default)
    except (TypeError, ValueError):
        n = default
    return max(1, min(n, DEKLARACIJA_A4_COUNT * 10))


def _deklaracija_sheets(n):
    pages = []
    items = [True] * n
    for i in range(0, len(items), DEKLARACIJA_A4_COUNT):
        pages.append(items[i:i + DEKLARACIJA_A4_COUNT])
    return pages


def _deklaracija_fields_from_post(data):
    fields = {}
    for attr, _label in MagacinDeklaracijaBrend.POLJA:
        raw = (data.get(attr) or '').strip()
        field = MagacinDeklaracijaBrend._meta.get_field(attr)
        fields[attr] = raw[: field.max_length]
    return fields


@login_required(login_url='login')
@user_passes_test(_superuser_required)
def magacin_stampa_deklaracije(request):
    if request.method == 'POST':
        action = (request.POST.get('action') or 'save').strip()
        try:
            if action == 'delete':
                brend = get_object_or_404(
                    MagacinDeklaracijaBrend, pk=request.POST.get('brend_id'),
                )
                brend.delete()
                messages.success(request, 'Brend je obrisan.')
                return redirect('staff_magacin_stampa_deklaracije')
            fields = _deklaracija_fields_from_post(request.POST)
            if not fields['naziv']:
                raise MagacinError('Unesi naziv.')
            brend_id = (request.POST.get('brend_id') or '').strip()
            with transaction.atomic():
                if brend_id:
                    brend = get_object_or_404(MagacinDeklaracijaBrend, pk=brend_id)
                    for attr, value in fields.items():
                        setattr(brend, attr, value)
                    brend.save()
                else:
                    MagacinDeklaracijaBrend.objects.create(**fields)
            messages.success(request, 'Brend je sačuvan.')
        except MagacinError as exc:
            messages.error(request, str(exc))
        except IntegrityError:
            messages.error(request, 'Brend s tim nazivom već postoji.')
        except (TypeError, ValueError):
            messages.error(request, 'Brend nije sačuvan.')
        return redirect('staff_magacin_stampa_deklaracije')

    editing = None
    edit_id = (request.GET.get('id') or '').strip()
    if edit_id:
        editing = MagacinDeklaracijaBrend.objects.filter(pk=edit_id).first()
    context = _magacin_context(
        request,
        section='stampa_deklaracije',
        page_title='Štampaj deklaracije — Magacin',
        hide_top_search=True,
    )
    form_polja = []
    for attr, label in MagacinDeklaracijaBrend.POLJA:
        field = MagacinDeklaracijaBrend._meta.get_field(attr)
        form_polja.append({
            'attr': attr,
            'label': label,
            'value': (getattr(editing, attr) or '') if editing else '',
            'max_length': field.max_length,
            'required': attr == 'naziv',
        })
    context.update({
        'brendovi': list(MagacinDeklaracijaBrend.objects.all()),
        'editing': editing,
        'sheet_count': DEKLARACIJA_A4_COUNT,
        'form_polja': form_polja,
    })
    return render(request, 'staff/magacin/stampa_deklaracije.html', context)


@login_required(login_url='login')
@user_passes_test(_superuser_required)
@require_GET
def magacin_stampa_deklaracije_print(request, pk):
    brend = get_object_or_404(MagacinDeklaracijaBrend, pk=pk)
    n = _deklaracija_copy_count(request.GET.get('n'))
    return render(request, 'staff/magacin/deklaracija_etiketa.html', {
        'brend': brend,
        'deklaracija_redovi': brend.deklaracija_redovi(),
        'sheets': _deklaracija_sheets(n),
        'etiketa_count': n,
        'cols': DEKLARACIJA_A4_COLS,
        'rows': DEKLARACIJA_A4_ROWS,
    })


def _uvoz_marza_pct(stavka):
    pct = stavka.vpc_marza_pct
    if pct is not None:
        return pct
    vpc = stavka.vpc_netto
    mpc = stavka.mpc_brutto
    if vpc and mpc and vpc > 0:
        return ((mpc - vpc) / vpc * Decimal('100')).quantize(Decimal('0.01'))
    return None


def _fmt_km(value):
    if value is None:
        return '—'
    return f'{value} KM'


def _delta_float(new, old):
    if new is None or old is None:
        return None
    return float(new - old)


def _change_row(field, old_label, new_label, new, old):
    if new == old:
        return None
    if new is None or old is None:
        direction = 'down' if new is None else 'up'
    else:
        direction = 'up' if new > old else 'down'
    word = 'rast' if direction == 'up' else 'pad'
    return {
        'field': field,
        'from': old_label,
        'to': new_label,
        'direction': direction,
        'label': f'{field} {word}',
    }


def _product_uvoz_price_history(product):
    stavke = list(
        UvozStavka.objects.filter(
            Q(product=product) | Q(artikal_naziv=product.naziv)
        )
        .select_related('uvoz')
        .order_by('-uvoz__kreiran', '-id')[:50]
    )
    stavke.reverse()

    history = []
    prev = None
    for stavka in stavke:
        mpc = stavka.mpc_brutto
        vpc = stavka.vpc_netto
        nabavna = stavka.nabavna
        marza = _uvoz_marza_pct(stavka)
        changes = []
        delta_mpc = delta_vpc = delta_nabavna = delta_marza = None
        if prev is not None:
            delta_mpc = _delta_float(mpc, prev['mpc'])
            delta_vpc = _delta_float(vpc, prev['vpc'])
            delta_nabavna = _delta_float(nabavna, prev['nabavna'])
            delta_marza = _delta_float(marza, prev['marza'])
            for item in (
                _change_row('Mpc', _fmt_km(prev['mpc']), _fmt_km(mpc), mpc, prev['mpc']),
                _change_row('Vpc', _fmt_km(prev['vpc']), _fmt_km(vpc), vpc, prev['vpc']),
                _change_row('Nabavna', _fmt_km(prev['nabavna']), _fmt_km(nabavna), nabavna, prev['nabavna']),
                _change_row(
                    'Marža',
                    f'{prev["marza"]}%' if prev['marza'] is not None else '—',
                    f'{marza}%' if marza is not None else '—',
                    marza,
                    prev['marza'],
                ),
            ):
                if item:
                    changes.append(item)
        history.append({
            'stavka': stavka,
            'uvoz': stavka.uvoz,
            'mpc': mpc,
            'vpc': vpc,
            'nabavna': nabavna,
            'marza': marza,
            'delta_mpc': delta_mpc,
            'delta_vpc': delta_vpc,
            'delta_nabavna': delta_nabavna,
            'delta_marza': delta_marza,
            'changes': changes,
            'is_first': prev is None,
        })
        prev = {'mpc': mpc, 'vpc': vpc, 'nabavna': nabavna, 'marza': marza}

    compare = [row for row in history if not row['is_first']]
    chart = {
        'labels': [
            timezone.localtime(row['uvoz'].kreiran).strftime('%d.%m.%Y')
            for row in compare
        ],
        'mpc': [row['delta_mpc'] for row in compare],
        'vpc': [row['delta_vpc'] for row in compare],
        'nabavna': [row['delta_nabavna'] for row in compare],
        'marza': [row['delta_marza'] for row in compare],
        'has_compare': bool(compare),
    }
    return list(reversed(history)), chart


def _nivelacija_kljuc(product=None, naziv=''):
    if product is not None:
        return f'p:{product.pk}'
    return f'n:{(naziv or "").strip().casefold()}'


def _nivelacije_rows(query=''):
    """Artikli kojima se Mpc ili Vpc promijenio između uvoza (ista evidencija kao na artiklu)."""
    stavke = list(
        UvozStavka.objects.select_related('uvoz', 'product')
        .order_by('uvoz__kreiran', 'id')
    )
    groups = {}
    for stavka in stavke:
        if stavka.product_id:
            key = ('p', stavka.product_id)
        else:
            key = ('n', (stavka.artikal_naziv or '').strip().casefold())
        groups.setdefault(key, []).append(stavka)

    rows = []
    for items in groups.values():
        prev = None
        last = None
        change_count = 0
        for stavka in items:
            if prev is not None:
                mpc_changed = stavka.mpc_brutto != prev.mpc_brutto
                vpc_changed = stavka.vpc_netto != prev.vpc_netto
                if mpc_changed or vpc_changed:
                    change_count += 1
                    product = stavka.product or prev.product
                    naziv = product.naziv if product else (stavka.artikal_naziv or prev.artikal_naziv)
                    last = {
                        'product': product,
                        'naziv': naziv,
                        'sifra': (product.sifra or '') if product else '',
                        'kljuc': _nivelacija_kljuc(product, naziv),
                        'uvoz': stavka.uvoz,
                        'prev_mpc': prev.mpc_brutto,
                        'mpc': stavka.mpc_brutto,
                        'prev_vpc': prev.vpc_netto,
                        'vpc': stavka.vpc_netto,
                        'mpc_change': _change_row(
                            'Mpc', _fmt_km(prev.mpc_brutto), _fmt_km(stavka.mpc_brutto),
                            stavka.mpc_brutto, prev.mpc_brutto,
                        ),
                        'vpc_change': _change_row(
                            'Vpc', _fmt_km(prev.vpc_netto), _fmt_km(stavka.vpc_netto),
                            stavka.vpc_netto, prev.vpc_netto,
                        ),
                    }
            prev = stavka
        if last:
            last['change_count'] = change_count
            rows.append(last)

    q = (query or '').strip().casefold()
    if q:
        rows = [
            row for row in rows
            if q in (row['naziv'] or '').casefold() or q in (row['sifra'] or '').casefold()
        ]
    rows.sort(key=lambda row: (row['uvoz'].kreiran, row['uvoz'].pk), reverse=True)
    return rows


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
            url = reverse('staff_magacin_artikal', args=[product.pk])
            q = _magacin_search_query(request)
            if q:
                url = f'{url}?{urlencode({"pretraga": q})}'
            return redirect(url)
        except MagacinError as exc:
            messages.error(request, str(exc))
    meta = getattr(product, 'magacin_meta', None)
    context = _magacin_context(request, section='artikli', page_title=f'Izmjena — {product.naziv}', hide_top_search=True)
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
    _attach_movement_kupci(page.object_list)
    context = _magacin_context(request, section='artikli', page_title=f'Istorija — {product.naziv}', hide_top_search=True)
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
            if action == 'skini':
                loc = get_object_or_404(WarehouseLocation, pk=request.POST.get('location_id'))
                if is_ignored_stock_location(loc):
                    raise MagacinError('Lokacija Prenos u MP se ne evidentira.')
                product = get_object_or_404(Product, pk=int(request.POST.get('product_id') or 0))
                variation = None
                vid = (request.POST.get('variation_id') or '').strip()
                if vid:
                    variation = get_object_or_404(ProductVariation, pk=vid, artikal=product)
                qty = _parse_qty(request.POST.get('kolicina') or '0')
                if qty <= 0:
                    raise MagacinError('Unesi količinu koju skidaš s lokacije.')
                apply_movement(
                    product=product,
                    variation=variation,
                    location=loc,
                    tip='prodaja',
                    kolicina=qty,
                    napomena=request.POST.get('napomena') or f'Skini sa {loc.label}',
                    user=request.user,
                )
                messages.success(request, f'Skinuto {qty} kom s {loc.label}.')
                query = (request.POST.get('pretraga') or '').strip()
                params = {'lokacija': loc.pk}
                if query:
                    params['pretraga'] = query
                return redirect(f"{reverse('staff_magacin_lokacije')}?{urlencode(params)}")
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
        except (ValueError, TypeError):
            messages.error(request, 'Artikal ili lokacija nije validna.')
        if action == 'skini':
            loc_id = (request.POST.get('location_id') or '').strip()
            query = (request.POST.get('pretraga') or '').strip()
            if loc_id:
                params = {'lokacija': loc_id}
                if query:
                    params['pretraga'] = query
                return redirect(f"{reverse('staff_magacin_lokacije')}?{urlencode(params)}")
        return redirect('staff_magacin_lokacije')

    query = (request.GET.get('pretraga') or '').strip()
    selected_id = (request.GET.get('lokacija') or '').strip()
    loc_qs = WarehouseLocation.objects.exclude(
        sifra__icontains='prenos',
    ).exclude(
        naziv__icontains='prenos',
    ).exclude(
        odoo_location_path__icontains='prenos',
    )
    if query:
        loc_qs = loc_qs.filter(
            Q(sifra__icontains=query)
            | Q(naziv__icontains=query)
            | Q(opis__icontains=query)
        )
    elif selected_id.isdigit():
        loc_qs = loc_qs.filter(pk=int(selected_id))
    else:
        loc_qs = loc_qs.none()
    locations = []
    for loc in loc_qs:
        agg = loc.zalihe.filter(kolicina__gt=0).aggregate(
            na_stanju=Sum('kolicina'),
            artikala=Count('product', distinct=True),
        )
        locations.append({
            'location': loc,
            'na_stanju': int(agg['na_stanju'] or 0),
            'artikala': int(agg['artikala'] or 0),
        })
    selected_location = None
    location_stock = []
    if selected_id.isdigit():
        selected_location = WarehouseLocation.objects.filter(pk=int(selected_id)).first()
        if selected_location:
            location_stock = list(
                WarehouseStock.objects.filter(location=selected_location, kolicina__gt=0)
                .select_related('product', 'variation')
                .order_by('product__naziv', 'variation__naziv')
            )
    context = _magacin_context(request, section='lokacije', page_title='Lokacije — Magacin')
    context.update({
        'locations': locations,
        'location_query': query,
        'selected_location': selected_location,
        'location_stock': location_stock,
    })
    return render(request, 'staff/magacin/lokacije.html', context)


def _location_print_rows(location):
    stocks = (
        WarehouseStock.objects.filter(location=location, kolicina__gt=0)
        .select_related('product', 'variation')
        .order_by('product__naziv', 'variation__naziv', 'id')
    )
    rows = []
    for stock in stocks:
        product = stock.product
        variation = stock.variation
        naziv = product.naziv if product else '—'
        if variation:
            naziv = f'{naziv} — {variation.naziv}'.strip()
        sifra = ''
        if variation and variation.sifra:
            sifra = variation.sifra
        elif product:
            sifra = product.sifra or ''
        rows.append({
            'naziv': naziv,
            'sifra': sifra,
            'kolicina': int(stock.kolicina or 0),
        })
    return rows


@login_required(login_url='login')
@user_passes_test(_superuser_required)
def magacin_lokacija_stampa(request, pk):
    location = get_object_or_404(WarehouseLocation, pk=pk)
    if is_ignored_stock_location(location):
        raise Http404('Lokacija se ne štampa.')
    rows = _location_print_rows(location)
    komada = sum(row['kolicina'] for row in rows)
    return render(request, 'staff/magacin/lokacija_print.html', {
        'location': location,
        'rows': rows,
        'artikala': len(rows),
        'komada': komada,
        'print_mode': True,
    })


@login_required(login_url='login')
@user_passes_test(_superuser_required)
def magacin_zalihe(request):
    _ensure_magacin_locations()
    location_id = request.GET.get('lokacija') or ''
    query = _magacin_search_query(request)
    only_low = request.GET.get('nisko') == '1'

    qs = WarehouseStock.objects.select_related(
        'product', 'product__kategorija', 'variation', 'location',
    ).exclude(ignored_location_q('location'))
    if location_id:
        qs = qs.filter(location_id=location_id)
    else:
        qs = countable_stock_qs(qs)
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


@login_required(login_url='login')
@user_passes_test(_superuser_required)
def magacin_rezervni_dijelovi(request):
    query = _magacin_search_query(request)
    qs = (
        OrderItem.objects.filter(rezervni_dio=True)
        .exclude(narudzba__status=Order.Status.OTKAZANA)
        .select_related('artikal', 'narudzba')
        .order_by('-narudzba__kreirana', '-id')
    )
    if query:
        qs = qs.filter(
            Q(naziv__icontains=query)
            | Q(artikal__naziv__icontains=query)
            | Q(artikal__sifra__icontains=query)
            | Q(narudzba__broj__icontains=query)
            | Q(narudzba__ime_prezime__icontains=query)
        )
    paginator = Paginator(qs, 60)
    page = paginator.get_page(request.GET.get('page') or 1)
    context = _magacin_context(
        request, section='rezervni', page_title='Rezervni dijelovi — Magacin',
    )
    context.update({
        'page': page,
        'magacin_search': query,
    })
    return render(request, 'staff/magacin/rezervni_dijelovi.html', context)


def _mp_datum_from_iso(value):
    text = str(value or '').strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return parse_mp_daily_datum(text)


def _mp_samo_promjene_flag(request):
    if request.method == 'POST' and (request.POST.get('action') or 'ocitaj').strip() == 'ocitaj':
        return request.POST.get('samo_promjene') == '1'
    if request.GET.get('samo_promjene') is not None:
        return request.GET.get('samo_promjene') == '1'
    return bool(request.session.get('mp_dnevno_samo_promjene'))


@login_required(login_url='login')
@user_passes_test(_superuser_required)
def magacin_mp_dnevno_skidanje(request):
    _ensure_magacin_locations()
    result = None
    preview = None
    preview_datum = None
    mp_error = ''
    samo_promjene = _mp_samo_promjene_flag(request)
    request.session['mp_dnevno_samo_promjene'] = bool(samo_promjene)
    request.session.modified = True
    if request.method == 'POST':
        action = (request.POST.get('action') or 'ocitaj').strip()
        try:
            if action == 'ukloni':
                request.session.pop('mp_dnevno_preview', None)
                messages.success(request, 'Unos je uklonjen. Ništa nije skinuto sa stanja.')
                return redirect('staff_magacin_mp_dnevno')
            if action == 'skini':
                payload = request.session.get('mp_dnevno_preview') or {}
                parsed = payload.get('rows') or []
                if not parsed:
                    raise MagacinError('Nema prepoznatih šifara. Prvo uploaduj izvještaj.')
                result = deduct_mp_daily_stock(parsed=parsed, user=request.user)
                datum = (
                    _mp_datum_from_iso(payload.get('datum'))
                    or parse_mp_daily_datum(
                        payload.get('raw_text') or '',
                        filename=payload.get('fajl_naziv') or '',
                    )
                    or timezone.localdate()
                )
                request.session.pop('mp_dnevno_preview', None)
                if result['taken']:
                    batch = save_mp_daily_skidanje(
                        result,
                        user=request.user,
                        raw_text=payload.get('raw_text') or '',
                        datum=datum,
                    )
                    messages.success(
                        request,
                        f'Skinuto {len(result["taken"])} šifara s maloprodaje'
                        + (f', {len(result["skipped"])} nije skinuto' if result['skipped'] else '')
                        + '.',
                    )
                    dest = reverse('staff_magacin_mp_dnevno')
                    if batch.datum:
                        dest = f'{dest}?datum={batch.datum.isoformat()}'
                    return redirect(dest)
                messages.warning(request, 'Ništa nije skinuto s maloprodaje.')
                mp_error = 'Ništa nije skinuto s maloprodaje.'
            else:
                fajl = request.FILES.get('fajl')
                if fajl is None:
                    raise MagacinError('Uploaduj sliku ili PDF izvještaja.')
                tekst = extract_mp_daily_text_from_upload(fajl)
                parsed = parse_mp_daily_text(tekst)
                if not parsed:
                    raise MagacinError('Nisam našao šifre ispod kolone ŠIFRA.')
                preview = preview_mp_daily_rows(parsed)
                fajl_naziv = (getattr(fajl, 'name', '') or '')[:200]
                preview_datum = parse_mp_daily_datum(tekst, filename=fajl_naziv) or timezone.localdate()
                request.session['mp_dnevno_preview'] = {
                    'rows': [{'sifra': row['sifra'], 'qty': row['qty']} for row in parsed],
                    'raw_text': tekst[:20000],
                    'fajl_naziv': fajl_naziv,
                    'datum': preview_datum.isoformat(),
                }
                request.session.modified = True
        except MagacinError as exc:
            mp_error = str(exc)
            messages.error(request, mp_error)
        except Exception as exc:
            logger.exception('MP dnevno očitavanje nije uspjelo')
            mp_error = f'Očitavanje slike nije uspjelo: {exc}'
            messages.error(request, mp_error)
    elif request.session.get('mp_dnevno_preview'):
        payload = request.session.get('mp_dnevno_preview') or {}
        preview = preview_mp_daily_rows(payload.get('rows') or [])
        preview_datum = _mp_datum_from_iso(payload.get('datum')) or parse_mp_daily_datum(
            payload.get('raw_text') or '',
            filename=payload.get('fajl_naziv') or '',
        )
    odabrani_datum = _mp_datum_from_iso(request.GET.get('datum'))
    skidanja = []
    datumi = []
    qs = MagacinMpDnevnoSkidanje.objects.select_related('kreirao')
    if odabrani_datum:
        skidanja = list(
            qs.filter(datum=odabrani_datum)
            .prefetch_related('stavke')
            .order_by('-kreiran', '-id')
        )
    else:
        datumi = list(
            qs.exclude(datum__isnull=True)
            .values('datum')
            .annotate(
                stavki=Sum('skinuto_stavki'),
                komada=Sum('skinuto_komada'),
                broj=Count('id'),
            )
            .order_by('-datum')
        )
    context = _magacin_context(
        request,
        section='mp_dnevno',
        page_title='Dnevno skidanje MP lagera — Magacin',
    )
    preview_ukupno = len(preview or [])
    if preview and samo_promjene:
        preview = [row for row in preview if row.get('changed')]
    context.update({
        'result': result,
        'preview': preview,
        'preview_ukupno': preview_ukupno,
        'preview_datum': preview_datum,
        'samo_promjene': samo_promjene,
        'mp_error': mp_error,
        'skidanja': skidanja,
        'datumi': datumi,
        'odabrani_datum': odabrani_datum,
    })
    return render(request, 'staff/magacin/mp_dnevno_skidanje.html', context)


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


def _order_text_search_q(query):
    raw = (query or '').strip()
    if not raw:
        return Q()
    stripped = raw.lstrip('#')
    digits = ''.join(ch for ch in raw if ch.isdigit())
    filt = (
        Q(broj__icontains=stripped)
        | Q(ime_prezime__icontains=raw)
        | Q(telefon__icontains=raw)
    )
    if digits and digits not in {raw, stripped}:
        filt |= Q(broj__icontains=digits) | Q(telefon__icontains=digits)
    return filt


@login_required(login_url='login')
@user_passes_test(_superuser_required)
def magacin_narudzbe(request):
    izvor = (request.GET.get('izvor') or 'sve').strip()
    show_validated = (request.GET.get('validirane') or '') == '1'
    show_all_validated = (request.GET.get('sve') or '') == '1'
    order_query = (request.GET.get('pretraga') or request.GET.get('q') or '').strip()
    today = timezone.localdate()
    day = _parse_brza_posta_day(request.GET.get('datum')) if show_validated else today
    prev_day = day - timedelta(days=1)
    next_day = day + timedelta(days=1)
    orders = (
        Order.objects.exclude(status=Order.Status.OTKAZANA)
        .exclude(_prenos_mp_q())
        .prefetch_related(
            'stavke',
            Prefetch(
                'magacin_holds',
                queryset=OrderStockHold.objects.select_related('location'),
            ),
        )
        .order_by('-kreirana')
    )
    if izvor == 'magacin':
        orders = orders.filter(izvor=Order.Izvor.MAGACIN)
    elif izvor == 'webshop':
        orders = orders.filter(izvor=Order.Izvor.WEBSHOP)
    validated_q = _validated_orders_q()
    if show_validated:
        orders = orders.filter(validated_q)
        if not order_query and not show_all_validated:
            orders = orders.filter(_brza_posta_day_q(day))
    else:
        orders = orders.exclude(validated_q)
    if order_query:
        orders = orders.filter(_order_text_search_q(order_query))
    order_list = list(orders[:200] if show_validated else orders[:80])
    if not show_validated:
        locked = pending_mp_brojevi(collect_mp_checks(order_list))
        for order in order_list:
            order.needs_mp_check = order.broj in locked
    vp_ids = set(
        MagacinVpNarudzba.objects.filter(
            order_id__in=[order.pk for order in order_list],
        ).values_list('order_id', flat=True)
    ) if order_list else set()
    for order in order_list:
        order.is_vp = order.pk in vp_ids
        seen = []
        for hold in order.magacin_holds.all():
            sifra = (hold.location.sifra if hold.location_id else '') or ''
            if sifra and sifra not in seen:
                seen.append(sifra)
        order.lager_lokacije = seen
    base_qs = Order.objects.exclude(status=Order.Status.OTKAZANA).exclude(_prenos_mp_q())

    def _list_qs(
        *,
        izvor_value=izvor,
        all_validated=show_all_validated,
        query=order_query,
        day_value=day,
        validated=show_validated,
    ):
        params = {}
        if validated:
            params['validirane'] = '1'
            if all_validated:
                params['sve'] = '1'
            elif day_value and day_value != today:
                params['datum'] = day_value.isoformat()
        if query:
            params['pretraga'] = query
        if izvor_value and izvor_value != 'sve':
            params['izvor'] = izvor_value
        return urlencode(params)

    context = _magacin_context(request, section='narudzbe', page_title='Narudžbe — Magacin')
    context.update({
        'orders': order_list,
        'izvor_filter': izvor,
        'show_validated': show_validated,
        'show_all_validated': show_all_validated,
        'order_query': order_query,
        'day': day,
        'today': today,
        'prev_day': prev_day,
        'next_day': next_day,
        'can_go_next': next_day <= today,
        'is_today': day == today,
        'qs_sve': _list_qs(izvor_value='sve'),
        'qs_magacin': _list_qs(izvor_value='magacin'),
        'qs_webshop': _list_qs(izvor_value='webshop'),
        'qs_today': _list_qs(all_validated=False, day_value=today, query=''),
        'qs_all': _list_qs(all_validated=True, query=''),
        'qs_clear': _list_qs(query=''),
        'qs_prev_day': _list_qs(all_validated=False, day_value=prev_day, query=''),
        'qs_next_day': _list_qs(all_validated=False, day_value=next_day, query=''),
        'qs_validated': _list_qs(validated=True, all_validated=False, day_value=today, query=''),
        'rucne_count': base_qs.filter(
            izvor=Order.Izvor.MAGACIN,
        ).exclude(validated_q).count(),
        'validated_count': base_qs.filter(validated_q).count(),
        'packing_ready_count': len(packing_ready_orders()),
    })
    return render(request, 'staff/magacin/narudzbe.html', context)


def _parse_brza_posta_day(raw):
    today = timezone.localdate()
    text = (raw or '').strip()
    if text:
        try:
            day = date.fromisoformat(text)
        except ValueError:
            day = today
    else:
        day = today
    if day > today:
        return today
    return day


def _brza_posta_day_q(day):
    return Q(zapakovana_at__date=day) | Q(zapakovana_at__isnull=True, kreirana__date=day)


def _brza_posta_orders_qs(day):
    return (
        Order.objects.filter(_validated_orders_q())
        .exclude(status=Order.Status.OTKAZANA)
        .exclude(_prenos_mp_q())
        .filter(_brza_posta_day_q(day))
        .order_by('brza_posta_unijeta', 'broj')
    )


@login_required(login_url='login')
@user_passes_test(_superuser_required)
def magacin_brza_posta(request):
    day = _parse_brza_posta_day(request.GET.get('datum'))
    today = timezone.localdate()
    prev_day = day - timedelta(days=1)
    next_day = day + timedelta(days=1)
    orders = list(_brza_posta_orders_qs(day))
    pending = sum(1 for row in orders if not (row.xexpress_sifra or '').strip())
    context = _magacin_context(
        request, section='narudzbe', page_title='Pošalji u X-Express — Magacin',
    )
    context.update({
        'day': day,
        'today': today,
        'prev_day': prev_day,
        'next_day': next_day,
        'can_go_next': next_day <= today,
        'is_today': day == today,
        'orders': orders,
        'pending_count': pending,
    })
    return render(request, 'staff/magacin/brza_posta.html', context)


@login_required(login_url='login')
@user_passes_test(_superuser_required)
def magacin_brza_posta_detail(request, broj):
    order = get_object_or_404(
        Order.objects.exclude(status=Order.Status.OTKAZANA).exclude(_prenos_mp_q()),
        broj=broj,
    )
    if not (
        order.lager_status == Order.LagerStatus.VALIDIRANO
        or order.status == Order.Status.ZAVRSENA
    ):
        messages.warning(request, 'Samo validatovane narudžbe idu u Brzu poštu.')
        return redirect('staff_magacin_brza_posta')
    day = _parse_brza_posta_day(request.GET.get('datum') or request.POST.get('datum'))
    if request.method == 'POST' and (request.POST.get('action') or '') == 'unesi':
        if not order.brza_posta_unijeta:
            order.brza_posta_unijeta = True
            order.brza_posta_unijeta_at = timezone.now()
            order.save(update_fields=['brza_posta_unijeta', 'brza_posta_unijeta_at'])
            messages.success(request, f'Narudžba #{order.broj} je unijeta u Brzu poštu.')
        else:
            messages.info(request, f'Narudžba #{order.broj} je već unijeta.')
        return redirect(f"{reverse('staff_magacin_brza_posta')}?datum={day.isoformat()}")
    context = _magacin_context(
        request, section='narudzbe', page_title=f'Brza pošta #{order.broj} — Magacin',
    )
    context.update({
        'order': order,
        'day': day,
        'grad_value': ' '.join(
            part for part in ((order.postanski_broj or '').strip(), (order.grad or '').strip()) if part
        ),
        'iznos_copy': f'{order.ukupno:.2f}'.replace('.', ','),
    })
    return render(request, 'staff/magacin/brza_posta_detail.html', context)


def _mark_brza_posta_entered(order):
    if getattr(order, 'brza_posta_unijeta', False):
        return
    order.brza_posta_unijeta = True
    order.brza_posta_unijeta_at = timezone.now()
    order.save(update_fields=['brza_posta_unijeta', 'brza_posta_unijeta_at'])


@login_required(login_url='login')
@user_passes_test(_superuser_required)
@require_POST
def magacin_xexpress_bulk(request):
    from .xexpress_service import XExpressAlreadySent, XExpressError, create_shipment

    brojevi = [b.strip() for b in request.POST.getlist('b') if (b or '').strip()]
    brojevi = list(dict.fromkeys(brojevi))[:40]
    day = _parse_brza_posta_day(request.POST.get('datum'))
    nxt = (request.POST.get('next') or '').strip()
    if not brojevi:
        messages.error(request, 'Odaberi barem jednu narudžbu za X-Express.')
    else:
        sent, skipped, errors = [], [], []
        for broj in brojevi:
            order = Order.objects.filter(broj=broj).exclude(status=Order.Status.OTKAZANA).first()
            if order is None:
                errors.append(f'#{broj} nije pronađena')
                continue
            if not (
                order.lager_status == Order.LagerStatus.VALIDIRANO
                or order.status == Order.Status.ZAVRSENA
            ):
                errors.append(f'#{broj} nije validatovana')
                continue
            try:
                result = create_shipment(order)
            except XExpressAlreadySent:
                skipped.append(broj)
                continue
            except XExpressError as exc:
                errors.append(f'#{broj}: {exc}')
                continue
            except Exception as exc:
                logger.exception('X-Express bulk #%s', broj)
                errors.append(f'#{broj}: {exc}')
                continue
            sifra = (result or {}).get('sifra') or order.xexpress_sifra
            sent.append(f'#{broj} {sifra}'.strip())
            order.refresh_from_db()
            _mark_brza_posta_entered(order)
        if sent:
            messages.success(
                request,
                'X-Express: ' + ', '.join(sent) + '.',
            )
        if skipped:
            messages.info(
                request,
                'Već poslane: ' + ', '.join(f'#{b}' for b in skipped) + '.',
            )
        if errors:
            messages.error(request, 'X-Express greške: ' + '; '.join(errors))
    if nxt.startswith('/') and not nxt.startswith('//'):
        return redirect(nxt)
    return redirect(f"{reverse('staff_magacin_brza_posta')}?datum={day.isoformat()}")


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
        .prefetch_related('stavke', 'magacin_holds')
    )
    by_broj = {order.broj: order for order in orders}
    ordered = [by_broj[broj] for broj in brojevi if broj in by_broj]
    if not ordered:
        messages.error(request, 'Odabrane narudžbe nisu pronađene.')
        return redirect('staff_magacin_narudzbe')
    blocked = pending_mp_brojevi(collect_mp_checks(ordered))
    if blocked:
        first = next(order.broj for order in ordered if order.broj in blocked)
        messages.warning(
            request,
            f'Narudžba #{first} ima artikal iz maloprodaje. '
            'Prvo označi Ima u MP ili Nema, pa štampaj.',
        )
        return redirect(_provjera_url(first, next_print=True))
    print_jobs = [_order_print_job(order) for order in ordered]
    context = {
        **print_jobs[0],
        'print_jobs': print_jobs,
        'print_brojevi': [order.broj for order in ordered],
        'requires_mp_check': False,
        'allow_reprint': True,
        'mark_printed_url': reverse('staff_magacin_narudzbe_mark_printed'),
    }
    return render(request, 'staff/order_print.html', context)


@login_required(login_url='login')
@user_passes_test(_superuser_required)
def magacin_narudzbe_stampa_kolicine(request):
    brojevi = [b.strip() for b in request.GET.getlist('b') if (b or '').strip()]
    brojevi = list(dict.fromkeys(brojevi))[:30]
    if not brojevi:
        messages.error(request, 'Odaberi barem jednu VP narudžbu za štampu količina.')
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
    print_jobs = []
    for order in ordered:
        stavke = []
        for item in order.stavke.all():
            qty = int(item.kolicina_faktura or 0)
            if qty <= 0:
                continue
            stavke.append({
                'naziv': item.puni_naziv,
                'kolicina': qty,
            })
        print_jobs.append({
            'order': order,
            'stavke': stavke,
        })
    return render(request, 'staff/magacin/stampa_kolicine.html', {
        'print_jobs': print_jobs,
    })


@login_required(login_url='login')
@user_passes_test(_superuser_required)
@require_POST
def magacin_narudzbe_validiraj(request):
    brojevi = [b.strip() for b in request.POST.getlist('b') if (b or '').strip()]
    brojevi = list(dict.fromkeys(brojevi))[:80]
    next_url = request.POST.get('next') or reverse('staff_magacin_narudzbe')
    if not brojevi:
        messages.error(request, 'Označi barem jednu narudžbu za validaciju.')
        return HttpResponseRedirect(next_url)
    orders = list(
        Order.objects.filter(broj__in=brojevi)
        .exclude(status=Order.Status.OTKAZANA)
    )
    if not orders:
        messages.error(request, 'Označene narudžbe nisu pronađene.')
        return HttpResponseRedirect(next_url)
    ok = 0
    errors = []
    for order in orders:
        try:
            validate_order_stock(order, user=request.user)
            ok += 1
        except MagacinError as exc:
            errors.append(f'#{order.broj}: {exc}')
    if ok:
        messages.success(request, f'Validirano {ok} narudžb{"a" if ok == 1 else "i"}.')
    for err in errors:
        messages.error(request, err)
    return HttpResponseRedirect(next_url)


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


def _order_was_picked(order):
    items = list(order.stavke.all())
    if any(item.kolicina_pokupljeno is not None for item in items):
        return True
    state = order.pick_state if isinstance(getattr(order, 'pick_state', None), dict) else {}
    return any(
        isinstance(row, dict) and (row.get('done') or int(row.get('got') or 0) > 0)
        for row in state.values()
    )


PACKING_READY_LIMIT = 80
PACKING_TODAY_LIMIT = 200
PACKING_REPRINT_PASSWORD = 'admin'
PACKING_REPRINT_SESSION_KEY = 'mg_packing_reprint_ok'
PACKING_REPRINT_TTL = 15 * 60


def _packing_reprint_password_ok(raw):
    password = (raw or '').strip()
    expected = PACKING_REPRINT_PASSWORD
    if len(password) != len(expected):
        return False
    return hmac.compare_digest(password, expected)


def _packing_reprint_unlocked(request):
    raw = request.session.get(PACKING_REPRINT_SESSION_KEY)
    try:
        started = float(raw)
    except (TypeError, ValueError):
        return False
    return (timezone.now().timestamp() - started) <= PACKING_REPRINT_TTL


def _unlock_packing_reprint(request):
    request.session[PACKING_REPRINT_SESSION_KEY] = timezone.now().timestamp()


def _picked_item_exists():
    return Exists(
        OrderItem.objects.filter(
            narudzba_id=OuterRef('pk'),
            kolicina_pokupljeno__isnull=False,
        )
    )


def packing_ready_orders():
    """Narudžbe spremne za packing: validatovane, pickovane, još neodštampane.

    Filter po pokupljenoj količini ide u bazu — inače starije validirane
    (nikad pickovane) popune LIMIT i nova pickovana ostane skrivena.
    """
    orders = list(
        Order.objects.exclude(status=Order.Status.OTKAZANA)
        .exclude(_prenos_mp_q())
        .filter(_validated_orders_q(), packing_odstampana=False)
        .filter(_picked_item_exists())
        .prefetch_related('stavke', 'magacin_holds')
        .order_by('kreirana')[:PACKING_READY_LIMIT]
    )
    return [order for order in orders if _order_was_picked(order)]


def packing_orders_for_date(day):
    """Pickovane i validatovane pošiljke za datum, uključujući već odštampane packinge."""
    if day is None:
        day = timezone.localdate()
    orders = list(
        Order.objects.exclude(status=Order.Status.OTKAZANA)
        .exclude(_prenos_mp_q())
        .filter(_validated_orders_q())
        .filter(_picked_item_exists())
        .filter(
            Q(zapakovana_at__date=day)
            | Q(packing_odstampana_at__date=day)
            | Q(kreirana__date=day)
        )
        .prefetch_related('stavke', 'magacin_holds')
        .order_by('kreirana')[:PACKING_TODAY_LIMIT]
    )
    return [order for order in orders if _order_was_picked(order)]


def packing_today_orders():
    """Sve pickovane pošiljke od danas, uključujući već odštampane packinge."""
    return packing_orders_for_date(timezone.localdate())


def _trim_picks_to_qty(picks, qty):
    remaining = max(0, int(qty or 0))
    out = []
    for pick in picks or []:
        if remaining <= 0:
            break
        take = min(max(0, int(pick.get('take') or 0)), remaining)
        if take <= 0:
            continue
        row = dict(pick)
        row['take'] = take
        out.append(row)
        remaining -= take
    return out


def _picks_from_pick_state(order):
    state = order.pick_state if isinstance(getattr(order, 'pick_state', None), dict) else {}
    by_item = {}
    for key, row in state.items():
        if not isinstance(row, dict):
            continue
        try:
            got = max(0, int(row.get('got') or 0))
        except (TypeError, ValueError):
            continue
        if got <= 0:
            continue
        item_id = row.get('item_id')
        loc = ''
        if isinstance(key, str) and ':' in str(key):
            raw_id, loc = str(key).split(':', 1)
            if not item_id:
                try:
                    item_id = int(raw_id)
                except (TypeError, ValueError):
                    item_id = None
        try:
            item_id = int(item_id or 0)
        except (TypeError, ValueError):
            item_id = 0
        if not item_id:
            continue
        loc = (loc or '').strip() or 'MP'
        by_item.setdefault(item_id, []).append({
            'location_name': loc,
            'location_id': None,
            'take': got,
            'on_hand': got,
        })
    return by_item


def _build_picked_packing_lines(order):
    from .views import _magacin_hold_picks

    items = list(order.stavke.select_related('artikal', 'varijacija').all())
    from_state = _picks_from_pick_state(order)
    hold_picks = _magacin_hold_picks(order, items)
    lines = []
    rb = 0
    for item in items:
        qty = int(item.kolicina_faktura or 0)
        if qty <= 0:
            continue
        if item.pk in from_state:
            picks = _trim_picks_to_qty(from_state[item.pk], qty)
        elif item.pk in hold_picks:
            picks = _trim_picks_to_qty(hold_picks[item.pk][0], qty)
        else:
            picks = []
        picks = sorted(
            picks,
            key=lambda p: (
                1 if (p.get('location_name') or '') in {'MP', 'Provjeri u MP', 'Nije popisan'} else 0,
                (p.get('location_name') or '').casefold(),
            ),
        )
        if picks:
            pick_text = ' · '.join(
                f"{p['take']}× {p['location_name']}" for p in picks
            )
        else:
            pick_text = '—'
        display_name = item.product_naziv or item.naziv
        if item.varijacija_naziv:
            display_name = f'{display_name} — {item.varijacija_naziv}'
        rb += 1
        lines.append({
            'rb': rb,
            'item_id': item.pk,
            'naziv': display_name,
            'sifra': item.sifra or '',
            'kolicina': qty,
            'picks': picks,
            'pick_text': pick_text,
            'shortfall': 0,
            'check_mp': any((p.get('location_name') or '') in {'MP', 'Provjeri u MP'} for p in picks),
            'nije_popisan': any((p.get('location_name') or '') == 'Nije popisan' for p in picks),
        })
    return lines, ''


def _order_packing_job(order):
    packing_lines, odoo_error = _build_picked_packing_lines(order)
    created = timezone.localtime(order.kreirana)
    order.is_vp = is_vp_order(order)
    return {
        'order': order,
        'packing_lines': packing_lines,
        'odoo_error': odoo_error,
        'datum': created.strftime('%d.%m.%Y.'),
        'vrijeme': created.strftime('%H:%M'),
    }


def _render_packing_jobs(ordered):
    now = timezone.now()
    Order.objects.filter(
        pk__in=[order.pk for order in ordered],
        packing_odstampana=False,
    ).update(packing_odstampana=True, packing_odstampana_at=now)
    invalidate_magacin_nav_counts()
    print_jobs = [_order_packing_job(order) for order in ordered]
    return {
        **print_jobs[0],
        'print_jobs': print_jobs,
        'print_brojevi': [order.broj for order in ordered],
    }


@login_required(login_url='login')
@user_passes_test(_superuser_required)
def magacin_narudzbe_packing_izbor(request):
    if request.method == 'POST':
        if not _packing_reprint_password_ok(request.POST.get('lozinka')):
            messages.error(request, 'Pogrešna lozinka za reprint packinga.')
            return redirect('staff_magacin_narudzbe')
        _unlock_packing_reprint(request)
        return redirect('staff_magacin_narudzbe_packing_izbor')
    if not _packing_reprint_unlocked(request):
        messages.error(request, 'Unesi lozinku za reprint packinga.')
        return redirect('staff_magacin_narudzbe')
    day = _parse_iso_date(request.GET.get('datum')) or timezone.localdate()
    orders = packing_orders_for_date(day)
    context = _magacin_context(
        request, section='narudzbe', page_title='Reprint packinga — Magacin',
        hide_top_search=True,
    )
    context.update({
        'orders': orders,
        'datum': day.isoformat(),
        'datum_label': day.strftime('%d.%m.%Y.'),
        'print_url': reverse('staff_magacin_narudzbe_packing'),
    })
    return render(request, 'staff/magacin/packing_izbor.html', context)


@login_required(login_url='login')
@user_passes_test(_superuser_required)
def magacin_narudzbe_packing(request):
    if request.method == 'POST' and request.POST.get('action') == 'stampaj':
        if not _packing_reprint_unlocked(request):
            messages.error(request, 'Unesi lozinku za reprint packinga.')
            return redirect('staff_magacin_narudzbe')
        day = _parse_iso_date(request.POST.get('datum')) or timezone.localdate()
        wanted = [b.strip() for b in request.POST.getlist('b') if (b or '').strip()]
        wanted = list(dict.fromkeys(wanted))[:PACKING_TODAY_LIMIT]
        available = {order.broj: order for order in packing_orders_for_date(day)}
        ordered = [available[broj] for broj in wanted if broj in available]
        if not ordered:
            messages.info(request, f'Nema označenih packing pošiljki za {day.strftime("%d.%m.%Y.")}.')
            url = reverse('staff_magacin_narudzbe_packing_izbor')
            return redirect(f'{url}?datum={day.isoformat()}')
        return render(
            request,
            'staff/magacin/narudzbe_packing.html',
            _render_packing_jobs(ordered),
        )
    ordered = packing_ready_orders()
    if not ordered:
        messages.info(request, 'Nema pickovanih i validatovanih narudžbi za packing.')
        return redirect('staff_magacin_narudzbe')
    return render(
        request,
        'staff/magacin/narudzbe_packing.html',
        _render_packing_jobs(ordered),
    )


@login_required(login_url='login')
@user_passes_test(_superuser_required)
def magacin_artikli_lookup(request):
    query = (request.GET.get('q') or '').strip()
    include_zero = (request.GET.get('bez_zalihe') or '') == '1'
    try:
        limit = int(request.GET.get('limit') or 30)
    except (TypeError, ValueError):
        limit = 30
    limit = max(1, min(limit, 200))
    products, exact = search_products(query, limit=limit, include_zero=include_zero)
    if exact:
        products = [exact]
    results = []
    for product in products:
        totals = display_stock_totals(product)
        if not include_zero and totals['na_stanju'] <= 0 and totals['dostupno'] <= 0:
            continue
        results.append({
            'id': product.pk,
            'naziv': product.naziv,
            'sifra': product.sifra or '',
            'barkod': product.barkod or '',
            'cijena': str(product.prikazna_cijena),
            'na_stanju': totals['na_stanju'],
            'dostupno': totals['dostupno'],
            'varijacije': [
                {
                    'id': var.pk,
                    'naziv': var.naziv,
                    'sifra': var.sifra or '',
                    'cijena': str(var.prikazna_cijena),
                    'na_stanju': display_stock_totals(product, var)['dostupno'],
                }
                for var in product.varijacije.all()
            ],
        })
    return JsonResponse({
        'results': results,
        'query': query,
        'include_zero': include_zero,
        'exact': bool(exact),
    })


def _phone_digits(value):
    return ''.join(ch for ch in (value or '') if ch.isdigit())


@login_required(login_url='login')
@user_passes_test(_superuser_required)
def magacin_kupci_lookup(request):
    query = (request.GET.get('q') or '').strip()
    qs = WarehouseCustomer.objects.order_by('-azuriran', 'ime_prezime')
    if query:
        digits = _phone_digits(query)
        filt = (
            Q(ime_prezime__icontains=query)
            | Q(telefon__icontains=query)
            | Q(grad__icontains=query)
        )
        if digits:
            filt |= Q(telefon__icontains=digits)
        matches = list(qs.filter(filt)[:40])
        if digits and len(matches) < 40:
            seen = {row.pk for row in matches}
            for row in WarehouseCustomer.objects.order_by('-azuriran'):
                if row.pk in seen:
                    continue
                if digits in _phone_digits(row.telefon):
                    matches.append(row)
                    seen.add(row.pk)
                if len(matches) >= 40:
                    break
        results = [_customer_payload(row) for row in matches]
    else:
        results = [_customer_payload(row) for row in qs[:40]]
    return JsonResponse({'results': results, 'query': query})


def _customer_payload(customer):
    return {
        'id': customer.pk,
        'ime_prezime': customer.ime_prezime,
        'telefon': customer.telefon,
        'adresa': customer.adresa,
        'grad': customer.grad,
        'email': customer.email,
        'postanski_broj': customer.postanski_broj,
    }


@login_required(login_url='login')
@user_passes_test(_superuser_required)
@require_GET
def magacin_loyalty_telefon(request):
    from .loyalty import loyalty_info_za_telefon

    telefon = (request.GET.get('telefon') or request.GET.get('q') or '').strip()
    info = loyalty_info_za_telefon(telefon) if telefon else None
    return JsonResponse({'ok': True, 'loyalty': info})


@login_required(login_url='login')
@user_passes_test(_superuser_required)
@require_POST
def magacin_kupci_save(request):
    ime = (request.POST.get('ime_prezime') or '').strip()
    telefon = (request.POST.get('telefon') or '').strip()
    postanski_broj = (request.POST.get('postanski_broj') or '').strip()
    customer_id = (request.POST.get('customer_id') or '').strip() or None
    if not ime or not telefon:
        return JsonResponse({'ok': False, 'error': 'Ime i telefon su obavezni.'}, status=400)
    if not customer_id and not postanski_broj:
        return JsonResponse({'ok': False, 'error': 'Poštanski broj je obavezan.'}, status=400)
    try:
        customer = _save_warehouse_customer(
            ime=ime,
            telefon=telefon,
            adresa=request.POST.get('adresa') or '',
            grad=request.POST.get('grad') or '',
            email=request.POST.get('email') or '',
            postanski_broj=postanski_broj,
            customer_id=customer_id,
            replace=bool(customer_id),
        )
    except MagacinError as exc:
        return JsonResponse({'ok': False, 'error': str(exc)}, status=400)
    except (TypeError, ValueError):
        return JsonResponse({'ok': False, 'error': 'Kupac nije sačuvan.'}, status=400)
    if not customer:
        return JsonResponse({'ok': False, 'error': 'Kupac nije sačuvan.'}, status=400)
    return JsonResponse({'ok': True, 'customer': _customer_payload(customer)})


def _save_warehouse_customer(
    *, ime, telefon, adresa='', grad='', email='', postanski_broj='',
    customer_id=None, replace=False,
):
    ime = (ime or '').strip()
    telefon = (telefon or '').strip()
    if not ime or not telefon:
        return None
    customer = None
    if customer_id:
        customer = WarehouseCustomer.objects.filter(pk=int(customer_id)).first()
        if customer is None:
            return None
        clash = (
            WarehouseCustomer.objects.filter(telefon=telefon)
            .exclude(pk=customer.pk)
            .first()
        )
        if clash:
            raise MagacinError('Već postoji kupac s tim telefonom.')
    else:
        customer = (
            WarehouseCustomer.objects.filter(ime_prezime__iexact=ime, telefon=telefon).first()
            or WarehouseCustomer.objects.filter(telefon=telefon).first()
        )
    fields = {
        'ime_prezime': ime[:200],
        'telefon': telefon[:30],
        'adresa': (adresa or '').strip()[:300],
        'grad': (grad or '').strip()[:100],
        'email': (email or '').strip()[:254],
        'postanski_broj': (postanski_broj or '').strip()[:20],
    }
    if customer:
        changed = []
        for key, value in fields.items():
            if not replace and not value:
                continue
            if getattr(customer, key) != value:
                setattr(customer, key, value)
                changed.append(key)
        if changed:
            customer.save(update_fields=changed)
        return customer
    return WarehouseCustomer.objects.create(**fields)


@login_required(login_url='login')
@user_passes_test(_superuser_required)
def magacin_kupci(request):
    if request.method == 'POST':
        action = (request.POST.get('action') or 'save').strip()
        try:
            if action == 'delete':
                customer = get_object_or_404(WarehouseCustomer, pk=request.POST.get('customer_id'))
                customer.delete()
                messages.success(request, 'Kupac je obrisan.')
            else:
                if not (request.POST.get('customer_id') or '').strip() and not (
                    request.POST.get('postanski_broj') or ''
                ).strip():
                    raise MagacinError('Poštanski broj je obavezan.')
                customer = _save_warehouse_customer(
                    ime=request.POST.get('ime_prezime') or '',
                    telefon=request.POST.get('telefon') or '',
                    adresa=request.POST.get('adresa') or '',
                    grad=request.POST.get('grad') or '',
                    email=request.POST.get('email') or '',
                    postanski_broj=request.POST.get('postanski_broj') or '',
                    customer_id=request.POST.get('customer_id') or None,
                    replace=True,
                )
                if not customer:
                    raise MagacinError('Ime i telefon su obavezni.')
                messages.success(request, 'Kupac je sačuvan.')
        except MagacinError as exc:
            messages.error(request, str(exc))
        except (TypeError, ValueError):
            messages.error(request, 'Kupac nije sačuvan.')
        return redirect('staff_magacin_kupci')

    query = (request.GET.get('q') or '').strip()
    qs = WarehouseCustomer.objects.order_by('ime_prezime', 'id')
    if query:
        digits = _phone_digits(query)
        filt = (
            Q(ime_prezime__icontains=query)
            | Q(telefon__icontains=query)
            | Q(grad__icontains=query)
            | Q(adresa__icontains=query)
        )
        if digits:
            filt |= Q(telefon__icontains=digits)
        qs = qs.filter(filt)
    editing = None
    edit_id = (request.GET.get('id') or '').strip()
    if edit_id:
        editing = WarehouseCustomer.objects.filter(pk=edit_id).first()
    context = _magacin_context(request, section='kupci', page_title='Kupci — Magacin')
    context.update({
        'customers': list(qs[:300]),
        'customer_query': query,
        'editing': editing,
        'customer_count': qs.count(),
    })
    return render(request, 'staff/magacin/kupci.html', context)


@login_required(login_url='login')
@user_passes_test(_superuser_required)
def magacin_narudzba_nova(request):
    requested_broj = (
        request.POST.get('order_broj') or request.GET.get('broj') or ''
    ).strip().lstrip('#')
    existing = _editable_order_from_request(request)
    if requested_broj and existing is None:
        found = Order.objects.filter(broj=requested_broj).first()
        if found is not None:
            messages.error(request, 'Ova narudžba se ne može mijenjati.')
            return redirect('staff_order_detail', broj=found.broj)
        messages.error(request, 'Narudžba nije pronađena.')
        return redirect('staff_magacin_narudzbe')
    if existing is not None and existing.status == Order.Status.REZERVACIJA:
        page_title = f'Rezervacija #{existing.broj}'
    elif existing is not None:
        page_title = f'Narudžba #{existing.broj}'
    else:
        page_title = 'Nova ručna narudžba'
    context = _magacin_context(request, section='narudzbe', page_title=page_title)
    context['customer_lookup_url'] = reverse('staff_magacin_kupci_lookup')
    context['loyalty_lookup_url'] = reverse('staff_magacin_loyalty_telefon')
    context['existing_order'] = existing
    if request.method == 'POST':
        if (request.POST.get('action') or '').strip() == 'otkazi':
            if existing is None:
                messages.error(request, 'Narudžba za otkazivanje nije pronađena.')
                return redirect('staff_magacin_narudzbe')
            try:
                cancel_order_stock(existing, user=request.user)
            except MagacinError as exc:
                messages.error(request, str(exc))
                return redirect(f"{reverse('staff_magacin_narudzba_nova')}?broj={existing.broj}")
            messages.success(
                request,
                f'Narudžba #{existing.broj} je otkazana — rezervacija je vraćena na lokacije.',
            )
            return redirect('staff_magacin_narudzbe')
        try:
            order = _create_manual_order(request, existing=existing)
        except MagacinError as exc:
            messages.error(request, str(exc))
            context['form'] = request.POST
            context['form_lines'] = _posted_display_lines(request)
            return render(request, 'staff/magacin/narudzba_nova.html', context)
        if order.status == Order.Status.REZERVACIJA:
            messages.success(
                request,
                f'Rezervacija #{order.broj} je sačuvana. '
                'Možeš dodati ili izbaciti artikle, pa Sačuvaj kad je gotova.',
            )
            return redirect(f"{reverse('staff_magacin_narudzba_nova')}?broj={order.broj}")
        if existing is not None:
            messages.success(
                request,
                f'Narudžba #{order.broj} je ažurirana. '
                'Picking je usklađen — uklonjeni artikli su izbačeni, dodani su na listu.',
            )
            if order.pick_claimed_by_id:
                return redirect('staff_magacin_pakuj_detail', broj=order.broj)
            return redirect('staff_magacin_pakuj')
        return _after_order_created_redirect(request, order)

    if existing:
        from .pricing import order_waived_shipping

        loyalty_info = existing.loyalty_popust_info()
        context['form'] = {
            'ime_prezime': existing.ime_prezime,
            'telefon': existing.telefon,
            'email': existing.email,
            'adresa': existing.adresa,
            'grad': existing.grad,
            'postanski_broj': existing.postanski_broj,
            'napomena': existing.napomena,
            'popust_pct': _manual_popust_pct_display(existing),
            'bez_dostave': '1' if order_waived_shipping(existing) else '',
            'placanje': _manual_placanje(existing),
            'loyalty_auto': '1' if loyalty_info else '',
        }
        context['form_lines'] = _order_display_lines(existing)
    else:
        context['form'] = {}
        context['form_lines'] = []
    return render(request, 'staff/magacin/narudzba_nova.html', context)


def _editable_order_from_request(request):
    broj = (request.POST.get('order_broj') or request.GET.get('broj') or '').strip().lstrip('#')
    if not broj:
        return None
    order = (
        Order.objects.filter(broj=broj)
        .exclude(status=Order.Status.OTKAZANA)
        .first()
    )
    if order is None:
        return None
    if order.status == Order.Status.REZERVACIJA:
        return order
    if getattr(order, 'izvor', '') != Order.Izvor.MAGACIN:
        return None
    if not order_is_editable(order):
        return None
    return order


def _posted_display_lines(request):
    lines = []
    product_ids = request.POST.getlist('product_id')
    variation_ids = request.POST.getlist('variation_id')
    kolicine = request.POST.getlist('kolicina')
    mp_flags = request.POST.getlist('mp_ok')
    rezervni_flags = request.POST.getlist('rezervni')
    spare_names = request.POST.getlist('spare_naziv')
    spare_prices = request.POST.getlist('spare_cijena')
    for index, raw_pid in enumerate(product_ids):
        try:
            qty = _parse_qty(kolicine[index] if index < len(kolicine) else 1)
        except MagacinError:
            qty = 1
        if (rezervni_flags[index] if index < len(rezervni_flags) else '') == '1':
            naziv = (spare_names[index] if index < len(spare_names) else '').strip()
            try:
                cijena = _parse_money(spare_prices[index] if index < len(spare_prices) else '') or Decimal('0.00')
            except MagacinError:
                cijena = Decimal('0.00')
            spare_product = None
            if raw_pid:
                try:
                    spare_product = Product.objects.filter(pk=int(raw_pid)).first()
                except (TypeError, ValueError):
                    spare_product = None
            available = display_stock_totals(spare_product)['dostupno'] if spare_product else 0
            lines.append({
                'product': spare_product,
                'variation': None,
                'qty': qty,
                'mp_ok': (mp_flags[index] if index < len(mp_flags) else '') == '1',
                'cijena': cijena,
                'dostupno': available,
                'rezervni': True,
                'naziv': naziv or 'Rezervni dio',
            })
            continue
        try:
            product = Product.objects.get(pk=int(raw_pid))
        except (TypeError, ValueError, Product.DoesNotExist):
            continue
        variation = None
        raw_vid = variation_ids[index] if index < len(variation_ids) else ''
        if raw_vid:
            variation = ProductVariation.objects.filter(pk=int(raw_vid), artikal=product).first()
        cijena = variation.prikazna_cijena if variation else product.prikazna_cijena
        available = display_stock_totals(product, variation)['dostupno']
        lines.append({
            'product': product,
            'variation': variation,
            'qty': qty,
            'mp_ok': (mp_flags[index] if index < len(mp_flags) else '') == '1',
            'cijena': cijena,
            'dostupno': available,
            'rezervni': False,
            'naziv': product.naziv,
        })
    return lines


def _held_qty_on_order(order, product, variation):
    if order is None or product is None:
        return 0
    filt = {'product': product, 'status': OrderStockHold.Status.REZERVISANO}
    if variation is None:
        filt['variation__isnull'] = True
    else:
        filt['variation'] = variation
    return sum(int(h.kolicina or 0) for h in order.magacin_holds.filter(**filt))


def _order_display_lines(order):
    lines = []
    for item in order.stavke.select_related('artikal', 'varijacija'):
        product = item.artikal
        variation = item.varijacija
        available = 0
        if product is not None:
            available = display_stock_totals(product, variation)['dostupno'] + _held_qty_on_order(
                order, product, variation,
            )
        lines.append({
            'product': product,
            'variation': variation,
            'qty': item.kolicina,
            'mp_ok': 'Nije popisan' in (order.napomena or ''),
            'cijena': item.cijena,
            'dostupno': available,
            'rezervni': bool(getattr(item, 'rezervni_dio', False)),
            'naziv': item.naziv or (product.naziv if product else 'Rezervni dio'),
        })
    return lines


def _create_manual_order(request, *, existing=None):
    ime = (request.POST.get('ime_prezime') or '').strip()
    telefon = (request.POST.get('telefon') or '').strip()
    if not ime:
        raise MagacinError('Ime i prezime su obavezni.')
    if not telefon:
        raise MagacinError('Telefon je obavezan.')
    email = (request.POST.get('email') or '').strip() or 'carpologijabh@gmail.com'
    adresa = (request.POST.get('adresa') or '').strip() or 'Ručni unos'
    grad = (request.POST.get('grad') or '').strip() or '—'
    _save_warehouse_customer(
        ime=ime,
        telefon=telefon,
        adresa=adresa,
        grad=grad,
        email='' if email == 'carpologijabh@gmail.com' else email,
        postanski_broj=(request.POST.get('postanski_broj') or '').strip(),
    )
    product_ids = request.POST.getlist('product_id')
    variation_ids = request.POST.getlist('variation_id')
    kolicine = request.POST.getlist('kolicina')
    mp_flags = request.POST.getlist('mp_ok')
    rezervni_flags = request.POST.getlist('rezervni')
    spare_names = request.POST.getlist('spare_naziv')
    spare_prices = request.POST.getlist('spare_cijena')
    if not product_ids:
        raise MagacinError('Dodaj barem jedan artikal.')

    lines = []
    for index, raw_pid in enumerate(product_ids):
        qty = _parse_qty(kolicine[index] if index < len(kolicine) else 1)
        if qty <= 0:
            raise MagacinError('Količina mora biti veća od nule.')
        if (rezervni_flags[index] if index < len(rezervni_flags) else '') == '1':
            naziv = (spare_names[index] if index < len(spare_names) else '').strip()
            if not naziv:
                raise MagacinError('Unesi naziv rezervnog dijela.')
            if not raw_pid:
                raise MagacinError('Odaberi artikal za koji šalješ rezervni dio.')
            try:
                spare_product = Product.objects.get(pk=int(raw_pid))
            except (TypeError, ValueError, Product.DoesNotExist):
                raise MagacinError('Artikal za rezervni dio nije pronađen.')
            cijena = _parse_money(spare_prices[index] if index < len(spare_prices) else '')
            if cijena is None or cijena < 0:
                raise MagacinError('Unesi naplatu za rezervni dio.')
            cijena = cijena.quantize(Decimal('0.01'))
            mp_ok = (mp_flags[index] if index < len(mp_flags) else '') == '1'
            available = display_stock_totals(spare_product)['dostupno'] + _held_qty_on_order(
                existing, spare_product, None,
            )
            if available < qty and not mp_ok:
                raise MagacinError(
                    f'„{spare_product.naziv}” nema dostupnog artikla ({available}). '
                    'Označi Nije popisan da ga dodaš, ili makni stavku.'
                )
            lines.append({
                'product': spare_product,
                'variation': None,
                'qty': qty,
                'mp_ok': mp_ok,
                'cijena': cijena,
                'bazna': cijena,
                'shortfall': max(0, qty - available),
                'rezervni': True,
                'naziv': naziv,
            })
            continue
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
        mp_ok = (mp_flags[index] if index < len(mp_flags) else '') == '1'
        available = display_stock_totals(product, variation)['dostupno'] + _held_qty_on_order(
            existing, product, variation,
        )
        if available < qty and not mp_ok:
            raise MagacinError(
                f'„{product.naziv}” nema dostupnog artikla ({available}). '
                'Označi Nije popisan da ga dodaš, ili makni stavku.'
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
            'rezervni': False,
            'naziv': product.naziv,
        })

    medjuzbir = sum((line['cijena'] * line['qty'] for line in lines), Decimal('0.00'))
    from .pricing import _loyalty_osnovica_iz_korpe, _postotni_popust, _standardna_dostava
    from .loyalty import loyalty_coupon_za_telefon
    placanje = (request.POST.get('placanje') or 'gotovina').strip().lower()
    if placanje not in ('gotovina', 'kartica'):
        placanje = 'gotovina'
    popust_pct = _parse_manual_popust_pct(request.POST.get('popust_pct'))
    loyalty_auto = (request.POST.get('loyalty_auto') or '').strip().lower() in (
        '1', 'on', 'true', 'da',
    )
    loyalty_coupon = None
    if loyalty_auto or not popust_pct:
        loyalty_coupon = loyalty_coupon_za_telefon(telefon)
        if loyalty_coupon and loyalty_coupon.postotak and loyalty_coupon.postotak > 0:
            if loyalty_auto and popust_pct and popust_pct != loyalty_coupon.postotak:
                loyalty_coupon = None
            else:
                popust_pct = loyalty_coupon.postotak
        else:
            loyalty_coupon = None
    if loyalty_coupon and popust_pct:
        cart_items = []
        for line in lines:
            cijena = line['cijena']
            bazna = line.get('bazna') if line.get('bazna') is not None else cijena
            cart_items.append({
                'cijena': cijena,
                'bazna_cijena': bazna,
                'cijena_decimal': cijena,
                'bazna_cijena_decimal': bazna,
                'quantity': line['qty'],
                'na_akciji': bool(bazna and cijena < bazna),
            })
        popust = _postotni_popust(_loyalty_osnovica_iz_korpe(cart_items), popust_pct)
    else:
        popust = _postotni_popust(medjuzbir, popust_pct) if popust_pct else Decimal('0.00')
    if popust > medjuzbir:
        popust = medjuzbir
    dostava, _, _, _ = _standardna_dostava(medjuzbir)
    bez_dostave = (request.POST.get('bez_dostave') or '').strip().lower() in (
        '1', 'on', 'true', 'da',
    )
    if placanje == 'kartica':
        loyalty_coupon = None
        popust_pct = Decimal('100')
        popust = medjuzbir
        bez_dostave = True
    if bez_dostave:
        dostava = Decimal('0.00')
    action = (request.POST.get('action') or '').strip()
    if action == 'rezervacija':
        keep_reservation = True
    elif action == 'sacuvaj':
        keep_reservation = False
    elif existing is not None:
        keep_reservation = existing.status == Order.Status.REZERVACIJA
    else:
        keep_reservation = False
    with transaction.atomic():
        order = _save_manual_order(
            request, ime, telefon, email, adresa, grad, medjuzbir, dostava, lines,
            existing=existing,
            rezervacija=keep_reservation,
            popust=popust,
            popust_pct=popust_pct,
            bez_dostave=bez_dostave,
            placanje=placanje,
            loyalty_coupon=loyalty_coupon,
        )
    return order


def _parse_manual_popust_pct(raw):
    text = (raw or '').strip().replace('%', '').replace(',', '.').replace(' ', '')
    if not text:
        return Decimal('0')
    try:
        pct = Decimal(text)
    except InvalidOperation:
        raise MagacinError('Popust mora biti broj (npr. 10).')
    if pct < 0 or pct > 100:
        raise MagacinError('Popust mora biti od 0 do 100.')
    return pct


def _manual_popust_pct_display(order):
    for row in (getattr(order, 'popust_detalji', None) or []):
        if not isinstance(row, dict):
            continue
        if row.get('placanje') == 'kartica':
            continue
        raw = row.get('postotak')
        if raw not in (None, ''):
            return str(raw)
    return ''


def _manual_placanje(order):
    from .pricing import order_paid_by_card

    return 'kartica' if order_paid_by_card(order) else 'gotovina'


def _strip_card_pay_note(napomena):
    skip = {'plaćeno karticom', 'placeno karticom'}
    lines = [
        line for line in (napomena or '').splitlines()
        if line.strip().casefold() not in skip
    ]
    return '\n'.join(lines).strip()


def _clear_order_items_and_holds(order, user=None):
    for item in list(order.stavke.all()):
        product = item.artikal
        variation = item.varijacija
        if product is not None:
            release_holds_for_product(order, product, variation, user=user)
        item.delete()
    order.pick_state = {}
    order.save(update_fields=['pick_state'])


def _save_manual_order(
    request, ime, telefon, email, adresa, grad, medjuzbir, dostava, lines,
    *, existing=None, rezervacija=False, popust=None, popust_pct=None,
    bez_dostave=False, placanje='gotovina', loyalty_coupon=None,
):
    napomena = _strip_card_pay_note((request.POST.get('napomena') or '').strip())
    mp_names = [
        (f"{line['product'].naziv} {line['variation'].naziv}".strip() if line['variation'] else line['product'].naziv)
        for line in lines
        if line.get('product') and line['mp_ok'] and line['shortfall'] > 0
    ]
    if mp_names:
        extra = 'Nije popisan: ' + ', '.join(mp_names)
        napomena = f'{napomena}\n{extra}'.strip() if napomena else extra
    if placanje == 'kartica':
        napomena = f'{napomena}\nPlaćeno karticom'.strip() if napomena else 'Plaćeno karticom'
    popust = popust if popust is not None else Decimal('0.00')
    popust_pct = popust_pct if popust_pct is not None else Decimal('0')
    popust_detalji = []
    if placanje == 'kartica':
        popust_detalji.append({
            'opis': 'Plaćeno karticom',
            'iznos': str(popust),
            'postotak': '100',
            'placanje': 'kartica',
        })
    elif popust > 0 and popust_pct > 0:
        pct_label = (
            str(int(popust_pct))
            if popust_pct == popust_pct.to_integral()
            else str(popust_pct)
        )
        if loyalty_coupon:
            popust_detalji.append({
                'opis': f'Loyalty član — {pct_label}% popusta',
                'iznos': str(popust),
                'postotak': pct_label,
                'kupon': loyalty_coupon.kod,
            })
        else:
            popust_detalji.append({
                'opis': f'Ručni popust {pct_label}%',
                'iznos': str(popust),
                'postotak': pct_label,
            })
    if bez_dostave:
        popust_detalji.append({
            'opis': 'Bez dostave',
            'iznos': '0.00',
            'bez_dostave': True,
        })
    ukupno = medjuzbir - popust + dostava
    if ukupno < 0:
        ukupno = Decimal('0.00')
    status = Order.Status.REZERVACIJA if rezervacija else Order.Status.NOVA
    if existing is not None:
        _clear_order_items_and_holds(existing, user=request.user)
        existing.ime_prezime = ime[:200]
        existing.email = email[:254]
        existing.telefon = telefon[:30]
        existing.adresa = adresa[:300]
        existing.grad = grad[:100]
        existing.postanski_broj = (request.POST.get('postanski_broj') or '').strip()[:20]
        existing.napomena = napomena
        existing.medjuzbir = medjuzbir
        existing.dostava = dostava
        existing.popust = popust
        existing.popust_detalji = popust_detalji
        existing.ukupno = ukupno
        existing.status = status
        if loyalty_coupon:
            existing.kupon_kod = loyalty_coupon.kod
        existing.save()
        order = existing
    else:
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
            popust=popust,
            popust_detalji=popust_detalji,
            ukupno=ukupno,
            status=status,
            izvor=Order.Izvor.MAGACIN,
            kupon_kod=loyalty_coupon.kod if loyalty_coupon else '',
        )
    for line in lines:
        if line.get('rezervni'):
            naziv = (line.get('naziv') or 'Rezervni dio')[:200]
            spare_product = line.get('product')
            OrderItem.objects.create(
                narudzba=order,
                artikal=spare_product,
                varijacija=None,
                naziv=naziv,
                product_naziv=(spare_product.naziv[:200] if spare_product else naziv),
                varijacija_naziv=naziv[:100],
                sifra=((spare_product.sifra if spare_product else '') or 'REZERVNI')[:200],
                cijena=line['cijena'],
                bazna_cijena=line['bazna'],
                kolicina=line['qty'],
                rezervni_dio=True,
            )
            leftover = reserve_for_order(
                order,
                spare_product,
                line['qty'] - line['shortfall'],
                variation=None,
                user=request.user,
                napomena=f'Rezervacija #{order.broj}',
            )
            if leftover and not line['mp_ok']:
                raise MagacinError(f'Nije rezervisana puna količina za {spare_product.naziv}.')
            continue
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
    invalidate_magacin_nav_counts()
    return order


@login_required(login_url='login')
@user_passes_test(_superuser_required)
def magacin_pakovanje(request):
    from .views import _build_order_packing_lines

    query = (request.GET.get('pretraga') or request.GET.get('q') or '').strip()
    selected_broj = (request.GET.get('broj') or '').strip().lstrip('#')
    orders = []
    if query:
        digits = ''.join(ch for ch in query if ch.isdigit())
        filt = (
            Q(broj__icontains=query.lstrip('#'))
            | Q(ime_prezime__icontains=query)
            | Q(telefon__icontains=query)
        )
        if digits and digits != query:
            filt |= Q(broj__icontains=digits) | Q(telefon__icontains=digits)
        orders = list(
            Order.objects.exclude(status=Order.Status.OTKAZANA)
            .filter(filt)
            .prefetch_related('stavke')
            .order_by('-kreirana')[:80]
        )
        if len(orders) == 1 and not selected_broj:
            selected_broj = orders[0].broj

    packing_order = None
    packing_lines = []
    odoo_error = ''
    datum = ''
    vrijeme = ''
    if selected_broj:
        packing_order = get_object_or_404(
            Order.objects.exclude(status=Order.Status.OTKAZANA).prefetch_related('stavke'),
            broj=selected_broj,
        )
        packing_lines, odoo_error = _build_order_packing_lines(packing_order)
        packing_order.is_vp = is_vp_order(packing_order)
        created = timezone.localtime(packing_order.kreirana)
        datum = created.strftime('%d.%m.%Y.')
        vrijeme = created.strftime('%H:%M')

    context = _magacin_context(request, section='pakovanje', page_title='Pretraga narudžbi — Magacin')
    context.update({
        'orders': orders,
        'order_query': query,
        'selected_broj': selected_broj,
        'order': packing_order,
        'packing_lines': packing_lines,
        'odoo_error': odoo_error,
        'datum': datum,
        'vrijeme': vrijeme,
    })
    return render(request, 'staff/magacin/pakovanje.html', context)


def _location_sort_key(name):
    """Abeceda + prirodni broj: A-2 prije A-10, pa B-1."""
    text = (name or '').strip()
    parts = []
    for part in re.split(r'(\d+)', text.casefold()):
        if not part:
            continue
        parts.append((1, int(part)) if part.isdigit() else (0, part))
    return parts


def _packing_location_groups(lines):
    groups = {}
    mp_items = []
    mp_confirmed_items = []
    nije_items = []
    for line in lines:
        picks = line.get('picks') or []
        for pick in picks:
            name = pick.get('location_name') or '?'
            row = {
                'line_id': line.get('rb'),
                'item_id': line.get('item_id'),
                'naziv': line['naziv'],
                'sifra': line.get('sifra') or '',
                'barkod': line.get('barkod') or '',
                'slika': line.get('slika') or '',
                'brend': line.get('brend') or '',
                'kategorija': line.get('kategorija') or '',
                'take': pick.get('take') or line.get('kolicina'),
                'on_hand': pick.get('on_hand') or 0,
                'loc_path': pick.get('location_path') or '',
                'kolicina': line.get('kolicina'),
                'rezervni': bool(line.get('rezervni') or name == 'Rezervni dio'),
                'nije_popisan': name == 'Nije popisan',
            }
            if name == 'MP':
                mp_confirmed_items.append(row)
            elif name == 'Nije popisan':
                nije_items.append(row)
            else:
                groups.setdefault(name, []).append(row)
        if line.get('check_mp'):
            take = line.get('shortfall') or (0 if picks else line.get('kolicina') or 0)
            if take:
                mp_items.append({
                    'line_id': line.get('rb'),
                    'item_id': line.get('item_id'),
                    'naziv': line['naziv'],
                    'sifra': line.get('sifra') or '',
                    'barkod': line.get('barkod') or '',
                    'slika': line.get('slika') or '',
                    'brend': line.get('brend') or '',
                    'kategorija': line.get('kategorija') or '',
                    'take': take,
                    'on_hand': 0,
                    'loc_path': 'Maloprodaja',
                    'kolicina': line.get('kolicina'),
                })

    def _number_items(items):
        items.sort(key=lambda item: (
            (item.get('naziv') or '').casefold(),
            item.get('sifra') or '',
            item.get('line_id') or 0,
        ))
        for index, item in enumerate(items, start=1):
            item['rb'] = index
        return items

    ordered = []
    for index, name in enumerate(sorted(groups, key=_location_sort_key), start=1):
        ordered.append({
            'label': name,
            'rb': index,
            'rb_label': f'{index:02d}',
            'items': _number_items(groups[name]),
        })
    if mp_items:
        index = len(ordered) + 1
        ordered.append({
            'label': 'Provjeri u MP',
            'rb': index,
            'rb_label': f'{index:02d}',
            'items': _number_items(mp_items),
        })
    if mp_confirmed_items:
        index = len(ordered) + 1
        ordered.append({
            'label': 'MP',
            'rb': index,
            'rb_label': f'{index:02d}',
            'items': _number_items(mp_confirmed_items),
        })
    if nije_items:
        index = len(ordered) + 1
        ordered.append({
            'label': 'Nije popisan',
            'rb': index,
            'rb_label': f'{index:02d}',
            'items': _number_items(nije_items),
        })
    return ordered


def _pick_queue(location_groups):
    from .models import WarehouseLocation, WarehouseStock, OrderItem

    loc_by_sifra = {
        loc.sifra: loc
        for loc in WarehouseLocation.objects.all()
    }
    queue = []
    index = 0
    item_ids = [
        item.get('item_id')
        for loc in location_groups
        for item in loc.get('items') or []
        if item.get('item_id')
    ]
    order_items = {
        row.pk: row
        for row in OrderItem.objects.filter(pk__in=[i for i in item_ids if i]).select_related(
            'artikal', 'artikal__brend', 'artikal__kategorija', 'varijacija',
        )
    } if item_ids else {}
    for loc in location_groups:
        if loc['label'] == 'Provjeri u MP':
            continue
        loc_obj = loc_by_sifra.get(loc['label'])
        loc_path = ''
        if loc['label'] in {'MP', 'Provjeri u MP'}:
            loc_path = 'Maloprodaja'
        elif loc['label'] == 'Nije popisan':
            loc_path = 'Nije popisan'
        elif loc['label'] == 'Rezervni dio':
            loc_path = 'Slanje rezervnog dijela'
        elif loc_obj:
            loc_path = loc_obj.odoo_location_path or loc_obj.naziv or ''
        for item in loc['items']:
            index += 1
            codes = []
            for raw in (item.get('sifra'), item.get('barkod')):
                text = (raw or '').strip()
                if text and text.casefold() not in [c.casefold() for c in codes]:
                    codes.append(text)
            item_id = item.get('item_id')
            on_hand = int(item.get('on_hand') or 0)
            oi = order_items.get(item_id)
            if loc_obj and oi and oi.artikal_id:
                stock = WarehouseStock.objects.filter(
                    location=loc_obj,
                    product_id=oi.artikal_id,
                    variation_id=oi.varijacija_id,
                ).first()
                if stock is not None:
                    on_hand = int(stock.kolicina or 0)
            brend = item.get('brend') or ''
            kategorija = item.get('kategorija') or ''
            if oi and oi.artikal:
                if not brend and oi.artikal.brend_id:
                    brend = oi.artikal.brend.naziv or ''
                if not kategorija and oi.artikal.kategorija_id:
                    kategorija = oi.artikal.kategorija.naziv or ''
            queue.append({
                'key': f"{item_id}:{loc['label']}" if item_id else f"{loc['label']}-{item['line_id']}-{item['rb']}",
                'i': index,
                'item_id': item_id,
                'loc': loc['label'],
                'loc_path': loc_path or item.get('loc_path') or '',
                'loc_rb': loc['rb_label'],
                'rb': item['rb'],
                'naziv': item['naziv'],
                'sifra': item.get('sifra') or '',
                'barkod': item.get('barkod') or '',
                'slika': item.get('slika') or '',
                'brend': brend,
                'kategorija': kategorija,
                'need': int(item.get('take') or 0),
                'on_hand': on_hand,
                'codes': codes,
                'is_mp': loc['label'] in {'MP', 'Provjeri u MP'},
                'nije_popisan': loc['label'] == 'Nije popisan' or bool(item.get('nije_popisan')),
                'rezervni': bool(item.get('rezervni') or loc['label'] == 'Rezervni dio'),
            })
    return queue


def _prenos_scan_codes(item):
    if item is None:
        return []
    codes = []
    product = getattr(item, 'artikal', None)
    variation = getattr(item, 'varijacija', None)
    for raw in (
        getattr(item, 'sifra', '') or '',
        getattr(variation, 'sifra', '') or '' if variation else '',
        getattr(product, 'sifra', '') or '' if product else '',
        getattr(product, 'barkod', '') or '' if product else '',
        getattr(variation, 'barkod', '') or '' if variation else '',
    ):
        text = str(raw or '').strip()
        if text and text.casefold() not in [c.casefold() for c in codes]:
            codes.append(text)
    return codes


def _order_pick_bundle(order):
    from .views import _build_order_packing_lines

    lines, error = _build_order_packing_lines(order)
    groups = _packing_location_groups(lines)
    return _pick_queue(groups), groups, error


def _mp_group_key(item):
    sifra = (item.get('sifra') or '').strip().casefold()
    if sifra:
        return f's:{sifra}'
    return f'n:{(item.get("naziv") or "").strip().casefold()}'


def collect_mp_checks(orders=None):
    """Artikli bez zalihe (Provjeri u MP) — samo lokalni Magacin, bez Odoo poziva.

    Online narudžbe nemaju rezervaciju; slobodna magacinska zaliha se i dalje
    uzima s lokacije, ne šalje u MP. Nije popisan se ne šalje na provjeru.
    """
    from .magacin import order_has_nije_popisan

    if orders is None:
        orders = list(
            _unvalidated_orders_qs()
            .prefetch_related('stavke__artikal', 'stavke__varijacija', 'magacin_holds')
            .order_by('-kreirana')[:200]
        )
    grouped = {}
    remaining_avail = {}

    def _cover_from_warehouse(product, variation, qty):
        if product is None or qty <= 0:
            return 0
        key = (product.pk, getattr(variation, 'pk', None))
        if key not in remaining_avail:
            remaining_avail[key] = display_stock_totals(product, variation)['dostupno']
        take = min(qty, remaining_avail[key])
        remaining_avail[key] -= take
        return take

    for order in orders:
        state = order.pick_state or {}
        hold_qty = {}
        for hold in order.magacin_holds.all():
            if hold.status == 'otkazano':
                continue
            hkey = (hold.product_id, hold.variation_id)
            hold_qty[hkey] = hold_qty.get(hkey, 0) + int(hold.kolicina or 0)
        for item in order.stavke.all():
            if getattr(item, 'rezervni_dio', False):
                continue
            reserved = hold_qty.get((item.artikal_id, item.varijacija_id), 0)
            if reserved <= 0 and item.varijacija_id:
                reserved = hold_qty.get((item.artikal_id, None), 0)
            short = max(0, int(item.kolicina or 0) - reserved)
            short -= _cover_from_warehouse(item.artikal, item.varijacija, short)
            if short <= 0:
                continue
            if order_has_nije_popisan(order, item):
                continue
            pick_key = f'{item.pk}:Provjeri u MP'
            saved = state.get(pick_key) or {}
            if saved.get('done') or saved.get('mp_checked'):
                continue
            slika = ''
            product = getattr(item, 'artikal', None)
            if product is not None:
                img = product.prikazna_slika
                if img:
                    try:
                        slika = img.url
                    except ValueError:
                        slika = ''
            row = {
                'naziv': item.product_naziv or item.naziv,
                'sifra': item.sifra or '',
                'barkod': '',
                'slika': slika,
                'need': short,
                'item_id': item.pk,
                'key': pick_key,
            }
            key = _mp_group_key(row)
            group = grouped.setdefault(key, {
                'key': key,
                'naziv': row['naziv'],
                'sifra': row['sifra'],
                'barkod': '',
                'slika': slika,
                'need': 0,
                'lines': [],
            })
            if slika and not group.get('slika'):
                group['slika'] = slika
            group['need'] += short
            group['lines'].append({
                'broj': order.broj,
                'ime': order.ime_prezime,
                'telefon': order.telefon or '',
                'item_id': item.pk,
                'key': pick_key,
                'need': short,
            })
    return list(grouped.values())


def pending_mp_brojevi(mp_groups):
    brojevi = set()
    for group in mp_groups or []:
        for line in group.get('lines') or []:
            broj = (line.get('broj') or '').strip()
            if broj:
                brojevi.add(broj)
    return brojevi


def order_needs_mp_check(order):
    return bool(collect_mp_checks([order]))


def _provjera_url(broj=None, *, next_print=False, next_pick=False):
    url = reverse('staff_magacin_pakuj_provjera')
    params = {}
    if broj:
        params['narudzba'] = broj
    if next_print:
        params['next'] = 'stampa'
    elif next_pick:
        params['next'] = 'pick'
    if params:
        url = f'{url}?{urlencode(params)}'
    return url


def collect_mp_customers(groups=None, *, next_print=False, next_pick=True):
    if groups is None:
        groups = collect_mp_checks()
    by_broj = {}
    for group in groups or []:
        for line in group.get('lines') or []:
            broj = (line.get('broj') or '').strip()
            if not broj:
                continue
            row = by_broj.setdefault(broj, {
                'broj': broj,
                'ime': line.get('ime') or '',
                'telefon': line.get('telefon') or '',
                'need': 0,
                'artikala': 0,
                'open_url': '',
            })
            if not row['ime']:
                row['ime'] = line.get('ime') or ''
            if not row['telefon']:
                row['telefon'] = line.get('telefon') or ''
            row['need'] += int(line.get('need') or 0)
            row['artikala'] += 1
    customers = list(by_broj.values())
    for row in customers:
        row['open_url'] = _provjera_url(
            row['broj'],
            next_print=next_print,
            next_pick=next_pick and not next_print,
        )
    customers.sort(key=lambda item: ((item.get('ime') or '').casefold(), item.get('broj') or ''))
    return customers


def _after_order_created_redirect(request, order):
    messages.success(request, f'Narudžba #{order.broj} je kreirana.')
    return redirect('staff_magacin_narudzbe')


def _after_mp_check_done_redirect(request, *, focus_order, focus_broj, next_print):
    if next_print:
        if focus_broj:
            messages.success(
                request,
                f'Narudžba #{focus_broj} je provjerena. Sada možeš štampati.',
            )
            return redirect('staff_magacin_narudzbe')
        return redirect('staff_magacin_pakuj_provjera')
    messages.success(request, 'Artikli u MP su provjereni. Sada možeš picking narudžbi.')
    return redirect('staff_magacin_pakuj')


def _pick_claim_name(order):
    return (order.pick_claimed_name or '').strip() or (
        _user_display(order.pick_claimed_by) if order.pick_claimed_by_id else ''
    )


def claim_order_pick(order, user):
    """Prvi sken/otvaranje preuzima nalog. Drugi korisnik ne može ući."""
    if not user or not getattr(user, 'is_authenticated', False):
        return False, ''
    if order.pick_claimed_by_id and order.pick_claimed_by_id != user.pk:
        return False, _pick_claim_name(order)
    if order.pick_claimed_by_id == user.pk:
        return True, _pick_claim_name(order)
    order.pick_claimed_by = user
    order.pick_claimed_at = timezone.now()
    order.pick_claimed_name = _user_display(user)[:120]
    order.save(update_fields=['pick_claimed_by', 'pick_claimed_at', 'pick_claimed_name'])
    return True, order.pick_claimed_name


def release_order_pick(order):
    """Skini preuzimanje da nalog može uzeti neko drugi."""
    if not order.pick_claimed_by_id and not (order.pick_claimed_name or '').strip():
        return False, ''
    name = _pick_claim_name(order)
    order.pick_claimed_by = None
    order.pick_claimed_at = None
    order.pick_claimed_name = ''
    order.save(update_fields=['pick_claimed_by', 'pick_claimed_at', 'pick_claimed_name'])
    return True, name


def _pakuj_zauzeto_url(broj):
    return f"{reverse('staff_magacin_pakuj')}?{urlencode({'zauzeto': broj})}"


def apply_mp_check(group_lines, *, found, user=None, found_qty=None):
    by_broj = {}
    for line in group_lines:
        by_broj.setdefault(line['broj'], []).append(line)
    remaining = None if found_qty is None else max(0, int(found_qty))
    if found and remaining == 0:
        found = False
    for broj, lines in by_broj.items():
        order = Order.objects.filter(broj=broj).first()
        if not order:
            continue
        state = dict(order.pick_state or {})
        if found:
            for line in lines:
                need = int(line.get('need') or 0)
                take = need if remaining is None else min(need, remaining)
                if remaining is not None:
                    remaining -= take
                state[line['key']] = {
                    'got': 0,
                    'done': False,
                    'mp_checked': True,
                    'mp_found': take,
                    'item_id': line.get('item_id'),
                    'need': need,
                }
                item = order.stavke.filter(pk=line.get('item_id')).first()
                if not item:
                    continue
                reserved = max(0, int(item.kolicina or 0) - need)
                invoice_qty = reserved + take
                if invoice_qty < int(item.kolicina or 0):
                    item.kolicina_pokupljeno = invoice_qty
                    item.save(update_fields=['kolicina_pokupljeno'])
            order.pick_state = state
            order.save(update_fields=['pick_state'])
            continue
        cancelled = False
        for line in lines:
            item = order.stavke.filter(pk=line.get('item_id')).first()
            if not item:
                continue
            if OrderItem.objects.filter(narudzba_id=order.pk).count() <= 1:
                try:
                    cancel_order_stock(order, user=user)
                except MagacinError:
                    pass
                cancelled = True
                break
            try:
                remove_item_from_order(order, item, user=user)
            except MagacinError:
                continue
        if cancelled:
            continue
        order.refresh_from_db()
        state = dict(order.pick_state or {})
        for line in lines:
            state.pop(line.get('key'), None)
        order.pick_state = state
        order.save(update_fields=['pick_state'])
    invalidate_magacin_nav_counts()


def _parse_pick_lines(raw):
    if isinstance(raw, list):
        return raw
    text = (raw or '').strip()
    if not text:
        return []
    data = json.loads(text)
    if isinstance(data, dict):
        data = data.get('lines') or []
    if not isinstance(data, list):
        raise MagacinError('Picking podaci nisu validni.')
    return data


def _pick_line_loc(raw):
    loc = str((raw or {}).get('loc') or '').strip()
    if loc:
        return loc
    key = str((raw or {}).get('key') or '')
    if ':' in key:
        return key.split(':', 1)[1].strip()
    return ''


def apply_order_pick(order, lines, *, finalize=False, user=None):
    """Sačuvaj picking i postavi količinu za fakturu.

    finalize=True (završi picking): stavke s 0 pokupljenih se skidaju s narudžbe
    (nema artikla). Ostale dobiju pokupljenu količinu za račun.
    """
    state = dict(order.pick_state or {})
    picked_by_item = {}
    missing_lines = []
    for raw in lines or []:
        try:
            item_id = int(raw.get('item_id') or 0)
        except (TypeError, ValueError):
            item_id = 0
        try:
            got = max(0, int(raw.get('got') or 0))
            need = max(0, int(raw.get('need') or 0))
        except (TypeError, ValueError):
            continue
        done = bool(raw.get('done'))
        if need:
            got = min(got, need)
        key = str(raw.get('key') or '')
        if not key:
            continue
        prev = state.get(key) if isinstance(state.get(key), dict) else {}
        row = {'got': got, 'done': done, 'item_id': item_id or None, 'need': need}
        loc = _pick_line_loc(raw)
        if loc:
            row['loc'] = loc
        if prev.get('mp_checked') or raw.get('mp_checked'):
            row['mp_checked'] = True
        state[key] = row
        if not item_id:
            continue
        if finalize and got == 0 and need > 0:
            missing_lines.append((item_id, _pick_line_loc(raw), need))
        if not finalize and not done:
            continue
        picked_by_item[item_id] = picked_by_item.get(item_id, 0) + got

    order.pick_state = state
    order.save(update_fields=['pick_state'])

    def _drop_zero(item, loc, qty):
        if is_prenos_mp_order(order):
            try:
                cancelled = drop_prenos_mp_item(order, item, user=user)
            except MagacinError:
                cancelled = False
            return {'cancelled': cancelled, 'removed': True}
        try:
            return drop_missing_pick_line(
                order, item, loc=loc, qty=qty, user=user,
            )
        except MagacinError:
            if OrderItem.objects.filter(narudzba_id=order.pk).count() <= 1:
                try:
                    cancel_order_stock(order, user=user)
                except MagacinError:
                    order.lager_status = Order.LagerStatus.OTKAZANO
                    order.status = Order.Status.OTKAZANA
                    order.save(update_fields=['lager_status', 'status'])
                return {'cancelled': True, 'removed': True}
            try:
                remove_item_from_order(order, item, user=user)
            except MagacinError:
                item_id = item.pk
                item.delete()
                order.refresh_from_db()
            return {'cancelled': False, 'removed': True}

    if finalize:
        for item_id, loc, qty in missing_lines:
            item = OrderItem.objects.filter(pk=item_id, narudzba=order).first()
            if item is None:
                continue
            result = _drop_zero(item, loc, qty)
            if result.get('cancelled'):
                return state

    items = {item.pk: item for item in order.stavke.all()}
    if finalize:
        for item in items.values():
            if item.pk not in picked_by_item:
                continue
            qty = max(0, min(int(item.kolicina), int(picked_by_item.get(item.pk, 0))))
            if qty <= 0:
                result = _drop_zero(item, '', int(item.kolicina or 0))
                if result.get('cancelled'):
                    return state
                continue
            item.kolicina_pokupljeno = qty
            item.save(update_fields=['kolicina_pokupljeno'])
    else:
        for item_id, qty in picked_by_item.items():
            item = items.get(item_id)
            if not item:
                continue
            qty = max(0, min(int(item.kolicina), int(qty)))
            item.kolicina_pokupljeno = qty
            item.save(update_fields=['kolicina_pokupljeno'])
    return state


@login_required(login_url='login')
@user_passes_test(_superuser_required)
@require_GET
def magacin_narudzba_barkod(request, broj):
    from django.http import HttpResponse
    from .loyalty import generisi_loyalty_barcode_png

    order = get_object_or_404(Order, broj=broj)
    png = generisi_loyalty_barcode_png(order.barkod)
    response = HttpResponse(png, content_type='image/png')
    response['Content-Disposition'] = f'inline; filename="narudzba-{order.broj}-barkod.png"'
    response['Cache-Control'] = 'private, max-age=300'
    return response


@login_required(login_url='login')
@user_passes_test(_superuser_required)
@require_GET
def magacin_pakuj_sken(request):
    raw = (request.GET.get('q') or '').strip()
    order = find_order_by_scan(raw, qs=_unvalidated_orders_qs())
    if not order:
        messages.warning(request, f'Narudžba za barkod „{raw or "—"}” nije na pickingu.')
        return redirect('staff_magacin_pakuj')
    if order_needs_mp_check(order):
        messages.warning(
            request,
            f'Narudžba #{order.broj} ima artikal iz maloprodaje. '
            'Prvo Artikli u MP.',
        )
        return redirect(_provjera_url(order.broj, next_pick=True))
    ok, holder = claim_order_pick(order, request.user)
    if not ok:
        messages.error(
            request,
            f'Narudžbu #{order.broj} je već preuzeo {holder or "drugi radnik"}.',
        )
        return redirect(_pakuj_zauzeto_url(order.broj))
    return redirect('staff_magacin_pakuj_detail', broj=order.broj)


def pending_prenos_mp_jobs():
    orders = (
        _unvalidated_orders_qs()
        .filter(ime_prezime='Prenos u MP')
        .prefetch_related('stavke__artikal', 'magacin_holds__location')
        .order_by('kreirana')
    )
    jobs = []
    for order in orders:
        items = list(order.stavke.all())
        item = items[0] if items else None
        hold = order.magacin_holds.first()
        product = item.artikal if item else None
        slika = ''
        if product is not None:
            img = product.prikazna_slika
            if img:
                try:
                    slika = img.url
                except ValueError:
                    slika = ''
        qty = sum(int(row.kolicina or 0) for row in items)
        jobs.append({
            'order': order,
            'naziv': (
                item.naziv if len(items) == 1 and item
                else f'Prenos u MP ({len(items)} stavki)'
            ),
            'sifra': (item.sifra if item and len(items) == 1 else ''),
            'qty': qty,
            'stavki': len(items),
            'lokacija': hold.location.sifra if hold and hold.location else '',
            'slika': slika,
        })
    return jobs


def pending_vp_orders():
    order_ids = list(
        MagacinVpNarudzba.objects.filter(
            status=MagacinVpNarudzba.Status.ZAVRSENA,
            order_id__isnull=False,
        ).values_list('order_id', flat=True)
    )
    if not order_ids:
        return []
    orders = list(
        Order.objects.filter(pk__in=order_ids)
        .exclude(status=Order.Status.OTKAZANA)
        .exclude(zapakovana=True)
        .prefetch_related('stavke')
        .order_by('kreirana')
    )
    ready = []
    for order in orders:
        if (
            order.lager_status != Order.LagerStatus.VALIDIRANO
            and order_needs_mp_check(order)
        ):
            continue
        order.vp_stavki = order.stavke.count()
        order.needs_print_packed = order.lager_status == Order.LagerStatus.VALIDIRANO
        ready.append(order)
    return ready


def _order_pick_status(order):
    if (
        order.lager_status == Order.LagerStatus.VALIDIRANO
        or order.status == Order.Status.ZAVRSENA
    ):
        return 'zavrseno'
    claimed = bool(order.pick_claimed_by_id or (order.pick_claimed_name or '').strip())
    state = order.pick_state if isinstance(order.pick_state, dict) else {}
    progressed = False
    for row in state.values():
        if isinstance(row, dict) and (row.get('done') or int(row.get('got') or 0) > 0):
            progressed = True
            break
    if claimed or progressed:
        return 'u_toku'
    return 'ceka'


def collect_pick_jobs():
    jobs = []
    seen = set()
    qs = list(
        _unvalidated_orders_qs()
        .prefetch_related('stavke', 'magacin_holds')
        .order_by('-kreirana')
    )
    locked = pending_mp_brojevi(collect_mp_checks(qs))
    for order in qs:
        if not is_prenos_mp_order(order) and (
            order.broj in locked or order_needs_mp_check(order)
        ):
            continue
        order.pick_status = _order_pick_status(order)
        order.stavki = order.stavke.count()
        order.pick_open_url = reverse('staff_magacin_pakuj_detail', args=[order.broj])
        jobs.append(order)
        seen.add(order.pk)
    done_qs = (
        _completed_pick_qs()
        .prefetch_related('stavke')
        .order_by(F('zapakovana_at').desc(nulls_last=True), '-kreirana')[:80]
    )
    for order in done_qs:
        if order.pk in seen:
            continue
        order.pick_status = 'zavrseno'
        order.stavki = order.stavke.count()
        order.pick_open_url = reverse('staff_magacin_pakuj_detail', args=[order.broj])
        jobs.append(order)
    return jobs


@login_required(login_url='login')
@user_passes_test(_superuser_required)
def magacin_pakuj(request):
    claimed_order = None
    zauzeto = (request.GET.get('zauzeto') or '').strip()
    if zauzeto:
        claimed_order = (
            _unvalidated_orders_qs()
            .filter(broj=zauzeto)
            .exclude(pick_claimed_name='')
            .first()
        )
        if claimed_order is None:
            claimed_order = (
                _unvalidated_orders_qs()
                .filter(broj=zauzeto, pick_claimed_by__isnull=False)
                .first()
            )
    explicit_status = (request.GET.get('status') or '').strip()
    status_filter = explicit_status or 'ceka'
    query = (request.GET.get('pretraga') or request.GET.get('q') or '').strip()
    jobs = collect_pick_jobs()
    counts = {
        'sve': 0,
        'ceka': 0,
        'u_toku': 0,
        'zavrseno': 0,
    }
    for job in jobs:
        if job.pick_status in counts:
            counts[job.pick_status] += 1
    counts['zavrseno'] = _completed_pick_qs().count()
    counts['sve'] = counts['ceka'] + counts['u_toku'] + counts['zavrseno']
    if query:
        q = query.casefold()
        q_digits = ''.join(ch for ch in query if ch.isdigit())
        jobs = [
            job for job in jobs
            if q in (job.broj or '').casefold()
            or q.lstrip('#') in (job.broj or '').casefold()
            or q in (job.ime_prezime or '').casefold()
            or q in (job.telefon or '').casefold()
            or (q_digits and q_digits in ''.join(ch for ch in (job.telefon or '') if ch.isdigit()))
            or (q_digits and q_digits in (job.broj or ''))
        ]
    if status_filter in {'ceka', 'u_toku', 'zavrseno'}:
        jobs = [job for job in jobs if job.pick_status == status_filter]
    context = _magacin_context(request, section='pakuj', page_title='Picking — Magacin')
    context.update({
        'pick_fullscreen': False,
        'prenos_mp_jobs': pending_prenos_mp_jobs(),
        'vp_orders': pending_vp_orders(),
        'mp_pending_count': len(collect_mp_customers()),
        'claimed_order': claimed_order,
        'pick_jobs': jobs,
        'pick_status_filter': status_filter,
        'pick_query': query,
        'pick_counts': counts,
    })
    return render(request, 'staff/magacin/pakuj.html', context)


@login_required(login_url='login')
@user_passes_test(_superuser_required)
@require_POST
def magacin_pakuj_oslobodi(request, broj):
    order = get_object_or_404(_unvalidated_orders_qs(), broj=broj)
    released, holder = release_order_pick(order)
    if released:
        messages.success(
            request,
            f'Skinuto preuzimanje #{order.broj}'
            + (f' ({holder})' if holder else '')
            + '. Sada je može preuzeti neko drugi.',
        )
    else:
        messages.info(request, f'Narudžba #{order.broj} nije preuzeta.')
    next_url = (request.POST.get('next') or '').strip()
    if not next_url:
        next_url = reverse('staff_magacin_pakuj')
    return redirect(next_url)


@login_required(login_url='login')
@user_passes_test(_superuser_required)
def magacin_pakuj_provjera(request):
    focus_broj = (request.POST.get('narudzba') or request.GET.get('narudzba') or '').strip()
    next_dest = (request.POST.get('next') or request.GET.get('next') or '').strip()
    next_print = next_dest == 'stampa'
    next_pick = not next_print
    focus_order = None
    if focus_broj:
        focus_order = (
            _unvalidated_orders_qs()
            .prefetch_related('stavke__artikal', 'magacin_holds')
            .filter(broj=focus_broj)
            .first()
        )
        if focus_order is None:
            focus_broj = ''

    if request.method == 'POST':
        key = (request.POST.get('group') or '').strip()
        found = (request.POST.get('action') or '') == 'ima'
        source = collect_mp_checks([focus_order] if focus_order else None)
        group = next((row for row in source if row['key'] == key), None)
        if not group:
            messages.error(request, 'Stavka za provjeru nije pronađena.')
        else:
            lines = group['lines']
            if focus_order:
                lines = [line for line in lines if line.get('broj') == focus_order.broj]
            found_qty = None
            if found:
                raw_qty = (request.POST.get('kolicina') or '').strip()
                if raw_qty != '':
                    try:
                        found_qty = _parse_qty(raw_qty)
                    except MagacinError as exc:
                        messages.error(request, str(exc))
                        return redirect(_provjera_url(focus_broj, next_print=next_print, next_pick=next_pick))
                    need = int(group.get('need') or 0)
                    if need:
                        found_qty = min(found_qty, need)
            apply_mp_check(lines, found=found, user=request.user, found_qty=found_qty)
            if found and found_qty == 0:
                found = False
            if found:
                extra = ''
                need = int(group.get('need') or 0)
                if found_qty is not None and need and found_qty < need:
                    extra = f' ({found_qty}/{need})'
                messages.success(
                    request,
                    f'{group["naziv"]} — ima u MP{extra}, ubačeno na narudžbu.',
                )
            else:
                messages.success(request, f'{group["naziv"]} — nema u MP, izbačeno s narudžbe.')
        if focus_order:
            focus_order = (
                _unvalidated_orders_qs()
                .prefetch_related('stavke__artikal', 'magacin_holds')
                .filter(pk=focus_order.pk)
                .first()
            )
            leftover_focus = collect_mp_checks([focus_order]) if focus_order else []
            if leftover_focus:
                return redirect(_provjera_url(focus_broj, next_print=next_print, next_pick=next_pick))
            if next_print:
                return _after_mp_check_done_redirect(
                    request,
                    focus_order=focus_order,
                    focus_broj=focus_broj,
                    next_print=True,
                )
            leftover_all = collect_mp_checks()
            if leftover_all and focus_order:
                messages.success(
                    request,
                    f'{focus_order.ime_prezime} #{focus_broj} je spreman za picking. '
                    'Ostali kupci ostaju na provjeri MP.',
                )
                return redirect('staff_magacin_pakuj')
            return _after_mp_check_done_redirect(
                request,
                focus_order=focus_order,
                focus_broj=focus_broj,
                next_print=False,
            )
        leftover_all = collect_mp_checks()
        if leftover_all:
            return redirect(_provjera_url(next_pick=True))
        return _after_mp_check_done_redirect(
            request,
            focus_order=None,
            focus_broj='',
            next_print=False,
        )

    customers = []
    groups = []
    if focus_order:
        groups = collect_mp_checks([focus_order])
        if not groups:
            if next_print:
                messages.success(request, f'Narudžba #{focus_broj} nema više stavki za Provjeru MP.')
                return redirect('staff_magacin_narudzbe')
            return redirect('staff_magacin_pakuj')
    else:
        customers = collect_mp_customers(next_print=next_print, next_pick=next_pick)
        if not customers:
            if next_print and focus_broj:
                messages.success(request, f'Narudžba #{focus_broj} nema više stavki za Provjeru MP.')
                return redirect('staff_magacin_narudzbe')
            return redirect('staff_magacin_pakuj')

    context = _magacin_context(
        request,
        section='pakuj',
        page_title='Artikli u MP — Magacin' if not next_print else 'Provjera MP — Magacin',
    )
    context.update({
        'groups': groups,
        'customers': customers,
        'mp_count': len(customers) if customers else len(groups),
        'pick_fullscreen': False,
        'focus_broj': focus_broj,
        'next_print': next_print,
        'next_pick': next_pick,
        'focus_order': focus_order,
    })
    return render(request, 'staff/magacin/pakuj_provjera.html', context)


def _item_pick_label(item):
    if item is None:
        return 'Artikal'
    if getattr(item, 'rezervni_dio', False):
        parent = ''
        if item.artikal_id:
            parent = item.artikal.naziv or ''
        parent = parent or item.product_naziv or ''
        part = (item.naziv or '').strip()
        if parent and part and part.casefold() != parent.casefold():
            return f'{parent} — {part}'
        return parent or part or 'Rezervni dio'
    return item.puni_naziv or item.naziv or item.product_naziv or 'Artikal'


def _completed_pick_rows(order):
    items = {
        item.pk: item
        for item in order.stavke.select_related('artikal', 'varijacija')
    }
    rows = []
    from_state = _picks_from_pick_state(order)
    if from_state:
        for item_id, picks in from_state.items():
            item = items.get(item_id)
            for pick in picks:
                qty = int(pick.get('take') or 0)
                if qty <= 0:
                    continue
                loc = (pick.get('location_name') or '').strip() or '—'
                rows.append({
                    'naziv': _item_pick_label(item),
                    'sifra': (item.sifra if item else '') or '',
                    'loc': loc,
                    'qty': qty,
                    'rezervni': bool(item and getattr(item, 'rezervni_dio', False)),
                })
        if rows:
            return rows
    moves = (
        WarehouseMovement.objects.filter(
            tip=WarehouseMovement.Tip.PRODAJA,
            napomena__icontains=f'#{order.broj}',
        )
        .select_related('product', 'location')
        .order_by('location__sifra', 'id')
    )
    for mv in moves:
        qty = abs(int(mv.kolicina or 0))
        if qty <= 0:
            continue
        loc = ''
        if mv.location_id:
            loc = mv.location.sifra or mv.location.naziv or ''
        rows.append({
            'naziv': mv.product.naziv if mv.product_id else 'Artikal',
            'sifra': (mv.product.sifra if mv.product_id else '') or '',
            'loc': loc or '—',
            'qty': qty,
            'rezervni': False,
        })
    if rows:
        return rows
    for item in items.values():
        qty = int(item.kolicina_faktura or 0)
        if qty <= 0:
            continue
        rows.append({
            'naziv': _item_pick_label(item),
            'sifra': item.sifra or '',
            'loc': '—',
            'qty': qty,
            'rezervni': bool(getattr(item, 'rezervni_dio', False)),
        })
    return rows


def _render_completed_pick(request, order):
    rows = _completed_pick_rows(order)
    context = _magacin_context(
        request, section='pakuj', page_title=f'Pick lista #{order.broj} — Magacin',
    )
    context.update({
        'order': order,
        'pick_rows': rows,
        'pick_readonly': True,
        'pick_fullscreen': False,
    })
    return render(request, 'staff/magacin/pakuj_zavrseno.html', context)


@login_required(login_url='login')
@user_passes_test(_superuser_required)
def magacin_pakuj_detail(request, broj):
    order = (
        _unvalidated_orders_qs()
        .prefetch_related('stavke', 'magacin_holds')
        .filter(broj=broj)
        .first()
    )
    if order is None:
        order = (
            _completed_pick_qs()
            .prefetch_related('stavke')
            .filter(broj=broj)
            .first()
        )
        if order is None:
            raise Http404
        if request.method == 'POST':
            messages.info(request, 'Ova pick lista je završena.')
            return redirect('staff_magacin_pakuj_detail', broj=order.broj)
        return _render_completed_pick(request, order)
    prenos_mp = is_prenos_mp_order(order)
    if not prenos_mp and order_needs_mp_check(order):
        if request.method == 'POST' and (request.POST.get('action') or '') == 'pick_save':
            return JsonResponse(
                {'ok': False, 'error': 'Prvo Artikli u MP (Ima / Nema).'},
                status=403,
            )
        messages.warning(request, 'Prvo Artikli u MP, pa picking te narudžbe.')
        return redirect(_provjera_url(order.broj, next_pick=True))
    ok, holder = claim_order_pick(order, request.user)
    if not ok:
        if request.method == 'POST':
            return JsonResponse(
                {'ok': False, 'error': f'Narudžbu je već preuzeo {holder or "drugi radnik"}.'},
                status=403,
            )
        messages.error(
            request,
            f'Narudžbu #{order.broj} je već preuzeo {holder or "drugi radnik"}.',
        )
        return redirect(_pakuj_zauzeto_url(order.broj))
    if request.method == 'POST':
        action = (request.POST.get('action') or '').strip()
        if action == 'otkazi':
            try:
                cancel_order_stock(order, user=request.user)
            except MagacinError as exc:
                messages.error(request, str(exc))
                return redirect('staff_magacin_pakuj_detail', broj=order.broj)
            if prenos_mp:
                messages.success(
                    request,
                    'Prenos u MP je otkazan — artikli su vraćeni na lokacije.',
                )
            else:
                messages.success(
                    request,
                    f'Narudžba #{order.broj} je otkazana — rezervacija je vraćena na lokacije.',
                )
            return redirect('staff_magacin_pakuj')
        if action in {'dodaj', 'ukloni', 'kolicina'}:
            if not order_is_editable(order):
                if _pakuj_is_ajax(request):
                    return JsonResponse(
                        {'ok': False, 'error': 'Narudžba se ne može mijenjati.'},
                        status=400,
                    )
                messages.error(request, 'Narudžba se ne može mijenjati.')
                return redirect('staff_magacin_pakuj_detail', broj=order.broj)
            return _pakuj_edit_order(request, order)
        if action == 'pick_nema':
            try:
                item = get_object_or_404(order.stavke, pk=int(request.POST.get('item_id') or 0))
                loc = (request.POST.get('loc') or '').strip()
                raw_need = request.POST.get('need') or request.POST.get('kolicina') or '0'
                qty = _parse_qty(raw_need)
                result = drop_missing_pick_line(
                    order, item, loc=loc, qty=qty, user=request.user,
                )
            except (MagacinError, ValueError, TypeError) as exc:
                if _pakuj_is_ajax(request):
                    return JsonResponse(
                        {'ok': False, 'error': str(exc) if str(exc) else 'Stavka nije skinuta.'},
                        status=400,
                    )
                messages.error(request, str(exc) if str(exc) else 'Stavka nije skinuta.')
                return redirect('staff_magacin_pakuj_detail', broj=order.broj)
            invalidate_magacin_nav_counts()
            if result.get('cancelled'):
                message = 'Artikal nema — narudžba je otkazana, zaliha je skinuta s lokacije.'
            elif result.get('removed'):
                message = 'Artikal nema — skinut s narudžbe i s lokacije.'
            else:
                message = 'Artikal nema na toj lokaciji — količina je smanjena, zaliha je skinuta.'
            if _pakuj_is_ajax(request):
                payload = {
                    'ok': True,
                    'reload': True,
                    'cancelled': bool(result.get('cancelled')),
                    'removed': bool(result.get('removed')),
                    'message': message,
                }
                if result.get('cancelled'):
                    payload['redirect'] = reverse('staff_magacin_pakuj')
                return JsonResponse(payload)
            messages.success(request, message)
            if result.get('cancelled'):
                return redirect('staff_magacin_pakuj')
            return redirect('staff_magacin_pakuj_detail', broj=order.broj)
        if action == 'pick_ocisti':
            if not _packing_reprint_password_ok(request.POST.get('lozinka')):
                if _pakuj_is_ajax(request):
                    return JsonResponse(
                        {'ok': False, 'error': 'Pogrešna šifra.'},
                        status=403,
                    )
                messages.error(request, 'Pogrešna šifra.')
                return redirect('staff_magacin_pakuj_detail', broj=order.broj)
            try:
                item = get_object_or_404(order.stavke, pk=int(request.POST.get('item_id') or 0))
                loc = (request.POST.get('loc') or '').strip()
                result = clear_pick_location_stock(
                    order, item, loc=loc, user=request.user,
                )
            except (MagacinError, ValueError, TypeError) as exc:
                if _pakuj_is_ajax(request):
                    return JsonResponse(
                        {'ok': False, 'error': str(exc) if str(exc) else 'Lokacija nije očišćena.'},
                        status=400,
                    )
                messages.error(request, str(exc) if str(exc) else 'Lokacija nije očišćena.')
                return redirect('staff_magacin_pakuj_detail', broj=order.broj)
            loc_label = result.get('loc') or loc
            cancelled = False
            product = item.artikal
            if product is not None:
                product.refresh_from_db(fields=['na_stanju', 'stanje'])
            still_on_site = bool(product and product.na_stanju)
            if prenos_mp and not result.get('relocated'):
                item_id = item.pk
                try:
                    cancelled = trim_prenos_mp_item(order, item, user=request.user)
                except MagacinError:
                    cancelled = False
                if cancelled:
                    message = (
                        f'Lokacija {loc_label} očišćena — količine na toj lokaciji su 0. '
                        'Prenos u MP je uklonjen.'
                    )
                elif not order.stavke.filter(pk=item_id).exists():
                    message = (
                        f'Lokacija {loc_label} očišćena — količine na toj lokaciji su 0. '
                        'Stavka je skinuta s prenosa u MP.'
                    )
                else:
                    message = (
                        f'Lokacija {loc_label} očišćena — količine na toj lokaciji su 0. '
                        'Količina na prenosu je usklađena.'
                    )
            elif result.get('relocated'):
                message = (
                    f'Lokacija {loc_label} očišćena — količine na toj lokaciji su 0, '
                    'rezervacija prebačena na drugu lokaciju.'
                )
            else:
                message = f'Lokacija {loc_label} očišćena — količine na toj lokaciji su 0.'
            if still_on_site:
                message += ' Artikal ostaje na sajtu.'
            else:
                message += ' Nema ga ni na jednoj lokaciji — Nije na stanju, skinut sa sajta.'
            if _pakuj_is_ajax(request):
                payload = {
                    'ok': True,
                    'reload': not cancelled,
                    'cleared': int(result.get('cleared') or 0),
                    'relocated': int(result.get('relocated') or 0),
                    'cancelled': cancelled,
                    'message': message,
                }
                if cancelled:
                    payload['redirect'] = reverse('staff_magacin_pakuj')
                return JsonResponse(payload)
            messages.success(request, message)
            if cancelled:
                return redirect('staff_magacin_pakuj')
            return redirect('staff_magacin_pakuj_detail', broj=order.broj)
        if action in {'validiraj', 'pick_save'}:
            try:
                pick_lines = _parse_pick_lines(request.POST.get('pick_json'))
                if prenos_mp and action == 'validiraj' and pick_lines:
                    got = 0
                    for row in pick_lines:
                        try:
                            got += max(0, int(row.get('got') or 0))
                        except (TypeError, ValueError):
                            continue
                    if got <= 0:
                        messages.error(
                            request,
                            'Unesi količinu za prenos ili ukloni iz lokacije.',
                        )
                        return redirect('staff_magacin_pakuj_detail', broj=order.broj)
                apply_order_pick(
                    order,
                    pick_lines,
                    finalize=(action == 'validiraj'),
                    user=request.user,
                )
            except (MagacinError, json.JSONDecodeError, TypeError, ValueError) as exc:
                if action == 'pick_save':
                    return JsonResponse({'ok': False, 'error': str(exc)}, status=400)
                messages.error(request, f'Picking nije sačuvan: {exc}')
                return redirect('staff_magacin_pakuj_detail', broj=order.broj)
            if action == 'pick_save':
                return JsonResponse({'ok': True})
            order.refresh_from_db()
            if order.status == Order.Status.OTKAZANA:
                messages.success(
                    request,
                    f'Narudžba #{order.broj} je otkazana — nijedan artikal nije pokupljen.',
                )
                return redirect('staff_magacin_pakuj')
            try:
                validate_order_stock(order, user=request.user)
                if not is_prenos_mp_order(order):
                    messages.success(request, f'Narudžba #{order.broj} je validatovana.')
                return redirect('staff_magacin_pakuj')
            except MagacinError as exc:
                messages.error(request, str(exc))
                return redirect('staff_magacin_pakuj_detail', broj=order.broj)

    queue, location_groups, odoo_error = _order_pick_bundle(order)
    mp_count = sum(1 for item in queue if item.get('is_mp'))
    prenos_items = list(
        order.stavke.select_related('artikal', 'varijacija')
    ) if prenos_mp else []
    prenos_single = prenos_mp and len(prenos_items) <= 1
    prenos_item = prenos_items[0] if prenos_single and prenos_items else None
    prenos_hold = (
        order.magacin_holds.filter(status=OrderStockHold.Status.REZERVISANO)
        .select_related('location')
        .first()
        if prenos_item else None
    )
    context = _magacin_context(request, section='pakuj', page_title=f'Pick #{order.broj} — Magacin')
    context.update({
        'order': order,
        'location_groups': location_groups,
        'pick_queue_json': json.dumps(queue, ensure_ascii=False).replace('<', '\\u003c'),
        'pick_state_json': json.dumps(order.pick_state or {}, ensure_ascii=False).replace('<', '\\u003c'),
        'pick_total': len(queue),
        'odoo_error': odoo_error,
        'pick_fullscreen': True,
        'mp_count': mp_count,
        'is_prenos_mp': prenos_mp,
        'prenos_item': prenos_item,
        'prenos_hold': prenos_hold,
        'prenos_items': prenos_items,
        'prenos_codes_json': json.dumps(_prenos_scan_codes(prenos_item), ensure_ascii=False).replace('<', '\\u003c'),
        'can_edit_order': order_is_editable(order),
        'edit_form_url': (
            f"{reverse('staff_magacin_narudzba_nova')}?broj={order.broj}"
            if getattr(order, 'izvor', '') == Order.Izvor.MAGACIN and order_is_editable(order)
            else ''
        ),
        'is_vp_order': is_vp_order(order),
        'order_items': list(order.stavke.select_related('artikal', 'varijacija')),
        'lookup_url': reverse('staff_magacin_artikli_lookup'),
    })
    template = 'staff/magacin/pakuj_prenos.html' if prenos_single else 'staff/magacin/pakuj_detail.html'
    return render(request, template, context)


def _pakuj_is_ajax(request):
    return request.headers.get('X-Requested-With') == 'XMLHttpRequest'


def _pakuj_order_payload(order):
    items = list(order.stavke.all())
    return {
        'ok': True,
        'ukupno': str(order.ukupno),
        'medjuzbir': str(order.medjuzbir),
        'reload': True,
        'stavke': [
            {
                'id': row.pk,
                'naziv': row.puni_naziv,
                'sifra': row.sifra or '',
                'kolicina': row.kolicina,
                'cijena': str(row.cijena),
            }
            for row in items
        ],
    }


def _pakuj_need_mp(request, order, product, variation, qty, *, available, naziv):
    message = (
        f'„{naziv}” nema dostupnog artikla ({available}). '
        'Označi Nije popisan da ga dodaš, ili makni stavku.'
    )
    if _pakuj_is_ajax(request):
        return JsonResponse({
            'ok': False,
            'need_mp': True,
            'error': message,
            'available': available,
            'naziv': naziv,
            'product_id': product.pk,
            'variation_id': variation.pk if variation else '',
            'kolicina': qty,
        }, status=409)
    messages.error(request, message)
    return redirect('staff_magacin_pakuj_detail', broj=order.broj)


def _pakuj_edit_order(request, order):
    action = (request.POST.get('action') or '').strip()
    ajax = _pakuj_is_ajax(request)
    try:
        if action == 'dodaj':
            product = get_object_or_404(magacin_products_qs(), pk=int(request.POST.get('product_id') or 0))
            variation = None
            var_id = request.POST.get('variation_id')
            if var_id:
                variation = get_object_or_404(ProductVariation, pk=int(var_id), artikal=product)
            qty = _parse_qty(request.POST.get('kolicina') or '1')
            mp_ok = request.POST.get('mp_ok') == '1'
            available = display_stock_totals(product, variation)['dostupno']
            if qty > available and not mp_ok:
                naziv = f'{product.naziv} {variation.naziv}'.strip() if variation else product.naziv
                return _pakuj_need_mp(
                    request, order, product, variation, qty,
                    available=available, naziv=naziv,
                )
            add_item_to_order(
                order,
                product=product,
                variation=variation,
                qty=qty,
                mp_ok=mp_ok,
                user=request.user,
            )
        elif action == 'kolicina':
            item = get_object_or_404(order.stavke, pk=int(request.POST.get('stavka_id') or 0))
            qty = _parse_qty(request.POST.get('kolicina') or '1')
            mp_ok = request.POST.get('mp_ok') == '1'
            product = item.artikal
            if product is None:
                raise MagacinError('Artikal više ne postoji.')
            delta = qty - int(item.kolicina or 0)
            if delta > 0 and not mp_ok:
                available = display_stock_totals(product, item.varijacija)['dostupno']
                if delta > available:
                    return _pakuj_need_mp(
                        request, order, product, item.varijacija, qty,
                        available=available, naziv=item.puni_naziv,
                    )
            set_order_item_qty(order, item, qty, mp_ok=mp_ok, user=request.user)
        elif action == 'ukloni':
            item = get_object_or_404(order.stavke, pk=int(request.POST.get('stavka_id') or 0))
            remove_item_from_order(order, item, user=request.user)
        else:
            raise MagacinError('Nepoznata akcija.')
        invalidate_magacin_nav_counts()
        order.refresh_from_db()
        if ajax:
            return JsonResponse(_pakuj_order_payload(order))
        messages.success(request, 'Narudžba i račun su ažurirani.')
        return redirect('staff_magacin_pakuj_detail', broj=order.broj)
    except (MagacinError, Product.DoesNotExist, ValueError) as exc:
        if ajax:
            return JsonResponse(
                {'ok': False, 'error': str(exc) if str(exc) else 'Narudžba nije izmijenjena.'},
                status=400,
            )
        messages.error(request, str(exc) if str(exc) else 'Narudžba nije izmijenjena.')
        return redirect('staff_magacin_pakuj_detail', broj=order.broj)


@login_required(login_url='login')
@user_passes_test(_superuser_required)
def magacin_pakuj_stampaj_zapakovano(request, broj):
    order = get_object_or_404(Order, broj=broj)
    if not is_vp_order(order):
        messages.error(request, 'Štampa zapakovanog je samo za VP narudžbe.')
        return redirect('staff_magacin_pakuj')
    try:
        if order.lager_status != Order.LagerStatus.VALIDIRANO:
            raise MagacinError('Prvo validatuj VP narudžbu.')
        mark_order_packed(order)
    except MagacinError as exc:
        messages.error(request, str(exc))
        return redirect('staff_magacin_pakuj')
    stampa = reverse('staff_magacin_narudzbe_stampa')
    return redirect(f'{stampa}?{urlencode({"b": order.broj})}')


@login_required(login_url='login')
@user_passes_test(_superuser_required)
def magacin_uvoz(request):
    if request.method == 'POST' and request.POST.get('action') == 'uvoz_u_mp':
        try:
            result = move_uvoz_leftovers_to_mp(user=request.user)
        except MagacinError as exc:
            messages.error(request, str(exc) if str(exc) else 'Prebacivanje u MP nije uspjelo.')
            return redirect('staff_magacin_uvoz')
        if result['count']:
            messages.success(
                request,
                f'Uvoz lokacija u MP: {result["count"]} artikala ({result["qty"]} kom) '
                f'skinuto s lokacije Uvoz. Na sajtu ostaju na stanju. Uvozi nisu dirani.',
            )
        else:
            messages.info(
                request,
                'Nema artikala koji su ostali samo na lokaciji Uvoz.',
            )
        return redirect('staff_magacin_uvoz')

    uvozi = attach_uvoz_list_metrics(
        Uvoz.objects.filter(izvor=Uvoz.Izvor.MAGACIN)
        .select_related('kreirao')
        .order_by('-kreiran')[:200]
    )
    leftover, _loc = leftover_uvoz_stocks()
    leftover_qty = sum(int(row.kolicina or 0) for row in leftover)
    context = _magacin_context(request, section='uvoz', page_title='Uvoz — Magacin')
    context.update({
        'uvozi': uvozi,
        'uvoz_mp_count': len({row.product_id for row in leftover}),
        'uvoz_mp_qty': leftover_qty,
    })
    return render(request, 'staff/magacin/uvoz.html', context)


@login_required(login_url='login')
@user_passes_test(_superuser_required)
def magacin_uvoz_novi(request):
    from .uvoz_import import parse_uvoz_json_rows, parse_uvoz_paste

    draft_naziv = (request.POST.get('naziv') or '').strip()
    paste_text = request.POST.get('paste_text') or ''
    if request.method == 'POST':
        try:
            rows = parse_uvoz_json_rows(request.POST.get('rows_json') or '')
            if not rows:
                rows = parse_uvoz_paste(paste_text)
            if not rows:
                raise MagacinError('Zalijepi redove iz Excela u tabelu ili u polje za lijepljenje.')
            uvoz, result = create_magacin_uvoz_from_rows(
                rows,
                naziv=draft_naziv,
                user=request.user,
            )
            messages.success(
                request,
                f'Uvoz „{uvoz.naziv}” sačuvan: {result["updated"]} ažurirano, '
                f'{result["created"]} kreirano, {result["qty_total"]} kom na Novi uvoz.',
            )
            if result.get('errors'):
                messages.warning(
                    request,
                    f'{len(result["errors"])} greška(ka) — vidi detalje uvoza.',
                )
            return redirect('staff_magacin_uvoz_detail', pk=uvoz.pk)
        except MagacinError as exc:
            messages.error(request, str(exc))
        except ValueError as exc:
            messages.error(request, str(exc))
        except Exception as exc:
            messages.error(request, f'Uvoz nije uspio: {exc}')

    context = _magacin_context(request, section='uvoz', page_title='Novi uvoz — Magacin')
    context.update({
        'draft_naziv': draft_naziv,
        'paste_text': paste_text,
    })
    return render(request, 'staff/magacin/uvoz_novi.html', context)


@login_required(login_url='login')
@user_passes_test(_superuser_required)
def magacin_uvoz_detail(request, pk):
    uvoz = get_object_or_404(
        Uvoz.objects.select_related('kreirao'),
        pk=pk,
        izvor=Uvoz.Izvor.MAGACIN,
    )
    if request.method == 'POST' and request.POST.get('action') == 'delete':
        naziv = uvoz.naziv
        uvoz.delete()
        messages.success(request, f'Uvoz „{naziv}” je obrisan.')
        return redirect('staff_magacin_uvoz')

    stavke = list(uvoz.stavke.select_related('product').all())
    context = _magacin_context(request, section='uvoz', page_title=f'{uvoz.naziv} — Magacin')
    context.update({
        'uvoz': uvoz,
        'stavke': stavke,
    })
    return render(request, 'staff/magacin/uvoz_detail.html', context)


@login_required(login_url='login')
@user_passes_test(_superuser_required)
def magacin_uvoz_stampa(request, pk):
    uvoz = get_object_or_404(
        Uvoz.objects.select_related('kreirao'),
        pk=pk,
        izvor=Uvoz.Izvor.MAGACIN,
    )
    stavke = list(uvoz.stavke.select_related('product').all())
    kolicina_ukupno = sum((row.kolicina or Decimal('0')) for row in stavke)
    return render(request, 'staff/magacin/uvoz_print.html', {
        'uvoz': uvoz,
        'stavke': stavke,
        'kolicina_ukupno': kolicina_ukupno,
        'print_mode': True,
    })


@login_required(login_url='login')
@user_passes_test(_superuser_required)
def magacin_nivelacije(request):
    query = _magacin_search_query(request)
    show_done = (request.GET.get('izmjenjene') or request.POST.get('izmjenjene') or '') == '1'
    if request.method == 'POST':
        action = (request.POST.get('action') or '').strip()
        kljuc = (request.POST.get('kljuc') or '').strip()[:220]
        uvoz_id = request.POST.get('uvoz_id')
        product_id = request.POST.get('product_id')
        product = None
        if product_id:
            product = Product.objects.filter(pk=product_id).first()
            if product:
                kljuc = _nivelacija_kljuc(product)
        uvoz = Uvoz.objects.filter(pk=uvoz_id).first() if uvoz_id else None
        if kljuc and uvoz:
            if action == 'oznaci':
                NivelacijaOznaka.objects.get_or_create(
                    kljuc=kljuc,
                    uvoz=uvoz,
                    defaults={'product': product, 'kreirao': request.user},
                )
                messages.success(request, 'Artikal je označen kao izmjenjen.')
            elif action == 'skini':
                NivelacijaOznaka.objects.filter(kljuc=kljuc, uvoz=uvoz).delete()
                messages.success(request, 'Artikal je vraćen na nivelacije.')
        else:
            messages.error(request, 'Nivelacija nije pronađena.')
        params = {}
        if query:
            params['pretraga'] = query
        if show_done:
            params['izmjenjene'] = '1'
        url = reverse('staff_magacin_nivelacije')
        if params:
            url = f'{url}?{urlencode(params)}'
        return redirect(url)

    rows = _nivelacije_rows(query)
    marks = {
        (oznaka.kljuc, oznaka.uvoz_id)
        for oznaka in NivelacijaOznaka.objects.filter(
            kljuc__in=[row['kljuc'] for row in rows],
            uvoz_id__in=[row['uvoz'].pk for row in rows],
        )
    }
    for row in rows:
        row['izmjenjen'] = (row['kljuc'], row['uvoz'].pk) in marks
    rows = [row for row in rows if row['izmjenjen'] == show_done]
    context = _magacin_context(request, section='nivelacije', page_title='Nivelacije — Magacin')
    context.update({
        'rows': rows,
        'magacin_search': query,
        'show_done': show_done,
    })
    return render(request, 'staff/magacin/nivelacije.html', context)


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


def _ponuda_line_from_catalog(product, variation=None, qty=1):
    qty = max(1, int(qty or 1))
    if variation is not None:
        naziv = f'{product.naziv} — {variation.naziv}'
        sifra = (variation.sifra or product.sifra or '')[:SIFRA_MAX_LENGTH]
        cijena = variation.prikazna_cijena or Decimal('0.00')
    else:
        naziv = product.naziv
        sifra = (product.sifra or '')[:SIFRA_MAX_LENGTH]
        cijena = product.prikazna_cijena or Decimal('0.00')
    return {
        'product': product,
        'variation': variation,
        'naziv': naziv[:200],
        'sifra': sifra,
        'kolicina': qty,
        'cijena': Decimal(str(cijena)).quantize(Decimal('0.01')),
        'manuelno': False,
    }


@login_required(login_url='login')
@user_passes_test(_superuser_required)
def magacin_ponude(request):
    if request.method == 'POST' and (request.POST.get('action') or '') == 'nova':
        ponuda = MagacinPonuda(kreirao=request.user)
        ponuda.save()
        return redirect('staff_magacin_ponuda_detail', pk=ponuda.pk)
    nacrti = MagacinPonuda.objects.filter(status=MagacinPonuda.Status.NACRT).order_by('-azuriran')[:30]
    objavljene = MagacinPonuda.objects.filter(status=MagacinPonuda.Status.OBJAVLJENA).order_by('-objavljena_at', '-id')[:40]
    prihvacene = MagacinPonuda.objects.filter(status=MagacinPonuda.Status.PRIHVACENA).select_related('order').order_by('-prihvacena_at', '-id')[:40]
    context = _magacin_context(request, section='ponude', page_title='Kreiraj ponudu — Magacin')
    context.update({'nacrti': nacrti, 'objavljene': objavljene, 'prihvacene': prihvacene})
    return render(request, 'staff/magacin/ponude.html', context)


def _ponuda_ajax_payload(ponuda):
    totals = ponuda_totals(ponuda)
    stavke = []
    for row in ponuda.stavke.all():
        stavke.append({
            'pk': row.pk,
            'naziv': row.naziv,
            'sifra': row.sifra or '',
            'kolicina': int(row.kolicina or 0),
            'cijena': f'{row.cijena:.2f}',
            'ukupno': f'{row.ukupno:.2f}',
            'manuelno': bool(row.manuelno),
            'product_id': row.product_id or '',
            'variation_id': row.variation_id or '',
        })
    return {
        'ok': True,
        'stavke': stavke,
        'totals': {
            'osnova': f'{totals["osnova"]:.2f}',
            'popust': f'{totals["popust"]:.2f}',
            'net': f'{totals["net"]:.2f}',
            'pdv': f'{totals["pdv"]:.2f}',
            'ukupno_sa_pdv': f'{totals["ukupno_sa_pdv"]:.2f}',
        },
        'status': ponuda.status,
    }


@login_required(login_url='login')
@user_passes_test(_superuser_required)
def magacin_ponuda_detail(request, pk):
    ponuda = get_object_or_404(MagacinPonuda.objects.select_related('order'), pk=pk)
    ajax_actions = {'dodaj', 'dodaj_rucno', 'kolicina', 'cijena', 'ukloni', 'popust'}
    if request.method == 'POST':
        action = (request.POST.get('action') or '').strip()
        if ponuda.status == MagacinPonuda.Status.PRIHVACENA:
            if _pakuj_is_ajax(request):
                return JsonResponse(
                    {'ok': False, 'error': 'Ponuda je prihvaćena. Otvori picking.'},
                    status=400,
                )
            messages.error(request, 'Ponuda je prihvaćena. Otvori picking.')
            pick = _ponuda_pick_url(ponuda)
            return redirect(pick or reverse('staff_magacin_ponuda_detail', args=[ponuda.pk]))
        try:
            if action == 'kupac':
                ponuda.ime_prezime = (request.POST.get('ime_prezime') or '').strip()[:200]
                ponuda.telefon = (request.POST.get('telefon') or '').strip()[:30]
                ponuda.email = (request.POST.get('email') or '').strip()[:254]
                ponuda.adresa = (request.POST.get('adresa') or '').strip()[:300]
                ponuda.grad = (request.POST.get('grad') or '').strip()[:100]
                ponuda.napomena = (request.POST.get('napomena') or '').strip()
                ponuda.save()
                messages.success(request, 'Podaci kupca su sačuvani.')
            elif action == 'dodaj':
                product = get_object_or_404(Product, pk=int(request.POST.get('product_id') or 0))
                variation = None
                vid = (request.POST.get('variation_id') or '').strip()
                if vid:
                    variation = get_object_or_404(ProductVariation, pk=int(vid), artikal=product)
                qty = _parse_qty(request.POST.get('kolicina') or '1')
                existing = ponuda.stavke.filter(
                    product=product,
                    variation=variation,
                    manuelno=False,
                ).first()
                if existing:
                    existing.kolicina = int(existing.kolicina or 0) + qty
                    existing.save(update_fields=['kolicina'])
                else:
                    data = _ponuda_line_from_catalog(product, variation, qty)
                    next_ord = (ponuda.stavke.order_by('-redoslijed').values_list('redoslijed', flat=True).first() or 0) + 1
                    MagacinPonudaStavka.objects.create(ponuda=ponuda, redoslijed=next_ord, **data)
            elif action == 'dodaj_rucno':
                naziv = (request.POST.get('naziv') or '').strip()
                if not naziv:
                    raise MagacinError('Unesi naziv artikla.')
                cijena = _parse_money(request.POST.get('cijena'))
                if cijena is None or cijena < 0:
                    raise MagacinError('Unesi cijenu sa PDV.')
                qty = _parse_qty(request.POST.get('kolicina') or '1')
                next_ord = (ponuda.stavke.order_by('-redoslijed').values_list('redoslijed', flat=True).first() or 0) + 1
                MagacinPonudaStavka.objects.create(
                    ponuda=ponuda,
                    naziv=naziv[:200],
                    sifra=(request.POST.get('sifra') or '').strip()[:SIFRA_MAX_LENGTH],
                    kolicina=qty,
                    cijena=cijena.quantize(Decimal('0.01')),
                    manuelno=True,
                    redoslijed=next_ord,
                )
            elif action == 'kolicina':
                row = get_object_or_404(ponuda.stavke, pk=int(request.POST.get('stavka_id') or 0))
                row.kolicina = max(1, _parse_qty(request.POST.get('kolicina') or '1'))
                row.save(update_fields=['kolicina'])
            elif action == 'cijena':
                row = get_object_or_404(ponuda.stavke, pk=int(request.POST.get('stavka_id') or 0))
                cijena = _parse_money(request.POST.get('cijena'))
                if cijena is None or cijena < 0:
                    raise MagacinError('Cijena nije validna.')
                row.cijena = cijena.quantize(Decimal('0.01'))
                row.save(update_fields=['cijena'])
            elif action == 'ukloni':
                get_object_or_404(ponuda.stavke, pk=int(request.POST.get('stavka_id') or 0)).delete()
            elif action == 'popust':
                pct_raw = (request.POST.get('popust_postotak') or '').strip()
                km_raw = (request.POST.get('popust_iznos') or '').strip()
                pct = _parse_money(pct_raw) if pct_raw else None
                km = _parse_money(km_raw) if km_raw else Decimal('0.00')
                if pct is not None and (pct < 0 or pct > 100):
                    raise MagacinError('Popust % mora biti između 0 i 100.')
                if km is None or km < 0:
                    raise MagacinError('Popust u KM nije validan.')
                ponuda.popust_postotak = pct
                ponuda.popust_iznos = km.quantize(Decimal('0.01'))
                ponuda.save(update_fields=['popust_postotak', 'popust_iznos', 'azuriran'])
                messages.success(request, 'Popust je sačuvan.')
            elif action == 'objavi':
                if not ponuda.stavke.exists():
                    raise MagacinError('Dodaj barem jedan artikal prije objave.')
                ponuda.status = MagacinPonuda.Status.OBJAVLJENA
                if not ponuda.objavljena_at:
                    ponuda.objavljena_at = timezone.now()
                ponuda.save(update_fields=['status', 'objavljena_at', 'azuriran'])
                messages.success(request, 'Ponuda je spremna — kopiraj link.')
            elif action == 'obrisi':
                ponuda.delete()
                messages.success(request, 'Ponuda je obrisana.')
                return redirect('staff_magacin_ponude')
            else:
                raise MagacinError('Nepoznata akcija.')
        except (MagacinError, ValueError, TypeError, Product.DoesNotExist) as exc:
            if _pakuj_is_ajax(request):
                return JsonResponse(
                    {'ok': False, 'error': str(exc) if str(exc) else 'Greška pri spremanju ponude.'},
                    status=400,
                )
            messages.error(request, str(exc) if str(exc) else 'Greška pri spremanju ponude.')
            return redirect('staff_magacin_ponuda_detail', pk=ponuda.pk)
        if _pakuj_is_ajax(request) and action in ajax_actions:
            return JsonResponse(_ponuda_ajax_payload(ponuda))
        if action == 'objavi':
            return redirect(reverse('staff_magacin_ponuda_detail', args=[ponuda.pk]) + '#pnLink')
        return redirect('staff_magacin_ponuda_detail', pk=ponuda.pk)

    totals = ponuda_totals(ponuda)
    public_url = request.build_absolute_uri(reverse('ponuda_javna', args=[ponuda.token]))
    context = _magacin_context(
        request, section='ponude', page_title=f'Ponuda {ponuda.broj} — Magacin',
    )
    context.update({
        'ponuda': ponuda,
        'stavke': list(ponuda.stavke.all()),
        'totals': totals,
        'public_url': public_url,
        'lookup_url': reverse('staff_magacin_artikli_lookup'),
        'pick_url': _ponuda_pick_url(ponuda),
    })
    return render(request, 'staff/magacin/ponuda_detail.html', context)


@login_required(login_url='login')
@user_passes_test(_superuser_required)
@require_POST
def magacin_ponuda_prihvati(request, pk):
    ponuda = get_object_or_404(MagacinPonuda, pk=pk)
    try:
        order = accept_ponuda(ponuda, user=request.user)
    except MagacinError as exc:
        messages.error(request, str(exc))
        return redirect('staff_magacin_ponude')
    if (ponuda.ime_prezime or '').strip() and (ponuda.telefon or '').strip():
        try:
            _save_warehouse_customer(
                ime=ponuda.ime_prezime,
                telefon=ponuda.telefon,
                adresa=ponuda.adresa,
                grad=ponuda.grad,
                email=ponuda.email,
            )
        except MagacinError:
            pass
    invalidate_magacin_nav_counts()
    messages.success(
        request,
        f'Ponuda {ponuda.broj} je prihvaćena — narudžba #{order.broj} je na pickingu.',
    )
    return redirect('staff_magacin_ponude')


def _ponuda_pick_url(ponuda):
    order = getattr(ponuda, 'order', None)
    if order is None:
        return ''
    if order.lager_status == Order.LagerStatus.VALIDIRANO or order.zapakovana:
        return reverse('staff_order_detail', args=[order.broj])
    return reverse('staff_magacin_pakuj_detail', args=[order.broj])


def ponuda_javna(request, token):
    ponuda = get_object_or_404(
        MagacinPonuda.objects.select_related('order').prefetch_related('stavke'),
        token=token,
        status__in=[MagacinPonuda.Status.OBJAVLJENA, MagacinPonuda.Status.PRIHVACENA],
    )
    settings_obj = SiteSettings.load()
    is_staff = bool(
        getattr(request.user, 'is_authenticated', False)
        and getattr(request.user, 'is_superuser', False)
    )
    context = {
        'ponuda': ponuda,
        'stavke': list(ponuda.stavke.all()),
        'totals': ponuda_totals(ponuda),
        'site_settings': settings_obj,
        'print_mode': (request.GET.get('print') or '') == '1',
        'can_accept': is_staff and ponuda.status == MagacinPonuda.Status.OBJAVLJENA and not ponuda.order_id,
        'pick_url': _ponuda_pick_url(ponuda) if is_staff else '',
    }
    return render(request, 'ponuda_pdf.html', context)


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
        agg = loc.zalihe.filter(kolicina__gt=0).aggregate(
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


def _popis_is_ajax(request):
    return request.headers.get('X-Requested-With') == 'XMLHttpRequest'


def _provjera_location_from_request(request):
    raw = (request.POST.get('location_id') or request.GET.get('lokacija') or '').strip()
    if not raw:
        return None
    try:
        return usable_locations().filter(pk=int(raw)).first()
    except (TypeError, ValueError):
        return None


def _provjera_item_payload(location, product, variation=None):
    qty = location_stock_qty(location, product, variation)
    naziv = product.naziv
    sifra = product.sifra or ''
    if variation:
        naziv = f'{product.naziv} {variation.naziv}'.strip()
        sifra = variation.sifra or product.sifra or ''
    return {
        'ok': True,
        'product_id': product.pk,
        'variation_id': variation.pk if variation else '',
        'naziv': naziv,
        'sifra': sifra or '',
        'na_stanju': int(qty),
    }


def _provjera_product_from_post(request):
    try:
        product = Product.objects.get(pk=int(request.POST.get('product_id') or 0))
    except (Product.DoesNotExist, TypeError, ValueError):
        raise MagacinError('Artikal nije pronađen.')
    variation = None
    var_id = (request.POST.get('variation_id') or '').strip()
    if var_id:
        try:
            variation = ProductVariation.objects.get(pk=int(var_id), artikal=product)
        except (ProductVariation.DoesNotExist, TypeError, ValueError):
            raise MagacinError('Varijacija nije pronađena.')
    return product, variation


def _popis_stavka_rows(popis):
    if not popis:
        return []
    return list(popis.stavke.select_related('product', 'variation').order_by('-redoslijed', '-id'))


POPIS_DUPLICATE_WARNING = (
    'Artikal je već na ovom popisu. Provjeri je li netko izmiješao artikle.'
)


def _popis_payload(popis, *, added_id=None, already_on_list=False):
    stavke = _popis_stavka_rows(popis)
    return {
        'ok': True,
        'status': popis.status if popis else '',
        'popis_id': popis.pk if popis else None,
        'count': len(stavke),
        'total_qty': sum(int(row.kolicina or 0) for row in stavke),
        'all_checked': bool(stavke) and all(bool(row.cekirano) for row in stavke),
        'added_id': added_id,
        'already_on_list': bool(already_on_list),
        'warning': POPIS_DUPLICATE_WARNING if already_on_list else '',
        'stavke': [
            {
                'id': row.pk,
                'naziv': row.naziv,
                'sifra': row.sifra or '',
                'kolicina': int(row.kolicina or 0),
                'ocekivano': int(row.ocekivano or 0),
                'razlika': int(row.kolicina or 0) - int(row.ocekivano or 0),
                'cekirano': bool(row.cekirano),
                'tacno': int(row.ocekivano or 0) == int(row.kolicina or 0),
            }
            for row in stavke
        ],
    }


def _popis_redirect(popis=None):
    if popis and popis.pk:
        return redirect('staff_magacin_popis_detail', pk=popis.pk)
    return redirect('staff_magacin_popis')


def _popis_from_request(request, pk=None):
    raw = pk if pk is not None else (request.POST.get('popis_id') or request.GET.get('id'))
    if raw not in (None, ''):
        try:
            return MagacinPopis.objects.select_related('location').filter(pk=int(raw)).first()
        except (TypeError, ValueError):
            return None
    return active_popis()


@login_required(login_url='login')
@user_passes_test(_superuser_required)
def magacin_popis(request, pk=None):
    popis = _popis_from_request(request, pk)
    if request.method == 'POST':
        action = (request.POST.get('action') or '').strip()
        ajax = _popis_is_ajax(request)
        try:
            if action == 'provjera_start':
                location = _provjera_location_from_request(request)
                if location is None:
                    messages.error(request, 'Prvo izaberi lokaciju za provjeru.')
                    return redirect(f"{reverse('staff_magacin_popis')}?mode=provjera")
                return redirect(
                    f"{reverse('staff_magacin_popis')}?mode=provjera&lokacija={location.pk}"
                )
            if action == 'provjera_artikal':
                location = _provjera_location_from_request(request)
                if location is None:
                    raise MagacinError('Lokacija nije odabrana.')
                product, variation = _provjera_product_from_post(request)
                return JsonResponse(_provjera_item_payload(location, product, variation))
            if action == 'provjera_izmijeni':
                location = _provjera_location_from_request(request)
                if location is None:
                    raise MagacinError('Lokacija nije odabrana.')
                product, variation = _provjera_product_from_post(request)
                qty = _parse_qty(request.POST.get('kolicina') if request.POST.get('kolicina') not in (None, '') else '0')
                if qty < 0:
                    raise MagacinError('Količina ne može biti negativna.')
                novo = set_location_counted_qty(
                    location=location,
                    product=product,
                    variation=variation,
                    qty=qty,
                    user=request.user,
                )
                payload = _provjera_item_payload(location, product, variation)
                payload['na_stanju'] = int(novo)
                payload['izmijenjeno'] = True
                return JsonResponse(payload)
            if action == 'novi':
                loc_id = (request.POST.get('location_id') or '').strip()
                if not loc_id:
                    messages.error(request, 'Prvo izaberi lokaciju koju popisuješ.')
                    return redirect('staff_magacin_popis')
                location = WarehouseLocation.objects.filter(pk=loc_id).first()
                if location is None:
                    raise MagacinError('Lokacija nije pronađena.')
                popis = start_popis(user=request.user, location=location)
                return _popis_redirect(popis)
            if action == 'nastavi':
                target = _popis_from_request(request, pk)
                if target is None:
                    raise MagacinError('Popis nije pronađen.')
                popis = resume_popis(target)
                return _popis_redirect(popis)
            if popis is None:
                raise MagacinError('Nema otvorenog popisa.')
            if action == 'dodaj':
                try:
                    product = Product.objects.get(pk=int(request.POST.get('product_id') or 0))
                except (Product.DoesNotExist, TypeError, ValueError):
                    raise MagacinError('Artikal nije pronađen.')
                variation = None
                var_id = (request.POST.get('variation_id') or '').strip()
                if var_id:
                    try:
                        variation = ProductVariation.objects.get(pk=int(var_id), artikal=product)
                    except (ProductVariation.DoesNotExist, TypeError, ValueError):
                        raise MagacinError('Varijacija nije pronađena.')
                stavka = add_popis_stavka(
                    popis,
                    product=product,
                    variation=variation,
                    qty=_parse_qty(request.POST.get('kolicina') or '1'),
                )
                already = bool(getattr(stavka, 'already_on_list', False))
                if already and not ajax:
                    messages.warning(request, POPIS_DUPLICATE_WARNING)
                if ajax:
                    return JsonResponse(_popis_payload(
                        popis,
                        added_id=stavka.pk if stavka else None,
                        already_on_list=already,
                    ))
                return _popis_redirect(popis)
            if action == 'set_qty':
                stavka = set_popis_stavka_qty(
                    popis,
                    request.POST.get('stavka_id'),
                    _parse_qty(request.POST.get('kolicina') if request.POST.get('kolicina') not in (None, '') else '0'),
                )
                if ajax:
                    return JsonResponse(_popis_payload(popis, added_id=stavka.pk if stavka else None))
                return _popis_redirect(popis)
            if action == 'ukloni':
                remove_popis_stavka(popis, request.POST.get('stavka_id'))
                if ajax:
                    return JsonResponse(_popis_payload(popis))
                return _popis_redirect(popis)
            if action == 'cekiraj':
                set_popis_cekirano(
                    popis,
                    request.POST.get('stavka_id'),
                    (request.POST.get('cekirano') or '') in {'1', 'true', 'True', 'on'},
                )
                if ajax:
                    return JsonResponse(_popis_payload(popis))
                return _popis_redirect(popis)
            if action == 'pauziraj':
                pause_popis(popis)
                return redirect('staff_magacin_popis')
            if action == 'obrisi':
                popis.delete()
                return redirect('staff_magacin_popis')
            if action == 'zavrsi':
                finish_popis(popis, user=request.user)
                messages.success(request, f'Popis #{popis.pk} je završen. Količine na lokaciji su ažurirane.')
                return redirect('staff_magacin_popis')
            raise MagacinError('Nepoznata akcija.')
        except (MagacinError, Product.DoesNotExist, ValueError) as exc:
            if ajax:
                return JsonResponse(
                    {'ok': False, 'error': str(exc) if str(exc) else 'Greška na popisu.'},
                    status=400,
                )
            messages.error(request, str(exc) if str(exc) else 'Greška na popisu.')
            return _popis_redirect(popis)

    if pk and popis is None:
        messages.error(request, 'Popis nije pronađen.')
        return redirect('staff_magacin_popis')
    provjera_mode = (request.GET.get('mode') or '').strip().lower() == 'provjera'
    provjera_location = None
    pick_location = request.GET.get('nova') == '1'
    if provjera_mode:
        popis = None
        provjera_location = _provjera_location_from_request(request)
    elif pick_location and not pk:
        popis = None
    elif not pk:
        popis = popis or active_popis()
    loc_query = (request.GET.get('q') or '').strip()
    popis_lokacije = []
    if ((provjera_mode and not provjera_location) or (not popis or not popis.location_id)) and loc_query:
        popis_lokacije = list(
            usable_locations().filter(
                Q(sifra__icontains=loc_query)
                | Q(naziv__icontains=loc_query)
                | Q(opis__icontains=loc_query)
            ).order_by('redoslijed', 'sifra')[:40]
        )
    paused = list(paused_popisi())
    if popis and popis.status == MagacinPopis.Status.PAUZIRAN:
        paused = [row for row in paused if row.pk != popis.pk]
    finished = list(finished_popisi())
    if popis and popis.status == MagacinPopis.Status.ZAVRSEN:
        finished = [row for row in finished if row.pk != popis.pk]
    stavke = _popis_stavka_rows(popis)
    for row in stavke:
        row.razlika = int(row.kolicina or 0) - int(row.ocekivano or 0)
    context = _magacin_context(
        request,
        section='popis',
        page_title='Provjera — Magacin' if provjera_mode else 'Popis — Magacin',
        hide_top_search=True,
    )
    context.update({
        'popis': popis,
        'stavke': stavke,
        'total_qty': sum(int(row.kolicina or 0) for row in stavke),
        'paused_popisi': paused,
        'finished_popisi': finished,
        'popis_spreman_za_stampu': popis_spreman_za_stampu(popis),
        'lookup_url': reverse('staff_magacin_artikli_lookup'),
        'popis_lokacija_q': loc_query,
        'popis_lokacije': popis_lokacije,
        'nova': pick_location,
        'provjera_mode': provjera_mode,
        'provjera_location': provjera_location,
        'pp_boot': _popis_payload(popis) if popis else {'ok': True, 'stavke': [], 'count': 0, 'total_qty': 0},
    })
    return render(request, 'staff/magacin/popis.html', context)


@login_required(login_url='login')
@user_passes_test(_superuser_required)
def magacin_popis_stampa(request):
    popis = None
    data = request.POST if request.method == 'POST' else request.GET
    raw_id = data.get('id')
    if raw_id:
        popis = MagacinPopis.objects.select_related('location').filter(pk=raw_id).first()
    if popis is None:
        popis = active_popis() or MagacinPopis.objects.select_related('location').order_by('-kreiran').first()
    if popis is None:
        messages.error(request, 'Nema popisa za štampu.')
        return redirect('staff_magacin_popis')
    stavke = list(popis.stavke.all())
    if not stavke:
        messages.error(request, 'Nema stavki za štampu.')
        if popis.pk:
            return redirect('staff_magacin_popis_detail', pk=popis.pk)
        return redirect('staff_magacin_popis')
    razlike = (data.get('razlike') or '') == '1'
    if request.method == 'POST':
        mark_popis_odstampan(popis)
    for row in stavke:
        row.razlika = int(row.kolicina or 0) - int(row.ocekivano or 0)
    context = _magacin_context(request, section='popis', page_title='Štampa popisa — Magacin')
    context.update({
        'popis': popis,
        'stavke': stavke,
        'razlike': razlike,
        'print_mode': True,
    })
    return render(request, 'staff/magacin/popis_print.html', context)


POPIS_TEST_SESSION_KEY = 'mg_popis_test_v1'


def _popis_test_state(request):
    raw = request.session.get(POPIS_TEST_SESSION_KEY)
    if not isinstance(raw, dict):
        raw = {}
    items = raw.get('items')
    if not isinstance(items, list):
        items = []
    try:
        location_id = int(raw.get('location_id') or 0)
    except (TypeError, ValueError):
        location_id = 0
    return {
        'items': items,
        'current': raw.get('current') or '',
        'location_id': location_id or None,
    }


def _popis_test_save(request, state):
    request.session[POPIS_TEST_SESSION_KEY] = {
        'items': state.get('items') or [],
        'current': state.get('current') or '',
        'location_id': state.get('location_id') or None,
    }
    request.session.modified = True


def _popis_test_get_location(state):
    loc_id = state.get('location_id')
    if not loc_id:
        return None
    return usable_locations().filter(pk=int(loc_id)).first()


def _popis_test_location_payload(location):
    if location is None:
        return None
    return {
        'id': location.pk,
        'sifra': location.sifra or '',
        'naziv': location.naziv or '',
        'label': location.label,
    }


def _popis_test_image_url(product, variation=None):
    for img in (
        getattr(variation, 'prikazna_slika', None) if variation else None,
        getattr(product, 'prikazna_slika', None),
    ):
        if not img:
            continue
        try:
            return img.url
        except Exception:
            continue
    return ''


def _popis_test_item_key(product_id, variation_id, location_id):
    return f'p{int(product_id)}:v{int(variation_id or 0)}:l{int(location_id or 0)}'


def _popis_test_build_item(product, variation, location):
    sistem = location_stock_qty(location, product, variation) if location else 0
    naziv = product.naziv
    sifra = product.sifra or ''
    barkod = (product.barkod or '').strip()
    if variation:
        naziv = f'{product.naziv} {variation.naziv}'.strip()
        sifra = variation.sifra or product.sifra or ''
    return {
        'key': _popis_test_item_key(product.pk, variation.pk if variation else 0, location.pk if location else 0),
        'product_id': product.pk,
        'variation_id': variation.pk if variation else None,
        'location_id': location.pk if location else None,
        'naziv': naziv,
        'sifra': sifra or '',
        'barkod': barkod,
        'brend': product.brend.naziv if getattr(product, 'brend', None) else '',
        'kategorija': product.kategorija.naziv if getattr(product, 'kategorija', None) else '',
        'slika': _popis_test_image_url(product, variation),
        'sistem': int(sistem),
        'popisano': int(sistem),
        'na_stanju': bool(sistem > 0 or product.na_stanju),
    }


def _popis_test_resolve(query):
    q = (query or '').strip()
    if not q:
        raise MagacinError('Unesi barkod, šifru ili naziv.')
    folded = q.casefold()
    variation = (
        ProductVariation.objects.filter(
            Q(sifra__iexact=q) | Q(sifra_normalized__iexact=folded)
        )
        .select_related('artikal', 'artikal__brend', 'artikal__kategorija')
        .first()
    )
    if variation:
        return variation.artikal, variation
    products, exact = search_products(q, limit=8, include_zero=True)
    product = exact
    if product is None:
        items = list(products)
        if len(items) == 1:
            product = items[0]
        elif items:
            raise MagacinError('Više rezultata. Unesi tačan barkod ili šifru.')
        else:
            raise MagacinError('Artikal nije pronađen.')
    if product.varijacije.exists():
        matched = product.varijacije.filter(
            Q(sifra__iexact=q) | Q(sifra_normalized__iexact=folded)
        ).first()
        if matched:
            return product, matched
    return product, None


def _popis_test_decorate(item):
    if not isinstance(item, dict):
        return item
    sistem = int(item.get('sistem') or 0)
    popisano = int(item.get('popisano') or 0)
    item['sistem'] = sistem
    item['popisano'] = popisano
    item['razlika'] = popisano - sistem
    return item


def _popis_test_payload(state):
    items = [_popis_test_decorate(dict(row)) for row in (state.get('items') or [])]
    current_key = state.get('current') or ''
    current = next((row for row in items if row.get('key') == current_key), None)
    if current is None and items:
        current = items[0]
        current_key = current.get('key') or ''
    location = _popis_test_get_location(state)
    return {
        'ok': True,
        'current': current,
        'items': items,
        'count': len(items),
        'location': _popis_test_location_payload(location),
    }


def _popis_test_apply_counts(state, location, *, user=None):
    """Upiši popisane količine na izabranu lokaciju (korekcija)."""
    applied = 0
    with transaction.atomic():
        for row in (state.get('items') or []):
            try:
                product_id = int(row.get('product_id') or 0)
            except (TypeError, ValueError):
                continue
            if not product_id:
                continue
            product = Product.objects.filter(pk=product_id).first()
            if product is None:
                continue
            variation = None
            var_id = row.get('variation_id')
            if var_id not in (None, '', 0, '0'):
                try:
                    variation = ProductVariation.objects.filter(
                        pk=int(var_id), artikal=product,
                    ).first()
                except (TypeError, ValueError):
                    variation = None
            try:
                qty = max(0, int(row.get('popisano') or 0))
            except (TypeError, ValueError):
                qty = 0
            set_location_counted_qty(
                location=location,
                product=product,
                variation=variation,
                qty=qty,
                user=user,
                napomena=f'Popis TEST {location.sifra}',
            )
            applied += 1
    return applied


@login_required(login_url='login')
@user_passes_test(_superuser_required)
def magacin_popis_test(request):
    state = _popis_test_state(request)
    if request.method == 'POST':
        action = (request.POST.get('action') or '').strip()
        ajax = _popis_is_ajax(request)
        try:
            if action == 'set_location':
                loc_id = (request.POST.get('location_id') or '').strip()
                location = usable_locations().filter(pk=int(loc_id)).first() if loc_id else None
                if location is None:
                    raise MagacinError('Prvo izaberi lokaciju koju popisuješ.')
                if state.get('location_id') != location.pk:
                    state['items'] = []
                    state['current'] = ''
                state['location_id'] = location.pk
                _popis_test_save(request, state)
                if ajax:
                    return JsonResponse(_popis_test_payload(state))
                return redirect('staff_magacin_popis_test')
            if action == 'clear_location':
                state['location_id'] = None
                state['items'] = []
                state['current'] = ''
                _popis_test_save(request, state)
                if ajax:
                    return JsonResponse(_popis_test_payload(state))
                return redirect('staff_magacin_popis_test')
            if action == 'scan':
                location = _popis_test_get_location(state)
                if location is None:
                    raise MagacinError('Prvo izaberi lokaciju koju popisuješ.')
                product, variation = _popis_test_resolve(
                    request.POST.get('q') or request.POST.get('barkod') or '',
                )
                built = _popis_test_build_item(product, variation, location)
                existing = next(
                    (row for row in state['items'] if row.get('key') == built['key']),
                    None,
                )
                if existing is None:
                    state['items'].insert(0, built)
                    existing = built
                state['current'] = existing['key']
                _popis_test_save(request, state)
            elif action == 'select':
                key = (request.POST.get('key') or '').strip()
                if not any(row.get('key') == key for row in state['items']):
                    raise MagacinError('Stavka nije na popisu.')
                state['current'] = key
                _popis_test_save(request, state)
            elif action in {'set_qty', 'plus', 'minus', 'brzi'}:
                key = (request.POST.get('key') or state.get('current') or '').strip()
                item = next((row for row in state['items'] if row.get('key') == key), None)
                if item is None:
                    raise MagacinError('Prvo skeniraj artikal.')
                qty = int(item.get('popisano') or 0)
                if action == 'set_qty':
                    qty = max(0, _parse_qty(
                        request.POST.get('kolicina') if request.POST.get('kolicina') not in (None, '') else '0'
                    ))
                elif action == 'plus':
                    qty += 1
                elif action == 'minus':
                    qty = max(0, qty - 1)
                else:
                    qty += 1
                item['popisano'] = qty
                state['current'] = item['key']
                _popis_test_save(request, state)
            elif action == 'sacuvaj':
                _popis_test_save(request, state)
                if ajax:
                    payload = _popis_test_payload(state)
                    payload['saved'] = True
                    payload['message'] = 'Popis je sačuvan (TEST — ne mijenja zalihe).'
                    return JsonResponse(payload)
                messages.success(request, 'Popis je sačuvan (TEST — ne mijenja zalihe).')
                return redirect('staff_magacin_popis_test')
            elif action == 'zavrsi':
                location = _popis_test_get_location(state)
                if location is None:
                    raise MagacinError('Prvo izaberi lokaciju koju popisuješ.')
                applied = _popis_test_apply_counts(state, location, user=request.user)
                request.session.pop(POPIS_TEST_SESSION_KEY, None)
                request.session.modified = True
                loc_label = location.sifra or location.label
                message = (
                    f'Popis je završen. {applied} artikala upisano na lokaciju {loc_label}.'
                    if applied
                    else f'Popis je završen. Nema artikala za upis na lokaciju {loc_label}.'
                )
                if ajax:
                    return JsonResponse({
                        'ok': True,
                        'cleared': True,
                        'applied': applied,
                        'current': None,
                        'items': [],
                        'count': 0,
                        'location': None,
                        'message': message,
                    })
                messages.success(request, message)
                return redirect('staff_magacin_popis_test')
            else:
                raise MagacinError('Nepoznata akcija.')
            if ajax:
                return JsonResponse(_popis_test_payload(state))
            return redirect('staff_magacin_popis_test')
        except (MagacinError, ValueError) as exc:
            if ajax:
                return JsonResponse(
                    {'ok': False, 'error': str(exc) if str(exc) else 'Greška na popisu.'},
                    status=400,
                )
            messages.error(request, str(exc) if str(exc) else 'Greška na popisu.')
            return redirect('staff_magacin_popis_test')

    payload = _popis_test_payload(state)
    loc_query = (request.GET.get('q') or '').strip()
    popis_lokacije = []
    location = _popis_test_get_location(state)
    if location is None and loc_query:
        popis_lokacije = list(
            usable_locations().filter(
                Q(sifra__icontains=loc_query)
                | Q(naziv__icontains=loc_query)
                | Q(opis__icontains=loc_query)
            ).order_by('redoslijed', 'sifra')[:40]
        )
    context = _magacin_context(
        request,
        section='popis_test',
        page_title='Popis robe — Magacin',
        hide_top_search=True,
    )
    context.update({
        'current': payload['current'],
        'items': payload['items'],
        'location': location,
        'popis_lokacija_q': loc_query,
        'popis_lokacije': popis_lokacije,
        'lookup_url': reverse('staff_magacin_artikli_lookup'),
    })
    return render(request, 'staff/magacin/popis_test.html', context)


PROVJERA_LAGERA_SESSION_KEY = 'mg_provjera_lagera'


def _provjera_lagera_mode(request):
    raw = (request.POST.get('mode') or request.GET.get('mode') or '').strip().lower()
    return raw if raw in {'mp', 'vp'} else ''


@login_required(login_url='login')
@user_passes_test(_superuser_required)
def magacin_provjera_lagera(request):
    _ensure_magacin_locations()
    mode = _provjera_lagera_mode(request)
    lager_error = ''
    if request.method == 'POST':
        action = (request.POST.get('action') or 'uporedi').strip()
        try:
            if not mode:
                raise MagacinError('Izaberi Maloprodaju ili Veleprodaju.')
            if action == 'ukloni':
                request.session.pop(PROVJERA_LAGERA_SESSION_KEY, None)
                request.session.modified = True
                return redirect(f"{reverse('staff_magacin_provjera_lagera')}?mode={mode}")
            fajl = request.FILES.get('fajl')
            if fajl is None:
                raise MagacinError('Uploaduj PDF ili sliku tabele (kolone Šifra, Naziv, Količina).')
            parsed = extract_lager_document_rows(fajl)
            if not parsed:
                raise MagacinError('Nisam našao kolone Šifra, Naziv i Količina na dokumentu.')
            compared = compare_lager_document(parsed, mode=mode)
            request.session[PROVJERA_LAGERA_SESSION_KEY] = {
                'mode': mode,
                'fajl_naziv': (getattr(fajl, 'name', '') or '')[:200],
                'rows': compared['rows'],
                'summary': compared['summary'],
            }
            request.session.modified = True
            return redirect(f"{reverse('staff_magacin_provjera_lagera')}?mode={mode}")
        except MagacinError as exc:
            lager_error = str(exc)
            messages.error(request, lager_error)

    payload = request.session.get(PROVJERA_LAGERA_SESSION_KEY) or {}
    result_rows = []
    summary = None
    fajl_naziv = ''
    if mode and payload.get('mode') == mode:
        result_rows = payload.get('rows') or []
        summary = payload.get('summary')
        fajl_naziv = payload.get('fajl_naziv') or ''
    prikaz = (request.GET.get('prikaz') or '').strip().lower()
    if prikaz not in {'razlike', 'nema_sifre'}:
        prikaz = ''
    shown_rows = result_rows
    if prikaz == 'razlike':
        shown_rows = [
            row for row in result_rows
            if row.get('status') in {'manjak', 'visak'}
        ]
    elif prikaz == 'nema_sifre':
        shown_rows = [
            row for row in result_rows
            if row.get('status') == 'nema_sifre'
        ]
    context = _magacin_context(
        request,
        section='provjera_lagera',
        page_title='Provjera lagera — Magacin',
        hide_top_search=True,
    )
    context.update({
        'lager_mode': mode,
        'has_mp_location': maloprodaja_locations().exists(),
        'lager_error': lager_error,
        'result_rows': shown_rows,
        'result_all_count': len(result_rows),
        'summary': summary,
        'fajl_naziv': fajl_naziv,
        'lager_prikaz': prikaz,
    })
    return render(request, 'staff/magacin/provjera_lagera.html', context)


@login_required(login_url='login')
@user_passes_test(_superuser_required)
def magacin_provjera_lagera_stampa(request):
    mode = _provjera_lagera_mode(request)
    payload = request.session.get(PROVJERA_LAGERA_SESSION_KEY) or {}
    rows = []
    if mode and payload.get('mode') == mode:
        rows = [
            row for row in (payload.get('rows') or [])
            if row.get('status') in {'manjak', 'visak'}
        ]
    label = 'Maloprodaja' if mode == 'mp' else 'Veleprodaja' if mode == 'vp' else 'Lager'
    return render(request, 'staff/magacin/provjera_lagera_print.html', {
        'lager_mode': mode,
        'label': label,
        'fajl_naziv': payload.get('fajl_naziv') or '',
        'rows': rows,
        'print_mode': True,
    })


@login_required(login_url='login')
@user_passes_test(_superuser_required)
def magacin_fali_na_sajtu(request):
    _ensure_magacin_locations()
    query = (request.GET.get('q') or request.POST.get('q') or '').strip()
    if request.method == 'POST' and (request.POST.get('action') or '').strip() == 'prenos_mp':
        try:
            product = get_object_or_404(
                magacin_products_qs(), pk=int(request.POST.get('product_id') or 0),
            )
            variation = None
            raw_vid = (request.POST.get('variation_id') or '').strip()
            if raw_vid:
                variation = get_object_or_404(ProductVariation, pk=int(raw_vid), artikal=product)
            location = get_object_or_404(
                usable_locations(), pk=int(request.POST.get('location_id') or 0),
            )
            if is_uncountable_stock_location(location):
                raise MagacinError('Prenos u MP ide s magacinske lokacije, ne s maloprodaje.')
            order = create_prenos_mp_pick(
                product=product,
                variation=variation,
                location=location,
                qty=_parse_qty(request.POST.get('kolicina') or '1'),
                user=request.user,
            )
            stavki = order.stavke.count()
            qty = request.POST.get('kolicina')
            if stavki == 1:
                qty = order.stavke.first().kolicina
            messages.success(
                request,
                f'Prenos u MP ({qty} kom) je na Pickingu #{order.broj}'
                f' ({stavki} stavk{"a" if stavki == 1 else "i"}). '
                'Otvori Picking pa pokupi sve stavke.',
            )
        except (MagacinError, Product.DoesNotExist, ProductVariation.DoesNotExist, WarehouseLocation.DoesNotExist, TypeError, ValueError) as exc:
            messages.error(request, str(exc) if str(exc) else 'Prenos u MP nije uspio.')
        url = reverse('staff_magacin_fali_na_sajtu')
        if query:
            url = f'{url}?{urlencode({"q": query})}'
        return redirect(url)
    rows = missing_maloprodaja_rows(query=query)
    paginator = Paginator(rows, 40)
    page = paginator.get_page(request.GET.get('page') or 1)
    context = _magacin_context(
        request, section='fali_na_sajtu', page_title='Fali na sajtu — Magacin',
    )
    context.update({
        'page': page,
        'result_count': paginator.count,
        'fali_q': query,
        'has_mp_location': maloprodaja_locations().exists(),
    })
    return render(request, 'staff/magacin/fali_na_sajtu.html', context)


def _vp_is_ajax(request):
    return request.headers.get('X-Requested-With') == 'XMLHttpRequest'


def _vp_stock_block(request, draft, product, variation, qty, *, mp_ok=False, replace=False, stavka=None):
    from .magacin import _stock_scope

    _, stock_variation = _stock_scope(product, variation)
    existing = stavka or draft.stavke.filter(product=product, variation=stock_variation).first()
    available = display_stock_totals(product, variation)['dostupno']
    needed = qty if replace else qty + (existing.kolicina if existing else 0)
    if needed <= available or mp_ok or (existing and existing.mp_ok):
        return None
    naziv = product.naziv
    if variation:
        naziv = f'{product.naziv} {variation.naziv}'.strip()
    message = (
        f'„{naziv}” nema dostupnog artikla ({available}). '
        'Označi Nije popisan da ga dodaš, ili makni stavku.'
    )
    if _vp_is_ajax(request):
        return JsonResponse({
            'ok': False,
            'need_mp': True,
            'error': message,
            'available': available,
            'naziv': naziv,
            'product_id': product.pk,
            'variation_id': variation.pk if variation else '',
            'stavka_id': existing.pk if existing and replace else '',
            'kolicina': qty,
        }, status=409)
    messages.error(request, message)
    return redirect('staff_magacin_vp_narudzba')


def _vp_draft_payload(draft):
    stavke = list(draft.stavke.select_related('product', 'variation')) if draft else []
    osnova = sum((row.ukupno for row in stavke), Decimal('0.00'))
    totals = vp_draft_totals(osnova, bulk=bool(getattr(draft, 'bulk', False)))
    return {
        'ok': True,
        'ukupno': str(totals['osnova']),
        'pdv': str(totals['pdv']),
        'ukupno_sa_pdv': str(totals['ukupno_sa_pdv']),
        'bulk': totals['bulk'],
        'stavke': [
            {
                'id': row.pk,
                'product_id': row.product_id or '',
                'variation_id': row.variation_id or '',
                'naziv': row.naziv,
                'sifra': row.sifra or '',
                'kolicina': row.kolicina,
                'cijena': str(row.cijena),
                'mp_ok': bool(row.mp_ok),
            }
            for row in stavke
        ],
    }


@login_required(login_url='login')
@user_passes_test(_superuser_required)
def magacin_vp_narudzba(request):
    draft = active_vp_narudzba()
    if request.method == 'POST':
        action = (request.POST.get('action') or '').strip()
        try:
            if action == 'novi':
                start_vp_narudzba(user=request.user)
                return redirect('staff_magacin_vp_narudzba')
            if draft is None:
                raise MagacinError('Nema otvorene VP narudžbe.')
            if action == 'kupac':
                customer_id = request.POST.get('customer_id')
                customer = get_object_or_404(WarehouseCustomer, pk=int(customer_id or 0))
                set_vp_customer(draft, customer)
                return redirect('staff_magacin_vp_narudzba')
            if action == 'dodaj':
                product = get_object_or_404(magacin_products_qs(), pk=int(request.POST.get('product_id') or 0))
                variation = None
                var_id = request.POST.get('variation_id')
                if var_id:
                    variation = get_object_or_404(ProductVariation, pk=int(var_id), artikal=product)
                qty = _parse_qty(request.POST.get('kolicina') or '1')
                mp_ok = request.POST.get('mp_ok') == '1'
                from .magacin import _stock_scope
                _, variation = _stock_scope(product, variation)
                blocked = _vp_stock_block(request, draft, product, variation, qty, mp_ok=mp_ok)
                if blocked:
                    return blocked
                add_vp_stavka(
                    draft,
                    product=product,
                    variation=variation,
                    qty=qty,
                    mp_ok=mp_ok,
                )
                if _vp_is_ajax(request):
                    return JsonResponse(_vp_draft_payload(draft))
                return redirect('staff_magacin_vp_narudzba')
            if action == 'bulk':
                result = add_vp_bulk_stavke(draft, request.POST.get('tekst') or '')
                added_n = len(result['added'])
                skipped_n = len(result['skipped'])
                if _vp_is_ajax(request):
                    payload = _vp_draft_payload(draft)
                    payload.update({
                        'added': added_n,
                        'skipped': result['skipped'],
                        'mp': [row['naziv'] for row in result['added'] if row.get('mp_ok')],
                    })
                    return JsonResponse(payload)
                if added_n:
                    messages.success(
                        request,
                        f'Bulk: uneseno {added_n} artikala'
                        + (f', preskočeno {skipped_n} (nema u bazi).' if skipped_n else '.'),
                    )
                elif skipped_n:
                    messages.error(
                        request,
                        'Nijedan artikal nije unesen — nazivi iz liste nisu u bazi.',
                    )
                if skipped_n:
                    names = ', '.join(row['naziv'] for row in result['skipped'][:8])
                    extra = '…' if skipped_n > 8 else ''
                    messages.warning(request, f'Preskočeno: {names}{extra}')
                return redirect('staff_magacin_vp_narudzba')
            if action == 'kolicina':
                stavka_id = int(request.POST.get('stavka_id') or 0)
                qty = _parse_qty(request.POST.get('kolicina') or '1')
                mp_ok = request.POST.get('mp_ok') == '1'
                stavka = draft.stavke.filter(pk=stavka_id).first()
                if stavka and stavka.product_id:
                    blocked = _vp_stock_block(
                        request, draft, stavka.product, stavka.variation, qty,
                        mp_ok=mp_ok, replace=True, stavka=stavka,
                    )
                    if blocked:
                        return blocked
                set_vp_stavka_qty(draft, stavka_id, qty, mp_ok=mp_ok)
                if _vp_is_ajax(request):
                    return JsonResponse(_vp_draft_payload(draft))
                return redirect('staff_magacin_vp_narudzba')
            if action == 'ukloni':
                remove_vp_stavka(draft, int(request.POST.get('stavka_id') or 0))
                if _vp_is_ajax(request):
                    return JsonResponse(_vp_draft_payload(draft))
                return redirect('staff_magacin_vp_narudzba')
            if action == 'obrisi':
                draft.delete()
                return redirect('staff_magacin_vp_narudzba')
            if action in {'zavrsi', 'rezervacija'}:
                order = finish_vp_narudzba(
                    draft, user=request.user, rezervacija=(action == 'rezervacija'),
                )
                invalidate_magacin_nav_counts()
                if action == 'rezervacija':
                    messages.success(
                        request,
                        f'Rezervacija #{order.broj} je sačuvana. '
                        'Možeš dodati ili izbaciti artikle, pa Sačuvaj kad je gotova.',
                    )
                    return redirect(
                        f"{reverse('staff_magacin_narudzba_nova')}?broj={order.broj}"
                    )
                return _after_order_created_redirect(request, order)
            raise MagacinError('Nepoznata akcija.')
        except (MagacinError, Product.DoesNotExist, WarehouseCustomer.DoesNotExist, ValueError) as exc:
            if _vp_is_ajax(request):
                return JsonResponse({'ok': False, 'error': str(exc) if str(exc) else 'Greška na VP narudžbi.'}, status=400)
            messages.error(request, str(exc) if str(exc) else 'Greška na VP narudžbi.')
            return redirect('staff_magacin_vp_narudzba')

    stavke = list(draft.stavke.select_related('product', 'variation')) if draft else []
    osnova = sum((row.ukupno for row in stavke), Decimal('0.00'))
    totals = vp_draft_totals(osnova, bulk=bool(getattr(draft, 'bulk', False))) if draft else vp_draft_totals(0)
    context = _magacin_context(request, section='narudzbe', page_title='VP narudžbe — Magacin')
    context.update({
        'draft': draft,
        'stavke': stavke,
        'ukupno': totals['osnova'],
        'pdv': totals['pdv'],
        'ukupno_sa_pdv': totals['ukupno_sa_pdv'],
        'vp_bulk': totals['bulk'],
        'lookup_url': reverse('staff_magacin_artikli_lookup'),
        'customer_lookup_url': reverse('staff_magacin_kupci_lookup'),
        'customer_save_url': reverse('staff_magacin_kupci_save'),
    })
    return render(request, 'staff/magacin/vp_narudzba.html', context)


def _backup_page_context(request, *, page_title='Backup baze — Magacin'):
    context = _magacin_context(request, section='podesavanja', page_title=page_title)
    context.update({
        'backups': list_backups(),
        'backup_status': backup_storage_status(),
        'backup_next': reverse('staff_magacin_podesavanja'),
    })
    return context


@login_required(login_url='login')
@user_passes_test(_superuser_required)
def magacin_podesavanja(request):
    context = _backup_page_context(request, page_title='Podešavanja — Magacin')
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


def _backup_redirect(request):
    nxt = (request.POST.get('next') or request.GET.get('next') or '').strip()
    if nxt.startswith('/') and not nxt.startswith('//'):
        return redirect(nxt)
    return redirect('staff_magacin_backup')


@login_required(login_url='login')
@user_passes_test(_superuser_required)
def magacin_backup(request):
    if request.method == 'POST':
        action = (request.POST.get('action') or 'create').strip()
        if action in {'restore', 'upload_restore'} and not _packing_reprint_password_ok(
            request.POST.get('lozinka')
        ):
            messages.error(request, 'Pogrešna šifra.')
            return _backup_redirect(request)
        if action == 'upload_restore':
            uploaded = request.FILES.get('fajl')
            if not uploaded:
                messages.error(request, 'Odaberi backup fajl sa diska (.sqlite3 ili .dump).')
                return _backup_redirect(request)
            try:
                info = save_uploaded_backup(uploaded)
                result = restore_backup(info['name'])
            except BackupError as exc:
                messages.error(request, str(exc) if str(exc) else 'Restore nije uspio.')
                return _backup_redirect(request)
            safety = result.get('safety') or ''
            extra = f' Trenutno stanje je sačuvano kao {safety}.' if safety else ''
            messages.success(
                request,
                f'Baza je vraćena iz {result.get("restored")}.{extra}',
            )
            return _backup_redirect(request)
        if action == 'restore':
            name = (request.POST.get('name') or '').strip()
            try:
                result = restore_backup(name)
            except BackupError as exc:
                messages.error(request, str(exc) if str(exc) else 'Restore nije uspio.')
                return _backup_redirect(request)
            safety = result.get('safety') or ''
            extra = f' Trenutno stanje je sačuvano kao {safety}.' if safety else ''
            messages.success(
                request,
                f'Baza je vraćena na backup {result.get("restored")}.{extra}',
            )
            return _backup_redirect(request)
        try:
            info = create_backup()
        except BackupError as exc:
            messages.error(request, str(exc) if str(exc) else 'Backup nije uspio.')
            return _backup_redirect(request)
        messages.success(
            request,
            f'Backup baze je spreman: {info["name"]} ({info["size_label"]}). '
            'Preuzmi ga na svoj disk.',
        )
        return _backup_redirect(request)

    context = _backup_page_context(request)
    context['backup_next'] = reverse('staff_magacin_backup')
    return render(request, 'staff/magacin/backup.html', context)


@login_required(login_url='login')
@user_passes_test(_superuser_required)
@require_GET
def magacin_backup_download(request, name):
    try:
        path = resolve_backup_file(name)
    except BackupError as exc:
        raise Http404(str(exc)) from exc
    return FileResponse(path.open('rb'), as_attachment=True, filename=path.name)


@login_required(login_url='login')
@user_passes_test(_superuser_required)
@require_GET
def magacin_backup_download_current(request):
    try:
        info = create_backup()
        path = resolve_backup_file(info['name'])
    except BackupError as exc:
        messages.error(request, str(exc) if str(exc) else 'Backup nije uspio.')
        return _backup_redirect(request)
    return FileResponse(path.open('rb'), as_attachment=True, filename=path.name)


@login_required(login_url='login')
@user_passes_test(_superuser_required)
@require_POST
def magacin_sync(request):
    next_url = request.POST.get('next') or reverse('staff_magacin_artikli')
    action = (request.POST.get('action') or 'start').strip()
    try:
        if action == 'cancel':
            job = request.session.get(MAGACIN_SYNC_SESSION_KEY) or load_running_sync_job()
            if job:
                cancel_sync(job, user=request.user)
                persist_sync_job(job)
            request.session.pop(MAGACIN_SYNC_SESSION_KEY, None)
            request.session.modified = True
            messages.info(request, 'Sinhronizacija je prekinuta.')
            return HttpResponseRedirect(next_url.split('?')[0] if next_url else reverse('staff_magacin_artikli'))
        if action == 'continue':
            job = request.session.get(MAGACIN_SYNC_SESSION_KEY) or load_running_sync_job()
            if not job:
                raise MagacinError('Sync sesija je istekla. Pokreni Sync ponovo.')
            if job.get('cancelled'):
                request.session.pop(MAGACIN_SYNC_SESSION_KEY, None)
                request.session.modified = True
                messages.info(request, 'Sinhronizacija je prekinuta.')
                return HttpResponseRedirect(next_url.split('?')[0] if next_url else reverse('staff_magacin_artikli'))
            job = run_sync_until(job, user=request.user)
        else:
            product = None
            product_id = request.POST.get('product_id')
            if product_id:
                product = get_object_or_404(Product, pk=product_id)
            if action == 'stock':
                job = start_stock_sync(user=request.user, product=product)
            elif action == 'prices':
                job = start_price_sync(user=request.user, product=product)
            elif action == 'sifre':
                job = start_sifra_sync(user=request.user)
            else:
                job = start_full_sync(user=request.user, product=product)
            persist_sync_job(job)
            job = run_sync_until(job, user=request.user)

        persist_sync_job(job)
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
