"""Online nagrada — poklon za posjetioce koji su trenutno na sajtu."""

from __future__ import annotations

import random
import re
from datetime import timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from django.db.models import Q
from django.utils import timezone

from .models import (
    ActiveCartItem,
    LiveVisitor,
    OnlineGiftCampaign,
    OnlineGiftClaim,
    OnlineGiftPush,
    Order,
    Product,
)

_EMAIL_RE = re.compile(r'^[^\s@]+@[^\s@]+\.[^\s@]+$')

SESSION_REWARD_KEY = 'online_gift_reward'
SESSION_PLAYED_KEY = 'online_gift_played'
SESSION_DECLINED_KEY = 'online_gift_declined'
# Gost kliknuo „Registruj se i igraj” — ne prikazuj nagradu dok se ne uloguje
SESSION_HIDE_GUEST_KEY = 'online_gift_hide_guest'
# Nakon prijave (poslije registracije) odmah prikaži igru
SESSION_AFTER_AUTH_KEY = 'online_gift_play_after_auth'
# legacy session keys from greb/wheel
_LEGACY_KEYS = (
    'greb_greb_reward',
    'prize_wheel_reward',
    'greb_greb_played',
    'prize_wheel_spun',
)

ONLINE_WINDOW_MINUTES = 30
# Staff feed: koliko unazad pratimo pobjednike
STAFF_FEED_HOURS = 48
STAFF_FEED_LIMIT = 40
# Online badge u feedu (usklađeno s live_visitors.ONLINE_MINUTES)
STAFF_ONLINE_MINUTES = 1
# Bočni auto nagradna igra — uključuje se u SiteSettings
WELCOME_GIFT_DELAY_DEFAULT = 15


def _side_gift_settings():
    """(aktivan, delay_sekundi) — bočni pulsirajući popup nagradne igre."""
    try:
        from .models import SiteSettings

        s = SiteSettings.load()
        aktivan = bool(getattr(s, 'online_nagrada_bočni_aktivan', False))
        try:
            delay = max(0, int(getattr(s, 'online_nagrada_delay_seconds', None) or WELCOME_GIFT_DELAY_DEFAULT))
        except (TypeError, ValueError):
            delay = WELCOME_GIFT_DELAY_DEFAULT
        return aktivan, delay
    except Exception:
        return False, WELCOME_GIFT_DELAY_DEFAULT


def _q(value):
    return Decimal(value).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


def _session_key(request):
    if not request or not getattr(request, 'session', None):
        return ''
    if not request.session.session_key:
        try:
            request.session.save()
        except Exception:
            return ''
    return request.session.session_key or ''


def get_active_campaign():
    return (
        OnlineGiftCampaign.objects.filter(aktivan=True)
        .select_related('product')
        .order_by('-azurirano', '-id')
        .first()
    )


def _is_tracked_online(request):
    """Posjetilac kojeg staff vidi u uživo analitici (nedavna aktivnost)."""
    key = _session_key(request)
    if not key:
        return False
    cutoff = timezone.now() - timedelta(minutes=ONLINE_WINDOW_MINUTES)
    return LiveVisitor.objects.filter(
        session_key=key,
        last_seen__gte=cutoff,
    ).exists()


def _already_played(request, campaign):
    if not campaign or not request:
        return False
    played = request.session.get(SESSION_PLAYED_KEY) or {}
    if str(campaign.pk) in played or campaign.pk in played:
        return True
    session_key = _session_key(request)
    user = getattr(request, 'user', None)
    lookup = Q()
    if session_key:
        lookup |= Q(session_key=session_key)
    if user is not None and getattr(user, 'is_authenticated', False):
        lookup |= Q(user=user)
    if not lookup:
        return False
    return OnlineGiftClaim.objects.filter(lookup, campaign=campaign).exists()


def _already_declined(request, campaign):
    """Kupac je u ovoj sesiji odbio ponudu (Ne, hvala / X) — ne nudi ponovo."""
    if not campaign or not request:
        return False
    declined = request.session.get(SESSION_DECLINED_KEY) or {}
    if str(campaign.pk) in declined or campaign.pk in declined:
        return True
    session_key = _session_key(request)
    if not session_key:
        return False
    return OnlineGiftPush.objects.filter(
        campaign=campaign,
        session_key=session_key,
        dismissed=True,
        played=False,
    ).exists()


def _campaign_id_str(campaign):
    return str(campaign.pk) if campaign else ''


def mark_gift_registration_intent(request):
    """
    Gost ide na registraciju zbog nagrade:
    - ne prikazuj popup gostu više
    - nakon uspješne prijave odmah ponudi igru
    (ne bilježi se kao odbijanje)
    """
    campaign = get_active_campaign()
    if not campaign or not request:
        return
    cid = _campaign_id_str(campaign)
    request.session[SESSION_HIDE_GUEST_KEY] = cid
    request.session[SESSION_AFTER_AUTH_KEY] = cid
    request.session.modified = True


