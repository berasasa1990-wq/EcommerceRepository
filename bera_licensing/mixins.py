from .decorators import module_required


class ModuleRequiredMixin:
    module_code = None

    def dispatch(self, request, *args, **kwargs):
        return module_required(self.module_code)(super().dispatch)(request, *args, **kwargs)
