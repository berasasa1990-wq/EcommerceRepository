import logging
import time
from email.utils import formataddr
from types import SimpleNamespace

from django.conf import settings
from django.contrib.auth.models import User
from django.core.mail import EmailMultiAlternatives, get_connection
from django.template.loader import render_to_string
from django.utils import timezone

from .models import (
    MarketingEmailCampaign,
    MarketingSubscriber,
    MarketingSubscriberGroup,
    Order,
    SiteSettings,
)
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


def marketing_recipients(*, group=None, include_registered=False):
    seen = set()
    recipients = []

    if include_registered:
        for user in marketing_recipient_users():
            email = user.email.strip().lower()
            seen.add(email)
            recipients.append(SimpleNamespace(
                email=email,
                display_name=_marketing_display_name(email, user.first_name),
            ))

    subscribers_qs = MarketingSubscriber.objects.filter(aktivan=True).order_by('id')
    if group is not None:
        subscribers_qs = subscribers_qs.filter(grupa=group)
    elif not include_registered:
        subscribers_qs = subscribers_qs.none()

    for subscriber in subscribers_qs:
        email = subscriber.email.strip().lower()
        if not email or email in seen:
            continue
        seen.add(email)
        recipients.append(SimpleNamespace(
            email=email,
            display_name=_marketing_display_name(email, subscriber.ime),
        ))
    return recipients


def _next_subscriber_group_number():
    existing = MarketingSubscriberGroup.objects.order_by('-redoslijed', '-id').first()
    if not existing:
        return 1
    naziv = (existing.naziv or '').strip()
    if naziv.lower().startswith('grupa '):
        try:
            return int(naziv.split()[-1]) + 1
        except ValueError:
            pass
    return MarketingSubscriberGroup.objects.count() + 1


def create_marketing_subscriber_group(*, naziv='', added_by=None):
    naziv = (naziv or '').strip()
    if not naziv:
        naziv = f'Grupa {_next_subscriber_group_number()}'
    redoslijed = (
        MarketingSubscriberGroup.objects.order_by('-redoslijed').values_list('redoslijed', flat=True).first() or 0
    ) + 1
    return MarketingSubscriberGroup.objects.create(
        naziv=naziv,
        redoslijed=redoslijed,
        dodao=added_by,
    )


def auto_distribute_subscribers_to_groups(*, group_size=None, added_by=None):
    group_size = group_size or settings.MARKETING_SUBSCRIBER_GROUP_SIZE
    unassigned = list(
        MarketingSubscriber.objects.filter(aktivan=True, grupa__isnull=True).order_by('id'),
    )
    if not unassigned:
        return {'assigned': 0, 'groups_created': 0, 'groups_used': 0}

    groups_created = 0
    groups_used = set()
    assigned = 0
    index = 0
    while index < len(unassigned):
        group = None
        for candidate in MarketingSubscriberGroup.objects.order_by('redoslijed', 'id'):
            current_count = candidate.pretplatnici.filter(aktivan=True).count()
            if current_count < group_size:
                group = candidate
                break
        if group is None:
            group = create_marketing_subscriber_group(added_by=added_by)
            groups_created += 1

        capacity = group_size - group.pretplatnici.filter(aktivan=True).count()
        batch = unassigned[index:index + capacity]
        for subscriber in batch:
            subscriber.grupa = group
            subscriber.save(update_fields=['grupa'])
            assigned += 1
        groups_used.add(group.pk)
        index += len(batch)

    return {
        'assigned': assigned,
        'groups_created': groups_created,
        'groups_used': len(groups_used),
    }


def marketing_send_groups(*, campaign=None):
    """Marketing grupe s aktivnim pretplatnicima — za padajući izbor pri slanju."""
    already_sent = set()
    if campaign:
        already_sent = _sent_emails_for_campaign_title(campaign)
        already_sent.update(_bootstrap_legacy_sent_emails(campaign))

    groups = []
    for group in MarketingSubscriberGroup.objects.order_by('redoslijed', 'id'):
        recipients = marketing_recipients(group=group)
        if not recipients:
            continue
        total = len(recipients)
        sent = sum(1 for recipient in recipients if recipient.email in already_sent)
        groups.append({
            'pk': group.pk,
            'naziv': group.naziv,
            'total': total,
            'sent': sent,
            'remaining': max(total - sent, 0),
        })
    return groups


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
        'warranty_seal_url': f'{settings.SITE_URL}{settings.STATIC_URL}img/warranty-seal.svg',
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


def _marketing_recipient_from_payload(payload):
    return SimpleNamespace(
        email=payload['email'],
        display_name=payload.get('display_name') or _marketing_display_name(payload['email']),
    )


