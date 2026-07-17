import secrets
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.contrib.auth.models import User
from django.db.models import Q
from django.utils import timezone

from .cart_tracking import get_cart_session_key
from .models import Coupon, LiveVisitorOffer, Order, Product, ProductVariation

OFFER_TIMER_MINUTES = 9
# Legacy — stari pozivi su nudili 10%; novi nude besplatnu dostavu.
REGISTRATION_INVITE_DISCOUNT = Decimal('0')
REGISTRATION_COUPON_NAME = 'Registracijski popust (uživo)'
REGISTRATION_FREE_SHIPPING_NAME = 'Besplatna dostava (registracija uživo)'
SESSION_REG_INVITE_KEY = 'live_reg_invite_pending'
SESSION_FREE_SHIPPING_KEY = 'cart_free_shipping_first'
# Welcome reg popup — uključuje se u SiteSettings
WELCOME_REG_DELAY_DEFAULT = 8
SESSION_WELCOME_REG_KEY = 'welcome_reg_invite_done'
SESSION_WELCOME_REG_CLOCK = 'welcome_reg_clock_ts'
AUTO_REG_CODE = 'AUTO-REG-WELCOME'
# AI dwell: odmah na ulasku na artikal → flash % popust (uključuje se u SiteSettings)
PRODUCT_DWELL_SECONDS = 0  # 0 = odmah na ulasku (nema čekanja)
PRODUCT_DWELL_FLASH_SECONDS = 120  # koliko traje snizena cijena (odbrojavanje od ulaska)
PRODUCT_DWELL_DISCOUNT_DEFAULT = Decimal('10')
SESSION_PRODUCT_DWELL_KEY = 'product_page_dwell'
SESSION_DWELL_FLASH_KEY = 'product_dwell_flash'  # {pid: {percent, expires_ts, base}}
AUTO_DWELL_CODE = 'AUTO-DWELL'


def _product_dwell_settings():
    """(aktivan, default popust %) iz SiteSettings — AI dwell, max 50%."""
    try:
        from .models import SiteSettings

        s = SiteSettings.load()
        aktivan = bool(getattr(s, 'product_dwell_popup_aktivan', False))
        percent = _clamp_percent(
            getattr(s, 'product_dwell_popust', None) or PRODUCT_DWELL_DISCOUNT_DEFAULT,
        )
        return aktivan, percent
    except Exception:
        return False, PRODUCT_DWELL_DISCOUNT_DEFAULT


def get_dwell_flash_seconds():
    """Trajanje flash cijene (sekundi) iz admina, clamp 30–3600."""
    try:
        from .models import SiteSettings

        s = SiteSettings.load()
        sec = int(getattr(s, 'product_dwell_flash_seconds', None) or PRODUCT_DWELL_FLASH_SECONDS)
        return max(30, min(sec, 3600))
    except Exception:
        return PRODUCT_DWELL_FLASH_SECONDS


def get_dwell_ui():
    """Tekstovi + CSS vars za AI dwell (iz SiteSettings)."""
    try:
        from .models import SiteSettings

        return SiteSettings.load().get_dwell_ui()
    except Exception:
        return {
            'active': False,
            'tag_text': 'Ograničena ponuda',
            'timer_label': 'Ističe za',
            'catalog_label': '',
            'flash_seconds': PRODUCT_DWELL_FLASH_SECONDS,
            'sale_pulse': True,
            'css_vars': '',
        }


def get_dwell_percent_for_product(product_id):
    """
    Popust % za artikal:
    1) ručni unos u ProductDwellItem (ako postoji)
    2) inače default iz SiteSettings
    """
    _, default_pct = _product_dwell_settings()
    if not product_id:
        return default_pct
    try:
        pid = int(product_id)
    except (TypeError, ValueError):
        return default_pct
    try:
        from .models import ProductDwellItem

        item = (
            ProductDwellItem.objects
            .filter(product_id=pid)
            .only('popust')
            .first()
        )
        if item and item.popust is not None and item.popust > 0:
            return _clamp_percent(item.popust)
    except Exception:
        pass
    return default_pct


def product_allowed_for_dwell(product_id):
    """
    True ako AI dwell smije raditi na ovom artiklu.
    Samo artikli iz ProductDwellItem (popust po artiklu) koji su aktivni i na stanju.
    """
    if not product_id:
        return False
    try:
        pid = int(product_id)
    except (TypeError, ValueError):
        return False
    try:
        from .models import ProductDwellItem, SiteSettings

        s = SiteSettings.load()
        if not bool(getattr(s, 'product_dwell_popup_aktivan', False)):
            return False
        return ProductDwellItem.objects.filter(
            settings=s,
            product_id=pid,
            product__aktivan=True,
            product__na_stanju=True,
        ).exists()
    except Exception:
        return False


def get_dwell_catalog_map():
    """
    Map product_id -> {percent, base, sale} za katalog/pretragu.
    Kad je AI dwell uključen — snizena cijena na karticama BEZ tajmera.
    Tajmer se aktivira tek na product page.
    """
    aktivan, _default_pct = _product_dwell_settings()
    if not aktivan:
        return {}
    try:
        from .models import ProductDwellItem, SiteSettings

        s = SiteSettings.load()
        items = (
            ProductDwellItem.objects
            .filter(
                settings=s,
                product__aktivan=True,
                product__na_stanju=True,
            )
            .select_related('product')
            .prefetch_related('product__varijacije')
        )
        result = {}
        for item in items:
            product = item.product
            pct = _clamp_percent(item.popust)
            if pct <= 0:
                continue
            try:
                # Katalog cijena (min varijacija ako postoje)
                base = product.katalog_prikazna_cijena
                base_d = Decimal(str(base))
            except (InvalidOperation, TypeError, ValueError, AttributeError):
                continue
            if base_d <= 0:
                continue
            sale_d = _discounted_price(base_d, pct)
            if sale_d >= base_d:
                continue
            if pct == pct.to_integral_value():
                pct_str = str(int(pct))
            else:
                pct_str = str(pct)
            # Ključ kao int — dict_get u templateu koristi product.pk
            result[int(product.pk)] = {
                'percent': pct_str,
                'base': str(base_d.quantize(Decimal('0.01'))),
                'sale': str(sale_d),
            }
        return result
    except Exception:
        return {}


