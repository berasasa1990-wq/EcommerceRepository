import logging
from email.utils import formataddr

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.utils import timezone

from .models import SiteSettings
from .pricing import sazetak_iz_narudzbe

logger = logging.getLogger(__name__)


class EmailNotConfiguredError(Exception):
    pass


def _ensure_email_configured():
    if not settings.EMAIL_HOST_PASSWORD:
        raise EmailNotConfiguredError(
            'EMAIL_APP_PASSWORD (Proton SMTP token) nije postavljen u okruženju.',
        )
    if not settings.EMAIL_HOST_USER:
        raise EmailNotConfiguredError('EMAIL_HOST_USER nije postavljen u okruženju.')
    if not settings.ORDER_NOTIFICATION_EMAIL:
        raise EmailNotConfiguredError('ORDER_NOTIFICATION_EMAIL nije postavljen u okruženju.')


def _from_email():
    return formataddr(('opremazaribolov.ba', settings.DEFAULT_FROM_EMAIL))


def _admin_order_text(order):
    lines = [
        f'Nova narudžba #{order.broj}',
        '',
        f'Ime i prezime: {order.ime_prezime}',
        f'Email: {order.email}',
        f'Telefon: {order.telefon}',
        f'Adresa: {order.adresa}',
        f'Grad: {order.grad}',
    ]
    if order.postanski_broj:
        lines.append(f'Poštanski broj: {order.postanski_broj}')
    if order.napomena:
        lines.append(f'Napomena: {order.napomena}')
    lines.extend(['', 'Stavke:', ''])
    for item in order.stavke.all():
        lines.append(
            f'- {item.puni_naziv} (šifra: {item.sifra or "—"}) × {item.kolicina} = {item.ukupno} KM',
        )
    summary = sazetak_iz_narudzbe(order)
    dostava_tekst = 'Besplatno' if order.dostava == 0 else f'{order.dostava} KM'
    lines.extend([
        '',
        f'Iznos bez PDV-a: {summary["pdv_artikli"]["bez_pdv"]} KM',
        f'PDV (17%): {summary["pdv_artikli"]["pdv"]} KM',
        f'Iznos sa PDV-om: {order.medjuzbir} KM',
    ])
    if order.popust:
        lines.append(f'Popust: -{order.popust} KM')
    lines.extend([
        f'{order.dostava_naziv}: {dostava_tekst}',
        f'Ukupno za plaćanje: {order.ukupno} KM',
    ])
    return '\n'.join(lines)


def _email_context(order, show_warranty=False):
    site_settings = SiteSettings.load()
    logo_url = None
    if site_settings.logo:
        logo_url = f'{settings.SITE_URL}{site_settings.logo.url}'

    return {
        'order': order,
        'summary': sazetak_iz_narudzbe(order),
        'datum': timezone.localtime(order.kreirana).strftime('%d.%m.%Y.'),
        'vrijeme': timezone.localtime(order.kreirana).strftime('%H:%M'),
        'site_name': 'opremazaribolov.ba',
        'site_url': settings.SITE_URL,
        'logo_url': logo_url,
        'store_email': settings.STORE_EMAIL,
        'store_phone': settings.STORE_PHONE,
        'show_warranty': show_warranty,
    }


def _render_order_html(order, show_warranty=False):
    return render_to_string(
        'emails/order_customer.html',
        _email_context(order, show_warranty=show_warranty),
    )


def send_admin_order_notification(order):
    """Obavijest trgovini — uvijek na ORDER_NOTIFICATION_EMAIL."""
    _ensure_email_configured()

    recipient = settings.ORDER_NOTIFICATION_EMAIL
    admin_mail = EmailMultiAlternatives(
        subject=f'Nova narudžba #{order.broj} — opremazaribolov.ba',
        body=_admin_order_text(order),
        from_email=_from_email(),
        to=[recipient],
        reply_to=[order.email],
    )
    admin_mail.attach_alternative(_render_order_html(order, show_warranty=True), 'text/html')
    admin_mail.send(fail_silently=False)
    logger.info(
        'Admin obavijest za narudžbu #%s poslana na %s (SMTP: %s)',
        order.broj,
        recipient,
        settings.EMAIL_HOST,
    )


def send_customer_order_confirmation(order):
    """Potvrda kupcu na email iz narudžbe."""
    _ensure_email_configured()

    customer_text = (
        f'Hvala na narudžbi #{order.broj}.\n\n'
        f'Ukupno za plaćanje: {order.ukupno} KM\n\n'
        f'U prilogu emaila nalazi se potvrda vaše narudžbe.\n'
    )
    customer_mail = EmailMultiAlternatives(
        subject=f'Potvrda narudžbe #{order.broj} — opremazaribolov.ba',
        body=customer_text,
        from_email=_from_email(),
        to=[order.email],
        reply_to=[settings.ORDER_NOTIFICATION_EMAIL],
    )
    customer_mail.attach_alternative(_render_order_html(order, show_warranty=False), 'text/html')
    customer_mail.send(fail_silently=False)
    logger.info(
        'Potvrda narudžbe #%s poslana kupcu na %s',
        order.broj,
        order.email,
    )


def send_order_emails(order):
    """Prvo obavijest trgovini, zatim potvrda kupcu."""
    send_admin_order_notification(order)
    try:
        send_customer_order_confirmation(order)
    except Exception:
        logger.exception(
            'Potvrda kupcu za narudžbu #%s nije poslana, admin obavijest je poslana.',
            order.broj,
        )
        raise