"""Prijava na obavijest kad artikal ponovo bude na stanju."""

import logging

from django.core.exceptions import ValidationError
from django.core.signing import dumps, loads
from django.core.validators import validate_email
from django.db import IntegrityError
from django.utils import timezone

from .models import Product, StockNotify

logger = logging.getLogger(__name__)

_EMAIL_TOKEN_SALT = 'stock-notify-mail'


def email_action_token(email, product_id):
    return dumps({'e': (email or '').strip().lower(), 'p': int(product_id)}, salt=_EMAIL_TOKEN_SALT)


def parse_email_action_token(token, max_age=60 * 60 * 24 * 400):
    data = loads(token, salt=_EMAIL_TOKEN_SALT, max_age=max_age)
    return (data.get('e') or '').strip().lower(), int(data.get('p') or 0)


def subscribe(*, product, email, user=None):
    email = (email or '').strip().lower()
    if not email:
        raise ValidationError('Unesi email.')
    validate_email(email)
    if product.na_stanju:
        return 'in_stock'
    pending = StockNotify.objects.filter(
        product=product, email=email, notified_at__isnull=True,
    ).first()
    if pending:
        if user and not pending.user_id:
            pending.user = user
            pending.save(update_fields=['user'])
        return 'exists'
    try:
        StockNotify.objects.create(product=product, email=email, user=user)
    except IntegrityError:
        return 'exists'
    return 'created'


def notify_back_in_stock(product_id):
    product = Product.objects.select_related('brend').filter(pk=product_id).first()
    if product is None or not product.na_stanju:
        return 0
    pending = list(
        StockNotify.objects.filter(product_id=product_id, notified_at__isnull=True)
    )
    if not pending:
        return 0
    from .emails import send_stock_back_email

    sent = 0
    now = timezone.now()
    for row in pending:
        try:
            send_stock_back_email(to_email=row.email, product=product)
            row.notified_at = now
            row.save(update_fields=['notified_at'])
            sent += 1
        except Exception:
            logger.exception('Obavijest o stanju nije poslana na %s', row.email)
    return sent


def unsubscribe_email(email):
    """Ukini sve čekajuće obavijesti za email. Vraća broj obrisanih."""
    email = (email or '').strip().lower()
    if not email:
        return 0
    pending = StockNotify.objects.filter(email=email, notified_at__isnull=True)
    count = pending.count()
    pending.delete()
    return count
