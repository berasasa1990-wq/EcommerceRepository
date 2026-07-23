import base64
import logging
import time
from decimal import Decimal, InvalidOperation

from django.db import IntegrityError, OperationalError, transaction

from .models import BARKOD_MAX_LENGTH, SIFRA_MAX_LENGTH, Category, Product, ProductVariation
from .odoo_client import OdooClient, OdooError
from .product_merge import sync_primary_stock
from .utils.images import prepared_product_image_payload, process_product_image_bytes, save_prepared_product_image

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


def import_chunk_size(*, load_images, stock_only, images_only=False, names_only=False):
    if stock_only or names_only:
        return IMPORT_CHUNK_STOCK_ONLY
    if load_images or images_only:
        return IMPORT_CHUNK_WITH_IMAGES
    return IMPORT_CHUNK_NO_IMAGES


def merge_import_stats(target, chunk_stats):
    for key in ('pregledano', 'kreirano', 'azurirano', 'preskoceno', 'varijacija_kreirano', 'varijacija_azurirano'):
        target[key] = target.get(key, 0) + chunk_stats.get(key, 0)
    target.setdefault('greske', []).extend(chunk_stats.get('greske', []))
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
        if value is False or value is None or value == '':
            return 0
        return max(0, int(float(value)))
    except (TypeError, ValueError):
        return 0


def _odoo_qty_from_record(record):
    """
    Pročitaj količinu iz Odoo zapisa.
    Pokušaj qty_available, free_qty, virtual_available (zavisno od verzije / prava).
    """
    if not record:
        return 0
    best = 0
    for key in ('qty_available', 'free_qty', 'virtual_available'):
        if key not in record:
            continue
        raw = record.get(key)
        if raw is False or raw is None:
            continue
        q = _int_qty(raw)
        if q > best:
            best = q
    return best


def _odoo_stock_update_fields(qty, *, existing=False):
    """
    Odoo stanje → sajt:
    - qty > 0: stavi na stanje (stanje + na_stanju=True)
    - qty == 0 i postojeći artikal: NE skidaj sa stanja (ručno ostaje na sajtu)
    - qty == 0 i novi artikal: dozvoli 0 / nije na stanju
    Vraća dict polja za update, ili prazan dict ako ne dirati stanje.
    """
    qty = _int_qty(qty)
    if qty > 0:
        return {'stanje': qty, 'na_stanju': True}
    if not existing:
        return {'stanje': 0, 'na_stanju': False}
    return {}


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


def _normalize_odoo_image(image_value):
    if not image_value or image_value is False:
        return None
    if isinstance(image_value, bytes):
        return image_value
    if isinstance(image_value, str):
        return image_value
    return None


def _variant_image_b64(variant, template_image=None):
    for key in ('image_variant_1920', 'image_1920'):
        normalized = _normalize_odoo_image(variant.get(key))
        if normalized:
            return normalized
    return _normalize_odoo_image(template_image)