def _welcome_reg_settings():
    """(aktivan, popust %, delay_sekundi) iz SiteSettings."""
    try:
        from .models import SiteSettings

        s = SiteSettings.load()
        aktivan = bool(getattr(s, 'welcome_reg_popup_aktivan', False))
        percent = _clamp_percent(
            getattr(s, 'welcome_reg_popust', None) or Decimal('10'),
        )
        try:
            raw_delay = getattr(s, 'welcome_reg_delay_seconds', None)
            if raw_delay is None:
                delay = WELCOME_REG_DELAY_DEFAULT
            else:
                delay = max(0, int(raw_delay))
        except (TypeError, ValueError):
            delay = WELCOME_REG_DELAY_DEFAULT
        return aktivan, percent, delay
    except Exception:
        return False, Decimal('10'), WELCOME_REG_DELAY_DEFAULT


def _clamp_percent(value):
    """Opšti clamp; AI/dwell koriste max 10 preko SiteSettings + AI_MAX."""
    try:
        percent = Decimal(str(value or 0))
    except (InvalidOperation, TypeError, ValueError):
        percent = Decimal('0')
    if percent < 0:
        return Decimal('0')
    if percent > 50:
        return Decimal('50')
    return percent.quantize(Decimal('0.01'))


def _discounted_price(base_price, percent):
    base_price = Decimal(str(base_price or 0))
    percent = _clamp_percent(percent)
    if percent <= 0:
        return base_price.quantize(Decimal('0.01'))
    return (base_price * (Decimal('1') - percent / Decimal('100'))).quantize(Decimal('0.01'))


def _generate_activation_code():
    return f'PONUDA-{secrets.token_hex(3).upper()[:6]}'


def _offer_lookup_q(request):
    session_key = get_cart_session_key(request)
    clauses = Q()
    if session_key:
        clauses |= Q(session_key=session_key)
    user = getattr(request, 'user', None)
    if user and user.is_authenticated:
        clauses |= Q(user=user)
    return clauses


def _active_offer_filter():
    return (
        Q(tip=LiveVisitorOffer.Tip.NARUDZBA, kod_aktiviran=False)
        | Q(tip=LiveVisitorOffer.Tip.ARTIKAL, added_to_cart=False)
        | Q(tip=LiveVisitorOffer.Tip.REGISTRACIJA)
    )


def _upsert_live_visitor_offer(session_key, defaults, *, target_user=None):
    if target_user and not isinstance(target_user, User):
        target_user = None

    defaults = dict(defaults)
    defaults['session_key'] = session_key

    tip = defaults.get('tip')
    product = defaults.get('product')
    # Artikal-ponude: jedna po proizvodu (može ih biti više aktivnih za istog kupca)
    if tip == LiveVisitorOffer.Tip.ARTIKAL and product is not None:
        qs = LiveVisitorOffer.objects.filter(
            session_key=session_key,
            tip=LiveVisitorOffer.Tip.ARTIKAL,
            product=product,
        )
        if target_user:
            defaults['user'] = target_user
            offer = qs.filter(user=target_user).first() or qs.first()
        else:
            defaults['user'] = None
            offer = qs.filter(user__isnull=True).first() or qs.first()
        if offer:
            for field, value in defaults.items():
                setattr(offer, field, value)
            offer.save(update_fields=list(defaults.keys()) + ['azurirano'])
            return offer
        return LiveVisitorOffer.objects.create(**defaults)

    # Narudžba / registracija: jedna aktivna po sesiji/useru (kao do sada)
    if target_user:
        defaults['user'] = target_user
        offer = LiveVisitorOffer.objects.filter(user=target_user).exclude(
            tip=LiveVisitorOffer.Tip.ARTIKAL,
        ).first()
        if not offer:
            offer = LiveVisitorOffer.objects.filter(session_key=session_key).exclude(
                tip=LiveVisitorOffer.Tip.ARTIKAL,
            ).first()
        if offer:
            for field, value in defaults.items():
                setattr(offer, field, value)
            offer.save(update_fields=list(defaults.keys()) + ['azurirano'])
        else:
            LiveVisitorOffer.objects.filter(session_key=session_key).exclude(
                tip=LiveVisitorOffer.Tip.ARTIKAL,
            ).delete()
            offer = LiveVisitorOffer.objects.create(**defaults)
    else:
        defaults['user'] = None
        offer = LiveVisitorOffer.objects.filter(
            session_key=session_key,
            user__isnull=True,
        ).exclude(tip=LiveVisitorOffer.Tip.ARTIKAL).first()
        if offer:
            for field, value in defaults.items():
                setattr(offer, field, value)
            offer.save(update_fields=list(defaults.keys()) + ['azurirano'])
        else:
            LiveVisitorOffer.objects.filter(
                session_key=session_key, user__isnull=True,
            ).exclude(tip=LiveVisitorOffer.Tip.ARTIKAL).delete()
            offer = LiveVisitorOffer.objects.create(**defaults)
    return offer


def is_auto_dwell_offer(offer):
    if not offer:
        return False
    code = (getattr(offer, 'aktivacioni_kod', None) or '').strip()
    return code == AUTO_DWELL_CODE or code.startswith(f'{AUTO_DWELL_CODE}-')


def _dwell_state(request):
    raw = request.session.get(SESSION_PRODUCT_DWELL_KEY)
    if not isinstance(raw, dict):
        return {'product_id': None, 'started_ts': None, 'offered_ids': []}
    offered = []
    for x in (raw.get('offered_ids') or []):
        try:
            offered.append(int(x))
        except (TypeError, ValueError):
            continue
    pid = raw.get('product_id')
    try:
        pid = int(pid) if pid else None
    except (TypeError, ValueError):
        pid = None
    started = raw.get('started_ts')
    try:
        started = float(started) if started is not None else None
    except (TypeError, ValueError):
        started = None
    return {'product_id': pid, 'started_ts': started, 'offered_ids': offered[:40]}


def _save_dwell_state(request, state):
    request.session[SESSION_PRODUCT_DWELL_KEY] = {
        'product_id': state.get('product_id'),
        'started_ts': state.get('started_ts'),
        'offered_ids': list(state.get('offered_ids') or [])[:40],
    }
    request.session.modified = True


