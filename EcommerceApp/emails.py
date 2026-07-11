import logging
from decimal import Decimal
from email.utils import formataddr

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.utils import timezone

from .models import (
    Order,
    SiteSettings,
)
from .pricing import pripremi_stavke_za_racun, sazetak_iz_narudzbe

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


def _cart_notification_items(cart):
    items = []
    for raw in cart.cart.values():
        price = Decimal(str(raw.get('cijena', 0)))
        qty = int(raw.get('quantity', 0) or 0)
        name = raw.get('product_naziv') or raw.get('naziv', '')
        variation = (raw.get('varijacija_naziv') or '').strip()
        if variation:
            name = f'{name} — {variation}'
        items.append({
            'naziv': name,
            'quantity': qty,
            'ukupno': (price * qty).quantize(Decimal('0.01')),
        })
    return items


def send_cart_add_notification(
    request,
    *,
    product,
    variation=None,
    quantity_added=1,
    line_price=None,
    line_total=None,
    total_qty_in_line=None,
    cart=None,
):
    """Obavijest trgovini kad posjetilac doda artikal u korpu."""
    try:
        _ensure_email_configured()
    except EmailNotConfiguredError:
        logger.warning('Cart add obavijest preskočena — email nije konfigurisan.')
        return

    if line_price is None:
        line_price = variation.prikazna_cijena if variation else product.prikazna_cijena
    if line_total is None:
        line_total = (Decimal(str(line_price)) * quantity_added).quantize(Decimal('0.01'))
    if total_qty_in_line is None:
        total_qty_in_line = quantity_added

    label = product.naziv
    if variation:
        label = f'{product.naziv} — {variation.naziv}'

    user = getattr(request, 'user', None)
    if user and user.is_authenticated:
        user_email = (user.email or '').strip()
        user_label = user.get_full_name().strip() or user_email or 'Registrovan korisnik'
        registered = True
    else:
        user_email = ''
        user_label = 'Gost'
        registered = False

    created = timezone.localtime(timezone.now())
    product_url = f'{settings.SITE_URL}{product.get_absolute_url()}'
    active_carts_url = f'{settings.SITE_URL}/nalog/aktivne-korpe/'

    cart_items = _cart_notification_items(cart) if cart is not None else []
    cart_line_count = len(cart_items)
    cart_item_count = sum(item['quantity'] for item in cart_items)
    cart_total = cart.ukupno if cart is not None else line_total

    body_lines = [
        'Artikal je dodan u korpu na opremazaribolov.ba',
        '',
        f'Korisnik: {user_label}',
        f'Email: {user_email or "—"}',
        f'Registrovan: {"Da" if registered else "Ne (gost)"}',
        f'Vrijeme: {created.strftime("%d.%m.%Y. %H:%M")}',
        '',
        'Dodano:',
        f'- {label}',
        f'  Količina u ovom koraku: {quantity_added}',
        f'  Ukupno u korpi (ova stavka): {total_qty_in_line}',
        f'  Cijena: {line_price} KM',
        f'  Iznos (ovaj korak): {line_total} KM',
        f'  Link: {product_url}',
    ]
    if cart_items:
        body_lines.extend(['', f'Trenutna korpa ({cart_line_count} artikala, {cart_item_count} kom):', ''])
        for item in cart_items:
            body_lines.append(f'- {item["naziv"]} × {item["quantity"]} = {item["ukupno"]} KM')
        body_lines.extend(['', f'Ukupno u korpi: {cart_total} KM'])
    body_lines.extend([
        '',
        f'Pregled aktivnih korpi: {active_carts_url}',
    ])

    reply_to = [user_email] if user_email and '@' in user_email else None
    recipient = settings.ORDER_NOTIFICATION_EMAIL

    mail = EmailMultiAlternatives(
        subject=f'Korpa: {label} — opremazaribolov.ba',
        body='\n'.join(body_lines),
        from_email=_from_email(),
        to=[recipient],
        reply_to=reply_to,
    )
    mail.attach_alternative(
        render_to_string('emails/cart_add_notification.html', {
            'label': label,
            'product': product,
            'variation': variation,
            'product_url': product_url,
            'active_carts_url': active_carts_url,
            'user_label': user_label,
            'user_email': user_email,
            'registered': registered,
            'created': created,
            'quantity_added': quantity_added,
            'total_qty_in_line': total_qty_in_line,
            'line_price': line_price,
            'line_total': line_total,
            'cart_items': cart_items,
            'cart_line_count': cart_line_count,
            'cart_item_count': cart_item_count,
            'cart_total': cart_total,
            'site_url': settings.SITE_URL,
        }),
        'text/html',
    )
    mail.send(fail_silently=False)
    logger.info(
        'Cart add obavijest poslana za %s (korisnik: %s)',
        label,
        user_email or 'gost',
    )


