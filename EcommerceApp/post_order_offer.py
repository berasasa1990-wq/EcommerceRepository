"""Session-scoped offers for a just completed webshop order."""
import json
from types import SimpleNamespace
from decimal import Decimal, ROUND_HALF_UP

from django.db import transaction
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from .models import Akcija, AkcijaFlashLine, CardPayment, Order, OrderItem, Product

SESSION_KEY = 'post_order_offers'
DURATION = 15 * 60


def cash_on_delivery(order):
    return (order.izvor == Order.Izvor.WEBSHOP and not order.placeno_karticom()
            and not CardPayment.objects.filter(order=order).exists()
            and order.packing_placanje_label() == 'GOTOVINSKI')


def grant_offer(request, order):
    if not order or not cash_on_delivery(order):
        return
    entries = request.session.get(SESSION_KEY, {})
    key = str(order.pk)
    if key in entries:
        return
    offer = Akcija.objects.filter(tip=Akcija.Tip.POSLIJE_NARUDZBE, aktivan=True).order_by('redoslijed', '-pk').first()
    if not offer:
        return
    # Retain only unexpired grants. Reloading the confirmation never resets time.
    now = timezone.now().timestamp()
    entries = {k: v for k, v in entries.items() if v.get('expires', 0) > now}
    entries[key] = {'offer_id': offer.pk, 'expires': now + DURATION}
    request.session[SESSION_KEY] = entries


def order_can_change(order):
    return (cash_on_delivery(order) and order.status in (Order.Status.NOVA, Order.Status.REZERVACIJA, Order.Status.POTVRDJENA)
            and not order.zapakovana and not order.stanje_skinuto
            and order.lager_status not in (Order.LagerStatus.VALIDIRANO, Order.LagerStatus.OTKAZANO))


def marker(line):
    return f'Poslije narudžbe — ponuda {line.akcija_id}, stavka {line.pk}'


def prices(product, line, variation=None):
    regular = Decimal(str((variation or product).bazna_cijena))
    percent = Decimal(str(line.effective_discount_percent() or 0))
    if not percent.is_finite() or not 0 <= percent <= 100 or regular < 0:
        raise ValueError('Popust nije ispravno podešen.')
    price = (regular * (1 - percent / 100)).quantize(Decimal('.01'), rounding=ROUND_HALF_UP)
    return regular, percent, price


def offer_context(request, order):
    grant = request.session.get(SESSION_KEY, {}).get(str(order.pk), {})
    if grant.get('expires', 0) <= timezone.now().timestamp() or not order_can_change(order):
        return None
    offer = Akcija.objects.filter(pk=grant.get('offer_id'), tip=Akcija.Tip.POSLIJE_NARUDZBE, aktivan=True).first()
    if not offer:
        return None
    rows = []
    for line in offer.flash_lines.select_related('product').prefetch_related('product__varijacije').all()[:4]:
        product = line.product
        if not product.aktivan or not product.na_stanju:
            continue
        variations = list(product.varijacije.all())
        available = [v for v in variations if v.na_stanju]
        if variations and not available:
            continue
        try:
            regular, percent, price = prices(product, line, available[0] if available else None)
        except ValueError:
            continue
        choices = []
        for variation in available:
            vr, vp, value = prices(product, line, variation)
            choices.append({'id': variation.pk, 'name': variation.naziv, 'regular': str(vr), 'price': str(value)})
        rows.append({'line': line, 'product': product, 'regular': regular, 'percent': percent, 'price': price,
                     'variations': choices,
                     'added': order.stavke.filter(popust_opis=marker(line)).exists()})
    return {'rows': rows, 'expires_ms': int(grant['expires'] * 1000)} if rows else None