def _guest_hidden_until_auth(request, campaign):
    if not campaign or not request:
        return False
    if _user_is_registered(request):
        return False
    return str(request.session.get(SESSION_HIDE_GUEST_KEY) or '') == _campaign_id_str(campaign)


def should_play_gift_after_auth(request, campaign=None):
    """Registrovan kupac koji je došao preko „Registruj se i igraj”."""
    campaign = campaign or get_active_campaign()
    if not campaign or not request:
        return False
    if not _user_is_registered(request):
        return False
    return str(request.session.get(SESSION_AFTER_AUTH_KEY) or '') == _campaign_id_str(campaign)


def clear_gift_after_auth(request):
    if not request:
        return
    request.session.pop(SESSION_AFTER_AUTH_KEY, None)
    request.session.pop(SESSION_HIDE_GUEST_KEY, None)
    request.session.modified = True


def _blocked_staff_path(request):
    path = getattr(request, 'path', '') or ''
    return path.startswith('/nalog/') or path.startswith('/admin')


def _base_eligible(request, campaign):
    """Zajednička pravila (publika, putanja, jednom, već ima nagradu)."""
    if not campaign:
        return False
    user = getattr(request, 'user', None)
    if user is not None and getattr(user, 'is_authenticated', False):
        if user.is_staff or user.is_superuser:
            if _blocked_staff_path(request):
                return False
    # Publika: gosti smiju igrati s emailom (ne traži aktivaciju naloga)
    if not campaign.audience_matches(user):
        # REGISTERED kampanja: i dalje prikaži gostu — email gate pri igri
        if campaign.audience != OnlineGiftCampaign.Audience.REGISTERED:
            return False
    if _blocked_staff_path(request):
        return False
    if campaign.once_per_visitor and _already_played(request, campaign):
        return False
    if _already_declined(request, campaign):
        return False
    reward = get_session_reward(request)
    if reward and int(reward.get('campaign_id') or 0) == campaign.pk:
        return False
    return True


def get_active_push(request, campaign=None):
    """Aktivni staff push za ovu sesiju (još nije otvoren/zatvoren)."""
    campaign = campaign or get_active_campaign()
    if not campaign:
        return None
    session_key = _session_key(request)
    if not session_key:
        return None
    return (
        OnlineGiftPush.objects.filter(
            campaign=campaign,
            session_key=session_key,
            played=False,
            dismissed=False,
        )
        .order_by('-kreirano')
        .first()
    )


def _seconds_on_site(request):
    key = _session_key(request)
    if not key:
        return 0
    visitor = LiveVisitor.objects.filter(session_key=key).only('first_seen').first()
    if not visitor or not visitor.first_seen:
        return 0
    return max(0, (timezone.now() - visitor.first_seen).total_seconds())


def can_show_online_gift(request):
    """
    Treba li prikazati popup sada.
    - Staff push → bilo kome (odmah)
    - Nakon registracije/prijave → odmah
    - automatic / bočni → svima
    - Igranje traži email (ne aktivaciju naloga)
    """
    campaign = get_active_campaign()
    if not campaign or not _base_eligible(request, campaign):
        return None
    # Ručni push (staff) uvijek ima prioritet
    if get_active_push(request, campaign):
        return campaign
    # Upravo se registrovao/prijavio zbog nagrade — odmah igraj
    if should_play_gift_after_auth(request, campaign):
        return campaign

    side_on, side_delay = _side_gift_settings()
    # Auto bočni popup (SiteSettings) ili kampanja.automatic
    auto_on = side_on or bool(campaign.automatic)
    if not auto_on:
        return None
    # Bočni / auto — odmah svima
    return campaign


def campaign_shell_enabled(request):
    """Učitaj JS/shell za poll (i kad je manuelno / bočni toggle)."""
    campaign = get_active_campaign()
    if not campaign:
        return False
    if _blocked_staff_path(request):
        return False
    user = getattr(request, 'user', None)
    if user is not None and getattr(user, 'is_authenticated', False):
        if user.is_staff or user.is_superuser:
            return False
    # Shell i kad je samo bočni toggle (kampanja automatic=False)
    side_on, _ = _side_gift_settings()
    if not campaign.aktivan:
        return False
    if not (campaign.automatic or side_on):
        # I dalje shell za staff push
        return True
    return True


def _user_is_registered(request):
    user = getattr(request, 'user', None)
    return bool(user is not None and getattr(user, 'is_authenticated', False))


def _registered_email(request):
    user = getattr(request, 'user', None)
    if user is not None and getattr(user, 'is_authenticated', False):
        return (user.email or '').strip()
    return ''


