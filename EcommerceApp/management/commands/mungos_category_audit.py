"""SELECT-only category coverage audit; no HTTP client or model writes."""
from collections import Counter
from django.core.management.base import BaseCommand
from EcommerceApp.models import Category, Product
from EcommerceApp.mungos_categories import CATEGORY_CODES, CATEGORY_PREFIX, resolve_category
from EcommerceApp.mungos_product import build_product_preview, sanitized_json


class Command(BaseCommand):
    help = 'Read-only Mungos category audit; bez HTTP-a i izmjena webshopa.'
    requires_system_checks = []

    def handle(self, *args, **options):
        categories = {c.pk: c for c in Category.objects.order_by('pk')}
        # Populate FK caches to resolve all ancestors without per-category queries.
        for category in categories.values():
            category._state.fields_cache['roditelj'] = categories.get(category.roditelj_id)
        counts = Counter()
        ready = blocked = full_ready = 0
        product_rows = []
        target_ids = {14, 20, 22, 23, 34, 35, 36, 37, 38}
        products = Product.objects.order_by('pk').prefetch_related('varijacije', 'dodatne_slike')
        for product in products.iterator(chunk_size=200):
            product._state.fields_cache['kategorija'] = categories.get(product.kategorija_id)
            code, source = resolve_category(product.kategorija)
            counts[product.kategorija_id] += 1
            ready += bool(code)
            blocked += not bool(code)
            preview = build_product_preview(product)
            full_ready += preview['status'] == 'READY_FOR_REVIEW'
            if product.pk in target_ids:
                product_rows.append({
                    'product_id': product.pk, 'name': product.naziv,
                    'category_id': product.kategorija_id,
                    'category': preview['category'], 'categoryCode': code,
                    'mapping_source_id': source, 'status': preview['status'],
                    'reviewReasons': preview['reviewReasons'],
                })
        rows = []
        for category in categories.values():
            code, source = resolve_category(category)
            old = category
            visited = set()
            previous = None
            while old and old.pk not in visited:
                visited.add(old.pk)
                suffix = CATEGORY_CODES.get(old.naziv.strip().casefold())
                if suffix:
                    previous = CATEGORY_PREFIX + suffix
                    break
                old = old.roditelj
            rows.append({
                'category_id': category.pk, 'name': category.naziv,
                'parent_id': category.roditelj_id,
                'parent': category.roditelj.naziv if category.roditelj else None,
                'product_count': counts[category.pk],
                'previous_mapping': previous, 'current_mapping': code,
                'proposed_categoryCode': code, 'mapping_source_id': source,
                'status': 'MAPPED' if code else 'NEEDS_REVIEW',
            })
        unmapped = [row for row in rows if not row['current_mapping']]
        self.stdout.write(sanitized_json({
            'scope': 'Configured database; direct product counts, all products including inactive.',
            'TOTAL_CATEGORIES': len(rows), 'MAPPED_CATEGORIES': len(rows) - len(unmapped),
            'UNMAPPED_CATEGORIES': len(unmapped), 'TOTAL_PRODUCTS': ready + blocked,
            'PRODUCTS_READY_BY_CATEGORY': ready, 'PRODUCTS_BLOCKED_BY_CATEGORY': blocked,
            'PRODUCTS_READY_PAYLOAD': full_ready,
            'PRODUCTS_OTHER_PAYLOAD_REVIEW': ready + blocked - full_ready,
            'PRODUCTS_WITHOUT_CATEGORY': counts[None],
            'categories': rows, 'unmapped_categories': unmapped,
            'requested_products': product_rows,
            'requested_products_not_found': sorted(target_ids - {r['product_id'] for r in product_rows}),
        }))
