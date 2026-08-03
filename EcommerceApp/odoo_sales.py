"""
Kreiranje Odoo Sales narudžbe iz web narudžbe (staff dugme „Odoo narudžba”).

Kupac (res.partner): name, street, city, phone (+ email/zip).
Stavke: isti artikli i količine — match po Odoo ID, šifri, pa nazivu.
"""
from __future__ import annotations

import logging
import re
from decimal import Decimal

from django.utils import timezone

from .odoo_client import OdooClient, OdooError, odoo_je_konfigurisan

logger = logging.getLogger(__name__)

# Besplatna dostava od 250 KM (web pravilo); ispod → Brza pošta 11 KM u Odoo SO
ODOO_FREE_SHIPPING_THRESHOLD = Decimal('250.00')
ODOO_DELIVERY_PRICE = Decimal('11.00')
ODOO_DELIVERY_LINE_NAME = 'Brza posta dostava'
ODOO_DELIVERY_PRODUCT_NAMES = (
    'Brza posta dostava',
    'Brza pošta dostava',
    'Brza pošta',
    'Brza posta',
    'Dostava Brza pošta',
    'Dostava Brza posta',
    'Dostava X-express',
    'Dostava X express',
    'Dostava',
)


def _order_goods_amount(order) -> Decimal:
    """Iznos artikala (bez dostave) — prag za besplatnu dostavu."""
    try:
        medjuzbir = Decimal(str(getattr(order, 'medjuzbir', None) or 0))
    except Exception:
        medjuzbir = Decimal('0')
    if medjuzbir > 0:
        return medjuzbir
    # Fallback: ukupno − dostava
    try:
        ukupno = Decimal(str(getattr(order, 'ukupno', None) or 0))
        dostava = Decimal(str(getattr(order, 'dostava', None) or 0))
        return max(Decimal('0'), ukupno - dostava)
    except Exception:
        return Decimal('0')


def _should_add_brza_posta_delivery(order) -> bool:
    """True ako je iznos ispod 250 KM → dodaj dostavu 11 KM u Odoo."""
    try:
        from .models import SiteSettings
        settings = SiteSettings.load()
        threshold = Decimal(str(
            getattr(settings, 'besplatna_dostava_od', None)
            or ODOO_FREE_SHIPPING_THRESHOLD
        ))
    except Exception:
        threshold = ODOO_FREE_SHIPPING_THRESHOLD
    if threshold <= 0:
        threshold = ODOO_FREE_SHIPPING_THRESHOLD
    return _order_goods_amount(order) < threshold


def _delivery_unit_price(order) -> Decimal:
    """Cijena dostave za Odoo liniju (default 11 KM)."""
    try:
        from .models import SiteSettings
        settings = SiteSettings.load()
        price = Decimal(str(
            getattr(settings, 'dostava_cijena', None) or ODOO_DELIVERY_PRICE
        ))
        if price > 0:
            return price.quantize(Decimal('0.01'))
    except Exception:
        pass
    # Ako web naplaćuje dostavu, koristi taj iznos
    try:
        web_dostava = Decimal(str(getattr(order, 'dostava', None) or 0))
        if web_dostava > 0:
            return web_dostava.quantize(Decimal('0.01'))
    except Exception:
        pass
    return ODOO_DELIVERY_PRICE


def _find_brza_posta_delivery_product(client: OdooClient):
    """
    Pronađi Odoo artikal za dostavu.
    Preferira „Brza posta dostava”; fallback npr. „Dostava X-express” (11 KM).
    """
    for name in ODOO_DELIVERY_PRODUCT_NAMES:
        row = client.find_product_by_name(name)
        if row:
            return row
    # Blaga pretraga: dostava / brza / express, preferiraj cijenu ~11
    try:
        rows = client.search_read(
            'product.product',
            [
                ('sale_ok', '=', True),
                '|', '|',
                ('name', 'ilike', 'dostav'),
                ('name', 'ilike', 'brza'),
                ('name', 'ilike', 'express'),
            ],
            ['id', 'name', 'display_name', 'default_code', 'lst_price', 'uom_id'],
            limit=30,
        )
    except OdooError:
        rows = []

    target = float(ODOO_DELIVERY_PRICE)
    scored = []
    for row in rows or []:
        label = f"{row.get('name') or ''} {row.get('display_name') or ''}".casefold()
        # Izbaci random artikle (npr. „Goal Post”)
        if 'dostav' not in label and 'express' not in label and 'brza' not in label:
            continue
        if 'dostav' not in label and 'post' in label and 'brza' not in label:
            continue
        try:
            price = float(row.get('lst_price') or 0)
        except (TypeError, ValueError):
            price = 0
        score = 0
        if 'brza' in label and ('post' in label or 'pošt' in label):
            score += 100
        if 'dostav' in label:
            score += 50
        if 'express' in label:
            score += 30
        if abs(price - target) < 0.05:
            score += 40
        elif abs(price - target) <= 1:
            score += 10
        scored.append((score, row))
    if not scored:
        return None
    scored.sort(key=lambda x: x[0], reverse=True)
    return scored[0][1]