def touch_product_dwell(request, product_id=None):
    """
    Prati koliko dugo kupac gleda isti artikal.
    product_id=None → napustio stranicu artikla (reset brojača).
    """
    if not request:
        return
    state = _dwell_state(request)
    now_ts = timezone.now().timestamp()
    if not product_id:
        if state.get('product_id') or state.get('started_ts'):
            state['product_id'] = None
            state['started_ts'] = None
            _save_dwell_state(request, state)
        return
    try:
        pid = int(product_id)
    except (TypeError, ValueError):
        return
    if pid <= 0:
        return
    if state.get('product_id') == pid and state.get('started_ts'):
        # Isti artikal — brojač teče dalje
        return
    state['product_id'] = pid
    state['started_ts'] = now_ts
    _save_dwell_state(request, state)


def touch_product_dwell_from_path(request, path=''):
    """Heartbeat: path /artikal/<slug> → nastavi dwell, inače reset."""
    import re

    path_only = ((path or '').strip().split('?', 1)[0]).rstrip('/') or '/'
    match = re.match(r'^/artikal/([^/]+)$', path_only)
    if not match:
        touch_product_dwell(request, None)
        return
    product = Product.objects.filter(slug=match.group(1), aktivan=True).only('pk').first()
    if product:
        touch_product_dwell(request, product.pk)
    else:
        touch_product_dwell(request, None)


def _flash_deals(request):
    raw = request.session.get(SESSION_DWELL_FLASH_KEY)
    return dict(raw) if isinstance(raw, dict) else {}


def _save_flash_deals(request, deals):
    request.session[SESSION_DWELL_FLASH_KEY] = deals
    request.session.modified = True


def get_active_dwell_flash(request, product_id):
    """
    Aktivna flash cijena za artikal (bez popupa).
    Vraća {percent, expires_ts, remaining_seconds, base, sale} ili None.
    """
    if not request or not product_id:
        return None
    try:
        pid = str(int(product_id))
    except (TypeError, ValueError):
        return None
    deals = _flash_deals(request)
    deal = deals.get(pid)
    if not isinstance(deal, dict):
        return None
    try:
        expires = float(deal.get('expires_ts') or 0)
    except (TypeError, ValueError):
        expires = 0
    now = timezone.now().timestamp()
    remaining = int(expires - now)
    if remaining <= 0:
        deals.pop(pid, None)
        _save_flash_deals(request, deals)
        return None
    try:
        percent = Decimal(str(deal.get('percent') or 0))
    except (InvalidOperation, TypeError, ValueError):
        percent = Decimal('0')
    if percent <= 0:
        return None
    base = deal.get('base')
    sale = deal.get('sale')
    try:
        pct_f = float(percent)
        pct_out = int(pct_f) if pct_f == int(pct_f) else pct_f
    except (TypeError, ValueError):
        pct_out = str(percent)
    return {
        'product_id': int(pid),
        'percent': percent,
        'percent_display': pct_out,
        'expires_ts': expires,
        'remaining_seconds': remaining,
        'base': base,
        'sale': sale,
    }


def get_all_active_dwell_flashes(request):
    """
    Sve aktivne AI dwell flash cijene u sesiji (za početnu / kartice).
    Vraća dict str(product_id) -> {percent_display, expires_ts, remaining_seconds, base, sale}.
    """
    if not request:
        return {}
    deals = _flash_deals(request)
    if not deals:
        return {}
    now = timezone.now().timestamp()
    changed = False
    result = {}
    for pid, deal in list(deals.items()):
        if not isinstance(deal, dict):
            deals.pop(pid, None)
            changed = True
            continue
        try:
            expires = float(deal.get('expires_ts') or 0)
        except (TypeError, ValueError):
            expires = 0
        remaining = int(expires - now)
        if remaining <= 0:
            deals.pop(pid, None)
            changed = True
            continue
        try:
            percent = Decimal(str(deal.get('percent') or 0))
        except (InvalidOperation, TypeError, ValueError):
            percent = Decimal('0')
        if percent <= 0:
            continue
        try:
            pct_f = float(percent)
            pct_out = int(pct_f) if pct_f == int(pct_f) else pct_f
        except (TypeError, ValueError):
            pct_out = str(percent)
        result[str(pid)] = {
            'product_id': int(pid) if str(pid).isdigit() else pid,
            'percent': pct_out,
            'expires_ts': expires,
            'remaining_seconds': remaining,
            'base': deal.get('base'),
            'sale': deal.get('sale'),
        }
    if changed:
        _save_flash_deals(request, deals)
    return result


def activate_product_dwell_flash(request, product_id, *, force=False):
    """
    Odmah na ulasku na artikal: aktiviraj 2-min flash cijenu (BEZ popupa).
    Timer kreće od trenutka ulaska; kad istekne — u ovoj sesiji više nema ponude
    (samo regularna cijena). Ako se vrati dok traje — nastavlja se preostalo vrijeme.

    force=True ili staff/superuser: dozvoli pregled i ponovni prikaz (test u admin nalogu).
    """
    aktivan, _default_pct = _product_dwell_settings()
    if not aktivan:
        return None, 'AI dwell nije aktivan.'
    if not request or _blocked_path(request):
        return None, 'Nedostupno.'

    user = getattr(request, 'user', None)
    is_staff_user = bool(
        user
        and getattr(user, 'is_authenticated', False)
        and (getattr(user, 'is_staff', False) or getattr(user, 'is_superuser', False))
    )
    # Staff/superuser MOGU vidjeti flash radi pregleda (prije su bili potpuno blokirani).
    force_preview = bool(force or is_staff_user)

    try:
        pid = int(product_id)
    except (TypeError, ValueError):
        return None, 'Neispravan artikal.'

    product = Product.objects.filter(pk=pid, aktivan=True).first()
    if not product:
        return None, 'Artikal nije pronađen.'
    if not getattr(product, 'na_stanju', False):
        return None, 'Artikal nije na stanju.'

    if not product_allowed_for_dwell(pid):
        return None, 'AI dwell nije uključen za ovaj artikal.'

    dwell_percent = get_dwell_percent_for_product(pid)
    if dwell_percent <= 0:
        return None, 'Popust za ovaj artikal nije postavljen.'

    # Već aktivna flash — vrati istu (povratak na artikal dok traje)
    active = get_active_dwell_flash(request, pid)
    if active:
        return active, None

    state = _dwell_state(request)
    offered_ids = list(state.get('offered_ids') or [])
    # Već isteklo u ovoj sesiji — samo regularna cijena (staff smije ponovo)
    if pid in offered_ids and not force_preview:
        return None, 'Flash cijena za ovaj artikal je već istekla u ovoj posjeti.'
    if pid in offered_ids and force_preview:
        offered_ids = [x for x in offered_ids if x != pid]

    base = product.prikazna_cijena
    try:
        base_d = Decimal(str(base))
    except (InvalidOperation, TypeError, ValueError):
        return None, 'Cijena nije dostupna.'
    if base_d <= 0:
        return None, 'Cijena nije dostupna.'
    sale_d = _discounted_price(base_d, dwell_percent)
    if sale_d >= base_d:
        return None, 'Popust ne smanjuje cijenu.'

    flash_seconds = get_dwell_flash_seconds()
    expires = timezone.now().timestamp() + flash_seconds
    deals = _flash_deals(request)
    deals[str(pid)] = {
        'percent': str(dwell_percent),
        'expires_ts': expires,
        'base': str(base_d.quantize(Decimal('0.01'))),
        'sale': str(sale_d),
    }
    _save_flash_deals(request, deals)

    # Staff preview ne troši „jednom po posjeti” slot
    if not force_preview:
        offered_ids.append(pid)
    state['offered_ids'] = offered_ids[:40]
    state['product_id'] = pid
    state['started_ts'] = timezone.now().timestamp()
    _save_dwell_state(request, state)

    return get_active_dwell_flash(request, pid), None