def _session_gift_email(request):
    """Email iz sesije (gost unio za igru) ili sa naloga."""
    if not request:
        return ''
    reg = _registered_email(request)
    if reg:
        return reg
    return _normalize_email(request.session.get('online_gift_email') or '')


def gift_requires_email(request):
    """
    Da bi igrao, mora unijeti email (ili biti ulogovan s emailom).
    Ne traži aktivaciju naloga — samo email za kontakt/praćenje.
    """
    return not bool(_session_gift_email(request))


def gift_requires_registration(request):
    """Više se ne forsira registracija — dovoljan je email."""
    return False


def _normalize_email(value):
    email = (value or '').strip().lower()
    if not email or len(email) > 254:
        return ''
    if not _EMAIL_RE.match(email):
        return ''
    return email


def _persist_guest_email(request, email):
    """Sačuvaj email u sesiju + LiveVisitor da ga staff vidi u uživo analitici."""
    email = _normalize_email(email)
    if not email:
        return ''
    request.session['online_gift_email'] = email
    request.session.modified = True
    session_key = _session_key(request)
    if session_key:
        LiveVisitor.objects.filter(session_key=session_key).update(email=email[:254])
        # Ako još nema ime, stavi dio prije @
        LiveVisitor.objects.filter(
            session_key=session_key,
            ime__in=['', 'Gost'],
        ).update(ime=email.split('@', 1)[0][:120])
    return email


def _campaign_payload(campaign, *, delay_seconds=None, show_now=False, source='auto', request=None):
    product = None
    if campaign.prize_type == OnlineGiftCampaign.PrizeType.PRODUCT and campaign.product_id:
        product = _product_payload(campaign.product)
    delay = int(campaign.popup_delay_seconds or 0) if delay_seconds is None else int(delay_seconds)
    requires_registration = False
    requires_email = True
    is_registered = False
    user_email = ''
    force_show = False
    # Uvijek bočni pulsirajući stil — ne fullscreen, stranica ostaje skrolabilna
    side_mode = True
    if request is not None:
        is_registered = _user_is_registered(request)
        requires_registration = False
        user_email = _session_gift_email(request) or ''
        requires_email = gift_requires_email(request)
        force_show = bool(show_now and should_play_gift_after_auth(request, campaign))
    return {
        'id': campaign.pk,
        'naslov': campaign.naslov or 'Online nagrada za tebe!',
        'poruka': campaign.poruka or '',
        'delay_seconds': delay,
        'prize_type': campaign.prize_type,
        'prize_label': campaign.prize_label(),
        'product': product,
        'automatic': bool(campaign.automatic),
        'show_now': bool(show_now),
        'force_show': force_show,
        'side_mode': side_mode,
        'source': source,
        'requires_registration': requires_registration,
        'is_registered': is_registered,
        'requires_email': requires_email,
        'user_email': user_email,
        'register_url': '/registracija/?next=/',
        'claim_url': '/online-nagrada/otkrij/',
        'dismiss_url': '/online-nagrada/zatvori/',
        'poll_url': '/online-nagrada/status/',
    }


def build_online_gift_context(request):
    """Server-side: bočni/auto popup ili shell za poll (staff push)."""
    campaign = get_active_campaign()
    if not campaign or not campaign_shell_enabled(request):
        return None
    show = can_show_online_gift(request)
    if show:
        push = get_active_push(request, campaign)
        after_auth = should_play_gift_after_auth(request, campaign)
        if push:
            source = 'manual'
        elif after_auth:
            source = 'after_auth'
        else:
            source = 'auto'
        return _campaign_payload(
            campaign, delay_seconds=0, show_now=True, source=source, request=request,
        )
    # Bočni auto: odmah sa strane za sve (bez delay-a / tracked filtera)
    side_on, _side_delay = _side_gift_settings()
    if (
        (side_on or campaign.automatic)
        and _base_eligible(request, campaign)
        and not _guest_hidden_until_auth(request, campaign)
    ):
        return _campaign_payload(
            campaign,
            delay_seconds=0,
            show_now=True,
            source='auto',
            request=request,
        )
    return _campaign_payload(
        campaign, delay_seconds=0, show_now=False, source='poll', request=request,
    )


def poll_online_gift(request):
    """JSON status za klijentski poll — kad staff pusti nagradu uživo."""
    campaign = can_show_online_gift(request)
    if not campaign:
        return {'active': False}
    source = 'auto' if campaign.automatic else 'manual'
    payload = _campaign_payload(
        campaign,
        delay_seconds=0,
        show_now=True,
        source=source,
        request=request,
    )
    return {'active': True, 'gift': payload}


