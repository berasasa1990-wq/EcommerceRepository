import base64
import logging
import time
from decimal import Decimal, InvalidOperation

from django.core.files.base import ContentFile
from django.db import OperationalError, transaction

from .models import Category, Product, ProductVariation
from .odoo_client import OdooClient, OdooError
from .product_merge import sync_primary_stock
from .utils.images import process_product_image_bytes

logger = logging.getLogger(__name__)

MAX_DB_RETRIES = 6
DB_RETRY_BASE_DELAY = 0.4
IMPORT_CHUNK_STOCK_ONLY = 40
IMPORT_CHUNK_NO_IMAGES = 25
IMPORT_CHUNK_WITH_IMAGES = 6


def _empty_import_stats(*, total=0, position=0):
    return {
        'pregledano': 0,
        'kreirano': 0,
        'azurirano': 0,
        'preskoceno': 0,
        'varijacija_kreirano': 0,
        'varijacija_azurirano': 0,
        'greske': [],
        'total': total,
        'position': position,
        'done': total == 0 or position >= total,
    }


def import_chunk_size(*, load_images, stock_only):
    if stock_only:
        return IMPORT_CHUNK_STOCK_ONLY
    if load_images:
        return IMPORT_CHUNK_WITH_IMAGES
    return IMPORT_CHUNK_NO_IMAGES


def merge_import_stats(target, chunk_stats):
    for key in ('pregledano', 'kreirano', 'azurirano', 'preskoceno', 'varijacija_kreirano', 'varijacija_azurirano'):
        target[key] += chunk_stats.get(key, 0)
    target['greske'].extend(chunk_stats.get('greske', []))
    target['position'] = chunk_stats.get('position', target.get('position', 0))
    target['total'] = chunk_stats.get('total', target.get('total', 0))
    target['done'] = chunk_stats.get('done', False)
    return target


def _decimal(value, default='0'):
    try:
        return Decimal(str(value if value is not None else default))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal(default)


def _int_qty(value):
    try:
        return max(0, int(float(value or 0)))
    except (TypeError, ValueError):
        return 0


def _sifra_zauzeta(sifra, *, product_pk=None, variation_pk=None):
    product_qs = Product.objects.filter(sifra=sifra)
    if product_pk:
        product_qs = product_qs.exclude(pk=product_pk)
    if product_qs.exists():
        return True
    variation_qs = ProductVariation.objects.filter(sifra=sifra)
    if variation_pk:
        variation_qs = variation_qs.exclude(pk=variation_pk)
    return variation_qs.exists()


def _unique_sifra(prefix, odoo_id, *, product_pk=None, variation_pk=None):
    base = f'{prefix}{odoo_id}'
    if not _sifra_zauzeta(base, product_pk=product_pk, variation_pk=variation_pk):
        return base
    counter = 1
    while True:
        candidate = f'{base}-{counter}'
        if not _sifra_zauzeta(candidate, product_pk=product_pk, variation_pk=variation_pk):
            return candidate
        counter += 1


def _process_odoo_image(image_b64, filename):
    if not image_b64:
        return None
    try:
        raw = base64.b64decode(image_b64)
    except (ValueError, TypeError):
        return None
    if not raw:
        return None
    try:
        processed = process_product_image_bytes(raw, filename)
        return {'name': processed.name, 'data': processed.read()}
    except Exception as exc:
        logger.warning('Obrada Odoo slike nije uspjela (%s): %s', filename, exc)
        return {'name': filename, 'data': raw}


def _image_content_file(prepared_image):
    return ContentFile(prepared_image['data'], name=prepared_image['name'])


def _resolve_django_category(odoo_category_id, selected_category):
    if selected_category:
        return selected_category
    return Category.objects.filter(odoo_category_id=int(odoo_category_id)).first()


def _is_database_locked(exc):
    message = str(exc).lower()
    return 'database is locked' in message or 'database table is locked' in message


def _preskoceno_rezultat():
    return {
        'action': 'preskoceno',
        'varijacija_kreirano': 0,
        'varijacija_azurirano': 0,
    }