def send_marketing_campaign_email(campaign, recipient, *, subject_prefix='', connection=None):
    _ensure_email_configured()
    mail = EmailMultiAlternatives(
        subject=f'{subject_prefix}{campaign.naslov} — opremazaribolov.ba',
        body=_marketing_plain_text(campaign, recipient),
        from_email=_from_email(),
        to=[recipient.email.strip()],
        reply_to=[settings.ORDER_NOTIFICATION_EMAIL],
        connection=connection,
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


def _normalize_marketing_email(email):
    return (email or '').strip().lower()


def _campaign_sent_email_set(campaign):
    return {
        _normalize_marketing_email(email)
        for email in (campaign.slanje_poslati or [])
        if _normalize_marketing_email(email)
    }


def _sent_emails_for_campaign_title(campaign):
    """Svi emailovi koji su već primili kampanju s istim naslovom."""
    sent = _campaign_sent_email_set(campaign)
    if not campaign.naslov:
        return sent
    related = MarketingEmailCampaign.objects.filter(
        naslov=campaign.naslov,
    ).exclude(pk=campaign.pk).exclude(
        status=MarketingEmailCampaign.Status.DRAFT,
    )
    for other in related:
        sent.update(_campaign_sent_email_set(other))
    return sent


def _bootstrap_legacy_sent_emails(campaign):
    """Prethodna slanja prije slanje_poslati polja — rekonstruiši iz broj_primaoca."""
    if campaign.slanje_poslati:
        return [
            _normalize_marketing_email(email)
            for email in campaign.slanje_poslati
            if _normalize_marketing_email(email)
        ]
    legacy = []
    if campaign.broj_primaoca and campaign.slanje_lista:
        for payload in campaign.slanje_lista[:campaign.broj_primaoca]:
            email = _normalize_marketing_email(payload.get('email'))
            if email and email not in legacy:
                legacy.append(email)
    return legacy


def marketing_send_progress(campaign):
    group = campaign.slanje_grupa
    include_registered = campaign.slanje_ukljuci_registrovane
    recipients = marketing_recipients(group=group, include_registered=include_registered)
    already_sent = _sent_emails_for_campaign_title(campaign)
    already_sent.update(_bootstrap_legacy_sent_emails(campaign))
    total = len(recipients)
    sent_in_scope = sum(1 for recipient in recipients if recipient.email in already_sent)
    return {
        'already_sent': sent_in_scope,
        'remaining': max(total - sent_in_scope, 0),
        'total': total,
        'can_resume': (
            sent_in_scope > 0
            and campaign.status in (
                MarketingEmailCampaign.Status.DRAFT,
                MarketingEmailCampaign.Status.SENDING,
                MarketingEmailCampaign.Status.FAILED,
            )
            and sent_in_scope < total
        ),
        'group': group,
        'include_registered': include_registered,
    }


def start_marketing_campaign_send(
    campaign,
    *,
    user=None,
    group=None,
):
    """Pripremi kampanju za batch slanje (izbjegava HTTP timeout na velikim listama)."""
    _ensure_email_configured()
    if not campaign.banner:
        raise ValueError('Kampanja nema banner sliku.')

    if group is None:
        raise ValueError('Odaberite marketing grupu za slanje.')

    recipients = marketing_recipients(group=group)
    if not recipients:
        raise ValueError('Nema email adresa za slanje u odabranoj grupi.')

    already_sent = _sent_emails_for_campaign_title(campaign)
    legacy_sent = _bootstrap_legacy_sent_emails(campaign)
    if legacy_sent:
        already_sent.update(legacy_sent)
        if not campaign.slanje_poslati:
            campaign.slanje_poslati = legacy_sent

    remaining = [
        recipient for recipient in recipients
        if recipient.email.lower() not in already_sent
    ]

    if not remaining:
        if already_sent:
            campaign.slanje_poslati = sorted(already_sent)
            campaign.broj_primaoca = len(already_sent)
            campaign.slanje_ukupno = len(recipients)
            campaign.slanje_lista = []
            campaign.slanje_offset = 0
            campaign.poslano = timezone.now()
            campaign.status = MarketingEmailCampaign.Status.DRAFT
            campaign.save(update_fields=[
                'slanje_poslati', 'broj_primaoca', 'slanje_ukupno',
                'slanje_lista', 'slanje_offset', 'poslano', 'status',
            ])
            return 0
        raise ValueError('Nema preostalih email adresa u odabranoj grupi.')

    campaign.slanje_lista = [
        {'email': recipient.email, 'display_name': recipient.display_name}
        for recipient in remaining
    ]
    campaign.slanje_ukupno = len(recipients)
    campaign.slanje_offset = 0
    campaign.broj_primaoca = sum(
        1 for recipient in recipients if recipient.email.lower() in already_sent
    )
    campaign.slanje_poslati = sorted(already_sent)
    campaign.broj_gresaka = 0
    campaign.poslano = None
    campaign.slanje_grupa = group
    campaign.slanje_ukljuci_registrovane = False
    campaign.status = MarketingEmailCampaign.Status.SENDING
    if user is not None:
        campaign.poslao = user
    campaign.save(update_fields=[
        'slanje_lista', 'slanje_ukupno', 'slanje_offset', 'slanje_poslati',
        'broj_primaoca', 'broj_gresaka', 'poslano', 'status', 'poslao',
        'slanje_grupa', 'slanje_ukljuci_registrovane',
    ])
    return len(remaining)


def send_marketing_campaign_batch(campaign):
    """Pošalji sljedeću grupu emailova i ažuriraj napredak kampanje."""
    _ensure_email_configured()
    if campaign.status != MarketingEmailCampaign.Status.SENDING:
        raise ValueError('Kampanja nije u statusu slanja.')

    recipient_payloads = campaign.slanje_lista or []
    queue_total = len(recipient_payloads)
    audience_total = campaign.slanje_ukupno or queue_total
    offset = campaign.slanje_offset
    batch_size = max(1, settings.MARKETING_EMAIL_BATCH_SIZE)
    batch = recipient_payloads[offset:offset + batch_size]
    sent_registry = list(campaign.slanje_poslati or [])

    if not batch:
        return _finalize_marketing_campaign_send(campaign, total=audience_total)

    batch_sent = 0
    batch_failed = 0
    pause_seconds = max(0.0, settings.MARKETING_EMAIL_BATCH_PAUSE)
    chunk_size = max(1, min(10, batch_size))

    for chunk_start in range(0, len(batch), chunk_size):
        chunk = batch[chunk_start:chunk_start + chunk_size]
        connection = get_connection()
        try:
            connection.open()
            for index, payload in enumerate(chunk):
                recipient = _marketing_recipient_from_payload(payload)
                try:
                    send_marketing_campaign_email(campaign, recipient, connection=connection)
                    normalized = _normalize_marketing_email(recipient.email)
                    if normalized and normalized not in sent_registry:
                        sent_registry.append(normalized)
                    batch_sent += 1
                except Exception:
                    batch_failed += 1
                    logger.exception(
                        'Marketing email nije poslan na %s (kampanja #%s)',
                        recipient.email,
                        campaign.pk,
                    )
                if pause_seconds and index < len(chunk) - 1:
                    time.sleep(pause_seconds)
        finally:
            try:
                connection.close()
            except Exception:
                logger.debug('Zatvaranje SMTP konekcije nije uspjelo.', exc_info=True)
        if chunk_start + chunk_size < len(batch):
            time.sleep(max(0.5, settings.MARKETING_EMAIL_GROUP_PAUSE / 4))

    campaign.slanje_offset = offset + len(batch)
    campaign.slanje_poslati = sent_registry
    campaign.broj_primaoca += batch_sent
    campaign.broj_gresaka += batch_failed
    campaign.save(update_fields=[
        'slanje_offset', 'slanje_poslati', 'broj_primaoca', 'broj_gresaka',
    ])

    done = campaign.slanje_offset >= queue_total
    if done:
        return _finalize_marketing_campaign_send(campaign, total=audience_total)

    group_number = (campaign.slanje_offset + batch_size - 1) // batch_size
    group_total = (queue_total + batch_size - 1) // batch_size
    return {
        'done': False,
        'offset': campaign.broj_primaoca,
        'total': audience_total,
        'sent': campaign.broj_primaoca,
        'failed': campaign.broj_gresaka,
        'batch_sent': batch_sent,
        'batch_failed': batch_failed,
        'batch_size': batch_size,
        'group_number': group_number,
        'group_total': group_total,
        'skipped': audience_total - queue_total,
    }


def _finalize_marketing_campaign_send(campaign, *, total):
    campaign.poslano = timezone.now()
    if campaign.broj_primaoca:
        campaign.status = MarketingEmailCampaign.Status.DRAFT
    else:
        campaign.status = MarketingEmailCampaign.Status.FAILED
    campaign.slanje_lista = []
    campaign.slanje_offset = 0
    campaign.save(update_fields=[
        'poslano', 'status', 'slanje_lista', 'slanje_offset',
    ])
    return {
        'done': True,
        'offset': campaign.broj_primaoca,
        'total': total,
        'sent': campaign.broj_primaoca,
        'failed': campaign.broj_gresaka,
        'batch_sent': 0,
        'batch_failed': 0,
    }


def send_marketing_campaign(campaign, *, user=None):
    """Kompatibilnost — pokreće batch slanje od početka."""
    total = start_marketing_campaign_send(campaign, user=user)
    result = {'sent': 0, 'failed': 0, 'total': total, 'done': False}
    while not result.get('done'):
        result = send_marketing_campaign_batch(campaign)
    return result['sent'], result['failed'], result['total']


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