def _process_odoo_image(image_b64, filename):
    image_b64 = _normalize_odoo_image(image_b64)
    if not image_b64:
        return None
    try:
        raw = base64.b64decode(image_b64) if isinstance(image_b64, str) else image_b64
    except (ValueError, TypeError):
        return None
    if not raw:
        return None
    try:
        processed = process_product_image_bytes(raw, filename)
        return prepared_product_image_payload(processed)
    except Exception as exc:
        logger.warning('Obrada Odoo slike nije uspjela (%s): %s', filename, exc)
        return {'name': filename, 'data': raw}


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
    images_only=False,
    names_only=False,
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
        images_only = False
        names_only = False
    if images_only:
        load_images = True
        update_existing = True
        stock_only = False
        names_only = False
    if names_only:
        load_images = False
        update_existing = True
        stock_only = False
        images_only = False

    if template_ids is None:
        template_ids = fetch_template_ids_from_odoo(
            odoo_category_id,
            include_children=include_children,
            client=client,
        )

    total = len(template_ids)
    if limit is None:
        limit = import_chunk_size(
            load_images=load_images,
            stock_only=stock_only,
            images_only=images_only,
            names_only=names_only,
        )
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
                images_only=images_only,
                names_only=names_only,
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
    odoo_template_id = _odoo_id(template.get('id'))
    if odoo_template_id is None:
        return _preskoceno_rezultat()
    template['id'] = odoo_template_id

    # Specijalni režimi (samo naziv / slike / stanje) — NIKAD ne kreiraju novi artikal
    if kwargs.get('images_only'):
        prepared = _prepare_template_import(client, template, **kwargs)
        return _run_with_db_retry(lambda: _commit_images_only_import(prepared))

    if kwargs.get('names_only'):
        prepared = _prepare_template_import(client, template, **kwargs)
        return _run_with_db_retry(lambda: _commit_names_only_import(prepared))

    if kwargs.get('stock_only'):
        prepared = _prepare_template_import(client, template, **kwargs)
        return _run_with_db_retry(lambda: _commit_template_import(prepared))

    # Spojene varijacije (odoo_template_id na varijaciji)
    if ProductVariation.objects.filter(odoo_template_id=odoo_template_id).exists():
        return _run_with_db_retry(
            lambda: _commit_merged_variation_import(
                template,
                update_existing=kwargs.get('update_existing', True),
                stock_only=kwargs.get('stock_only', False),
                names_only=kwargs.get('names_only', False),
                excluded_brand_ids=kwargs.get('excluded_brand_ids') or set(),
            )
        )

    prepared = _prepare_template_import(client, template, **kwargs)
    return _run_with_db_retry(lambda: _commit_template_import(prepared))


def _commit_merged_variation_import(
    template,
    *,
    update_existing,
    stock_only=False,
    names_only=False,
    excluded_brand_ids=None,
):
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

    if names_only:
        new_name = _odoo_template_name(template)
        if not new_name:
            return _preskoceno_rezultat()
        changed = False
        var_name = new_name[:100]
        if (variation.naziv or '').strip() != var_name:
            variation.naziv = var_name
            variation.save(update_fields=['naziv'])
            changed = True
        product = variation.artikal
        # Uskladi i naziv parent artikla (spojene varijacije)
        if product and (product.naziv or '').strip() != new_name[:200]:
            product.naziv = new_name[:200]
            update_fields = ['naziv']
            if hasattr(product, 'azuriran'):
                from django.utils import timezone
                product.azuriran = timezone.now()
                update_fields.append('azuriran')
            product.save(update_fields=update_fields)
            changed = True
        return {
            'action': 'azurirano' if changed else 'azurirano',
            'varijacija_kreirano': 0,
            'varijacija_azurirano': 1 if changed else 0,
        }

    qty = _odoo_qty_from_record(template)
    stock_fields = _odoo_stock_update_fields(qty, existing=True)
    if stock_only:
        if not stock_fields:
            return _preskoceno_rezultat()
        for key, value in stock_fields.items():
            setattr(variation, key, value)
        variation.save(update_fields=list(stock_fields.keys()))
    else:
        sifra = (template.get('default_code') or '').strip()
        variation.cijena = _decimal(template.get('list_price'))
        if sifra:
            variation.sifra = sifra[:SIFRA_MAX_LENGTH]
        variation.naziv = (_odoo_template_name(template) or variation.naziv)[:100]
        for key, value in stock_fields.items():
            setattr(variation, key, value)
        variation.save()

    product = variation.artikal
    if stock_fields:
        sync_primary_stock(product)
        if qty > 0:
            product.stanje = max(_int_qty(product.stanje), qty)
            product.na_stanju = True
        product.save(update_fields=['stanje', 'na_stanju'])
    elif qty > 0 and product:
        # Odoo ima zalihu — stavi parent artikal na stanju
        _apply_product_in_stock(product, qty)

    return {
        'action': 'azurirano',
        'varijacija_kreirano': 0,
        'varijacija_azurirano': 1 if stock_fields else 0,
    }