def push_online_gift_to_visitor(*, session_key, staff_user=None, target_user=None):
    """Staff: ručno pusti nagradu kupcu na sajtu."""
    session_key = (session_key or '').strip()
    if not session_key:
        raise ValueError('Nema sesije kupca.')
    campaign = get_active_campaign()
    if not campaign:
        raise ValueError('Nema aktivne online nagrade. Uključi je u adminu.')

    # Već odigrao?
    lookup = Q(session_key=session_key)
    if target_user is not None and getattr(target_user, 'pk', None):
        lookup |= Q(user_id=target_user.pk)
    if campaign.once_per_visitor and OnlineGiftClaim.objects.filter(
        lookup, campaign=campaign,
    ).exists():
        raise ValueError('Ovaj posjetilac je već otvorio nagradu.')

    push, created = OnlineGiftPush.objects.update_or_create(
        campaign=campaign,
        session_key=session_key,
        defaults={
            'user': target_user if target_user is not None and getattr(target_user, 'pk', None) else None,
            'staff': staff_user if staff_user is not None and getattr(staff_user, 'is_authenticated', False) else None,
            'played': False,
            'dismissed': False,
        },
    )
    return push, created


def set_campaign_automatic(automatic):
    """Uključi/isključi automatski režim aktivne kampanje."""
    campaign = get_active_campaign()
    if not campaign:
        raise ValueError('Nema aktivne online nagrade.')
    campaign.automatic = bool(automatic)
    campaign.save(update_fields=['automatic', 'azurirano'])
    return campaign


def get_campaign_staff_status():
    campaign = get_active_campaign()
    if not campaign:
        return {
            'active': False,
            'automatic': False,
            'campaign_id': None,
            'campaign_name': '',
            'prize_label': '',
        }
    return {
        'active': True,
        'automatic': bool(campaign.automatic),
        'campaign_id': campaign.pk,
        'campaign_name': campaign.naziv or '',
        'prize_label': campaign.prize_label(),
    }


def get_session_reward(request):
    if not request:
        return None
    raw = request.session.get(SESSION_REWARD_KEY)
    if not isinstance(raw, dict):
        for k in _LEGACY_KEYS:
            raw = request.session.get(k)
            if isinstance(raw, dict) and not raw.get('consumed'):
                break
        else:
            return None
    if raw.get('consumed'):
        return None
    return raw


def clear_session_reward(request):
    if not request:
        return
    changed = False
    for key in (SESSION_REWARD_KEY,) + _LEGACY_KEYS:
        if key in request.session:
            del request.session[key]
            changed = True
    if changed:
        request.session.modified = True


def mark_reward_consumed(request, order=None):
    """Označi nagradu iskorištenom; veži na narudžbu ako je proslijeđena."""
    reward = get_session_reward(request)
    if not reward:
        return
    claim_id = reward.get('claim_id') or reward.get('spin_id')
    if claim_id:
        update_fields = {'reward_consumed': True}
        if order is not None and getattr(order, 'pk', None):
            update_fields['order_id'] = order.pk
        OnlineGiftClaim.objects.filter(pk=claim_id).update(**update_fields)
    clear_session_reward(request)


def _ago_label(dt, now):
    if not dt:
        return '—'
    seconds = max(0, int((now - dt).total_seconds()))
    if seconds < 60:
        return 'upravo sada'
    if seconds < 3600:
        return f'prije {seconds // 60} min'
    if seconds < 86400:
        return f'prije {seconds // 3600} h'
    return f'prije {seconds // 86400} d'


def _visitor_for_claim(claim, visitor_by_session, visitor_by_user):
    if claim.session_key and claim.session_key in visitor_by_session:
        return visitor_by_session[claim.session_key]
    if claim.user_id and claim.user_id in visitor_by_user:
        return visitor_by_user[claim.user_id]
    return None


def _display_name_for_claim(claim, visitor):
    if visitor and (visitor.ime or '').strip():
        return visitor.ime.strip()[:120]
    user = claim.user
    if user is not None:
        full = (user.get_full_name() or '').strip()
        if full:
            return full[:120]
        first = (user.first_name or '').strip()
        if first:
            return first[:120]
        email = (user.email or '').strip()
        if email:
            return email.split('@', 1)[0][:120]
    if visitor and (visitor.email or '').strip():
        return visitor.email.strip().split('@', 1)[0][:120]
    return 'Gost'


def _display_email_for_claim(claim, visitor):
    if visitor and (visitor.email or '').strip():
        return visitor.email.strip()
    user = claim.user
    if user is not None and (user.email or '').strip():
        return user.email.strip()
    return ''


def _resolve_order_for_claim(claim, orders_by_user, orders_by_email, email):
    """Veza na narudžbu: FK, ili heuristika po user/email nakon pobjede."""
    if claim.order_id and claim.order:
        return claim.order
    if claim.user_id and claim.user_id in orders_by_user:
        order = orders_by_user[claim.user_id]
        if order.kreirana and claim.kreirano and order.kreirana >= claim.kreirano:
            return order
    email_key = (email or '').strip().lower()
    if email_key and email_key in orders_by_email:
        order = orders_by_email[email_key]
        if order.kreirana and claim.kreirano and order.kreirana >= claim.kreirano:
            return order
    return None


