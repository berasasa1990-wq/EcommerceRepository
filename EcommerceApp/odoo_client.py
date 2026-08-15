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

    def get_all_sale_template_ids(self):
        """Svi product.template koji se mogu prodavati (cijeli Odoo katalog)."""
        return self.get_sale_template_ids()

    def get_sale_template_ids_page(self, *, offset=0, limit=250):
        """Jedna stranica aktivnih product.template ID-jeva (isti skup kao Odoo API)."""
        uid = self.authenticate()
        try:
            ids = self.models.execute_kw(
                self.db,
                uid,
                self.api_key,
                'product.template',
                'search',
                [[]],
                {
                    'offset': max(0, int(offset)),
                    'limit': max(1, int(limit)),
                    'order': 'id asc',
                },
            )
        except xmlrpc.client.Fault as exc:
            raise OdooError(f'Odoo greška (product.template.search): {exc.faultString}') from exc
        except Exception as exc:
            raise OdooError(f'Odoo greška (product.template.search): {exc}') from exc
        return [int(tid) for tid in (ids or []) if tid]

    def get_sale_template_ids(self, *, since=None):
        domain = [('sale_ok', '=', True)]
        if since is not None:
            domain.append(('write_date', '>=', _odoo_datetime(since)))
        records = self.search_read_batched(
            'product.template',
            domain,
            ['id'],
            batch_size=PRODUCT_BATCH_SIZE,
            order='id asc',
        )
        return [int(record['id']) for record in records if record.get('id')]

    def get_quant_product_ids_changed_since(self, since):
        """product.product ID-jevi čije su interne zalihe mijenjane od `since`."""
        if since is None:
            return []
        records = self.search_read_batched(
            'stock.quant',
            [
                ('location_id.usage', '=', 'internal'),
                ('write_date', '>=', _odoo_datetime(since)),
            ],
            ['product_id'],
            batch_size=PRODUCT_BATCH_SIZE,
            order='id asc',
        )
        ids = []
        seen = set()
        for record in records:
            product = record.get('product_id')
            pid = product[0] if isinstance(product, (list, tuple)) else product
            if not pid:
                continue
            pid = int(pid)
            if pid in seen:
                continue
            seen.add(pid)
            ids.append(pid)
        return ids

    def get_internal_locations(self):
        """Interna skladišta / police iz Odoa."""
        return self.search_read(
            'stock.location',
            [('usage', '=', 'internal')],
            ['id', 'name', 'complete_name'],
            order='complete_name asc',
        )

    def get_variant_ids_for_templates(self, template_ids):
        """product.product ID → product.template ID za zadate template ID-jeve."""
        template_ids = sorted({int(tid) for tid in (template_ids or []) if tid})
        if not template_ids:
            return {}
        mapping = {}
        for offset in range(0, len(template_ids), PRODUCT_BATCH_SIZE):
            chunk = template_ids[offset:offset + PRODUCT_BATCH_SIZE]
            rows = self.search_read(
                'product.product',
                [('product_tmpl_id', 'in', chunk)],
                ['id', 'product_tmpl_id'],
            )
            for row in rows or []:
                vid = row.get('id')
                tmpl = row.get('product_tmpl_id')
                if not vid:
                    continue
                tid = tmpl[0] if isinstance(tmpl, (list, tuple)) else tmpl
                if not tid:
                    continue
                mapping[int(vid)] = int(tid)
        return mapping

    def get_template_ids_for_variants(self, variant_ids):
        """product.product ID → product.template ID."""
        variant_ids = sorted({int(vid) for vid in (variant_ids or []) if vid})
        if not variant_ids:
            return {}
        mapping = {}
        for offset in range(0, len(variant_ids), VARIANT_BATCH_SIZE):
            chunk = variant_ids[offset:offset + VARIANT_BATCH_SIZE]
            rows = self.search_read(
                'product.product',
                [('id', 'in', chunk)],
                ['id', 'product_tmpl_id'],
            )
            for row in rows or []:
                vid = row.get('id')
                tmpl = row.get('product_tmpl_id')
                if not vid:
                    continue
                tid = tmpl[0] if isinstance(tmpl, (list, tuple)) else tmpl
                if not tid:
                    continue
                mapping[int(vid)] = int(tid)
        return mapping

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
        uid = self.authenticate()
        for offset in range(0, len(template_ids), PRODUCT_BATCH_SIZE):
            chunk = template_ids[offset:offset + PRODUCT_BATCH_SIZE]
            try:
                rows = self.models.execute_kw(
                    self.db,
                    uid,
                    self.api_key,
                    'product.template',
                    'search_read',
                    [[('id', 'in', chunk)]],
                    {
                        'fields': fields,
                        'context': {'active_test': False},
                    },
                )
            except xmlrpc.client.Fault as exc:
                raise OdooError(f'Odoo greška (product.template.search_read): {exc.faultString}') from exc
            except Exception as exc:
                raise OdooError(f'Odoo greška (product.template.search_read): {exc}') from exc
            records.extend(rows or [])

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
                    ['id', 'product_tmpl_id', 'default_code', 'name', 'display_name'],
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

    def find_product_by_default_code(self, code):
        """product.product po default_code (šifra)."""
        code = (code or '').strip()
        if not code:
            return None
        rows = self.search_read(
            'product.product',
            [('default_code', '=', code), ('sale_ok', '=', True)],
            ['id', 'name', 'display_name', 'default_code', 'lst_price', 'uom_id'],
            limit=5,
        )
        if not rows:
            rows = self.search_read(
                'product.product',
                [('default_code', 'ilike', code), ('sale_ok', '=', True)],
                ['id', 'name', 'display_name', 'default_code', 'lst_price', 'uom_id'],
                limit=5,
            )
        return rows[0] if rows else None

    def find_product_by_name(self, name):
        """
        product.product po tačnom ili bliskom nazivu.
        Isti nazivi artikala web ↔ Odoo.
        """
        name = (name or '').strip()
        if not name:
            return None
        fields = ['id', 'name', 'display_name', 'default_code', 'lst_price', 'uom_id']
        # Tačan name
        rows = self.search_read(
            'product.product',
            [('name', '=', name), ('sale_ok', '=', True)],
            fields,
            limit=5,
        )
        if rows:
            return rows[0]
        # Tačan display_name
        rows = self.search_read(
            'product.product',
            [('display_name', '=', name), ('sale_ok', '=', True)],
            fields,
            limit=5,
        )
        if rows:
            return rows[0]
        # product.template name → prva varijanta
        templates = self.search_read(
            'product.template',
            [('name', '=', name), ('sale_ok', '=', True)],
            ['id', 'name', 'product_variant_id'],
            limit=3,
        )
        if templates:
            tmpl = templates[0]
            variant = tmpl.get('product_variant_id')
            if isinstance(variant, (list, tuple)) and variant:
                vid = int(variant[0])
                vrows = self.search_read(
                    'product.product',
                    [('id', '=', vid)],
                    fields,
                    limit=1,
                )
                if vrows:
                    return vrows[0]
            variants = self.get_product_ids_for_templates([int(tmpl['id'])]).get(int(tmpl['id'])) or []
            if variants:
                return variants[0]
        # Blago: ilike name
        rows = self.search_read(
            'product.product',
            [('name', 'ilike', name), ('sale_ok', '=', True)],
            fields,
            limit=8,
        )
        if not rows:
            return None
        name_cf = name.casefold()
        for row in rows:
            for key in ('name', 'display_name'):
                val = (row.get(key) or '').strip().casefold()
                if val == name_cf:
                    return row
        # Ako je jedan rezultat — prihvati
        if len(rows) == 1:
            return rows[0]
        return rows[0]

    def find_or_create_customer(
        self,
        *,
        name,
        street='',
        city='',
        phone='',
        email='',
        zip_code='',
        comment='',
    ):
        """
        Uvijek kreira novog kupca s tačnim podacima iz narudžbe.

        Ne spaja po telefonu — isti broj smije postojati na više kupaca.
        street = adresa za slanje, street2 = telefon (Odoo Adresa 2).
        """
        vals = OdooClient._partner_vals_from_order(
            name=name,
            street=street,
            city=city,
            phone=phone,
            email=email,
            zip_code=zip_code,
            comment=comment,
        )
        try:
            partner_id = self.execute('res.partner', 'create', vals)
        except OdooError:
            # Ako Odoo odbije zbog ponovljenog telefona, ostavi telefon u Adresa 2.
            retry = dict(vals)
            retry.pop('phone', None)
            retry.pop('mobile', None)
            partner_id = self.execute('res.partner', 'create', retry)
        if isinstance(partner_id, list):
            partner_id = partner_id[0] if partner_id else None
        if not partner_id:
            raise OdooError('res.partner create nije vratio id.')
        return int(partner_id), True

    @staticmethod
    def _partner_vals_from_order(
        *,
        name,
        street='',
        city='',
        phone='',
        email='',
        zip_code='',
        comment='',
    ):
        name = (name or '').strip() or 'Kupac web'
        street = (street or '').strip()
        city = (city or '').strip()
        phone = (phone or '').strip()
        email = (email or '').strip().lower()
        zip_code = (zip_code or '').strip()
        comment = (comment or '').strip()
        dummy_emails = {'rucna@opremazaribolov.ba'}
        if email in dummy_emails:
            email = ''

        vals = {
            'name': name[:200],
            'street': street[:250],
            'city': city[:100],
            'customer_rank': 1,
        }
        if phone:
            vals['street2'] = phone[:250]
            vals['phone'] = phone[:64]
            vals['mobile'] = phone[:64]
        if email:
            vals['email'] = email[:120]
        if zip_code:
            vals['zip'] = zip_code[:24]
        if comment:
            vals['comment'] = comment[:2000]
        return vals

    def find_sale_order_by_web_ref(self, web_broj):
        """Već postoji SO sa origin/client_order_ref = WEB-{broj}."""
        ref = f'WEB-{web_broj}'
        rows = self.search_read(
            'sale.order',
            ['|', ('client_order_ref', '=', ref), ('origin', '=', ref)],
            ['id', 'name', 'state', 'partner_id', 'amount_total'],
            limit=3,
            order='id desc',
        )
        return rows[0] if rows else None

    def create_sale_order(self, *, partner_id, lines, client_order_ref='', origin='', note=''):
        """
        Kreiraj draft sale.order.
        lines: [{product_id, quantity, price_unit?, name?}, ...]
        """
        if not partner_id:
            raise OdooError('partner_id je obavezan.')
        if not lines:
            raise OdooError('sale.order mora imati barem jednu stavku.')

        order_lines = []
        for line in lines:
            product_id = int(line['product_id'])
            qty = float(line.get('quantity') or line.get('product_uom_qty') or 1)
            if qty <= 0:
                continue
            line_vals = {
                'product_id': product_id,
                'product_uom_qty': qty,
            }
            if line.get('price_unit') is not None:
                try:
                    line_vals['price_unit'] = float(line['price_unit'])
                except (TypeError, ValueError):
                    pass
            if line.get('name'):
                line_vals['name'] = str(line['name'])[:500]
            order_lines.append((0, 0, line_vals))

        if not order_lines:
            raise OdooError('Nema validnih stavki za sale.order.')

        vals = {
            'partner_id': int(partner_id),
            'partner_invoice_id': int(partner_id),
            'partner_shipping_id': int(partner_id),
            'order_line': order_lines,
        }
        if client_order_ref:
            vals['client_order_ref'] = str(client_order_ref)[:64]
        if origin:
            vals['origin'] = str(origin)[:64]
        if note:
            vals['note'] = str(note)[:4000]

        order_id = self.execute('sale.order', 'create', vals)
        if isinstance(order_id, list):
            order_id = order_id[0] if order_id else None
        if not order_id:
            raise OdooError('sale.order create nije vratio id.')

        rows = self.search_read(
            'sale.order',
            [('id', '=', int(order_id))],
            ['id', 'name', 'state', 'amount_total', 'partner_id'],
            limit=1,
        )
        if not rows:
            return {'id': int(order_id), 'name': str(order_id), 'state': 'draft'}
        return rows[0]

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
                location_id = int(location[0])
                location_path = str(location[1] or '').strip()
            elif location:
                location_id = int(location)
                location_path = str(location)
            else:
                continue
            if for_packing and not _is_packing_pick_location(location_path):
                continue
            location_name = _short_location_name(location_path) or location_path
            if not location_name:
                continue

            qty = _quant_on_hand(record)
            on_hand = _quant_raw_quantity(record)
            reserved = _quant_reserved(record)
            if qty <= 0 and on_hand <= 0:
                continue

            buckets = by_product.setdefault(int(product_id), {})
            if location_id in buckets:
                buckets[location_id]['quantity'] += qty
                buckets[location_id]['on_hand'] += on_hand
                buckets[location_id]['reserved_quantity'] += reserved
            else:
                buckets[location_id] = {
                    'location_id': location_id,
                    'location_name': location_name,
                    'location_path': location_path,
                    'quantity': qty,
                    'on_hand': on_hand,
                    'reserved_quantity': reserved,
                }

        result = {}
        for product_id, locations in by_product.items():
            result[product_id] = sorted(
                locations.values(),
                key=lambda item: (item.get('location_name') or '').casefold(),
            )
        return result

    def deduct_stock_picks(self, picks, *, origin=''):
        """
        Smanji zalihu na navedenim lokacijama (inventory apply na stock.quant).
        picks: lista {product_id, location_id, quantity|take, location_name?}
        Vraća listu uspješnih/neuspješnih stavki.
        """
        results = []
        for pick in picks or []:
            product_id = pick.get('product_id') or pick.get('odoo_product_id')
            location_id = pick.get('location_id')
            qty = pick.get('take') if pick.get('take') is not None else pick.get('quantity')
            location_name = pick.get('location_name') or str(location_id or '')
            try:
                product_id = int(product_id)
                location_id = int(location_id)
                qty = max(0, int(float(qty or 0)))
            except (TypeError, ValueError):
                results.append({
                    'ok': False,
                    'product_id': product_id,
                    'location_id': location_id,
                    'location_name': location_name,
                    'quantity': qty,
                    'error': 'Neispravan pick (product/location/qty).',
                })
                continue
            if qty <= 0:
                continue
            try:
                applied = self._apply_quant_decrease(
                    product_id=product_id,
                    location_id=location_id,
                    qty=qty,
                    origin=origin,
                )
                results.append({
                    'ok': True,
                    'product_id': product_id,
                    'location_id': location_id,
                    'location_name': location_name,
                    'quantity': applied,
                    'error': None,
                })
            except OdooError as exc:
                results.append({
                    'ok': False,
                    'product_id': product_id,
                    'location_id': location_id,
                    'location_name': location_name,
                    'quantity': qty,
                    'error': str(exc),
                })
        return results

    def _apply_quant_decrease(self, *, product_id, location_id, qty, origin=''):
        """
        Smanji zalihu na lokaciji.
        1) stock.scrap (pouzdano preko XML-RPC)
        2) fallback: inventory_quantity + action_apply_inventory
           (Odoo 18 često vrati None → XML-RPC greška „cannot marshal None”,
            iako je apply uspio — tada re-čitamo quant).
        """
        product_id = int(product_id)
        location_id = int(location_id)
        qty = max(0, int(float(qty or 0)))
        if qty <= 0:
            return 0

        try:
            return self._apply_quant_decrease_scrap(
                product_id=product_id,
                location_id=location_id,
                qty=qty,
                origin=origin,
            )
        except OdooError:
            return self._apply_quant_decrease_inventory(
                product_id=product_id,
                location_id=location_id,
                qty=qty,
            )

    def _product_uom_id(self, product_id):
        rows = self.search_read(
            'product.product',
            [('id', '=', int(product_id))],
            ['uom_id'],
            limit=1,
        )
        if not rows:
            raise OdooError(f'product.product id={product_id} ne postoji.')
        uom = rows[0].get('uom_id')
        if isinstance(uom, (list, tuple)):
            return int(uom[0])
        if uom:
            return int(uom)
        raise OdooError(f'Nema UoM za product={product_id}.')

    def _location_on_hand(self, product_id, location_id):
        quants = self.search_read(
            'stock.quant',
            [
                ('product_id', '=', int(product_id)),
                ('location_id', '=', int(location_id)),
            ],
            ['quantity'],
        )
        total = 0.0
        for quant in quants:
            try:
                total += float(quant.get('quantity') or 0)
            except (TypeError, ValueError):
                continue
        return total

    def _apply_quant_decrease_scrap(self, *, product_id, location_id, qty, origin=''):
        """Skidanje preko stock.scrap + action_validate (Odoo 18 friendly)."""
        qty = max(0, int(float(qty or 0)))
        if qty <= 0:
            return 0
        before = self._location_on_hand(product_id, location_id)
        if before + 1e-9 < float(qty):
            raise OdooError(
                f'Nedovoljno zalihe na lokaciji {location_id} '
                f'(ima {before:g}, treba {qty}).'
            )

        uom_id = self._product_uom_id(product_id)
        vals = {
            'product_id': int(product_id),
            'product_uom_id': int(uom_id),
            'scrap_qty': float(qty),
            'location_id': int(location_id),
            'origin': (origin or '')[:200],
        }
        scrap_id = self.execute('stock.scrap', 'create', vals)
        if isinstance(scrap_id, list):
            scrap_id = scrap_id[0] if scrap_id else None
        if not scrap_id:
            raise OdooError('stock.scrap create nije vratio id.')

        try:
            self.execute('stock.scrap', 'action_validate', [int(scrap_id)])
        except OdooError as exc:
            # action_validate često vrati None ili action dict; None → marshal greška
            if not _is_xmlrpc_none_marshal_error(exc):
                # ponekad vrati wizard / treba confirm — probaj force
                try:
                    self.execute(
                        'stock.scrap',
                        'action_validate',
                        [int(scrap_id)],
                        context={'skip_sanity_check': True},
                    )
                except OdooError as exc2:
                    if not _is_xmlrpc_none_marshal_error(exc2):
                        raise OdooError(
                            f'stock.scrap.action_validate nije uspio (#{scrap_id}): {exc}'
                        ) from exc

        after = self._location_on_hand(product_id, location_id)
        decreased = before - after
        if decreased + 1e-6 < float(qty):
            # scrap možda u draft — pročitaj state
            scrap_rows = self.search_read(
                'stock.scrap',
                [('id', '=', int(scrap_id))],
                ['state', 'scrap_qty'],
                limit=1,
            )
            state = (scrap_rows[0].get('state') if scrap_rows else None) or '?'
            raise OdooError(
                f'stock.scrap #{scrap_id} state={state}: očekivano −{qty}, '
                f'stanje {before:g} → {after:g}.'
            )
        return int(qty)

    def _apply_quant_decrease_inventory(self, *, product_id, location_id, qty):
        """Fallback: inventory_quantity + action_apply_inventory (Odoo 16–18)."""
        remaining = float(qty)
        applied = 0.0
        quants = self.search_read(
            'stock.quant',
            [
                ('product_id', '=', int(product_id)),
                ('location_id', '=', int(location_id)),
                ('quantity', '>', 0),
            ],
            ['id', 'quantity'],
            order='quantity desc',
        )
        if not quants:
            raise OdooError(
                f'Nema quant-a za product={product_id} na location={location_id}.'
            )

        for quant in quants:
            if remaining <= 0:
                break
            quant_id = int(quant['id'])
            current = float(quant.get('quantity') or 0)
            if current <= 0:
                continue
            take = min(current, remaining)
            new_qty = current - take
            before = current

            self.execute(
                'stock.quant',
                'write',
                [quant_id],
                {
                    'inventory_quantity': new_qty,
                    'inventory_quantity_set': True,
                },
                context={'inventory_mode': True},
            )
            try:
                self.execute(
                    'stock.quant',
                    'action_apply_inventory',
                    [quant_id],
                    context={'inventory_mode': True},
                )
            except OdooError as exc:
                # Odoo 18 XML-RPC: metoda radi, ali response=None → marshall error
                if not _is_xmlrpc_none_marshal_error(exc):
                    raise OdooError(
                        f'action_apply_inventory nije uspio (quant {quant_id}): {exc}'
                    ) from exc

            after_rows = self.search_read(
                'stock.quant',
                [('id', '=', quant_id)],
                ['quantity'],
                limit=1,
            )
            after = float(after_rows[0].get('quantity') or 0) if after_rows else before
            if after > before - take + 0.01:
                raise OdooError(
                    f'Quant {quant_id}: stanje nije smanjeno ({before:g} → {after:g}, '
                    f'očekivano {new_qty:g}).'
                )
            applied += take
            remaining -= take

        if remaining > 0.001:
            raise OdooError(
                f'Nedovoljno zalihe na lokaciji {location_id} za product {product_id} '
                f'(traženo {qty}, skinuto {applied}).'
            )
        return int(applied)


