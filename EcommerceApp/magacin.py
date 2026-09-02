"""Lokalni magacin: zalihe po lokacijama, kretanja i Odoo sync."""

from __future__ import annotations

import base64
import csv
import json
import logging
import os
import re
import time
from collections import defaultdict
from datetime import date, timedelta
from io import BytesIO, StringIO

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


def obrisi_artikal_iz_baze(product):
    """Trajno obriši artikal iz baze, uključujući slike i magacin zalihe."""
    from django.db.models.deletion import ProtectedError

    if product is None or not getattr(product, 'pk', None):
        raise MagacinError('Artikal nije pronađen.')
    try:
        with transaction.atomic():
            if getattr(product, 'slika', None):
                try:
                    product.slika.delete(save=False)
                except Exception:
                    pass
            for img in list(product.dodatne_slike.all()):
                try:
                    if img.slika:
                        img.slika.delete(save=False)
                except Exception:
                    pass
            product.delete()
    except ProtectedError as exc:
        raise MagacinError(
            'Artikal se ne može obrisati jer postoje povezani podaci koji to sprečavaju.'
        ) from exc
    except IntegrityError as exc:
        raise MagacinError(
            'Artikal se ne može obrisati zbog povezanih zapisa u bazi.'
        ) from exc


logger = logging.getLogger(__name__)


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


def _product_has_variations(product):
    if product is None:
        return False
    return ProductVariation.objects.filter(artikal_id=product.pk).exists()


def _merge_stock_rows(rows):
    """Spoji zalihe iste lokacije na parent (variation_key=0)."""
    if not rows:
        return None
    if len(rows) == 1:
        keeper = rows[0]
        if keeper.variation_id is not None or int(keeper.variation_key or 0) != 0:
            keeper.variation = None
            keeper.variation_key = 0
            keeper.save(update_fields=['variation', 'variation_key', 'azurirano'])
        return keeper
    keeper = next((row for row in rows if int(row.variation_key or 0) == 0), rows[0])
    qty = sum(int(row.kolicina or 0) for row in rows)
    reserved = sum(max(0, int(row.rezervisano or 0)) for row in rows)
    for row in rows:
        if row.pk != keeper.pk:
            row.delete()
    keeper.variation = None
    keeper.variation_key = 0
    keeper.kolicina = qty
    keeper.rezervisano = min(reserved, qty)
    keeper.save(update_fields=['variation', 'variation_key', 'kolicina', 'rezervisano', 'azurirano'])
    return keeper


@transaction.atomic
def coalesce_unassigned_stock(product):
    """Bez varijacija: spoji leftover variation_key redove na parent po lokaciji."""
    if product is None or _product_has_variations(product):
        return
    grouped = defaultdict(list)
    for row in WarehouseStock.objects.select_for_update().filter(product=product):
        grouped[row.location_id].append(row)
    for group in grouped.values():
        _merge_stock_rows(group)


def fold_stock_after_variation_delete(sender, instance, **kwargs):
    product_id = getattr(instance, 'artikal_id', None)
    if not product_id:
        return
    product = Product.objects.filter(pk=product_id).first()
    if product is None:
        return
    if not WarehouseStock.objects.filter(product_id=product_id).exists():
        return
    coalesce_unassigned_stock(product)
    refresh_catalog_qty(product)


def get_or_create_stock(*, product, variation, location):
    if variation is None:
        qs = WarehouseStock.objects.select_for_update().filter(product=product, location=location)
        if _product_has_variations(product):
            qs = qs.filter(Q(variation__isnull=True) | Q(variation_key=0))
        rows = list(qs)
        if rows:
            if (
                len(rows) == 1
                and rows[0].variation_id is None
                and int(rows[0].variation_key or 0) == 0
            ):
                return rows[0]
            return _merge_stock_rows(rows)
        return WarehouseStock.objects.create(
            product=product,
            variation=None,
            variation_key=0,
            location=location,
            kolicina=0,
            rezervisano=0,
        )
    variation_key = int(getattr(variation, 'pk', variation) or 0)
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


def ignored_location_q(prefix=''):
    return _location_keyword_q(_IGNORED_LOCATION_KEYWORDS, prefix=prefix)


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


def maloprodaja_locations():
    return WarehouseLocation.objects.filter(aktivan=True).filter(
        _location_keyword_q(('maloprodaja',))
    ).exclude(ignored_location_q())


def default_maloprodaja_location(product=None):
    qs = maloprodaja_locations().order_by('redoslijed', 'id')
    if product is not None:
        loc_id = (
            WarehouseStock.objects.filter(
                product=product,
                location__in=qs,
                kolicina__gt=0,
            )
            .order_by('location__redoslijed', 'location_id')
            .values_list('location_id', flat=True)
            .first()
        )
        if loc_id:
            return qs.filter(pk=loc_id).first()
    return qs.first()


def usable_locations():
    return WarehouseLocation.objects.filter(aktivan=True).exclude(ignored_location_q())


def countable_stock_qs(qs=None):
    qs = WarehouseStock.objects.all() if qs is None else qs
    return qs.exclude(uncountable_location_q('location'))


def recorded_stock_qs(qs=None):
    """Zaliha na svim lokacijama koje se evidentiraju (magacin + maloprodaja, ne Prenos)."""
    qs = WarehouseStock.objects.all() if qs is None else qs
    return qs.exclude(ignored_location_q('location'))


def _agg_stock(qs):
    agg = qs.aggregate(na_stanju=Sum('kolicina'), rezervisano=Sum('rezervisano'))
    na_stanju = _int(agg.get('na_stanju'))
    rezervisano = max(0, _int(agg.get('rezervisano')))
    return {
        'na_stanju': na_stanju,
        'rezervisano': rezervisano,
        'dostupno': max(0, na_stanju - rezervisano),
    }


def _stock_scope(product, variation=None):
    """Zaliha za artikal/varijaciju. Bez varijacija — sve lokacije artikla. Inače fallback na parent."""
    base = countable_stock_qs(WarehouseStock.objects.filter(product=product))
    if not _product_has_variations(product) or variation is None:
        return base, None
    own = base.filter(variation=variation)
    if own.filter(kolicina__gt=0).exists():
        return own, variation
    unassigned = base.filter(Q(variation__isnull=True) | Q(variation_key=0))
    if unassigned.filter(kolicina__gt=0).exists():
        return unassigned, None
    return own, variation


def stock_totals(product, variation=None):
    qs, _ = _stock_scope(product, variation)
    return _agg_stock(qs)


def location_rows(product, variation=None, *, locations=None):
    if locations is None:
        locations = list(usable_locations())
    locations = [loc for loc in locations if not is_uncountable_stock_location(loc)]
    qs, _ = _stock_scope(product, variation)
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


def _mp_stock_qty(product, variation=None):
    locations = list(maloprodaja_locations())
    if not locations or product is None:
        return 0
    qs = WarehouseStock.objects.filter(product=product, location__in=locations)
    if variation is not None:
        own = qs.filter(variation=variation)
        if own.filter(kolicina__gt=0).exists():
            qs = own
        else:
            qs = qs.filter(Q(variation__isnull=True) | Q(variation_key=0))
    elif not _product_has_variations(product):
        qs = qs.filter(Q(variation__isnull=True) | Q(variation_key=0))
    return max(0, _int(qs.aggregate(s=Sum('kolicina'))['s'] or 0))


def maloprodaja_location_rows(product, variation=None):
    """Zaliha na maloprodaji — prikaz ispod magacinskih lokacija, ne ulazi u magacinski zbir."""
    locations = list(maloprodaja_locations().order_by('redoslijed', 'id'))
    if not locations:
        return []
    qs = WarehouseStock.objects.filter(product=product, location__in=locations)
    if variation is not None:
        own = qs.filter(variation=variation)
        if own.filter(kolicina__gt=0).exists():
            qs = own
        else:
            qs = qs.filter(Q(variation__isnull=True) | Q(variation_key=0))
    elif not _product_has_variations(product):
        qs = qs.filter(Q(variation__isnull=True) | Q(variation_key=0))
    by_loc = defaultdict(lambda: {'kolicina': 0, 'rezervisano': 0})
    for row in qs:
        bucket = by_loc[row.location_id]
        bucket['kolicina'] += _int(row.kolicina)
        bucket['rezervisano'] += max(0, _int(row.rezervisano))
    rows = []
    for loc in locations:
        data = by_loc.get(loc.id, {'kolicina': 0, 'rezervisano': 0})
        qty = _int(data['kolicina'])
        reserved = max(0, _int(data['rezervisano']))
        if qty <= 0:
            continue
        rows.append({
            'location': loc,
            'kolicina': qty,
            'rezervisano': reserved,
            'dostupno': max(0, qty - reserved),
            'is_mp': True,
        })
    return rows


def display_stock_totals(product, variation=None):
    """Ukupno na stanju i dostupno, uključujući maloprodaju kao ostale lokacije."""
    totals = dict(stock_totals(product, variation))
    for row in maloprodaja_location_rows(product, variation):
        totals['na_stanju'] += int(row.get('kolicina') or 0)
        totals['rezervisano'] += int(row.get('rezervisano') or 0)
    totals['dostupno'] = max(0, totals['na_stanju'] - totals['rezervisano'])
    return totals


def missing_maloprodaja_rows(*, query=''):
    """Artikli na magacinskim lokacijama koji nemaju ništa u maloprodaji."""
    mp_ids = list(maloprodaja_locations().values_list('pk', flat=True))
    mp_keys = set()
    if mp_ids:
        mp_keys = set(
            WarehouseStock.objects.filter(location_id__in=mp_ids, kolicina__gt=0)
            .values_list('product_id', 'variation_key')
        )
    pending_keys = set(
        OrderItem.objects.filter(narudzba__ime_prezime='Prenos u MP')
        .exclude(narudzba__status=Order.Status.OTKAZANA)
        .exclude(narudzba__lager_status=Order.LagerStatus.VALIDIRANO)
        .values_list('artikal_id', 'varijacija_id')
    )
    pending_keys = {(pid, vid or 0) for pid, vid in pending_keys if pid}
    stocks = (
        countable_stock_qs()
        .filter(kolicina__gt=0)
        .select_related('product', 'variation', 'location')
        .order_by('product__naziv', 'location__sifra')
    )
    q = (query or '').strip()
    if q:
        stocks = stocks.filter(
            Q(product__naziv__icontains=q)
            | Q(product__sifra__icontains=q)
            | Q(product__barkod__icontains=q)
            | Q(variation__sifra__icontains=q)
            | Q(variation__naziv__icontains=q)
        )
    grouped = {}
    for stock in stocks:
        dostupno = max(0, int(stock.kolicina or 0) - max(0, int(stock.rezervisano or 0)))
        if dostupno <= 0:
            continue
        key = (stock.product_id, int(stock.variation_key or 0))
        if key in mp_keys:
            continue
        bucket = grouped.get(key)
        if bucket is None:
            bucket = {
                'product': stock.product,
                'variation': stock.variation,
                'locations': [],
                'max_qty': 0,
                'pending_prenos': key in pending_keys,
            }
            grouped[key] = bucket
        bucket['locations'].append({
            'location': stock.location,
            'kolicina': int(stock.kolicina or 0),
            'rezervisano': max(0, int(stock.rezervisano or 0)),
            'dostupno': dostupno,
        })
        bucket['max_qty'] += dostupno
    rows = [row for row in grouped.values() if row['max_qty'] > 0]
    rows.sort(key=lambda row: (
        (row['product'].naziv or '').casefold(),
        (row['variation'].naziv if row['variation'] else ''),
    ))
    return rows


def order_location_rows(product, variation=None):
    """Lokacije za narudžbu: magacin pa maloprodaja zadnja. Prenos se i dalje preskače."""
    rows, totals = location_rows(product, variation)
    extra_qty = 0
    extra_res = 0
    for row in maloprodaja_location_rows(product, variation):
        rows.append(row)
        extra_qty += int(row.get('kolicina') or 0)
        extra_res += int(row.get('rezervisano') or 0)
    totals = {
        'na_stanju': int(totals.get('na_stanju') or 0) + extra_qty,
        'rezervisano': int(totals.get('rezervisano') or 0) + extra_res,
    }
    totals['dostupno'] = max(0, totals['na_stanju'] - totals['rezervisano'])
    return rows, totals


NIJE_POPISAN_LABEL = 'Nije popisan'
VIRTUAL_PICK_LOCS = frozenset({'MP', 'Provjeri u MP', 'Rezervni dio', NIJE_POPISAN_LABEL})


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


def _location_stock_qty_qs():
    """Zaliha po artiklu na svim evidentiranim lokacijama osim Prenos u MP."""
    return recorded_stock_qs().values('product_id').annotate(qty=Sum('kolicina'))


def maybe_unhide_on_restock(product, *, now_in_stock, new_qty):
    """
    Ako je staff sakrio artikal sa sajta, vrati ga kad opet dođe na stanje
    ili kad se količina poveća.
    """
    if not getattr(product, 'sakriven_do_stanja', False):
        return False
    if not now_in_stock:
        return False
    old_in_stock = bool(product.na_stanju)
    old_qty = _int(product.stanje)
    qty = _int(new_qty)
    if old_in_stock and qty <= old_qty:
        return False
    product.sakriven_do_stanja = False
    return True