def get_online_gift_staff_feed(*, limit=STAFF_FEED_LIMIT, hours=STAFF_FEED_HOURS):
    """
    Staff live feed: ko je osvojio online nagradu, šta radi poslije, da li je poručio.
    """
    now = timezone.now()
    cutoff = now - timedelta(hours=hours)
    claims = list(
        OnlineGiftClaim.objects.filter(
            won=True,
            reward_claimed=True,
            kreirano__gte=cutoff,
        )
        .select_related('campaign', 'product', 'user', 'order')
        .order_by('-kreirano')[:limit]
    )
    if not claims:
        return {
            'winners': [],
            'winners_count': 0,
            'ordered_count': 0,
            'online_winners_count': 0,
            'hours': hours,
        }

    session_keys = [c.session_key for c in claims if c.session_key]
    user_ids = [c.user_id for c in claims if c.user_id]

    visitors = []
    if session_keys or user_ids:
        q = Q()
        if session_keys:
            q |= Q(session_key__in=session_keys)
        if user_ids:
            q |= Q(user_id__in=user_ids)
        visitors = list(
            LiveVisitor.objects.filter(q)
            .select_related('user')
            .order_by('-last_seen')
        )
    visitor_by_session = {}
    visitor_by_user = {}
    for v in visitors:
        if v.session_key and v.session_key not in visitor_by_session:
            visitor_by_session[v.session_key] = v
        if v.user_id and v.user_id not in visitor_by_user:
            visitor_by_user[v.user_id] = v

    cart_value_by_session = {}
    cart_value_by_user = {}
    if session_keys:
        from django.db.models import Sum

        for row in (
            ActiveCartItem.objects.filter(session_key__in=session_keys)
            .values('session_key')
            .annotate(total=Sum('ukupno'))
        ):
            cart_value_by_session[row['session_key']] = row['total'] or Decimal('0')
    if user_ids:
        from django.db.models import Sum

        for row in (
            ActiveCartItem.objects.filter(user_id__in=user_ids)
            .values('user_id')
            .annotate(total=Sum('ukupno'))
        ):
            cart_value_by_user[row['user_id']] = row['total'] or Decimal('0')

    # Narudžbe poslije najstarijeg claim-a (za backfill bez order FK)
    oldest = min((c.kreirano for c in claims if c.kreirano), default=cutoff)
    emails_for_orders = set()
    for c in claims:
        if c.user_id and c.user and (c.user.email or '').strip():
            emails_for_orders.add(c.user.email.strip())
        v = _visitor_for_claim(c, visitor_by_session, visitor_by_user)
        em = _display_email_for_claim(c, v)
        if em:
            emails_for_orders.add(em)

    orders_by_user = {}
    orders_by_email = {}
    order_lookup = Q()
    if user_ids:
        order_lookup |= Q(korisnik_id__in=user_ids)
    if emails_for_orders:
        order_lookup |= Q(email__in=list(emails_for_orders))
    if order_lookup:
        recent_orders = (
            Order.objects.filter(order_lookup, kreirana__gte=oldest)
            .exclude(status=Order.Status.OTKAZANA)
            .order_by('-kreirana')
        )
        for order in recent_orders:
            if order.korisnik_id and order.korisnik_id not in orders_by_user:
                orders_by_user[order.korisnik_id] = order
            email_key = (order.email or '').strip().lower()
            if email_key and email_key not in orders_by_email:
                orders_by_email[email_key] = order

    winners = []
    ordered_count = 0
    online_winners_count = 0
    online_cutoff = now - timedelta(minutes=STAFF_ONLINE_MINUTES)

    for claim in claims:
        visitor = _visitor_for_claim(claim, visitor_by_session, visitor_by_user)
        email = _display_email_for_claim(claim, visitor)
        ime = _display_name_for_claim(claim, visitor)
        grad = ''
        if visitor:
            grad = (visitor.grad or '').strip()

        order = _resolve_order_for_claim(claim, orders_by_user, orders_by_email, email)

        cart_value = Decimal('0')
        if claim.user_id and claim.user_id in cart_value_by_user:
            cart_value = cart_value_by_user[claim.user_id] or Decimal('0')
        elif claim.session_key and claim.session_key in cart_value_by_session:
            cart_value = cart_value_by_session[claim.session_key] or Decimal('0')
        try:
            cart_value = Decimal(str(cart_value or 0)).quantize(Decimal('0.01'))
        except Exception:
            cart_value = Decimal('0.00')
        has_cart = cart_value > 0

        is_online = bool(
            visitor
            and visitor.last_seen
            and visitor.last_seen > online_cutoff
        )
        if is_online:
            online_winners_count += 1

        products = []
        categories = []
        if visitor:
            for item in (visitor.pregledani_proizvodi or [])[:6]:
                if isinstance(item, dict) and item.get('naziv'):
                    products.append({
                        'id': item.get('id') or 0,
                        'naziv': str(item.get('naziv') or '')[:80],
                        'views': int(item.get('views') or 1),
                    })
            for c in (visitor.pregledane_kategorije or [])[:6]:
                if isinstance(c, dict):
                    name = str(c.get('naziv') or c.get('name') or '').strip()
                else:
                    name = str(c or '').strip()
                if name:
                    categories.append(name)

        # Status funnel: poručio → u korpi → online → nagrada čeka
        if order:
            status_key = 'ordered'
            status_label = 'Poručio'
            ordered_count += 1
        elif claim.reward_consumed:
            status_key = 'consumed'
            status_label = 'Iskorišteno'
            ordered_count += 1
        elif has_cart:
            status_key = 'in_cart'
            status_label = 'U korpi'
        elif is_online:
            status_key = 'browsing'
            status_label = 'Gleda sajt'
        else:
            status_key = 'won'
            status_label = 'Osvojio — čeka'

        activity_bits = []
        if is_online:
            activity_bits.append('online sada')
        elif visitor and visitor.last_seen:
            activity_bits.append(_ago_label(visitor.last_seen, now))
        if has_cart:
            activity_bits.append(f'korpa {cart_value:.2f} KM')
        if products:
            activity_bits.append(
                products[0]['naziv'][:36]
                + (f' (+{len(products) - 1})' if len(products) > 1 else '')
            )
        elif categories:
            activity_bits.append(categories[0][:36])
        activity_label = ' · '.join(activity_bits) if activity_bits else 'Nema dalje aktivnosti'

        winners.append({
            'claim_id': claim.pk,
            'campaign_name': (
                claim.campaign.naziv if claim.campaign_id and claim.campaign else ''
            ),
            'prize_label': claim.prize_label(),
            'prize_type': claim.prize_type or '',
            'ime': ime,
            'email': email,
            'grad': grad,
            'session_key': claim.session_key or '',
            'user_id': claim.user_id or None,
            'is_registered': bool(claim.user_id),
            'is_online': is_online,
            'has_cart': has_cart,
            'cart_value': str(cart_value),
            'cart_value_label': f'{cart_value:.2f} KM' if cart_value > 0 else '—',
            'products': products,
            'products_label': ', '.join(p['naziv'] for p in products[:3]) or '—',
            'categories': categories,
            'categories_label': ', '.join(categories[:3]) or '—',
            'activity_label': activity_label,
            'last_seen_label': (
                _ago_label(visitor.last_seen, now) if visitor and visitor.last_seen else '—'
            ),
            'won_at': claim.kreirano,
            'won_at_label': timezone.localtime(claim.kreirano).strftime('%d.%m. %H:%M'),
            'won_ago_label': _ago_label(claim.kreirano, now),
            'reward_consumed': bool(claim.reward_consumed),
            'has_ordered': bool(order) or bool(claim.reward_consumed),
            'order_id': order.pk if order else None,
            'order_number': order.broj if order else '',
            'order_total': str(order.ukupno) if order else '',
            'order_total_label': (
                f'{order.ukupno:.2f} KM' if order else ''
            ),
            'status_key': status_key,
            'status_label': status_label,
        })

    return {
        'winners': winners,
        'winners_count': len(winners),
        'ordered_count': ordered_count,
        'online_winners_count': online_winners_count,
        'hours': hours,
    }


