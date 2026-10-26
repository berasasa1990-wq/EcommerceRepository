from django.apps import AppConfig


class BeraLicensingConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'bera_licensing'
    verbose_name = 'BERA IT licence'

    def ready(self):
        from . import checks  # noqa: F401
