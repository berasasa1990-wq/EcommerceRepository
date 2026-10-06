"""Composite products: catalog availability and immutable order components."""
from django.db import transaction
from .models import Product, ProductSetComponent, Order, OrderItem, WarehouseStock


def component_stock_totals(component):
    from .magacin import display_stock_totals
    product = component.product
    if WarehouseStock.objects.filter(product=product).exists() or product.magacin_sync_at:
        return display_stock_totals(product, component.variation)
    target = component.variation or product
    qty = max(0, int(target.stanje or 0)) if target.na_stanju else 0
    return {'na_stanju': qty, 'rezervisano': 0, 'dostupno': qty}


def set_stock_totals(product):
    rows = list(product.set_components.select_related('product', 'variation'))
    if not rows:
        return {'na_stanju': 0, 'rezervisano': 0, 'dostupno': 0}
    capacities, available = [], []
    for row in rows:
        component = row.product
        if component.is_set or not component.aktivan or component.sakriven_do_stanja:
            return {'na_stanju': 0, 'rezervisano': 0, 'dostupno': 0}
        totals = component_stock_totals(row)
        capacities.append(max(0, totals['na_stanju']) // row.quantity)
        available.append(max(0, totals['dostupno']) // row.quantity)
    physical, free = min(capacities), min(available)
    return {'na_stanju': physical, 'rezervisano': max(0, physical - free), 'dostupno': free}


def refresh_set(product):
    qty = set_stock_totals(product)['dostupno']
    Product.objects.filter(pk=product.pk).update(stanje=qty, na_stanju=qty > 0)
    product.stanje, product.na_stanju = qty, qty > 0
    return qty


def refresh_dependent_sets(sender, instance, raw=False, **kwargs):
    if raw:
        return
    if isinstance(instance, ProductSetComponent):
        ids = [instance.set_product_id]
    else:
        component_id = instance.pk if isinstance(instance, Product) else (
            instance.artikal_id if sender.__name__ == 'ProductVariation' else instance.product_id)
        ids = ProductSetComponent.objects.filter(product_id=component_id).values_list('set_product_id', flat=True)
    for product in Product.objects.filter(pk__in=ids, is_set=True):
        refresh_set(product)


@transaction.atomic
def prepare_order_sets(order):
    """Snapshot each set once; the invoice parent keeps the customer's set price."""
    from .magacin import MagacinError
    Order.objects.select_for_update().get(pk=order.pk)
    for parent in order.stavke.filter(artikal__is_set=True, set_parent__isnull=True, is_set_parent=False).select_related('artikal'):
        components = list(parent.artikal.set_components.select_related('product', 'variation'))
        if not components:
            raise MagacinError(f'Set „{parent.naziv}” nema unesene artikle.')
        if parent.varijacija_id:
            raise MagacinError('Set se prodaje bez varijacija.')
        for component in components:
            if parent.kolicina * component.quantity > 1000000:
                raise MagacinError('Ukupna količina komponente seta prelazi 1000000 komada.')
            product, variation = component.product, component.variation
            if product.is_set:
                raise MagacinError('Set ne može sadržavati drugi set.')
            OrderItem.objects.create(
                narudzba=order, set_parent=parent, set_component_quantity=component.quantity,
                artikal=product, varijacija=variation, naziv=product.naziv[:200],
                product_naziv=product.naziv[:200], varijacija_naziv=variation.naziv if variation else '',
                sifra=(variation.sifra if variation and variation.sifra else product.sifra) or '',
                cijena=0, bazna_cijena=0, kolicina=parent.kolicina * component.quantity,
            )
        parent.is_set_parent = True
        parent.save(update_fields=['is_set_parent'])
    if hasattr(order, '_prefetched_objects_cache'):
        order._prefetched_objects_cache.pop('stavke', None)


@transaction.atomic
def reserve_set(order, product, qty, *, user=None, napomena='', set_item=None):
    from .magacin import reserve_for_order, ensure_web_product_stock, MagacinError
    prepare_order_sets(order)
    parents = order.stavke.filter(artikal=product, is_set_parent=True)
    if set_item is not None:
        parents = parents.filter(pk=set_item.pk)
    parent = parents.order_by('-pk').first()
    rows = list(parent.set_children.select_related('artikal', 'varijacija')) if parent else []
    if not rows:
        raise MagacinError('Prvo dodaj set na narudžbu.')
    # Lock shared component products in a stable order before reserving any stock.
    list(Product.objects.select_for_update().filter(pk__in=[r.artikal_id for r in rows]).order_by('pk'))
    for row in rows:
        component = ensure_web_product_stock(row.artikal)
        leftover = reserve_for_order(order, component, qty * row.set_component_quantity,
                                     variation=row.varijacija, user=user, napomena=napomena)
        if leftover:
            raise MagacinError(f'Set „{product.naziv}”: nema dovoljno artikla „{row.puni_naziv}”.')
    return 0


def sync_set_picked(order, *, require_complete=False):
    from .magacin import MagacinError
    for parent in order.stavke.filter(is_set_parent=True).prefetch_related('set_children'):
        children = list(parent.set_children.all())
        complete = bool(children) and all(c.kolicina_pokupljeno == c.kolicina for c in children)
        if require_complete and not complete:
            raise MagacinError(f'Set „{parent.naziv}” nije kompletan. Pokupi sve artikle i količine iz seta.')
        parent.kolicina_pokupljeno = parent.kolicina if complete else None
        parent.save(update_fields=['kolicina_pokupljeno'])


@transaction.atomic
def save_set(*, product_id=None, name, regular_price, sale_price=None, code='', rows, category_id=None,
             brand_id=None, image=None, description='', active=True):
    from decimal import Decimal, InvalidOperation
    from .magacin import MagacinError
    from .models import ProductVariation, Category, Brand
    name = (name or '').strip()
    if not name or len(name) > 200:
        raise MagacinError('Unesi naziv seta (najviše 200 znakova).')
    product = Product.objects.select_for_update().get(pk=product_id) if product_id else Product()
    if product.pk and (product.varijacije.exists() or product.used_in_sets.exists() or WarehouseStock.objects.filter(product=product, kolicina__gt=0).exists()):
        raise MagacinError('Set ne može imati vlastitu zalihu, varijacije ili biti dio drugog seta.')
    if not isinstance(rows, list) or not 1 <= len(rows) <= 100:
        raise MagacinError('Dodaj od 1 do 100 artikala u set.')
    components, seen = [], set()
    for row in rows:
        try:
            component = Product.objects.get(pk=row['product_id'], aktivan=True, is_set=False)
            variation = ProductVariation.objects.get(pk=row['variation_id'], artikal=component) if row.get('variation_id') else None
            qty = int(str(row['quantity']))
            if not 1 <= qty <= 1000000 or (component.varijacije.exists() and variation is None):
                raise ValueError
        except (KeyError, TypeError, ValueError, Product.DoesNotExist, ProductVariation.DoesNotExist):
            raise MagacinError('Odaberi ispravan artikal, varijaciju i cijelu količinu od 1 do 1000000.')
        key = (component.pk, variation.pk if variation else None)
        if key in seen or component.pk == product.pk:
            raise MagacinError('Artikal je ponovljen ili je set dodan sam sebi.')
        seen.add(key)
        components.append((component, variation, qty))
    try:
        regular = (sum(((v.bazna_cijena if v else p.bazna_cijena) * q
                        for p, v, q in components), Decimal('0.00')) if regular_price is None
                   else Decimal(str(regular_price).replace(',', '.')))
        sale = Decimal(str(sale_price).replace(',', '.')) if sale_price not in (None, '') else None
        if not regular.is_finite() or not 0 < regular <= Decimal('99999999.99') or regular != regular.quantize(Decimal('.01')):
            raise ValueError
        if sale is not None and (not sale.is_finite() or not 0 < sale < regular or sale != sale.quantize(Decimal('.01'))):
            raise ValueError
    except (InvalidOperation, ValueError, ArithmeticError):
        raise MagacinError('Unesi ispravnu regularnu cijenu i sniženu cijenu manju od regularne.')
    code = (code or '').strip() or None
    if code and Product.objects.filter(sifra=code).exclude(pk=product.pk).exists():
        raise MagacinError('Šifra je već zauzeta.')
    product.naziv, product.sifra, product.is_set = name, code, True
    product.cijena, product.akcijska_cijena = regular, sale
    product.akcija_postotak, product.akcija_do = None, None
    product.kategorija = Category.objects.get(pk=category_id) if category_id else None
    product.brend = Brand.objects.get(pk=brand_id) if brand_id else None
    product.opis, product.aktivan = description, active
    if image:
        product.slika = image
    product.save()
    product.set_components.all().delete()
    ProductSetComponent.objects.bulk_create([
        ProductSetComponent(set_product=product, product=p, variation=v, quantity=q)
        for p, v, q in components
    ])
    refresh_set(product)
    return product