@never_cache
@require_POST
def add_product(request, broj):
    if request.POST.get('confirmed') != '1':
        return JsonResponse({'error': 'Potvrdite dodavanje u istu narudžbu.'}, status=400)
    try:
        items = json.loads(request.POST['items']) if 'items' in request.POST else [request.POST]
    except (ValueError, TypeError):
        return JsonResponse({'error': 'Odabir artikala nije ispravan.'}, status=400)
    if not isinstance(items, list) or not 1 <= len(items) <= 4 or any(not isinstance(item, dict) for item in items):
        return JsonResponse({'error': 'Odaberite od 1 do 4 artikla.'}, status=400)
    # Confirm the whole selection together: a failed reservation adds nothing.
    with transaction.atomic():
        for item in items:
            data = {key: str(item.get(key, '')) for key in ('line_id', 'variation_id')}
            response = _add_confirmed_product(SimpleNamespace(session=request.session, POST=data), broj)
            if response.status_code != 200:
                transaction.set_rollback(True)
                return response
    return response


def _add_confirmed_product(request, broj):
    from .magacin import MagacinError, ensure_web_product_stock, reserve_for_order
    grant_entries = request.session.get(SESSION_KEY, {})
    # A public order number alone never authorizes modifying an order.
    with transaction.atomic():
        order = Order.objects.select_for_update().filter(broj=broj).first()
        grant = grant_entries.get(str(order.pk), {}) if order else {}
        if not order or grant.get('expires', 0) <= timezone.now().timestamp():
            return JsonResponse({'error': 'Ponuda je istekla ili nije dostupna u ovoj sesiji.'}, status=403)
        if not order_can_change(order):
            return JsonResponse({'error': 'Narudžba je već obrađena.'}, status=409)
        line = AkcijaFlashLine.objects.select_related('akcija').filter(
            pk=request.POST.get('line_id') if request.POST.get('line_id', '').isdigit() else None,
            akcija_id=grant.get('offer_id'), akcija__aktivan=True,
            akcija__tip=Akcija.Tip.POSLIJE_NARUDZBE).first()
        if not line:
            return JsonResponse({'error': 'Artikal nije dio ove ponude.'}, status=400)
        if order.stavke.filter(popust_opis=marker(line)).exists():
            return JsonResponse({'added': True, 'total': str(order.ukupno)})
        product = Product.objects.select_for_update().get(pk=line.product_id)
        if not product.aktivan or not product.na_stanju:
            return JsonResponse({'error': 'Artikal više nije na stanju.'}, status=409)
        variation = None
        if product.varijacije.exists():
            variation_id = request.POST.get('variation_id', '')
            variation = product.varijacije.filter(pk=variation_id if variation_id.isdigit() else None, na_stanju=True).first()
            if not variation:
                return JsonResponse({'error': 'Odaberite dostupnu varijaciju.'}, status=400)
        try:
            regular, percent, price = prices(product, line, variation)
        except ValueError as exc:
            return JsonResponse({'error': str(exc)}, status=400)
        product = ensure_web_product_stock(product)
        item = OrderItem.objects.create(narudzba=order, artikal=product, varijacija=variation,
            naziv=product.naziv, product_naziv=product.naziv, varijacija_naziv=variation.naziv if variation else '',
            sifra=(variation.sifra if variation else product.sifra) or '', cijena=price, bazna_cijena=regular,
            popust_opis=marker(line), popust_postotak=percent, popust_iznos=regular-price, kolicina=1)
        try:
            missing = reserve_for_order(order, product, 1, variation=variation, set_item=item,
                                        napomena=f'Poslije narudžbe #{order.broj}')
        except MagacinError as exc:
            transaction.set_rollback(True)
            return JsonResponse({'error': str(exc)}, status=409)
        if missing:
            transaction.set_rollback(True)
            return JsonResponse({'error': 'Artikal više nije dostupan na lageru.'}, status=409)
        order.medjuzbir += price
        order.ukupno += price
        details = list(order.popust_detalji or [])
        details.append({'opis': f'Poslije narudžbe: {product.naziv} −{percent}%', 'iznos': str(regular-price)})
        order.popust_detalji = details
        order.save(update_fields=['medjuzbir', 'ukupno', 'popust_detalji'])
    return JsonResponse({'added': True, 'total': str(order.ukupno)})