def maybe_create_product_dwell_offer(request):
    """
    Legacy hook iz poll-a: NE pravi popup.
    Flash cijena se aktivira s product page (activate_product_dwell_flash).
    """
    # Zatvori stare dwell popup-e ako ih ima (migracija sa starog ponašanja)
    try:
        session_key = get_cart_session_key(request)
        if session_key:
            LiveVisitorOffer.objects.filter(
                session_key=session_key,
                aktivacioni_kod=AUTO_DWELL_CODE,
                show_popup=True,
            ).update(show_popup=False)
    except Exception:
        pass
    return None


def product_offer_already_accepted(session_key, product_id, *, target_user=None):
    """
    Kupac je već prihvatio popust na ovaj artikal (dodao u korpu).
    Ne dozvoli drugi popust na isti artikal.
    """
    if not product_id:
        return False
    try:
        product_id = int(product_id)
    except (TypeError, ValueError):
        return False
    clauses = Q(product_id=product_id, tip=LiveVisitorOffer.Tip.ARTIKAL, added_to_cart=True)
    identity = Q()
    if session_key:
        identity |= Q(session_key=session_key)
    if target_user is not None and getattr(target_user, 'pk', None):
        identity |= Q(user_id=target_user.pk)
    elif target_user is not None and isinstance(target_user, int):
        identity |= Q(user_id=target_user)
    if not identity:
        return False
    return LiveVisitorOffer.objects.filter(clauses).filter(identity).exists()


def send_live_visitor_offer(
    session_key,
    *,
    product_id=None,
    discount_percent=0,
    free_shipping=False,
    staff_user=None,
    target_user=None,
):
    if not session_key:
        raise ValueError('Sesija posjetioca nije pronađena.')

    percent = _clamp_percent(discount_percent)
    free_shipping = bool(free_shipping)
    product = None
    if product_id:
        product = Product.objects.filter(pk=product_id, aktivan=True).first()
        if not product:
            raise ValueError('Artikal nije pronađen ili nije aktivan.')
        if product_offer_already_accepted(session_key, product.pk, target_user=target_user):
            raise ValueError(
                'Kupac je već prihvatio popust na ovaj artikal i dodao ga u korpu. '
                'Ne može se poslati drugi put.'
            )

    if product:
        tip = LiveVisitorOffer.Tip.ARTIKAL
        code = ''
    elif percent > 0 or free_shipping:
        tip = LiveVisitorOffer.Tip.NARUDZBA
        code = _generate_activation_code()
    else:
        raise ValueError('Unesite popust %, besplatnu dostavu ili odaberite artikal.')

    defaults = {
        'tip': tip,
        'product': product,
        'discount_percent': percent,
        'besplatna_dostava': free_shipping,
        'aktivacioni_kod': code,
        'kod_aktiviran': False,
        'show_popup': True,
        'added_to_cart': False,
        'poslao': staff_user if isinstance(staff_user, User) else None,
    }
    return _upsert_live_visitor_offer(session_key, defaults, target_user=target_user)


def send_live_visitor_registration_invite(
    session_key,
    *,
    staff_user=None,
    target_user=None,
    auto=False,
    discount_percent=None,
    free_shipping=None,
):
    """
    Pošalji gostu popup poziv na registraciju.
    default: % popust (iz SiteSettings za auto) ili besplatna dostava (staff legacy).
    """
    if not session_key:
        raise ValueError('Sesija posjetioca nije pronađena.')
    if target_user and getattr(target_user, 'is_authenticated', False):
        raise ValueError('Kupac je već registrovan.')

    if discount_percent is None and free_shipping is None:
        if auto:
            _, pct, _ = _welcome_reg_settings()
            discount_percent = pct
            free_shipping = pct <= 0
        else:
            discount_percent = Decimal('0')
            free_shipping = True

    percent = _clamp_percent(discount_percent or 0)
    free_shipping = bool(free_shipping) if free_shipping is not None else (percent <= 0)

    defaults = {
        'tip': LiveVisitorOffer.Tip.REGISTRACIJA,
        'product': None,
        'discount_percent': percent,
        'besplatna_dostava': free_shipping and percent <= 0,
        'aktivacioni_kod': AUTO_REG_CODE if auto else '',
        'kod_aktiviran': False,
        'show_popup': True,
        'added_to_cart': False,
        'poslao': staff_user if isinstance(staff_user, User) else None,
    }
    # Ako ima % — to je nagrada (ne free shipping osim eksplicitno)
    if percent > 0:
        defaults['besplatna_dostava'] = bool(free_shipping) if free_shipping else False
    return _upsert_live_visitor_offer(session_key, defaults, target_user=None)


def _blocked_path(request):
    path = getattr(request, 'path', '') or ''
    return path.startswith('/nalog/') or path.startswith('/admin')


def _seconds_on_site(request):
    """Sekunde od first_seen LiveVisitor-a (ili 0)."""
    session_key = get_cart_session_key(request)
    if not session_key:
        return 0
    from .models import LiveVisitor

    visitor = (
        LiveVisitor.objects.filter(session_key=session_key)
        .only('first_seen')
        .first()
    )
    if not visitor or not visitor.first_seen:
        return 0
    return max(0, (timezone.now() - visitor.first_seen).total_seconds())


