"""Staff settings editor using the existing SiteSettings form and inline schema."""
from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.db import transaction
from django.db.models import Q
from django.forms import inlineformset_factory, modelform_factory
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_http_methods
from django.views.decorators.clickjacking import xframe_options_sameorigin

from .admin import SiteSettingsAdmin, SiteSettingsAdminForm
from .models import SiteSettings, Product, Category, Brand


LOOKUP_MODELS = {'Product': Product, 'Category': Category, 'Brand': Brand}


def can_edit_settings(user):
    return user.is_authenticated and user.is_active and (user.is_staff or user.is_superuser)


class SettingsSelect(forms.Select):
    """Render only selected records; search and validation remain server-side."""
    def optgroups(self, name, value, attrs=None):
        choices = self.choices
        if hasattr(choices, 'queryset'):
            ids = [item for item in value if str(item).isdigit()]
            self.choices = [('', '---------')] + [
                (obj.pk, str(obj)) for obj in choices.queryset.filter(pk__in=ids)
            ]
        try:
            return super().optgroups(name, value, attrs)
        finally:
            self.choices = choices


def style_form(form):
    for field in form.fields.values():
        field.widget.attrs['form'] = 'panelSettingsForm'
        if (isinstance(field, forms.ModelChoiceField) and not field.widget.is_hidden
                and field.queryset.model.__name__ in LOOKUP_MODELS):
            field.widget = SettingsSelect(attrs={
                'form': 'panelSettingsForm',
                'data-settings-lookup': field.queryset.model.__name__,
                'data-lookup-url': reverse('staff_settings_lookup'),
            })
            field.widget.choices = field.choices
        elif isinstance(field, forms.CharField) and not isinstance(field.widget, forms.Textarea):
            field.widget.attrs.setdefault('size', 40)
    return form


def make_settings_form(instance, data=None, files=None):
    names = []
    for _, options in SiteSettingsAdmin.fieldsets:
        for name in options['fields']:
            if name not in SiteSettingsAdmin.readonly_fields and name not in names:
                names.append(name)
    Form = modelform_factory(SiteSettings, form=SiteSettingsAdminForm, fields=names)
    return style_form(Form(data=data, files=files, instance=instance))


@login_required(login_url='login')
@user_passes_test(can_edit_settings, login_url='login')
@require_http_methods(['POST'])
def staff_settings_logo(request):
    # Saving a logo must not depend on unrelated fields and homepage formsets.
    if 'logo' not in request.FILES:
        return JsonResponse({'error': 'Odaberite sliku loga.'}, status=400)
    Form = modelform_factory(SiteSettings, fields=['logo'])
    form = Form(request.POST, request.FILES, instance=SiteSettings.objects.get_or_create(pk=1)[0])
    if not form.is_valid():
        return JsonResponse({'error': ' '.join(form.errors['logo'])}, status=400)
    site = form.save()
    return JsonResponse({'url': site.logo.url, 'message': 'Logo je sačuvan i prikazuje se na sajtu.'})


def make_inline_groups(instance, data=None, files=None):
    groups = []
    for inline in SiteSettingsAdmin.inlines:
        options = {'extra': 0, 'can_delete': True, 'fk_name': inline.fk_name,
                   'fields': inline.fields}
        if hasattr(inline, 'max_num') and inline.max_num is not None:
            options.update(max_num=inline.max_num, validate_max=True)
        FormSet = inlineformset_factory(SiteSettings, inline.model, **options)
        prefix = FormSet.get_default_prefix()
        # An omitted section keeps its existing rows. Never interpret missing
        # management data as a request to empty or delete the section.
        submitted = data is not None and any(key.startswith(prefix + '-') for key in data)
        submitted = submitted or (files is not None and any(key.startswith(prefix + '-') for key in files))
        formset = FormSet(data=data if submitted else None, files=files if submitted else None, instance=instance,
                          queryset=inline.model.objects.filter(postavke=instance).order_by('redoslijed', 'pk'))
        style_form(formset.management_form)
        for form in formset.forms:
            style_form(form)
        groups.append({'title': inline.verbose_name_plural, 'formset': formset,
                       'empty_form': style_form(formset.empty_form), 'submitted': submitted})
    return groups


