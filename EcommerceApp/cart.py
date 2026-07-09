from decimal import ROUND_HALF_UP, Decimal

from .models import Product, ProductVariation
from .upsell import get_deal_info_for_cart_item, get_quantity_deal, calculate_deal_adjusted_total

PDV_STOPA = Decimal('0.17')


def izracunaj_pdv(ukupno_sa_pdvom):
    ukupno_sa_pdvom = Decimal(ukupno_sa_pdvom)
    bez_pdv = (ukupno_sa_pdvom / (Decimal('1') + PDV_STOPA)).quantize(
        Decimal('0.01'), rounding=ROUND_HALF_UP,
    )
    pdv = (ukupno_sa_pdvom - bez_pdv).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    return {
        'bez_pdv': bez_pdv,
        'pdv': pdv,
        'sa_pdvom': ukupno_sa_pdvom.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP),
    }


class Cart:
    SESSION_KEY = 'cart'
    COUPON_KEY = 'applied_coupon'
    COUPON_APPLIED_KEY = 'coupon_applied_by_user'
    COUPON_KEEP_KEY = 'coupon_keep_after_apply'
    RECOVERY_DISCOUNT_KEY = 'cart_recovery_discount_percent'

    def __init__(self, request):
        self.request = request
        cart = request.session.get(self.SESSION_KEY)
        if not isinstance(cart, dict):
            cart = {}
        self.cart = cart

    def save(self):
        self.request.session[self.SESSION_KEY] = self.cart
        self.request.session.modified = True

    def _line_key(self, product_id, variation_id=None):
        return f'{product_id}:{variation_id or 0}'

    def add(self, product, variation=None, quantity=1, custom_price=None, *, promo_bazna=None, gratis_akcija_id=None):
        key = self._line_key(product.pk, variation.pk if variation else None)
        quantity = max(1, int(quantity or 1))
        prikazna = variation.prikazna_cijena if variation else product.prikazna_cijena
        if custom_price is not None:
            price = Decimal(str(custom_price))
            bazna = Decimal(str(promo_bazna)) if promo_bazna is not None else prikazna
            na_akciji = price < bazna
        else:
            price = prikazna
            bazna = variation.bazna_cijena if variation else product.bazna_cijena
            na_akciji = variation.na_akciji if variation else product.na_akciji
        varijacija_naziv = variation.naziv if variation else ''
        naziv = product.naziv
        sifra = variation.sifra if variation and variation.sifra else (product.sifra or '')
        slika = ''
        if variation and variation.slika:
            slika = variation.slika.url
        elif product.prikazna_slika:
            slika = product.prikazna_slika.url

        if key in self.cart:
            self.cart[key]['quantity'] += quantity
            if custom_price is not None:
                self.cart[key]['cijena'] = str(price)
                self.cart[key]['bazna_cijena'] = str(bazna)
                self.cart[key]['na_akciji'] = na_akciji
                self.cart[key]['timer_akcija'] = True
            if gratis_akcija_id is not None:
                self.cart[key]['gratis_akcija_id'] = gratis_akcija_id
                self.cart[key]['gratis_promo'] = True
        else:
            self.cart[key] = {
                'product_id': product.pk,
                'slug': product.slug,
                'variation_id': variation.pk if variation else None,
                'naziv': naziv,
                'product_naziv': product.naziv,
                'varijacija_naziv': varijacija_naziv,
                'sifra': sifra,
                'cijena': str(price),
                'bazna_cijena': str(bazna),
                'na_akciji': na_akciji,
                'slika': slika,
                'quantity': quantity,
                'upsell': bool(custom_price),
                'timer_akcija': bool(custom_price),
            }
            if gratis_akcija_id is not None:
                self.cart[key]['gratis_akcija_id'] = gratis_akcija_id
                self.cart[key]['gratis_promo'] = True
        self.save()
        self._track_line(key)

    def remove(self, key):
        if key in self.cart:
            del self.cart[key]
            self.save()
            self._untrack_line(key)

    def set_quantity(self, key, quantity):
        if key not in self.cart:
            return
        if quantity <= 0:
            self.remove(key)
        else:
            self.cart[key]['quantity'] = quantity
            self.save()
            self._track_line(key)

    def clear(self):
        self._untrack_all()
        self.cart = {}
        self.clear_coupon()
        self.clear_recovery_discount()
        self.save()

    def get_recovery_discount_percent(self):
        raw = self.request.session.get(self.RECOVERY_DISCOUNT_KEY)
        if raw in (None, ''):
            return Decimal('0')
        try:
            percent = Decimal(str(raw))
        except Exception:
            return Decimal('0')
        if percent <= 0:
            return Decimal('0')
        return min(percent, Decimal('50'))

    def set_recovery_discount(self, percent):
        percent = Decimal(str(percent or 0))
        if percent <= 0:
            self.clear_recovery_discount()
            return
        self.request.session[self.RECOVERY_DISCOUNT_KEY] = str(
            min(percent, Decimal('50')).quantize(Decimal('0.01')),
        )
        self.request.session.modified = True

    def clear_recovery_discount(self):
        if self.RECOVERY_DISCOUNT_KEY in self.request.session:
            del self.request.session[self.RECOVERY_DISCOUNT_KEY]
            self.request.session.modified = True

    def _track_line(self, key):
        from .cart_tracking import track_cart_line_added_or_updated
        track_cart_line_added_or_updated(self.request, key, self.cart.get(key))

    def _untrack_line(self, key):
        from .cart_tracking import track_cart_line_removed
        track_cart_line_removed(self.request, key)

    def _untrack_all(self):
        from .cart_tracking import track_cart_cleared
        track_cart_cleared(self.request)

    def get_coupon_code(self):
        if not self.is_coupon_applied():
            return ''
        return self.request.session.get(self.COUPON_KEY, '')

    def is_coupon_applied(self):
        return bool(self.request.session.get(self.COUPON_APPLIED_KEY))

    def set_coupon_code(self, code):
        self.request.session[self.COUPON_KEY] = code
        self.request.session[self.COUPON_APPLIED_KEY] = True
        self.request.session.modified = True

    def clear_coupon(self):
        for key in (self.COUPON_KEY, self.COUPON_APPLIED_KEY, self.COUPON_KEEP_KEY):
            if key in self.request.session:
                del self.request.session[key]
        self.request.session.modified = True

    def mark_coupon_keep_after_apply(self):
        self.request.session[self.COUPON_KEEP_KEY] = True
        self.request.session.modified = True

    def should_keep_coupon_on_cart_view(self):
        return bool(self.request.session.pop(self.COUPON_KEEP_KEY, False))

    def __iter__(self):
        from .korpa_nudjenje import build_korpa_nudjenje_map

        # Compute base total once (using raw prices from cart) for threshold-based discounts
        base_total = sum(
            Decimal(it['cijena']) * it['quantity']
            for it in self.cart.values()
        )
        korpa_nudjenje_map = build_korpa_nudjenje_map(self)

        for key, item in self.cart.items():
            item = item.copy()
            item['key'] = key
            if 'varijacija_naziv' not in item:
                item['varijacija_naziv'] = (
                    item['naziv']
                    if item.get('variation_id') and item.get('naziv') != item.get('product_naziv')
                    else ''
                )
            if 'product_naziv' not in item:
                item['product_naziv'] = item.get('naziv', '')
            item['cijena_decimal'] = Decimal(item['cijena'])
            item['bazna_cijena_decimal'] = Decimal(item['bazna_cijena'])

            # Compute deal if exists
            deal_info = None
            try:
                from .models import Product
                product = Product.objects.filter(pk=item['product_id']).first()
                if product:
                    deal_info = get_deal_info_for_cart_item(item, product)
            except Exception:
                pass

            if deal_info and deal_info.get('has_discount'):
                item['ukupno_stavka'] = deal_info['deal_total']
                item['deal_info'] = deal_info
            else:
                item['ukupno_stavka'] = item['cijena_decimal'] * item['quantity']
                item['deal_info'] = deal_info if deal_info and deal_info.get('message') else None

            # Uslov prodaja: popust na jednu jedinicu kad ostatak korpe dostigne prag
            try:
                from .models import Akcija
                uslov = None
                for candidate in Akcija.objects.filter(
                    tip=Akcija.Tip.USLOV,
                    aktivan=True,
                    artikal_id=item['product_id'],
                    popust_postotak__isnull=False,
                    prag_korpe_km__isnull=False,
                ).order_by('redoslijed', '-id'):
                    if candidate.jos_traje():
                        uslov = candidate
                        break
                if uslov:
                    pct = uslov.popust_postotak
                    threshold = uslov.prag_korpe_km
                    item['akcija_popup_discount'] = {
                        'percent': float(pct),
                        'threshold': float(threshold),
                        'akcija_id': uslov.id,
                    }
                    # Uslov prodaja: tačno 1 komad ovog artikla se NE računa u prag;
                    # ostatak korpe (ostali artikli + preostali komadi ovog) ide u prag.
                    # Primjer: 20 KM/kom, prag 50 KM, samo ovaj artikal:
                    #   3 kom → prag 40 KM (2×20), bez popusta;
                    #   4 kom → prag 60 KM (3×20), popust na 1 komad (4.).
                    qualifying_total = base_total - item['cijena_decimal']
                    if qualifying_total >= threshold and pct > 0:
                        disc_price = (item['cijena_decimal'] * (Decimal('1') - pct / Decimal('100'))).quantize(Decimal('0.01'))
                        item['ukupno_stavka'] = item['cijena_decimal'] * (item['quantity'] - 1) + disc_price
                        item['discounted_unit_price'] = disc_price
            except Exception:
                pass

            nudjenje = korpa_nudjenje_map.get(item['product_id'])
            if nudjenje:
                item['korpa_nudjenje'] = nudjenje

            yield item

    def __len__(self):
        return sum(item['quantity'] for item in self.cart.values())

    @property
    def item_count(self):
        return len(self.cart)

    @property
    def ukupno(self):
        total = Decimal('0')
        for item in self:
            total += Decimal(str(item.get('ukupno_stavka', 0)))
        return total.quantize(Decimal('0.01'))

    @property
    def pdv_pregled(self):
        return izracunaj_pdv(self.ukupno)

    def sazetak(self, user=None):
        from .pricing import izracunaj_sazetak
        return izracunaj_sazetak(
            self.ukupno,
            user=user,
            coupon_code=self.get_coupon_code(),
            cart_items=list(self),
            recovery_discount_percent=self.get_recovery_discount_percent(),
        )

    def get_product_and_variation(self, item):
        try:
            product = Product.objects.get(pk=item['product_id'], aktivan=True, na_stanju=True)
        except Product.DoesNotExist:
            return None, None
        variation = None
        if item.get('variation_id'):
            try:
                variation = ProductVariation.objects.get(
                    pk=item['variation_id'], artikal=product, na_stanju=True,
                )
            except ProductVariation.DoesNotExist:
                return None, None
        return product, variation