def _brend_je_zasticen(product, excluded_brand_ids):
    if not excluded_brand_ids or product is None or not product.brend_id:
        return False
    return product.brend_id in excluded_brand_ids


def _run_with_db_retry(callback):
    last_error = None
    for attempt in range(MAX_DB_RETRIES):
        try:
            with transaction.atomic():
                return callback()
        except OperationalError as exc:
            last_error = exc
            if not _is_database_locked(exc) or attempt == MAX_DB_RETRIES - 1:
                raise
            delay = DB_RETRY_BASE_DELAY * (attempt + 1)
            logger.warning('SQLite zaključan, ponovni pokušaj za %.1fs (%s/%s)', delay, attempt + 1, MAX_DB_RETRIES)
            time.sleep(delay)
    raise last_error


def fetch_template_ids_from_odoo(
    odoo_category_id,
    *,
    include_children=True,
    client=None,
):
    client = client or OdooClient.from_settings()
    templates = client.get_products_in_category(
        odoo_category_id,
        include_children=include_children,
    )
    return [template['id'] for template in templates]


def import_products_from_odoo(
    odoo_category_id,
    *,
    django_category=None,
    include_children=True,
    update_existing=True,
    load_images=True,
    stock_only=False,
    excluded_brand_ids=None,
    client=None,
    template_ids=None,
    start=0,
    limit=None,
):
    client = client or OdooClient.from_settings()
    django_category = _resolve_django_category(odoo_category_id, django_category)
    excluded_brand_ids = set(excluded_brand_ids or [])
    if stock_only:
        load_images = False
        update_existing = True

    if template_ids is None:
        template_ids = fetch_template_ids_from_odoo(
            odoo_category_id,
            include_children=include_children,
            client=client,
        )

    total = len(template_ids)
    if limit is None:
        limit = import_chunk_size(load_images=load_images, stock_only=stock_only)
    end = min(start + max(limit, 0), total)
    chunk_ids = template_ids[start:end]

    stats = _empty_import_stats(total=total, position=start)

    if not chunk_ids:
        stats['done'] = True
        stats['position'] = total
        return stats

    templates = client.get_templates_by_ids(chunk_ids)

    for template in templates:
        try:
            template_image = None
            if load_images:
                template_image = client.get_template_image(template['id'])

            result = _import_template_with_retry(
                client,
                template,
                django_category=django_category,
                update_existing=update_existing,
                load_images=load_images,
                stock_only=stock_only,
                excluded_brand_ids=excluded_brand_ids,
                template_image=template_image,
            )
            stats['pregledano'] += 1
            stats[result['action']] += 1
            stats['varijacija_kreirano'] += result['varijacija_kreirano']
            stats['varijacija_azurirano'] += result['varijacija_azurirano']
        except Exception as exc:
            logger.exception('Odoo import greška za template %s', template.get('id'))
            stats['pregledano'] += 1
            stats['greske'].append(f'{template.get("name", "?")}: {exc}')

    stats['position'] = end
    stats['done'] = end >= total
    return stats


def _import_template_with_retry(client, template, **kwargs):
    odoo_template_id = template['id']
    if ProductVariation.objects.filter(odoo_template_id=odoo_template_id).exists():
        return _run_with_db_retry(
            lambda: _commit_merged_variation_import(
                template,
                update_existing=kwargs.get('update_existing', True),
                stock_only=kwargs.get('stock_only', False),
                excluded_brand_ids=kwargs.get('excluded_brand_ids') or set(),
            )
        )

    prepared = _prepare_template_import(client, template, **kwargs)
    return _run_with_db_retry(lambda: _commit_template_import(prepared))