def send_live_offer_email(*, to_email, visitor_name='', offer=None):
    """
    Email kupcu kad staff pošalje uživo ponudu (popup na sajtu + email).
    Ne prekida slanje popup-a ako email nije konfigurisan.
    """
    to_email = (to_email or '').strip()
    if not to_email or '@' not in to_email:
        raise ValueError('Kupac nema valjan email.')

    try:
        _ensure_email_configured()
    except EmailNotConfiguredError:
        logger.warning('Live offer email preskočen — email nije konfigurisan.')
        raise

    from .models import LiveVisitorOffer

    site_url = (settings.SITE_URL or '').rstrip('/')
    name = (visitor_name or '').strip() or 'poštovani kupče'
    product = getattr(offer, 'product', None) if offer else None
    product_name = product.naziv if product else ''
    product_url = f'{site_url}{product.get_absolute_url()}' if product else site_url
    percent = getattr(offer, 'discount_percent', None) or Decimal('0')
    try:
        percent = Decimal(str(percent))
    except Exception:
        percent = Decimal('0')
    code = (getattr(offer, 'aktivacioni_kod', None) or '').strip()
    tip = getattr(offer, 'tip', None) or ''
    free_shipping = bool(getattr(offer, 'besplatna_dostava', False))

    if tip == LiveVisitorOffer.Tip.ARTIKAL and product:
        subject = f'Posebna ponuda: {product_name} — opremazaribolov.ba'
        headline = 'Imate posebnu ponudu na sajtu'
        parts = []
        if percent > 0:
            parts.append(f'popust od {percent:g}% na artikal „{product_name}”')
        else:
            parts.append(f'posebnu ponudu za artikal „{product_name}”')
        if free_shipping:
            parts.append('besplatnu dostavu na prvu kupovinu')
        body_lead = 'Pripremili smo Vam ' + ' i '.join(parts) + '.'
    elif tip == LiveVisitorOffer.Tip.NARUDZBA:
        if free_shipping and percent <= 0:
            subject = 'Besplatna dostava na prvu kupovinu — opremazaribolov.ba'
            headline = 'Besplatna dostava na prvu kupovinu'
            body_lead = (
                f'Prihvatite ponudu na sajtu — na prvu narudžbu dostava vam je besplatna'
                f'{f" (kod: {code})" if code else ""}.'
            )
        elif free_shipping and percent > 0:
            subject = f'{percent:g}% popusta + besplatna dostava — opremazaribolov.ba'
            headline = 'Popust i besplatna dostava'
            body_lead = (
                f'Vaš kod za {percent:g}% popusta na narudžbu: {code or "—"}. '
                f'Uz to, na prvu kupovinu imate besplatnu dostavu.'
            )
        else:
            subject = f'Vaš kod za {percent:g}% popusta — opremazaribolov.ba'
            headline = 'Popust na vašu narudžbu'
            body_lead = f'Vaš aktivacioni kod za {percent:g}% popusta na narudžbu: {code or "—"}.'
    else:
        subject = 'Posebna ponuda — opremazaribolov.ba'
        headline = 'Imate posebnu ponudu'
        body_lead = 'Otvorite sajt da vidite personalizovanu ponudu.'

    text_lines = [
        f'Poštovani/a {name},',
        '',
        body_lead,
        '',
        'Ponuda je aktivna i na sajtu kao popup — dovoljno je da otvorite stranicu.',
        f'Sajt: {site_url}',
    ]
    if product_url and product:
        text_lines.append(f'Artikal: {product_url}')
    if code:
        text_lines.append(f'Kod: {code}')
    text_lines.extend([
        '',
        'Lijep pozdrav,',
        'opremazaribolov.ba',
    ])

    mail = EmailMultiAlternatives(
        subject=subject,
        body='\n'.join(text_lines),
        from_email=_from_email(),
        to=[to_email],
    )
    mail.attach_alternative(
        render_to_string('emails/live_offer.html', {
            'visitor_name': name,
            'headline': headline,
            'body_lead': body_lead,
            'product_name': product_name,
            'product_url': product_url if product else '',
            'discount_percent': percent,
            'free_shipping': free_shipping,
            'activation_code': code,
            'site_url': site_url,
            'store_email': settings.STORE_EMAIL,
        }),
        'text/html',
    )
    mail.send(fail_silently=False)
    logger.info('Live offer email poslan na %s', to_email)


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