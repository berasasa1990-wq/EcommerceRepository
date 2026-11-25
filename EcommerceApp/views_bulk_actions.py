"""Catalog selection and scheduled percentage discounts for administrators."""
from datetime import date
from decimal import Decimal, InvalidOperation

from django import forms
from django.contrib.auth.decorators import login_required, user_passes_test
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.core.paginator import Paginator
from django.http import JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from .models import Akcija, Product
from .views import _superuser_required, _base_context
from .views_magacin import _akcija_apply_product_discount


@login_required(login_url='login')
@user_passes_test(_superuser_required)
@require_http_methods(['GET', 'POST'])
def bulk_actions(request):
    if request.method == 'POST':
        try:
            scope = request.POST.get('scope', 'selected')
            if scope not in ('selected', 'stock'):
                raise ValueError('Odaberi artikle ili kompletan lager.')
            pct = Decimal(request.POST.get('percent', '').replace(',', '.'))
            if not pct.is_finite() or not 0 < pct < 100:
                raise ValueError('Sniženje mora biti veće od 0 i manje od 100 %.')
            pct = pct.quantize(Decimal('0.01'))
            if not 0 < pct < 100:
                raise ValueError('Unesi procenat od 0,01 do 99,99 %.')
            if scope == 'stock':
                date_time = forms.DateTimeField()
                start = date_time.clean(request.POST.get('starts_at'))
                end = date_time.clean(request.POST.get('ends_at'))
                if end <= start:
                    raise ValueError('Završetak akcije mora biti nakon početka.')
                if end <= timezone.now():
                    raise ValueError('Završetak akcije mora biti u budućnosti.')
                Akcija.objects.create(
                    naziv='Kompletan lager',
                    tip=Akcija.Tip.AKCIJSKA,
                    aktivan=True,
                    kompletan_lager=True,
                    popust_postotak=pct,
                    pocetak=start,
                    kraj_lagera=end,
                )
                start_label = timezone.localtime(start).strftime('%d.%m.%Y. %H:%M')
                end_label = timezone.localtime(end).strftime('%d.%m.%Y. %H:%M')
                return JsonResponse({'message': f'Kompletan lager −{pct}% od {start_label} do {end_label}.'})
            ids = request.POST.getlist('artikal')
            if not ids or len(ids) > 2000 or any(not value.isdigit() for value in ids):
                raise ValueError('Označi od 1 do 2000 artikala.')
            ids = set(map(int, ids))
            end = date.fromisoformat(request.POST.get('end_date', ''))
            if end < timezone.localdate():
                raise ValueError('Datum završetka ne može biti u prošlosti.')
            with transaction.atomic():
                products = list(Product.objects.select_for_update().filter(pk__in=ids, obrisan_at__isnull=True).prefetch_related('varijacije'))
                if len(products) != len(ids):
                    raise ValueError('Neki artikli više nisu dostupni. Osvježi katalog.')
                for product in products:
                    _akcija_apply_product_discount(product, pct, end)
            return JsonResponse({'message': f'Akcija −{pct}% postavljena na {len(products)} artikala do {end.strftime("%d.%m.%Y.")}'})
        except ValidationError:
            return JsonResponse({'error': 'Unesi ispravan datum i vrijeme početka i završetka akcije.'}, status=400)
        except (ValueError, InvalidOperation) as exc:
            return JsonResponse({'error': str(exc) if isinstance(exc, ValueError) and str(exc) and not str(exc).startswith('Invalid isoformat') else 'Unesi ispravan procenat i datum završetka.'}, status=400)
    if request.GET.get('catalog') == '1':
        query = request.GET.get('q', '').strip()[:200]
        products = Product.objects.filter(obrisan_at__isnull=True, na_stanju=True, stanje__gt=0)
        if query:
            products = products.filter(Q(naziv__icontains=query) | Q(sifra__icontains=query) | Q(barkod__icontains=query))
        page = Paginator(products.order_by('naziv', 'pk'), 60).get_page(request.GET.get('page'))
        return JsonResponse({'results': [{'id': p.pk, 'name': p.naziv, 'sku': p.sifra or '', 'price': str(p.cijena), 'image': p.slika.url if p.slika else ''} for p in page], 'pages': page.paginator.num_pages, 'page': page.number, 'total': page.paginator.count})
    return render(request, 'staff/bulk_actions.html', {**_base_context(), 'today': timezone.localdate().isoformat()})
