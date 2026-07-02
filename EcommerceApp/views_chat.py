import json

from django.contrib.auth.decorators import login_required, user_passes_test
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_GET, require_POST

from .chat import (
    add_customer_message,
    add_staff_message,
    customer_needs_guest_info,
    get_customer_conversation,
    mark_conversation_read_by_customer,
    mark_conversation_read_by_staff,
    mark_staff_online,
    serialize_conversation_summary,
    serialize_message,
    set_guest_info,
    staff_unread_total,
)
from .models import ChatConversation, ChatMessage


def _json_body(request):
    if not request.body:
        return {}
    try:
        return json.loads(request.body.decode('utf-8'))
    except json.JSONDecodeError:
        return {}


def _staff_required(user):
    return user.is_authenticated and user.is_staff


@require_GET
def chat_state(request):
    conversation = get_customer_conversation(request)
    messages = list(conversation.messages.order_by('created_at')[:200])
    if request.GET.get('mark_read') == '1':
        mark_conversation_read_by_customer(conversation)
        customer_unread = 0
    else:
        customer_unread = conversation.customer_unread_count
    return JsonResponse({
        'conversation_id': conversation.pk,
        'needs_guest_info': customer_needs_guest_info(conversation, request),
        'display_name': conversation.display_name,
        'display_email': conversation.display_email,
        'is_registered': conversation.is_registered,
        'customer_unread_count': customer_unread,
        'messages': [serialize_message(message) for message in messages],
    })


@require_POST
def chat_guest_info(request):
    data = _json_body(request)
    name = (data.get('name') or '').strip()
    email = (data.get('email') or '').strip()
    if not name or not email or '@' not in email:
        return JsonResponse({'ok': False, 'error': 'Unesite ime i ispravan email.'}, status=400)

    conversation = get_customer_conversation(request)
    if request.user.is_authenticated:
        return JsonResponse({'ok': True, 'needs_guest_info': False})

    set_guest_info(conversation, name, email)
    return JsonResponse({
        'ok': True,
        'needs_guest_info': False,
        'display_name': conversation.display_name,
        'display_email': conversation.display_email,
    })


@require_POST
def chat_send(request):
    data = _json_body(request)
    body = (data.get('body') or '').strip()
    if not body:
        return JsonResponse({'ok': False, 'error': 'Poruka ne može biti prazna.'}, status=400)
    if len(body) > 2000:
        return JsonResponse({'ok': False, 'error': 'Poruka je predugačka.'}, status=400)

    conversation = get_customer_conversation(request)
    if customer_needs_guest_info(conversation, request):
        return JsonResponse({
            'ok': False,
            'error': 'Unesite ime i email prije slanja poruke.',
            'needs_guest_info': True,
        }, status=400)

    message = add_customer_message(conversation, body)
    return JsonResponse({
        'ok': True,
        'message': serialize_message(message),
    })


@require_GET
def chat_badge(request):
    conversation = get_customer_conversation(request)
    return JsonResponse({
        'customer_unread_count': conversation.customer_unread_count,
    })


@require_GET
def chat_poll(request):
    conversation = get_customer_conversation(request)
    after_id = int(request.GET.get('after_id', '0') or 0)
    new_messages = list(
        conversation.messages.filter(pk__gt=after_id).order_by('created_at'),
    )
    if request.GET.get('open') == '1':
        mark_conversation_read_by_customer(conversation)
        customer_unread = 0
    else:
        customer_unread = conversation.customer_unread_count
    return JsonResponse({
        'ok': True,
        'messages': [serialize_message(message) for message in new_messages],
        'customer_unread_count': customer_unread,
    })


@require_POST
@login_required(login_url='login')
@user_passes_test(_staff_required)
def chat_staff_ping(request):
    mark_staff_online()
    return JsonResponse({
        'ok': True,
        'unread_conversations': staff_unread_total(),
    })


@require_GET
@login_required(login_url='login')
@user_passes_test(_staff_required)
def chat_staff_inbox(request):
    mark_staff_online()
    conversations = ChatConversation.objects.filter(
        status=ChatConversation.Status.OPEN,
    ).order_by('-last_message_at')[:100]
    return JsonResponse({
        'ok': True,
        'unread_conversations': staff_unread_total(),
        'conversations': [
            serialize_conversation_summary(conversation, include_preview=True)
            for conversation in conversations
        ],
    })


@require_GET
@login_required(login_url='login')
@user_passes_test(_staff_required)
def chat_staff_conversation(request, pk):
    mark_staff_online()
    conversation = get_object_or_404(ChatConversation, pk=pk)
    messages = list(conversation.messages.order_by('created_at')[:300])
    mark_conversation_read_by_staff(conversation)
    return JsonResponse({
        'ok': True,
        'conversation': serialize_conversation_summary(conversation),
        'messages': [serialize_message(message) for message in messages],
        'unread_conversations': staff_unread_total(),
    })


@require_POST
@login_required(login_url='login')
@user_passes_test(_staff_required)
def chat_staff_send(request, pk):
    data = _json_body(request)
    body = (data.get('body') or '').strip()
    if not body:
        return JsonResponse({'ok': False, 'error': 'Poruka ne može biti prazna.'}, status=400)
    if len(body) > 2000:
        return JsonResponse({'ok': False, 'error': 'Poruka je predugačka.'}, status=400)

    conversation = get_object_or_404(ChatConversation, pk=pk, status=ChatConversation.Status.OPEN)
    message = add_staff_message(conversation, request.user, body)
    return JsonResponse({
        'ok': True,
        'message': serialize_message(message),
        'unread_conversations': staff_unread_total(),
    })


@require_POST
@login_required(login_url='login')
@user_passes_test(_staff_required)
def chat_staff_read(request, pk):
    conversation = get_object_or_404(ChatConversation, pk=pk)
    mark_conversation_read_by_staff(conversation)
    return JsonResponse({
        'ok': True,
        'unread_conversations': staff_unread_total(),
    })