def _prepare_template_import(
    client,
    template,
    *,
    django_category,
    update_existing,
    load_images,
    stock_only=False,
    images_only=False,
    names_only=False,
    excluded_brand_ids=None,
    template_image,
):
    excluded_brand_ids = excluded_brand_ids or set()
    odoo_template_id = template['id']
    normalized_template_image = _normalize_odoo_image(template_image)
    prepared_image = None
    if load_images and normalized_template_image:
        prepared_image = _process_odoo_image(
            normalized_template_image,
            f'odoo-template-{odoo_template_id}.jpg',
        )

    variant_payloads = []
    variant_ids = template.get('product_variant_ids') or []
    needs_variants = bool(
        variant_ids and (
            stock_only
            or images_only
            or names_only
            or len(variant_ids) > 1
            or (load_images and not normalized_template_image)
        )
    )
    if needs_variants:
        variants = client.get_product_variants(
            variant_ids,
            with_images=load_images and not stock_only and not names_only,
        )
        for variant in variants:
            prepared_variant_image = None
            if load_images:
                image_b64 = _variant_image_b64(variant, normalized_template_image)
                if image_b64:
                    prepared_variant_image = _process_odoo_image(
                        image_b64,
                        f'odoo-variant-{variant["id"]}.jpg',
                    )
            variant_payloads.append({
                'variant': variant,
                'image': prepared_variant_image,
            })

    if load_images and not prepared_image:
        for payload in variant_payloads:
            if payload.get('image'):
                prepared_image = payload['image']
                break

    return {
        'template': template,
        'django_category': django_category,
        'update_existing': update_existing,
        'load_images': load_images,
        'stock_only': stock_only,
        'images_only': images_only,
        'names_only': names_only,
        'excluded_brand_ids': excluded_brand_ids,
        'prepared_image': prepared_image,
        'variant_payloads': variant_payloads,
    }


def _commit_images_only_import(prepared):
    template = prepared['template']
    excluded_brand_ids = prepared.get('excluded_brand_ids') or set()
    prepared_image = prepared['prepared_image']
    variant_payloads = prepared['variant_payloads']

    product = _find_product_for_template(template)
    if product is None:
        return _preskoceno_rezultat()
    if _brend_je_zasticen(product, excluded_brand_ids):
        return _preskoceno_rezultat()

    changed = False
    if prepared_image:
        save_prepared_product_image(product.slika, prepared_image)
        changed = True

    product.save()

    variant_stats = {'kreirano': 0, 'azurirano': 0}
    if variant_payloads:
        for payload in variant_payloads:
            if not payload.get('image'):
                continue
            variant = payload['variant']
            variation = ProductVariation.objects.filter(
                artikal=product,
                odoo_variant_id=variant['id'],
            ).first()
            if variation is None:
                continue
            save_prepared_product_image(variation.slika, payload['image'])
            variation.save()
            variant_stats['azurirano'] += 1
            changed = True

    if not changed:
        return _preskoceno_rezultat()

    return {
        'action': 'azurirano',
        'varijacija_kreirano': 0,
        'varijacija_azurirano': variant_stats['azurirano'],
    }


def _variation_display_name(product, variant):
    """Naziv varijacije iz Odoo display_name (bez prefiksa naziva artikla)."""
    display = variant.get('display_name')
    if display is False or display is None:
        display = ''
    display = str(display).strip()
    product_name = (product.naziv or '').strip()
    naziv = display.replace(product_name, '').strip(' ,-') if product_name else display
    if not naziv:
        naziv = display or product_name
    return naziv[:100]


def _odoo_template_name(template):
    """Naziv iz Odoo template (name; Odoo ponekad vrati False)."""
    raw = template.get('name')
    if raw is False or raw is None:
        return ''
    return str(raw).strip()