def _commit_merged_variation_import(template, *, update_existing, stock_only=False, excluded_brand_ids=None):
    excluded_brand_ids = excluded_brand_ids or set()
    variation = ProductVariation.objects.select_related('artikal', 'artikal__brend').filter(
        odoo_template_id=template['id'],
    ).first()
    if variation is None:
        raise ValueError('Spojena varijacija nije pronađena.')

    if not update_existing:
        return _preskoceno_rezultat()

    if _brend_je_zasticen(variation.artikal, excluded_brand_ids):
        return _preskoceno_rezultat()

    qty = _int_qty(template.get('qty_available'))
    variation.stanje = qty
    variation.na_stanju = qty > 0
    if stock_only:
        variation.save(update_fields=['stanje', 'na_stanju'])
    else:
        sifra = (template.get('default_code') or '').strip()
        variation.cijena = _decimal(template.get('list_price'))
        if sifra:
            variation.sifra = sifra[:50]
        variation.naziv = (template.get('name') or variation.naziv)[:100]
        variation.save()

    product = variation.artikal
    sync_primary_stock(product)
    product.save(update_fields=['stanje', 'na_stanju'])

    return {
        'action': 'azurirano',
        'varijacija_kreirano': 0,
        'varijacija_azurirano': 1,
    }


def _prepare_template_import(
    client,
    template,
    *,
    django_category,
    update_existing,
    load_images,
    stock_only=False,
    excluded_brand_ids=None,
    template_image,
):
    excluded_brand_ids = excluded_brand_ids or set()
    odoo_template_id = template['id']
    prepared_image = None
    if load_images and template_image:
        prepared_image = _process_odoo_image(
            template_image,
            f'odoo-template-{odoo_template_id}.jpg',
        )

    variant_payloads = []
    variant_ids = template.get('product_variant_ids') or []
    if variant_ids and (stock_only or len(variant_ids) > 1):
        variants = client.get_product_variants(variant_ids, with_images=load_images and not stock_only)
        for variant in variants:
            prepared_variant_image = None
            if load_images:
                image_b64 = variant.get('image_variant_1920')
                if image_b64:
                    prepared_variant_image = _process_odoo_image(
                        image_b64,
                        f'odoo-variant-{variant["id"]}.jpg',
                    )
            variant_payloads.append({
                'variant': variant,
                'image': prepared_variant_image,
            })

    return {
        'template': template,
        'django_category': django_category,
        'update_existing': update_existing,
        'load_images': load_images,
        'stock_only': stock_only,
        'excluded_brand_ids': excluded_brand_ids,
        'prepared_image': prepared_image,
        'variant_payloads': variant_payloads,
    }


def _find_product_for_template(template):
    odoo_template_id = template['id']
    product = Product.objects.select_related('brend').filter(odoo_template_id=odoo_template_id).first()
    if product is None and template.get('default_code'):
        product = Product.objects.select_related('brend').filter(sifra=template['default_code']).first()
    return product


def _commit_stock_only(product, template, variant_payloads):
    if variant_payloads:
        stats = {'kreirano': 0, 'azurirano': 0}
        for payload in variant_payloads:
            variant = payload['variant']
            variation = ProductVariation.objects.filter(
                artikal=product,
                odoo_variant_id=variant['id'],
            ).first()
            if variation is None and variant.get('default_code'):
                variation = ProductVariation.objects.filter(
                    artikal=product,
                    sifra=variant['default_code'],
                ).first()
            if variation is None:
                continue
            qty = _int_qty(variant.get('qty_available'))
            variation.stanje = qty
            variation.na_stanju = qty > 0
            variation.save(update_fields=['stanje', 'na_stanju'])
            stats['azurirano'] += 1
        sync_primary_stock(product)
        product.save(update_fields=['stanje', 'na_stanju'])
        return stats

    qty = _int_qty(template.get('qty_available'))
    product.stanje = qty
    product.na_stanju = qty > 0
    product.save(update_fields=['stanje', 'na_stanju'])
    return {'kreirano': 0, 'azurirano': 0}


