import logging
from email.utils import formataddr
from types import SimpleNamespace

from django.conf import settings
from django.contrib.auth.models import User
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.utils import timezone

from .models import MarketingEmailCampaign, MarketingSubscriber, Order, SiteSettings
from .pricing import pripremi_stavke_za_racun, sazetak_iz_narudzbe

logger = logging.getLogger(__name__)

MARKETING_TEST_EMAIL = 'narudzbe@opremazaribolov.ba'


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


def get_order_email_context(order):
    """Kontekst za prikaz potvrde narudžbe (email ili staff pregled)."""
    return _email_context(order)


def _email_context(order):
    site_settings = SiteSettings.load()
    logo_url = None
    if site_settings.logo:
        logo_url = f'{settings.SITE_URL}{site_settings.logo.url}'

    created = timezone.localtime(order.kreirana)

    return {
        'order': order,
        'summary': sazetak_iz_narudzbe(order),
        'stavke': pripremi_stavke_za_racun(order),
        'datum': created.strftime('%d.%m.%Y.'),
        'datum_kratko': f'{created.day}. {created.month}. {created.year}.',
        'vrijeme': created.strftime('%H:%M'),
        'site_name': 'opremazaribolov.ba',
        'site_url': settings.SITE_URL,
        'logo_url': logo_url,
        'store_email': settings.STORE_EMAIL,
        'store_phone': settings.STORE_PHONE,
        'dostava_naziv': site_settings.dostava_naziv,
        'politika_garancija': site_settings.politika_garancija,
    }


def _render_order_html(order):
    return render_to_string(
        'emails/order_customer.html',
        _email_context(order),
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
    admin_mail.attach_alternative(_render_order_html(order), 'text/html')
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
        f'U prilogu emaila nalazi se potvrda narudžbe i garantni list.\n'
    )
    customer_mail = EmailMultiAlternatives(
        subject=f'Potvrda narudžbe #{order.broj} — opremazaribolov.ba',
        body=customer_text,
        from_email=_from_email(),
        to=[order.email],
        reply_to=[settings.ORDER_NOTIFICATION_EMAIL],
    )
    customer_mail.attach_alternative(_render_order_html(order), 'text/html')
    customer_mail.send(fail_silently=False)
    logger.info(
        'Potvrda narudžbe #%s poslana kupcu na %s',
        order.broj,
        order.email,
    )


def send_chat_notification(conversation, message):
    """Obavijest trgovini o novoj chat poruci kad niko od osoblja nije na sajtu."""
    _ensure_email_configured()

    recipient = settings.ORDER_NOTIFICATION_EMAIL
    name = conversation.display_name
    email = conversation.display_email or '—'
    registered = 'Da (registrovan korisnik)' if conversation.is_registered else 'Ne (gost)'
    created = timezone.localtime(message.created_at)

    body_lines = [
        'Nova poruka u chatu na opremazaribolov.ba',
        '',
        f'Ime: {name}',
        f'Email: {email}',
        f'Registrovan: {registered}',
        f'Vrijeme: {created.strftime("%d.%m.%Y. %H:%M")}',
        '',
        'Poruka:',
        message.body,
        '',
        f'Prijavite se na sajt kao administrator da odgovorite: {settings.SITE_URL}/',
    ]
    reply_to = [email] if email and email != '—' and '@' in email else None

    mail = EmailMultiAlternatives(
        subject=f'Chat poruka — {name}',
        body='\n'.join(body_lines),
        from_email=_from_email(),
        to=[recipient],
        reply_to=reply_to,
    )
    html_body = render_to_string('emails/chat_notification.html', {
        'conversation': conversation,
        'message': message,
        'name': name,
        'email': email,
        'registered': registered,
        'created': created,
        'site_url': settings.SITE_URL,
    })
    mail.attach_alternative(html_body, 'text/html')
    mail.send(fail_silently=False)
    logger.info('Chat obavijest poslana za razgovor #%s (%s)', conversation.pk, email)


def _marketing_display_name(email, name=''):
    cleaned = (name or '').strip()
    if cleaned:
        return cleaned
    return email.split('@', 1)[0]


def _registered_marketing_emails():
    seen = set()
    for user in User.objects.filter(is_active=True).exclude(email='').order_by('id'):
        email = (user.email or '').strip().lower()
        if not email or '@' not in email:
            continue
        if email in seen:
            continue
        seen.add(email)
    return seen


def marketing_recipient_users():
    """Zadržano radi kompatibilnosti — vraća samo registrovane korisnike."""
    seen = set()
    recipients = []
    for user in User.objects.filter(is_active=True).exclude(email='').order_by('id'):
        email = (user.email or '').strip()
        if not email or '@' not in email:
            continue
        key = email.lower()
        if key in seen:
            continue
        seen.add(key)
        recipients.append(user)
    return recipients


def marketing_recipients():
    seen = set()
    recipients = []
    for user in marketing_recipient_users():
        email = user.email.strip().lower()
        seen.add(email)
        recipients.append(SimpleNamespace(
            email=email,
            display_name=_marketing_display_name(email, user.first_name),
        ))
    for subscriber in MarketingSubscriber.objects.filter(aktivan=True).order_by('id'):
        email = subscriber.email.strip().lower()
        if not email or email in seen:
            continue
        seen.add(email)
        recipients.append(SimpleNamespace(
            email=email,
            display_name=_marketing_display_name(email, subscriber.ime),
        ))
    return recipients