@login_required(login_url='login')
@user_passes_test(can_edit_settings, login_url='login')
@require_http_methods(['GET', 'POST'])
@xframe_options_sameorigin
def staff_site_settings(request):
    site = SiteSettings.objects.get_or_create(pk=1)[0]
    data = request.POST if request.method == 'POST' else None
    files = request.FILES if request.method == 'POST' else None
    form = make_settings_form(site, data, files)
    inline_groups = make_inline_groups(site, data, files)
    if request.method == 'POST':
        valid = form.is_valid()
        for group in inline_groups:
            if group['submitted']:
                valid = group['formset'].is_valid() and valid
        if valid:
            with transaction.atomic():
                form.save()
                for group in inline_groups:
                    if group['submitted']:
                        group['formset'].save()
            messages.success(request, 'Podešavanja su sačuvana.')
            if request.headers.get('Accept') == 'application/json':
                return JsonResponse({'message': 'Podešavanja su sačuvana.', 'redirect': reverse('staff_site_settings')})
            return redirect('staff_site_settings')
    error_summary = []
    if request.method == 'POST':
        for name, errors in form.errors.items():
            label = form.fields[name].label if name in form.fields else 'Podešavanja'
            error_summary.extend(f'{label}: {error}' for error in errors)
        for group in inline_groups:
            if not group['submitted']:
                continue
            formset = group['formset']
            error_summary.extend(f"{group['title']}: {error}" for error in formset.non_form_errors())
            for index, row in enumerate(formset.forms, 1):
                if formset.can_delete and formset._should_delete_form(row):
                    continue
                for name, errors in row.errors.items():
                    label = row.fields[name].label if name in row.fields else 'Red'
                    error_summary.extend(f"{group['title']} — red {index}, {label}: {error}" for error in errors)
        if request.headers.get('Accept') == 'application/json':
            return JsonResponse({'errors': error_summary}, status=400)
    sections = []
    for title, options in SiteSettingsAdmin.fieldsets:
        fields = [form[name] for name in options['fields'] if name in form.fields]
        sections.append({'title': title, 'description': options.get('description', ''), 'fields': fields})
    return render(request, 'staff/settings.html', {
        'settings_form': form, 'settings_sections': sections, 'inline_groups': inline_groups,
        'settings_error_summary': error_summary,
    })


@login_required(login_url='login')
@user_passes_test(can_edit_settings, login_url='login')
@require_GET
def staff_settings_lookup(request):
    model = LOOKUP_MODELS.get(request.GET.get('model'))
    if model is None:
        return JsonResponse({'results': []}, status=400)
    query = request.GET.get('q', '').strip()[:100]
    objects = model.objects.all()
    if query:
        predicate = Q(naziv__icontains=query)
        if model is Product:
            predicate |= Q(sifra__icontains=query)
        objects = objects.filter(predicate)
    return JsonResponse({'results': [{'id': obj.pk, 'text': str(obj)}
                                     for obj in objects.order_by('naziv', 'pk')[:30]]})


@login_required(login_url='login')
@user_passes_test(can_edit_settings, login_url='login')
@require_GET
def staff_settings(request):
    return settings_workspace(request)


@login_required(login_url='login')
@user_passes_test(can_edit_settings, login_url='login')
@require_GET
def staff_b2b_workspace(request):
    return settings_workspace(request, b2b=True)


B2B_KEYS = ('b2bsettings', 'b2baccount', 'b2bsubmission', 'b2blive')
WMS_KEYS = ('warehousecustomer', 'warehouselocation', 'productwarehousemeta', 'warehousesynclog')
PRODUCT_KEYS = ('product', 'homefeaturedproduct', 'homenovoproduct', 'homebestsellerproduct', 'tag', 'category', 'brand')
PROMOTION_KEYS = ('akcija', 'upselloffer')
SETTINGS_KEYS = ('banner', 'userprofile', 'pageseo')


