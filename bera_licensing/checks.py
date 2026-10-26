from django.conf import settings
from django.core.checks import Error, register
from .registry import MODULES


@register()
def check_licensing(app_configs, **kwargs):
    unknown = set(settings.BERA_LICENSE_ENFORCED_MODULES) - MODULES.keys()
    if unknown:
        return [Error('Nepoznat kod u BERA_LICENSE_ENFORCED_MODULES.', id='bera_licensing.E001')]
    return []