def _mark_push_done(request, campaign, *, played=False, dismissed=False):
    session_key = _session_key(request)
    if not session_key or not campaign:
        return
    qs = OnlineGiftPush.objects.filter(
        campaign=campaign,
        session_key=session_key,
        played=False,
        dismissed=False,
    )
    fields = {}
    if played:
        fields['played'] = True
    if dismissed:
        fields['dismissed'] = True
    if fields:
        qs.update(**fields)


def dismiss_online_gift(request):
    """
    Kupac zatvorio nagradu.
    reason=register → ide na registraciju (ne broji se kao odbijanje; poslije prijave igra odmah).
    Inače: odbio (X / Ne, hvala) — ne nudi ponovo.
    """
    campaign = get_active_campaign()
    if not campaign:
        return

    reason = ''
    try:
        reason = (request.POST.get('reason') or request.GET.get('reason') or '').strip().lower()
    except Exception:
        reason = ''

    if reason in ('register', 'registracija', 'reg'):
        mark_gift_registration_intent(request)
        return

    # Pravo odbijanje
    clear_gift_after_auth(request)
    declined = request.session.get(SESSION_DECLINED_KEY) or {}
    declined[str(campaign.pk)] = 1
    request.session[SESSION_DECLINED_KEY] = declined
    request.session.modified = True

    session_key = _session_key(request)
    if not session_key:
        return

    # Aktivni push (ručni) → dismissed
    updated = OnlineGiftPush.objects.filter(
        campaign=campaign,
        session_key=session_key,
        played=False,
        dismissed=False,
    ).update(dismissed=True)

    if updated:
        return

    # Automatska ponuda: nema push-a — kreiraj zapis da staff vidi „odbio”
    already = OnlineGiftPush.objects.filter(
        campaign=campaign,
        session_key=session_key,
    ).order_by('-kreirano').first()
    if already and (already.played or already.dismissed):
        return

    user = getattr(request, 'user', None)
    OnlineGiftPush.objects.create(
        campaign=campaign,
        session_key=session_key,
        user=(
            user
            if user is not None and getattr(user, 'is_authenticated', False)
            else None
        ),
        played=False,
        dismissed=True,
    )


