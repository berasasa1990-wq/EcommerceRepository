from datetime import timedelta
from functools import wraps
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.hashers import check_password
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import F
from django.http import Http404
from django.middleware.csrf import rotate_token
from django.shortcuts import render, redirect
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_POST, require_http_methods
from bera_licensing.registry import IMPLEMENTED
from bera_licensing.services import module_enabled
from .forms import BootstrapForm, LoginForm, ActivationDurationForm
from .models import ControlState, ModuleSwitch, AuditEvent, ModuleActivation
from .services import initialize, IDLE_SECONDS, issue_module_code, display_activation_code, expiry_status

MODULES = {
    'chatbot': ('ChatBot', 'Asistent za kupovinu, preporuke artikala i razgovor s kupcima.'),
    'loyalty': ('Loyalty program', 'Kartice kupaca, bodovi i pogodnosti za vjerne kupce.'),
    'article_plus': ('Akcija – Ponuda +', 'Plaćeni modul: popust na dodatni artikal uz kupovinu prvog.'),
    'quantity': ('Količinski popust', 'Plaćeni modul: niže cijene za kupovinu više komada.'),
    'bundle': ('Bundle set', 'Plaćeni modul: više artikala zajedno uz popust na set.'),
    'scratch_cards': ('Greb-greb', 'Promotivne nagradne kartice.'),
    'gift_vouchers': ('Poklon vaučeri', 'Digitalni pokloni i vaučeri.'),
    'newsletter': ('Newsletter', 'Kampanje i obavijesti kupcima.'),
    'b2b': ('B2B veleprodaja', 'Poslovni kupci i posebni uslovi prodaje.'),
    'analytics': ('Napredna analitika', 'Detaljni izvještaji i pregled prodaje.'),
}


def address(request):
    # Do not trust client-supplied forwarding headers.
    return request.META.get('REMOTE_ADDR') or None


def authenticated(request):
    auth = request.session.get('bera_control', {})
    now = timezone.now().timestamp()
    state = ControlState.objects.get(pk=1)
    if not state.initialized_at or auth.get('generation') != state.generation or not 0 <= now - auth.get('seen', 0) < IDLE_SECONDS:
        request.session.pop('bera_control', None)
        return False
    request.session['bera_control'] = {'generation': state.generation, 'seen': now}
    return True


def owner_required(view):
    @wraps(view)
    @never_cache
    def wrapped(request, *args, **kwargs):
        if not authenticated(request):
            return redirect('bera_control:login')
        return view(request, *args, **kwargs)
    return wrapped


@never_cache
@sensitive_post_parameters('password')
@require_http_methods(['GET', 'POST'])
def login(request):
    if authenticated(request):
        return redirect('bera_control:dashboard')
    state = ControlState.objects.get(pk=1)
    form = LoginForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        now = timezone.now()
        with transaction.atomic():
            # Write first to serialize attempts on SQLite as well as PostgreSQL.
            ControlState.objects.filter(pk=1).update(failures=F('failures'))
            state = ControlState.objects.get(pk=1)
            locked = state.locked_until and state.locked_until > now
            valid = check_password(form.cleaned_data['password'], state.password_hash) if state.initialized_at and not locked else False
            if locked:
                AuditEvent.objects.create(action='login_locked', address=address(request))
            elif valid:
                ControlState.objects.filter(pk=1).update(failures=0, locked_until=None)
                AuditEvent.objects.create(action='login_success', address=address(request))
            else:
                failures = (0 if state.locked_until else state.failures) + 1
                ControlState.objects.filter(pk=1).update(failures=failures,
                    locked_until=now + timedelta(minutes=15) if failures >= 5 else None)
                AuditEvent.objects.create(action='login_failed', address=address(request))
        if valid:
            request.session.cycle_key()
            rotate_token(request)
            request.session['bera_control'] = {'generation': state.generation, 'seen': now.timestamp()}
            return redirect('bera_control:dashboard')
        form.add_error(None, 'Prijava nije uspjela. Nakon 5 pokušaja pristup se zaključava na 15 minuta.')
    return render(request, 'bera_control/auth.html', {'form': form, 'title': 'Prijava'})


@never_cache
@sensitive_post_parameters('password', 'confirm', 'token')
@require_http_methods(['GET', 'POST'])
def setup(request):
    if ControlState.objects.get(pk=1).initialized_at:
        raise Http404
    form = BootstrapForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        try:
            initialize(form.cleaned_data['token'], form.cleaned_data['password'], address(request))
        except PermissionDenied:
            AuditEvent.objects.create(action='bootstrap_rejected', address=address(request))
            raise
        return redirect('bera_control:login')
    return render(request, 'bera_control/auth.html', {'form': form, 'title': 'Sigurno prvo postavljanje', 'setup': True})


