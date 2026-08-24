"""Lokalni magacin: zalihe po lokacijama, kretanja i Odoo sync."""

from __future__ import annotations

import csv
import re
import time
from collections import defaultdict
from datetime import timedelta
from io import StringIO

from django.db import IntegrityError, transaction
from django.db.models import Q, Sum
from django.utils import timezone

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from .models import (
    BARKOD_MAX_LENGTH,
    SIFRA_MAX_LENGTH,
    Order,
    OrderItem,
    OrderStockHold,
    Product,
    ProductVariation,
    ProductWarehouseMeta,
    Uvoz,
    UvozStavka,
    WarehouseLocation,
    WarehouseMovement,
    WarehouseStock,
    WarehouseSupplier,
    WarehouseSyncLog,
    MagacinPopis,
    MagacinPopisStavka,
    MagacinVpNarudzba,
    MagacinVpStavka,
)

VP_MPC_DIVISOR = Decimal('1.38')


class MagacinError(Exception):
    pass


def _int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def stock_filter(*, product, variation, location=None):
    filt = {'product': product, 'location': location} if location is not None else {'product': product}
    if variation is None:
        filt['variation__isnull'] = True
    else:
        filt['variation'] = variation
    return filt


def get_or_create_stock(*, product, variation, location):
    variation_key = int(getattr(variation, 'pk', variation) or 0) if variation else 0
    stock = (
        WarehouseStock.objects.select_for_update()
        .filter(product=product, variation_key=variation_key, location=location)
        .first()
    )
    if stock:
        return stock
    return WarehouseStock.objects.create(
        product=product,
        variation=variation,
        variation_key=variation_key,
        location=location,
        kolicina=0,
        rezervisano=0,
    )


_IGNORED_LOCATION_KEYWORDS = ('prenos',)
_UNCOUNTABLE_LOCATION_KEYWORDS = ('prenos', 'maloprodaja')


def _location_match_text(location=None, *, name='', path='', sifra=''):
    parts = [name, path, sifra]
    if location is not None:
        parts.extend([
            getattr(location, 'sifra', ''),
            getattr(location, 'naziv', ''),
            getattr(location, 'odoo_location_path', ''),
        ])
    return ' '.join(str(part or '') for part in parts).casefold()


def _location_keyword_q(keywords, prefix=''):
    from django.db.models import Q

    field_prefix = f'{prefix}__' if prefix else ''
    query = Q()
    for key in keywords:
        query |= (
            Q(**{f'{field_prefix}sifra__icontains': key})
            | Q(**{f'{field_prefix}naziv__icontains': key})
            | Q(**{f'{field_prefix}odoo_location_path__icontains': key})
        )
    return query


def ignored_location_q():
    return _location_keyword_q(_IGNORED_LOCATION_KEYWORDS)


def uncountable_location_q(prefix=''):
    return _location_keyword_q(_UNCOUNTABLE_LOCATION_KEYWORDS, prefix=prefix)


def is_ignored_stock_location(location=None, *, name='', path='', sifra=''):
    """Prenos u MP se ne evidentira kao magacinska lokacija."""
    text = _location_match_text(location, name=name, path=path, sifra=sifra)
    return any(key in text for key in _IGNORED_LOCATION_KEYWORDS)


def is_uncountable_stock_location(location=None, *, name='', path='', sifra=''):
    """Prenos i maloprodaja nisu magacinsko stanje."""
    text = _location_match_text(location, name=name, path=path, sifra=sifra)
    return any(key in text for key in _UNCOUNTABLE_LOCATION_KEYWORDS)


def usable_locations():
    return WarehouseLocation.objects.filter(aktivan=True).exclude(ignored_location_q())


def countable_stock_qs(qs=None):
    qs = WarehouseStock.objects.all() if qs is None else qs
    return qs.exclude(uncountable_location_q('location'))


def stock_totals(product, variation=None):
    qs = countable_stock_qs(WarehouseStock.objects.filter(product=product))
    if variation is not None:
        qs = qs.filter(variation=variation)
    agg = qs.aggregate(na_stanju=Sum('kolicina'), rezervisano=Sum('rezervisano'))
    na_stanju = _int(agg.get('na_stanju'))
    rezervisano = max(0, _int(agg.get('rezervisano')))
    return {
        'na_stanju': na_stanju,
        'rezervisano': rezervisano,
        'dostupno': max(0, na_stanju - rezervisano),
    }


def location_rows(product, variation=None, *, locations=None):
    if locations is None:
        locations = list(usable_locations())
    locations = [loc for loc in locations if not is_uncountable_stock_location(loc)]
    qs = countable_stock_qs(WarehouseStock.objects.filter(product=product))
    if variation is not None:
        qs = qs.filter(variation=variation)
    by_loc = defaultdict(lambda: {'kolicina': 0, 'rezervisano': 0})
    for row in qs:
        bucket = by_loc[row.location_id]
        bucket['kolicina'] += _int(row.kolicina)
        bucket['rezervisano'] += max(0, _int(row.rezervisano))

    rows = []
    total_qty = 0
    total_res = 0
    for loc in locations:
        data = by_loc.get(loc.id, {'kolicina': 0, 'rezervisano': 0})
        qty = _int(data['kolicina'])
        reserved = max(0, _int(data['rezervisano']))
        available = max(0, qty - reserved)
        total_qty += qty
        total_res += reserved
        if qty <= 0:
            continue
        rows.append({
            'location': loc,
            'kolicina': qty,
            'rezervisano': reserved,
            'dostupno': available,
        })
    rows.sort(key=lambda row: (-row['kolicina'], (row['location'].sifra or '').casefold()))
    return rows, {
        'na_stanju': total_qty,
        'rezervisano': total_res,
        'dostupno': max(0, total_qty - total_res),
    }


def _keeps_site_without_locations(product):
    try:
        return bool(product.magacin_meta.mp_bez_lokacije)
    except ProductWarehouseMeta.DoesNotExist:
        return False


def mark_mp_without_location(product):
    meta, created = ProductWarehouseMeta.objects.get_or_create(product=product)
    if created or not meta.mp_bez_lokacije:
        meta.mp_bez_lokacije = True
        meta.save(update_fields=['mp_bez_lokacije'])
    return meta


def clear_mp_without_location(product):
    try:
        meta = product.magacin_meta
    except ProductWarehouseMeta.DoesNotExist:
        return
    if not meta.mp_bez_lokacije:
        return
    meta.mp_bez_lokacije = False
    meta.save(update_fields=['mp_bez_lokacije'])


def refresh_catalog_qty(product):
    """Usaglasi Product/Variation.stanje sa zbirom magacinskih lokacija."""
    variations = list(product.varijacije.all())
    var_totals = []
    if variations:
        product_total = 0
        for variation in variations:
            total = countable_stock_qs(WarehouseStock.objects.filter(
                product=product, variation=variation,
            )).aggregate(s=Sum('kolicina'))['s'] or 0
            total = max(0, _int(total))
            var_totals.append((variation, total))
            product_total += total
    else:
        product_total = countable_stock_qs(WarehouseStock.objects.filter(
            product=product, variation__isnull=True,
        )).aggregate(s=Sum('kolicina'))['s'] or 0
        product_total = max(0, _int(product_total))

    if product_total <= 0 and _keeps_site_without_locations(product):
        return product_total
    if product_total > 0:
        clear_mp_without_location(product)

    for variation, total in var_totals:
        if variation.stanje != total or variation.na_stanju != (total > 0):
            variation.stanje = total
            variation.na_stanju = total > 0
            variation.save(update_fields=['stanje', 'na_stanju'])

    na_stanju = product_total > 0
    update_fields = []
    if product.stanje != product_total:
        product.stanje = product_total
        update_fields.append('stanje')
    if product.na_stanju != na_stanju:
        product.na_stanju = na_stanju
        update_fields.append('na_stanju')
    if update_fields:
        product.save(update_fields=update_fields)
    return product_total


@transaction.atomic
def apply_movement(
    *,
    product,
    location,
    tip,
    kolicina,
    variation=None,
    to_location=None,
    napomena='',
    user=None,
    rezervisano=None,
    from_reservation=False,
):
    """
    Promijeni zalihu i upiši kretanje.
    tip: prijem | prodaja | transfer | korekcija | rezervacija
    kolicina: uvijek pozitivna osim za korekciju (apsolutna nova količina).
    """
    if not isinstance(product, Product):
        product = Product.objects.select_for_update().get(pk=product)
    else:
        product = Product.objects.select_for_update().get(pk=product.pk)

    if variation:
        if not isinstance(variation, ProductVariation):
            variation = ProductVariation.objects.get(pk=variation)
        if variation.artikal_id != product.pk:
            raise MagacinError('Varijacija ne pripada ovom artiklu.')

    if not isinstance(location, WarehouseLocation):
        location = WarehouseLocation.objects.get(pk=location)
    if is_ignored_stock_location(location):
        raise MagacinError('Lokacija Prenos u MP se ne evidentira.')
    if not location.aktivan:
        raise MagacinError('Lokacija nije aktivna.')

    tip = (tip or '').strip().lower()
    valid = {choice.value for choice in WarehouseMovement.Tip}
    if tip not in valid:
        raise MagacinError('Nepoznat tip kretanja.')

    qty = _int(kolicina)
    if tip != WarehouseMovement.Tip.KOREKCIJA and qty <= 0:
        raise MagacinError('Količina mora biti veća od nule.')
    if tip == WarehouseMovement.Tip.KOREKCIJA and qty < 0:
        raise MagacinError('Nova količina ne može biti negativna.')

    stock = get_or_create_stock(product=product, variation=variation, location=location)
    signed = 0

    if tip == WarehouseMovement.Tip.PRIJEM:
        stock.kolicina += qty
        signed = qty
    elif tip == WarehouseMovement.Tip.PRODAJA:
        if from_reservation:
            if stock.rezervisano < qty:
                raise MagacinError(
                    f'Nedovoljno rezervacije na {location.label} (ima {stock.rezervisano}, treba {qty}).'
                )
        elif stock.dostupno < qty:
            raise MagacinError(
                f'Nedovoljno slobodne zalihe na {location.label} '
                f'(dostupno {stock.dostupno}, treba {qty}).'
            )
        if stock.kolicina < qty:
            raise MagacinError(
                f'Nedovoljno zalihe na {location.label} (ima {stock.kolicina}, treba {qty}).'
            )
        stock.kolicina -= qty
        if from_reservation:
            stock.rezervisano = max(0, stock.rezervisano - qty)
        signed = -qty
    elif tip == WarehouseMovement.Tip.KOREKCIJA:
        signed = qty - stock.kolicina
        stock.kolicina = qty
    elif tip == WarehouseMovement.Tip.REZERVACIJA:
        new_reserved = rezervisano if rezervisano is not None else qty
        new_reserved = max(0, _int(new_reserved))
        if new_reserved > stock.kolicina:
            raise MagacinError('Rezervisano ne može biti veće od količine na stanju.')
        signed = new_reserved - stock.rezervisano
        stock.rezervisano = new_reserved
        qty = signed
    elif tip == WarehouseMovement.Tip.TRANSFER:
        if to_location is None:
            raise MagacinError('Odaberi odredišnu lokaciju.')
        if not isinstance(to_location, WarehouseLocation):
            to_location = WarehouseLocation.objects.get(pk=to_location)
        if to_location.pk == location.pk:
            raise MagacinError('Odredište mora biti druga lokacija.')
        if not to_location.aktivan:
            raise MagacinError('Odredišna lokacija nije aktivna.')
        if stock.kolicina < qty:
            raise MagacinError(
                f'Nedovoljno zalihe na {location.label} (ima {stock.kolicina}, treba {qty}).'
            )
        dest = get_or_create_stock(product=product, variation=variation, location=to_location)
        stock.kolicina -= qty
        if stock.rezervisano > stock.kolicina:
            stock.rezervisano = stock.kolicina
        dest.kolicina += qty
        dest.save(update_fields=['kolicina', 'rezervisano', 'azurirano'])
        signed = -qty
    elif tip == WarehouseMovement.Tip.SYNC:
        signed = qty - stock.kolicina
        stock.kolicina = qty
        if rezervisano is not None:
            stock.rezervisano = max(0, _int(rezervisano))
    else:
        raise MagacinError('Nepoznat tip kretanja.')

    if stock.rezervisano > stock.kolicina:
        stock.rezervisano = max(0, stock.kolicina)
    stock.save(update_fields=['kolicina', 'rezervisano', 'azurirano'])

    movement = WarehouseMovement.objects.create(
        product=product,
        variation=variation,
        location=location,
        to_location=to_location if tip == WarehouseMovement.Tip.TRANSFER else None,
        tip=tip,
        kolicina=signed if tip != WarehouseMovement.Tip.TRANSFER else qty,
        napomena=(napomena or '')[:300],
        korisnik=user if getattr(user, 'is_authenticated', False) else None,
    )
    refresh_catalog_qty(product)
    return movement


NOVI_UVOZ_SIFRA = 'UVOZ'
NOVI_UVOZ_NAZIV = 'Novi uvoz'


def ensure_novi_uvoz_location():
    """Lokacija na koju ide sva količina iz Excel uvoza, dok je korisnik ne rasporedi."""
    existing = (
        WarehouseLocation.objects.filter(
            Q(naziv__iexact=NOVI_UVOZ_NAZIV) | Q(sifra__iexact=NOVI_UVOZ_SIFRA)
        )
        .order_by('redoslijed', 'id')
        .first()
    )
    if existing:
        fields = []
        if existing.naziv != NOVI_UVOZ_NAZIV:
            existing.naziv = NOVI_UVOZ_NAZIV
            fields.append('naziv')
        if not existing.aktivan:
            existing.aktivan = True
            fields.append('aktivan')
        if fields:
            existing.save(update_fields=fields)
        return existing
    return WarehouseLocation.objects.create(
        sifra=NOVI_UVOZ_SIFRA,
        naziv=NOVI_UVOZ_NAZIV,
        opis='Količine iz Excel uvoza, za raspodjelu',
        aktivan=True,
        redoslijed=1,
    )


