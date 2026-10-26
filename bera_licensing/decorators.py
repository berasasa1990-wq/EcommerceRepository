from functools import wraps
from django.http import JsonResponse
from .services import module_enabled


def module_required(code):
    def decorate(view):
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            if not module_enabled(code):
                return JsonResponse({'error': 'module_locked', 'module': code}, status=403)
            return view(request, *args, **kwargs)
        return wrapped
    return decorate
