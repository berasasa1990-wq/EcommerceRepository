import json
from django.db.models import Prefetch
from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.shortcuts import get_object_or_404, redirect, render
from .models import Product, ProductVariation, ProductSetComponent, Category, Brand
from .magacin import MagacinError
from .warehouse_access import warehouse_user_required
from .product_sets import save_set, refresh_set, component_stock_totals
from .views_magacin import _magacin_context


@login_required(login_url='login')
@user_passes_test(warehouse_user_required)
def product_sets(request):
    raw_id = request.POST.get('product_id') or request.GET.get('id')
    product = get_object_or_404(Product, pk=raw_id) if raw_id else None
    rows = []
    data = {'naziv': product.naziv if product else '', 'sifra': product.sifra or '' if product else '',
            'cijena': str(product.cijena) if product else '',
            'akcijska_cijena': str(product.akcijska_cijena) if product and product.akcijska_cijena is not None else '',
            'opis': product.opis if product else '', 'aktivan': bool(product.aktivan) if product else True,
            'kategorija_id': str(product.kategorija_id or '') if product else '',
            'brend_id': str(product.brend_id or '') if product else ''}
    if product:
        rows = [{'product_id': c.product_id, 'variation_id': c.variation_id or '', 'quantity': c.quantity,
                 'unit_price': str(c.variation.bazna_cijena if c.variation else c.product.bazna_cijena),
                 'label': c.product.naziv + (' — ' + c.variation.naziv if c.variation else '')}
                for c in product.set_components.select_related('product', 'variation')]
    error = ''
    if request.method == 'POST':
        data.update({key: request.POST.get(key, '') for key in data})
        data['aktivan'] = request.POST.get('aktivan') == '1'
        try:
            rows = json.loads(request.POST.get('components_json', '[]'))
            saved = save_set(product_id=product.pk if product else None, name=data['naziv'], code=data['sifra'],
                             regular_price=None, sale_price=data['akcijska_cijena'], rows=rows,
                             category_id=data['kategorija_id'], brand_id=data['brend_id'],
                             description=data['opis'], active=data['aktivan'], image=request.FILES.get('slika'))
            messages.success(request, f'Set „{saved.naziv}” je sačuvan.')
            return redirect(f'{request.path}?id={saved.pk}')
        except (MagacinError, ValueError, TypeError, Category.DoesNotExist, Brand.DoesNotExist) as exc:
            error = str(exc) if isinstance(exc, MagacinError) else 'Podaci seta nisu ispravni.'
            if not isinstance(rows, list):
                rows = []
    if request.method == 'POST':
        for row in rows:
            if not isinstance(row, dict):
                continue
            try:
                component = Product.objects.get(pk=row.get('product_id'))
                variation = ProductVariation.objects.get(pk=row['variation_id'], artikal=component) if row.get('variation_id') else None
                row['unit_price'] = str(variation.bazna_cijena if variation else component.bazna_cijena)
            except (Product.DoesNotExist, ProductVariation.DoesNotExist, ValueError, TypeError):
                row['unit_price'] = '0.00'
        rows = [row for row in rows if isinstance(row, dict)]
    sets = list(Product.objects.filter(is_set=True).order_by('naziv').prefetch_related(Prefetch('set_components', queryset=ProductSetComponent.objects.select_related('product', 'variation'))))
    for item in sets:
        refresh_set(item)
        item.component_stock_rows = []
        for component in item.set_components.all():
            totals = component_stock_totals(component)
            item.component_stock_rows.append({
                'component': component, **totals,
                'short_stock': totals['dostupno'] < component.quantity,
            })
    context = _magacin_context(request, section='setovi', page_title='Artikli u setu — Magacin', hide_top_search=True)
    context.update({'product': product, 'sets': sets, 'form_data': data, 'components': rows,
                    'form_error': error, 'categories': Category.objects.filter(aktivan=True), 'brands': Brand.objects.all()})
    return render(request, 'staff/magacin/product_sets.html', context)
