import logging
import time

from django.core.cache import cache
from django.db import transaction
from django.utils import timezone

from .emails import EmailNotConfiguredError, send_chat_notification
from .models import ChatConversation, ChatMessage

logger = logging.getLogger(__name__)

STAFF_ONLINE_CACHE_KEY = 'chat_staff_online'
STAFF_ONLINE_TTL = 180
EMAIL_DEBOUNCE_SECONDS = 600


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


def get_customer_conversation(request):
    ensure_session_key(request)
    if request.user.is_authenticated:
        conversation = (
            ChatConversation.objects.filter(
                user=request.user,
                status=ChatConversation.Status.OPEN,
            )
            .order_by('-last_message_at')
            .first()
        )
        if conversation:
            return conversation

    conversation = (
        ChatConversation.objects.filter(
            session_key=request.session.session_key,
            user__isnull=True,
            status=ChatConversation.Status.OPEN,
        )
        .order_by('-last_message_at')
        .first()
    )
    if conversation:
        return conversation

    return ChatConversation.objects.create(
        session_key=request.session.session_key,
        user=request.user if request.user.is_authenticated else None,
        guest_name=_default_guest_name(request),
        guest_email=_default_guest_email(request),
    )


def _default_guest_name(request):
    if request.user.is_authenticated:
        return request.user.get_full_name().strip()
    return ''


def _default_guest_email(request):
    if request.user.is_authenticated:
        return request.user.email
    return ''


def customer_needs_guest_info(conversation, request):
    if request.user.is_authenticated:
        return False
    return not (conversation.guest_name.strip() and conversation.guest_email.strip())


def set_guest_info(conversation, name, email):
    conversation.guest_name = name.strip()
    conversation.guest_email = email.strip()
    conversation.save(update_fields=['guest_name', 'guest_email'])


def serialize_message(message):
    return {
        'id': message.pk,
        'sender_type': message.sender_type,
        'body': message.body,
        'created_at': timezone.localtime(message.created_at).isoformat(),
        'staff_name': (
            message.staff_user.get_full_name().strip() or 'Podrška'
            if message.staff_user_id else ''
        ),
    }


def serialize_conversation_summary(conversation, *, include_preview=False):
    data = {
        'id': conversation.pk,
        'display_name': conversation.display_name,
        'display_email': conversation.display_email,
        'is_registered': conversation.is_registered,
        'staff_unread_count': conversation.staff_unread_count,
        'customer_unread_count': conversation.customer_unread_count,
        'last_message_at': timezone.localtime(conversation.last_message_at).isoformat(),
        'status': conversation.status,
    }
    if include_preview:
        last_message = conversation.messages.order_by('-created_at').first()
        data['preview'] = last_message.body[:120] if last_message else ''
    return data


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