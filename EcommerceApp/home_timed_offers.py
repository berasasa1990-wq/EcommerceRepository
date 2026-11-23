from django.db import transaction
from django.utils import timezone

from .forms import HomeTimedOfferForm
from .models import Akcija, AkcijaFlashLine


def home_offer_forms(data, section):
    offers = {
        offer.tip: offer for offer in Akcija.objects.filter(tip__in=Akcija.HOME_TIMED_HOURS).prefetch_related('flash_lines')
    }
    result = {}
    for kind in Akcija.HOME_TIMED_HOURS:
        offer = offers.get(kind)
        discounts = {f'popust_{line.product_id}': line.popust_postotak for line in offer.flash_lines.all()} if offer else {}
        initial = {
            'enabled': bool(offer and offer.aktivan),
            'artikli': [line.product_id for line in offer.flash_lines.all()] if offer else [],
            **discounts,
        }
        result[kind] = {
            'kind': kind,
            'title': kind.label,
            'duration': '24 sata' if kind == Akcija.Tip.HOME_DAILY else '7 dana',
            'active_until': offer.zavrsava if offer and offer.flash_still_running() else None,
            'form': HomeTimedOfferForm(data if section == kind else None, initial=initial, auto_id=f'id_{kind}_%s'),
        }
    return result


@transaction.atomic
def save_home_offer(kind, cleaned_data):
    kind = Akcija.Tip(kind)
    offer, _ = Akcija.objects.select_for_update().get_or_create(
        tip=kind, defaults={'naziv': kind.label, 'aktivan': False},
    )
    enabled = cleaned_data['enabled']
    if enabled and not offer.flash_still_running():
        offer.pocetak = timezone.now()
    offer.aktivan = enabled
    offer.trajanje_sati = Akcija.HOME_TIMED_HOURS[kind]
    offer.save(update_fields=['aktivan', 'pocetak', 'trajanje_sati'])
    offer.flash_lines.all().delete()
    AkcijaFlashLine.objects.bulk_create([
        AkcijaFlashLine(
            akcija=offer, product=product, redoslijed=index,
            popust_postotak=cleaned_data[f'popust_{product.pk}'],
        )
        for index, product in enumerate(cleaned_data['artikli'])
    ])


def active_home_offers(products):
    offers = {
        offer.tip: offer for offer in Akcija.objects.filter(tip__in=Akcija.HOME_TIMED_HOURS, aktivan=True)
        .prefetch_related('flash_lines') if offer.zavrsava and offer.flash_still_running()
    }
    if not offers:
        return []
    ids = {line.product_id for offer in offers.values() for line in offer.flash_lines.all()}
    available = {product.pk: product for product in products.filter(pk__in=ids)}
    result = []
    for kind in Akcija.HOME_TIMED_HOURS:
        offer = offers.get(kind)
        if not offer:
            continue
        items = [available[line.product_id] for line in offer.flash_lines.all() if line.product_id in available]
        if items:
            title_first, title_last = kind.label.split(' ', 1)
            result.append({
                'title': kind.label, 'title_first': title_first, 'title_last': title_last,
                'ends_at': offer.zavrsava, 'products': items,
            })
    return result
