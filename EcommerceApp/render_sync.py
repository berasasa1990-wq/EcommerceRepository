import logging

import requests
from django.conf import settings

from .sync_handlers import serialize_korisnik, serialize_narudzba

logger = logging.getLogger(__name__)


def sync_je_aktivan():
    return bool(
        getattr(settings, 'SYNC_ENABLED', False)
        and getattr(settings, 'SYNC_REMOTE_URL', '')
        and getattr(settings, 'SYNC_API_KEY', '')
    )


def _headers():
    return {
        'Content-Type': 'application/json',
        'X-Sync-Key': settings.SYNC_API_KEY,
    }


def _post(endpoint, payload):
    if not sync_je_aktivan():
        logger.warning('Sync preskočen za %s — SYNC_ENABLED=%s, URL=%s, KEY=%s',
                       endpoint,
                       getattr(settings, 'SYNC_ENABLED', False),
                       getattr(settings, 'SYNC_REMOTE_URL', ''),
                       'set' if getattr(settings, 'SYNC_API_KEY', '') else 'missing')
        return None
    url = f'{settings.SYNC_REMOTE_URL.rstrip("/")}{endpoint}'
    try:
        response = requests.post(
            url,
            json=payload,
            headers=_headers(),
            timeout=getattr(settings, 'SYNC_TIMEOUT', 15),
        )
        response.raise_for_status()
        data = response.json()
        if isinstance(data, dict) and data.get('ok') is False:
            logger.error('Sync odgovor nije OK (%s): %s', endpoint, data)
        else:
            logger.info('Sync uspješan (%s): %s', endpoint, data if isinstance(data, dict) else 'OK')
        return data
    except Exception as exc:
        logger.exception('Sync prema Renderu nije uspio (%s)', endpoint)
        return None


def sync_korisnik(user):
    if not user or not user.email:
        logger.info("sync_korisnik preskočen: nema user ili email")
        return None
    logger.info("Šaljem sync_korisnik za %s na %s", user.email, settings.SYNC_REMOTE_URL)
    return _post('/api/sync/korisnik/', serialize_korisnik(user))


def sync_narudzba(order):
    order = order.__class__.objects.prefetch_related('stavke').select_related('korisnik').get(pk=order.pk)
    logger.info("Šaljem sync_narudzba #%s (email=%s, korisnik_id=%s) na %s", order.broj, order.email, order.korisnik_id, settings.SYNC_REMOTE_URL)
    result = _post('/api/sync/narudzba/', serialize_narudzba(order))
    return result