from django.core.management.base import BaseCommand

from EcommerceApp.models import Product
from EcommerceApp.utils.images import rename_product_images_to_title


class Command(BaseCommand):
    help = (
        'Preimenuje postojeće slike artikala (glavna + galerija + varijacije) '
        'prema nazivu/slug-u artikla — bez re-uploada i bez re-encode.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--all',
            action='store_true',
            help='Svi artikli koji imaju sliku (glavnu, galeriju ili varijaciju).',
        )
        parser.add_argument(
            '--pk',
            type=int,
            nargs='*',
            default=[],
            help='ID artikala (može više).',
        )
        parser.add_argument(
            '--slug',
            type=str,
            nargs='*',
            default=[],
            help='Slug artikala (može više).',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Samo ispiši šta bi se desilo (bez pisanja).',
        )

    def handle(self, *args, **options):
        qs = Product.objects.all()
        if options['pk']:
            qs = qs.filter(pk__in=options['pk'])
        elif options['slug']:
            qs = qs.filter(slug__in=options['slug'])
        elif options['all']:
            qs = qs.filter(models_q_has_any_image()).distinct()
        else:
            self.stderr.write(
                'Navedi --all, --pk ID … ili --slug slug …',
            )
            return

        qs = qs.prefetch_related('dodatne_slike', 'varijacije').order_by('pk')
        dry = options['dry_run']
        renamed = 0
        skipped = 0
        errors = 0

        for product in qs:
            try:
                if dry:
                    from EcommerceApp.utils.images import product_image_seo_label
                    # dry-run: samo usporedi imena, ne piši
                    base = product_image_seo_label(product.naziv or product.slug or 'artikal')
                    planned = []
                    if product.slika:
                        old = product.slika.name
                        stem = old.rsplit('/', 1)[-1].rsplit('.', 1)[0]
                        if stem != base:
                            planned.append(f'  main: {old} → …/{base}.*')
                    for idx, extra in enumerate(
                        product.dodatne_slike.exclude(slika='').order_by('redoslijed', 'id'),
                        start=1,
                    ):
                        label = product_image_seo_label(
                            product.naziv or product.slug or 'artikal',
                            extra=f'galerija-{idx}',
                        )
                        stem = extra.slika.name.rsplit('/', 1)[-1].rsplit('.', 1)[0]
                        if stem != label:
                            planned.append(f'  extra: {extra.slika.name} → …/{label}.*')
                    for var in product.varijacije.exclude(slika='').exclude(slika=None):
                        label = product_image_seo_label(
                            product.naziv or product.slug or 'artikal',
                            extra=var.naziv or str(var.pk),
                        )
                        stem = var.slika.name.rsplit('/', 1)[-1].rsplit('.', 1)[0]
                        if stem != label:
                            planned.append(f'  var: {var.slika.name} → …/{label}.*')
                    if planned:
                        self.stdout.write(f'[dry-run] {product.pk} {product.naziv}')
                        for line in planned:
                            self.stdout.write(line)
                        renamed += len(planned)
                    else:
                        skipped += 1
                    continue

                results = rename_product_images_to_title(product)
                changed_any = False
                for kind, label, result in results:
                    if result.get('changed'):
                        changed_any = True
                        renamed += 1
                        self.stdout.write(
                            f'OK {kind} {product.pk}: '
                            f'{result["old_name"]} → {result["new_name"]}',
                        )
                    else:
                        skipped += 1
                if not results:
                    skipped += 1
                elif not changed_any:
                    self.stdout.write(f'SKIP {product.pk}: već usklađeno ({product.naziv})')
            except Exception as exc:
                errors += 1
                self.stderr.write(f'GREŠKA product {product.pk}: {exc}')

        self.stdout.write(self.style.SUCCESS(
            f'Završeno: {renamed} preimenovano, {skipped} preskočeno, {errors} grešaka'
            + (' (dry-run)' if dry else '')
            + '.',
        ))


def models_q_has_any_image():
    from django.db.models import Q

    return (
        Q(slika__isnull=False) & ~Q(slika='')
    ) | Q(dodatne_slike__slika__isnull=False) | (
        Q(varijacije__slika__isnull=False) & ~Q(varijacije__slika='')
    )
