"""Lokalni magacin: zalihe po lokacijama, kretanja i Odoo sync."""

from __future__ import annotations

import re
import time
from collections import defaultdict
from datetime import timedelta

from django.db import IntegrityError, transaction
from django.db.models import Q, Sum
from django.utils import timezone

from decimal import Decimal

from .models import (
    BARKOD_MAX_LENGTH,
    SIFRA_MAX_LENGTH,
    Order,
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
)


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


def refresh_catalog_qty(product):
    """Usaglasi Product/Variation.stanje sa zbirom magacinskih lokacija."""
    variations = list(product.varijacije.all())
    if variations:
        product_total = 0
        for variation in variations:
            total = countable_stock_qs(WarehouseStock.objects.filter(
                product=product, variation=variation,
            )).aggregate(s=Sum('kolicina'))['s'] or 0
            total = max(0, _int(total))
            if variation.stanje != total or variation.na_stanju != (total > 0):
                variation.stanje = total
                variation.na_stanju = total > 0
                variation.save(update_fields=['stanje', 'na_stanju'])
            product_total += total
    else:
        product_total = countable_stock_qs(WarehouseStock.objects.filter(
            product=product, variation__isnull=True,
        )).aggregate(s=Sum('kolicina'))['s'] or 0
        product_total = max(0, _int(product_total))

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
        uvoz.log_detalji = stats['details']
        uvoz.save()

    stats['uvoz'] = uvoz
    return uvoz, stats


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
        exact_qs = magacin_products_qs().filter(
            Q(sifra__iexact=q) | Q(barkod__iexact=q) | Q(varijacije__sifra__iexact=q)
        ).distinct()
        exact = list(exact_qs[:2])
        if len(exact) == 1 and not query_looks_like_name(q):
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


def _find_existing_sync_product(template):
    """Nađi artikal isključivo po Odoo ID. Šifra samo ako artikal još nema Odoo ID."""
    from .odoo_import import _find_product_for_template, _link_product_odoo_template, _odoo_id

    odoo_id = _odoo_id(template.get('id'))
    if odoo_id is None:
        return None
    product = Product.objects.filter(odoo_template_id=odoo_id).first()
    if product is not None:
        return product

    found = _find_product_for_template(template)
    if found is None:
        return None
    # Parent već vezan na DRUGI Odoo ID — to nije ovaj artikal, kreiraj novi.
    if found.odoo_template_id and int(found.odoo_template_id) != int(odoo_id):
        return None
    return _link_product_odoo_template(found, odoo_id)


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
                _finish_log(
                    log, started,
                    poruka=(
                        f'Sync završen: Odoo {odoo_n} artikala, Magacin {magacin_odoo}. '
                        f'Novo {job.get("kreirano") or 0}, '
                        f'zaliha {job.get("zaliha") or 0}, '
                        f'lokacija {job.get("lokacija") or 0}.'
                        + (
                            f' Još fali {job.get("nedostaje")} po Odoo ID.'
                            if int(job.get('nedostaje') or 0) > 0
                            else ' Broj je usklađen.'
                        )
                    ),
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
        for vid in leftover:
            tid = v2t.get(vid)
            if tid and tid in by_template:
                mapping[vid] = (by_template[tid], None)
    return mapping


def persist_sync_job(job):
    if not job or not job.get('log_id'):
        return
    WarehouseSyncLog.objects.filter(pk=job['log_id']).update(job_data=job)


def load_running_sync_job():
    log = (
        WarehouseSyncLog.objects.filter(status=WarehouseSyncLog.Status.U_TOKU)
        .exclude(job_data={})
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


def reserve_for_order(order, product, qty, *, variation=None, user=None, napomena=''):
    """Rezerviši slobodnu zalihu za narudžbu. Vraća koliko nije rezervisano."""
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


@transaction.atomic
def validate_order_stock(order, *, user=None):
    """Skini rezervisane količine s lokacija te narudžbe."""
    if order.lager_status == Order.LagerStatus.VALIDIRANO:
        return
    if order.lager_status == Order.LagerStatus.OTKAZANO:
        raise MagacinError('Otkazana narudžba se ne može validirati.')
    holds = list(order.magacin_holds.filter(status=OrderStockHold.Status.REZERVISANO))
    for hold in holds:
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
    order.lager_status = Order.LagerStatus.VALIDIRANO
    order.zapakovana = True
    order.zapakovana_at = timezone.now()
    update_fields = ['lager_status', 'zapakovana', 'zapakovana_at']
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
    log = WarehouseSyncLog.objects.order_by('-started_at').first()
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