def refresh_catalog_qty(product):
    """Na sajtu dok ima količinu na bilo kojoj lokaciji (magacin + MP). Bez zalihe = skini sa sajta."""
    variations = list(ProductVariation.objects.filter(artikal_id=product.pk))
    if not variations:
        coalesce_unassigned_stock(product)

    for variation in variations:
        mag_qty = _agg_stock(countable_stock_qs(WarehouseStock.objects.filter(
            product=product, variation=variation,
        )))['dostupno']
        mp_qty = sum(int(row.get('dostupno') or 0) for row in maloprodaja_location_rows(product, variation))
        var_qty = max(0, mag_qty) + max(0, mp_qty)
        var_in_stock = var_qty > 0
        if variation.stanje != var_qty or variation.na_stanju != var_in_stock:
            variation.stanje = var_qty
            variation.na_stanju = var_in_stock
            variation.save(update_fields=['stanje', 'na_stanju'])

    catalog_qty = max(0, _int(display_stock_totals(product)['dostupno']))
    in_stock = catalog_qty > 0
    clear_mp_without_location(product)

    update_fields = []
    if maybe_unhide_on_restock(product, now_in_stock=in_stock, new_qty=catalog_qty):
        update_fields.append('sakriven_do_stanja')
    if product.stanje != catalog_qty:
        product.stanje = catalog_qty
        update_fields.append('stanje')
    if product.na_stanju != in_stock:
        product.na_stanju = in_stock
        update_fields.append('na_stanju')
    if update_fields:
        product.save(update_fields=update_fields)
    return catalog_qty