def _client_welcome_elapsed(request):
    """Sekunde od učitavanja stranice (šalje live-offer-poll.js)."""
    if not request:
        return None
    raw = None
    try:
        raw = request.GET.get('welcome_elapsed')
    except Exception:
        raw = None
    if raw is None or raw == '':
        return None
    try:
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        return None


def maybe_auto_welcome_registration(request):
    """
    Gost → automatski popup registracija + % na prvu narudžbu.
    Uključuje se u SiteSettings → „Registracija + popust”.
    Kašnjenje (npr. 4 s) broji se od učitavanja stranice (client welcome_elapsed),
    ne odmah na prvi request.
    """
    aktivan, percent, delay = _welcome_reg_settings()
    if not aktivan:
        return None
    if not request or _blocked_path(request):
        return None
    user = getattr(request, 'user', None)
    if user and getattr(user, 'is_authenticated', False):
        return None
    if request.session.get(SESSION_WELCOME_REG_KEY):
        return None

    session_key = get_cart_session_key(request)
    if not session_key:
        return None

    # Već ima (ili je imao) reg poziv u ovoj sesiji
    existing = (
        LiveVisitorOffer.objects.filter(
            session_key=session_key,
            tip=LiveVisitorOffer.Tip.REGISTRACIJA,
        )
        .order_by('-azurirano')
        .first()
    )
    if existing:
        request.session[SESSION_WELCOME_REG_KEY] = '1'
        request.session.modified = True
        return existing if existing.show_popup and not existing.kod_aktiviran else None

    # Client šalje welcome_elapsed (sekunde od page load) — to je izvor istine za delay
    client_elapsed = _client_welcome_elapsed(request)
    now_ts = timezone.now().timestamp()
    clock = request.session.get(SESSION_WELCOME_REG_CLOCK)
    try:
        clock = float(clock) if clock is not None else None
    except (TypeError, ValueError):
        clock = None
    if clock is None:
        request.session[SESSION_WELCOME_REG_CLOCK] = now_ts
        request.session.modified = True
        clock = now_ts

    if delay <= 0:
        elapsed = 0.0
    elif client_elapsed is not None:
        # Poll s JS-a: tačno N sekundi nakon učitavanja stranice
        elapsed = client_elapsed
    else:
        # SSR / bez welcome_elapsed — nikad ne kreiraj odmah kad delay > 0
        return None

    if elapsed + 0.15 < float(delay):
        return None

    offer = send_live_visitor_registration_invite(
        session_key,
        auto=True,
        discount_percent=percent,
        free_shipping=percent <= 0,
    )
    request.session[SESSION_WELCOME_REG_KEY] = '1'
    request.session.modified = True
    return offer


def set_session_free_shipping(request, active=True):
    if active:
        request.session[SESSION_FREE_SHIPPING_KEY] = '1'
    else:
        request.session.pop(SESSION_FREE_SHIPPING_KEY, None)
    request.session.modified = True


def session_has_free_shipping(request):
    return bool(request and request.session.get(SESSION_FREE_SHIPPING_KEY))


def user_has_first_order_free_shipping(user):
    """Registrovan kupac s prihvaćenom besplatnom dostavom, još bez narudžbe."""
    if not user or not getattr(user, 'is_authenticated', False):
        return False
    if Order.objects.filter(korisnik=user).exists():
        return False
    return LiveVisitorOffer.objects.filter(
        user=user,
        besplatna_dostava=True,
        kod_aktiviran=True,
    ).exists()


def has_free_shipping_reward(request, user=None):
    user = user if user is not None else getattr(request, 'user', None)
    if session_has_free_shipping(request):
        if not user or not getattr(user, 'is_authenticated', False):
            return True
        # Registrovan: samo dok nema nijednu narudžbu
        return not Order.objects.filter(korisnik=user).exists()
    return user_has_first_order_free_shipping(user)


def clear_free_shipping_reward(request, user=None):
    if request is not None:
        set_session_free_shipping(request, False)
    user = user if user is not None else (getattr(request, 'user', None) if request else None)
    if user and getattr(user, 'is_authenticated', False):
        # Ostavi kod_aktiviran=True (iskorišteno), ali nakon narudžbe
        # user_has_first_order_free_shipping više ne prolazi jer postoji Order.
        pass


def mark_registration_invite_pending(request, offer):
    """Zapamti u sesiji da je posjetilac dobio poziv na registraciju."""
    if not offer or offer.tip != LiveVisitorOffer.Tip.REGISTRACIJA:
        return
    pct = offer.discount_percent or Decimal('0')
    if pct > 0:
        request.session[SESSION_REG_INVITE_KEY] = f'percent:{pct}'
    else:
        request.session[SESSION_REG_INVITE_KEY] = 'free_shipping'
    request.session.modified = True


def claim_registration_invite_reward(request, user):
    """
    Nakon registracije: % kupon ili besplatna dostava na prvu narudžbu.
    """
    if not user or not user.pk:
        return None

    session_key = get_cart_session_key(request)
    pending = request.session.get(SESSION_REG_INVITE_KEY)

    offer = None
    if session_key:
        offer = (
            LiveVisitorOffer.objects
            .filter(
                session_key=session_key,
                tip=LiveVisitorOffer.Tip.REGISTRACIJA,
                kod_aktiviran=False,
            )
            .order_by('-azurirano')
            .first()
        )

    if not offer and not pending:
        return None

    # Već iskoristio ranije (ima narudžbu) — ne daj ponovo
    if Order.objects.filter(korisnik=user).exists():
        if offer:
            offer.user = user
            offer.kod_aktiviran = True
            offer.show_popup = False
            offer.save(update_fields=[
                'user', 'kod_aktiviran', 'show_popup', 'azurirano',
            ])
        request.session.pop(SESSION_REG_INVITE_KEY, None)
        return None

    percent = Decimal('0')
    free_ship = True
    if offer:
        percent = _clamp_percent(offer.discount_percent or 0)
        free_ship = bool(offer.besplatna_dostava) and percent <= 0
        offer.user = user
        offer.kod_aktiviran = True
        offer.show_popup = False
        offer.save(update_fields=[
            'user', 'kod_aktiviran', 'show_popup', 'azurirano',
        ])
    else:
        # Pending iz sesije
        if isinstance(pending, str) and pending.startswith('percent:'):
            try:
                percent = _clamp_percent(pending.split(':', 1)[1])
            except Exception:
                percent = Decimal('10')
            free_ship = False
        sk = session_key or f'reg-user-{user.pk}'
        LiveVisitorOffer.objects.create(
            session_key=sk,
            user=user,
            tip=LiveVisitorOffer.Tip.REGISTRACIJA,
            discount_percent=percent,
            besplatna_dostava=free_ship,
            kod_aktiviran=True,
            show_popup=False,
            added_to_cart=False,
        )

    request.session.pop(SESSION_REG_INVITE_KEY, None)
    request.session.modified = True

    if percent > 0:
        # Kreiraj jednokratni kupon za prvu narudžbu
        import secrets
        code = f'REG{secrets.token_hex(3).upper()[:6]}'
        while Coupon.objects.filter(kod=code).exists():
            code = f'REG{secrets.token_hex(3).upper()[:6]}'
        Coupon.objects.create(
            kod=code,
            naziv=REGISTRATION_COUPON_NAME,
            postotak=percent,
            vlasnik=user,
            aktivan=True,
            automatski=True,
        )
        return {'percent': str(percent), 'type': 'registration', 'coupon': code}

    set_session_free_shipping(request, True)
    return {'free_shipping': True, 'type': 'registration'}


