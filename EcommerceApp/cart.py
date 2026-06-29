from decimal import ROUND_HALF_UP, Decimal

from .models import Product, ProductVariation

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

    def add(self, product, variation=None, quantity=1, custom_price=None):
        key = self._line_key(product.pk, variation.pk if variation else None)
        if custom_price is not None:
            price = Decimal(str(custom_price))
        else:
            price = variation.prikazna_cijena if variation else product.prikazna_cijena
        bazna = variation.bazna_cijena if variation else product.bazna_cijena
        na_akciji = variation.na_akciji if variation else product.na_akciji
        varijacija_naziv = variation.naziv if variation else ''
        naziv = product.naziv
        sifra = variation.sifra if variation and variation.sifra else (product.sifra or '')
        slika = ''
        if variation and variation.slika:
            slika = variation.slika.url
        elif product.slika:
            slika = product.slika.url

        if key in self.cart:
            self.cart[key]['quantity'] += quantity
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
                'upsell': bool(custom_price),  # mark as from upsell if custom price used
            }
        self.save()

    def remove(self, key):
        if key in self.cart:
            del self.cart[key]
            self.save()

    def set_quantity(self, key, quantity):
        if key not in self.cart:
            return
        if quantity <= 0:
            self.remove(key)
        else:
            self.cart[key]['quantity'] = quantity
            self.save()

    def clear(self):
        self.cart = {}
        self.clear_coupon()
        self.save()

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
            item['ukupno_stavka'] = item['cijena_decimal'] * item['quantity']
            yield item

    def __len__(self):
        return sum(item['quantity'] for item in self.cart.values())

    @property
    def item_count(self):
        return len(self.cart)

    @property
    def ukupno(self):
        return sum(
            Decimal(item['cijena']) * item['quantity']
            for item in self.cart.values()
        )

    @property
    def pdv_pregled(self):
        return izracunaj_pdv(self.ukupno)

    def sazetak(self, user=None):
        from .pricing import izracunaj_sazetak
        return izracunaj_sazetak(
            self.ukupno,
            user=user,
            coupon_code=self.get_coupon_code(),
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