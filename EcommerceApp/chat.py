import logging
import time

from django.core.cache import cache
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from decimal import Decimal, ROUND_HALF_UP

from .emails import EmailNotConfiguredError, send_chat_notification
from .models import ChatConversation, ChatMessage, Product, SiteSettings

logger = logging.getLogger(__name__)

STAFF_ONLINE_CACHE_KEY = 'chat_staff_online'
STAFF_ONLINE_TTL = 180
EMAIL_DEBOUNCE_SECONDS = 600

DEFAULT_PROACTIVE_GREETING = (
    'Zdravo! 👋 Dobrodošli na opremazaribolov.ba.\n\n'
    'Treba li vam pomoć ili preporuka pri kupovini?\n'
    'Pišite nam — tu smo da pomognemo.'
)


def chat_settings():
    """Aktivnost, delay (ms) i pozdrav iz admina."""
    s = SiteSettings.load()
    delay_sec = int(getattr(s, 'chat_delay_seconds', 120) or 120)
    delay_sec = max(10, min(delay_sec, 3600))
    greeting = (getattr(s, 'chat_pozdrav_poruka', None) or '').strip()
    if not greeting:
        greeting = DEFAULT_PROACTIVE_GREETING
    return {
        'enabled': bool(getattr(s, 'chat_sa_kupcem_aktivan', True)),
        'delay_seconds': delay_sec,
        'delay_ms': delay_sec * 1000,
        'greeting': greeting,
    }


def ensure_session_key(request):
    if not request.session.session_key:
        request.session.save()
    return request.session.session_key


def mark_staff_online():
    cache.set(STAFF_ONLINE_CACHE_KEY, time.time(), STAFF_ONLINE_TTL)


def is_staff_online():
    last_seen = cache.get(STAFF_ONLINE_CACHE_KEY)
    if not last_seen:
        return False
    return (time.time() - last_seen) < STAFF_ONLINE_TTL


def find_open_customer_conversation(request):
    """Pronađi otvoren razgovor bez kreiranja novog."""
    ensure_session_key(request)
    if request.user.is_authenticated:
        by_user = (
            ChatConversation.objects.filter(
                user=request.user,
                status=ChatConversation.Status.OPEN,
            )
            .order_by('-last_message_at')
            .first()
        )
        if by_user:
            return by_user

    return (
        ChatConversation.objects.filter(
            session_key=request.session.session_key,
            status=ChatConversation.Status.OPEN,
        )
        .order_by('-last_message_at')
        .first()
    )


def get_customer_conversation(request, *, create=True):
    conversation = find_open_customer_conversation(request)
    if conversation:
        # Ako je gost kasnije prijavljen, veži razgovor
        if (
            request.user.is_authenticated
            and not conversation.user_id
            and conversation.session_key == request.session.session_key
        ):
            conversation.user = request.user
            conversation.save(update_fields=['user'])
        return conversation

    if not create:
        return None

    ensure_session_key(request)
    # Ponovo otvori zadnji zatvoreni razgovor u ovoj sesiji
    # (npr. greškom zatvoren pagehide-om pri navigaciji) — zadrži poruke
    reopen_q = ChatConversation.objects.filter(
        status=ChatConversation.Status.CLOSED,
        session_key=request.session.session_key,
    )
    if request.user.is_authenticated:
        reopen_q = ChatConversation.objects.filter(
            status=ChatConversation.Status.CLOSED,
        ).filter(
            Q(session_key=request.session.session_key) | Q(user=request.user),
        )
    closed = reopen_q.order_by('-last_message_at').first()
    if closed:
        closed.status = ChatConversation.Status.OPEN
        if request.user.is_authenticated and not closed.user_id:
            closed.user = request.user
            closed.save(update_fields=['status', 'user'])
        else:
            closed.save(update_fields=['status'])
        return closed

    return ChatConversation.objects.create(
        session_key=request.session.session_key,
        user=request.user if request.user.is_authenticated else None,
        guest_name=_default_guest_name(request),
        guest_email=_default_guest_email(request),
    )