def registration_reward_coupon_code(user):
    """Legacy: stari registracijski % kupon (ako još postoji aktivan)."""
    coupon = get_active_registration_reward_coupon(user)
    return coupon.kod if coupon else ''


def get_active_registration_reward_coupon(user):
    if not user or not getattr(user, 'is_authenticated', False):
        return None
    return (
        Coupon.objects
        .filter(
            vlasnik=user,
            naziv=REGISTRATION_COUPON_NAME,
            aktivan=True,
        )
        .order_by('-kreiran')
        .first()
    )


def consume_registration_reward(user):
    """Nakon narudžbe — stari % kupon više ne vrijedi."""
    if not user or not getattr(user, 'is_authenticated', False):
        return
    Coupon.objects.filter(
        vlasnik=user,
        naziv=REGISTRATION_COUPON_NAME,
        aktivan=True,
    ).update(aktivan=False)


def get_active_live_visitor_offer(request):
    lookup = _offer_lookup_q(request)
    if not lookup:
        return None
    # Registrovanim korisnicima ne prikazuj poziv na registraciju
    user = getattr(request, 'user', None)
    if user and user.is_authenticated:
        LiveVisitorOffer.objects.filter(
            lookup,
            tip=LiveVisitorOffer.Tip.REGISTRACIJA,
            show_popup=True,
        ).update(show_popup=False)
    from .browse_interest_offer import AUTO_BROWSE_CODE, AI_PRODAJA_CODE

    return (
        LiveVisitorOffer.objects.filter(
            lookup,
            show_popup=True,
        )
        .filter(_active_offer_filter())
        .exclude(aktivacioni_kod=AUTO_BROWSE_CODE)
        .exclude(aktivacioni_kod__startswith=f'{AUTO_BROWSE_CODE}-')
        .exclude(aktivacioni_kod=AI_PRODAJA_CODE)
        .exclude(aktivacioni_kod__startswith=f'{AI_PRODAJA_CODE}-')
        # Dwell više nije popup — samo flash cijena na product page
        .exclude(aktivacioni_kod=AUTO_DWELL_CODE)
        .exclude(aktivacioni_kod__startswith=f'{AUTO_DWELL_CODE}-')
        .select_related('product')
        .order_by('-azurirano')
        .first()
    )


def _offer_timer_seconds(offer):
    expires_at = offer.azurirano + timedelta(minutes=OFFER_TIMER_MINUTES)
    remaining = int((expires_at - timezone.now()).total_seconds())
    return max(0, remaining)


def _percent_display(discount):
    if not discount or discount <= 0:
        return None
    return int(discount) if discount == int(discount) else float(discount)


def _build_order_offer_payload(offer):
    discount = offer.discount_percent or Decimal('0')
    pct_display = _percent_display(discount)
    free_shipping = bool(getattr(offer, 'besplatna_dostava', False))
    if not free_shipping and (not pct_display or not offer.aktivacioni_kod):
        return None
    if free_shipping and not pct_display:
        return {
            'offer_type': 'free_shipping',
            'offer_id': offer.pk,
            'offer_version': int(offer.azurirano.timestamp()),
            'free_shipping': True,
            'title': 'Besplatna dostava na prvu kupovinu',
            'message': (
                'Prihvatite ponudu i na prvu narudžbu dostava vam je besplatna. '
                'Vrijedi samo jednom — za prvu kupovinu.'
            ),
            'activation_code': offer.aktivacioni_kod or '',
            'timer_seconds': _offer_timer_seconds(offer),
            'timer_minutes': OFFER_TIMER_MINUTES,
            'activate_url': '/ponuda/aktiviraj/',
            'dismiss_url': '/ponuda/zatvori/',
            'cta_label': 'Prihvati besplatnu dostavu',
        }
    return {
        'offer_type': 'order',
        'offer_id': offer.pk,
        'offer_version': int(offer.azurirano.timestamp()),
        'discount_percent': pct_display,
        'free_shipping': free_shipping,
        'activation_code': offer.aktivacioni_kod,
        'timer_seconds': _offer_timer_seconds(offer),
        'timer_minutes': OFFER_TIMER_MINUTES,
        'activate_url': '/ponuda/aktiviraj/',
        'dismiss_url': '/ponuda/zatvori/',
    }