def marketing_recipient_counts():
    registered = len(marketing_recipient_users())
    registered_emails = _registered_marketing_emails()
    subscribers = MarketingSubscriber.objects.filter(aktivan=True).exclude(
        email__in=registered_emails,
    ).count()
    return {
        'registered': registered,
        'subscribers': subscribers,
        'total': registered + subscribers,
    }


def bulk_import_marketing_subscribers(entries, *, added_by=None, source=MarketingSubscriber.Source.MANUAL):
    registered_emails = _registered_marketing_emails()
    existing = set(
        MarketingSubscriber.objects.values_list('email', flat=True),
    )
    added = 0
    skipped_registered = 0
    skipped_duplicate = 0
    invalid = 0
    for email, name in entries:
        normalized = (email or '').strip().lower()
        if not normalized or '@' not in normalized:
            invalid += 1
            continue
        if normalized in registered_emails:
            skipped_registered += 1
            continue
        if normalized in existing:
            skipped_duplicate += 1
            continue
        MarketingSubscriber.objects.create(
            email=normalized,
            ime=(name or '').strip()[:120],
            izvor=source,
            dodao=added_by,
        )
        existing.add(normalized)
        added += 1
    return {
        'added': added,
        'skipped_registered': skipped_registered,
        'skipped_duplicate': skipped_duplicate,
        'invalid': invalid,
    }


def import_marketing_subscribers_from_orders(*, added_by=None):
    seen_orders = set()
    entries = []
    for order in Order.objects.exclude(email='').order_by('-kreirana'):
        email = order.email.strip().lower()
        if not email or '@' not in email or email in seen_orders:
            continue
        seen_orders.add(email)
        entries.append((email, order.ime_prezime))
    return bulk_import_marketing_subscribers(
        entries,
        added_by=added_by,
        source=MarketingSubscriber.Source.ORDER,
    )


def _marketing_email_context(campaign, recipient):
    site_settings = SiteSettings.load()
    logo_url = None
    if site_settings.logo:
        logo_url = f'{settings.SITE_URL}{site_settings.logo.url}'
    banner_url = None
    if campaign.banner:
        banner_url = f'{settings.SITE_URL}{campaign.banner.url}'
    return {
        'campaign': campaign,
        'recipient': recipient,
        'display_name': recipient.display_name,
        'site_name': 'opremazaribolov.ba',
        'site_url': settings.SITE_URL,
        'logo_url': logo_url,
        'banner_url': banner_url,
        'cta_url': campaign.effective_cta_link,
        'store_email': settings.STORE_EMAIL,
        'store_phone': settings.STORE_PHONE,
    }


def _render_marketing_html(campaign, recipient):
    return render_to_string(
        'emails/marketing_campaign.html',
        _marketing_email_context(campaign, recipient),
    )


def _marketing_plain_text(campaign, recipient):
    lines = [
        f'Poštovani {recipient.display_name},',
        '',
        campaign.naslov,
    ]
    if campaign.uvod:
        lines.extend(['', campaign.uvod.strip()])
    lines.extend([
        '',
        f'{campaign.cta_tekst}: {campaign.effective_cta_link}',
        '',
        f'Pozdrav, tim {settings.SITE_URL.replace("https://", "").replace("http://", "")}',
    ])
    return '\n'.join(lines)


def send_marketing_campaign_email(campaign, recipient, *, subject_prefix=''):
    _ensure_email_configured()
    mail = EmailMultiAlternatives(
        subject=f'{subject_prefix}{campaign.naslov} — opremazaribolov.ba',
        body=_marketing_plain_text(campaign, recipient),
        from_email=_from_email(),
        to=[recipient.email.strip()],
        reply_to=[settings.ORDER_NOTIFICATION_EMAIL],
    )
    mail.attach_alternative(_render_marketing_html(campaign, recipient), 'text/html')
    mail.send(fail_silently=False)


def send_marketing_campaign_test_email(campaign):
    """Pošalji test verziju kampanje na internu adresu prije masovnog slanja."""
    if not campaign.banner:
        raise ValueError('Kampanja nema banner sliku.')
    test_recipient = SimpleNamespace(
        email=MARKETING_TEST_EMAIL,
        display_name='Test',
    )
    send_marketing_campaign_email(
        campaign,
        test_recipient,
        subject_prefix='[TEST] ',
    )
    logger.info(
        'Marketing test email poslan na %s (kampanja #%s)',
        MARKETING_TEST_EMAIL,
        campaign.pk,
    )


def send_marketing_campaign(campaign):
    """Pošalji marketing kampanju svim registrovanim korisnicima i pretplatnicima."""
    _ensure_email_configured()
    if not campaign.banner:
        raise ValueError('Kampanja nema banner sliku.')

    recipients = marketing_recipients()
    if not recipients:
        raise ValueError('Nema email adresa za slanje kampanje.')

    sent = 0
    failed = 0
    for recipient in recipients:
        try:
            send_marketing_campaign_email(campaign, recipient)
            sent += 1
        except Exception:
            failed += 1
            logger.exception(
                'Marketing email nije poslan na %s (kampanja #%s)',
                recipient.email,
                campaign.pk,
            )

    campaign.broj_primaoca = sent
    campaign.broj_gresaka = failed
    campaign.poslano = timezone.now()
    campaign.status = (
        MarketingEmailCampaign.Status.SENT if sent else MarketingEmailCampaign.Status.FAILED
    )
    campaign.save(update_fields=[
        'broj_primaoca', 'broj_gresaka', 'poslano', 'status',
    ])
    return sent, failed, len(recipients)


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