@login_required(login_url='login')
@user_passes_test(can_edit_settings, login_url='login')
@require_GET
def staff_loyalty_workspace(request):
    return render(request, 'staff/loyalty_workspace.html')


@login_required(login_url='login')
@user_passes_test(can_edit_settings, login_url='login')
@require_GET
def staff_wms_workspace(request):
    sections = [item for item in panel_model_sections(request) if item['key'] in WMS_KEYS]
    sections.sort(key=lambda item: WMS_KEYS.index(item['key']))
    return render(request, 'staff/wms_workspace.html', {'wms_sections': sections})


@login_required(login_url='login')
@user_passes_test(can_edit_settings, login_url='login')
@require_GET
def staff_scratch_workspace(request):
    return render(request, 'staff/scratch_workspace.html')


def panel_model_sections(request):
    from .panel_admin import panel_admin_site
    navigation = []
    for app in panel_admin_site.get_app_list(request):
        if app['app_label'] != 'EcommerceApp':
            continue
        for model in app['models']:
            key = model['object_name'].lower()
            from .panel_modules import model_module, module_locked
            navigation.append({
                'key': key, 'label': 'Novo u ponudi' if key == 'homenovoproduct' else 'Sretni Greb-Greb' if key == 'scratchprize' else model['name'],
                'module_locked': module_locked(model_module(key)),
                'url': model.get('admin_url'), 'add_url': model.get('add_url'),
                'workspace_url': reverse('staff_scratch_workspace') if key == 'scratchprize' else reverse('staff_model_workspace', args=[key]),
            })
    navigation.sort(key=lambda item: str(item['label']).casefold())
    return navigation


@login_required(login_url='login')
@user_passes_test(can_edit_settings, login_url='login')
@require_GET
def staff_model_workspace(request, section):
    from django.http import Http404
    if not any(item['key'] == section and section not in B2B_KEYS for item in panel_model_sections(request)):
        raise Http404
    return settings_workspace(request, section=section)


def settings_workspace(request, b2b=False, section=None):
    navigation = panel_model_sections(request)
    if b2b:
        navigation = [item for item in navigation if item['key'] in B2B_KEYS]
        navigation.sort(key=lambda item: B2B_KEYS.index(item['key']))
    elif section:
        if section == 'product':
            navigation = [item for item in navigation if item['key'] in PRODUCT_KEYS]
            navigation.sort(key=lambda item: PRODUCT_KEYS.index(item['key']))
        elif section == 'akcija':
            navigation = [item for item in navigation if item['key'] in PROMOTION_KEYS]
            navigation.sort(key=lambda item: PROMOTION_KEYS.index(item['key']))
        else:
            navigation = [item for item in navigation if item['key'] == section]
    else:
        navigation = [item for item in navigation if item['key'] in SETTINGS_KEYS]
        navigation.sort(key=lambda item: SETTINGS_KEYS.index(item['key']))
        navigation.insert(0, {'key': 'site', 'label': 'Podešavanja sajta',
                              'url': reverse('staff_site_settings'), 'add_url': None})
    selected = next((item for item in navigation if item['key'] == request.GET.get('sekcija')), navigation[0])
    return render(request, 'staff/settings_workspace.html', {
        'settings_navigation': navigation, 'selected_settings': selected,
        'workspace_title': 'B2B' if b2b else navigation[0]['label'] if section else 'Podešavanja',
        'workspace_group': 'B2B' if b2b else 'Artikli' if section == 'product' else 'Akcije' if section == 'akcija' else 'Webshop',
        'workspace_url': reverse('staff_model_workspace', args=[section]) if section else reverse('staff_b2b_workspace' if b2b else 'staff_settings'),
    })