def ensure_proactive_greeting(conversation):
    """
    Prva automatska poruka podrške (pozdrav + ponuda pomoći iz admina).
    Vraća True ako je poruka upravo kreirana.
    """
    if conversation is None:
        return False
    if conversation.messages.exists():
        return False

    greeting = chat_settings()['greeting']
    ChatMessage.objects.create(
        conversation=conversation,
        sender_type=ChatMessage.Sender.STAFF,
        staff_user=None,
        body=greeting,
        read_by_staff=True,
        read_by_customer=True,
    )
    conversation.last_message_at = timezone.now()
    conversation.customer_unread_count = 0
    conversation.save(update_fields=['last_message_at', 'customer_unread_count'])
    return True


def close_customer_conversations(request):
    """
    Zatvori otvorene chat razgovore za posjetioca.

    Napomena: ne zovi ovo na svaki pagehide — multi-page navigacija
    (kategorija, korpa…) bi resetovala chat. Hard close samo kad
    stvarno napusti sesiju (opcionalno) ili staff ručno zatvori.
    """
    ensure_session_key(request)
    qs = ChatConversation.objects.filter(status=ChatConversation.Status.OPEN)
    if request.user.is_authenticated:
        qs = qs.filter(
            Q(user=request.user) | Q(session_key=request.session.session_key),
        )
    else:
        qs = qs.filter(session_key=request.session.session_key)
    return qs.update(status=ChatConversation.Status.CLOSED)


def soft_leave_customer_chat(request):
    """
    Soft leave: ne zatvara razgovor (ostaje OPEN za navigaciju po sajtu).
    Samo bilježi da je tab možda zatvoren — online status ide preko LiveVisitor.
    """
    ensure_session_key(request)
    # Razgovor ostaje otvoren da se pri promjeni kategorije/korpe ne resetuje.
    return 0


def _default_guest_name(request):
    if request.user.is_authenticated:
        return request.user.get_full_name().strip()
    return ''


def _default_guest_email(request):
    if request.user.is_authenticated:
        return request.user.email
    return ''


def customer_needs_guest_info(conversation, request):
    """Ime/email nisu obavezni — gost može odmah pisati (session chat)."""
    return False


def set_guest_info(conversation, name, email):
    conversation.guest_name = name.strip()
    conversation.guest_email = email.strip()
    conversation.save(update_fields=['guest_name', 'guest_email'])


def _serialize_product_offer(message):
    product = message.product
    if not product:
        return None
    image = ''
    if product.prikazna_slika:
        try:
            image = product.prikazna_slika.url
        except Exception:
            image = ''
    bazna = message.product_bazna_cijena
    if bazna is None:
        bazna = product.prikazna_cijena
    cijena = message.product_cijena
    if cijena is None:
        cijena = bazna
    pct = message.product_popust_postotak
    has_discount = bool(pct and pct > 0 and cijena is not None and bazna is not None and cijena < bazna)
    return {
        'product_id': product.pk,
        'slug': product.slug,
        'naziv': product.naziv,
        'image': image,
        'url': product.get_absolute_url() if hasattr(product, 'get_absolute_url') else f'/artikal/{product.slug}/',
        'bazna_cijena': f'{Decimal(bazna):.2f}',
        'cijena': f'{Decimal(cijena):.2f}',
        'popust_postotak': f'{Decimal(pct):.2f}' if pct is not None and pct > 0 else None,
        'has_discount': has_discount,
        'message_id': message.pk,
    }


