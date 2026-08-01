"""
Extract ProductAttribute rows from product/variation names (and short opis).

Run offline — never at search-request time:

    python manage.py extract_product_attributes
    python manage.py extract_product_attributes --replace
    python manage.py extract_product_attributes --limit 100
"""

from __future__ import annotations

from django.core.management.base import BaseCommand
from django.db import transaction

from EcommerceApp.models import Product, ProductAttribute, ProductVariation
from EcommerceApp.search.measures import extract_attributes_from_product_text
from EcommerceApp.search.normalize import normalize_search_text, tokenize_search_query


class Command(BaseCommand):
    help = (
        'Izvuci mjere/karakteristike iz naziva artikala i varijacija '
        'u ProductAttribute (za search po 3.60m, 150g, 4000, …).'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--replace',
            action='store_true',
            help='Obriši prethodno extract-ovane atribute prije unosa (ne dira manual).',
        )
        parser.add_argument(
            '--limit',
            type=int,
            default=0,
            help='Obradi samo N artikala (0 = svi).',
        )
        parser.add_argument(
            '--include-opis',
            action='store_true',
            help='Uključi prvih 400 znakova opisa (sporo / bučno — default off).',
        )

    def handle(self, *args, **options):
        replace = options['replace']
        limit = options['limit']
        include_opis = options['include_opis']

        qs = Product.objects.filter(aktivan=True).prefetch_related('varijacije').order_by('id')
        if limit > 0:
            qs = qs[:limit]

        created = 0
        skipped = 0
        products_touched = 0

        for product in qs.iterator(chunk_size=100):
            # Rebuild context from name + category keywords
            parts = [product.naziv or '']
            if product.kategorija_id:
                try:
                    parts.append(product.kategorija.naziv or '')
                    if product.kategorija.roditelj_id:
                        parts.append(product.kategorija.roditelj.naziv or '')
                except Exception:
                    pass
            if include_opis and product.opis:
                parts.append((product.opis or '')[:400])

            # Prefetch variations — iterator may not keep prefetch; query explicitly
            var_names = list(
                ProductVariation.objects.filter(artikal_id=product.pk)
                .values_list('naziv', flat=True)[:50],
            )
            parts.extend(var_names)

            blob = ' '.join(p for p in parts if p)
            ctx = set(tokenize_search_query(normalize_search_text(blob)))
            rows = extract_attributes_from_product_text(blob, context_tokens=ctx)
            if not rows:
                continue

            products_touched += 1
            with transaction.atomic():
                if replace:
                    ProductAttribute.objects.filter(
                        product_id=product.pk,
                        izvor='extract',
                    ).delete()

                for row in rows:
                    obj, was_created = ProductAttribute.objects.update_or_create(
                        product_id=product.pk,
                        attribute_type=row['attribute_type'],
                        normalized_numeric_value=row['normalized_numeric_value'],
                        unit=row.get('unit') or '',
                        defaults={
                            'text_value': row.get('text_value') or '',
                            'numeric_value': row.get('numeric_value'),
                            'aktivno': True,
                            'izvor': 'extract',
                        },
                    )
                    if was_created:
                        created += 1
                    else:
                        skipped += 1

        self.stdout.write(self.style.SUCCESS(
            f'Gotovo: artikala s atributima={products_touched}, '
            f'novih={created}, ažuriranih/postojećih={skipped}.',
        ))
