from django.utils import timezone
from .models import SiteModule


def legacy_loyalty_enabled():
    module = SiteModule.objects.filter(code='loyalty').first()
    return module is None or (module.enabled and (module.paid_until is None or module.paid_until > timezone.now()))


def loyalty_enabled():
    from bera_licensing.services import module_enabled
    return module_enabled("loyalty")
