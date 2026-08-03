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

    # Već sinhronizovano lokalno
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
                f'Ponovno kreiranje nije pokrenuto.'
            ),
        }

    client = OdooClient.from_settings()

    # Provjeri da li SO već postoji u Odoo po WEB ref
    remote = client.find_sale_order_by_web_ref(order.broj)
    if remote and not force:
        _save_odoo_link(order, remote)
        return {
            'ok': True,
            'existing': True,
            'sale_order_id': int(remote['id']),
            'sale_order_name': remote.get('name') or str(remote['id']),
            'message': (
                f'U Odoo već postoji Sales narudžba {remote.get("name")} '
                f'(WEB-{order.broj}).'
            ),
        }

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

    note_parts = [
        f'Web narudžba #{order.broj}',
        f'Email: {order.email}',
    ]
    if order.napomena:
        note_parts.append(f'Napomena kupca: {order.napomena}')
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
        'Odoo sale.order %s kreiran iz web #%s (partner=%s, lines=%s)',
        so.get('name'),
        order.broj,
        partner_id,
        len(matched_lines),
    )

    return {
        'ok': True,
        'existing': False,
        'sale_order_id': int(so['id']),
        'sale_order_name': so.get('name') or str(so['id']),
        'partner_id': partner_id,
        'partner_created': partner_created,
        'lines_matched': matched_lines,
        'missing': [],
        'amount_total': so.get('amount_total'),
        'message': (
            f'Odoo Sales narudžba {so.get("name")} kreirana '
            f'({len(matched_lines)} stavki'
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
