from django import forms
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_http_methods

from .b2b_forms import B2BAccountForm
from .models import B2BAccount, B2BAccountBrandRabat, B2BSubmission, Order


BrandRabatFormSet = forms.inlineformset_factory(
    B2BAccount, B2BAccountBrandRabat, fields=('brand', 'postotak'), extra=0,
)


class B2BOrderDetailsForm(forms.ModelForm):
    class Meta:
        model = Order
        fields = ('ime_prezime', 'email', 'telefon', 'adresa', 'grad', 'postanski_broj', 'napomena')
        widgets = {'napomena': forms.Textarea(attrs={'rows': 4})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Warehouse processing uses the checkout header to identify VP orders.
        lines = self.instance.napomena.split('\n', 3)
        self.note_header = []
        if lines[0] == 'VP narudžba':
            self.note_header.append(lines.pop(0))
            for prefix in ('B2B: ', 'Plaćanje: '):
                if lines and lines[0].startswith(prefix):
                    self.note_header.append(lines.pop(0))
        self.initial['napomena'] = '\n'.join(lines)

    def save(self):
        order = super().save(commit=False)
        if self.note_header:
            order.napomena = '\n'.join([*self.note_header, order.napomena]).rstrip()
        if self.changed_data:
            order.save(update_fields=self.changed_data)
        return order


@never_cache
@require_GET
def control_list(request, orders=False):
    if not request.user.is_superuser:
        return HttpResponseForbidden()

    query = request.GET.get('q', '').strip()[:150]
    if orders:
        records = B2BSubmission.objects.select_related('account', 'order').order_by('-created_at', '-pk')
        if query:
            records = records.filter(
                Q(order__broj__icontains=query) | Q(account__company__icontains=query) |
                Q(account__username__icontains=query) | Q(order__ime_prezime__icontains=query))
    else:
        records = B2BAccount.objects.order_by('company', 'username')
        if query:
            records = records.filter(
                Q(company__icontains=query) | Q(username__icontains=query) |
                Q(ime_prezime__icontains=query) | Q(telefon__icontains=query) |
                Q(email__icontains=query))

    return render(request, 'b2b/control_list.html', {
        'orders': orders, 'page': Paginator(records, 10).get_page(request.GET.get('page')),
        'q': query,
    })


@never_cache
@require_http_methods(['GET', 'POST'])
def account_details(request, pk=None):
    if not request.user.is_superuser:
        return HttpResponseForbidden()

    data = request.POST if request.method == 'POST' else None
    saved = False
    with transaction.atomic():
        accounts = B2BAccount.objects.select_for_update() if data is not None else B2BAccount.objects.all()
        account = get_object_or_404(accounts, pk=pk) if pk is not None else B2BAccount()
        form = B2BAccountForm(data, instance=account)
        rabats = BrandRabatFormSet(data, instance=account)
        if data is not None:
            valid_account = form.is_valid()
            valid_rabats = rabats.is_valid()
            if valid_account and valid_rabats:
                form.save()
                rabats.save()
                form = B2BAccountForm(instance=account)
                rabats = BrandRabatFormSet(instance=account)
                saved = True
    return render(request, 'b2b/control_details.html', {
        'form': form, 'rabats': rabats, 'record': account, 'saved': saved,
        'form_action': reverse('b2b_control_account', args=[account.pk]) if account.pk else request.path,
    })


@never_cache
@require_http_methods(['GET', 'POST'])
def order_details(request, pk):
    if not request.user.is_superuser:
        return HttpResponseForbidden()

    submission = get_object_or_404(B2BSubmission.objects.select_related('order', 'account'), pk=pk)
    data = request.POST if request.method == 'POST' else None
    form = B2BOrderDetailsForm(data, instance=submission.order)
    saved = data is not None and form.is_valid()
    if saved:
        form = B2BOrderDetailsForm(instance=form.save())
    return render(request, 'b2b/control_details.html', {
        'submission': submission, 'form': form, 'saved': saved,
    })
