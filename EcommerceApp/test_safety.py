"""Defense in depth installed ONLY by settings_render_test."""
from django.http import HttpResponse
from .apps import EcommerceappConfig


def install_outbound_guards():
    import requests
    from urllib import request
    from botocore.httpsession import URLLib3Session

    def deny_requests(*args, **kwargs):
        raise requests.RequestException('Odlazne HTTP integracije su isključene na testnom sajtu.')

    def deny_other(*args, **kwargs):
        raise RuntimeError('Odlazne HTTP/R2 integracije su isključene na testnom sajtu.')

    requests.sessions.Session.send = deny_requests
    request.OpenerDirector.open = deny_other
    URLLib3Session.send = deny_other
    # Commands launched by schedulers or from call_command must obey isolation too.
    from django.core.management.base import BaseCommand, CommandError
    original = BaseCommand.execute
    if not getattr(original, '_test_guard', False):
        def execute(command, *args, **kwargs):
            module = type(command).__module__
            name = module.rsplit('.', 1)[-1]
            if module.startswith('EcommerceApp.management.commands.') and (name.startswith('mungos_') or name in {'backup_r2', 'backup_db', 'sync_to_render', 'sync_media_to_r2', 'test_r2_upload', 'test_order_email'}):
                raise CommandError('Ovaj automatski/integracijski zadatak je isključen u testnom režimu.')
            return original(command, *args, **kwargs)
        execute._test_guard = True
        BaseCommand.execute = execute


class TestEcommerceConfig(EcommerceappConfig):
    def ready(self):
        install_outbound_guards()
        super().ready()


class TestSafetyMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path.startswith(('/placanje/monri/', '/api/sync/')):
            response = HttpResponse('Plaćanja i webhookovi su isključeni na testnom sajtu.', status=403)
        elif request.path == '/robots.txt':
            response = HttpResponse('User-agent: *\nDisallow: /\n', content_type='text/plain')
        else:
            response = self.get_response(request)
        response['X-Robots-Tag'] = 'noindex, nofollow, noarchive'
        return response
