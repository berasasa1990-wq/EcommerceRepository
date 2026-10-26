from django.contrib import messages
from django.shortcuts import redirect
from django.views.decorators.http import require_POST
from shop.views import panel_required
from .models import ActivationRequest
from .registry import MODULES


@panel_required
@require_POST
def request_activation(request):
    code = request.POST.get('module')
    if code in MODULES:
        # Administrative queue only; no entitlement fields are changed.
        if not ActivationRequest.objects.filter(module=code, requested_by=request.user).exists():
            ActivationRequest.objects.create(module=code, requested_by=request.user)
        messages.success(request, 'Zahtjev je evidentiran. Za obradu kontaktirajte BERA IT na https://bera-it.com.')
    return redirect('panel_section', section='moduli')


from django.views.decorators.debug import sensitive_post_parameters, sensitive_variables
from .services import activate_license, sync_license
from .security import LicenseError


@sensitive_post_parameters('activation_code')
@sensitive_variables()
@panel_required
@require_POST
def activate(request):
    try:
        changed = activate_license(request.POST.get('installation_id', ''), request.POST.get('activation_code', ''))
        messages.success(request, 'Aktivacija sačuvana. Licenca sinhronizirana.' if changed else 'Aktivacija sačuvana. Sinhronizacija je u toku.')
    except LicenseError:
        messages.error(request, 'Aktivacija ili početna provjera nije uspjela. Provjerite konfiguraciju i kod; ako je aktivacija sačuvana, koristite provjeru licence.')
    return redirect('panel_section', section='moduli')


@panel_required
@require_POST
def sync_now(request):
    try:
        changed = sync_license(force=True)
        messages.success(request, 'Licenca sinhronizirana.' if changed else 'Sinhronizacija je već u toku.')
    except LicenseError:
        messages.error(request, 'Provjera licence nije uspjela. Provjerite serversku konfiguraciju i vezu.')
    return redirect('panel_section', section='moduli')


@sensitive_post_parameters('activation_code')
@panel_required
@require_POST
def activate_module_code(request):
    from bera_control.services import redeem_module_code
    from bera_control.models import AuditEvent
    module = request.POST.get('module', '')
    if redeem_module_code(module, request.POST.get('activation_code', ''), request.META.get('REMOTE_ADDR') or None):
        messages.success(request, 'Modul je uspješno aktiviran. Možete ga odmah koristiti.')
    else:
        AuditEvent.objects.create(action='module_code_rejected', address=request.META.get('REMOTE_ADDR') or None)
        messages.error(request, 'Kod nije važeći, već je iskorišten ili modul nije odobren. Provjerite kod ili kontaktirajte BERA podršku.')
    return redirect('panel_section', section='moduli')