def _build_product_offer_payload(offer):
    product = offer.product
    if not product:
        return None
    discount = offer.discount_percent or Decimal('0')
    free_shipping = bool(getattr(offer, 'besplatna_dostava', False))
    in_stock_variations = list(
        product.varijacije.filter(na_stanju=True).order_by('redoslijed', 'id'),
    )

    if not in_stock_variations and not product.na_stanju:
        return None

    variations = []
    for variation in in_stock_variations:
        base_price = variation.prikazna_cijena
        final_price = _discounted_price(base_price, discount)
        variations.append({
            'id': variation.pk,
            'naziv': variation.naziv,
            'base_price': str(base_price),
            'final_price': str(final_price),
            'has_discount': discount > 0 and final_price < base_price,
        })

    if variations:
        display_base = variations[0]['base_price']
        display_final = variations[0]['final_price']
        has_discount = variations[0]['has_discount']
    else:
        display_base = str(product.prikazna_cijena)
        display_final = str(_discounted_price(product.prikazna_cijena, discount))
        has_discount = discount > 0 and Decimal(display_final) < Decimal(display_base)

    return {
        'offer_type': 'product',
        'offer_id': offer.pk,
        'offer_version': int(offer.azurirano.timestamp()),
        'product_id': product.pk,
        'product_name': product.naziv,
        'product_url': product.get_absolute_url(),
        'image_url': product.prikazna_slika.url if product.prikazna_slika else '',
        'discount_percent': _percent_display(discount),
        'has_discount': has_discount,
        'free_shipping': free_shipping,
        'has_variations': bool(variations),
        'variations': variations,
        'display_base_price': display_base,
        'display_final_price': display_final,
        'timer_seconds': _offer_timer_seconds(offer),
        'timer_minutes': OFFER_TIMER_MINUTES,
        'add_url': '/ponuda/dodaj/',
        'dismiss_url': '/ponuda/zatvori/',
    }


def _build_registration_offer_payload(offer):
    pct = offer.discount_percent or Decimal('0')
    pct_display = _percent_display(pct)
    free_ship = bool(getattr(offer, 'besplatna_dostava', False)) and (not pct or pct <= 0)

    if pct_display:
        return {
            'offer_type': 'registration',
            'offer_id': offer.pk,
            'offer_version': int(offer.azurirano.timestamp()),
            'discount_percent': pct_display,
            'free_shipping': False,
            'title': f'Registrujte se i uzmite {pct_display}% popusta',
            'message': (
                f'Kreirajte nalog i ostvarite {pct_display}% popusta na prvu narudžbu. '
                f'Kupon se automatski primjenjuje u korpi — jednokratno.'
            ),
            'benefits': [
                f'{pct_display}% popusta na prvu narudžbu',
                'Automatski se primjenjuje u korpi',
                'Vrijedi samo jednom — nakon porudžbe prestaje',
            ],
            'cta_label': f'Registruj se i uzmi {pct_display}%',
            'register_url': '/registracija/',
            'timer_seconds': _offer_timer_seconds(offer),
            'timer_minutes': OFFER_TIMER_MINUTES,
            'dismiss_url': '/ponuda/zatvori/',
        }

    return {
        'offer_type': 'registration',
        'offer_id': offer.pk,
        'offer_version': int(offer.azurirano.timestamp()),
        'discount_percent': None,
        'free_shipping': free_ship or True,
        'title': 'Registrujte se i ostvarite besplatnu dostavu',
        'message': (
            'Nakon registracije na prvu narudžbu imate besplatnu dostavu. '
            'Pogodnost vrijedi samo jednom — za prvu kupovinu.'
        ),
        'benefits': [
            'Besplatna dostava na prvu narudžbu',
            'Automatski se primjenjuje u korpi',
            'Vrijedi samo jednom — nakon porudžbe prestaje',
        ],
        'cta_label': 'Registruj se i uzmi besplatnu dostavu',
        'register_url': '/registracija/',
        'timer_seconds': _offer_timer_seconds(offer),
        'timer_minutes': OFFER_TIMER_MINUTES,
        'dismiss_url': '/ponuda/zatvori/',
    }


def _build_offer_payload(offer):
    if offer.tip == LiveVisitorOffer.Tip.REGISTRACIJA:
        return _build_registration_offer_payload(offer)
    if offer.tip == LiveVisitorOffer.Tip.NARUDZBA:
        return _build_order_offer_payload(offer)
    return _build_product_offer_payload(offer)


def build_live_visitor_offer_context(request):
    try:
        maybe_auto_welcome_registration(request)
    except Exception:
        pass
    offer = get_active_live_visitor_offer(request)
    if not offer:
        return None
    payload = _build_offer_payload(offer)
    if not payload:
        return None
    mark_registration_invite_pending(request, offer)
    payload['offer'] = offer
    if offer.product_id:
        payload['product'] = offer.product
    return payload


def poll_live_visitor_offer(request):
    # Welcome: gostu automatski reg + popust (nakon delay-a)
    try:
        maybe_auto_welcome_registration(request)
    except Exception:
        pass

    # 1 min na stranici artikla → 10% na taj artikal (ako je uključeno)
    try:
        maybe_create_product_dwell_offer(request)
    except Exception:
        pass

    # 2 min na sajtu → 10% na gledane artikle (kreiraj čak i ako ima reg invite)
    try:
        from .browse_interest_offer import maybe_create_browse_interest_offer

        maybe_create_browse_interest_offer(request)
    except Exception:
        pass

    offer = get_active_live_visitor_offer(request)
    if offer:
        # Auto dwell ima prioritet samo dok je aktivan; browse ide preko session payloada
        mark_registration_invite_pending(request, offer)
        # Ako je samo reg invite — i dalje pokušaj browse payload ispod? Ne: reg prvo.
        return _build_offer_payload(offer)

    # Personalizovana ponuda nakon 2 min (prema gledanju)
    try:
        from .browse_interest_offer import poll_browse_interest_offer

        return poll_browse_interest_offer(request)
    except Exception:
        return None


