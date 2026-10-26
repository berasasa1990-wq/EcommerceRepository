"""Use only on the main Django server after verifying a payment event."""
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import json
from django.conf import settings
from django.core import signing


def set_loyalty_module(*, enabled, revision, paid_until=None):
    """revision must increase per shop; retain the same revision when retrying an event.

    Use your billing record's monotonic version, persisted when payment state changes.
    paid_until is an aware datetime or None. Configure the shop URL on the server.
    """
    token = signing.dumps({
        'aud': 'bera-module-control', 'module': 'loyalty',
        'enabled': enabled, 'revision': revision,
        'paid_until': paid_until.isoformat() if paid_until is not None else None,
    }, key=settings.SHOP_MODULE_CONTROL_KEY, salt='bera-module-control-v1')
    request = Request(settings.SHOP_MODULE_CONTROL_URL,
                      data=urlencode({'token': token}).encode(), method='POST')
    with urlopen(request, timeout=15) as response:
        return json.load(response)
