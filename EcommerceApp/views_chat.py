import json
from decimal import Decimal, InvalidOperation

from django.contrib.auth.decorators import login_required, user_passes_test
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from .cart import Cart
from .chat import (
    add_customer_message,
    add_staff_message,
    add_staff_product_offer,
    chat_settings,
    close_customer_conversations,
    customer_needs_guest_info,
    ensure_proactive_greeting,
    get_customer_conversation,
    mark_conversation_read_by_customer,
    mark_conversation_read_by_staff,
    mark_staff_online,
    serialize_conversation_summary,
    serialize_conversations_list,
    serialize_message,
    set_guest_info,
    staff_unread_total,
)
from .models import ChatConversation, ChatMessage, Product


def _json_body(request):
    if not request.body:
        return {}
    try:
        return json.loads(request.body.decode('utf-8'))
    except json.JSONDecodeError:
        return {}


def _staff_required(user):
    return user.is_authenticated and user.is_staff


def _messages_qs(conversation):
    return (
        conversation.messages
        .select_related('staff_user', 'product')
        .order_by('created_at')
    )


@require_GET
def chat_config(request):
    """Javne chat postavke (delay, enabled) — bez kreiranja razgovora."""
    cfg = chat_settings()
    return JsonResponse({
        'ok': True,
        'enabled': cfg['enabled'],
        'delay_seconds': cfg['delay_seconds'],
        'delay_ms': cfg['delay_ms'],
        # pozdrav ne šaljemo unaprijed — kreira se na serveru pri otvaranju
    })