def _commit_names_only_import(prepared):
    """Ažurira samo naziv artikla / varijacija — forsira naziv iz Odoo-a."""
    template = prepared['template']
    excluded_brand_ids = prepared.get('excluded_brand_ids') or set()
    variant_payloads = prepared['variant_payloads']

    product = _find_product_for_template(template)
    if product is None:
        logger.info(
            'Samo naziv: artikal nije pronađen za Odoo template %s (%s)',
            template.get('id'),
            _odoo_template_name(template) or '?',
        )
        return _preskoceno_rezultat()
    if _brend_je_zasticen(product, excluded_brand_ids):
        return _preskoceno_rezultat()

    changed = False
    new_product_name = _odoo_template_name(template)
    if new_product_name:
        new_product_name = new_product_name[:200]
        old_name = (product.naziv or '').strip()
        if old_name != new_product_name:
            product.naziv = new_product_name
            update_fields = ['naziv']
            if hasattr(product, 'azuriran'):
                # auto_now se ne okine s update_fields osim ako je polje navedeno
                from django.utils import timezone
                product.azuriran = timezone.now()
                update_fields.append('azuriran')
            product.save(update_fields=update_fields)
            changed = True
            logger.info(
                'Samo naziv: product #%s odoo=%s „%s” → „%s”',
                product.pk,
                template.get('id'),
                old_name[:60],
                new_product_name[:60],
            )

    variant_azurirano = 0
    if variant_payloads:
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
            new_var_name = _variation_display_name(product, variant)
            if new_var_name and (variation.naziv or '').strip() != new_var_name:
                variation.naziv = new_var_name
                variation.save(update_fields=['naziv'])
                variant_azurirano += 1
                changed = True

    if not changed:
        # Već usklađeno — tretiraj kao uspješan sync, ne „preskočeno”
        return {
            'action': 'azurirano',
            'varijacija_kreirano': 0,
            'varijacija_azurirano': 0,
        }

    return {
        'action': 'azurirano',
        'varijacija_kreirano': 0,
        'varijacija_azurirano': variant_azurirano,
    }


def _odoo_id(value):
    """Normalizuj Odoo ID u int (XML-RPC ponekad pošalje string)."""
    try:
        if value is False or value is None or value == '':
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _link_product_odoo_template(product, odoo_template_id):
    """Zalijepi odoo_template_id na artikal ako fali (za buduće sync-ove)."""
    if product is None or odoo_template_id is None:
        return product
    if product.odoo_template_id == odoo_template_id:
        return product
    # Ako već ima drugi Odoo ID — ne prepisuj (drugi artikal)
    if product.odoo_template_id and product.odoo_template_id != odoo_template_id:
        return product
    # ID mora biti slobodan (unique)
    if Product.objects.filter(odoo_template_id=odoo_template_id).exclude(pk=product.pk).exists():
        return product
    product.odoo_template_id = odoo_template_id
    try:
        product.save(update_fields=['odoo_template_id'])
    except IntegrityError:
        logger.warning(
            'Nije moguće postaviti odoo_template_id=%s na product #%s',
            odoo_template_id,
            product.pk,
        )
    return product


def _find_product_for_template(template):
    """
    Pronađi postojeći artikal isključivo vezan za Odoo product.template ID.

    Redoslijed (Odoo ID je primarni ključ veze):
    1) Product.odoo_template_id == template.id
    2) ProductVariation.odoo_template_id == template.id → parent artikal
    3) ProductVariation.odoo_variant_id ∈ product_variant_ids → parent
    4) Product.sifra == default_code (samo ako artikal nema drugi odoo ID)
    5) ProductVariation.sifra == default_code → parent (isto pravilo)
    """
    odoo_template_id = _odoo_id(template.get('id'))
    if odoo_template_id is None:
        return None

    # 1) Direktno po Odoo template ID — glavni izvor istine
    product = (
        Product.objects.select_related('brend')
        .filter(odoo_template_id=odoo_template_id)
        .first()
    )
    if product is not None:
        return product

    # 2) Spojena varijacija nosi odoo_template_id
    variation = (
        ProductVariation.objects.select_related('artikal', 'artikal__brend')
        .filter(odoo_template_id=odoo_template_id)
        .first()
    )
    if variation is not None and variation.artikal_id:
        return _link_product_odoo_template(variation.artikal, odoo_template_id)

    # 3) Po Odoo variant ID-ovima template-a
    variant_ids = template.get('product_variant_ids') or []
    clean_variant_ids = [vid for vid in (_odoo_id(v) for v in variant_ids) if vid]
    if clean_variant_ids:
        variation = (
            ProductVariation.objects.select_related('artikal', 'artikal__brend')
            .filter(odoo_variant_id__in=clean_variant_ids)
            .first()
        )
        if variation is not None and variation.artikal_id:
            return _link_product_odoo_template(variation.artikal, odoo_template_id)

    # 4) Fallback: šifra — samo ako artikal nije već vezan na drugi Odoo ID
    code = template.get('default_code')
    if code is False or code is None:
        code = ''
    code = str(code).strip()
    if code:
        product = (
            Product.objects.select_related('brend')
            .filter(sifra=code)
            .first()
        )
        if product is not None:
            if product.odoo_template_id in (None, odoo_template_id):
                return _link_product_odoo_template(product, odoo_template_id)
            # Šifra pripada drugom Odoo artiklu — ne diraj
            logger.info(
                'Šifra %s već na product #%s (odoo_template_id=%s), traženi odoo=%s',
                code,
                product.pk,
                product.odoo_template_id,
                odoo_template_id,
            )

        variation = (
            ProductVariation.objects.select_related('artikal', 'artikal__brend')
            .filter(sifra=code)
            .first()
        )
        if variation is not None and variation.artikal_id:
            parent = variation.artikal
            if parent.odoo_template_id in (None, odoo_template_id):
                return _link_product_odoo_template(parent, odoo_template_id)

    return None