def _roll_win(campaign):
    try:
        chance = Decimal(str(campaign.win_chance_percent or 0))
    except (InvalidOperation, TypeError, ValueError):
        chance = Decimal('0')
    if chance <= 0:
        return False
    if chance >= 100:
        return True
    return Decimal(str(random.uniform(0, 100))) < chance


def _product_payload(product):
    if not product:
        return None
    image = ''
    try:
        if product.prikazna_slika:
            image = product.prikazna_slika.url
    except Exception:
        image = ''
    try:
        price_str = f'{_q(product.prikazna_cijena):.2f}'
    except Exception:
        price_str = '0'
    return {
        'id': product.pk,
        'naziv': product.naziv or 'Artikal',
        'image': image,
        'price': price_str,
        'url': product.get_absolute_url() if hasattr(product, 'get_absolute_url') else '',
        'cart_url': '/korpa/',
    }


def _add_free_product(request, product):
    from .cart import Cart

    if not product or not product.aktivan or not product.na_stanju:
        return False
    variation = None
    variations = list(
        product.varijacije.filter(na_stanju=True).order_by('redoslijed', 'id')[:1],
    )
    if variations:
        variation = variations[0]
    elif product.varijacije.exists():
        return False
    cart = Cart(request)
    key = cart._line_key(product.pk, variation.pk if variation else None)
    if key in cart.cart and cart.cart[key].get('online_gift'):
        return True
    bazna = variation.prikazna_cijena if variation else product.prikazna_cijena
    cart.add(
        product,
        variation=variation,
        quantity=1,
        custom_price=Decimal('0.00'),
        promo_bazna=bazna,
        discount_source='Online nagrada (gratis artikal)',
        discount_percent=Decimal('100'),
    )
    if key in cart.cart:
        cart.cart[key]['online_gift'] = True
        cart.cart[key]['prize_wheel'] = True  # legacy cart flags
        cart.save()
    return True