def _maybe_append_delivery_line(client: OdooClient, order, matched_lines: list) -> tuple[list, str | None]:
    """
    Ako je iznos < 250 KM, dodaj liniju „Brza posta dostava” 11 KM.
    Preko 250 KM — ništa.
    Vraća (lines, warning_or_None). Ako dostava treba a artikal ne postoji → greška u warning.
    """
    if not _should_add_brza_posta_delivery(order):
        return matched_lines, None

    product = _find_brza_posta_delivery_product(client)
    if not product:
        return matched_lines, (
            'Iznos je ispod 250 KM, ali u Odoo nije pronađen artikal za dostavu '
            f'(npr. „{ODOO_DELIVERY_LINE_NAME}” ili „Dostava X-express”). '
            'Kreirajte sale_ok artikal i pokušajte ponovo.'
        )

    price = float(_delivery_unit_price(order))
    matched_lines.append({
        'product_id': int(product['id']),
        'quantity': 1,
        'price_unit': price,
        # Na SO liniji uvijek prikaži „Brza posta dostava” (čak i ako je
        # Odoo artikal npr. Dostava X-express)
        'name': ODOO_DELIVERY_LINE_NAME,
        'matched_by': 'delivery_rule',
        'web_item_id': None,
    })
    return matched_lines, None


def _clean_item_name(item) -> str:
    """Naziv za match u Odoo — bez deal/popust napomena dodatih u checkout."""
    artikal = getattr(item, 'artikal', None)
    if artikal and getattr(artikal, 'naziv', None):
        base = (artikal.naziv or '').strip()
    else:
        base = (getattr(item, 'product_naziv', None) or getattr(item, 'naziv', None) or '').strip()

    # Ukloni uobičajene checkout sufikse
    for sep in (' (popust', ' (Deal', ' — Deal', '\n'):
        if sep in base:
            base = base.split(sep, 1)[0].strip()
    base = re.sub(r'\s{2,}', ' ', base).strip()

    var = (getattr(item, 'varijacija_naziv', None) or '').strip()
    # varijacija_naziv može sadržavati deal note
    if var:
        for sep in (' (popust', ' (Deal', ' — Deal'):
            if sep in var:
                var = var.split(sep, 1)[0].strip()
    if var and artikal and var.casefold() not in base.casefold():
        return f'{base} — {var}'
    return base


def _resolve_odoo_product_id(client: OdooClient, item, template_variants: dict) -> tuple[int | None, str]:
    """
    Vrati (product.product id, how_matched).
    Redoslijed: odoo_variant_id → template → šifra → naziv.
    """
    variation = getattr(item, 'varijacija', None)
    if variation and variation.odoo_variant_id:
        return int(variation.odoo_variant_id), 'odoo_variant_id'

    product = getattr(item, 'artikal', None)
    template_id = None
    if variation and variation.odoo_template_id:
        template_id = int(variation.odoo_template_id)
    elif product and product.odoo_template_id:
        template_id = int(product.odoo_template_id)

    if template_id:
        variants = template_variants.get(template_id) or []
        if len(variants) == 1:
            return int(variants[0]['id']), 'odoo_template_id'
        sifra = (getattr(item, 'sifra', None) or '').strip().casefold()
        if sifra:
            for variant in variants:
                code = str(variant.get('default_code') or '').strip().casefold()
                if code and code == sifra:
                    return int(variant['id']), 'odoo_template_id+sifra'
        if variants:
            # Ako ima odoo template, bolje prva varijanta nego ništa
            return int(variants[0]['id']), 'odoo_template_first_variant'

    sifra = (getattr(item, 'sifra', None) or '').strip()
    if sifra:
        row = client.find_product_by_default_code(sifra)
        if row:
            return int(row['id']), 'sifra'

    name = _clean_item_name(item)
    if name:
        row = client.find_product_by_name(name)
        if row:
            return int(row['id']), 'naziv'
        # Probaj samo bazni naziv bez varijacije
        if ' — ' in name:
            row = client.find_product_by_name(name.split(' — ', 1)[0].strip())
            if row:
                return int(row['id']), 'naziv_base'

    return None, ''


