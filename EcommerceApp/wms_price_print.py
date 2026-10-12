"""WMS entry points for the existing warehouse price-label functions."""
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
    return response
