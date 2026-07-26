import xmlrpc.client
from urllib.parse import urljoin

from django.conf import settings

PRODUCT_BATCH_SIZE = 100
VARIANT_BATCH_SIZE = 80
IMAGE_BATCH_SIZE = 5
ODOO_REQUEST_TIMEOUT = 180


class OdooError(Exception):
    pass


class _TimeoutTransportMixin:
    def __init__(self, timeout=ODOO_REQUEST_TIMEOUT, *args, **kwargs):
        self._timeout = timeout
        super().__init__(*args, **kwargs)

    def make_connection(self, host):
        connection = super().make_connection(host)
        connection.timeout = self._timeout
        return connection


class _TimeoutTransport(_TimeoutTransportMixin, xmlrpc.client.Transport):
    pass


class _TimeoutSafeTransport(_TimeoutTransportMixin, xmlrpc.client.SafeTransport):
    pass


class OdooClient:
    def __init__(self, url=None, db=None, username=None, api_key=None):
        self.url = (url or settings.ODOO_URL).rstrip('/')
        self.db = db or settings.ODOO_DB
        self.username = username or settings.ODOO_USERNAME
        self.api_key = api_key or settings.ODOO_API_KEY
        self._uid = None
        self._common = None
        self._models = None

    @classmethod
    def from_settings(cls):
        if not odoo_je_konfigurisan():
            raise OdooError('Odoo nije konfigurisan. Postavite ODOO_URL, ODOO_DB, ODOO_USERNAME i ODOO_API_KEY u .env.')
        return cls()

    def _proxy(self, path):
        transport_cls = _TimeoutSafeTransport if self.url.startswith('https://') else _TimeoutTransport
        return xmlrpc.client.ServerProxy(
            urljoin(f'{self.url}/', path),
            transport=transport_cls(),
            allow_none=True,
        )

    @property
    def common(self):
        if self._common is None:
            self._common = self._proxy('xmlrpc/2/common')
        return self._common

    @property
    def models(self):
        if self._models is None:
            self._models = self._proxy('xmlrpc/2/object')
        return self._models

    def authenticate(self):
        if self._uid:
            return self._uid
        try:
            uid = self.common.authenticate(self.db, self.username, self.api_key, {})
        except Exception as exc:
            raise OdooError(f'Odoo autentifikacija nije uspjela: {exc}') from exc
        if not uid:
            raise OdooError('Odoo autentifikacija nije uspjela. Provjerite URL, bazu, korisnika i API ključ.')
        self._uid = uid
        return uid

    def execute(self, model, method, *args, **kwargs):
        uid = self.authenticate()
        try:
            return self.models.execute_kw(
                self.db,
                uid,
                self.api_key,
                model,
                method,
                list(args),
                kwargs,
            )
        except xmlrpc.client.Fault as exc:
            raise OdooError(f'Odoo greška ({model}.{method}): {exc.faultString}') from exc
        except Exception as exc:
            raise OdooError(f'Odoo greška ({model}.{method}): {exc}') from exc

    def search_read(self, model, domain, fields, *, limit=None, offset=None, order=None):
        options = {'fields': fields}
        if limit is not None:
            options['limit'] = limit
        if offset is not None:
            options['offset'] = offset
        if order:
            options['order'] = order
        return self.execute(model, 'search_read', domain, **options)

    def search_read_batched(self, model, domain, fields, *, batch_size, order=None):
        results = []
        offset = 0
        while True:
            batch = self.search_read(
                model,
                domain,
                fields,
                limit=batch_size,
                offset=offset,
                order=order,
            )
            if not batch:
                break
            results.extend(batch)
            if len(batch) < batch_size:
                break
            offset += batch_size
        return results

    def list_product_categories(self):
        records = self.search_read(
            'product.category',
            [],
            ['id', 'name', 'complete_name', 'parent_id'],
            order='complete_name asc',
        )
        choices = []
        for record in records:
            label = record.get('complete_name') or record.get('name') or f'Kategorija #{record["id"]}'
            choices.append((str(record['id']), label))
        return choices

    def _product_template_fields(self):
        return [
            'id',
            'name',
            'default_code',
            'list_price',
            'description_sale',
            'barcode',
            'categ_id',
            'product_variant_ids',
            'qty_available',
            'virtual_available',
        ]

    def get_products_in_category(self, category_id, *, include_children=True):
        category_id = int(category_id)
        if include_children:
            domain = [('categ_id', 'child_of', category_id), ('sale_ok', '=', True)]
        else:
            domain = [('categ_id', '=', category_id), ('sale_ok', '=', True)]
        return self.search_read_batched(
            'product.template',
            domain,
            self._product_template_fields(),
            batch_size=PRODUCT_BATCH_SIZE,
            order='name asc',
        )

    def get_templates_by_ids(self, template_ids):
        if not template_ids:
            return []

        records = []
        fields = self._product_template_fields()
        for offset in range(0, len(template_ids), PRODUCT_BATCH_SIZE):
            chunk = template_ids[offset:offset + PRODUCT_BATCH_SIZE]
            records.extend(
                self.search_read('product.template', [('id', 'in', chunk)], fields)
            )

        by_id = {record['id']: record for record in records}
        return [by_id[template_id] for template_id in template_ids if template_id in by_id]

    def get_product_variants(self, variant_ids, *, with_images=False):
        if not variant_ids:
            return []

        fields = [
            'id',
            'display_name',
            'default_code',
            'barcode',
            'lst_price',
            'product_tmpl_id',
            'qty_available',
            'virtual_available',
        ]
        variants = []
        for offset in range(0, len(variant_ids), VARIANT_BATCH_SIZE):
            chunk = variant_ids[offset:offset + VARIANT_BATCH_SIZE]
            variants.extend(
                self.search_read('product.product', [('id', 'in', chunk)], fields)
            )

        if not with_images:
            return variants

        images = self.get_variant_images([variant['id'] for variant in variants])
        for variant in variants:
            image = images.get(variant['id'])
            variant['image_variant_1920'] = image
            variant['image_1920'] = image
        return variants

    def get_template_image(self, template_id):
        records = self.search_read(
            'product.template',
            [('id', '=', int(template_id))],
            ['id', 'image_1920'],
            limit=1,
        )
        if not records:
            return None
        return records[0].get('image_1920')

    def get_template_images(self, template_ids, *, batch_size=IMAGE_BATCH_SIZE):
        if not template_ids:
            return {}

        images = {}
        for offset in range(0, len(template_ids), batch_size):
            chunk = template_ids[offset:offset + batch_size]
            try:
                records = self.search_read(
                    'product.template',
                    [('id', 'in', chunk)],
                    ['id', 'image_1920'],
                )
            except OdooError:
                for template_id in chunk:
                    image = self.get_template_image(template_id)
                    if image:
                        images[int(template_id)] = image
                continue

            for record in records:
                image = record.get('image_1920')
                if image:
                    images[record['id']] = image
        return images

    def get_variant_image(self, variant_id):
        records = self.search_read(
            'product.product',
            [('id', '=', int(variant_id))],
            ['id', 'image_variant_1920'],
            limit=1,
        )
        if not records:
            return None
        record = records[0]
        return record.get('image_variant_1920') or record.get('image_1920')

    def get_variant_images(self, variant_ids, *, batch_size=IMAGE_BATCH_SIZE):
        if not variant_ids:
            return {}

        images = {}
        for offset in range(0, len(variant_ids), batch_size):
            chunk = variant_ids[offset:offset + batch_size]
            try:
                records = self.search_read(
                    'product.product',
                    [('id', 'in', chunk)],
                    ['id', 'image_variant_1920', 'image_1920'],
                )
            except OdooError:
                for variant_id in chunk:
                    image = self.get_variant_image(variant_id)
                    if image:
                        images[int(variant_id)] = image
                continue

            for record in records:
                image = record.get('image_variant_1920') or record.get('image_1920')
                if image:
                    images[record['id']] = image
        return images

    def get_product_ids_for_templates(self, template_ids):
        """Vrati mapu template_id → lista product.product id-eva."""
        if not template_ids:
            return {}
        template_ids = [int(t) for t in template_ids if t]
        if not template_ids:
            return {}

        records = []
        for offset in range(0, len(template_ids), VARIANT_BATCH_SIZE):
            chunk = template_ids[offset:offset + VARIANT_BATCH_SIZE]
            records.extend(
                self.search_read(
                    'product.product',
                    [('product_tmpl_id', 'in', chunk)],
                    ['id', 'product_tmpl_id', 'default_code'],
                )
            )

        by_template = {}
        for record in records:
            tmpl = record.get('product_tmpl_id')
            tmpl_id = tmpl[0] if isinstance(tmpl, (list, tuple)) else tmpl
            if tmpl_id is None:
                continue
            by_template.setdefault(int(tmpl_id), []).append(record)
        return by_template

    def get_internal_stock_quants(self, product_ids, *, for_packing=False):
        """
        stock.quant na internim lokacijama s količinom > 0.
        Vraća mapu product_id → lista {location_name, quantity}.

        for_packing=True: isključi transfer/tranzit lokacije (npr. „Prenos u MP”)
        da se za skladište uzimaju stvarne police (Vrata-2, …).
        """
        if not product_ids:
            return {}
        product_ids = sorted({int(pid) for pid in product_ids if pid})
        if not product_ids:
            return {}

        fields_candidates = [
            ['product_id', 'location_id', 'quantity', 'available_quantity', 'reserved_quantity'],
            ['product_id', 'location_id', 'quantity', 'reserved_quantity'],
            ['product_id', 'location_id', 'quantity'],
        ]
        domain = [
            ('product_id', 'in', product_ids),
            ('location_id.usage', '=', 'internal'),
            ('quantity', '>', 0),
        ]

        records = None
        last_error = None
        for fields in fields_candidates:
            try:
                records = self.search_read(
                    'stock.quant',
                    domain,
                    fields,
                    order='location_id asc',
                )
                break
            except OdooError as exc:
                last_error = exc
                continue
        if records is None:
            raise last_error or OdooError('Nije moguće pročitati stock.quant iz Odoa.')

        by_product = {}
        for record in records:
            product = record.get('product_id')
            product_id = product[0] if isinstance(product, (list, tuple)) else product
            if product_id is None:
                continue

            location = record.get('location_id')
            if isinstance(location, (list, tuple)) and len(location) >= 2:
                location_path = str(location[1] or '').strip()
            else:
                location_path = str(location or '').strip()
            if for_packing and not _is_packing_pick_location(location_path):
                continue
            location_name = _short_location_name(location_path)
            if not location_name:
                continue

            qty = _quant_on_hand(record)
            if qty <= 0:
                continue

            buckets = by_product.setdefault(int(product_id), {})
            buckets[location_name] = buckets.get(location_name, 0) + qty

        result = {}
        for product_id, locations in by_product.items():
            result[product_id] = [
                {'location_name': name, 'quantity': qty}
                for name, qty in sorted(locations.items(), key=lambda item: item[0].casefold())
            ]
        return result