def sync_site_visibility_from_locations(*, product_ids=None):
    """Uskladi sajt: nema lokacije / zalihe = skini, ima zalihu na bilo kojoj lokaciji = vrati."""
    qty_rows = _location_stock_qty_qs()
    if product_ids is not None:
        id_set = {int(pk) for pk in product_ids}
        qty_rows = qty_rows.filter(product_id__in=id_set)
    else:
        id_set = None
    qty_map = {
        int(row['product_id']): max(0, _int(row['qty']))
        for row in qty_rows
    }
    qs = Product.objects.only('id', 'na_stanju', 'stanje')
    if id_set is not None:
        qs = qs.filter(pk__in=id_set)

    off_ids = []
    refresh_ids = []
    for product in qs.iterator():
        qty = qty_map.get(product.pk, 0)
        should_be_on = qty > 0
        if product.na_stanju != should_be_on or int(product.stanje or 0) != qty:
            if should_be_on:
                refresh_ids.append(product.pk)
            else:
                off_ids.append(product.pk)

    if off_ids:
        for chunk in (off_ids[i:i + 500] for i in range(0, len(off_ids), 500)):
            Product.objects.filter(pk__in=chunk).update(na_stanju=False, stanje=0)
            ProductVariation.objects.filter(artikal_id__in=chunk).update(
                na_stanju=False, stanje=0,
            )
            ProductWarehouseMeta.objects.filter(
                product_id__in=chunk, mp_bez_lokacije=True,
            ).update(mp_bez_lokacije=False)

    for pk in refresh_ids:
        refresh_catalog_qty(Product.objects.get(pk=pk))

    return {
        'off': len(off_ids),
        'on': len(refresh_ids),
        'checked': qs.count() if id_set is not None else Product.objects.count(),
    }


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
        if signed == 0:
            return None
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
        if is_ignored_stock_location(to_location):
            raise MagacinError('Lokacija Prenos u MP se ne evidentira.')
        if from_reservation:
            if stock.rezervisano < qty:
                raise MagacinError(
                    f'Nedovoljno rezervacije na {location.label} (ima {stock.rezervisano}, treba {qty}).'
                )
        if stock.kolicina < qty:
            raise MagacinError(
                f'Nedovoljno zalihe na {location.label} (ima {stock.kolicina}, treba {qty}).'
            )
        dest = get_or_create_stock(product=product, variation=variation, location=to_location)
        stock.kolicina -= qty
        if from_reservation:
            stock.rezervisano = max(0, stock.rezervisano - qty)
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
                    na_stanju=False,
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
    """Uvozne količine koje su ostale samo na Uvoz prebaci na maloprodaju."""
    leftover, location = leftover_uvoz_stocks()
    if not leftover:
        return {'count': 0, 'qty': 0, 'product_ids': []}
    dest = default_maloprodaja_location()
    if dest is None:
        raise MagacinError('Nema maloprodajne lokacije za prenos uvoza.')
    moved_ids = []
    qty_total = 0
    seen = set()
    user_obj = user if getattr(user, 'is_authenticated', False) else None
    for stock in leftover:
        qty = _int(stock.kolicina)
        if qty <= 0:
            continue
        apply_movement(
            product=stock.product,
            variation=stock.variation,
            location=location,
            to_location=dest,
            tip=WarehouseMovement.Tip.TRANSFER,
            kolicina=qty,
            napomena='Uvoz lokacija u MP',
            user=user_obj,
        )
        qty_total += qty
        if stock.product_id not in seen:
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
    """Artikal ima zalihu na barem jednoj lokaciji (magacin ili maloprodaja; ne Prenos)."""
    from django.db.models import Exists, OuterRef

    return Exists(
        recorded_stock_qs(
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
SIFRA_SYNC_BATCH = 80
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


_ODOO_NAME_REF_PREFIX = re.compile(r'^\[[^\]]+\]\s*')


def _bare_product_name(value):
    """Naziv za match: trim, casefold, bez [REF] prefiksa iz Odoo display_name."""
    text = _norm_ident('' if value in (None, False) else str(value))
    if text.startswith('['):
        text = _ODOO_NAME_REF_PREFIX.sub('', text).strip()
    return _norm_name_key(text)


def _odoo_reference_code(*records):
    """Odoo Internal Reference (polje Reference / default_code)."""
    for source in records:
        if not source:
            continue
        for key in ('default_code', 'code', 'reference'):
            raw = source.get(key)
            if raw in (None, False, ''):
                continue
            text = _norm_ident(str(raw))
            if text:
                return text
    return ''


def _products_by_bare_name():
    grouped = {}
    qs = Product.objects.only('id', 'naziv', 'sifra', 'odoo_template_id', 'stanje', 'barkod')
    for row in qs:
        key = _bare_product_name(row.naziv)
        if not key:
            continue
        grouped.setdefault(key, []).append(row)
    return grouped


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
    product = Product(
        naziv=naziv,
        sifra=sifra,
        barkod=barkod,
        cijena=cijena,
        odoo_template_id=odoo_id,
        magacin_sync_at=now,
        aktivan=True,
        na_stanju=False,
        stanje=0,
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


def sync_sifra_chunk(client, template_ids):
    """Nađi isti naziv u Magacinu i upiši Odoo referencu. Nikad ne kreira artikal."""
    stats = {'azurirano': 0, 'preskoceno': 0}
    ids = [int(tid) for tid in (template_ids or []) if tid]
    if not ids:
        return stats
    from .odoo_import import _odoo_template_name, _sifra_zauzeta

    templates = client.get_templates_by_ids(ids) or []
    need_variant_ids = []
    for template in templates:
        if _odoo_reference_code(template):
            continue
        for vid in template.get('product_variant_ids') or []:
            if vid:
                need_variant_ids.append(int(vid))
    variants_by_id = {}
    if need_variant_ids and hasattr(client, 'get_product_variants'):
        for row in client.get_product_variants(need_variant_ids, with_images=False) or []:
            vid = row.get('id')
            if vid:
                variants_by_id[int(vid)] = row

    by_name = _products_by_bare_name()
    for template in templates:
        name_keys = []
        for raw in (_odoo_template_name(template), template.get('display_name')):
            key = _bare_product_name(raw)
            if key and key not in name_keys:
                name_keys.append(key)
        variant_rows = [
            variants_by_id[int(vid)]
            for vid in (template.get('product_variant_ids') or [])
            if vid and int(vid) in variants_by_id
        ]
        sifra = _odoo_reference_code(template, *variant_rows)
        if not name_keys or not sifra:
            stats['preskoceno'] += 1
            continue
        matches = []
        seen = set()
        for key in name_keys:
            for row in by_name.get(key) or []:
                if row.pk in seen:
                    continue
                seen.add(row.pk)
                matches.append(row)
        if not matches:
            stats['preskoceno'] += 1
            continue
        new_sifra = sifra[:SIFRA_MAX_LENGTH]
        keeper = max(matches, key=_keeper_rank)
        if _sifra_zauzeta(new_sifra, product_pk=keeper.pk):
            stats['preskoceno'] += 1
            continue
        changed = False
        if (keeper.sifra or '') != new_sifra:
            keeper.sifra = new_sifra
            keeper.save(update_fields=['sifra'])
            changed = True
        odoo_id = template.get('id')
        if odoo_id and not keeper.odoo_template_id:
            bound = _bind_sync_product(keeper, odoo_id)
            if bound.pk == keeper.pk:
                changed = True
        if changed:
            stats['azurirano'] += 1
        else:
            stats['preskoceno'] += 1
    return stats


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

        if phase == 'sifre':
            template_ids = job.get('template_ids') or []
            position = int(job.get('position') or 0)
            batch = template_ids[position:position + SIFRA_SYNC_BATCH]
            stats = sync_sifra_chunk(client, batch)
            job['position'] = position + len(batch)
            job['azurirano'] = int(job.get('azurirano') or 0) + int(stats.get('azurirano') or 0)
            job['preskoceno'] = int(job.get('preskoceno') or 0) + int(stats.get('preskoceno') or 0)
            _update_log_progress(
                log, started,
                f'Šifre: {job["position"]} / {len(template_ids)} '
                f'(ažurirano {job.get("azurirano") or 0})…',
                artikala=job.get('artikala') or 0,
            )
            if job['position'] >= len(template_ids):
                job['done'] = True
                job['phase'] = 'done'
                _finish_log(
                    log, started,
                    poruka=(
                        f'Šifre usklađene po nazivu: ažurirano {job.get("azurirano") or 0}, '
                        f'preskočeno {job.get("preskoceno") or 0}. Nema novih artikala.'
                    ),
                    artikala=job.get('azurirano') or 0,
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
                job['discovered_ids'] = []
                job['odoo_ukupno'] = len(all_odoo)
                if job.get('sifra_only'):
                    job['template_ids'] = sorted(all_odoo)
                    job['position'] = 0
                    job['phase'] = 'sifre'
                    if not job['template_ids']:
                        job['done'] = True
                        job['phase'] = 'done'
                        _finish_log(log, started, 'Nema Odoo artikala za sync šifri.')
                    else:
                        _update_log_progress(
                            log, started,
                            f'Šifre iz Odoo po nazivu: {len(job["template_ids"])} artikala…',
                            artikala=len(job['template_ids']),
                        )
                    return job
                local_ids = set(local_odoo_template_ids())
                missing = sorted(all_odoo - local_ids)
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
                sync_site_visibility_from_locations()
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


def start_sifra_sync(*, user=None):
    """Samo šifre: isti naziv u Magacinu ← Odoo referenca (default_code). Ne kreira artikle."""
    from .odoo_client import odoo_je_konfigurisan

    started = time.time()
    log = WarehouseSyncLog.objects.create(
        status=WarehouseSyncLog.Status.U_TOKU,
        izvor='Odoo šifre',
        korisnik=user if getattr(user, 'is_authenticated', False) else None,
    )
    if not odoo_je_konfigurisan():
        _fail_log(log, started, 'Odoo nije konfigurisan.')
        raise MagacinError('Odoo nije konfigurisan.')
    attach_site_odoo_products_to_magacin()
    _update_log_progress(log, started, 'Šifre iz Odoo po nazivu…')
    return {
        'log_id': log.pk,
        'started': started,
        'phase': 'discover',
        'template_ids': [],
        'position': 0,
        'stock_ids': [],
        'stock_extra_ids': [],
        'changed_ids': [],
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
        'incremental': False,
        'sifra_only': True,
        'single_product_id': None,
    }


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
    """Rasprodato: sve lokacije na 0 (i MP), artikal nestaje sa sajta."""
    stocks = list(
        WarehouseStock.objects.filter(product=product)
        .exclude(ignored_location_q('location'))
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
    """Aktiviraj artikal. Na sajtu je samo ako UKUPNO NA STANJU nije 0."""
    if not product.aktivan:
        product.aktivan = True
        product.save(update_fields=['aktivan'])
    refresh_catalog_qty(product)
    return product


def active_popis():
    from django.db import OperationalError, ProgrammingError

    try:
        return (
            MagacinPopis.objects.select_related('location')
            .filter(status=MagacinPopis.Status.U_TOKU)
            .order_by('-kreiran')
            .first()
        )
    except (ProgrammingError, OperationalError):
        return None


def _popis_location_qty(location, product, variation=None):
    if location is None or product is None:
        return 0
    qs = WarehouseStock.objects.filter(product=product, location=location)
    if variation is not None:
        own = qs.filter(variation=variation)
        qs = own if own.filter(kolicina__gt=0).exists() else qs.filter(
            Q(variation__isnull=True) | Q(variation_key=0)
        )
    else:
        qs = qs.filter(Q(variation__isnull=True) | Q(variation_key=0))
    return max(0, _int(qs.aggregate(s=Sum('kolicina'))['s'] or 0))


def location_stock_qty(location, product, variation=None):
    return _popis_location_qty(location, product, variation)


def set_location_counted_qty(*, location, product, variation=None, qty, user=None, napomena='Provjera popisa'):
    """Postavi apsolutnu količinu na lokaciji (korekcija). Vraća novo stanje."""
    qty = max(0, _int(qty))
    current = _popis_location_qty(location, product, variation)
    if current == qty:
        return current
    with transaction.atomic():
        apply_movement(
            product=product,
            variation=variation,
            location=location,
            tip=WarehouseMovement.Tip.KOREKCIJA,
            kolicina=qty,
            napomena=napomena or 'Provjera popisa',
            user=user,
        )
    return _popis_location_qty(location, product, variation)


def start_popis(*, user=None, location=None):
    if location is None:
        raise MagacinError('Odaberi lokaciju za popis.')
    if is_ignored_stock_location(location):
        raise MagacinError('Ova lokacija se ne popisuje.')
    if not getattr(location, 'aktivan', True):
        raise MagacinError('Lokacija nije aktivna.')
    existing = active_popis()
    if existing:
        if existing.location_id is None:
            existing.location = location
            existing.save(update_fields=['location'])
            return existing
        if existing.location_id == location.pk:
            return existing
        pause_popis(existing)
    return MagacinPopis.objects.create(
        kreirao=user if getattr(user, 'is_authenticated', False) else None,
        location=location,
    )


def finished_popisi():
    from django.db import OperationalError, ProgrammingError
    from django.db.models import Count, Q, Sum

    try:
        return (
            MagacinPopis.objects.filter(status=MagacinPopis.Status.ZAVRSEN)
            .select_related('location')
            .annotate(
                n_stavke=Count('stavke', distinct=True),
                n_kom=Sum('stavke__kolicina'),
                n_cekirano=Count('stavke', filter=Q(stavke__cekirano=True), distinct=True),
            )
            .order_by('-zavrsen_at', '-kreiran')[:50]
        )
    except (ProgrammingError, OperationalError):
        return MagacinPopis.objects.none()


def popis_spreman_za_stampu(popis):
    if not popis or popis.status != MagacinPopis.Status.ZAVRSEN:
        return False
    stavke = list(popis.stavke.all())
    return bool(stavke) and all(bool(row.cekirano) for row in stavke)


def paused_popisi():
    from django.db import OperationalError, ProgrammingError
    from django.db.models import Count, Sum

    try:
        return (
            MagacinPopis.objects.filter(status=MagacinPopis.Status.PAUZIRAN)
            .select_related('location')
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
    if not popis.location_id:
        raise MagacinError('Prvo izaberi lokaciju koju popisuješ.')
    if variation and variation.artikal_id != product.pk:
        raise MagacinError('Varijacija ne pripada artiklu.')
    naziv = product.naziv
    sifra = product.sifra or ''
    if variation:
        naziv = f'{product.naziv} {variation.naziv}'.strip()
        sifra = variation.sifra or product.sifra or ''
    expected = _popis_location_qty(popis.location, product, variation)
    existing = popis.stavke.filter(product=product, variation=variation).first()
    if existing:
        existing.kolicina += qty
        next_rb = (popis.stavke.order_by('-redoslijed').values_list('redoslijed', flat=True).first() or 0) + 1
        existing.redoslijed = next_rb
        existing.save(update_fields=['kolicina', 'redoslijed'])
        existing.already_on_list = True
        return existing
    next_rb = (popis.stavke.order_by('-redoslijed').values_list('redoslijed', flat=True).first() or 0) + 1
    stavka = MagacinPopisStavka.objects.create(
        popis=popis,
        product=product,
        variation=variation,
        naziv=naziv[:200],
        sifra=(sifra or '')[:SIFRA_MAX_LENGTH],
        ocekivano=expected,
        kolicina=qty,
        redoslijed=next_rb,
    )
    stavka.already_on_list = False
    return stavka


def finish_popis(popis, *, user=None):
    if popis.status == MagacinPopis.Status.ZAVRSEN:
        return popis
    location = popis.location
    if location is None:
        raise MagacinError('Popis nema lokaciju. Otvori novi popis i odaberi lokaciju.')
    if is_ignored_stock_location(location):
        raise MagacinError('Ova lokacija se ne popisuje.')
    for stavka in popis.stavke.select_related('product', 'variation'):
        if stavka.product_id is None:
            continue
        counted = max(0, _int(stavka.kolicina))
        current = _popis_location_qty(location, stavka.product, stavka.variation)
        if current == counted:
            continue
        apply_movement(
            product=stavka.product,
            variation=stavka.variation,
            location=location,
            tip=WarehouseMovement.Tip.KOREKCIJA,
            kolicina=counted,
            napomena=f'Popis #{popis.pk}',
            user=user,
        )
    popis.status = MagacinPopis.Status.ZAVRSEN
    popis.zavrsen_at = timezone.now()
    popis.save(update_fields=['status', 'zavrsen_at'])
    return popis


def mark_popis_odstampan(popis):
    if popis is None:
        return None
    if not popis.odstampan:
        popis.odstampan = True
        popis.save(update_fields=['odstampan'])
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


def set_popis_cekirano(popis, stavka_id, checked):
    stavka = popis.stavke.filter(pk=stavka_id).first()
    if not stavka:
        raise MagacinError('Stavka nije pronađena.')
    flag = bool(checked)
    if stavka.cekirano != flag:
        stavka.cekirano = flag
        stavka.save(update_fields=['cekirano'])
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


_MP_SKIP_TOKENS = {
    'šifra', 'sifra', 'sku', 'barkod', 'barcode', 'naziv', 'artikal',
    'kolicina', 'količina', 'kol', 'kol.', 'qty', 'kom', 'komada',
    'cijena', 'ukupno', 'iznos', 'mpc', 'pdv', 'r.b.', 'rb', '#', 'rb.', 'r.b',
}
_MP_SIFRA_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._/-]{1,39}$')


def _mp_norm_cell(value):
    return (value or '').replace('\xa0', ' ').strip()


def _mp_parse_qty_token(token):
    text = _mp_norm_cell(token).casefold().replace('×', 'x').replace('*', 'x')
    text = re.sub(r'^x\s*', '', text)
    text = re.sub(r'\s*(kom|kom\.|pcs|x)\s*$', '', text).strip().replace(' ', '').replace(',', '.')
    if re.fullmatch(r'\d+[.]000', text):
        text = text.split('.', 1)[0]
    if not re.fullmatch(r'\d+', text):
        return None
    qty = int(text)
    if 1 <= qty <= 9999:
        return qty
    return None


def _mp_is_sifra_token(token):
    text = _mp_norm_cell(token)
    if not _MP_SIFRA_RE.fullmatch(text):
        return False
    folded = text.casefold().rstrip('.')
    if folded in _MP_SKIP_TOKENS:
        return False
    if re.fullmatch(r'\d+[.,]\d{1,2}', text):
        return False
    if text.isdigit() and len(text) <= 3:
        return False
    return True


def _mp_is_sifra_from_column(token):
    """Šifra iz kolone SIFRA — uzmi kako piše, uključujući kratke brojeve (785)."""
    text = _mp_norm_cell(token)
    if not text or len(text) > 40:
        return False
    folded = text.casefold().rstrip('.')
    if folded in _MP_SKIP_TOKENS:
        return False
    if re.fullmatch(r'\d+[.,]\d+', text):
        return False
    return bool(_MP_SIFRA_RE.fullmatch(text))


_PROMET_GLUED_RE = re.compile(
    r'\.\d{3}(\d{8,14})\s+(\d+)[.,]000\s*\d{2}/\d{2}/\d{4}'
)


def _split_sifra_rbr_dok_candidates(blob):
    """ŠIFRA + RBR (1–2 znamenke) + DOK (4). RBR može biti 43, 59, …"""
    blob = str(blob or '')
    if len(blob) < 8 or not blob.isdigit():
        return []
    rest = blob[:-4]
    found = []
    seen = set()
    for slen in (4, 3, 5):
        if len(rest) <= slen:
            continue
        rbr = rest[slen:]
        if not rbr.isdigit() or not (1 <= len(rbr) <= 2):
            continue
        rbr_n = int(rbr)
        if not (1 <= rbr_n <= 99):
            continue
        sifra = rest[:slen]
        if sifra in seen:
            continue
        seen.add(sifra)
        found.append(sifra)
    return found


def _split_sifra_rbr_dok(blob):
    cands = _split_sifra_rbr_dok_candidates(blob)
    return cands[0] if cands else None


def _mp_existing_sifre(sifre):
    keys = []
    seen = set()
    for raw in sifre or []:
        key = str(raw or '').strip()
        folded = key.casefold()
        if not key or folded in seen:
            continue
        seen.add(folded)
        keys.append(key)
    if not keys:
        return set()
    q = Q()
    for key in keys:
        q |= Q(sifra__iexact=key)
    found = {val.casefold() for val in Product.objects.filter(q).values_list('sifra', flat=True)}
    found.update(
        val.casefold()
        for val in ProductVariation.objects.filter(q).values_list('sifra', flat=True)
    )
    return found


def _pick_promet_sifra(candidates, existing):
    if not candidates:
        return None
    for sifra in candidates:
        if sifra.casefold() in existing:
            return sifra
    return candidates[0]


def _parse_promet_glued(text):
    """
    'Promet po artiklima' PDF lijepi cijenu, šifru, rbr, dok i količinu 1.000+datum.
    Npr. 3.300422761541 3.00026/08/2026 → šifra 4227, količina 3.
    6.50078511548 1.00026/08/2026 → šifra 785, količina 1.
    10.5909745431534 1.00025/08/2026 → šifra 9745 (RBR 43), ne 97454.
    """
    pending = []
    all_cands = []
    for match in _PROMET_GLUED_RE.finditer(text or ''):
        cands = _split_sifra_rbr_dok_candidates(match.group(1))
        qty = int(match.group(2))
        if not cands or qty <= 0:
            continue
        pending.append((cands, qty))
        all_cands.extend(cands)
    if not pending:
        return []
    existing = _mp_existing_sifre(all_cands)
    rows = []
    for cands, qty in pending:
        sifra = _pick_promet_sifra(cands, existing)
        if sifra:
            rows.append((sifra, qty))
    return rows


def _merge_sifra_qty(pairs):
    merged = {}
    order = []
    for sifra, qty in pairs:
        sifra = str(sifra or '').strip()
        qty = int(qty or 0)
        if not sifra or qty <= 0:
            continue
        key = sifra.casefold()
        if key not in merged:
            merged[key] = {'sifra': sifra, 'qty': 0}
            order.append(key)
        merged[key]['qty'] += qty
    return [merged[key] for key in order if merged[key]['qty'] > 0]


def _mp_split_line(line):
    text = _mp_norm_cell(line)
    if not text:
        return []
    if '\t' in text:
        return [_mp_norm_cell(part) for part in text.split('\t') if _mp_norm_cell(part)]
    if text.count(';') >= 1:
        return [_mp_norm_cell(part) for part in text.split(';') if _mp_norm_cell(part)]
    if text.count(',') >= 2:
        try:
            return [
                _mp_norm_cell(part)
                for part in next(csv.reader(StringIO(text)))
                if _mp_norm_cell(part)
            ]
        except Exception:
            pass
    return [_mp_norm_cell(part) for part in re.split(r'\s+', text) if _mp_norm_cell(part)]


def _mp_header_indexes(cells):
    sifra_i = None
    qty_i = None
    for index, cell in enumerate(cells):
        folded = cell.casefold().replace(':', '').strip()
        if sifra_i is None and (
            folded in {'šifra', 'sifra', 'sku', 'sifra artikla', 'šifra artikla', 'barkod'}
            or folded.startswith('šifr')
            or folded.startswith('sifr')
        ):
            sifra_i = index
            continue
        if 'cijen' in folded or 'iznos' in folded or 'ukupn' in folded:
            continue
        if qty_i is None and (
            folded in {'količina', 'kolicina', 'kol', 'kol.', 'qty', 'kom', 'komada', 'prodano', 'prodato'}
            or folded.startswith('koli')
        ):
            qty_i = index
    if sifra_i is None or qty_i is None:
        return None
    return sifra_i, qty_i


def _mp_pair_from_cells(cells):
    tokens = [_mp_norm_cell(cell) for cell in cells if _mp_norm_cell(cell)]
    if not tokens:
        return None
    if _mp_parse_qty_token(tokens[0]) is not None and not _mp_is_sifra_token(tokens[0]) and len(tokens) > 1:
        tokens = tokens[1:]
    sifra = None
    qty = None
    for index in range(len(tokens) - 1, -1, -1):
        token = tokens[index]
        parsed = _mp_parse_qty_token(token)
        if parsed is None or _mp_is_sifra_token(token):
            continue
        qty = parsed
        tokens = tokens[:index] + tokens[index + 1:]
        break
    for token in tokens:
        if _mp_is_sifra_token(token):
            sifra = token
            break
    if not sifra:
        return None
    return sifra, qty or 1


_MP_DATE_RE = re.compile(
    r'(?:datum\s*:?\s*)?(\d{1,2})[./-](\d{1,2})[./-](\d{4})',
    re.IGNORECASE,
)


def _mp_date_from_parts(day, month, year):
    try:
        parsed = date(int(year), int(month), int(day))
    except (TypeError, ValueError):
        return None
    if parsed.year < 2000 or parsed.year > 2100:
        return None
    return parsed


def parse_mp_daily_datum(text, *, filename=''):
    """Datum dokumenta, npr. DATUM : 26.08.2026 ili 26/08/2026 u redovima."""
    sources = [text or '', filename or '']
    labeled = None
    first = None
    for source in sources:
        for match in _MP_DATE_RE.finditer(source):
            parsed = _mp_date_from_parts(*match.groups())
            if parsed is None:
                continue
            around = source[max(0, match.start() - 16):match.end()].casefold()
            if 'datum' in around:
                labeled = parsed
                break
            if first is None:
                first = parsed
        if labeled is not None:
            return labeled
    return labeled or first


def parse_mp_daily_text(text):
    """Iz teksta izvuci šifre ispod SIFRA i količine ispod Količina. Iste šifre se sabiraju."""
    raw = (text or '').replace('\r\n', '\n').replace('\r', '\n')
    glued = _parse_promet_glued(raw)
    if len(glued) >= 2:
        return _merge_sifra_qty(glued)
    lines = [line.strip() for line in raw.split('\n') if line.strip()]
    header = None
    pairs = []
    for line in lines:
        cells = _mp_split_line(line)
        if not cells:
            continue
        joined = ' '.join(cells).casefold()
        if any(mark in joined for mark in ('ukupno', 'smjena', 'skladište', 'skladiste')) and not header:
            if not any(_mp_is_sifra_from_column(cell) for cell in cells):
                continue
        if header is None:
            found = _mp_header_indexes(cells)
            if found:
                header = found
                continue
        if header is not None:
            sifra_i, qty_i = header
            if (
                sifra_i < len(cells)
                and qty_i < len(cells)
                and _mp_is_sifra_from_column(cells[sifra_i])
            ):
                pair = (cells[sifra_i], _mp_parse_qty_token(cells[qty_i]) or 1)
            else:
                pair = _mp_pair_from_cells(cells)
        else:
            pair = _mp_pair_from_cells(cells)
        if pair:
            pairs.append(pair)
    return _merge_sifra_qty(pairs)


def preview_mp_daily_rows(parsed):
    """Za svaku šifru: ima li artikal u bazi (zeleno) ili ne (crveno)."""
    rows = []
    for row in parsed or []:
        sifra = str(row.get('sifra') or '').strip()
        qty = int(row.get('qty') or 0)
        product, variation = find_product_by_sifra(sifra)
        mp_qty = 0
        if product is not None:
            mp_qty = sum(int(stock.kolicina or 0) for stock in _mp_stock_rows(product, variation))
        ostaje = max(0, mp_qty - qty) if product is not None else None
        changed = product is not None and ostaje is not None and ostaje != mp_qty
        rows.append({
            'sifra': sifra,
            'qty': qty,
            'found': product is not None,
            'naziv': product.naziv if product is not None else '',
            'mp_dostupno': mp_qty,
            'ostaje': ostaje,
            'changed': changed,
            'product_id': product.pk if product is not None else None,
            'variation_id': variation.pk if variation is not None else None,
        })
    return rows


def find_product_by_sifra(sifra):
    key = (sifra or '').strip()
    if not key:
        return None, None
    variation = (
        ProductVariation.objects.select_related('artikal')
        .filter(sifra__iexact=key)
        .first()
    )
    if variation is not None and variation.artikal_id:
        return variation.artikal, variation
    product = Product.objects.filter(sifra__iexact=key).first()
    if product is not None:
        return product, None
    folded = key.casefold()
    variation = (
        ProductVariation.objects.select_related('artikal')
        .filter(sifra_normalized__iexact=folded)
        .first()
    )
    if variation is not None and variation.artikal_id:
        return variation.artikal, variation
    return None, None


def _lager_compare_locations(mode):
    if mode == 'mp':
        return maloprodaja_locations()
    return usable_locations().exclude(_location_keyword_q(('maloprodaja',)))


def _lager_qty_on_locations(product, variation, locations):
    if product is None or not locations:
        return 0, []
    qs = WarehouseStock.objects.filter(
        product=product,
        location__in=locations,
        kolicina__gt=0,
    ).select_related('location')
    if variation is not None:
        own = list(qs.filter(variation=variation))
        stocks = own if own else list(qs.filter(variation__isnull=True))
    else:
        stocks = list(qs.filter(variation__isnull=True))
    total = sum(int(row.kolicina or 0) for row in stocks)
    labels = [
        f'{row.location.sifra} ({int(row.kolicina or 0)})'
        for row in stocks
        if row.location_id
    ]
    return total, labels


def compare_lager_document(parsed, *, mode):
    """Uporedi šifre/količine sa dokumenta sa MP ili VP lokacijama. Ne mijenja zalihe."""
    if mode not in {'mp', 'vp'}:
        raise MagacinError('Izaberi Maloprodaju ili Veleprodaju.')
    locations = list(_lager_compare_locations(mode))
    rows = []
    for item in parsed or []:
        raw_sifra = str(item.get('sifra') or '').strip()
        sifra = _lager_sifra_from_cell(raw_sifra) or (
            raw_sifra if any(ch.isdigit() for ch in raw_sifra) else ''
        )
        dokument = max(0, int(item.get('qty') or 0))
        pdf_naziv = str(item.get('naziv') or '').strip()
        if not sifra:
            continue
        product, variation = find_product_by_sifra(sifra)
        if product is None:
            rows.append({
                'sifra': sifra,
                'naziv': pdf_naziv,
                'dokument': dokument,
                'lager': None,
                'razlika': None,
                'status': 'nema_sifre',
                'status_label': 'Šifra nije pronađena',
                'lokacije': '',
                'on_location': False,
                'product_id': None,
                'variation_id': None,
            })
            continue
        katalog_naziv = product.naziv
        if variation:
            katalog_naziv = f'{product.naziv} {variation.naziv}'.strip()
        naziv = pdf_naziv or katalog_naziv
        lager, loc_labels = _lager_qty_on_locations(product, variation, locations)
        kasa = dokument - lager
        if kasa > 0:
            status, label = 'visak', f'Kasa +{kasa} Višak'
        elif kasa < 0:
            status, label = 'manjak', f'Kasa {kasa} Manjak'
        else:
            status, label = 'tacno', 'Tačno'
        rows.append({
            'sifra': sifra,
            'naziv': naziv or pdf_naziv,
            'dokument': dokument,
            'lager': lager,
            'lokacije': ', '.join(loc_labels),
            'product_id': product.pk,
            'variation_id': variation.pk if variation is not None else None,
            'razlika': kasa,
            'status': status,
            'status_label': label,
            'on_location': lager > 0,
        })
    summary = {
        'ukupno': len(rows),
        'tacno': sum(1 for row in rows if row['status'] == 'tacno'),
        'manjak': sum(1 for row in rows if row['status'] == 'manjak'),
        'visak': sum(1 for row in rows if row['status'] == 'visak'),
        'nije_na_lokaciji': sum(1 for row in rows if row['status'] == 'nije_na_lokaciji'),
        'nema_sifre': sum(1 for row in rows if row['status'] == 'nema_sifre'),
    }
    return {'rows': rows, 'summary': summary, 'mode': mode}


def _mp_stock_rows(product, variation=None):
    locations = list(maloprodaja_locations())
    if not locations:
        return []
    qs = WarehouseStock.objects.filter(
        product=product,
        location__in=locations,
        kolicina__gt=0,
    ).select_related('location')
    if variation is not None:
        own = qs.filter(variation=variation)
        if own.exists():
            qs = own
        else:
            qs = qs.filter(variation__isnull=True)
    else:
        qs = qs.filter(variation__isnull=True)
    return list(qs.order_by('-kolicina', 'location__redoslijed', 'id'))


@transaction.atomic
def deduct_mp_daily_stock(text=None, *, parsed=None, user=None):
    """Skini prepoznate količine samo s maloprodajnih lokacija artikla."""
    if parsed is None:
        parsed = parse_mp_daily_text(text)
    if not parsed:
        raise MagacinError('Nisam prepoznao šifre i količine na dokumentu.')
    if not maloprodaja_locations().exists():
        raise MagacinError('Nema maloprodajne lokacije.')
    taken = []
    skipped = []
    for row in parsed:
        sifra = row['sifra']
        qty = int(row['qty'] or 0)
        product, variation = find_product_by_sifra(sifra)
        if product is None:
            skipped.append({'sifra': sifra, 'qty': qty, 'razlog': 'šifra nije pronađena'})
            continue
        remaining = qty
        used = []
        for stock in _mp_stock_rows(product, variation):
            if remaining <= 0:
                break
            take = min(int(stock.dostupno or 0), remaining)
            if take <= 0:
                continue
            apply_movement(
                product=product,
                variation=variation if stock.variation_id else None,
                location=stock.location,
                tip=WarehouseMovement.Tip.PRODAJA,
                kolicina=take,
                napomena=f'Dnevno skidanje MP lagera {sifra}',
                user=user,
            )
            used.append({'location': stock.location.label, 'qty': take})
            remaining -= take
        if not used:
            skipped.append({
                'sifra': sifra,
                'qty': qty,
                'naziv': product.naziv,
                'razlog': 'nema količine na maloprodajnoj lokaciji',
            })
            continue
        taken.append({
            'sifra': sifra,
            'naziv': product.naziv,
            'qty': qty,
            'taken': qty - remaining,
            'leftover': remaining,
            'lokacije': used,
            'product_id': product.pk,
            'variation_id': variation.pk if variation is not None else None,
        })
        if remaining > 0:
            skipped.append({
                'sifra': sifra,
                'qty': remaining,
                'naziv': product.naziv,
                'razlog': f'skinuto {qty - remaining}, nedostaje {remaining} na MP',
            })
    return {'parsed': parsed, 'taken': taken, 'skipped': skipped}


_XAI_API_URL = 'https://api.x.ai/v1/chat/completions'
_MP_VISION_PROMPT = (
    'Na slici je tabela „Promet po artiklima” ili sličan izvještaj.\n'
    '1) Nađi DATUM dokumenta (npr. DATUM : 26.08.2026 ili 26/08/2026). Vrati ga u polju datum.\n'
    '2) Nađi zaglavlje kolone ŠIFRA / SIFRA / Sifra.\n'
    '3) Svaki broj ISPOD tog zaglavlja je šifra (npr. 785, 4227). Prepiši tačno.\n'
    '4) Nađi zaglavlje kolone KOLIČINA / Kolicina.\n'
    '5) U ISTOM REDU, broj ispod KOLIČINA je količina pored te šifre '
    '(1.000 znači 1, 3.000 znači 3, 2.000 znači 2).\n'
    '6) Ako se ista šifra pojavi više puta, vrati SVAKI red (npr. 4227 količina 3 i 4227 količina 2).\n'
    '7) Ignoriši DOK, RBR, naziv, cijenu, %RAB, vrijednost, ukupno, smjenu.\n'
    'Vrati SAMO JSON:\n'
    '{"datum":"26/08/2026","stavke":[{"sifra":"785","kolicina":1},{"sifra":"4227","kolicina":3}]}\n'
)


def _mp_resize_image_bytes(data, *, mime='image/jpeg'):
    from PIL import Image

    image = Image.open(BytesIO(data))
    if image.mode not in ('RGB', 'L'):
        image = image.convert('RGB')
    elif image.mode == 'L':
        image = image.convert('RGB')
    image.thumbnail((2200, 2200))
    buf = BytesIO()
    image.save(buf, format='JPEG', quality=82)
    return buf.getvalue(), 'image/jpeg'


def _mp_vision_content(image_bytes, *, mime='image/jpeg', prompt=None, max_tokens=4000, timeout=90):
    from .quick_activation import xai_api_key

    import requests

    api_key = xai_api_key()
    if not api_key:
        raise MagacinError('Za očitavanje slike treba XAI_API_KEY u .env.')
    payload_bytes, mime = _mp_resize_image_bytes(image_bytes, mime=mime)
    b64 = base64.b64encode(payload_bytes).decode('ascii')
    model = (os.environ.get('XAI_MODEL') or '').strip() or 'grok-4.5'
    try:
        resp = requests.post(
            _XAI_API_URL,
            headers={
                'Authorization': f'Bearer {api_key}',
                'Content-Type': 'application/json',
            },
            json={
                'model': model,
                'temperature': 0,
                'max_tokens': max_tokens,
                'messages': [
                    {
                        'role': 'user',
                        'content': [
                            {'type': 'text', 'text': prompt or _MP_VISION_PROMPT},
                            {
                                'type': 'image_url',
                                'image_url': {'url': f'data:{mime};base64,{b64}'},
                            },
                        ],
                    }
                ],
            },
            timeout=timeout,
        )
    except requests.RequestException as exc:
        raise MagacinError(f'Očitavanje slike nije uspjelo: {exc}') from exc
    if resp.status_code >= 400:
        raise MagacinError(
            'Očitavanje slike nije uspjelo. Provjeri XAI_API_KEY i pokušaj jasniju sliku.'
        )
    try:
        return (((resp.json() or {}).get('choices') or [{}])[0].get('message') or {}).get('content') or ''
    except ValueError as exc:
        raise MagacinError('Odgovor očitavanja nije validan.') from exc


def _mp_vision_table(image_bytes, *, mime='image/jpeg'):
    return _normalize_mp_vision_text(_mp_vision_content(image_bytes, mime=mime))


def _mp_rows_to_tsv(rows):
    lines = ['Šifra\tKoličina']
    for sifra, qty in rows:
        if sifra:
            lines.append(f'{sifra}\t{qty}')
    return '\n'.join(lines)


def _mp_parse_vision_json(stripped):
    try:
        data = json.loads(stripped)
    except (TypeError, ValueError):
        match = re.search(r'(\{.*\}|\[.*\])', stripped, re.DOTALL)
        if not match:
            return None
        try:
            data = json.loads(match.group(1))
        except (TypeError, ValueError):
            return None
    datum_raw = ''
    if isinstance(data, dict):
        datum_raw = str(
            data.get('datum') or data.get('date') or data.get('Datum') or ''
        ).strip()
        data = data.get('stavke') or data.get('rows') or data.get('items') or []
    if not isinstance(data, list):
        return None
    rows = []
    for row in data:
        if not isinstance(row, dict):
            continue
        sifra = str(
            row.get('sifra') or row.get('SIFRA') or row.get('sku') or row.get('code') or ''
        ).strip()
        qty = row.get('kolicina') or row.get('količina') or row.get('Kolicina') or row.get('qty')
        if not sifra or not _mp_is_sifra_from_column(sifra):
            continue
        parsed_qty = _mp_parse_qty_token(str(qty if qty is not None else '1'))
        rows.append((sifra, parsed_qty or 1))
    if not rows:
        return None
    return rows, datum_raw


def _mp_parse_markdown_table(stripped):
    rows = []
    header = None
    for raw_line in stripped.split('\n'):
        line = raw_line.strip()
        if not line.startswith('|'):
            continue
        cells = [_mp_norm_cell(part) for part in line.strip('|').split('|')]
        if not cells or set(''.join(cells)) <= set('-: '):
            continue
        if header is None:
            found = _mp_header_indexes(cells)
            if found:
                header = found
            continue
        sifra_i, qty_i = header
        if sifra_i >= len(cells) or qty_i >= len(cells):
            continue
        sifra = cells[sifra_i]
        if not _mp_is_sifra_from_column(sifra):
            continue
        rows.append((sifra, _mp_parse_qty_token(cells[qty_i]) or 1))
    return rows or None


def _normalize_mp_vision_text(raw):
    text = (raw or '').strip()
    if not text:
        return ''
    if text.startswith('```'):
        text = re.sub(r'^```(?:\w+)?\s*', '', text)
        text = re.sub(r'\s*```$', '', text)
    stripped = text.strip()
    parsed = _mp_parse_vision_json(stripped)
    if parsed:
        rows, datum_raw = parsed
        text = _mp_rows_to_tsv(rows)
        if datum_raw:
            text = f'DATUM: {datum_raw}\n{text}'
        return text
    parsed = _mp_parse_markdown_table(stripped)
    if parsed:
        return _mp_rows_to_tsv(parsed)
    return stripped


def _mp_pdf_text_and_images(data, *, max_pages=6, max_images_per_page=4):
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise MagacinError('pypdf nije instaliran. Pokreni: pip install pypdf') from exc
    reader = PdfReader(BytesIO(data))
    texts = []
    images = []
    pages = reader.pages if not max_pages else reader.pages[:max_pages]
    for page in pages:
        extracted = (page.extract_text() or '').strip()
        if extracted:
            texts.append(extracted)
        try:
            page_images = list(page.images or [])
        except Exception:
            page_images = []
        if max_images_per_page:
            page_images = page_images[:max_images_per_page]
        for img in page_images:
            blob = getattr(img, 'data', None)
            if blob:
                images.append(blob)
    return '\n'.join(texts), images


def extract_mp_daily_text_from_upload(uploaded):
    """Iz slike ili PDF-a izvuci tekst šifra+količina."""
    name = (getattr(uploaded, 'name', '') or '').strip().casefold()
    content_type = (getattr(uploaded, 'content_type', '') or '').casefold()
    data = uploaded.read()
    if hasattr(uploaded, 'seek'):
        try:
            uploaded.seek(0)
        except Exception:
            pass
    if not data:
        raise MagacinError('Fajl je prazan.')
    is_pdf = content_type == 'application/pdf' or name.endswith('.pdf')
    is_image = content_type.startswith('image/') or name.endswith((
        '.jpg', '.jpeg', '.png', '.webp', '.gif', '.bmp',
    ))
    if not is_pdf and not is_image:
        raise MagacinError('Uploaduj sliku (JPG/PNG) ili PDF.')
    if is_pdf:
        pdf_text, images = _mp_pdf_text_and_images(data)
        parsed = parse_mp_daily_text(pdf_text)
        if parsed:
            return pdf_text
        chunks = []
        for blob in images[:4]:
            chunks.append(_mp_vision_table(blob))
        if not chunks:
            raise MagacinError(
                'PDF nema čitljiv tekst ni slike. Sačuvaj stranicu kao sliku pa uploaduj.'
            )
        return '\n'.join(chunks)
    return _mp_vision_table(data, mime=content_type or 'image/jpeg')


_LAGER_VISION_PROMPT = (
    'Na slici je tabela „Stanje lagera”. Kolone slijeva:\n'
    'ŠIFRA | BARKOD | NAZIV | JM | KOLIČINA | MAL.CIJENA | VRIJEDNOST.\n'
    'Za SVAKI red:\n'
    '- sifra = SAMO BROJ iz kolone ŠIFRA (npr. 2, 16, 73). '
    'To NIJE barkod (702…) i NIJE kod iz naziva (60554NP-TX-…).\n'
    '- naziv = kolona NAZIV.\n'
    '- kolicina = kolona KOLIČINA (2.000 znači 2). '
    'To NIJE MAL.CIJENA (8.000) i NIJE VRIJEDNOST (16.00).\n'
    'Primjer: šifra 73, naziv „60554NP-TX-6-Y10 Ultra NP Carp XV2 Chodda”, količina 2.\n'
    'Vrati SAMO JSON:\n'
    '{"stavke":[{"sifra":"73","naziv":"60554NP-TX-6-Y10 Ultra NP Carp XV2 Chodda","kolicina":2}]}\n'
)

_STANJE_LAGERA_LINE = re.compile(
    r'^(\d{1,6})\s+(.+?)\s+(\d+)[.,]000\s+(\d+[.,]\d{2,3})\s+(-?\d+[.,]\d{2})\s*(?:kom\d*)?\s*$',
    re.IGNORECASE,
)

_LAGER_QTY_INDEX = 4  # 5. polje od početka (0-based)

_LAGER_SIFRA_SKIP = {
    'šifra', 'sifra', 'sku', 'naziv', 'kolicina', 'količina', 'artikal',
    'kol', 'kol.', 'qty', 'kom', 'komada', 'ukupno', 'rbr', 'dok',
}


def _lager_cell_text(token):
    if token is None or isinstance(token, bool):
        return ''
    if isinstance(token, int):
        return str(token)
    if isinstance(token, float):
        if token.is_integer():
            return str(int(token))
        return str(token).strip()
    return _mp_norm_cell(token)


def _lager_sifra_from_cell(token):
    """Šifra sa dokumenta — samo brojevi."""
    text = _lager_cell_text(token)
    if not text:
        return ''
    folded = text.casefold().replace(':', '').strip()
    if folded in _LAGER_SIFRA_SKIP or folded in {'za', 'za.'}:
        return ''
    if folded.startswith('šifr') or folded.startswith('sifr'):
        return ''
    compact = re.sub(r'\s+', '', text)
    if compact.isdigit():
        return compact[:80]
    match = re.match(r'^(\d+)', compact)
    if match:
        return match.group(1)[:80]
    return ''


def _lager_looks_like_sifra(token):
    text = _lager_sifra_from_cell(token)
    return bool(text and text.isdigit())


def _lager_looks_like_price(token):
    text = _lager_cell_text(token).replace(' ', '').replace(',', '.')
    text = re.sub(r'(km|bam)$', '', text, flags=re.I)
    if re.fullmatch(r'\d+\.\d{1,2}', text) and not re.fullmatch(r'\d+\.000', text):
        return True
    if isinstance(token, float) and not token.is_integer():
        return True
    return False


def _lager_parse_qty(token):
    if isinstance(token, bool):
        return None
    if _lager_looks_like_price(token):
        return None
    if isinstance(token, int):
        return token if token >= 0 else None
    if isinstance(token, float) and token.is_integer():
        qty = int(token)
        return qty if qty >= 0 else None
    parsed = _mp_parse_qty_token(_lager_cell_text(token))
    if parsed is not None:
        return parsed
    text = _lager_cell_text(token).replace(' ', '').replace(',', '.')
    if re.fullmatch(r'0+(\.0+)?', text):
        return 0
    return None


def _lager_dot000_qty(token):
    text = _lager_cell_text(token).replace(' ', '').replace(',', '.')
    match = re.fullmatch(r'(\d+)\.000', text)
    if not match:
        return None
    return int(match.group(1))


def _lager_unstick_qty(naziv, qty):
    """Ako je u količinu upala cijena, a količina zalijepljena na naziv — rastavi."""
    naziv = (naziv or '').strip()
    glued = re.search(r'^(.*\S)\s+(\d+)[.,]000$', naziv)
    if glued:
        return glued.group(1).strip(), int(glued.group(2))
    if qty is not None and _lager_looks_like_price(qty):
        qty = None
    match = re.search(r'^(.*\S)\s+(\d{1,4})$', naziv)
    if qty is None and match:
        return match.group(1).strip(), int(match.group(2))
    return naziv, qty


def _lager_header_indexes(cells):
    sifra_i = None
    naziv_i = None
    qty_i = None
    cijena_i = None
    for index, cell in enumerate(cells):
        folded = cell.casefold().replace(':', '').strip()
        if sifra_i is None and (
            folded in {'šifra', 'sifra', 'sku', 'sifra artikla', 'šifra artikla'}
            or folded.startswith('šifr')
            or folded.startswith('sifr')
        ):
            sifra_i = index
            continue
        if naziv_i is None and (
            folded in {'naziv', 'naziv artikla', 'artikal', 'artikel', 'name', 'opis'}
            or folded.startswith('naziv')
        ):
            naziv_i = index
            continue
        if (
            'cijen' in folded
            or 'iznos' in folded
            or 'ukupn' in folded
            or 'vrijed' in folded
            or folded in {'mpc', 'vpc', 'pdv', 'rabat', '%rab'}
        ):
            if cijena_i is None:
                cijena_i = index
            continue
        if qty_i is None and (
            folded in {'količina', 'kolicina', 'kol', 'kol.', 'qty', 'kom', 'komada'}
            or folded.startswith('koli')
        ):
            qty_i = index
    if sifra_i is None or qty_i is None:
        return None
    return sifra_i, naziv_i, qty_i, cijena_i


def _lager_skip_rbr(tokens):
    if len(tokens) < 2:
        return tokens
    first = tokens[0]
    if not first.isdigit():
        return tokens
    try:
        rbr = int(first)
    except ValueError:
        return tokens
    if not 1 <= rbr <= 199:
        return tokens
    if _lager_looks_like_sifra(tokens[1]) and (
        not tokens[1].isdigit() or len(tokens[1]) >= 4
    ):
        return tokens[1:]
    return tokens


def _lager_first_dot000(tokens, *, start_at=0):
    for index in range(max(0, start_at), len(tokens)):
        qty = _lager_dot000_qty(tokens[index])
        if qty is not None:
            return index, qty
    return None, None


def _lager_qty_from_fifth(tokens):
    """Količina je 5. polje od početka reda."""
    if len(tokens) >= 5:
        qty = _lager_parse_qty(tokens[_LAGER_QTY_INDEX])
        if qty is not None:
            return _LAGER_QTY_INDEX, qty
        dot_i, dot_qty = _lager_first_dot000(tokens, start_at=_LAGER_QTY_INDEX)
        if dot_i is not None:
            return dot_i, dot_qty
    return None, None


def _lager_pick_sifra(tokens, header=None, skip_indexes=None):
    skip = {i for i in (skip_indexes or []) if i is not None}
    if header:
        sifra_i = header[0]
        if sifra_i is not None and sifra_i < len(tokens) and sifra_i not in skip:
            sifra = _lager_sifra_from_cell(tokens[sifra_i])
            if sifra:
                return sifra, sifra_i
    for index, token in enumerate(tokens):
        if index in skip:
            continue
        if index == _LAGER_QTY_INDEX:
            continue
        if index == 0 and token.isdigit() and 1 <= int(token) <= 199 and len(tokens) > 1:
            continue
        sifra = _lager_sifra_from_cell(token)
        if sifra:
            return sifra, index
    return '', None


def _lager_triple_from_cells(cells, header=None):
    tokens = [_mp_norm_cell(cell) for cell in cells if _mp_norm_cell(cell)]
    if not tokens:
        return None
    qty_i, qty = _lager_qty_from_fifth(tokens)
    if header and qty is None:
        header_qty_i = header[2]
        if header_qty_i is not None and header_qty_i < len(tokens):
            qty = _lager_parse_qty(tokens[header_qty_i])
            if qty is not None:
                qty_i = header_qty_i
    sifra, sifra_at = _lager_pick_sifra(tokens, header, skip_indexes={qty_i})
    if header and not sifra:
        sifra_i = header[0]
        if sifra_i is not None and sifra_i < len(tokens):
            sifra = _lager_sifra_from_cell(tokens[sifra_i])
            sifra_at = sifra_i
    if not sifra or qty is None:
        return None
    naziv = ''
    if header and header[1] is not None and header[1] < len(tokens):
        naziv_i = header[1]
        end = qty_i if qty_i is not None and qty_i > naziv_i else naziv_i + 1
        cijena_i = header[3] if header else None
        if cijena_i is not None and naziv_i < cijena_i < end:
            end = cijena_i
        parts = []
        for index in range(naziv_i, end):
            if index in {sifra_at, qty_i, cijena_i}:
                continue
            if _lager_looks_like_price(tokens[index]):
                continue
            parts.append(tokens[index])
        naziv = ' '.join(parts).strip()
    elif sifra_at is not None and qty_i is not None and qty_i > sifra_at:
        parts = []
        for token in tokens[sifra_at + 1:qty_i]:
            if _lager_looks_like_price(token):
                continue
            parts.append(token)
        naziv = ' '.join(parts).strip()
    naziv, qty = _lager_unstick_qty(naziv, qty)
    return sifra, naziv, qty


def _merge_lager_rows(triples):
    merged = {}
    order = []
    for sifra, naziv, qty in triples:
        sifra = str(sifra or '').strip()
        naziv = str(naziv or '').strip()
        try:
            qty = int(qty or 0)
        except (TypeError, ValueError):
            continue
        if not sifra or qty < 0:
            continue
        key = sifra.casefold()
        if key not in merged:
            merged[key] = {'sifra': sifra, 'naziv': naziv, 'qty': 0}
            order.append(key)
        merged[key]['qty'] += qty
        if naziv and not merged[key]['naziv']:
            merged[key]['naziv'] = naziv
    return [merged[key] for key in order]


def _lager_parse_vision_json(stripped):
    try:
        data = json.loads(stripped)
    except (TypeError, ValueError):
        match = re.search(r'(\{.*\}|\[.*\])', stripped, re.DOTALL)
        if not match:
            return None
        try:
            data = json.loads(match.group(1))
        except (TypeError, ValueError):
            return None
    if isinstance(data, dict):
        data = data.get('stavke') or data.get('rows') or data.get('items') or []
    if not isinstance(data, list):
        return None
    triples = []
    for row in data:
        if not isinstance(row, dict):
            continue
        sifra = _lager_sifra_from_cell(
            row.get('sifra') or row.get('SIFRA') or row.get('sku') or row.get('code') or ''
        )
        naziv = str(
            row.get('naziv') or row.get('Naziv') or row.get('name') or row.get('artikal') or ''
        ).strip()
        qty = row.get('kolicina')
        if qty is None:
            qty = row.get('količina')
        if qty is None:
            qty = row.get('Kolicina')
        if qty is None:
            qty = row.get('qty')
        if not sifra:
            continue
        parsed_qty = _lager_parse_qty(qty if qty is not None else 1)
        naziv, parsed_qty = _lager_unstick_qty(naziv, parsed_qty)
        triples.append((sifra, naziv, parsed_qty if parsed_qty is not None else 1))
    return triples or None


def parse_stanje_lagera_text(text):
    """PDF „Stanje lagera”: ŠIFRA, NAZIV, KOLIČINA (ne barkod, ne mal.cijena)."""
    triples = []
    for line in (text or '').replace('\r\n', '\n').replace('\r', '\n').split('\n'):
        line = line.strip()
        if not line or re.fullmatch(r'\d{1,2}/\d{1,2}/\d{2,4}', line):
            continue
        match = _STANJE_LAGERA_LINE.match(line)
        if not match:
            continue
        sifra = match.group(1)
        naziv = re.sub(r'\s+', ' ', match.group(2)).strip()
        qty = int(match.group(3))
        if sifra and naziv:
            triples.append((sifra, naziv, qty))
    return _merge_lager_rows(triples) if triples else []


def parse_lager_document_text(text):
    """Iz teksta/JSON-a izvuci kolone Šifra, Naziv i Količina."""
    raw = (text or '').replace('\r\n', '\n').replace('\r', '\n').strip()
    if not raw:
        return []
    stanje = parse_stanje_lagera_text(raw)
    if stanje:
        return stanje
    if raw.startswith('```'):
        raw = re.sub(r'^```(?:\w+)?\s*', '', raw)
        raw = re.sub(r'\s*```$', '', raw)
        raw = raw.strip()
    from_json = _lager_parse_vision_json(raw)
    if from_json:
        return _merge_lager_rows(from_json)
    lines = [line.strip() for line in raw.split('\n') if line.strip()]
    header = None
    triples = []
    for line in lines:
        if line.startswith('|'):
            cells = [_mp_norm_cell(part) for part in line.strip('|').split('|')]
            if cells and set(''.join(cells)) <= set('-: '):
                continue
        else:
            cells = _mp_split_line(line)
        if not cells:
            continue
        if header is None:
            found = _lager_header_indexes(cells)
            if found:
                header = found
                continue
        triple = _lager_triple_from_cells(cells, header)
        if triple:
            triples.append(triple)
    if triples:
        return _merge_lager_rows(triples)
    fallback = parse_mp_daily_text(raw)
    return [{'sifra': row['sifra'], 'naziv': '', 'qty': row['qty']} for row in fallback]


def extract_lager_document_rows(uploaded):
    """Sa PDF/slike pročitaj kolone Šifra, Naziv i Količina."""
    name = (getattr(uploaded, 'name', '') or '').strip().casefold()
    content_type = (getattr(uploaded, 'content_type', '') or '').strip().casefold()
    data = uploaded.read()
    if hasattr(uploaded, 'seek'):
        try:
            uploaded.seek(0)
        except Exception:
            pass
    if not data:
        raise MagacinError('Fajl je prazan.')
    is_pdf = content_type == 'application/pdf' or name.endswith('.pdf')
    is_image = content_type.startswith('image/') or name.endswith((
        '.jpg', '.jpeg', '.png', '.webp', '.gif', '.bmp',
    ))
    if not is_pdf and not is_image:
        raise MagacinError('Uploaduj PDF ili sliku tabele (Šifra, Naziv, Količina).')

    def from_image(blob, mime='image/jpeg'):
        content = _mp_vision_content(
            blob,
            mime=mime,
            prompt=_LAGER_VISION_PROMPT,
            max_tokens=32768,
            timeout=180,
        )
        return parse_lager_document_text(content)

    if is_pdf:
        pdf_text, images = _mp_pdf_text_and_images(
            data, max_pages=None, max_images_per_page=None,
        )
        text_rows = parse_lager_document_text(pdf_text)
        if text_rows:
            return text_rows
        triples = []
        for blob in images:
            try:
                triples.extend(from_image(blob) or [])
            except MagacinError:
                continue
        if triples:
            return _merge_lager_rows(
                (row['sifra'], row.get('naziv') or '', row.get('qty') or 0)
                for row in triples
            )
        raise MagacinError(
            'Nisam pročitao kolone Šifra, Naziv i Količina. Uploaduj jasniji PDF ili sliku tabele.'
        )
    rows = from_image(data, mime=content_type or 'image/jpeg')
    if not rows:
        raise MagacinError(
            'Nisam pročitao kolone Šifra, Naziv i Količina sa slike. Probaj jasniji snimak tabele.'
        )
    return rows


def save_mp_daily_skidanje(result, *, user=None, fajl=None, raw_text='', datum=None):
    from .models import MagacinMpDnevnoSkidanje, MagacinMpDnevnoStavka

    taken = list((result or {}).get('taken') or [])
    komada = sum(int(row.get('taken') or 0) for row in taken)
    naziv = ''
    if fajl is not None:
        naziv = (getattr(fajl, 'name', '') or '')[:200]
        if hasattr(fajl, 'seek'):
            try:
                fajl.seek(0)
            except Exception:
                pass
    if datum is None:
        datum = parse_mp_daily_datum(raw_text, filename=naziv) or timezone.localdate()
    batch = MagacinMpDnevnoSkidanje(
        kreirao=user if getattr(user, 'is_authenticated', False) else None,
        datum=datum,
        fajl_naziv=naziv,
        raw_text=(raw_text or '')[:20000],
        skinuto_stavki=len(taken),
        skinuto_komada=komada,
    )
    if fajl is not None:
        batch.fajl = fajl
    batch.save()
    stavke = []
    for row in taken:
        lokacije = row.get('lokacije') or []
        loc_label = ', '.join(
            f"{item.get('location')} (−{item.get('qty')})"
            for item in lokacije
            if isinstance(item, dict)
        )
        stavke.append(MagacinMpDnevnoStavka(
            skidanje=batch,
            product_id=row.get('product_id'),
            variation_id=row.get('variation_id'),
            sifra=(row.get('sifra') or '')[:200],
            naziv=(row.get('naziv') or '')[:200],
            trazeno=int(row.get('qty') or 0),
            kolicina=int(row.get('taken') or 0),
            lokacija=loc_label[:300],
        ))
    if stavke:
        MagacinMpDnevnoStavka.objects.bulk_create(stavke)
    return batch


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


def ponuda_totals(ponuda):
    from .cart import izracunaj_pdv

    osnova = Decimal('0.00')
    for row in ponuda.stavke.all():
        osnova += (row.cijena or Decimal('0.00')) * int(row.kolicina or 0)
    osnova = osnova.quantize(Decimal('0.01'))
    popust = Decimal(str(ponuda.popust_iznos or 0)).quantize(Decimal('0.01'))
    pct = ponuda.popust_postotak
    if (not popust or popust <= 0) and pct:
        popust = (osnova * Decimal(str(pct)) / Decimal('100')).quantize(
            Decimal('0.01'), rounding=ROUND_HALF_UP,
        )
    if popust < 0:
        popust = Decimal('0.00')
    if popust > osnova:
        popust = osnova
    split = izracunaj_pdv((osnova - popust).quantize(Decimal('0.01')))
    return {
        'osnova': osnova,
        'popust': popust,
        'net': split['bez_pdv'],
        'pdv': split['pdv'],
        'ukupno_sa_pdv': split['sa_pdvom'],
    }


@transaction.atomic
def accept_ponuda(ponuda, *, user=None):
    """Prihvaćena ponuda → magacin narudžba, rezervacija po lokacijama, picking."""
    from .models import MagacinPonuda, Order, OrderItem

    if ponuda.order_id:
        return ponuda.order
    if ponuda.status == MagacinPonuda.Status.NACRT:
        raise MagacinError('Prvo objavi ponudu.')
    stavke = list(ponuda.stavke.select_related('product', 'variation'))
    if not stavke:
        raise MagacinError('Ponuda nema stavki.')
    ime = (ponuda.ime_prezime or '').strip() or f'Ponuda {ponuda.broj}'
    telefon = (ponuda.telefon or '').strip() or '—'
    email = (ponuda.email or '').strip() or 'carpologijabh@gmail.com'
    adresa = (ponuda.adresa or '').strip() or 'Ponuda'
    grad = (ponuda.grad or '').strip() or '—'
    totals = ponuda_totals(ponuda)
    napomena_parts = [f'Ponuda {ponuda.broj}']
    if (ponuda.napomena or '').strip():
        napomena_parts.append(ponuda.napomena.strip())
    popust = totals['popust']
    popust_detalji = []
    if popust > 0:
        if ponuda.popust_iznos and ponuda.popust_iznos > 0:
            popust_detalji.append({
                'opis': f'Popust na ponudi {ponuda.broj}',
                'iznos': str(popust),
            })
        elif ponuda.popust_postotak:
            pct = ponuda.popust_postotak
            pct_label = str(int(pct)) if pct == pct.to_integral() else str(pct)
            popust_detalji.append({
                'opis': f'Popust na ponudi {ponuda.broj} {pct_label}%',
                'iznos': str(popust),
                'postotak': pct_label,
            })
        else:
            popust_detalji.append({
                'opis': f'Popust na ponudi {ponuda.broj}',
                'iznos': str(popust),
            })
    order = Order.objects.create(
        ime_prezime=ime[:200],
        email=email[:254],
        telefon=telefon[:30],
        adresa=adresa[:300],
        grad=grad[:100],
        napomena='\n'.join(napomena_parts),
        medjuzbir=totals['osnova'],
        dostava=Decimal('0.00'),
        popust=popust,
        popust_detalji=popust_detalji,
        ukupno=totals['ukupno_sa_pdv'],
        status=Order.Status.NOVA,
        izvor=Order.Izvor.MAGACIN,
    )
    for row in stavke:
        product = row.product
        variation = row.variation
        qty = max(1, int(row.kolicina or 1))
        if row.manuelno or product is None:
            naziv = (row.naziv or 'Ručni artikal')[:200]
            OrderItem.objects.create(
                narudzba=order,
                artikal=None,
                varijacija=None,
                naziv=naziv,
                product_naziv=naziv,
                varijacija_naziv='',
                sifra=(row.sifra or 'RUCNO')[:SIFRA_MAX_LENGTH],
                cijena=row.cijena,
                bazna_cijena=row.cijena,
                kolicina=qty,
                rezervni_dio=True,
            )
            continue
        bazna = variation.bazna_cijena if variation else product.bazna_cijena
        OrderItem.objects.create(
            narudzba=order,
            artikal=product,
            varijacija=variation,
            naziv=(row.naziv or product.naziv)[:200],
            product_naziv=product.naziv[:200],
            varijacija_naziv=(variation.naziv[:100] if variation else ''),
            sifra=(
                (row.sifra or (variation.sifra if variation and variation.sifra else product.sifra) or '')
                [:SIFRA_MAX_LENGTH]
            ),
            cijena=row.cijena,
            bazna_cijena=bazna,
            kolicina=qty,
        )
        available = stock_totals(product, variation)['dostupno']
        take = min(qty, available)
        if take:
            reserve_for_order(
                order,
                product,
                take,
                variation=variation,
                user=user,
                napomena=f'Ponuda {ponuda.broj} #{order.broj}',
            )
    order.lager_status = Order.LagerStatus.REZERVISANO
    order.save(update_fields=['lager_status'])
    ponuda.status = MagacinPonuda.Status.PRIHVACENA
    ponuda.order = order
    ponuda.prihvacena_at = timezone.now()
    ponuda.save(update_fields=['status', 'order', 'prihvacena_at', 'azuriran'])
    return order


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
        available = display_stock_totals(row.product, row.variation)['dostupno']
        shortfall = max(0, row.kolicina - available)
        if shortfall > 0 and not row.mp_ok:
            raise MagacinError(
                f'„{row.naziv}” nema dostupnog artikla ({available}). '
                f'Označi {NIJE_POPISAN_LABEL} da ga dodaš, ili makni stavku.'
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
        napomena = f'{napomena}\n{NIJE_POPISAN_LABEL}: {", ".join(mp_names)}'
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
    rows, _ = order_location_rows(product, variation)
    for row in rows:
        if remaining <= 0:
            break
        take = min(row['dostupno'], remaining)
        if take <= 0:
            continue
        _stock, hold_variation = _stock_row_for_sale(product, variation, row['location'])
        apply_movement(
            product=product,
            variation=hold_variation,
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
    rows, _ = order_location_rows(product, variation)
    if location is not None:
        loc_pk = getattr(location, 'pk', location)
        rows = [row for row in rows if row['location'].pk == loc_pk]
    for row in rows:
        if remaining <= 0:
            break
        stock, hold_variation = _stock_row_for_sale(product, variation, row['location'])
        avail = max(0, int(stock.kolicina or 0) - max(0, int(stock.rezervisano or 0)))
        take = min(avail, remaining)
        if take <= 0:
            continue
        apply_movement(
            product=product,
            variation=hold_variation,
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
            variation=hold_variation,
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
    if is_vp_order(order) or is_prenos_mp_order(order):
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


def release_holds_for_product(order, product, variation=None, qty=None, *, user=None, napomena=None):
    holds = list(
        order.magacin_holds.filter(
            product=product,
            status=OrderStockHold.Status.REZERVISANO,
            **_hold_variation_filter(variation),
        ).order_by('-kolicina', '-pk')
    )
    remaining = qty if qty is not None else sum(hold.kolicina for hold in holds)
    remaining = max(0, _int(remaining))
    note = napomena or f'Izmjena #{order.broj}'
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
        if new_reserved != stock.rezervisano:
            apply_movement(
                product=hold.product,
                variation=hold.variation,
                location=hold.location,
                tip=WarehouseMovement.Tip.REZERVACIJA,
                kolicina=1,
                rezervisano=new_reserved,
                napomena=note,
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


def _assert_order_open(order):
    if order.lager_status == Order.LagerStatus.VALIDIRANO:
        raise MagacinError('Validirana narudžba se ne može mijenjati.')
    if order.lager_status == Order.LagerStatus.OTKAZANO or order.status == Order.Status.OTKAZANA:
        raise MagacinError('Otkazana narudžba se ne može mijenjati.')


def _assert_order_editable(order):
    if is_prenos_mp_order(order):
        raise MagacinError('Prenos u MP se ne može mijenjati.')
    _assert_order_open(order)


def order_is_editable(order):
    try:
        _assert_order_editable(order)
        return True
    except MagacinError:
        return False


def _note_nije_popisan(order, product, variation=None):
    naziv = f'{product.naziv} {variation.naziv}'.strip() if variation else product.naziv
    note = (order.napomena or '').strip()
    marker = f'{NIJE_POPISAN_LABEL}: {naziv}'
    if marker in note:
        return
    extra = marker if f'{NIJE_POPISAN_LABEL}:' in note else f'{NIJE_POPISAN_LABEL}: {naziv}'
    order.napomena = f'{note}\n{extra}'.strip() if note else extra
    order.save(update_fields=['napomena'])


def order_has_nije_popisan(order, item=None):
    note = order.napomena or ''
    if f'{NIJE_POPISAN_LABEL}:' not in note:
        return False
    if item is None:
        return True
    naziv = (getattr(item, 'product_naziv', None) or getattr(item, 'naziv', None) or '').strip()
    if naziv and naziv in note:
        return True
    product = getattr(item, 'artikal', None)
    if product is not None and product.naziv and product.naziv in note:
        return True
    return False


@transaction.atomic
def add_item_to_order(order, *, product, qty, variation=None, mp_ok=False, user=None):
    _assert_order_editable(order)
    qty = max(1, _int(qty))
    if variation and variation.artikal_id != product.pk:
        raise MagacinError('Varijacija ne pripada artiklu.')
    available = display_stock_totals(product, variation)['dostupno']
    shortfall = max(0, qty - available)
    if shortfall > 0 and not mp_ok:
        raise MagacinError(
            f'„{product.naziv}” nema dostupnog artikla ({available}). '
            f'Označi {NIJE_POPISAN_LABEL} da ga dodaš, ili makni stavku.'
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
        _note_nije_popisan(order, product, variation)
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
    available = display_stock_totals(product, variation)['dostupno']
    if delta > 0:
        shortfall = max(0, delta - available)
        if shortfall > 0 and not mp_ok:
            raise MagacinError(
                f'„{product.naziv}” nema dostupnog artikla ({available}). '
                f'Označi {NIJE_POPISAN_LABEL} da ga dodaš, ili makni stavku.'
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
            _note_nije_popisan(order, product, variation)
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
        loc in VIRTUAL_PICK_LOCS
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
def clear_pick_location_stock(order, item, *, loc, user=None):
    """Usputni popis: količine ovog artikla na toj lokaciji na 0.

    Druge lokacije se ne diraju. Sa sajta ide tek ako UKUPNO (sve lokacije + MP) padne na 0.
    Stavka ostaje na narudžbi; rezervacija se prebaci ako ima zalihe drugdje.
    """
    _assert_order_open(order)
    if item.narudzba_id != order.pk:
        raise MagacinError('Stavka nije na ovoj narudžbi.')
    loc = (loc or '').strip()
    product = item.artikal
    variation = item.varijacija
    if loc in VIRTUAL_PICK_LOCS or product is None:
        raise MagacinError('Ova lokacija se ne čisti s pickinga.')
    location = _location_for_pick_label(loc)
    if location is None:
        raise MagacinError('Lokacija nije pronađena.')
    if is_ignored_stock_location(location) or is_uncountable_stock_location(location):
        raise MagacinError('Ova lokacija se ne čisti s pickinga.')

    note = f'Usputni popis — očisti lokaciju picking #{order.broj}'
    stock, sell_variation = _stock_row_for_sale(product, variation, location)
    cleared = max(0, int(stock.kolicina or 0))
    if cleared or int(stock.rezervisano or 0):
        try:
            apply_movement(
                product=product,
                variation=sell_variation,
                location=location,
                tip=WarehouseMovement.Tip.KOREKCIJA,
                kolicina=0,
                napomena=note,
                user=user,
            )
        except MagacinError:
            stock = get_or_create_stock(
                product=product, variation=sell_variation, location=location,
            )
            stock.kolicina = 0
            stock.rezervisano = 0
            stock.save(update_fields=['kolicina', 'rezervisano', 'azurirano'])
            refresh_catalog_qty(product)

    hold_q = Q(**_hold_variation_filter(variation))
    if sell_variation != variation:
        hold_q |= Q(**_hold_variation_filter(sell_variation))
    holds = list(
        OrderStockHold.objects.select_for_update().filter(
            hold_q,
            product=product,
            location=location,
            status=OrderStockHold.Status.REZERVISANO,
        )
    )
    this_qty = 0
    for hold in holds:
        if hold.narudzba_id == order.pk:
            this_qty += max(0, int(hold.kolicina or 0))
        hold.status = OrderStockHold.Status.OTKAZANO
        hold.save(update_fields=['status'])

    relocated = 0
    if this_qty > 0:
        leftover = reserve_for_order(
            order,
            product,
            this_qty,
            variation=variation,
            user=user,
            napomena=f'Usputni popis prebacivanje #{order.broj}',
        )
        relocated = this_qty - leftover

    refresh_catalog_qty(product)
    _clear_pick_state_for_item(order, item.pk)
    return {
        'cleared': cleared,
        'relocated': relocated,
        'loc': location.sifra or loc,
        'on_site': bool(getattr(product, 'na_stanju', False)),
    }


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


def open_prenos_mp_order():
    """Jedan otvoreni picking za sve prenose u MP, dok se ne validira ili otkaže."""
    from .models import Order

    return (
        Order.objects.select_for_update()
        .filter(ime_prezime='Prenos u MP')
        .exclude(status=Order.Status.OTKAZANA)
        .exclude(
            lager_status__in=[
                Order.LagerStatus.VALIDIRANO,
                Order.LagerStatus.OTKAZANO,
            ]
        )
        .order_by('kreirana', 'pk')
        .first()
    )


@transaction.atomic
def create_prenos_mp_pick(*, product, variation=None, location, qty, user=None):
    """Dodaj stavku na isti Prenos u MP picking. Validate prebacuje zalihu na maloprodaju."""
    from .models import Order, OrderItem

    qty = _parse_move_qty(qty)
    if qty <= 0:
        raise MagacinError('Unesi količinu za prenos u MP.')
    rows, _ = location_rows(product, variation)
    row = next((item for item in rows if item['location'].pk == location.pk), None)
    if row is None or row['dostupno'] < qty:
        raise MagacinError('Nema dovoljno dostupne količine na toj lokaciji.')
    cijena = product.cijena or Decimal('0.00')
    order = open_prenos_mp_order()
    created = order is None
    loc_label = location.label or location.sifra or ''
    if created:
        order = Order.objects.create(
            ime_prezime='Prenos u MP',
            email='prenos@carpologijabh.local',
            telefon='-',
            adresa=loc_label[:300],
            grad='Magacin',
            napomena=f'Prenos u MP sa {loc_label}',
            medjuzbir=cijena * qty,
            dostava=Decimal('0.00'),
            ukupno=cijena * qty,
            status=Order.Status.NOVA,
            izvor=Order.Izvor.MAGACIN,
            pick_state={'kind': 'prenos_mp'},
        )
    else:
        state = dict(order.pick_state) if isinstance(order.pick_state, dict) else {}
        state['kind'] = 'prenos_mp'
        order.pick_state = state
        note = (order.napomena or '').strip()
        extra = f'sa {loc_label}'
        if extra not in note:
            order.napomena = f'{note}; {extra}'.strip('; ')[:300] if note else f'Prenos u MP {extra}'
        order.save(update_fields=['pick_state', 'napomena'])

    existing = OrderItem.objects.filter(
        narudzba=order, artikal=product, **_item_variation_filter(variation),
    ).first()
    if existing:
        existing.kolicina = int(existing.kolicina or 0) + qty
        existing.save(update_fields=['kolicina'])
    else:
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
    if not created:
        recalculate_order_totals(order)
    try:
        from .views_magacin import invalidate_magacin_nav_counts

        invalidate_magacin_nav_counts()
    except Exception:
        pass
    return order


@transaction.atomic
def drop_prenos_mp_item(order, item, *, user=None):
    """Skini jednu stavku s Prenos u MP pickinga. Ako ne ostane nijedna, otkaži picking."""
    if not is_prenos_mp_order(order):
        raise MagacinError('Ova narudžba nije Prenos u MP.')
    if item.narudzba_id != order.pk:
        raise MagacinError('Stavka nije na ovom prenosu.')
    product = item.artikal
    variation = item.varijacija
    if product is not None:
        release_holds_for_product(order, product, variation, user=user)
    item_id = item.pk
    item.delete()
    _clear_pick_state_for_item(order, item_id)
    if not OrderItem.objects.filter(narudzba_id=order.pk).exists():
        cancel_order_stock(order, user=user)
        return True
    recalculate_order_totals(order)
    return False


@transaction.atomic
def trim_prenos_mp_item(order, item, *, user=None):
    """Usputni popis na Prenosu u MP: ostavi preostalu rezervaciju ili skini stavku."""
    if not is_prenos_mp_order(order):
        raise MagacinError('Ova narudžba nije Prenos u MP.')
    if item.narudzba_id != order.pk:
        raise MagacinError('Stavka nije na ovom prenosu.')
    product = item.artikal
    variation = item.varijacija
    remaining = 0
    if product is not None:
        remaining = sum(
            int(hold.kolicina or 0)
            for hold in order.magacin_holds.filter(
                product=product,
                status=OrderStockHold.Status.REZERVISANO,
                **_hold_variation_filter(variation),
            )
        )
    if remaining <= 0:
        return drop_prenos_mp_item(order, item, user=user)
    if int(item.kolicina or 0) != remaining:
        item.kolicina = remaining
        item.save(update_fields=['kolicina'])
        recalculate_order_totals(order)
    return False


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


_PICK_SKIP_LOCS = {
    'mp', 'provjeri u mp', 'rezervni dio', 'prenos u mp',
}


def _pick_location_skipped(label):
    text = (label or '').strip().casefold()
    if not text or text in _PICK_SKIP_LOCS:
        return True
    return is_uncountable_stock_location(name=label, sifra=label, path=label)


def _location_for_pick_label(label):
    text = (label or '').strip()
    if not text or _pick_location_skipped(text):
        return None
    loc = WarehouseLocation.objects.filter(sifra__iexact=text).first()
    if loc is not None:
        return loc
    loc = WarehouseLocation.objects.filter(naziv__iexact=text).first()
    if loc is not None:
        return loc
    return WarehouseLocation.objects.filter(odoo_location_path__iexact=text).first()


def _iter_pick_deduct_rows(order):
    """Pokupljene količine po lokaciji iz pickinga. MP/rezervni se ne skidaju s magacina."""
    state = order.pick_state if isinstance(getattr(order, 'pick_state', None), dict) else {}
    items = {item.pk: item for item in order.stavke.select_related('artikal', 'varijacija')}
    rows = []
    for key, row in state.items():
        if not isinstance(row, dict):
            continue
        try:
            got = max(0, int(row.get('got') or 0))
        except (TypeError, ValueError):
            continue
        if got <= 0:
            continue
        loc_label = (row.get('loc') or '').strip() or _pick_line_loc_from_key(key)
        if _pick_location_skipped(loc_label):
            continue
        location = _location_for_pick_label(loc_label)
        if location is None or is_ignored_stock_location(location):
            continue
        try:
            item_id = int(row.get('item_id') or 0)
        except (TypeError, ValueError):
            item_id = 0
        if not item_id and isinstance(key, str) and ':' in str(key):
            try:
                item_id = int(str(key).split(':', 1)[0])
            except (TypeError, ValueError):
                item_id = 0
        item = items.get(item_id)
        if item is None or not item.artikal_id:
            continue
        rows.append({
            'product': item.artikal,
            'variation': item.varijacija,
            'location': location,
            'qty': got,
        })
    return rows


def _pick_line_loc_from_key(key):
    text = str(key or '')
    if ':' in text:
        return text.split(':', 1)[1].strip()
    return ''


def _stock_row_for_sale(product, variation, location):
    """Red zalihe s koj se skida: varijacija, ili parent ako je varijacija prazna."""
    _, sell_variation = _stock_scope(product, variation)
    stock = get_or_create_stock(
        product=product, variation=sell_variation, location=location,
    )
    if int(stock.kolicina or 0) > 0:
        return stock, sell_variation
    if sell_variation is not None:
        parent = get_or_create_stock(product=product, variation=None, location=location)
        if int(parent.kolicina or 0) > 0:
            return parent, None
    return stock, sell_variation


def _sell_qty_from_location(order, product, variation, location, qty, *, user=None):
    """Skini količinu s lokacije. Prvo rezervacija te narudžbe, pa slobodno stanje."""
    remaining = max(0, _int(qty))
    if remaining <= 0 or product is None or location is None:
        return 0
    _, sell_variation = _stock_scope(product, variation)
    sold = 0
    napomena = f'Validacija #{order.broj}'
    prenos_mp = is_prenos_mp_order(order)
    mp_dest = default_maloprodaja_location(product) if prenos_mp else None
    if prenos_mp and mp_dest is None:
        raise MagacinError('Nema maloprodajne lokacije za prenos u MP.')
    if prenos_mp:
        napomena = f'Prenos u MP #{order.broj}'

    def _apply_take(move_product, move_variation, move_location, take, *, from_reservation):
        kwargs = {
            'product': move_product,
            'variation': move_variation,
            'location': move_location,
            'tip': WarehouseMovement.Tip.PRODAJA,
            'kolicina': take,
            'napomena': napomena,
            'user': user,
            'from_reservation': from_reservation,
        }
        if prenos_mp:
            kwargs['tip'] = WarehouseMovement.Tip.TRANSFER
            kwargs['to_location'] = mp_dest
        apply_movement(**kwargs)

    hold_filter = Q(**_hold_variation_filter(variation))
    if sell_variation != variation:
        hold_filter |= Q(**_hold_variation_filter(sell_variation))
    holds = list(
        order.magacin_holds.filter(
            hold_filter,
            product=product,
            location=location,
            status=OrderStockHold.Status.REZERVISANO,
        ).order_by('-kolicina', '-pk')
    )
    for hold in holds:
        if remaining <= 0:
            break
        stock, move_variation = _stock_row_for_sale(
            hold.product, hold.variation or sell_variation, hold.location,
        )
        take = min(remaining, int(hold.kolicina or 0), int(stock.kolicina or 0))
        if take <= 0:
            continue
        from_reservation = int(stock.rezervisano or 0) >= take
        try:
            _apply_take(
                hold.product, move_variation, hold.location, take,
                from_reservation=from_reservation,
            )
        except MagacinError:
            if prenos_mp:
                raise
            continue
        if take >= int(hold.kolicina or 0):
            hold.status = OrderStockHold.Status.VALIDIRANO
            hold.save(update_fields=['status'])
        else:
            hold.kolicina = int(hold.kolicina or 0) - take
            hold.save(update_fields=['kolicina'])
        remaining -= take
        sold += take
    if remaining > 0:
        stock, move_variation = _stock_row_for_sale(product, variation, location)
        take = min(remaining, int(stock.kolicina or 0))
        if take > 0:
            from_reservation = int(stock.rezervisano or 0) >= take
            try:
                _apply_take(
                    product, move_variation, location, take,
                    from_reservation=from_reservation,
                )
                remaining -= take
                sold += take
            except MagacinError:
                if prenos_mp:
                    raise
    return sold


def _sell_remaining_holds(order, *, user=None):
    from collections import defaultdict

    reserved = defaultdict(int)
    holds = list(order.magacin_holds.filter(status=OrderStockHold.Status.REZERVISANO))
    for hold in holds:
        reserved[(hold.product_id, hold.variation_id)] += int(hold.kolicina or 0)
    items = list(order.stavke.all())
    picked = defaultdict(int)
    for item in items:
        if item.kolicina_pokupljeno is None:
            qty = int(item.kolicina or 0)
        else:
            qty = int(item.kolicina_pokupljeno or 0)
        picked[(item.artikal_id, item.varijacija_id)] += qty
    if items:
        for key, res in reserved.items():
            extra = res - picked.get(key, 0)
            if extra <= 0 or not key[0]:
                continue
            product = Product.objects.filter(pk=key[0]).first()
            if product is None:
                continue
            variation = ProductVariation.objects.filter(pk=key[1]).first() if key[1] else None
            release_holds_for_product(order, product, variation, qty=extra, user=user)
    for hold in list(order.magacin_holds.filter(status=OrderStockHold.Status.REZERVISANO)):
        _sell_qty_from_location(
            order, hold.product, hold.variation, hold.location, hold.kolicina, user=user,
        )


def _release_leftover_holds(order, *, user=None):
    leftover = list(order.magacin_holds.filter(status=OrderStockHold.Status.REZERVISANO))
    for hold in leftover:
        try:
            release_holds_for_product(
                order, hold.product, hold.variation, qty=hold.kolicina, user=user,
                napomena=f'Validacija #{order.broj}',
            )
        except MagacinError:
            hold.status = OrderStockHold.Status.OTKAZANO
            hold.save(update_fields=['status'])


def _stock_key(product, variation=None):
    product_id = getattr(product, 'pk', product)
    variation_id = getattr(variation, 'pk', variation) if variation else None
    return (product_id, variation_id)


def _mp_picked_qty(order, item_id):
    state = order.pick_state if isinstance(getattr(order, 'pick_state', None), dict) else {}
    total = 0
    found = 0
    for key, row in state.items():
        if not isinstance(row, dict):
            continue
        loc_label = (row.get('loc') or '').strip() or _pick_line_loc_from_key(key)
        if not _pick_location_skipped(loc_label):
            continue
        if (loc_label or '').strip().casefold() not in {'mp', 'provjeri u mp'}:
            continue
        try:
            iid = int(row.get('item_id') or 0)
        except (TypeError, ValueError):
            iid = 0
        if not iid and isinstance(key, str) and ':' in str(key):
            try:
                iid = int(str(key).split(':', 1)[0])
            except (TypeError, ValueError):
                iid = 0
        if iid != item_id:
            continue
        try:
            total += max(0, int(row.get('got') or 0))
        except (TypeError, ValueError):
            pass
        try:
            found = max(found, int(row.get('mp_found') or 0))
        except (TypeError, ValueError):
            pass
    return max(total, found)


def _warehouse_qty_still_needed(order, pick_rows):
    needed = defaultdict(int)
    if pick_rows:
        for row in pick_rows:
            needed[_stock_key(row['product'], row['variation'])] += int(row['qty'] or 0)
        return needed
    for item in order.stavke.all():
        if not item.artikal_id:
            continue
        if item.kolicina_pokupljeno is None:
            qty = int(item.kolicina or 0)
        else:
            qty = int(item.kolicina_pokupljeno or 0)
        qty = max(0, qty - _mp_picked_qty(order, item.pk))
        if qty <= 0:
            continue
        needed[_stock_key(item.artikal, item.varijacija)] += qty
    return needed


@transaction.atomic
def validate_order_stock(order, *, user=None):
    """Skini količine s picking lokacija (ručna, VP, webshop). Nikad ne ostavi validirano bez skidanja."""
    if order.lager_status == Order.LagerStatus.VALIDIRANO:
        return
    if order.lager_status == Order.LagerStatus.OTKAZANO:
        raise MagacinError('Otkazana narudžba se ne može validirati.')

    pick_rows = _iter_pick_deduct_rows(order)
    needed = _warehouse_qty_still_needed(order, pick_rows)
    if not any(needed.values()):
        for hold in order.magacin_holds.filter(status=OrderStockHold.Status.REZERVISANO):
            needed[_stock_key(hold.product, hold.variation)] += int(hold.kolicina or 0)
    for row in pick_rows:
        sold = _sell_qty_from_location(
            order, row['product'], row['variation'], row['location'], row['qty'],
            user=user,
        )
        key = _stock_key(row['product'], row['variation'])
        needed[key] = max(0, needed.get(key, 0) - sold)

    for hold in list(order.magacin_holds.filter(status=OrderStockHold.Status.REZERVISANO)):
        key = _stock_key(hold.product, hold.variation)
        still = needed.get(key, 0)
        if still <= 0 and hold.variation_id:
            key = _stock_key(hold.product, None)
            still = needed.get(key, 0)
        if still <= 0:
            continue
        sold = _sell_qty_from_location(
            order, hold.product, hold.variation, hold.location, min(hold.kolicina, still),
            user=user,
        )
        needed[key] = max(0, still - sold)

    for (product_id, variation_id), still in list(needed.items()):
        if still <= 0 or not product_id:
            continue
        product = Product.objects.filter(pk=product_id).first()
        if product is None:
            continue
        variation = ProductVariation.objects.filter(pk=variation_id).first() if variation_id else None
        leftover = deduct_for_order(
            product,
            still,
            variation=variation,
            user=user,
            napomena=f'Validacija #{order.broj}',
        )
        needed[(product_id, variation_id)] = leftover

    _release_leftover_holds(order, user=user)

    order.lager_status = Order.LagerStatus.VALIDIRANO
    update_fields = ['lager_status']
    if order.status != Order.Status.OTKAZANA:
        order.status = Order.Status.ZAVRSENA
        update_fields.append('status')
    order.zapakovana = True
    order.zapakovana_at = timezone.now()
    update_fields.extend(['zapakovana', 'zapakovana_at'])
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