def serialize_message(message):
    if message.sender_type == ChatMessage.Sender.STAFF:
        # Kupcu se ne prikazuje ime/email zaposlenika
        staff_name = 'Zaposlenik'
    else:
        staff_name = ''
    product_offer = None
    if message.product_id:
        # select_related kad je učitano
        product_offer = _serialize_product_offer(message)
    body = message.body or ''
    if not body and product_offer:
        body = f'Preporuka: {product_offer["naziv"]}'
    return {
        'id': message.pk,
        'sender_type': message.sender_type,
        'body': body,
        'created_at': timezone.localtime(message.created_at).isoformat(),
        'staff_name': staff_name,
        'is_product_offer': bool(product_offer),
        'product_offer': product_offer,
    }


def _online_lookup_for_conversations(conversations):
    """
    Map conversation.pk -> is_online (LiveVisitor presence / last_seen).
    """
    from datetime import timedelta

    from django.core.cache import cache

    from .live_visitors import (
        ONLINE_MINUTES,
        is_visitor_marked_left,
        _presence_cache_key,
    )
    from .models import LiveVisitor

    convs = list(conversations)
    if not convs:
        return {}

    session_keys = [c.session_key for c in convs if c.session_key]
    user_ids = [c.user_id for c in convs if c.user_id]

    left_keys = set()
    presence_keys = set()
    for sk in session_keys:
        if is_visitor_marked_left(sk):
            left_keys.add(sk)
        elif cache.get(_presence_cache_key(sk)):
            presence_keys.add(sk)

    # last_seen prozor (ista logika kao „trenutno na sajtu”)
    cutoff = timezone.now() - timedelta(seconds=max(45, int(ONLINE_MINUTES * 60)))
    online_sessions = set()
    online_users = set()
    if session_keys or user_ids:
        qs = LiveVisitor.objects.filter(last_seen__gte=cutoff)
        if session_keys:
            online_sessions = set(
                qs.filter(session_key__in=session_keys)
                .values_list('session_key', flat=True)
                .distinct()
            )
        if user_ids:
            online_users = set(
                qs.filter(user_id__in=user_ids)
                .exclude(user_id__isnull=True)
                .values_list('user_id', flat=True)
                .distinct()
            )

    result = {}
    for c in convs:
        sk = (c.session_key or '').strip()
        if sk and sk in left_keys:
            result[c.pk] = False
            continue
        online = False
        if sk and sk in presence_keys:
            online = True
        elif sk and sk in online_sessions:
            online = True
        elif c.user_id and c.user_id in online_users:
            online = True
        result[c.pk] = online
    return result


def serialize_conversation_summary(
    conversation,
    *,
    include_preview=False,
    is_online=None,
):
    if is_online is None:
        is_online = _online_lookup_for_conversations([conversation]).get(conversation.pk, False)
    data = {
        'id': conversation.pk,
        'display_name': conversation.display_name,
        'display_email': conversation.display_email,
        'is_registered': conversation.is_registered,
        'staff_unread_count': conversation.staff_unread_count,
        'customer_unread_count': conversation.customer_unread_count,
        'last_message_at': timezone.localtime(conversation.last_message_at).isoformat(),
        'status': conversation.status,
        'is_online': bool(is_online),
        'session_key': conversation.session_key or '',
    }
    if include_preview:
        last_message = conversation.messages.order_by('-created_at').first()
        if last_message:
            if last_message.product_id:
                data['preview'] = f'📦 {last_message.product.naziv if last_message.product_id else "Artikal"}'
            else:
                data['preview'] = (last_message.body or '')[:120]
        else:
            data['preview'] = ''
    return data


def serialize_conversations_list(conversations, *, include_preview=True):
    convs = list(conversations)
    online_map = _online_lookup_for_conversations(convs)
    return [
        serialize_conversation_summary(
            c,
            include_preview=include_preview,
            is_online=online_map.get(c.pk, False),
        )
        for c in convs
    ]


def _maybe_notify_staff(conversation, message):
    if is_staff_online():
        return
    cache_key = f'chat_email_sent:{conversation.pk}'
    if cache.get(cache_key):
        return
    try:
        send_chat_notification(conversation, message)
        cache.set(cache_key, 1, EMAIL_DEBOUNCE_SECONDS)
    except EmailNotConfiguredError:
        logger.warning('Chat email nije poslan — SMTP nije konfigurisan.')
    except Exception:
        logger.exception('Slanje chat email obavijesti nije uspjelo.')