# Lokacije koje nisu police za pakovanje online narudžbi (transfer, kupci, virtualno…)
_PACKING_LOCATION_EXCLUDE_KEYWORDS = (
    'prenos',
    'transfer',
    'transit',
    'output',
    'input',
    'inventory adjustment',
    'scrap',
    'vendor',
    'kupci',
    'customers',
    'partners',
)


def _is_packing_pick_location(location_path):
    """True ako je lokacija pogodna za listu pakovanja (stvarna polica)."""
    path = (location_path or '').strip()
    if not path:
        return False
    text = path.casefold()
    for keyword in _PACKING_LOCATION_EXCLUDE_KEYWORDS:
        if keyword in text:
            return False
    return True


def _short_location_name(name):
    """WH/Stock/A2 → A2; ostavi kratko ime lokacije za pakovanje."""
    name = (name or '').strip()
    if not name:
        return ''
    if '/' in name:
        name = name.rsplit('/', 1)[-1].strip()
    return name


def _quant_on_hand(record):
    """Preferiraj available_quantity, inače quantity − reserved."""
    if not record:
        return 0
    if 'available_quantity' in record and record.get('available_quantity') is not False:
        try:
            return max(0, int(float(record.get('available_quantity') or 0)))
        except (TypeError, ValueError):
            pass
    try:
        qty = float(record.get('quantity') or 0)
    except (TypeError, ValueError):
        qty = 0
    try:
        reserved = float(record.get('reserved_quantity') or 0)
    except (TypeError, ValueError):
        reserved = 0
    return max(0, int(qty - reserved)) if reserved else max(0, int(qty))


def odoo_je_konfigurisan():
    return bool(
        getattr(settings, 'ODOO_URL', '')
        and getattr(settings, 'ODOO_DB', '')
        and getattr(settings, 'ODOO_USERNAME', '')
        and getattr(settings, 'ODOO_API_KEY', '')
    )