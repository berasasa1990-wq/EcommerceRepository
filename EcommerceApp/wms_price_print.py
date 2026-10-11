"""WMS entry points for the existing warehouse price-label functions."""
from django.urls import reverse
from .bera_panel_modules import module_locked
from django.core.exceptions import PermissionDenied
from . import views_magacin


def price_print(request, mode='izbor'):
    if module_locked('stampa_cijena'):
        raise PermissionDenied
    handlers = {'izbor': views_magacin.magacin_stampa_cijena,
                'ista': views_magacin.magacin_stampa_cijena_ista,
                'razlicite': views_magacin.magacin_stampa_cijena_razlicite,
                'print': views_magacin.magacin_stampa_cijena_print}
    response = handlers[mode](request)
    if response.has_header('Location'):
        for suffix in ['_razlicite', '_ista', '']:
            old = reverse('staff_magacin_stampa_cijena'+suffix)
            if response['Location'].startswith(old):
                response['Location'] = reverse('staff_wms_stampa_cijena'+suffix)+response['Location'][len(old):]
                break
    return response