def _odoo_datetime(value):
    """Odoo XML-RPC write_date: 'YYYY-MM-DD HH:MM:SS' u UTC."""
    from datetime import timezone as dt_tz

    from django.utils import timezone as dj_tz

    if value is None:
        return None
    dt = value
    if dj_tz.is_aware(dt):
        dt = dt.astimezone(dt_tz.utc).replace(tzinfo=None)
    return dt.strftime('%Y-%m-%d %H:%M:%S')


def _is_xmlrpc_none_marshal_error(exc):
    """
    Odoo XML-RPC ne može vratiti None (allow_none=False na serveru).
    Metode poput action_apply_inventory / action_validate često vrate None
    iako su uspješno izvršene — greška je u serijalizaciji odgovora.
    """
    text = str(exc or '').casefold()
    return (
        'cannot marshal none' in text
        or 'allow_none' in text
        or 'marshaller' in text and 'none' in text
    )


# Lokacije koje nisu police za pakovanje online narudžbi (transfer, kupci, virtualno…)
_PACKING_LOCATION_EXCLUDE_KEYWORDS = (
    'prenos',
    'maloprodaja',
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


def _quant_raw_quantity(record):
    try:
        return max(0, int(float(record.get('quantity') or 0)))
    except (TypeError, ValueError):
        return 0


def _quant_reserved(record):
    try:
        return max(0, int(float(record.get('reserved_quantity') or 0)))
    except (TypeError, ValueError):
        return 0


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