@transaction.atomic
def add_customer_message(conversation, body):
    message = ChatMessage.objects.create(
        conversation=conversation,
        sender_type=ChatMessage.Sender.CUSTOMER,
        body=body,
        read_by_staff=False,
        read_by_customer=True,
    )
    conversation.staff_unread_count += 1
    conversation.last_message_at = timezone.now()
    conversation.save(update_fields=['staff_unread_count', 'last_message_at'])
    _maybe_notify_staff(conversation, message)
    return message


def _touch_staff_reply(conversation):
    conversation.customer_unread_count += 1
    conversation.staff_unread_count = 0
    conversation.last_message_at = timezone.now()
    conversation.save(update_fields=['customer_unread_count', 'staff_unread_count', 'last_message_at'])
    ChatMessage.objects.filter(
        conversation=conversation,
        sender_type=ChatMessage.Sender.CUSTOMER,
        read_by_staff=False,
    ).update(read_by_staff=True)
    mark_staff_online()


@transaction.atomic
def add_staff_message(conversation, staff_user, body):
    message = ChatMessage.objects.create(
        conversation=conversation,
        sender_type=ChatMessage.Sender.STAFF,
        staff_user=staff_user,
        body=body,
        read_by_staff=True,
        read_by_customer=False,
    )
    _touch_staff_reply(conversation)
    return message


@transaction.atomic
def add_staff_product_offer(conversation, staff_user, product, popust_postotak=None, note=''):
    """
    Staff šalje artikal kupcu (slika + cijena + opcionalni % + dodaj u korpu).
    """
    if not isinstance(product, Product):
        product = Product.objects.get(pk=product)

    bazna = product.prikazna_cijena
    pct = None
    if popust_postotak is not None and str(popust_postotak).strip() != '':
        pct = Decimal(str(popust_postotak)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        if pct <= 0:
            pct = None
        elif pct > 90:
            pct = Decimal('90.00')

    if pct:
        cijena = (bazna * (Decimal('1') - pct / Decimal('100'))).quantize(
            Decimal('0.01'), rounding=ROUND_HALF_UP,
        )
    else:
        cijena = bazna

    body = (note or '').strip()
    if not body:
        if pct:
            body = f'Preporučujemo: {product.naziv} (−{pct}%)'
        else:
            body = f'Preporučujemo: {product.naziv}'

    message = ChatMessage.objects.create(
        conversation=conversation,
        sender_type=ChatMessage.Sender.STAFF,
        staff_user=staff_user,
        body=body,
        product=product,
        product_popust_postotak=pct,
        product_cijena=cijena,
        product_bazna_cijena=bazna,
        read_by_staff=True,
        read_by_customer=False,
    )
    _touch_staff_reply(conversation)
    return message


def mark_conversation_read_by_staff(conversation):
    updated = ChatMessage.objects.filter(
        conversation=conversation,
        sender_type=ChatMessage.Sender.CUSTOMER,
        read_by_staff=False,
    ).update(read_by_staff=True)
    if updated or conversation.staff_unread_count:
        conversation.staff_unread_count = 0
        conversation.save(update_fields=['staff_unread_count'])
    mark_staff_online()


def mark_conversation_read_by_customer(conversation):
    updated = ChatMessage.objects.filter(
        conversation=conversation,
        sender_type=ChatMessage.Sender.STAFF,
        read_by_customer=False,
    ).update(read_by_customer=True)
    if updated or conversation.customer_unread_count:
        conversation.customer_unread_count = 0
        conversation.save(update_fields=['customer_unread_count'])


def staff_unread_total():
    return ChatConversation.objects.filter(
        status=ChatConversation.Status.OPEN,
        staff_unread_count__gt=0,
    ).count()