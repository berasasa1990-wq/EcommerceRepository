import xmlrpc.client
from urllib.parse import urljoin

from django.conf import settings


class OdooError(Exception):
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
        return xmlrpc.client.ServerProxy(
            urljoin(f'{self.url}/', path),
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

    def search_read(self, model, domain, fields, *, limit=None, order=None):
        options = {'fields': fields}
        if limit is not None:
            options['limit'] = limit
        if order:
            options['order'] = order
        return self.execute(model, 'search_read', domain, **options)

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

    def get_products_in_category(self, category_id, *, include_children=True):
        category_id = int(category_id)
        if include_children:
            domain = [('categ_id', 'child_of', category_id), ('sale_ok', '=', True)]
        else:
            domain = [('categ_id', '=', category_id), ('sale_ok', '=', True)]
        fields = [
            'id',
            'name',
            'default_code',
            'list_price',
            'description_sale',
            'barcode',
            'categ_id',
            'product_variant_ids',
            'qty_available',
        ]
        return self.search_read('product.template', domain, fields, order='name asc')

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
        ]
        if with_images:
            fields.append('image_variant_1920')
        return self.search_read('product.product', [('id', 'in', variant_ids)], fields)

    def get_template_images(self, template_ids):
        if not template_ids:
            return {}
        records = self.search_read(
            'product.template',
            [('id', 'in', template_ids)],
            ['id', 'image_1920'],
        )
        return {record['id']: record.get('image_1920') for record in records}


def odoo_je_konfigurisan():
    return bool(
        getattr(settings, 'ODOO_URL', '')
        and getattr(settings, 'ODOO_DB', '')
        and getattr(settings, 'ODOO_USERNAME', '')
        and getattr(settings, 'ODOO_API_KEY', '')
    )