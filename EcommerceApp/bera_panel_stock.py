"""Stock controls for the panel's existing ProductAdmin."""
from django.contrib import admin
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import JsonResponse
from django.urls import path, reverse
from django.utils.html import format_html
from .bera_panel_modules import module_locked


class PanelProductStockMixin:
    def stock_enabled(self):
        return not module_locked('wms_zalihe')

    def get_list_display(self, request):
        return (*super().get_list_display(request), 'panel_stock_controls')

    @admin.display(description='Količina / Lokacije')
    def panel_stock_controls(self, obj):
        if not self.stock_enabled():
            return 'Zaključano'
        return format_html('<span class="panel-stock-controls"><span>{}</span><button type="button" class="panel-stock-button" data-product="{}" data-direction="add" aria-label="Dodaj zalihu">+</button><button type="button" class="panel-stock-button" data-product="{}" data-direction="remove" aria-label="Skini zalihu">−</button></span>', obj.stanje, obj.pk, obj.pk)

    def get_readonly_fields(self, request, obj=None):
        fields = super().get_readonly_fields(request, obj)
        return fields if self.stock_enabled() else (*fields, 'stanje', 'na_stanju')

    def get_changelist_instance(self, request):
        result = super().get_changelist_instance(request)
        if not self.stock_enabled():
            result.list_editable = tuple(f for f in result.list_editable if f not in {'stanje', 'na_stanju'})
        return result

    def get_changelist_formset(self, request, **kwargs):
        formset = super().get_changelist_formset(request, **kwargs)
        if not self.stock_enabled():
            for field in ('stanje', 'na_stanju'):
                formset.form.base_fields.pop(field, None)
            formset.form._meta.fields = tuple(f for f in self.list_editable if f not in {'stanje', 'na_stanju'})
        return formset

    def get_inline_instances(self, request, obj=None):
        inlines = super().get_inline_instances(request, obj)
        if not self.stock_enabled():
            for inline in inlines:
                names = {field.name for field in inline.model._meta.fields}
                inline.readonly_fields = (*inline.readonly_fields, *(names & {'stanje', 'na_stanju'}))
        return inlines

    def get_urls(self):
        return [path('lokacije-zalihe/', self.admin_site.admin_view(self.panel_stock_view), name='EcommerceApp_product_panel_stock')] + super().get_urls()

    def panel_stock_view(self, request):
        from .models import Product, WarehouseLocation, WarehouseStock, ProductVariation

        from .magacin import MagacinError, set_location_counted_qty
        if not self.stock_enabled() or not self.has_change_permission(request) or not request.user.has_perm('EcommerceApp.add_warehousemovement'):
            raise PermissionDenied
        if request.method == 'GET':
            locations = WarehouseLocation.objects.order_by('naziv')
            variants = ProductVariation.objects.filter(artikal_id=request.GET.get('product')).order_by('pk')
            stocks = list(WarehouseStock.objects.filter(product_id=request.GET.get('product'), kolicina__gt=0).values('location_id', 'variation_id', 'kolicina', 'rezervisano'))
            return JsonResponse({'locations': [{'id': x.pk, 'name': x.naziv, 'stocks': [
                {'variation': row['variation_id'], 'quantity': row['kolicina'], 'available': max(0, row['kolicina'] - row['rezervisano'])}
                for row in stocks if row['location_id'] == x.pk
            ]} for x in locations], 'variants': [{'id': x.pk, 'name': str(x)} for x in variants]})
        if request.method != 'POST':
            return JsonResponse({'error': 'Metoda nije dozvoljena.'}, status=405)
        try:
            quantity = int(request.POST.get('quantity', '0'))
            if quantity < 1 or request.POST.get('direction') not in {'add', 'remove'}:
                raise ValueError
            with transaction.atomic():
                product = Product.objects.select_for_update().get(pk=request.POST.get('product'))
                if not self.has_change_permission(request, product):
                    raise PermissionDenied
                wms = WarehouseLocation.objects.get(pk=request.POST.get('location'))
                location = wms
                variation = None
                if product.varijacije.exists():
                    variation = ProductVariation.objects.get(pk=request.POST.get('variation'), artikal=product)
                stock = WarehouseStock.objects.filter(product=product, location=location, variation=variation).first()
                current = stock.kolicina if stock else 0
                target = current + quantity if request.POST['direction'] == 'add' else current - quantity
                if target < 0 or (stock and target < stock.rezervisano):
                    raise MagacinError('Nema dovoljno slobodne količine na odabranoj lokaciji.')
                set_location_counted_qty(location=location, product=product, variation=variation, qty=target, user=request.user, napomena='Panel Artikli: '+request.POST['direction'])
            return JsonResponse({'ok': True})
        except (ValueError, TypeError, Product.DoesNotExist, WarehouseLocation.DoesNotExist, ProductVariation.DoesNotExist):
            return JsonResponse({'error': 'Odaberite artikal, lokaciju, varijaciju ako postoji i pozitivnu količinu.'}, status=400)
        except MagacinError as exc:
            return JsonResponse({'error': str(exc)}, status=400)