def reveal_online_gift(request):
    """Otkrij nagradu (jedan klik) — win/lose + primjena nagrade. Traži email, ne aktivaciju."""
    campaign = get_active_campaign()
    if not campaign:
        raise ValueError('Online nagrada trenutno nije aktivna.')
    user = getattr(request, 'user', None)
    # Email obavezan (POST ili već sačuvan / sa naloga) — bez aktivacije računa
    post_email = _normalize_email(request.POST.get('email') or '')
    if post_email:
        _persist_guest_email(request, post_email)
    email = _session_gift_email(request)
    if not email:
        raise ValueError(
            'Unesite email da biste mogli igrati nagradnu igru. '
            'Nije potrebna aktivacija naloga.'
        )
    # Audience REGISTERED: dozvoli i gostu s emailom (samo email gate)
    if campaign.audience == OnlineGiftCampaign.Audience.REGISTERED and not _user_is_registered(request):
        # I dalje dozvoli igru s emailom — staff vidi kontakt
        pass
    elif not campaign.audience_matches(user) and not email:
        raise ValueError('Nagrada nije dostupna za vaš nalog.')
    if campaign.once_per_visitor and _already_played(request, campaign):
        raise ValueError('Već ste otvorili online nagradu.')
    # Manuelni režim: mora postojati staff push (ili after-auth / auto)
    if (
        not campaign.automatic
        and not get_active_push(request, campaign)
        and not should_play_gift_after_auth(request, campaign)
    ):
        side_on, _ = _side_gift_settings()
        if not side_on:
            raise ValueError('Nagrada nije dostupna. Staff ju još nije pustio.')

    # Iskoristio „poslije registracije” slot
    if should_play_gift_after_auth(request, campaign):
        clear_gift_after_auth(request)

    won = _roll_win(campaign)
    session_key = _session_key(request)
    claim = OnlineGiftClaim.objects.create(
        campaign=campaign,
        session_key=session_key or '',
        user=user if _user_is_registered(request) else None,
        won=won,
        prize_type=campaign.prize_type if won else '',
        product=(
            campaign.product
            if won and campaign.prize_type == OnlineGiftCampaign.PrizeType.PRODUCT
            else None
        ),
        discount_percent=(
            campaign.discount_percent or Decimal('0')
            if won and campaign.prize_type == OnlineGiftCampaign.PrizeType.PERCENT
            else Decimal('0')
        ),
        discount_km=(
            campaign.discount_km or Decimal('0')
            if won and campaign.prize_type == OnlineGiftCampaign.PrizeType.FIXED_KM
            else Decimal('0')
        ),
        reward_claimed=False,
        reward_consumed=False,
    )
    _mark_push_done(request, campaign, played=True)

    played = request.session.get(SESSION_PLAYED_KEY) or {}
    played[str(campaign.pk)] = claim.pk
    request.session[SESSION_PLAYED_KEY] = played
    request.session.modified = True

    result = {
        'ok': True,
        'won': won,
        'claim_id': claim.pk,
        'prize_label': campaign.prize_label() if won else '',
        'title': '',
        'message': '',
        'reward': None,
    }

    if not won:
        result['title'] = 'Sreću drugi put!'
        result['message'] = (
            'Hvala što ste na sajtu. Ovaj put niste dobili poklon — '
            'pridružite se sljedeći put.'
        )
        return result

    prize_type = campaign.prize_type
    reward = {
        'claim_id': claim.pk,
        'campaign_id': campaign.pk,
        'type': prize_type,
        'label': campaign.prize_label(),
        'percent': '0',
        'km': '0',
        'product_id': None,
        'free_shipping': False,
        'consumed': False,
        'product': None,
    }

    if prize_type == OnlineGiftCampaign.PrizeType.PRODUCT:
        product = campaign.product
        if not product or not _add_free_product(request, product):
            raise ValueError('Artikal nagrade nije dostupan.')
        reward['product_id'] = product.pk
        reward['product'] = _product_payload(product)
        result['title'] = 'Čestitamo!'
        result['message'] = (
            f'Osvojili ste gratis artikal „{product.naziv}”. '
            f'Ubačen je u korpu. Dostava se naplaćuje.'
        )
    elif prize_type == OnlineGiftCampaign.PrizeType.PERCENT:
        pct = _q(campaign.discount_percent or 0)
        if pct <= 0:
            raise ValueError('Neispravan %.')
        reward['percent'] = str(pct)
        pct_label = int(pct) if pct == int(pct) else pct
        result['title'] = 'Čestitamo!'
        result['message'] = (
            f'Osvojili ste {pct_label}% popusta na narudžbu (jednokratno). '
            f'Dostava se naplaćuje.'
        )
    elif prize_type == OnlineGiftCampaign.PrizeType.FIXED_KM:
        km = _q(campaign.discount_km or 0)
        if km <= 0:
            raise ValueError('Neispravan KM.')
        reward['km'] = str(km)
        result['title'] = 'Čestitamo!'
        result['message'] = (
            f'Osvojili ste {km} KM popusta na korpu. Dostava se naplaćuje.'
        )
    elif prize_type == OnlineGiftCampaign.PrizeType.FREE_SHIPPING:
        reward['free_shipping'] = True
        result['title'] = 'Čestitamo!'
        result['message'] = 'Osvojili ste besplatnu dostavu na narudžbu.'
    else:
        raise ValueError('Nepoznat tip nagrade.')

    request.session[SESSION_REWARD_KEY] = reward
    request.session.modified = True
    claim.reward_claimed = True
    claim.save(update_fields=['reward_claimed'])
    result['reward'] = reward
    result['prize_label'] = reward['label']
    return result


def reward_discount_percent(request):
    reward = get_session_reward(request)
    if not reward or reward.get('type') != OnlineGiftCampaign.PrizeType.PERCENT:
        return Decimal('0')
    try:
        pct = Decimal(str(reward.get('percent') or 0))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal('0')
    return min(pct, Decimal('100')) if pct > 0 else Decimal('0')


def reward_discount_km(request):
    reward = get_session_reward(request)
    if not reward or reward.get('type') != OnlineGiftCampaign.PrizeType.FIXED_KM:
        return Decimal('0')
    try:
        km = Decimal(str(reward.get('km') or 0))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal('0')
    return km if km > 0 else Decimal('0')


def reward_free_shipping(request):
    reward = get_session_reward(request)
    return bool(reward and reward.get('free_shipping'))


def active_reward_label(request):
    reward = get_session_reward(request)
    if not reward:
        return ''
    return reward.get('label') or ''