def _row_ukupna_fakturna(row):
    total = row.get('ukupno_fakturna')
    if total is not None and total != '':
        try:
            return Decimal(str(total)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        except Exception:
            pass
    qty = row.get('kolicina')
    fak = row.get('fakturna')
    if qty is None or fak is None or qty == '' or fak == '':
        return Decimal('0.00')
    try:
        return (Decimal(str(qty)) * Decimal(str(fak))).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    except Exception:
        return Decimal('0.00')


def _stavka_ukupna_fakturna(stavka):
    if stavka.ukupno_fakturna is not None:
        return stavka.ukupno_fakturna
    if stavka.kolicina is not None and stavka.fakturna is not None:
        return (stavka.kolicina * stavka.fakturna).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    return Decimal('0.00')


def attach_uvoz_list_metrics(uvozi):
    """Dodaj na listing: stavki, ažurirano, kreirano, promjena MPC, ukupna fakturna."""
    uvozi = list(uvozi)
    if not uvozi:
        return uvozi
    ids = [u.pk for u in uvozi]
    stavke = list(
        UvozStavka.objects.filter(uvoz_id__in=ids).only(
            'uvoz_id', 'status', 'product_id', 'artikal_naziv',
            'mpc_brutto', 'kolicina', 'fakturna', 'ukupno_fakturna',
        )
    )
    by_uvoz = defaultdict(list)
    for row in stavke:
        by_uvoz[row.uvoz_id].append(row)
    hist_counts = {}
    if any(u.broj_mpc_promjena is None for u in uvozi):
        hist_counts = _mpc_change_counts_for_uvozi(ids, stavke)
    for uvoz in uvozi:
        rows = by_uvoz.get(uvoz.pk, [])
        uvoz.list_stavki = len(rows)
        uvoz.list_azurirano = sum(1 for row in rows if row.status == UvozStavka.Status.UPDATED)
        uvoz.list_kreirano = sum(1 for row in rows if row.status == UvozStavka.Status.CREATED)
        if uvoz.ukupna_fakturna is not None:
            uvoz.list_fakturna = uvoz.ukupna_fakturna
        else:
            total = Decimal('0.00')
            for row in rows:
                total += _stavka_ukupna_fakturna(row)
            uvoz.list_fakturna = total
        if uvoz.broj_mpc_promjena is not None:
            uvoz.list_mpc = uvoz.broj_mpc_promjena
        else:
            uvoz.list_mpc = hist_counts.get(uvoz.pk, 0)
    return uvozi


def _mpc_change_counts_for_uvozi(uvoz_ids, listed_stavke):
    pids = {row.product_id for row in listed_stavke if row.product_id}
    names = {
        (row.artikal_naziv or '').strip()
        for row in listed_stavke
        if not row.product_id and (row.artikal_naziv or '').strip()
    }
    filt = Q()
    if pids:
        filt |= Q(product_id__in=pids)
    if names:
        filt |= Q(artikal_naziv__in=names)
    if not pids and not names:
        return {}
    hist = (
        UvozStavka.objects.filter(uvoz__izvor=Uvoz.Izvor.MAGACIN)
        .filter(filt)
        .select_related('uvoz')
        .order_by('uvoz__kreiran', 'id')
    )
    last = {}
    counts = defaultdict(int)
    idset = set(uvoz_ids)
    for row in hist:
        key = ('p', row.product_id) if row.product_id else ('n', (row.artikal_naziv or '').strip().casefold())
        prev = last.get(key)
        if (
            row.uvoz_id in idset
            and prev is not None
            and row.mpc_brutto is not None
            and prev != row.mpc_brutto
        ):
            counts[row.uvoz_id] += 1
        if row.mpc_brutto is not None:
            last[key] = row.mpc_brutto
    return counts


def _find_product_exact_name(name):
    """Pronađi artikal po 100% istom nazivu; prednost ima već u Magacinu."""
    name = (name or '').strip()
    if not name:
        return None
    qs = Product.objects.filter(naziv=name)
    magacin = qs.filter(
        Q(magacin_sync_at__isnull=False) | Q(odoo_template_id__isnull=False)
    ).first()
    if magacin:
        return magacin
    product = qs.first()
    if product:
        return product
    collapsed = re.sub(r'\s+', ' ', name)
    if collapsed != name:
        return _find_product_exact_name(collapsed)
    for candidate in Product.objects.filter(naziv__iexact=name).only('id', 'naziv')[:5]:
        if (candidate.naziv or '').strip() == name:
            return candidate
    return None


def apply_magacin_uvoz_row(row, *, location=None, user=None, napomena=''):
    """
    Primijeni jedan red uvoza na Magacin.
    Vraća dict: status, product, poruka, qty
    """
    name = (row.get('artikal') or '').strip()
    qty = _int(row.get('kolicina'))
    price = row.get('mpc_brutto')
    vpc = row.get('vpc_netto')
    if not name:
        return {
            'status': UvozStavka.Status.SKIPPED,
            'product': None,
            'poruka': 'Prazan naziv',
            'qty': 0,
        }
    product_guess = _find_product_exact_name(name)
    if qty <= 0:
        return {
            'status': UvozStavka.Status.SKIPPED,
            'product': product_guess,
            'poruka': 'Količina ≤ 0',
            'qty': 0,
        }
    location = location or ensure_novi_uvoz_location()
    try:
        with transaction.atomic():
            product = product_guess
            created = False
            mpc_changed = False
            now = timezone.now()
            if product is None:
                if price is None or price <= 0:
                    return {
                        'status': UvozStavka.Status.SKIPPED,
                        'product': None,
                        'poruka': 'Nema Mpc brutto — novi artikal nije kreiran',
                        'qty': 0,
                    }
                product = Product(
                    naziv=name[:200],
                    cijena=price,
                    na_stanju=True,
                    stanje=0,
                    aktivan=True,
                    prikazi_na_pocetnoj=False,
                    magacin_sync_at=now,
                )
                product.save()
                created = True
            else:
                fields = []
                if product.magacin_sync_at is None:
                    product.magacin_sync_at = now
                    fields.append('magacin_sync_at')
                if price is not None and price > 0 and product.cijena != price:
                    mpc_changed = True
                    product.cijena = price
                    if product.akcija_postotak:
                        product.akcijska_cijena = None
                        fields.append('akcijska_cijena')
                    fields.append('cijena')
                if not product.aktivan:
                    product.aktivan = True
                    fields.append('aktivan')
                if fields:
                    product.save(update_fields=fields)

            if vpc is not None and vpc > 0:
                meta, _ = ProductWarehouseMeta.objects.get_or_create(product=product)
                if meta.veleprodajna_cijena != vpc:
                    meta.veleprodajna_cijena = vpc
                    meta.save(update_fields=['veleprodajna_cijena'])

            apply_movement(
                product=product,
                location=location,
                tip=WarehouseMovement.Tip.PRIJEM,
                kolicina=qty,
                napomena=(napomena or 'Uvoz Excel')[:300],
                user=user,
            )
            status = UvozStavka.Status.CREATED if created else UvozStavka.Status.UPDATED
            price_label = f'{price} KM' if price is not None and price > 0 else 'cijena ista'
            return {
                'status': status,
                'product': product,
                'poruka': f'+{qty} kom na Novi uvoz, {price_label}',
                'qty': qty,
                'mpc_changed': mpc_changed,
            }
    except Exception as exc:
        return {
            'status': UvozStavka.Status.ERROR,
            'product': product_guess,
            'poruka': str(exc),
            'qty': 0,
        }


def apply_magacin_uvoz(rows, *, user=None, filename=''):
    """
    Uvoz iz Excel redova u Magacin.
    Postojeći artikli: ažuriraj MPC / VPC i dodaj količinu na lokaciju Novi uvoz.
    Nepostojeći: kreiraj pa primi istu količinu na Novi uvoz.
    """
    location = ensure_novi_uvoz_location()
    stats = {
        'updated': 0,
        'created': 0,
        'skipped': 0,
        'errors': [],
        'details': [],
        'location': location,
        'rows_total': len(rows),
        'qty_total': 0,
    }
    note = 'Uvoz Excel'
    if filename:
        note = f'Uvoz Excel: {filename}'[:300]

    for row in rows:
        name = (row.get('artikal') or '').strip()
        if not name:
            continue
        result = apply_magacin_uvoz_row(row, location=location, user=user, napomena=note)
        status = result['status']
        product = result['product']
        if status == UvozStavka.Status.CREATED:
            stats['created'] += 1
            stats['qty_total'] += result['qty']
        elif status == UvozStavka.Status.UPDATED:
            stats['updated'] += 1
            stats['qty_total'] += result['qty']
        elif status == UvozStavka.Status.ERROR:
            stats['errors'].append(f'{name}: {result["poruka"]}')
        else:
            stats['skipped'] += 1
        if len(stats['details']) < 200:
            stats['details'].append({
                'status': status,
                'naziv': name,
                'product_id': product.pk if product else None,
                'poruka': result['poruka'],
            })
    return stats


def create_magacin_uvoz_from_rows(rows, *, naziv='', user=None):
    """Snimi Magacin uvoz + stavke i primijeni količine na lokaciju Novi uvoz."""
    named = [(row.get('artikal') or '').strip() for row in rows]
    if not any(named):
        raise MagacinError('Nema redova za uvoz. Zalijepi podatke iz Excela.')

    if not naziv:
        naziv = f'Uvoz {timezone.localtime().strftime("%d.%m.%Y. %H:%M")}'

    location = ensure_novi_uvoz_location()
    note = f'Uvoz: {naziv}'[:300]
    stats = {
        'updated': 0,
        'created': 0,
        'skipped': 0,
        'mpc_changed': 0,
        'ukupna_fakturna': Decimal('0.00'),
        'errors': [],
        'details': [],
        'location': location,
        'rows_total': 0,
        'qty_total': 0,
    }

    with transaction.atomic():
        uvoz = Uvoz.objects.create(
            naziv=naziv[:200],
            izvor=Uvoz.Izvor.MAGACIN,
            kreirao=user if getattr(user, 'is_authenticated', False) else None,
        )
        stavke = []
        for i, row in enumerate(rows):
            name = (row.get('artikal') or '').strip()
            if not name:
                continue
            stats['rows_total'] += 1
            result = apply_magacin_uvoz_row(
                row, location=location, user=user, napomena=note,
            )
            status = result['status']
            product = result['product']
            if status == UvozStavka.Status.CREATED:
                stats['created'] += 1
                stats['qty_total'] += result['qty']
            elif status == UvozStavka.Status.UPDATED:
                stats['updated'] += 1
                stats['qty_total'] += result['qty']
            elif status == UvozStavka.Status.ERROR:
                stats['errors'].append(f'{name}: {result["poruka"]}')
            else:
                stats['skipped'] += 1
            if result.get('mpc_changed'):
                stats['mpc_changed'] += 1
            stats['ukupna_fakturna'] += _row_ukupna_fakturna(row)
            if len(stats['details']) < 200:
                stats['details'].append({
                    'status': status,
                    'naziv': name,
                    'product_id': product.pk if product else None,
                    'poruka': result['poruka'],
                })
            stavke.append(UvozStavka(
                uvoz=uvoz,
                artikal_naziv=name[:200],
                kolicina=row.get('kolicina'),
                fakturna=row.get('fakturna'),
                nabavna=row.get('nabavna'),
                vpc_netto=row.get('vpc_netto'),
                mpc_brutto=row.get('mpc_brutto'),
                vpc_marza=row.get('vpc_marza'),
                ukupno_fakturna=row.get('ukupno_fakturna'),
                product=product,
                status=status,
                poruka=(result['poruka'] or '')[:300],
                redoslijed=i,
            ))
        UvozStavka.objects.bulk_create(stavke)
        uvoz.broj_redova = len(stavke)
        uvoz.broj_azurirano = stats['updated']
        uvoz.broj_kreirano = stats['created']
        uvoz.broj_preskoceno = stats['skipped']
        uvoz.broj_mpc_promjena = stats['mpc_changed']
        uvoz.ukupna_fakturna = stats['ukupna_fakturna']
        uvoz.log_detalji = stats['details']
        uvoz.save()

    stats['uvoz'] = uvoz
    return uvoz, stats


def uvoz_location():
    return (
        WarehouseLocation.objects.filter(
            Q(naziv__iexact=NOVI_UVOZ_NAZIV) | Q(sifra__iexact=NOVI_UVOZ_SIFRA)
        )
        .order_by('redoslijed', 'id')
        .first()
    )


def leftover_uvoz_stocks():
    """Količine na Uvoz lokaciji koje nisu otišle ni na jednu drugu lokaciju."""
    location = uvoz_location()
    if location is None:
        return [], None
    stocks = list(
        countable_stock_qs(
            WarehouseStock.objects.filter(location=location, kolicina__gt=0)
        ).select_related('product', 'variation')
    )
    if not stocks:
        return [], location
    product_ids = {row.product_id for row in stocks if row.product_id}
    imported_ids = set(
        UvozStavka.objects.filter(
            uvoz__izvor=Uvoz.Izvor.MAGACIN,
            product_id__in=product_ids,
        ).values_list('product_id', flat=True)
    )
    other_ids = set(
        countable_stock_qs(
            WarehouseStock.objects.filter(product_id__in=product_ids, kolicina__gt=0)
        )
        .exclude(location=location)
        .values_list('product_id', flat=True)
    )
    leftover = [
        row
        for row in stocks
        if row.product_id in imported_ids
        and row.product_id not in other_ids
        and _int(row.rezervisano) <= 0
    ]
    return leftover, location


@transaction.atomic
def move_uvoz_leftovers_to_mp(*, user=None):
    """
    Skini s magacinskih lokacija uvozne artikle koji su ostali samo na Uvoz.
    Na sajtu ostaju na stanju (fizički su u MP). Zapise uvoza ne dira.
    """
    leftover, location = leftover_uvoz_stocks()
    if not leftover:
        return {'count': 0, 'qty': 0, 'product_ids': []}
    moved_ids = []
    qty_total = 0
    seen = set()
    user_obj = user if getattr(user, 'is_authenticated', False) else None
    for stock in leftover:
        qty = _int(stock.kolicina)
        qty_total += qty
        stock.kolicina = 0
        stock.rezervisano = 0
        stock.save(update_fields=['kolicina', 'rezervisano', 'azurirano'])
        WarehouseMovement.objects.create(
            product=stock.product,
            variation=stock.variation,
            location=location,
            tip=WarehouseMovement.Tip.KOREKCIJA,
            kolicina=-qty,
            napomena='Uvoz lokacija u MP',
            korisnik=user_obj,
        )
        if stock.product_id in seen:
            continue
        product = Product.objects.get(pk=stock.product_id)
        fields = []
        if not product.aktivan:
            product.aktivan = True
            fields.append('aktivan')
        if not product.na_stanju:
            product.na_stanju = True
            fields.append('na_stanju')
        if not product.stanje or product.stanje < 1:
            product.stanje = max(qty, 1)
            fields.append('stanje')
        if fields:
            product.save(update_fields=fields)
        for var in product.varijacije.all():
            var_fields = []
            if not var.na_stanju:
                var.na_stanju = True
                var_fields.append('na_stanju')
            if not var.stanje or var.stanje < 1:
                var.stanje = 1
                var_fields.append('stanje')
            if var_fields:
                var.save(update_fields=var_fields)
        mark_mp_without_location(product)
        seen.add(stock.product_id)
        moved_ids.append(stock.product_id)
    return {
        'count': len(moved_ids),
        'qty': qty_total,
        'product_ids': moved_ids,
    }


def magacin_products_qs():
    """Isti artikli kao na sajtu iz Odoa — bez drugog kataloga."""
    return Product.objects.filter(
        Q(magacin_sync_at__isnull=False) | Q(odoo_template_id__isnull=False)
    )


def magacin_in_stock_q():
    """Artikal ima zalihu na barem jednoj magacinskoj lokaciji (ne Prenos / maloprodaja)."""
    from django.db.models import Exists, OuterRef

    return Exists(
        countable_stock_qs(
            WarehouseStock.objects.filter(product_id=OuterRef('pk'), kolicina__gt=0)
        )
    )


def _product_search_q(query):
    from django.db.models import Q

    q = (query or '').strip()
    folded = q.casefold()
    return (
        Q(naziv__icontains=q)
        | Q(sifra__icontains=q)
        | Q(barkod__icontains=q)
        | Q(naziv_normalized__icontains=folded)
        | Q(sifra_normalized__icontains=folded)
        | Q(barkod_normalized__icontains=folded)
        | Q(search_keywords__icontains=q)
        | Q(varijacije__naziv__icontains=q)
        | Q(varijacije__sifra__icontains=q)
        | Q(varijacije__naziv_normalized__icontains=folded)
        | Q(varijacije__sifra_normalized__icontains=folded)
    )


def local_odoo_template_ids():
    """Odoo template ID-jevi artikala (Product) koji već postoje — bez varijacija."""
    ids = Product.objects.exclude(odoo_template_id=None).values_list(
        'odoo_template_id', flat=True,
    )
    return sorted(int(item) for item in ids if item)


def _missing_odoo_template_ids(template_ids):
    """Odoo template ID-jevi iz liste koji još nemaju Product na sajtu."""
    wanted = {int(tid) for tid in (template_ids or []) if tid}
    if not wanted:
        return []
    have = set(
        Product.objects.filter(odoo_template_id__in=wanted)
        .values_list('odoo_template_id', flat=True)
    )
    return sorted(wanted - {int(i) for i in have if i})


def _norm_ident(value):
    return (value or '').strip()


def _template_identity(template):
    from .odoo_import import _odoo_template_name

    naziv = _norm_ident(_odoo_template_name(template))
    raw_code = template.get('default_code')
    sifra = '' if raw_code in (None, False) else _norm_ident(str(raw_code))
    raw_bar = template.get('barcode')
    barkod = '' if raw_bar in (None, False) else _norm_ident(str(raw_bar))
    return naziv, sifra, barkod


def _identity_blockers(template, *, exclude_pk=None):
    """Artikli sa istim nazivom, šifrom ili barkodom."""
    naziv, sifra, barkod = _template_identity(template)
    q = Q()
    if sifra:
        q |= Q(sifra__iexact=sifra)
    if barkod:
        q |= Q(barkod__iexact=barkod)
    if naziv:
        q |= Q(naziv__iexact=naziv)
    if not q:
        return Product.objects.none()
    qs = Product.objects.filter(q)
    if exclude_pk:
        qs = qs.exclude(pk=exclude_pk)
    return qs


def _keeper_rank(product):
    orders = product.orderitem_set.count()
    stock = int(product.stanje or 0)
    real_sifra = 0 if (product.sifra or '').upper().startswith('ODOO-T') else 1
    has_barcode = 1 if _norm_ident(product.barkod) else 0
    return (orders, stock, real_sifra, has_barcode, -int(product.pk))


def _duplicate_identity_groups():
    """Grupe artikala sa istim nazivom / šifrom / barkodom."""
    groups = []
    seen = set()

    def add_group(items):
        pks = tuple(sorted(p.pk for p in items if p))
        if len(pks) < 2 or pks in seen:
            return
        seen.add(pks)
        groups.append(list(items))

    by_name = {}
    by_sifra = {}
    by_barkod = {}
    for product in Product.objects.all().only(
        'id', 'naziv', 'sifra', 'barkod', 'stanje', 'odoo_template_id',
    ):
        name = _norm_ident(product.naziv).casefold()
        if name:
            by_name.setdefault(name, []).append(product)
        sifra = _norm_ident(product.sifra)
        if sifra:
            by_sifra.setdefault(sifra.casefold(), []).append(product)
        barkod = _norm_ident(product.barkod)
        if barkod:
            by_barkod.setdefault(barkod, []).append(product)
    for bucket in (by_name, by_sifra, by_barkod):
        for items in bucket.values():
            if len(items) > 1:
                add_group(items)
    return groups


def cleanup_duplicate_identities(*, dry_run=False):
    """
    Obriši duple artikle po nazivu, šifri ili barkodu.
    Zadrži jedan (narudžbe > zaliha > prava šifra > barkod > stariji).
    """
    deleted = []
    skipped = []
    gone = set()
    for items in _duplicate_identity_groups():
        alive = [p for p in items if p.pk not in gone]
        if len(alive) < 2:
            continue
        keeper = max(alive, key=_keeper_rank)
        for product in alive:
            if product.pk == keeper.pk:
                continue
            if product.orderitem_set.exists():
                skipped.append({
                    'pk': product.pk,
                    'naziv': product.naziv,
                    'sifra': product.sifra,
                    'razlog': f'ima narudžbe, ostavljen uz #{keeper.pk}',
                })
                continue
            info = {
                'pk': product.pk,
                'naziv': product.naziv,
                'sifra': product.sifra,
                'odoo_template_id': product.odoo_template_id,
                'zadrzan': keeper.pk,
            }
            if not dry_run:
                product.delete()
            gone.add(product.pk)
            deleted.append(info)
    return {'obrisano': deleted, 'preskoceno': skipped}


def attach_site_odoo_products_to_magacin(*, when=None):
    """Označi postojeće Odoo artikle sa sajta kao Magacin — ne kreira nove."""
    when = when or timezone.now()
    product_ids = set(
        Product.objects.exclude(odoo_template_id=None).values_list('pk', flat=True)
    )
    product_ids.update(
        ProductVariation.objects.exclude(odoo_template_id=None)
        .values_list('artikal_id', flat=True)
    )
    product_ids.update(
        ProductVariation.objects.exclude(odoo_variant_id=None)
        .values_list('artikal_id', flat=True)
    )
    if not product_ids:
        return 0
    return Product.objects.filter(
        pk__in=product_ids,
        magacin_sync_at__isnull=True,
    ).update(magacin_sync_at=when)


def mark_magacin_synced(template_ids, *, when=None):
    if not template_ids:
        return 0
    when = when or timezone.now()
    return Product.objects.filter(odoo_template_id__in=list(template_ids)).update(magacin_sync_at=when)


def search_products(query, *, limit=40, include_zero=False):
    from django.db.models import Q

    qs = magacin_products_qs().select_related(
        'kategorija', 'brend', 'magacin_meta',
    ).prefetch_related('varijacije')
    if not include_zero:
        qs = qs.filter(magacin_in_stock_q())
    q = (query or '').strip()
    if q:
        qs = qs.filter(_product_search_q(q)).distinct()
        folded = q.casefold()
        exact_qs = magacin_products_qs().filter(
            Q(sifra__iexact=q)
            | Q(barkod__iexact=q)
            | Q(naziv__iexact=q)
            | Q(naziv_normalized__iexact=folded)
            | Q(varijacije__sifra__iexact=q)
            | Q(varijacije__naziv__iexact=q)
        ).distinct()
        exact = list(exact_qs[:2])
        if len(exact) == 1:
            return exact, exact[0]
    qs = qs.distinct()
    if include_zero:
        qs = qs.annotate(_na_stanju=magacin_in_stock_q()).order_by('-_na_stanju', 'naziv')
    else:
        qs = qs.order_by('naziv')
    if limit is None:
        return qs, None
    return qs[: max(0, int(limit))], None


def query_looks_like_name(query):
    text = (query or '').strip()
    return ' ' in text and not any(ch.isdigit() for ch in text)


def product_odoo_ids(product):
    ids = []
    for variation in product.varijacije.all():
        if variation.odoo_variant_id:
            ids.append(int(variation.odoo_variant_id))
    if product.odoo_template_id and not ids:
        ids.append(int(product.odoo_template_id))
    return ids


def _ensure_location_from_odoo(location_id, location_name, path=''):
    if is_uncountable_stock_location(name=location_name, path=path):
        return None
    existing = WarehouseLocation.objects.filter(odoo_location_id=location_id).first()
    if existing and is_uncountable_stock_location(existing):
        return None
    if existing:
        changed = False
        if location_name and existing.naziv != location_name and not existing.naziv:
            existing.naziv = location_name
            changed = True
        if path and existing.odoo_location_path != path:
            existing.odoo_location_path = path[:255]
            changed = True
        if changed:
            existing.save(update_fields=['naziv', 'odoo_location_path'])
        return existing

    sifra = (location_name or f'ODOO-{location_id}')[:20]
    base = sifra
    n = 2
    while WarehouseLocation.objects.filter(sifra=sifra).exclude(odoo_location_id=location_id).exists():
        sifra = f'{base[:16]}-{n}'
        n += 1
    return WarehouseLocation.objects.create(
        sifra=sifra,
        naziv=location_name or sifra,
        odoo_location_id=location_id,
        odoo_location_path=(path or '')[:255],
        redoslijed=1000,
    )


MAGACIN_SYNC_SESSION_KEY = 'magacin_sync_job'
STOCK_SYNC_BATCH = 180
CATALOG_SYNC_BATCH = 20
PRICE_SYNC_BATCH = 80
DISCOVER_SYNC_BATCH = 300


def _decimal_price(value, default='0'):
    try:
        if value is False or value is None or value == '':
            return Decimal(default)
        return Decimal(str(value))
    except (ArithmeticError, ValueError, TypeError):
        return Decimal(default)


def _template_qty(template):
    best = 0
    for key in ('qty_available', 'virtual_available', 'free_qty'):
        best = max(best, _int(template.get(key)))
    return best


def _safe_sifra(raw, *, odoo_id, product_pk=None):
    from .odoo_import import _sifra_zauzeta, _unique_sifra

    text = '' if raw in (None, False) else str(raw).strip()
    if text and not _sifra_zauzeta(text, product_pk=product_pk):
        return text[:SIFRA_MAX_LENGTH]
    if product_pk:
        current = Product.objects.filter(pk=product_pk).values_list('sifra', flat=True).first()
        if current:
            return current
    return _unique_sifra('ODOO-T', odoo_id, product_pk=product_pk)[:SIFRA_MAX_LENGTH]


def _product_has_image(product):
    slika = getattr(product, 'slika', None)
    return bool(slika and getattr(slika, 'name', ''))


def _variation_has_image(variation):
    slika = getattr(variation, 'slika', None)
    return bool(slika and getattr(slika, 'name', ''))


def _norm_name_key(value):
    return ' '.join((value or '').split()).casefold()


def _bind_sync_product(product, odoo_id):
    """Poveži Magacin artikal na Odoo template ID (Odoo je izvor istine)."""
    if product is None or odoo_id is None:
        return product
    odoo_id = int(odoo_id)
    if product.odoo_template_id == odoo_id:
        return product
    clash = Product.objects.filter(odoo_template_id=odoo_id).exclude(pk=product.pk).first()
    if clash is not None:
        return clash
    product.odoo_template_id = odoo_id
    try:
        product.save(update_fields=['odoo_template_id'])
    except IntegrityError:
        return product
    return product


def _find_existing_sync_product(template):
    """Nađi isti artikal: Odoo ID, pa naziv, pa šifra / barkod."""
    from .odoo_import import _odoo_id

    odoo_id = _odoo_id(template.get('id'))
    if odoo_id is not None:
        product = Product.objects.filter(odoo_template_id=odoo_id).first()
        if product is not None:
            return product
        variation = (
            ProductVariation.objects.filter(odoo_template_id=odoo_id)
            .select_related('artikal')
            .first()
        )
        if variation is not None and variation.artikal_id:
            parent = variation.artikal
            if not parent.odoo_template_id or parent.odoo_template_id == odoo_id:
                return _bind_sync_product(parent, odoo_id)
        variant_ids = [
            vid for vid in (_odoo_id(v) for v in (template.get('product_variant_ids') or []))
            if vid
        ]
        if variant_ids:
            variation = (
                ProductVariation.objects.filter(odoo_variant_id__in=variant_ids)
                .select_related('artikal')
                .first()
            )
            if variation is not None and variation.artikal_id:
                parent = variation.artikal
                if not parent.odoo_template_id or parent.odoo_template_id == odoo_id:
                    return _bind_sync_product(parent, odoo_id)

    naziv, sifra, barkod = _template_identity(template)
    candidates = []
    if naziv:
        name_key = _norm_name_key(naziv)
        for row in Product.objects.filter(naziv__iexact=naziv):
            if _norm_name_key(row.naziv) == name_key:
                candidates.append(row)
    if not candidates and sifra:
        candidates.extend(list(Product.objects.filter(sifra__iexact=sifra)))
    if not candidates and barkod:
        candidates.extend(list(Product.objects.filter(barkod__iexact=barkod)))
    if not candidates:
        return None
    unique = {row.pk: row for row in candidates}
    product = max(unique.values(), key=_keeper_rank)
    if odoo_id is not None:
        product = _bind_sync_product(product, odoo_id)
    return product


def _apply_image_once(image_field, image_b64, filename):
    from .odoo_import import _process_odoo_image
    from .utils.images import save_prepared_product_image

    if image_field and getattr(image_field, 'name', ''):
        return False
    prepared = _process_odoo_image(image_b64, filename)
    if not prepared:
        return False
    save_prepared_product_image(image_field, prepared)
    return True


def sync_catalog_chunk(client, template_ids, *, start=0, limit=CATALOG_SYNC_BATCH):
    """
    Postojeći artikal (Odoo ID / šifra) — ažuriraj, ne dupliraj.
    Artikal koj nema na sajtu — kreiraj.
    Sliku postavi samo kad artikal još nema sliku.
    """
    from django.utils import timezone

    from .odoo_import import _odoo_template_name

    total = len(template_ids or [])
    chunk_ids = list(template_ids[start:start + max(1, int(limit))])
    stats = {
        'kreirano': 0,
        'azurirano': 0,
        'preskoceno': 0,
        'position': start + len(chunk_ids),
        'done': (start + len(chunk_ids)) >= total,
        'total': total,
    }
    if not chunk_ids:
        stats['done'] = True
        return stats

    templates = client.get_templates_by_ids(chunk_ids)
    by_id = {int(row['id']): row for row in templates if row.get('id')}

    # Slike se ne vuku ovdje — na Renderu XML-RPC slika timeouta cijeli chunk
    # pa novi artikli nikad ne stignu. Količina/lokacija ide u stock fazi.
    images = {}

    now = timezone.now()
    for tid in chunk_ids:
        template = by_id.get(int(tid))
        if not template:
            stats['preskoceno'] += 1
            continue
        action = _sync_one_template(
            client,
            template,
            image_b64=images.get(int(tid)),
            synced_at=now,
        )
        stats[action] = stats.get(action, 0) + 1
    return stats


def _create_sync_product(template, *, image_b64=None, synced_at=None):
    from django.utils import timezone

    from .odoo_import import _odoo_template_name

    odoo_id = int(template['id'])
    naziv = (_odoo_template_name(template) or f'Artikal {odoo_id}')[:200]
    barkod = ''
    if template.get('barcode') not in (False, None, ''):
        barkod = str(template.get('barcode'))[:BARKOD_MAX_LENGTH]
    cijena = _decimal_price(template.get('list_price'))
    sifra = _safe_sifra(template.get('default_code'), odoo_id=odoo_id)
    now = synced_at or timezone.now()
    qty = _template_qty(template)
    product = Product(
        naziv=naziv,
        sifra=sifra,
        barkod=barkod,
        cijena=cijena,
        odoo_template_id=odoo_id,
        magacin_sync_at=now,
        aktivan=True,
        na_stanju=qty > 0,
        stanje=qty,
    )
    if image_b64:
        _apply_image_once(product.slika, image_b64, f'odoo-template-{odoo_id}.jpg')
    try:
        with transaction.atomic():
            product.save()
    except IntegrityError:
        existing = Product.objects.filter(odoo_template_id=odoo_id).first()
        if existing is not None:
            return existing, False
        existing = Product.objects.filter(sifra=sifra).first()
        if existing is not None and not existing.odoo_template_id:
            existing.odoo_template_id = odoo_id
            existing.magacin_sync_at = now
            existing.save(update_fields=['odoo_template_id', 'magacin_sync_at'])
            return existing, False
        from .odoo_import import _unique_sifra

        product.sifra = _unique_sifra('ODOO-T', odoo_id)[:SIFRA_MAX_LENGTH]
        product.save()
    return product, True


def _sync_one_template(client, template, *, image_b64=None, synced_at=None):
    from django.utils import timezone

    from .odoo_import import _odoo_template_name

    odoo_id = int(template['id'])
    product = _find_existing_sync_product(template)
    if product is None:
        product, created = _create_sync_product(
            template, image_b64=image_b64, synced_at=synced_at,
        )
        _sync_template_variations(client, product, template, create_images=bool(image_b64))
        return 'kreirano' if created else 'azurirano'
    naziv = (_odoo_template_name(template) or f'Artikal {odoo_id}')[:200]
    barkod = ''
    if template.get('barcode') not in (False, None, ''):
        barkod = str(template.get('barcode'))[:BARKOD_MAX_LENGTH]
    cijena = _decimal_price(template.get('list_price'))
    now = synced_at or timezone.now()

    raw_code = template.get('default_code')
    new_sifra = _safe_sifra(raw_code, odoo_id=odoo_id, product_pk=product.pk)
    changed = (
        (product.naziv or '') != naziv
        or product.cijena != cijena
        or (product.barkod or '') != barkod
        or (new_sifra and new_sifra != product.sifra)
    )
    need_image = bool(image_b64) and not _product_has_image(product)
    if not changed and not need_image:
        if product.magacin_sync_at is None:
            product.magacin_sync_at = now
            product.save(update_fields=['magacin_sync_at'])
            return 'azurirano'
        return 'preskoceno'

    update_fields = ['magacin_sync_at']
    if changed:
        product.naziv = naziv
        product.cijena = cijena
        product.barkod = barkod
        product.magacin_sync_at = now
        update_fields.extend(['naziv', 'cijena', 'barkod'])
        if new_sifra and new_sifra != product.sifra:
            product.sifra = new_sifra
            update_fields.append('sifra')
    else:
        product.magacin_sync_at = now
    if need_image:
        if _apply_image_once(product.slika, image_b64, f'odoo-template-{odoo_id}.jpg'):
            update_fields.append('slika')
    product.save(update_fields=update_fields)
    _sync_template_variations(client, product, template, create_images=need_image)
    return 'azurirano'


def _sync_template_variations(client, product, template, *, create_images):
    variant_ids = template.get('product_variant_ids') or []
    clean_ids = [int(v) for v in variant_ids if v]
    if len(clean_ids) <= 1:
        return
    if not hasattr(client, 'get_product_variants'):
        return
    variants = client.get_product_variants(clean_ids, with_images=create_images) or []
    for variant in variants:
        vid = variant.get('id')
        if not vid:
            continue
        vid = int(vid)
        raw_code = variant.get('default_code')
        v_sifra = '' if raw_code in (None, False) else str(raw_code).strip()
        variation = ProductVariation.objects.filter(odoo_variant_id=vid).first()
        if variation is None and v_sifra:
            variation = ProductVariation.objects.filter(artikal=product, sifra=v_sifra).first()
            if variation is not None and not variation.odoo_variant_id:
                variation.odoo_variant_id = vid
        if variation and variation.artikal_id != product.pk:
            continue
        v_naziv = (variant.get('display_name') or variant.get('name') or product.naziv or '')[:100]
        v_cijena = _decimal_price(variant.get('lst_price'), default=str(product.cijena))

        if variation is None:
            from .odoo_import import _unique_sifra

            if not v_sifra:
                v_sifra = _unique_sifra('ODOO-V', vid)
            elif ProductVariation.objects.filter(sifra=v_sifra).exists():
                v_sifra = _unique_sifra('ODOO-V', vid)
            variation = ProductVariation(
                artikal=product,
                naziv=v_naziv[:100],
                sifra=v_sifra[:SIFRA_MAX_LENGTH],
                cijena=v_cijena,
                odoo_variant_id=vid,
            )
            variation.save()
            if create_images:
                image_b64 = variant.get('image_variant_1920') or variant.get('image_1920')
                if image_b64:
                    _apply_image_once(variation.slika, image_b64, f'odoo-variant-{vid}.jpg')
                    variation.save(update_fields=['slika'])
            continue

        fields_changed = (
            (variation.naziv or '') != v_naziv
            or variation.cijena != v_cijena
        )
        if v_sifra and v_sifra != variation.sifra:
            taken = ProductVariation.objects.filter(sifra=v_sifra).exclude(pk=variation.pk).exists()
            if not taken:
                variation.sifra = v_sifra[:SIFRA_MAX_LENGTH]
                fields_changed = True
        need_image = create_images and not _variation_has_image(variation)
        if need_image:
            image_b64 = variant.get('image_variant_1920') or variant.get('image_1920')
            if image_b64:
                _apply_image_once(variation.slika, image_b64, f'odoo-variant-{vid}.jpg')
                fields_changed = True
        if not fields_changed:
            continue
        variation.naziv = v_naziv
        variation.cijena = v_cijena
        variation.save()


def _sync_variation_prices(client, product, template):
    variant_ids = [int(v) for v in (template.get('product_variant_ids') or []) if v]
    if len(variant_ids) <= 1 or not hasattr(client, 'get_product_variants'):
        return 0
    variants = client.get_product_variants(variant_ids, with_images=False) or []
    updated = 0
    for variant in variants:
        vid = variant.get('id')
        if not vid:
            continue
        variation = ProductVariation.objects.filter(
            odoo_variant_id=int(vid), artikal=product,
        ).first()
        if variation is None:
            continue
        v_cijena = _decimal_price(variant.get('lst_price'), default=str(product.cijena))
        if variation.cijena != v_cijena:
            variation.cijena = v_cijena
            variation.save(update_fields=['cijena'])
            updated += 1
    return updated


def sync_price_chunk(client, template_ids):
    """Po Odoo ID postavi MPC kao list_price u Odoo. Ne kreira artikle."""
    stats = {'azurirano': 0, 'preskoceno': 0}
    ids = [int(tid) for tid in (template_ids or []) if tid]
    if not ids:
        return stats
    templates = client.get_templates_by_ids(ids) or []
    by_id = {int(row['id']): row for row in templates if row.get('id')}
    products = {
        int(prod.odoo_template_id): prod
        for prod in Product.objects.filter(odoo_template_id__in=ids)
        if prod.odoo_template_id
    }
    for tid in ids:
        template = by_id.get(tid)
        if not template:
            stats['preskoceno'] += 1
            continue
        product = products.get(tid) or _find_existing_sync_product(template)
        if not product:
            stats['preskoceno'] += 1
            continue
        cijena = _decimal_price(template.get('list_price'))
        barkod = ''
        if template.get('barcode') not in (False, None, ''):
            barkod = str(template.get('barcode'))[:BARKOD_MAX_LENGTH]
        new_sifra = _safe_sifra(template.get('default_code'), odoo_id=tid, product_pk=product.pk)
        fields = []
        if product.cijena != cijena:
            product.cijena = cijena
            fields.append('cijena')
        if new_sifra and new_sifra != product.sifra:
            product.sifra = new_sifra
            fields.append('sifra')
        if (product.barkod or '') != barkod:
            product.barkod = barkod
            fields.append('barkod')
        changed = bool(fields)
        if fields:
            product.save(update_fields=fields)
        var_updated = _sync_variation_prices(client, product, template)
        if changed or var_updated:
            stats['azurirano'] += 1
        else:
            stats['preskoceno'] += 1
    return stats


def cancel_sync(job, *, user=None):
    """Zaustavi tekući sync nakon trenutnog chunka. Ne briše već ažurirane artikle."""
    log = WarehouseSyncLog.objects.filter(pk=job.get('log_id')).first() if job else None
    started = job.get('started') or time.time()
    poruka = (
        f'Sync prekinut: {job.get("artikala") or 0} artikala, '
        f'{job.get("lokacija") or 0} lokacija, '
        f'{job.get("zaliha") or 0} zaliha. '
        f'Ažurirano {job.get("azurirano") or 0}, '
        f'preskočeno {job.get("preskoceno") or 0}.'
    )
    if log and log.status == WarehouseSyncLog.Status.U_TOKU:
        log.status = WarehouseSyncLog.Status.PREKINUT
        log.poruka = poruka[:400]
        log.finished_at = timezone.now()
        log.artikala = int(job.get('artikala') or 0)
        log.lokacija = int(job.get('lokacija') or 0)
        log.trajanje_sekundi = max(0, int(time.time() - started))
        log.save(update_fields=[
            'status', 'poruka', 'finished_at', 'artikala', 'lokacija', 'trajanje_sekundi',
        ])
    job = dict(job or {})
    job['done'] = True
    job['cancelled'] = True
    job['phase'] = 'done'
    return job


def _fail_log(log, started, message):
    log.status = WarehouseSyncLog.Status.GRESKA
    log.poruka = (message or '')[:400]
    log.finished_at = timezone.now()
    log.trajanje_sekundi = max(0, int(time.time() - started))
    log.save(update_fields=['status', 'poruka', 'finished_at', 'trajanje_sekundi'])
    _invalidate_last_sync_cache()
    return log


def _finish_log(log, started, *, poruka, artikala=0, lokacija=0):
    log.status = WarehouseSyncLog.Status.USPJEH
    log.poruka = (poruka or '')[:400]
    log.artikala = artikala
    log.lokacija = lokacija
    log.finished_at = timezone.now()
    log.trajanje_sekundi = max(0, int(time.time() - started))
    log.save(update_fields=[
        'status', 'poruka', 'artikala', 'lokacija', 'finished_at', 'trajanje_sekundi',
    ])
    _invalidate_last_sync_cache()
    return log


def _update_log_progress(log, started, poruka, *, artikala=0, lokacija=0):
    log.poruka = (poruka or '')[:400]
    log.artikala = artikala
    log.lokacija = lokacija
    log.trajanje_sekundi = max(0, int(time.time() - started))
    log.save(update_fields=['poruka', 'artikala', 'lokacija', 'trajanje_sekundi'])


def start_full_sync(*, user=None, product=None):
    """
    Odoo → Magacin: cijeli katalog.
    Postojeći artikal se ne duplira (samo količina/lokacija + već vezani podaci).
    Artikal koj nema na sajtu se kreira.
    """
    from .odoo_client import OdooClient, OdooError, odoo_je_konfigurisan

    started = time.time()
    log = WarehouseSyncLog.objects.create(
        status=WarehouseSyncLog.Status.U_TOKU,
        izvor='Odoo',
        korisnik=user if getattr(user, 'is_authenticated', False) else None,
    )
    if not odoo_je_konfigurisan():
        _fail_log(log, started, 'Odoo nije konfigurisan.')
        raise MagacinError('Odoo nije konfigurisan.')

    incremental = False
    stock_extra_ids = []
    changed_ids = []
    attach_site_odoo_products_to_magacin()
    try:
        if product is not None:
            template_id = getattr(product, 'odoo_template_id', None)
            if not template_id:
                _fail_log(log, started, 'Artikal nije povezan sa Odoo template ID-jem.')
                raise MagacinError('Artikal nije povezan sa Odoo. Prvo uradi puni Sync.')
            template_ids = [int(template_id)]
            phase = 'catalog'
            progress = 'Katalog: 1 artikal…'
        else:
            template_ids = []
            phase = 'discover'
            progress = 'Čitam cijeli Odoo katalog…'
    except OdooError as exc:
        _fail_log(log, started, str(exc))
        raise MagacinError(str(exc)) from exc

    _update_log_progress(log, started, progress)
    return {
        'log_id': log.pk,
        'started': started,
        'phase': phase,
        'template_ids': template_ids,
        'position': 0,
        'stock_ids': [],
        'stock_extra_ids': stock_extra_ids,
        'changed_ids': changed_ids,
        'discovered_ids': [],
        'discover_offset': 0,
        'stock_position': 0,
        'artikala': 0,
        'lokacija': 0,
        'zaliha': 0,
        'kreirano': 0,
        'azurirano': 0,
        'preskoceno': 0,
        'done': False,
        'incremental': incremental,
        'single_product_id': getattr(product, 'pk', None),
    }


def run_sync_chunk(job, *, user=None):
    """Odradi jedan chunk (katalog ili zalihe). job se mijenja u mjestu."""
    from .odoo_client import OdooClient, OdooError

    log = WarehouseSyncLog.objects.filter(pk=job.get('log_id')).first()
    started = job.get('started') or time.time()
    if not log:
        raise MagacinError('Sync sesija nije pronađena.')

    client = OdooClient.from_settings()
    phase = job.get('phase') or 'catalog'

    try:
        if phase == 'prices':
            template_ids = job.get('template_ids') or []
            position = int(job.get('position') or 0)
            batch = template_ids[position:position + PRICE_SYNC_BATCH]
            stats = sync_price_chunk(client, batch)
            job['position'] = position + len(batch)
            job['azurirano'] = int(job.get('azurirano') or 0) + int(stats.get('azurirano') or 0)
            job['preskoceno'] = int(job.get('preskoceno') or 0) + int(stats.get('preskoceno') or 0)
            _update_log_progress(
                log, started,
                f'Cijene: {job["position"]} / {len(template_ids)} '
                f'(ažurirano {job.get("azurirano") or 0})…',
                artikala=job.get('artikala') or 0,
            )
            if job['position'] >= len(template_ids):
                job['done'] = True
                job['phase'] = 'done'
                _finish_log(
                    log, started,
                    poruka=(
                        f'Cijene usklađene s Odoo: ažurirano {job.get("azurirano") or 0}, '
                        f'preskočeno {job.get("preskoceno") or 0}.'
                    ),
                    artikala=job.get('artikala') or 0,
                )
            return job

        if phase == 'discover':
            offset = int(job.get('discover_offset') or 0)
            page = []
            if hasattr(client, 'get_sale_template_ids_page'):
                page = client.get_sale_template_ids_page(
                    offset=offset, limit=DISCOVER_SYNC_BATCH,
                ) or []
            else:
                page = client.get_all_sale_template_ids() or []
            discovered = list(job.get('discovered_ids') or [])
            discovered.extend(int(tid) for tid in page if tid)
            job['discovered_ids'] = discovered
            job['discover_offset'] = offset + len(page)
            _update_log_progress(
                log, started,
                f'Čitam Odoo katalog: {len(discovered)} artikala…',
            )
            page_done = (
                not hasattr(client, 'get_sale_template_ids_page')
                or len(page) < DISCOVER_SYNC_BATCH
            )
            if page_done:
                all_odoo = set(discovered)
                local_ids = set(local_odoo_template_ids())
                missing = sorted(all_odoo - local_ids)
                job['discovered_ids'] = []
                job['odoo_ukupno'] = len(all_odoo)
                job['nedostaje'] = len(missing)
                # Uvijek uvezi SVE što fali. Zalihe idu za cijeli magacin.
                job['template_ids'] = missing
                job['incremental'] = False
                if job['template_ids']:
                    job['phase'] = 'catalog'
                    job['position'] = 0
                else:
                    job['phase'] = 'locations'
                _update_log_progress(
                    log, started,
                    f'Odoo {len(all_odoo)} artikala, na sajtu {len(local_ids)}, '
                    f'dodajem {len(missing)} novih…',
                    artikala=len(local_ids),
                )
            return job

        if phase == 'catalog':
            template_ids = job.get('template_ids') or []
            position = int(job.get('position') or 0)
            stats = sync_catalog_chunk(
                client,
                template_ids,
                start=position,
                limit=CATALOG_SYNC_BATCH,
            )
            job['position'] = stats.get('position', position)
            job['kreirano'] = int(job.get('kreirano') or 0) + int(stats.get('kreirano') or 0)
            job['azurirano'] = int(job.get('azurirano') or 0) + int(stats.get('azurirano') or 0)
            job['preskoceno'] = int(job.get('preskoceno') or 0) + int(stats.get('preskoceno') or 0)
            job['artikala'] = magacin_products_qs().count()
            _update_log_progress(
                log, started,
                f'Dodajem nove: {job["position"]} / {len(template_ids)} '
                f'(novo {job.get("kreirano") or 0})…',
                artikala=job['artikala'],
            )
            if stats.get('done'):
                still_missing = _missing_odoo_template_ids(template_ids)
                if still_missing and int(job.get('catalog_pass') or 0) < 1:
                    job['catalog_pass'] = 1
                    job['template_ids'] = still_missing
                    job['position'] = 0
                    job['nedostaje'] = len(still_missing)
                    _update_log_progress(
                        log, started,
                        f'Ponavljam {len(still_missing)} artikala koji nisu upisani…',
                        artikala=job['artikala'],
                    )
                else:
                    job['nedostaje'] = len(still_missing)
                    job['phase'] = 'locations'
            return job

        if phase == 'locations':
            locations = client.get_internal_locations() or []
            created = 0
            for record in locations:
                loc_id = record.get('id')
                if not loc_id:
                    continue
                path = (record.get('complete_name') or record.get('name') or '').strip()
                name = (record.get('name') or path).strip()
                location = _ensure_location_from_odoo(int(loc_id), name, path)
                if location:
                    created += 1
            job['lokacija'] = created
            job['phase'] = 'stock'
            stock_ids, variant_to_template = _collect_odoo_product_ids(job, client)
            job['stock_ids'] = stock_ids
            job['variant_to_template'] = variant_to_template
            job['stock_position'] = 0
            _update_log_progress(
                log, started,
                f'Lokacije: {created}. Zalihe…',
                artikala=job.get('artikala') or 0,
                lokacija=created,
            )
            return job

        if phase == 'stock':
            stock_ids = job.get('stock_ids') or []
            pos = int(job.get('stock_position') or 0)
            batch = stock_ids[pos:pos + STOCK_SYNC_BATCH]
            if batch:
                updated, touched = _apply_quant_batch(
                    client, batch, variant_to_template=job.get('variant_to_template'),
                )
                job['zaliha'] = int(job.get('zaliha') or 0) + updated
                job['stock_position'] = pos + len(batch)
            else:
                job['stock_position'] = pos
            _update_log_progress(
                log, started,
                f'Zalihe: {job.get("stock_position") or 0} / {len(stock_ids)}…',
                artikala=job.get('artikala') or 0,
                lokacija=job.get('lokacija') or 0,
            )
            if job['stock_position'] >= len(stock_ids):
                job['done'] = True
                job['phase'] = 'done'
                magacin_odoo = Product.objects.exclude(odoo_template_id=None).count()
                odoo_n = int(job.get('odoo_ukupno') or magacin_odoo)
                job['artikala'] = magacin_odoo
                if job.get('stock_only'):
                    poruka = (
                        f'Zalihe usklađene s Odoo: ažurirano {job.get("zaliha") or 0} količina, '
                        f'lokacija {job.get("lokacija") or 0}.'
                    )
                else:
                    poruka = (
                        f'Sync završen: Odoo {odoo_n} artikala, Magacin {magacin_odoo}. '
                        f'Novo {job.get("kreirano") or 0}, '
                        f'zaliha {job.get("zaliha") or 0}, '
                        f'lokacija {job.get("lokacija") or 0}.'
                        + (
                            f' Još fali {job.get("nedostaje")} po Odoo ID.'
                            if int(job.get('nedostaje') or 0) > 0
                            else ' Broj je usklađen.'
                        )
                    )
                _finish_log(
                    log, started,
                    poruka=poruka,
                    artikala=magacin_odoo,
                    lokacija=job.get('lokacija') or 0,
                )
            return job
    except OdooError as exc:
        _fail_log(log, started, str(exc))
        job['done'] = True
        job['error'] = str(exc)
        raise MagacinError(str(exc)) from exc
    except Exception as exc:
        _fail_log(log, started, str(exc))
        job['done'] = True
        job['error'] = str(exc)
        raise

    return job


def _collect_odoo_product_ids(job, client=None):
    """Odoo product.product ID-jevi za čitanje stock.quant (ne template ID)."""
    single_id = job.get('single_product_id')
    qs = magacin_products_qs().prefetch_related('varijacije')
    if single_id:
        qs = qs.filter(pk=single_id)
    elif job.get('incremental'):
        template_ids = job.get('template_ids') or []
        extra = job.get('stock_extra_ids') or []
        if template_ids:
            qs = qs.filter(odoo_template_id__in=template_ids)
        elif extra:
            qs = qs.filter(
                Q_templates_or_variants(extra)
            ).distinct()
        else:
            qs = qs.none()
    ids = []
    seen = set()
    template_ids_needed = []
    for prod in qs:
        variations = list(prod.varijacije.all())
        variant_ids = [
            int(variation.odoo_variant_id)
            for variation in variations
            if variation.odoo_variant_id
        ]
        if variant_ids:
            for vid in variant_ids:
                if vid not in seen:
                    seen.add(vid)
                    ids.append(vid)
        elif prod.odoo_template_id:
            template_ids_needed.append(int(prod.odoo_template_id))

    variant_to_template = {}
    if template_ids_needed and client is not None:
        variant_to_template = client.get_variant_ids_for_templates(template_ids_needed) or {}
        for vid, tid in variant_to_template.items():
            vid = int(vid)
            if vid not in seen:
                seen.add(vid)
                ids.append(vid)
    else:
        for tid in template_ids_needed:
            if tid not in seen:
                seen.add(tid)
                ids.append(tid)

    extra_ids = [int(extra_id) for extra_id in (job.get('stock_extra_ids') or []) if extra_id]
    leftover_extra = [vid for vid in extra_ids if vid not in seen]
    if leftover_extra and client is not None:
        extra_map = client.get_template_ids_for_variants(leftover_extra) or {}
        variant_to_template.update(extra_map)
    for extra_id in extra_ids:
        if extra_id not in seen:
            seen.add(extra_id)
            ids.append(extra_id)
    return ids, variant_to_template


def Q_templates_or_variants(odoo_ids):
    from django.db.models import Q

    ids = [int(i) for i in odoo_ids if i]
    return Q(varijacije__odoo_variant_id__in=ids) | Q(odoo_template_id__in=ids)


def _stock_key(product, variation):
    return (product.pk, variation.pk if variation is not None else None)


def _apply_quant_batch(client, odoo_product_ids, *, variant_to_template=None):
    quants = client.get_internal_stock_quants(odoo_product_ids)
    mapping = _odoo_id_to_local(
        odoo_product_ids,
        variant_to_template=variant_to_template,
        client=client,
    )
    synced_keys = set()
    for odoo_pid in odoo_product_ids or []:
        mapped = mapping.get(int(odoo_pid))
        if mapped:
            synced_keys.add(_stock_key(*mapped))
    reported = defaultdict(set)
    updated = 0
    touched = set()
    with transaction.atomic():
        for odoo_pid, loc_rows in (quants or {}).items():
            mapped = mapping.get(int(odoo_pid))
            if not mapped:
                continue
            prod, variation = mapped
            key = _stock_key(prod, variation)
            touched.add(prod.pk)
            for item in loc_rows or []:
                loc_id = item.get('location_id')
                loc_name = (item.get('location_name') or '').strip()
                loc_path = (item.get('location_path') or loc_name or '').strip()
                if not loc_id:
                    continue
                location = _ensure_location_from_odoo(int(loc_id), loc_name, loc_path)
                if location is None:
                    continue
                qty = max(0, _int(item.get('on_hand', item.get('quantity'))))
                reserved = max(0, _int(item.get('reserved_quantity')))
                if location.odoo_location_id:
                    reported[key].add(int(location.odoo_location_id))
                stock = get_or_create_stock(product=prod, variation=variation, location=location)
                if stock.kolicina == qty and stock.rezervisano == reserved:
                    continue
                stock.kolicina = qty
                stock.rezervisano = min(reserved, qty)
                stock.save(update_fields=['kolicina', 'rezervisano', 'azurirano'])
                updated += 1
        for prod_id, var_id in synced_keys:
            qs = WarehouseStock.objects.select_related('location').filter(product_id=prod_id)
            qs = qs.filter(variation_id=var_id) if var_id else qs.filter(variation__isnull=True)
            seen_locs = reported.get((prod_id, var_id), set())
            for stock in qs:
                loc = stock.location
                if not loc.odoo_location_id or int(loc.odoo_location_id) in seen_locs:
                    continue
                if is_uncountable_stock_location(loc):
                    continue
                if stock.kolicina == 0 and stock.rezervisano == 0:
                    continue
                stock.kolicina = 0
                stock.rezervisano = 0
                stock.save(update_fields=['kolicina', 'rezervisano', 'azurirano'])
                updated += 1
                touched.add(prod_id)
        for pk in touched:
            refresh_catalog_qty(Product.objects.get(pk=pk))
    return updated, touched


def _odoo_id_to_local(odoo_product_ids, *, variant_to_template=None, client=None):
    """Mapiraj samo product.product ID → lokalni artikal. Nikad template ID kao variant ID."""
    ids = {int(i) for i in odoo_product_ids if i}
    mapping = {}
    if not ids:
        return mapping
    variations = ProductVariation.objects.filter(odoo_variant_id__in=ids).select_related('artikal')
    for variation in variations:
        mapping[int(variation.odoo_variant_id)] = (variation.artikal, variation)
    leftover = ids - set(mapping)
    v2t = {int(k): int(v) for k, v in (variant_to_template or {}).items() if k and v}
    if leftover and client is not None:
        missing = leftover - set(v2t)
        if missing:
            v2t.update(client.get_template_ids_for_variants(missing) or {})
    template_ids = {v2t[vid] for vid in leftover if vid in v2t}
    if template_ids:
        by_template = {
            int(prod.odoo_template_id): prod
            for prod in Product.objects.filter(odoo_template_id__in=template_ids)
            if prod.odoo_template_id
        }
        missing_tids = [tid for tid in template_ids if tid not in by_template]
        if missing_tids and client is not None and hasattr(client, 'get_templates_by_ids'):
            templates = client.get_templates_by_ids(missing_tids) or []
            for tmpl in templates:
                found = _find_existing_sync_product(tmpl)
                if found is None or not found.odoo_template_id:
                    continue
                by_template[int(found.odoo_template_id)] = found
        for vid in leftover:
            tid = v2t.get(vid)
            if tid and tid in by_template:
                mapping[vid] = (by_template[tid], None)
    return mapping


def _price_template_ids(*, product=None):
    qs = magacin_products_qs().exclude(odoo_template_id=None).order_by('id')
    if product is not None:
        qs = qs.filter(pk=product.pk)
    seen = []
    used = set()
    for tid in qs.values_list('odoo_template_id', flat=True):
        tid = int(tid)
        if tid in used:
            continue
        used.add(tid)
        seen.append(tid)
    return seen


def start_price_sync(*, user=None, product=None):
    """Samo MPC: nađi isti artikal po Odoo ID i postavi cijenu kao list_price u Odoo."""
    from .odoo_client import odoo_je_konfigurisan

    started = time.time()
    log = WarehouseSyncLog.objects.create(
        status=WarehouseSyncLog.Status.U_TOKU,
        izvor='Odoo cijene',
        korisnik=user if getattr(user, 'is_authenticated', False) else None,
    )
    if not odoo_je_konfigurisan():
        _fail_log(log, started, 'Odoo nije konfigurisan.')
        raise MagacinError('Odoo nije konfigurisan.')
    attach_site_odoo_products_to_magacin()
    if product is not None and not getattr(product, 'odoo_template_id', None):
        _fail_log(log, started, 'Artikal nije povezan sa Odoo template ID-jem.')
        raise MagacinError('Artikal nije povezan sa Odoo. Prvo uradi puni Sync.')
    template_ids = _price_template_ids(product=product)
    _update_log_progress(log, started, 'Cijene iz Odoo…', artikala=len(template_ids))
    return {
        'log_id': log.pk,
        'started': started,
        'phase': 'prices',
        'template_ids': template_ids,
        'position': 0,
        'stock_ids': [],
        'stock_extra_ids': [],
        'changed_ids': [],
        'discovered_ids': [],
        'discover_offset': 0,
        'stock_position': 0,
        'artikala': len(template_ids),
        'lokacija': 0,
        'zaliha': 0,
        'kreirano': 0,
        'azurirano': 0,
        'preskoceno': 0,
        'done': False,
        'incremental': False,
        'price_only': True,
        'single_product_id': getattr(product, 'pk', None) if product is not None else None,
    }


def start_stock_sync(*, user=None, product=None):
    """Samo zalihe: nađi isti artikal po Odoo ID i postavi količinu kao u Odoo."""
    from .odoo_client import odoo_je_konfigurisan

    started = time.time()
    log = WarehouseSyncLog.objects.create(
        status=WarehouseSyncLog.Status.U_TOKU,
        izvor='Odoo zalihe',
        korisnik=user if getattr(user, 'is_authenticated', False) else None,
    )
    if not odoo_je_konfigurisan():
        _fail_log(log, started, 'Odoo nije konfigurisan.')
        raise MagacinError('Odoo nije konfigurisan.')
    attach_site_odoo_products_to_magacin()
    single_id = None
    if product is not None:
        if not getattr(product, 'odoo_template_id', None):
            _fail_log(log, started, 'Artikal nije povezan sa Odoo template ID-jem.')
            raise MagacinError('Artikal nije povezan sa Odoo. Prvo uradi puni Sync.')
        single_id = product.pk
    _update_log_progress(log, started, 'Zalihe iz Odoo…')
    return {
        'log_id': log.pk,
        'started': started,
        'phase': 'locations',
        'template_ids': [],
        'position': 0,
        'stock_ids': [],
        'stock_extra_ids': [],
        'changed_ids': [],
        'discovered_ids': [],
        'discover_offset': 0,
        'stock_position': 0,
        'artikala': magacin_products_qs().count(),
        'lokacija': 0,
        'zaliha': 0,
        'kreirano': 0,
        'azurirano': 0,
        'preskoceno': 0,
        'done': False,
        'incremental': False,
        'stock_only': True,
        'single_product_id': single_id,
    }


def persist_sync_job(job):
    if not job or not job.get('log_id'):
        return
    WarehouseSyncLog.objects.filter(pk=job['log_id']).update(job_data=job)


def load_running_sync_job():
    log = (
        WarehouseSyncLog.objects.filter(status=WarehouseSyncLog.Status.U_TOKU)
        .order_by('-started_at')
        .first()
    )
    if not log:
        return None
    job = log.job_data if isinstance(log.job_data, dict) else None
    if not job or job.get('done') or job.get('cancelled'):
        return None
    job['log_id'] = log.pk
    return job


def run_sync_until(job, *, user=None, max_chunks=10, max_seconds=22):
    """Odradi više chunkova u jednom HTTP requestu, bez Render timeouta."""
    started = time.time()
    chunks = 0
    while not job.get('done') and chunks < max_chunks:
        if time.time() - started >= max_seconds:
            break
        job = run_sync_chunk(job, user=user)
        persist_sync_job(job)
        chunks += 1
        if job.get('error') or job.get('cancelled'):
            break
    return job


def sync_from_odoo(*, user=None, product=None):
    """Kompatibilnost: odradi cijeli sync u petlji (testovi / mali skup / Render shell)."""
    job = start_full_sync(user=user, product=product)
    persist_sync_job(job)
    while not job.get('done'):
        job = run_sync_chunk(job, user=user)
        persist_sync_job(job)
    if job.get('error'):
        raise MagacinError(job['error'])
    return WarehouseSyncLog.objects.filter(pk=job.get('log_id')).first()


@transaction.atomic
def skini_sa_sajta(product, *, user=None):
    """Rasprodato: sve magacinske količine na 0, artikal nestaje sa sajta."""
    stocks = list(
        countable_stock_qs(WarehouseStock.objects.filter(product=product))
        .filter(Q(kolicina__gt=0) | Q(rezervisano__gt=0))
        .select_related('location', 'variation')
    )
    for stock in stocks:
        apply_movement(
            product=product,
            variation=stock.variation,
            location=stock.location,
            tip=WarehouseMovement.Tip.KOREKCIJA,
            kolicina=0,
            napomena='Skini sa stanja (rasprodato)',
            user=user,
        )
    ProductVariation.objects.filter(artikal=product).update(stanje=0, na_stanju=False)
    clear_mp_without_location(product)
    product.stanje = 0
    product.na_stanju = False
    product.save(update_fields=['stanje', 'na_stanju'])
    return product


@transaction.atomic
def ubaci_na_sajt(product):
    """Stavi artikal na sajt bez magacinske zalihe (ima ga u radnji)."""
    update_fields = []
    if not product.aktivan:
        product.aktivan = True
        update_fields.append('aktivan')
    if not product.na_stanju:
        product.na_stanju = True
        update_fields.append('na_stanju')
    if not product.stanje or product.stanje < 1:
        product.stanje = 1
        update_fields.append('stanje')
    if update_fields:
        product.save(update_fields=update_fields)

    for var in product.varijacije.all():
        var_fields = []
        if not var.na_stanju:
            var.na_stanju = True
            var_fields.append('na_stanju')
        if not var.stanje or var.stanje < 1:
            var.stanje = 1
            var_fields.append('stanje')
        if var_fields:
            var.save(update_fields=var_fields)
    return product


def active_popis():
    from django.db import OperationalError, ProgrammingError

    try:
        return (
            MagacinPopis.objects.filter(status=MagacinPopis.Status.U_TOKU)
            .order_by('-kreiran')
            .first()
        )
    except (ProgrammingError, OperationalError):
        return None


def start_popis(*, user=None):
    existing = active_popis()
    if existing:
        return existing
    return MagacinPopis.objects.create(
        kreirao=user if getattr(user, 'is_authenticated', False) else None,
    )


def paused_popisi():
    from django.db import OperationalError, ProgrammingError
    from django.db.models import Count, Sum

    try:
        return (
            MagacinPopis.objects.filter(status=MagacinPopis.Status.PAUZIRAN)
            .annotate(
                n_stavke=Count('stavke'),
                n_kom=Sum('stavke__kolicina'),
            )
            .order_by('-azuriran', '-kreiran')
        )
    except (ProgrammingError, OperationalError):
        return MagacinPopis.objects.none()


def pause_popis(popis):
    if popis.status == MagacinPopis.Status.ZAVRSEN:
        raise MagacinError('Završen popis se ne može pauzirati.')
    if popis.status == MagacinPopis.Status.PAUZIRAN:
        return popis
    popis.status = MagacinPopis.Status.PAUZIRAN
    popis.save(update_fields=['status'])
    return popis


def resume_popis(popis):
    if popis.status == MagacinPopis.Status.ZAVRSEN:
        raise MagacinError('Završen popis se ne može nastaviti.')
    current = active_popis()
    if current and current.pk != popis.pk:
        pause_popis(current)
    if popis.status != MagacinPopis.Status.U_TOKU:
        popis.status = MagacinPopis.Status.U_TOKU
        popis.save(update_fields=['status'])
    return popis


def add_popis_stavka(popis, *, product, qty, variation=None):
    qty = max(1, _int(qty))
    if popis.status != MagacinPopis.Status.U_TOKU:
        raise MagacinError('Ovaj popis nije u toku. Nastavi ga pa dodaj artikle.')
    if variation and variation.artikal_id != product.pk:
        raise MagacinError('Varijacija ne pripada artiklu.')
    naziv = product.naziv
    sifra = product.sifra or ''
    if variation:
        naziv = f'{product.naziv} {variation.naziv}'.strip()
        sifra = variation.sifra or product.sifra or ''
    existing = popis.stavke.filter(product=product, variation=variation).first()
    if existing:
        existing.kolicina += qty
        next_rb = (popis.stavke.order_by('-redoslijed').values_list('redoslijed', flat=True).first() or 0) + 1
        existing.redoslijed = next_rb
        existing.save(update_fields=['kolicina', 'redoslijed'])
        return existing
    next_rb = (popis.stavke.order_by('-redoslijed').values_list('redoslijed', flat=True).first() or 0) + 1
    return MagacinPopisStavka.objects.create(
        popis=popis,
        product=product,
        variation=variation,
        naziv=naziv[:200],
        sifra=(sifra or '')[:SIFRA_MAX_LENGTH],
        kolicina=qty,
        redoslijed=next_rb,
    )


def finish_popis(popis):
    if popis.status == MagacinPopis.Status.ZAVRSEN:
        return popis
    popis.status = MagacinPopis.Status.ZAVRSEN
    popis.zavrsen_at = timezone.now()
    popis.save(update_fields=['status', 'zavrsen_at'])
    return popis


def set_popis_stavka_qty(popis, stavka_id, qty):
    if popis.status != MagacinPopis.Status.U_TOKU:
        raise MagacinError('Ovaj popis nije u toku.')
    stavka = popis.stavke.filter(pk=stavka_id).first()
    if not stavka:
        raise MagacinError('Stavka nije pronađena.')
    qty = _int(qty)
    if qty <= 0:
        stavka.delete()
        return None
    if stavka.kolicina != qty:
        stavka.kolicina = qty
        stavka.save(update_fields=['kolicina'])
    return stavka


def remove_popis_stavka(popis, stavka_id):
    if popis.status != MagacinPopis.Status.U_TOKU:
        raise MagacinError('Ovaj popis nije u toku.')
    stavka = popis.stavke.filter(pk=stavka_id).first()
    if not stavka:
        raise MagacinError('Stavka nije pronađena.')
    stavka.delete()
    return None


def vp_cijena_from_mpc(mpc):
    amount = Decimal(str(mpc or 0))
    if amount <= 0:
        return Decimal('0.00')
    return (amount / VP_MPC_DIVISOR).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


def vp_cijena(product, variation=None):
    mpc = variation.prikazna_cijena if variation else product.prikazna_cijena
    return vp_cijena_from_mpc(mpc), Decimal(str(mpc or 0)).quantize(Decimal('0.01'))


def active_vp_narudzba():
    from django.db import OperationalError, ProgrammingError

    try:
        return (
            MagacinVpNarudzba.objects.filter(status=MagacinVpNarudzba.Status.U_TOKU)
            .order_by('-kreiran')
            .first()
        )
    except (ProgrammingError, OperationalError):
        return None


def start_vp_narudzba(*, user=None):
    existing = active_vp_narudzba()
    if existing:
        return existing
    return MagacinVpNarudzba.objects.create(
        kreirao=user if getattr(user, 'is_authenticated', False) else None,
    )


def set_vp_customer(draft, customer):
    if draft.status != MagacinVpNarudzba.Status.U_TOKU:
        raise MagacinError('Ova VP narudžba je završena.')
    if not customer:
        raise MagacinError('Izaberi kupca.')
    draft.customer = customer
    draft.ime_prezime = (customer.ime_prezime or '')[:200]
    draft.telefon = (customer.telefon or '')[:30]
    draft.adresa = (customer.adresa or '')[:300]
    draft.grad = (customer.grad or '')[:100]
    draft.email = customer.email or ''
    draft.postanski_broj = (customer.postanski_broj or '')[:20]
    draft.save(update_fields=[
        'customer', 'ime_prezime', 'telefon', 'adresa', 'grad', 'email', 'postanski_broj', 'azuriran',
    ])
    return draft


def add_vp_stavka(draft, *, product, qty, variation=None, mp_ok=False, cijena=None):
    qty = max(1, _int(qty))
    if draft.status != MagacinVpNarudzba.Status.U_TOKU:
        raise MagacinError('Ova VP narudžba je završena.')
    if variation and variation.artikal_id != product.pk:
        raise MagacinError('Varijacija ne pripada artiklu.')
    naziv = product.naziv
    sifra = product.sifra or ''
    if variation:
        naziv = f'{product.naziv} {variation.naziv}'.strip()
        sifra = variation.sifra or product.sifra or ''
    catalog_cijena, mpc = vp_cijena(product, variation)
    if cijena is None or cijena == '':
        cijena = catalog_cijena
    else:
        try:
            cijena = Decimal(str(cijena)).quantize(Decimal('0.01'))
        except (InvalidOperation, TypeError, ValueError):
            cijena = catalog_cijena
        if cijena < 0:
            cijena = catalog_cijena
    existing = draft.stavke.filter(product=product, variation=variation).first()
    if existing:
        existing.kolicina += qty
        existing.cijena = cijena
        existing.mpc = mpc
        fields = ['kolicina', 'cijena', 'mpc']
        if mp_ok and not existing.mp_ok:
            existing.mp_ok = True
            fields.append('mp_ok')
        existing.save(update_fields=fields)
        return existing
    next_rb = (draft.stavke.order_by('-redoslijed').values_list('redoslijed', flat=True).first() or 0) + 1
    return MagacinVpStavka.objects.create(
        narudzba=draft,
        product=product,
        variation=variation,
        naziv=naziv[:200],
        sifra=(sifra or '')[:SIFRA_MAX_LENGTH],
        kolicina=qty,
        cijena=cijena,
        mpc=mpc,
        mp_ok=bool(mp_ok),
        redoslijed=next_rb,
    )


def set_vp_stavka_qty(draft, stavka_id, qty, *, mp_ok=False):
    if draft.status != MagacinVpNarudzba.Status.U_TOKU:
        raise MagacinError('Ova VP narudžba je završena.')
    qty = max(1, _int(qty))
    stavka = draft.stavke.filter(pk=stavka_id).first()
    if not stavka:
        raise MagacinError('Stavka nije pronađena.')
    fields = []
    if stavka.kolicina != qty:
        stavka.kolicina = qty
        fields.append('kolicina')
    if mp_ok and not stavka.mp_ok:
        stavka.mp_ok = True
        fields.append('mp_ok')
    if fields:
        stavka.save(update_fields=fields)
    return stavka


def remove_vp_stavka(draft, stavka_id):
    if draft.status != MagacinVpNarudzba.Status.U_TOKU:
        raise MagacinError('Ova VP narudžba je završena.')
    deleted, _ = draft.stavke.filter(pk=stavka_id).delete()
    if not deleted:
        raise MagacinError('Stavka nije pronađena.')


_BULK_SIFRA_RE = re.compile(r'šifra\s*:\s*([A-Za-z0-9._/-]+)', re.IGNORECASE)
_BULK_MONEY_RE = re.compile(
    r'^\s*\d+(?:[.,]\d{1,2})?\s*(?:KM)?\s*$',
    re.IGNORECASE,
)
_BULK_QTY_RE = re.compile(r'^\s*\d+\s*$')
_BULK_ROW_START_RE = re.compile(r'^\s*(\d+)\s*[.,;\t]+\s*(.*)$')


def _bulk_norm_name(value):
    text = _BULK_SIFRA_RE.sub('', value or '')
    return ' '.join(text.split()).casefold()


def _bulk_is_header(line):
    n = (line or '').casefold()
    if 'artikal' in n and any(token in n for token in ('kol', 'cijena', 'ukupno')):
        return True
    stripped = n.strip().strip(',')
    return stripped in {'#', 'rb', 'r.b.', 'r.b'}


def _bulk_is_money(value):
    return bool(_BULK_MONEY_RE.match(value or ''))


def _bulk_parse_money(value):
    text = (value or '').replace('KM', '').replace(' ', '').replace(',', '.')
    try:
        amount = Decimal(text)
    except (InvalidOperation, TypeError, ValueError):
        return None
    if amount < 0:
        return None
    return amount.quantize(Decimal('0.01'))


def _bulk_is_row_start(line):
    match = _BULK_ROW_START_RE.match(line or '')
    if not match:
        return False
    rest = (match.group(2) or '').strip()
    if not rest:
        return True
    first = rest.split('\t', 1)[0].split(',', 1)[0].strip()
    if _bulk_is_money(first) or _BULK_QTY_RE.match(first):
        return False
    n = rest.casefold()
    if 'artikal' in n and any(token in n for token in ('kol', 'cijena', 'ukupno')):
        return False
    return True


def _bulk_clean_field(value):
    return (value or '').replace('\n', ' ').strip().strip(',').strip()


def _bulk_split_fields(chunk):
    text = (chunk or '').strip()
    if not text:
        return []
    if text.count('\t') >= 2:
        return [_bulk_clean_field(part) for part in text.split('\t')]
    reader = csv.reader(StringIO(re.sub(r'\s*\n\s*', ' ', text)))
    try:
        return [_bulk_clean_field(part) for part in next(reader)]
    except StopIteration:
        return [_bulk_clean_field(text)]


def _bulk_parse_chunk(chunk):
    fields = [part for part in _bulk_split_fields(chunk) if part]
    if not fields:
        return None
    joined = ' '.join(fields)
    if _bulk_is_header(joined):
        return None
    if fields and _BULK_QTY_RE.match(fields[0]) and len(fields) > 1:
        fields = fields[1:]
    ukupno = None
    if fields and _bulk_is_money(fields[-1]):
        ukupno = _bulk_parse_money(fields[-1])
        fields = fields[:-1]
    cijena = None
    if fields and _bulk_is_money(fields[-1]):
        cijena = _bulk_parse_money(fields[-1])
        fields = fields[:-1]
    elif ukupno is not None:
        cijena = ukupno
        ukupno = None
    qty = 1
    if fields and _BULK_QTY_RE.match(fields[-1]):
        qty = max(1, int(fields[-1]))
        fields = fields[:-1]
    artikal = ' '.join(fields).strip()
    sifra_match = _BULK_SIFRA_RE.search(artikal)
    sifra = sifra_match.group(1) if sifra_match else ''
    naziv = _BULK_SIFRA_RE.sub('', artikal)
    naziv = ' '.join(naziv.split()).strip()
    if not naziv:
        return None
    return {
        'naziv': naziv,
        'sifra': sifra,
        'qty': qty,
        'cijena': cijena,
        'ukupno': ukupno,
    }


def parse_vp_bulk_text(text):
    """Parsiraj zalijepljenu VP tabelu (#, Artikal, Kol., Cijena, Ukupno)."""
    raw = (text or '').replace('\r\n', '\n').replace('\r', '\n')
    lines = [line.strip() for line in raw.split('\n')]
    chunks = []
    current = []
    for line in lines:
        if not line or _bulk_is_header(line):
            continue
        if _bulk_is_row_start(line) and current:
            chunks.append('\n'.join(current))
            current = [line]
        else:
            current.append(line)
    if current:
        chunks.append('\n'.join(current))
    rows = []
    for chunk in chunks:
        parsed = _bulk_parse_chunk(chunk)
        if parsed:
            rows.append(parsed)
    return rows


def _vp_bulk_name_index():
    index = {}
    products = magacin_products_qs().prefetch_related('varijacije')
    for product in products:
        variations = list(product.varijacije.all())
        key = _bulk_norm_name(product.naziv)
        if key and len(variations) <= 1:
            variation = variations[0] if variations else None
            index.setdefault(key, []).append((product, variation))
        for variation in variations:
            combined = _bulk_norm_name(f'{product.naziv} {variation.naziv}')
            if combined:
                index.setdefault(combined, []).append((product, variation))
            var_key = _bulk_norm_name(variation.naziv)
            if var_key and len(var_key) >= 12:
                index.setdefault(var_key, []).append((product, variation))
    return index


def _vp_bulk_match(naziv, index):
    hits = index.get(_bulk_norm_name(naziv)) or []
    if not hits:
        return None
    for product, variation in hits:
        if variation is None:
            return product, None
    return hits[0]


def add_vp_bulk_stavke(draft, text):
    if draft is None or draft.status != MagacinVpNarudzba.Status.U_TOKU:
        raise MagacinError('Nema otvorene VP narudžbe.')
    rows = parse_vp_bulk_text(text)
    if not rows:
        raise MagacinError(
            'Nema stavki za unos. Zalijepi tabelu s kolonama Artikal, Kol., Cijena.'
        )
    index = _vp_bulk_name_index()
    added = []
    skipped = []
    for row in rows:
        hit = _vp_bulk_match(row['naziv'], index)
        if hit is None:
            skipped.append({'naziv': row['naziv'], 'razlog': 'nema u bazi'})
            continue
        product, variation = hit
        available = stock_totals(product, variation)['dostupno']
        existing = draft.stavke.filter(product=product, variation=variation).first()
        needed = row['qty'] + (existing.kolicina if existing else 0)
        mp_ok = needed > available
        stavka = add_vp_stavka(
            draft,
            product=product,
            variation=variation,
            qty=row['qty'],
            mp_ok=mp_ok,
            cijena=row['cijena'],
        )
        added.append({
            'naziv': stavka.naziv,
            'kolicina': row['qty'],
            'cijena': str(stavka.cijena),
            'mp_ok': bool(stavka.mp_ok),
        })
    if added and not draft.bulk:
        draft.bulk = True
        draft.save(update_fields=['bulk', 'azuriran'])
    return {'added': added, 'skipped': skipped}


def vp_draft_totals(osnova, *, bulk=False):
    from .cart import PDV_STOPA

    osnova = Decimal(str(osnova or 0)).quantize(Decimal('0.01'))
    if not bulk:
        return {
            'osnova': osnova,
            'pdv': Decimal('0.00'),
            'ukupno_sa_pdv': osnova,
            'bulk': False,
        }
    pdv = (osnova * PDV_STOPA).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    return {
        'osnova': osnova,
        'pdv': pdv,
        'ukupno_sa_pdv': (osnova + pdv).quantize(Decimal('0.01')),
        'bulk': True,
    }


def finish_vp_narudzba(draft, *, user=None, rezervacija=False):
    if draft.status == MagacinVpNarudzba.Status.ZAVRSENA:
        return draft.order
    if not (draft.ime_prezime or '').strip() or not (draft.telefon or '').strip():
        raise MagacinError('Izaberi ili dodaj kupca.')
    stavke = list(draft.stavke.select_related('product', 'variation'))
    if not stavke:
        raise MagacinError('Dodaj barem jedan artikal.')
    lines = []
    mp_names = []
    for row in stavke:
        if not row.product_id:
            raise MagacinError(f'Artikal „{row.naziv}” više ne postoji.')
        available = stock_totals(row.product, row.variation)['dostupno']
        shortfall = max(0, row.kolicina - available)
        if shortfall > 0 and not row.mp_ok:
            raise MagacinError(
                f'„{row.naziv}” nema dovoljno zalihe u magacinu ({available}). '
                'Provjeri maloprodaju pa dodaj, ili makni stavku.'
            )
        if shortfall > 0 and row.mp_ok:
            mp_names.append(row.naziv)
        bazna = row.variation.bazna_cijena if row.variation else row.product.bazna_cijena
        lines.append({
            'product': row.product,
            'variation': row.variation,
            'qty': row.kolicina,
            'cijena': row.cijena,
            'bazna': bazna,
            'shortfall': shortfall,
        })
    medjuzbir = sum((line['cijena'] * line['qty'] for line in lines), Decimal('0.00'))
    totals = vp_draft_totals(medjuzbir, bulk=bool(getattr(draft, 'bulk', False)))
    email = (draft.email or '').strip() or 'vp@opremazaribolov.ba'
    adresa = (draft.adresa or '').strip() or 'VP narudžba'
    grad = (draft.grad or '').strip() or '—'
    napomena = 'VP narudžba'
    if totals['bulk']:
        napomena = (
            f'{napomena}\nBulk: PDV 17% = {totals["pdv"]} KM, '
            f'ukupno sa PDV {totals["ukupno_sa_pdv"]} KM'
        )
        medjuzbir = totals['ukupno_sa_pdv']
    if mp_names:
        napomena = f'{napomena}\nMaloprodaja: {", ".join(mp_names)}'
    with transaction.atomic():
        order = Order.objects.create(
            ime_prezime=draft.ime_prezime[:200],
            email=email[:254],
            telefon=draft.telefon[:30],
            adresa=adresa[:300],
            grad=grad[:100],
            postanski_broj=(draft.postanski_broj or '')[:20],
            napomena=napomena,
            medjuzbir=medjuzbir,
            dostava=Decimal('0.00'),
            popust=Decimal('0.00'),
            ukupno=medjuzbir,
            status=Order.Status.REZERVACIJA if rezervacija else Order.Status.NOVA,
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
            reserve_for_order(
                order,
                product,
                line['qty'] - line['shortfall'],
                variation=variation,
                user=user,
                napomena=f'VP rezervacija #{order.broj}',
            )
        order.lager_status = Order.LagerStatus.REZERVISANO
        order.save(update_fields=['lager_status'])
        draft.status = MagacinVpNarudzba.Status.ZAVRSENA
        draft.order = order
        draft.zavrsen_at = timezone.now()
        draft.save(update_fields=['status', 'order', 'zavrsen_at'])
    return order


def deduct_for_order(product, qty, *, variation=None, user=None, napomena=''):
    """Skini slobodnu količinu (bez rezervisanog). Vraća koliko nije skinuto."""
    remaining = max(0, _int(qty))
    if remaining <= 0:
        return 0
    rows, _ = location_rows(product, variation)
    for row in rows:
        if remaining <= 0:
            break
        take = min(row['dostupno'], remaining)
        if take <= 0:
            continue
        apply_movement(
            product=product,
            variation=variation,
            location=row['location'],
            tip=WarehouseMovement.Tip.PRODAJA,
            kolicina=take,
            napomena=napomena or 'Ručna narudžba',
            user=user,
        )
        remaining -= take
    return remaining


def reserve_for_order(order, product, qty, *, variation=None, user=None, napomena='', location=None):
    """Rezerviši slobodnu zalihu za narudžbu. Vraća koliko nije rezervisano."""
    remaining = max(0, _int(qty))
    if remaining <= 0:
        return 0
    rows, _ = location_rows(product, variation)
    if location is not None:
        loc_pk = getattr(location, 'pk', location)
        rows = [row for row in rows if row['location'].pk == loc_pk]
    for row in rows:
        if remaining <= 0:
            break
        take = min(row['dostupno'], remaining)
        if take <= 0:
            continue
        stock = get_or_create_stock(product=product, variation=variation, location=row['location'])
        apply_movement(
            product=product,
            variation=variation,
            location=row['location'],
            tip=WarehouseMovement.Tip.REZERVACIJA,
            kolicina=1,
            rezervisano=stock.rezervisano + take,
            napomena=napomena or f'Rezervacija #{order.broj}',
            user=user,
        )
        OrderStockHold.objects.create(
            narudzba=order,
            product=product,
            variation=variation,
            location=row['location'],
            kolicina=take,
            status=OrderStockHold.Status.REZERVISANO,
        )
        remaining -= take
    return remaining


def is_prenos_mp_order(order):
    state = order.pick_state if isinstance(getattr(order, 'pick_state', None), dict) else {}
    if state.get('kind') == 'prenos_mp':
        return True
    return (getattr(order, 'ime_prezime', '') or '').strip().casefold() == 'prenos u mp'


def is_vp_order(order):
    cached = getattr(order, '_is_vp_order', None)
    if cached is not None:
        return cached
    if (getattr(order, 'napomena', '') or '').startswith('VP narudžba'):
        order._is_vp_order = True
        return True
    found = MagacinVpNarudzba.objects.filter(order_id=order.pk).exists()
    order._is_vp_order = found
    return found


def vp_waiting_print_ids():
    """Samo validatovane VP koje još čekaju „Štampaj zapakovano” — nisu u _unvalidated_orders_qs."""
    return MagacinVpNarudzba.objects.filter(
        status=MagacinVpNarudzba.Status.ZAVRSENA,
        order_id__isnull=False,
        order__zapakovana=False,
        order__lager_status=Order.LagerStatus.VALIDIRANO,
    ).exclude(
        order__status=Order.Status.OTKAZANA,
    ).values_list('order_id', flat=True)


def _item_variation_filter(variation):
    if variation is None:
        return {'varijacija__isnull': True}
    return {'varijacija': variation}


def _hold_variation_filter(variation):
    if variation is None:
        return {'variation__isnull': True}
    return {'variation': variation}


def _clear_pick_state_for_item(order, item_id):
    state = dict(order.pick_state or {}) if isinstance(getattr(order, 'pick_state', None), dict) else {}
    if not state or not item_id:
        return
    prefix = f'{item_id}:'
    changed = False
    for key in list(state.keys()):
        row = state.get(key)
        if isinstance(row, dict) and (
            row.get('item_id') == item_id or str(key).startswith(prefix)
        ):
            del state[key]
            changed = True
    if changed:
        order.pick_state = state
        order.save(update_fields=['pick_state'])


def _fresh_order_items(order):
    if hasattr(order, '_prefetched_objects_cache'):
        order._prefetched_objects_cache.pop('stavke', None)
        order._prefetched_objects_cache.pop('magacin_holds', None)
    return list(OrderItem.objects.filter(narudzba_id=order.pk))


def recalculate_order_totals(order):
    items = _fresh_order_items(order)
    medjuzbir = sum(
        (Decimal(str(item.cijena or 0)) * int(item.kolicina or 0) for item in items),
        Decimal('0.00'),
    ).quantize(Decimal('0.01'))
    popust = Decimal(str(order.popust or 0)).quantize(Decimal('0.01'))
    if is_vp_order(order):
        dostava = Decimal('0.00')
    elif getattr(order, 'izvor', '') == Order.Izvor.MAGACIN:
        from .pricing import _standardna_dostava

        dostava, _, _, _ = _standardna_dostava(medjuzbir)
    else:
        dostava = Decimal(str(order.dostava or 0)).quantize(Decimal('0.01'))
    ukupno = (medjuzbir - popust + dostava).quantize(Decimal('0.01'))
    if ukupno < 0:
        ukupno = Decimal('0.00')
    order.medjuzbir = medjuzbir
    order.dostava = dostava
    order.ukupno = ukupno
    order.save(update_fields=['medjuzbir', 'dostava', 'ukupno'])
    return order


def release_holds_for_product(order, product, variation=None, qty=None, *, user=None):
    holds = list(
        order.magacin_holds.filter(
            product=product,
            status=OrderStockHold.Status.REZERVISANO,
            **_hold_variation_filter(variation),
        ).order_by('-kolicina', '-pk')
    )
    remaining = qty if qty is not None else sum(hold.kolicina for hold in holds)
    remaining = max(0, _int(remaining))
    for hold in holds:
        if remaining <= 0:
            break
        take = min(hold.kolicina, remaining)
        if take <= 0:
            continue
        stock = get_or_create_stock(
            product=hold.product, variation=hold.variation, location=hold.location,
        )
        new_reserved = max(0, stock.rezervisano - take)
        apply_movement(
            product=hold.product,
            variation=hold.variation,
            location=hold.location,
            tip=WarehouseMovement.Tip.REZERVACIJA,
            kolicina=1,
            rezervisano=new_reserved,
            napomena=f'Izmjena #{order.broj}',
            user=user,
        )
        if take >= hold.kolicina:
            hold.status = OrderStockHold.Status.OTKAZANO
            hold.save(update_fields=['status'])
        else:
            hold.kolicina -= take
            hold.save(update_fields=['kolicina'])
        remaining -= take
    return remaining


def _order_item_unit_price(order, product, variation=None):
    if is_vp_order(order):
        cijena, _mpc = vp_cijena(product, variation)
        bazna = variation.bazna_cijena if variation else product.bazna_cijena
        return cijena, bazna
    cijena = variation.prikazna_cijena if variation else product.prikazna_cijena
    bazna = variation.bazna_cijena if variation else product.bazna_cijena
    return cijena, bazna


def _assert_order_editable(order):
    if is_prenos_mp_order(order):
        raise MagacinError('Prenos u MP se ne može mijenjati.')
    if order.lager_status == Order.LagerStatus.VALIDIRANO:
        raise MagacinError('Validirana narudžba se ne može mijenjati.')
    if order.lager_status == Order.LagerStatus.OTKAZANO or order.status == Order.Status.OTKAZANA:
        raise MagacinError('Otkazana narudžba se ne može mijenjati.')


def order_is_editable(order):
    try:
        _assert_order_editable(order)
        return True
    except MagacinError:
        return False


def _note_maloprodaja(order, product, variation=None):
    naziv = f'{product.naziv} {variation.naziv}'.strip() if variation else product.naziv
    note = (order.napomena or '').strip()
    marker = f'Maloprodaja: {naziv}'
    if marker in note:
        return
    extra = marker if 'Maloprodaja:' in note else f'Maloprodaja: {naziv}'
    order.napomena = f'{note}\n{extra}'.strip() if note else extra
    order.save(update_fields=['napomena'])


@transaction.atomic
def add_item_to_order(order, *, product, qty, variation=None, mp_ok=False, user=None):
    _assert_order_editable(order)
    qty = max(1, _int(qty))
    if variation and variation.artikal_id != product.pk:
        raise MagacinError('Varijacija ne pripada artiklu.')
    available = stock_totals(product, variation)['dostupno']
    shortfall = max(0, qty - available)
    if shortfall > 0 and not mp_ok:
        raise MagacinError(
            f'„{product.naziv}” nema dovoljno zalihe u magacinu ({available}). '
            'Provjeri maloprodaju pa dodaj, ili makni stavku.'
        )
    cijena, bazna = _order_item_unit_price(order, product, variation)
    existing = OrderItem.objects.filter(
        narudzba_id=order.pk, artikal=product, **_item_variation_filter(variation),
    ).first()
    if existing:
        existing.kolicina += qty
        existing.kolicina_pokupljeno = None
        existing.cijena = cijena
        existing.save(update_fields=['kolicina', 'kolicina_pokupljeno', 'cijena'])
        item = existing
    else:
        item = OrderItem.objects.create(
            narudzba=order,
            artikal=product,
            varijacija=variation,
            naziv=product.naziv[:200],
            product_naziv=product.naziv[:200],
            varijacija_naziv=(variation.naziv[:100] if variation else ''),
            sifra=((variation.sifra if variation and variation.sifra else product.sifra) or '')[:200],
            cijena=cijena,
            bazna_cijena=bazna,
            kolicina=qty,
        )
    leftover = reserve_for_order(
        order,
        product,
        qty - shortfall,
        variation=variation,
        user=user,
        napomena=f'Izmjena #{order.broj}',
    )
    if leftover and not mp_ok:
        raise MagacinError(f'Nije rezervisana puna količina za {product.naziv}.')
    if shortfall > 0:
        _note_maloprodaja(order, product, variation)
    _clear_pick_state_for_item(order, item.pk)
    recalculate_order_totals(order)
    return item


@transaction.atomic
def set_order_item_qty(order, item, qty, *, mp_ok=False, user=None):
    _assert_order_editable(order)
    qty = max(1, _int(qty))
    if item.narudzba_id != order.pk:
        raise MagacinError('Stavka nije na ovoj narudžbi.')
    product = item.artikal
    if product is None:
        raise MagacinError('Artikal više ne postoji.')
    variation = item.varijacija
    current = int(item.kolicina or 0)
    if qty == current and not mp_ok:
        return item
    delta = qty - current
    available = stock_totals(product, variation)['dostupno']
    if delta > 0:
        shortfall = max(0, delta - available)
        if shortfall > 0 and not mp_ok:
            raise MagacinError(
                f'„{product.naziv}” nema dovoljno zalihe u magacinu ({available}). '
                'Provjeri maloprodaju pa dodaj, ili makni stavku.'
            )
        leftover = reserve_for_order(
            order,
            product,
            delta - shortfall,
            variation=variation,
            user=user,
            napomena=f'Izmjena #{order.broj}',
        )
        if leftover and not mp_ok:
            raise MagacinError(f'Nije rezervisana puna količina za {product.naziv}.')
        if shortfall > 0:
            _note_maloprodaja(order, product, variation)
    elif delta < 0:
        release_holds_for_product(order, product, variation, -delta, user=user)
    item.kolicina = qty
    item.kolicina_pokupljeno = None
    item.save(update_fields=['kolicina', 'kolicina_pokupljeno'])
    _clear_pick_state_for_item(order, item.pk)
    recalculate_order_totals(order)
    return item


@transaction.atomic
def remove_item_from_order(order, item, *, user=None):
    _assert_order_editable(order)
    if item.narudzba_id != order.pk:
        raise MagacinError('Stavka nije na ovoj narudžbi.')
    if OrderItem.objects.filter(narudzba_id=order.pk).count() <= 1:
        raise MagacinError('Narudžba mora imati barem jedan artikal.')
    product = item.artikal
    variation = item.varijacija
    if product is not None:
        release_holds_for_product(order, product, variation, user=user)
    item_id = item.pk
    item.delete()
    _clear_pick_state_for_item(order, item_id)
    recalculate_order_totals(order)


def _safe_release_pick_hold(hold, take, *, user=None, napomena=''):
    """Skini rezervaciju; ne ruši picking ako je zaliha već 0."""
    take = max(0, _int(take))
    if take <= 0:
        return
    stock = get_or_create_stock(
        product=hold.product, variation=hold.variation, location=hold.location,
    )
    on_hand = max(0, int(stock.kolicina or 0))
    reserved = max(0, int(stock.rezervisano or 0))
    new_reserved = min(on_hand, max(0, reserved - take))
    if new_reserved == reserved:
        return
    try:
        apply_movement(
            product=hold.product,
            variation=hold.variation,
            location=hold.location,
            tip=WarehouseMovement.Tip.REZERVACIJA,
            kolicina=1,
            rezervisano=new_reserved,
            napomena=napomena,
            user=user,
        )
    except MagacinError:
        stock = get_or_create_stock(
            product=hold.product, variation=hold.variation, location=hold.location,
        )
        stock.rezervisano = min(max(0, int(stock.kolicina or 0)), new_reserved)
        stock.save(update_fields=['rezervisano', 'azurirano'])


@transaction.atomic
def drop_missing_pick_line(order, item, *, loc, qty, user=None):
    """Picking 0 / nema: skini količinu s narudžbe i s te lokacije.

    Zaliha/rezervacija se koriguje best-effort — stavka se uvijek skida
    da se picking može završiti i kad artikla fizički nema.
    """
    _assert_order_editable(order)
    if item.narudzba_id != order.pk:
        raise MagacinError('Stavka nije na ovoj narudžbi.')
    qty = max(0, _int(qty))
    if qty <= 0:
        qty = int(item.kolicina or 0)
    loc = (loc or '').strip()
    product = item.artikal
    variation = item.varijacija
    skip_stock = (
        loc in {'MP', 'Provjeri u MP', 'Rezervni dio'}
        or getattr(item, 'rezervni_dio', False)
        or product is None
    )
    if not skip_stock:
        location = WarehouseLocation.objects.filter(sifra=loc).first()
        if location is not None and not is_ignored_stock_location(location):
            remaining = qty
            holds = list(
                order.magacin_holds.filter(
                    product=product,
                    location=location,
                    status=OrderStockHold.Status.REZERVISANO,
                    **_hold_variation_filter(variation),
                ).order_by('-kolicina', '-pk')
            )
            note = f'Nema na pickingu #{order.broj}'
            for hold in holds:
                if remaining <= 0:
                    break
                take = min(int(hold.kolicina or 0), remaining)
                if take <= 0:
                    continue
                _safe_release_pick_hold(hold, take, user=user, napomena=note)
                if take >= int(hold.kolicina or 0):
                    hold.status = OrderStockHold.Status.OTKAZANO
                    hold.save(update_fields=['status'])
                else:
                    hold.kolicina -= take
                    hold.save(update_fields=['kolicina'])
                remaining -= take
            stock = get_or_create_stock(
                product=product, variation=variation, location=location,
            )
            new_on_hand = max(0, int(stock.kolicina or 0) - qty)
            if new_on_hand != int(stock.kolicina or 0):
                try:
                    apply_movement(
                        product=product,
                        variation=variation,
                        location=location,
                        tip=WarehouseMovement.Tip.KOREKCIJA,
                        kolicina=new_on_hand,
                        napomena=note,
                        user=user,
                    )
                except MagacinError:
                    stock.kolicina = new_on_hand
                    if int(stock.rezervisano or 0) > new_on_hand:
                        stock.rezervisano = new_on_hand
                    stock.save(update_fields=['kolicina', 'rezervisano', 'azurirano'])
    new_qty = int(item.kolicina or 0) - qty
    if new_qty <= 0:
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
            _clear_pick_state_for_item(order, item_id)
            recalculate_order_totals(order)
        return {'cancelled': False, 'removed': True}
    item.kolicina = new_qty
    item.kolicina_pokupljeno = None
    item.save(update_fields=['kolicina', 'kolicina_pokupljeno'])
    _clear_pick_state_for_item(order, item.pk)
    recalculate_order_totals(order)
    return {'cancelled': False, 'removed': False}


@transaction.atomic
def mark_order_packed(order):
    if order.lager_status != Order.LagerStatus.VALIDIRANO:
        raise MagacinError('Prvo validatuj narudžbu.')
    if order.zapakovana and order.status == Order.Status.ZAVRSENA:
        return order
    order.zapakovana = True
    order.zapakovana_at = timezone.now()
    update_fields = ['zapakovana', 'zapakovana_at']
    if order.status != Order.Status.OTKAZANA:
        order.status = Order.Status.ZAVRSENA
        update_fields.append('status')
    order.save(update_fields=update_fields)
    try:
        from .views_magacin import invalidate_magacin_nav_counts

        invalidate_magacin_nav_counts()
    except Exception:
        pass
    return order


@transaction.atomic
def create_prenos_mp_pick(*, product, variation=None, location, qty, user=None):
    """Napravi picking za prenos u MP — Validate skida zalihu s odabrane lokacije."""
    from .models import Order, OrderItem

    qty = _parse_move_qty(qty)
    if qty <= 0:
        raise MagacinError('Unesi količinu za prenos u MP.')
    rows, _ = location_rows(product, variation)
    row = next((item for item in rows if item['location'].pk == location.pk), None)
    if row is None or row['dostupno'] < qty:
        raise MagacinError('Nema dovoljno dostupne količine na toj lokaciji.')
    cijena = product.cijena or Decimal('0.00')
    order = Order.objects.create(
        ime_prezime='Prenos u MP',
        email='prenos@carpologijabh.local',
        telefon='-',
        adresa=(location.label or location.sifra or '')[:300],
        grad='Magacin',
        napomena=f'Prenos u MP sa {location.label}',
        medjuzbir=cijena * qty,
        dostava=Decimal('0.00'),
        ukupno=cijena * qty,
        status=Order.Status.NOVA,
        izvor=Order.Izvor.MAGACIN,
        pick_state={'kind': 'prenos_mp', 'from_location_id': location.pk},
    )
    OrderItem.objects.create(
        narudzba=order,
        artikal=product,
        varijacija=variation,
        naziv=product.naziv[:200],
        product_naziv=product.naziv[:200],
        varijacija_naziv=(variation.naziv[:100] if variation else ''),
        sifra=((variation.sifra if variation and variation.sifra else product.sifra) or '')[:200],
        cijena=cijena,
        bazna_cijena=cijena,
        kolicina=qty,
    )
    leftover = reserve_for_order(
        order,
        product,
        qty,
        variation=variation,
        user=user,
        napomena=f'Prenos u MP #{order.broj}',
        location=location,
    )
    if leftover:
        raise MagacinError('Nije rezervisana puna količina za prenos u MP.')
    order.lager_status = Order.LagerStatus.REZERVISANO
    order.save(update_fields=['lager_status'])
    try:
        from .views_magacin import invalidate_magacin_nav_counts

        invalidate_magacin_nav_counts()
    except Exception:
        pass
    return order


def _parse_move_qty(raw):
    if isinstance(raw, int):
        return max(0, raw)
    text = str(raw or '').strip().replace(',', '.')
    if not text:
        return 0
    try:
        return max(0, int(Decimal(text)))
    except (ArithmeticError, ValueError, TypeError):
        raise MagacinError('Količina nije validan broj.')


@transaction.atomic
def validate_order_stock(order, *, user=None):
    """Skini rezervisane količine s lokacija te narudžbe."""
    from collections import defaultdict

    if order.lager_status == Order.LagerStatus.VALIDIRANO:
        return
    if order.lager_status == Order.LagerStatus.OTKAZANO:
        raise MagacinError('Otkazana narudžba se ne može validirati.')
    reserved = defaultdict(int)
    holds = list(order.magacin_holds.filter(status=OrderStockHold.Status.REZERVISANO))
    for hold in holds:
        reserved[(hold.product_id, hold.variation_id)] += int(hold.kolicina or 0)
    picked = defaultdict(int)
    for item in order.stavke.all():
        if item.kolicina_pokupljeno is None:
            qty = int(item.kolicina or 0)
        else:
            qty = int(item.kolicina_pokupljeno or 0)
        picked[(item.artikal_id, item.varijacija_id)] += qty
    for key, res in reserved.items():
        extra = res - picked.get(key, 0)
        if extra <= 0 or not key[0]:
            continue
        product = Product.objects.filter(pk=key[0]).first()
        if product is None:
            continue
        variation = ProductVariation.objects.filter(pk=key[1]).first() if key[1] else None
        release_holds_for_product(order, product, variation, qty=extra, user=user)
    holds = list(order.magacin_holds.filter(status=OrderStockHold.Status.REZERVISANO))
    for hold in holds:
        try:
            apply_movement(
                product=hold.product,
                variation=hold.variation,
                location=hold.location,
                tip=WarehouseMovement.Tip.PRODAJA,
                kolicina=hold.kolicina,
                napomena=f'Validacija #{order.broj}',
                user=user,
                from_reservation=True,
            )
            hold.status = OrderStockHold.Status.VALIDIRANO
            hold.save(update_fields=['status'])
        except MagacinError:
            try:
                release_holds_for_product(
                    order, hold.product, hold.variation, qty=hold.kolicina, user=user,
                )
            except MagacinError:
                hold.status = OrderStockHold.Status.OTKAZANO
                hold.save(update_fields=['status'])
    order.lager_status = Order.LagerStatus.VALIDIRANO
    update_fields = ['lager_status']
    if not is_vp_order(order):
        order.zapakovana = True
        order.zapakovana_at = timezone.now()
        update_fields.extend(['zapakovana', 'zapakovana_at'])
        if order.status != Order.Status.OTKAZANA:
            order.status = Order.Status.ZAVRSENA
            update_fields.append('status')
    order.save(update_fields=update_fields)
    try:
        from .views_magacin import invalidate_magacin_nav_counts

        invalidate_magacin_nav_counts()
    except Exception:
        pass


@transaction.atomic
def cancel_order_stock(order, *, user=None):
    """Vrati rezervaciju i otkaži narudžbu."""
    if order.lager_status == Order.LagerStatus.OTKAZANO:
        return
    if order.lager_status == Order.LagerStatus.VALIDIRANO:
        raise MagacinError('Validirana narudžba se ne može otkazati iz magacina.')
    holds = list(order.magacin_holds.filter(status=OrderStockHold.Status.REZERVISANO))
    for hold in holds:
        stock = get_or_create_stock(
            product=hold.product, variation=hold.variation, location=hold.location,
        )
        new_reserved = max(0, stock.rezervisano - hold.kolicina)
        apply_movement(
            product=hold.product,
            variation=hold.variation,
            location=hold.location,
            tip=WarehouseMovement.Tip.REZERVACIJA,
            kolicina=1,
            rezervisano=new_reserved,
            napomena=f'Otkazivanje #{order.broj}',
            user=user,
        )
        hold.status = OrderStockHold.Status.OTKAZANO
        hold.save(update_fields=['status'])
    order.lager_status = Order.LagerStatus.OTKAZANO
    order.status = Order.Status.OTKAZANA
    order.save(update_fields=['lager_status', 'status'])
    try:
        from .views_magacin import invalidate_magacin_nav_counts

        invalidate_magacin_nav_counts()
    except Exception:
        pass


def _invalidate_last_sync_cache():
    from django.core.cache import cache

    cache.delete('mg_last_sync_v1')


def last_sync():
    import sys
    from django.core.cache import cache

    use_cache = 'test' not in sys.argv
    if use_cache:
        cached = cache.get('mg_last_sync_v1')
        if cached is not None:
            return cached or None
    log = WarehouseSyncLog.objects.defer('job_data').order_by('-started_at').first()
    if use_cache:
        cache.set('mg_last_sync_v1', log or False, 30)
    return log


def last_successful_sync():
    return (
        WarehouseSyncLog.objects.filter(status=WarehouseSyncLog.Status.USPJEH)
        .order_by('-finished_at', '-started_at')
        .first()
    )


def seed_default_locations():
    defaults = [
        ('A-10', 'Glavni magacin', 10),
        ('B-03', 'Maloprodaja Sarajevo', 20),
        ('C-02', 'Maloprodaja Banja Luka', 30),
        ('V-01', 'Vanjski lager', 40),
        ('MP-02', 'Maloprodaja Mostar', 50),
    ]
    created = 0
    for sifra, naziv, order in defaults:
        _, was_created = WarehouseLocation.objects.get_or_create(
            sifra=sifra,
            defaults={'naziv': naziv, 'redoslijed': order, 'aktivan': True},
        )
        if was_created:
            created += 1
    WarehouseSupplier.objects.get_or_create(
        naziv='Carpologija d.o.o.',
        defaults={'aktivan': True},
    )
    return created