def _apply_product_in_stock(product, qty):
    """Forsiraj artikal na stanju kad Odoo ima količinu > 0."""
    qty = _int_qty(qty)
    if qty <= 0:
        return False
    product.stanje = qty
    product.na_stanju = True
    product.save(update_fields=['stanje', 'na_stanju'])
    logger.info(
        'Odoo stock: product #%s (odoo=%s) → stanje=%s, na_stanju=True',
        product.pk,
        product.odoo_template_id,
        qty,
    )
    return True


def _commit_stock_only(product, template, variant_payloads):
    """
    Samo količine: Odoo > 0 → na stanju na sajtu; Odoo 0 → ne diraj sajt.

    Bitno: većina Odoo artikala ima product_variant_ids, ali na sajtu često
    NEMA ProductVariation redova. U tom slučaju količinu uzimamo s template-a
    (ili zbroj variant qty) i pišemo direktno na Product.
    """
    stats = {'kreirano': 0, 'azurirano': 0}
    template_qty = _odoo_qty_from_record(template)
    variant_qty_sum = 0
    variations_updated = 0

    if variant_payloads:
        for payload in variant_payloads:
            variant = payload['variant']
            qty = _odoo_qty_from_record(variant)
            variant_qty_sum += qty

            odoo_vid = _odoo_id(variant.get('id'))
            variation = None
            if odoo_vid:
                variation = ProductVariation.objects.filter(
                    artikal=product,
                    odoo_variant_id=odoo_vid,
                ).first()
            if variation is None:
                code = variant.get('default_code')
                if code not in (False, None, ''):
                    code = str(code).strip()
                    variation = ProductVariation.objects.filter(
                        artikal=product,
                        sifra=code,
                    ).first()
            if variation is None:
                continue

            stock_fields = _odoo_stock_update_fields(qty, existing=True)
            if not stock_fields:
                continue
            for key, value in stock_fields.items():
                setattr(variation, key, value)
            variation.save(update_fields=list(stock_fields.keys()))
            variations_updated += 1
            stats['azurirano'] += 1

    effective_qty = max(template_qty, variant_qty_sum)

    site_was_on_stock = bool(product.na_stanju)
    site_stanje = _int_qty(product.stanje)

    if variations_updated:
        sync_primary_stock(product)
        # Ako sync ostavi 0 a Odoo ima zalihu — forsira na stanju
        if effective_qty > 0:
            product.stanje = max(_int_qty(product.stanje), effective_qty)
            product.na_stanju = True
        elif site_was_on_stock:
            # Odoo 0 — ne skidaj sa stanja na sajtu
            product.na_stanju = True
            product.stanje = max(_int_qty(product.stanje), site_stanje)
        product.save(update_fields=['stanje', 'na_stanju'])
        return stats

    # Nema varijacija na sajtu (ili nijedna nije matchana) — piši na Product
    if effective_qty > 0:
        if _apply_product_in_stock(product, effective_qty):
            stats['azurirano'] = 1
    # effective_qty == 0 → ne diraj postojeće stanje na sajtu
    return stats


