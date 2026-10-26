from django.conf import settings
from .registry import MODULES, IMPLEMENTED
from .services import license_details, module_enabled
from .credentials import centrally_managed, installation_id
from .models import LicenseState
from django.utils import timezone


def licensing_context(request):
    if not request.path.startswith('/panel') or not request.user.is_authenticated or not request.user.is_staff:
        return {}
    details = license_details()
    managed = centrally_managed()
    state = LicenseState.objects.filter(installation_id=installation_id()).first()
    status = ('Aktivna' if timezone.now() < details[1] else 'Grace period') if details and timezone.now() < details[2] else 'Nema važeće licence'
    from bera_control.models import ModuleActivation
    from bera_control.services import expiry_status
    activations = {item.module: item for item in ModuleActivation.objects.all()}
    modules = []
    now = timezone.now()
    for code, value in MODULES.items():
        activation = activations.get(code)
        expires = activation.expires_at if activation else details[1] if details and (managed or code in settings.BERA_LICENSE_ENFORCED_MODULES) else None
        modules.append(dict(code=code, name=value[0], description=value[1],
            enabled=module_enabled(code), implemented=code in IMPLEMENTED,
            enforced=(managed or code in settings.BERA_LICENSE_ENFORCED_MODULES),
            **expiry_status(expires, now)))
    return {'bera_license_state': state, 'bera_license_status': status, 'bera_license_connected': managed,
            'bera_modules': modules}