@require_GET
def chat_state(request):
    cfg = chat_settings()
    if not cfg['enabled'] and not (request.user.is_authenticated and request.user.is_staff):
        # Isključen chat za kupce — staff i dalje može otvoriti postojeće
        if request.GET.get('proactive') == '1':
            return JsonResponse({'ok': False, 'error': 'Chat je isključen.', 'enabled': False}, status=403)

    conversation = get_customer_conversation(request, create=True)
    if request.GET.get('proactive') == '1':
        ensure_proactive_greeting(conversation)
    messages = list(_messages_qs(conversation)[:200])
    if request.GET.get('mark_read') == '1':
        mark_conversation_read_by_customer(conversation)
        customer_unread = 0
    else:
        customer_unread = conversation.customer_unread_count
    return JsonResponse({
        'ok': True,
        'enabled': cfg['enabled'],
        'conversation_id': conversation.pk,
        'status': conversation.status,
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

    conversation = get_customer_conversation(request, create=True)
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

    conversation = get_customer_conversation(request, create=True)
    if conversation.status != ChatConversation.Status.OPEN:
        return JsonResponse({
            'ok': False,
            'error': 'Chat je zatvoren. Osvežite stranicu da započnete novi razgovor.',
            'closed': True,
        }, status=400)

    message = add_customer_message(conversation, body)
    return JsonResponse({
        'ok': True,
        'message': serialize_message(message),
    })


@require_GET
def chat_badge(request):
    conversation = get_customer_conversation(request, create=False)
    if not conversation:
        return JsonResponse({'customer_unread_count': 0, 'status': 'none'})
    return JsonResponse({
        'customer_unread_count': conversation.customer_unread_count,
        'status': conversation.status,
        'conversation_id': conversation.pk,
    })


@require_GET
def chat_poll(request):
    conversation = get_customer_conversation(request, create=False)
    if not conversation:
        return JsonResponse({
            'ok': True,
            'messages': [],
            'customer_unread_count': 0,
            'status': 'none',
            'closed': False,
        })

    after_id = int(request.GET.get('after_id', '0') or 0)
    new_messages = list(
        _messages_qs(conversation).filter(pk__gt=after_id),
    )
    if request.GET.get('open') == '1':
        mark_conversation_read_by_customer(conversation)
        customer_unread = 0
    else:
        customer_unread = conversation.customer_unread_count

    closed = conversation.status != ChatConversation.Status.OPEN
    return JsonResponse({
        'ok': True,
        'messages': [serialize_message(message) for message in new_messages],
        'customer_unread_count': customer_unread,
        'status': conversation.status,
        'closed': closed,
    })


@csrf_exempt
@require_POST
def chat_leave(request):
    """Kupac napušta sajt — zatvori chat za obje strane."""
    closed = close_customer_conversations(request)
    return JsonResponse({'ok': True, 'closed': int(closed)})


@require_POST
def chat_add_product(request):
    """Kupac dodaje artikal iz chat ponude u korpu."""
    data = _json_body(request)
    try:
        message_id = int(data.get('message_id') or 0)
    except (TypeError, ValueError):
        message_id = 0
    if not message_id:
        return JsonResponse({'ok': False, 'error': 'Nedostaje poruka.'}, status=400)

    conversation = get_customer_conversation(request, create=False)
    if not conversation:
        return JsonResponse({'ok': False, 'error': 'Nema aktivnog chata.'}, status=400)

    message = (
        ChatMessage.objects
        .select_related('product')
        .filter(
            pk=message_id,
            conversation=conversation,
            product__isnull=False,
            sender_type=ChatMessage.Sender.STAFF,
        )
        .first()
    )
    if not message or not message.product_id:
        return JsonResponse({'ok': False, 'error': 'Ponuda nije pronađena.'}, status=404)

    product = message.product
    if not product.aktivan:
        return JsonResponse({'ok': False, 'error': 'Artikal više nije dostupan.'}, status=400)

    cart = Cart(request)
    bazna = message.product_bazna_cijena or product.prikazna_cijena
    cijena = message.product_cijena
    pct = message.product_popust_postotak

    if cijena is not None and pct and pct > 0 and cijena < bazna:
        cart.add(
            product,
            quantity=1,
            custom_price=cijena,
            promo_bazna=bazna,
            discount_source=f'Chat ponuda (−{pct}%)',
            discount_percent=pct,
        )
    elif cijena is not None and cijena < bazna:
        cart.add(
            product,
            quantity=1,
            custom_price=cijena,
            promo_bazna=bazna,
            discount_source='Chat ponuda',
        )
    else:
        cart.add(product, quantity=1)

    return JsonResponse({
        'ok': True,
        'message': f'„{product.naziv}” je dodano u korpu.',
        'cart_count': len(cart),
        'cart_url': '/korpa/',
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
    conversations = list(
        ChatConversation.objects.filter(
            status=ChatConversation.Status.OPEN,
        ).order_by('-last_message_at')[:100]
    )
    serialized = serialize_conversations_list(conversations, include_preview=True)
    online_unread = sum(
        1 for c in serialized
        if c.get('is_online') and (c.get('staff_unread_count') or 0) > 0
    )
    return JsonResponse({
        'ok': True,
        'unread_conversations': staff_unread_total(),
        'online_unread_conversations': online_unread,
        'conversations': serialized,
    })


@require_GET
@login_required(login_url='login')
@user_passes_test(_staff_required)
def chat_staff_conversation(request, pk):
    mark_staff_online()
    conversation = get_object_or_404(ChatConversation, pk=pk)
    messages = list(_messages_qs(conversation)[:300])
    mark_conversation_read_by_staff(conversation)
    return JsonResponse({
        'ok': True,
        'conversation': serialize_conversation_summary(conversation, include_preview=False),
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

    conversation = get_object_or_404(
        ChatConversation,
        pk=pk,
        status=ChatConversation.Status.OPEN,
    )
    message = add_staff_message(conversation, request.user, body)
    return JsonResponse({
        'ok': True,
        'message': serialize_message(message),
        'unread_conversations': staff_unread_total(),
    })


@require_POST
@login_required(login_url='login')
@user_passes_test(_staff_required)
def chat_staff_send_product(request, pk):
    """Staff šalje artikal (opcionalni % popust) u chat."""
    data = _json_body(request)
    try:
        product_id = int(data.get('product_id') or 0)
    except (TypeError, ValueError):
        product_id = 0
    if not product_id:
        return JsonResponse({'ok': False, 'error': 'Odaberite artikal.'}, status=400)

    product = Product.objects.filter(pk=product_id, aktivan=True).first()
    if not product:
        return JsonResponse({'ok': False, 'error': 'Artikal nije pronađen.'}, status=404)

    popust = data.get('popust_postotak')
    if popust is not None and str(popust).strip() != '':
        try:
            popust = Decimal(str(popust).replace(',', '.'))
        except (InvalidOperation, ValueError):
            return JsonResponse({'ok': False, 'error': 'Neispravan popust %.'}, status=400)
    else:
        popust = None

    note = (data.get('note') or data.get('body') or '').strip()
    conversation = get_object_or_404(
        ChatConversation,
        pk=pk,
        status=ChatConversation.Status.OPEN,
    )
    message = add_staff_product_offer(
        conversation,
        request.user,
        product,
        popust_postotak=popust,
        note=note,
    )
    # reload product for serialization
    message = ChatMessage.objects.select_related('product', 'staff_user').get(pk=message.pk)
    return JsonResponse({
        'ok': True,
        'message': serialize_message(message),
        'unread_conversations': staff_unread_total(),
    })


@require_GET
@login_required(login_url='login')
@user_passes_test(_staff_required)
def chat_staff_product_search(request):
    """Brza pretraga artikala za slanje u chat."""
    q = (request.GET.get('q') or '').strip()
    qs = Product.objects.filter(aktivan=True)
    if q:
        qs = qs.filter(
            Q(naziv__icontains=q)
            | Q(sifra__icontains=q)
            | Q(slug__icontains=q),
        )
    products = list(qs.order_by('naziv')[:20])
    results = []
    for product in products:
        price = product.prikazna_cijena
        results.append({
            'id': product.pk,
            'label': product.naziv,
            'sifra': product.sifra or '',
            'price': f'{price:.2f}',
            'image': product.prikazna_slika.url if product.prikazna_slika else '',
        })
    return JsonResponse({'ok': True, 'results': results, 'query': q})


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


@require_POST
@login_required(login_url='login')
@user_passes_test(_staff_required)
def chat_staff_close(request, pk):
    conversation = get_object_or_404(ChatConversation, pk=pk)
    conversation.status = ChatConversation.Status.CLOSED
    conversation.save(update_fields=['status'])
    return JsonResponse({
        'ok': True,
        'unread_conversations': staff_unread_total(),
    })