def activate_live_visitor_offer_code(request, cart):
    lookup = _offer_lookup_q(request)
    if not lookup:
        return False, 'Ponuda više nije dostupna.'

    offer = LiveVisitorOffer.objects.filter(
        lookup,
        tip=LiveVisitorOffer.Tip.NARUDZBA,
        show_popup=True,
        kod_aktiviran=False,
    ).order_by('-azurirano').first()
    if not offer:
        return False, 'Ponuda više nije dostupna.'

    percent = offer.discount_percent or Decimal('0')
    free_shipping = bool(getattr(offer, 'besplatna_dostava', False))
    if percent <= 0 and not free_shipping:
        return False, 'Ponuda nema popusta ni besplatne dostave.'

    messages = []
    if percent > 0:
        cart.set_recovery_discount(percent)
        pct = _percent_display(percent)
        messages.append(f'{pct}% popusta na cijelu narudžbu')

    assigned_user = False
    if free_shipping:
        set_session_free_shipping(request, True)
        user = getattr(request, 'user', None)
        if user and user.is_authenticated:
            offer.user = user
            assigned_user = True
        messages.append('besplatna dostava na prvu kupovinu')

    offer.kod_aktiviran = True
    offer.show_popup = False
    update_fields = ['kod_aktiviran', 'show_popup', 'azurirano']
    if assigned_user:
        update_fields.append('user')
    offer.save(update_fields=update_fields)

    try:
        from .staff_alerts import notify_offer_accepted
        from .models import LiveVisitor
        from .cart_tracking import get_cart_session_key

        sk = get_cart_session_key(request) or (offer.session_key or '')
        lv = LiveVisitor.objects.filter(session_key=sk).only(
            'ime', 'email', 'grad',
        ).first() if sk else None
        notify_offer_accepted(
            ime=(lv.ime if lv else '') or '',
            email=(lv.email if lv else '') or '',
            grad=(lv.grad if lv else '') or '',
            session_key=sk,
            product_name='cijelu narudžbu' if percent > 0 else 'besplatnu dostavu',
            discount_percent=percent if percent > 0 else None,
            source='ponudu na narudžbu',
        )
    except Exception:
        pass

    if free_shipping and percent > 0:
        msg = (
            f'Šta god da poručite, {messages[0]} — i {messages[1]}. '
            f'Besplatna dostava vrijedi samo za prvu narudžbu.'
        )
    elif free_shipping:
        msg = (
            'Besplatna dostava je aktivirana na prvu kupovinu. '
            'Vrijedi samo jednom — za prvu narudžbu.'
        )
    else:
        pct = _percent_display(percent)
        msg = f'Šta god da poručite, {pct}% vam je sniženo na cijelu narudžbu.'

    return True, {
        'percent': _percent_display(percent) if percent > 0 else None,
        'free_shipping': free_shipping,
        'message': msg,
    }


def apply_live_visitor_offer(request, cart):
    lookup = _offer_lookup_q(request)
    if not lookup:
        return False, 'Ponuda više nije dostupna.'

    offer = LiveVisitorOffer.objects.filter(
        lookup,
        tip=LiveVisitorOffer.Tip.ARTIKAL,
        show_popup=True,
        added_to_cart=False,
    ).select_related('product').order_by('-azurirano').first()
    if not offer or not offer.product:
        return False, 'Ponuda više nije dostupna.'

    product = offer.product
    if not product.aktivan:
        return False, 'Artikal više nije dostupan.'

    in_stock_variations = product.varijacije.filter(na_stanju=True)
    variation = None
    var_id = (request.POST.get('variation_id') or '').strip()
    if var_id:
        try:
            variation = in_stock_variations.get(pk=int(var_id))
        except (ProductVariation.DoesNotExist, ValueError):
            return False, 'Izaberite ispravnu varijaciju.'
    elif in_stock_variations.exists():
        return False, 'Izaberite varijaciju.'

    if variation and not variation.na_stanju:
        return False, 'Varijacija nije na stanju.'
    if not variation and not product.na_stanju:
        return False, 'Artikal nije na stanju.'

    discount = offer.discount_percent or Decimal('0')
    free_shipping = bool(getattr(offer, 'besplatna_dostava', False))
    base_price = variation.prikazna_cijena if variation else product.prikazna_cijena
    if discount > 0:
        final_price = _discounted_price(base_price, discount)
        cart.add(
            product,
            variation=variation,
            quantity=1,
            custom_price=final_price,
            promo_bazna=base_price,
            discount_source=f'Uživo ponuda / staff (−{discount}%)',
            discount_percent=discount,
        )
    else:
        cart.add(product, variation=variation, quantity=1)

    assigned_user = False
    if free_shipping:
        set_session_free_shipping(request, True)
        user = getattr(request, 'user', None)
        if user and user.is_authenticated:
            offer.user = user
            assigned_user = True

    offer.added_to_cart = True
    if free_shipping:
        offer.kod_aktiviran = True
    offer.show_popup = False
    update_fields = ['added_to_cart', 'show_popup', 'azurirano']
    if free_shipping:
        update_fields.append('kod_aktiviran')
    if assigned_user:
        update_fields.append('user')
    offer.save(update_fields=update_fields)

    # Staff toast — AI prodaja / bilo koja prihvaćena ponuda
    try:
        from .staff_alerts import notify_offer_accepted
        from .models import LiveVisitor
        from .cart_tracking import get_cart_session_key

        sk = get_cart_session_key(request) or (offer.session_key or '')
        lv = LiveVisitor.objects.filter(session_key=sk).only(
            'ime', 'email', 'grad',
        ).first() if sk else None
        code = (offer.aktivacioni_kod or '').strip()
        source = 'AI prodaja' if code == 'AI-PRODAJA' or code.startswith('AI-PRODAJA') else 'ponudu'
        notify_offer_accepted(
            ime=(lv.ime if lv else '') or '',
            email=(lv.email if lv else '') or (getattr(request.user, 'email', '') if getattr(request, 'user', None) else ''),
            grad=(lv.grad if lv else '') or '',
            session_key=sk,
            product_name=product.naziv or '',
            discount_percent=discount,
            source=source,
        )
    except Exception:
        pass

    label = f'{product.naziv}' + (f' — {variation.naziv}' if variation else '')
    parts = []
    if discount > 0:
        pct = _percent_display(discount)
        parts.append(f'popustom od {pct}%')
    if free_shipping:
        parts.append('besplatnom dostavom na prvu kupovinu')
    if parts:
        return True, f'"{label}" je dodato u korpu s {" i ".join(parts)}.'
    return True, f'"{label}" je dodato u korpu.'


def dismiss_live_visitor_offer(request):
    lookup = _offer_lookup_q(request)
    if not lookup:
        return
    # Zatvori samo jednu (trenutnu) — ostale artikal-ponude ostaju u redu
    offer_id = None
    try:
        offer_id = int(request.POST.get('offer_id') or request.GET.get('offer_id') or 0)
    except (TypeError, ValueError):
        offer_id = None
    if offer_id:
        LiveVisitorOffer.objects.filter(lookup, pk=offer_id, show_popup=True).update(
            show_popup=False,
        )
        return
    offer = (
        LiveVisitorOffer.objects.filter(lookup, show_popup=True)
        .filter(_active_offer_filter())
        .order_by('-azurirano')
        .first()
    )
    if offer:
        offer.show_popup = False
        offer.save(update_fields=['show_popup', 'azurirano'])