def create_odoo_sale_order_for_web_order(order, *, force: bool = False) -> dict:
    """
    Napravi Odoo sale.order iz web Order.

    Vraća dict:
      ok, sale_order_id, sale_order_name, partner_id, partner_created,
      lines_matched, missing, message, existing
    """
    if not odoo_je_konfigurisan():
        return {
            'ok': False,
            'message': 'Odoo nije konfigurisan (ODOO_URL / DB / USERNAME / API_KEY).',
        }

    if not order:
        return {'ok': False, 'message': 'Narudžba nije pronađena.'}

    # Već sinhronizovano lokalno — bez force ne kreira ponovo
    existing_id = getattr(order, 'odoo_sale_order_id', None)
    existing_name = getattr(order, 'odoo_sale_order_name', '') or ''
    if existing_id and not force:
        return {
            'ok': True,
            'existing': True,
            'sale_order_id': int(existing_id),
            'sale_order_name': existing_name or str(existing_id),
            'message': (
                f'Narudžba je već u Odoo Sales kao {existing_name or existing_id}. '
                f'Za ponovni unos potvrdite „Odoo narudžba” ponovo.'
            ),
            'can_force': True,
        }

    client = OdooClient.from_settings()

    # Provjeri da li SO već postoji u Odoo po WEB ref (samo bez force)
    remote = None
    if not force:
        remote = client.find_sale_order_by_web_ref(order.broj)
        if remote:
            _save_odoo_link(order, remote)
            return {
                'ok': True,
                'existing': True,
                'sale_order_id': int(remote['id']),
                'sale_order_name': remote.get('name') or str(remote['id']),
                'message': (
                    f'U Odoo već postoji Sales narudžba {remote.get("name")} '
                    f'(WEB-{order.broj}). Za ponovni unos potvrdite dugme ponovo.'
                ),
                'can_force': True,
            }
    else:
        # Zapamti prethodnu vezu za napomenu na novoj SO
        if not existing_id:
            remote = client.find_sale_order_by_web_ref(order.broj)
            if remote:
                existing_id = int(remote['id'])
                existing_name = remote.get('name') or str(existing_id)

    items = list(order.stavke.select_related('artikal', 'varijacija').all())
    if not items:
        return {'ok': False, 'message': 'Narudžba nema stavki.'}

    # Template → variants cache
    template_ids = set()
    for item in items:
        variation = item.varijacija
        if variation and variation.odoo_template_id:
            template_ids.add(int(variation.odoo_template_id))
        elif item.artikal and item.artikal.odoo_template_id:
            template_ids.add(int(item.artikal.odoo_template_id))
    template_variants = {}
    if template_ids:
        template_variants = client.get_product_ids_for_templates(list(template_ids))

    matched_lines = []
    missing = []
    for item in items:
        product_id, how = _resolve_odoo_product_id(client, item, template_variants)
        display = _clean_item_name(item)
        if not product_id:
            missing.append({
                'naziv': display,
                'sifra': item.sifra or '',
                'kolicina': item.kolicina,
            })
            continue
        try:
            price = float(item.cijena)
        except (TypeError, ValueError):
            price = None
        matched_lines.append({
            'product_id': product_id,
            'quantity': int(item.kolicina or 1),
            'price_unit': price,
            'name': display,
            'matched_by': how,
            'web_item_id': item.pk,
        })

    if not matched_lines:
        names = ', '.join(m['naziv'] for m in missing[:5])
        return {
            'ok': False,
            'missing': missing,
            'message': (
                f'Nijedan artikal nije pronađen u Odoo. '
                f'Provjerite nazive/šifre (npr. {names}).'
            ),
        }

    if missing:
        # Ne kreiraj djelomičnu SO bez potvrde — fail jasno
        names = '; '.join(
            f"{m['naziv']}" + (f" [{m['sifra']}]" if m['sifra'] else '')
            for m in missing[:8]
        )
        return {
            'ok': False,
            'missing': missing,
            'lines_matched': matched_lines,
            'message': (
                f'{len(missing)} artikal(a) nije pronađeno u Odoo: {names}. '
                f'Uskladite nazive ili uvezite artikle, pa pokušajte ponovo.'
            ),
        }

    # Iznos < 250 KM → dodaj Brza posta dostava 11 KM; ≥ 250 → bez dostave
    delivery_added = False
    matched_lines, delivery_error = _maybe_append_delivery_line(
        client, order, matched_lines,
    )
    if delivery_error:
        return {
            'ok': False,
            'message': delivery_error,
            'lines_matched': matched_lines,
        }
    delivery_added = any(
        line.get('matched_by') == 'delivery_rule' for line in matched_lines
    )

    note_parts = [
        f'Web narudžba #{order.broj}',
        f'Email: {order.email}',
    ]
    if force and (existing_id or existing_name):
        note_parts.append(
            f'Ponovni unos iz weba (prethodni Odoo SO: '
            f'{existing_name or existing_id}).'
        )
    if order.napomena:
        note_parts.append(f'Napomena kupca: {order.napomena}')
    goods = _order_goods_amount(order)
    if delivery_added:
        note_parts.append(
            f'Dostava Odoo: Brza posta {_delivery_unit_price(order)} KM '
            f'(iznos artikala {goods} KM < 250 KM)'
        )
    else:
        note_parts.append(
            f'Dostava Odoo: besplatna (iznos artikala {goods} KM ≥ 250 KM)'
            if goods >= ODOO_FREE_SHIPPING_THRESHOLD
            else f'Iznos artikala: {goods} KM'
        )
    if order.dostava and Decimal(str(order.dostava)) > 0:
        note_parts.append(f'Dostava (web): {order.dostava} KM')
    if order.popust and Decimal(str(order.popust)) > 0:
        note_parts.append(f'Popust (web): {order.popust} KM')
    note_parts.append(f'Ukupno web: {order.ukupno} KM')

    partner_id, partner_created = client.find_or_create_customer(
        name=order.ime_prezime,
        street=order.adresa,
        city=order.grad,
        phone=order.telefon,
        email=order.email,
        zip_code=order.postanski_broj or '',
        comment=f'Kupac sa opremazaribolov.ba — web #{order.broj}',
    )

    so = client.create_sale_order(
        partner_id=partner_id,
        lines=matched_lines,
        client_order_ref=f'WEB-{order.broj}',
        origin=f'WEB-{order.broj}',
        note='\n'.join(note_parts),
    )

    _save_odoo_link(order, so)

    logger.info(
        'Odoo sale.order %s kreiran iz web #%s (partner=%s, lines=%s, delivery=%s)',
        so.get('name'),
        order.broj,
        partner_id,
        len(matched_lines),
        delivery_added,
    )

    delivery_msg = (
        f', + Brza posta {_delivery_unit_price(order)} KM'
        if delivery_added
        else ''
    )
    reenter_msg = ' (ponovni unos)' if force else ''
    return {
        'ok': True,
        'existing': False,
        'reentered': bool(force),
        'sale_order_id': int(so['id']),
        'sale_order_name': so.get('name') or str(so['id']),
        'partner_id': partner_id,
        'partner_created': partner_created,
        'lines_matched': matched_lines,
        'missing': [],
        'delivery_added': delivery_added,
        'amount_total': so.get('amount_total'),
        'message': (
            f'Odoo Sales narudžba {so.get("name")} kreirana{reenter_msg} '
            f'({len(matched_lines)} stavki{delivery_msg}'
            f'{", novi kupac" if partner_created else ""}).'
        ),
    }


def _save_odoo_link(order, so_row: dict) -> None:
    """Sačuvaj vezu web Order ↔ Odoo sale.order."""
    if not order or not so_row:
        return
    order.odoo_sale_order_id = int(so_row['id'])
    order.odoo_sale_order_name = (so_row.get('name') or '')[:40]
    order.odoo_sale_synced_at = timezone.now()
    order.save(update_fields=[
        'odoo_sale_order_id',
        'odoo_sale_order_name',
        'odoo_sale_synced_at',
    ])