def _commit_template_import(prepared):
    template = prepared['template']
    django_category = prepared['django_category']
    update_existing = prepared['update_existing']
    stock_only = prepared.get('stock_only', False)
    excluded_brand_ids = prepared.get('excluded_brand_ids') or set()
    prepared_image = prepared['prepared_image']
    variant_payloads = prepared['variant_payloads']

    product = _find_product_for_template(template)

    if product and _brend_je_zasticen(product, excluded_brand_ids):
        return _preskoceno_rezultat()

    if stock_only:
        if product is None:
            return _preskoceno_rezultat()
        variant_stats = _commit_stock_only(product, template, variant_payloads)
        return {
            'action': 'azurirano',
            'varijacija_kreirano': variant_stats['kreirano'],
            'varijacija_azurirano': variant_stats['azurirano'],
        }

    odoo_template_id = template['id']

    if product and not update_existing:
        return _preskoceno_rezultat()

    created = product is None
    qty = _int_qty(template.get('qty_available'))
    sifra = (template.get('default_code') or '').strip() or _unique_sifra(
        'ODOO-T',
        odoo_template_id,
        product_pk=product.pk if product else None,
    )

    values = {
        'naziv': (template.get('name') or f'Artikal {odoo_template_id}')[:200],
        'sifra': sifra[:50],
        'barkod': (template.get('barcode') or '')[:50],
        'opis': template.get('description_sale') or '',
        'cijena': _decimal(template.get('list_price')),
        'kategorija': django_category,
        'na_stanju': qty > 0,
        'stanje': qty,
        'odoo_template_id': odoo_template_id,
        'aktivan': True,
    }

    if product is None:
        product = Product(**values)
    else:
        if django_category is None:
            values.pop('kategorija', None)
        for key, value in values.items():
            setattr(product, key, value)

    if prepared_image:
        product.slika.save(
            prepared_image['name'],
            _image_content_file(prepared_image),
            save=False,
        )

    product.save()

    variant_stats = _commit_variations(
        product,
        variant_payloads,
        update_existing=update_existing,
    )

    if variant_payloads:
        sync_primary_stock(product)
        product.save(update_fields=['stanje', 'na_stanju'])

    return {
        'action': 'kreirano' if created else 'azurirano',
        'varijacija_kreirano': variant_stats['kreirano'],
        'varijacija_azurirano': variant_stats['azurirano'],
    }


def _commit_variations(product, variant_payloads, *, update_existing):
    stats = {'kreirano': 0, 'azurirano': 0}

    if not variant_payloads:
        product.varijacije.all().delete()
        return stats

    seen_ids = set()

    for payload in variant_payloads:
        variant = payload['variant']
        prepared_image = payload['image']
        odoo_variant_id = variant['id']
        seen_ids.add(odoo_variant_id)

        variation = ProductVariation.objects.filter(odoo_variant_id=odoo_variant_id).first()
        if variation is None and variant.get('default_code'):
            variation = ProductVariation.objects.filter(sifra=variant['default_code']).first()

        if variation and variation.artikal_id != product.pk:
            variation = None

        if variation and not update_existing:
            continue

        created = variation is None
        qty = _int_qty(variant.get('qty_available'))
        sifra = (variant.get('default_code') or '').strip() or _unique_sifra(
            'ODOO-V',
            odoo_variant_id,
            variation_pk=variation.pk if variation else None,
        )
        naziv = (variant.get('display_name') or product.naziv).replace(product.naziv, '').strip(' ,-')
        if not naziv:
            naziv = variant.get('display_name') or product.naziv
        naziv = naziv[:100]

        values = {
            'artikal': product,
            'naziv': naziv,
            'sifra': sifra[:50],
            'cijena': _decimal(variant.get('lst_price') or product.cijena),
            'na_stanju': qty > 0,
            'stanje': qty,
            'odoo_variant_id': odoo_variant_id,
        }

        if variation is None:
            variation = ProductVariation(**values)
        else:
            for key, value in values.items():
                setattr(variation, key, value)

        if prepared_image:
            variation.slika.save(
                prepared_image['name'],
                _image_content_file(prepared_image),
                save=False,
            )

        variation.save()
        if created:
            stats['kreirano'] += 1
        else:
            stats['azurirano'] += 1

    product.varijacije.exclude(odoo_variant_id__in=seen_ids).delete()
    return stats