def _commit_template_import(prepared):
    template = prepared['template']
    django_category = prepared['django_category']
    update_existing = prepared['update_existing']
    stock_only = prepared.get('stock_only', False)
    names_only = prepared.get('names_only', False)
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

    if names_only:
        if product is None:
            return _preskoceno_rezultat()
        return _commit_names_only_import(prepared)

    odoo_template_id = _odoo_id(template.get('id'))
    if odoo_template_id is None:
        return _preskoceno_rezultat()

    # Još jednom — Odoo ID je obavezan match prije kreiranja
    if product is None:
        product = _find_product_for_template(template)

    if product and not update_existing:
        return _preskoceno_rezultat()

    created = product is None
    # Sačuvaj stanje/opis sa sajta prije bilo kakvog overwrite-a
    site_was_on_stock = bool(product.na_stanju) if product else False
    site_stanje = _int_qty(product.stanje) if product else 0
    site_opis = (product.opis or '') if product else ''

    raw_code = template.get('default_code')
    if raw_code is False or raw_code is None:
        raw_code = ''
    sifra = str(raw_code).strip() or _unique_sifra(
        'ODOO-T',
        odoo_template_id,
        product_pk=product.pk if product else None,
    )
    # Ako šifra pripada drugom artiklu, generiši jedinstvenu (ne diraj tuđi)
    if product is None and _sifra_zauzeta(sifra):
        sifra = _unique_sifra('ODOO-T', odoo_template_id)
    elif product is not None and sifra and _sifra_zauzeta(sifra, product_pk=product.pk):
        # zadrži postojeću šifru artikla umjesto sudara
        sifra = product.sifra or _unique_sifra('ODOO-T', odoo_template_id, product_pk=product.pk)

    values = {
        'naziv': (_odoo_template_name(template) or f'Artikal {odoo_template_id}')[:200],
        'sifra': (sifra or '')[:SIFRA_MAX_LENGTH] or None,
        'barkod': (str(template.get('barcode') or '') if template.get('barcode') not in (False, None) else '')[:BARKOD_MAX_LENGTH],
        'cijena': _decimal(template.get('list_price')),
        'kategorija': django_category,
        'odoo_template_id': odoo_template_id,
        'aktivan': True,
    }
    # Opis: samo za NOVE artikle. Postojeći zadržavaju opis sa sajta (u Odoo-u često prazan).
    if created:
        odoo_opis = template.get('description_sale')
        if odoo_opis is False or odoo_opis is None:
            odoo_opis = ''
        values['opis'] = str(odoo_opis)

    # Stanje: Odoo > 0 → na stanju; Odoo 0 na postojećem → NE skidaj sa stanja
    template_qty = _odoo_qty_from_record(template)
    variant_qty_sum = sum(
        _odoo_qty_from_record(p.get('variant') or {})
        for p in (variant_payloads or [])
    )
    effective_qty = max(template_qty, variant_qty_sum)
    values.update(
        _odoo_stock_update_fields(
            effective_qty,
            existing=not created,
        )
    )

    if product is None:
        product = Product(**values)
    else:
        if django_category is None:
            values.pop('kategorija', None)
        for key, value in values.items():
            setattr(product, key, value)
        # Nikad ne diraj postojeći opis
        product.opis = site_opis

    if prepared_image:
        save_prepared_product_image(product.slika, prepared_image)

    # Savepoint: IntegrityError ne smije pokvariti vanjski atomic (retry petlja)
    try:
        with transaction.atomic():
            product.save()
    except IntegrityError:
        existing = (
            Product.objects.filter(odoo_template_id=odoo_template_id).first()
            or (Product.objects.filter(sifra=sifra).first() if sifra else None)
        )
        if existing is None:
            raise
        logger.warning(
            'IntegrityError pri save product odoo=%s — ažuriram postojeći #%s (ne kreiram duplikat)',
            odoo_template_id,
            existing.pk,
        )
        product = existing
        created = False
        site_was_on_stock = bool(product.na_stanju)
        site_stanje = _int_qty(product.stanje)
        site_opis = product.opis or ''
        merge_values = dict(values)
        merge_values.pop('opis', None)  # ne diraj opis
        if django_category is None:
            merge_values.pop('kategorija', None)
        for key, value in merge_values.items():
            setattr(product, key, value)
        product.opis = site_opis
        if prepared_image:
            save_prepared_product_image(product.slika, prepared_image)
        product.save()

    variant_stats = _commit_variations(
        product,
        variant_payloads,
        update_existing=update_existing,
    )

    if variant_payloads:
        sync_primary_stock(product)
        product.save(update_fields=['stanje', 'na_stanju'])

    # Odoo > 0 → na stanju; Odoo 0 → vrati/ostavi stanje sa sajta (ne skidaj)
    if effective_qty > 0:
        if not product.na_stanju or _int_qty(product.stanje) < effective_qty:
            product.stanje = max(_int_qty(product.stanje), effective_qty)
            product.na_stanju = True
            product.save(update_fields=['stanje', 'na_stanju'])
    elif not created and site_was_on_stock:
        # Odoo nema zalihu — ne skidaj artikal sa stanja na sajtu
        if not product.na_stanju or _int_qty(product.stanje) < site_stanje:
            product.na_stanju = True
            product.stanje = max(_int_qty(product.stanje), site_stanje)
            product.save(update_fields=['stanje', 'na_stanju'])
            logger.info(
                'Odoo stock 0: product #%s ostaje na stanju (sajt stanje=%s)',
                product.pk,
                product.stanje,
            )

    # Još jednom: postojeći opis se ne dira
    if not created and (product.opis or '') != site_opis:
        product.opis = site_opis
        product.save(update_fields=['opis'])

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
        odoo_variant_id = _odoo_id(variant.get('id'))
        if odoo_variant_id is None:
            continue
        seen_ids.add(odoo_variant_id)

        variation = ProductVariation.objects.filter(odoo_variant_id=odoo_variant_id).first()
        if variation is None:
            # Varijacija s odoo_template_id ovog template-a
            variation = ProductVariation.objects.filter(
                artikal=product,
                odoo_template_id=product.odoo_template_id,
            ).first() if product.odoo_template_id else None
        if variation is None and variant.get('default_code'):
            code = str(variant.get('default_code') or '').strip()
            variation = ProductVariation.objects.filter(artikal=product, sifra=code).first()
            if variation is None:
                variation = ProductVariation.objects.filter(sifra=code).first()

        if variation and variation.artikal_id != product.pk:
            # Varijacija pripada drugom artiklu — ne “otmi” je; traži na ovom artiklu
            variation = ProductVariation.objects.filter(
                artikal=product,
                odoo_variant_id=odoo_variant_id,
            ).first()

        if variation and not update_existing:
            continue

        created = variation is None
        raw_vcode = variant.get('default_code')
        if raw_vcode is False or raw_vcode is None:
            raw_vcode = ''
        sifra = str(raw_vcode).strip() or _unique_sifra(
            'ODOO-V',
            odoo_variant_id,
            variation_pk=variation.pk if variation else None,
        )
        naziv = _variation_display_name(product, variant)
        if not naziv:
            naziv = (product.naziv or '')[:100]

        values = {
            'artikal': product,
            'naziv': naziv,
            'sifra': sifra[:SIFRA_MAX_LENGTH],
            'cijena': _decimal(variant.get('lst_price') or product.cijena),
            'odoo_variant_id': odoo_variant_id,
        }
        values.update(
            _odoo_stock_update_fields(
                _odoo_qty_from_record(variant),
                existing=not created,
            )
        )

        if variation is None:
            variation = ProductVariation(**values)
        else:
            for key, value in values.items():
                setattr(variation, key, value)

        if prepared_image:
            save_prepared_product_image(variation.slika, prepared_image)

        variation.save()
        if created:
            stats['kreirano'] += 1
        else:
            stats['azurirano'] += 1

    product.varijacije.exclude(odoo_variant_id__in=seen_ids).delete()
    return stats