@owner_required
def dashboard(request):
    switches = {item.code: item.enabled for item in ModuleSwitch.objects.all()}
    activations = {item.module: item for item in ModuleActivation.objects.all()}
    modules = []
    for code, (name, description) in MODULES.items():
        activation = activations.get(code)
        modules.append(dict(code=code, name=name, description=description, connected=code in IMPLEMENTED,
            local=switches.get(code, True), active=module_enabled(code),
            license_code=display_activation_code(activation) if activation else '',
            pending=bool(activation and not activation.activated_at), has_code=activation is not None,
            duration_days=activation.duration_days if activation else None,
            **expiry_status(activation.expires_at if activation else None)))
    return render(request, 'bera_control/dashboard.html', {
        'modules': modules, 'events': AuditEvent.objects.all()[:15],
        'shop_name': settings.BERA_CONTROL_SHOP_NAME, 'domain': request.get_host(),
    })


@owner_required
@require_POST
def toggle(request, code):
    if code not in MODULES or code not in IMPLEMENTED:
        raise Http404
    if request.POST.get('enabled') not in ('on', 'off'):
        raise PermissionDenied('Neispravno stanje.')
    enabled = request.POST['enabled'] == 'on'
    duration_form = ActivationDurationForm(request.POST)
    if not duration_form.is_valid():
        messages.error(request, 'Trajanje mora biti od 1 do 36500 dana ili prazno za neograničeno.')
        return redirect('bera_control:dashboard')
    with transaction.atomic():
        ControlState.objects.filter(pk=1).update(failures=F('failures'))
        module, _ = ModuleSwitch.objects.get_or_create(code=code)
        previous = module.enabled
        module.enabled = enabled
        module.save()
        if enabled and not ModuleActivation.objects.filter(module=code).exists():
            issue_module_code(code, duration_form.cleaned_data['duration_days'])
        AuditEvent.objects.create(action='module_changed', module=code, previous=previous, current=enabled, address=address(request))
    messages.success(request, 'Modul je odobren. Pošaljite kupcu prikazani kod za aktivaciju.' if enabled else 'Modul je isključen. Podaci su sačuvani.')
    return redirect('bera_control:dashboard')


@owner_required
@require_POST
def logout(request):
    request.session.pop('bera_control', None)
    request.session.cycle_key()
    AuditEvent.objects.create(action='logout', address=address(request))
    return redirect('bera_control:login')


@owner_required
@require_POST
def generate_code(request, code):
    if code not in MODULES or code not in IMPLEMENTED:
        raise Http404
    form = ActivationDurationForm(request.POST)
    if not form.is_valid():
        messages.error(request, 'Trajanje mora biti od 1 do 36500 dana ili prazno za neograničeno.')
        return redirect('bera_control:dashboard')
    with transaction.atomic():
        ControlState.objects.filter(pk=1).update(failures=F('failures'))
        ModuleSwitch.objects.update_or_create(code=code, defaults={'enabled': True})
        issue_module_code(code, form.cleaned_data['duration_days'])
        AuditEvent.objects.create(action='module_code_issued', module=code, address=address(request))
    messages.success(request, 'Novi kod je spreman za slanje kupcu. Prethodni kod više ne vrijedi; modul čeka aktivaciju.')
    return redirect('bera_control:dashboard')


@owner_required
@require_POST
def set_duration(request, code):
    if code not in MODULES or code not in IMPLEMENTED:
        raise Http404
    form = ActivationDurationForm(request.POST)
    if not form.is_valid():
        messages.error(request, 'Trajanje mora biti od 1 do 36500 dana ili prazno za neograničeno.')
        return redirect('bera_control:dashboard')
    with transaction.atomic():
        ControlState.objects.filter(pk=1).update(failures=F('failures'))
        activation = ModuleActivation.objects.filter(module=code).first()
        if activation is None:
            raise Http404
        days = form.cleaned_data['duration_days']
        activation.duration_days = days
        activation.expires_at = timezone.now() + timedelta(days=days) if activation.activated_at and days else None
        activation.save(update_fields=['duration_days', 'expires_at'])
        AuditEvent.objects.create(action='module_duration_changed', module=code, address=address(request))
    messages.success(request, 'Trajanje je sačuvano. Za aktivirani modul novi rok počinje danas; za novi kod počinje kupčevom aktivacijom.')
    return redirect('bera_control:dashboard')
