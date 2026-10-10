from decimal import Decimal
from django.test import TestCase
from .models import Coupon
from .pricing import izracunaj_sazetak, annotate_cart_coupon_prices


class CouponSaleExclusionTests(TestCase):
    def items(self):
        return [
            {'cijena_decimal': Decimal('100'), 'bazna_cijena_decimal': Decimal('100'), 'quantity': 1, 'ukupno_stavka': Decimal('100')},
            {'cijena_decimal': Decimal('80'), 'bazna_cijena_decimal': Decimal('100'), 'quantity': 2, 'ukupno_stavka': Decimal('160'), 'na_akciji': True},
        ]

    def test_percentage_excludes_sale_items_in_total_and_display(self):
        coupon = Coupon.objects.create(kod='SALECHECK', postotak=10)
        items = self.items()
        summary = izracunaj_sazetak(260, coupon_code=coupon.kod, cart_items=items)
        self.assertEqual(summary['kupon_popust'], Decimal('10'))
        annotate_cart_coupon_prices(items, coupon)
        self.assertEqual(items[0]['coupon_total'], Decimal('90'))
        self.assertNotIn('coupon_total', items[1])
        summary = izracunaj_sazetak(160, coupon_code=coupon.kod, cart_items=items[1:])
        self.assertEqual(summary['kupon_popust'], Decimal('0'))

    def test_fixed_coupon_cannot_discount_sale_items(self):
        coupon = Coupon.objects.create(kod='FIXEDCHECK', vrsta=Coupon.Vrsta.IZNOS, iznos=150)
        summary = izracunaj_sazetak(260, coupon_code=coupon.kod, cart_items=self.items())
        self.assertEqual(summary['kupon_popust'], Decimal('100'))
        summary = izracunaj_sazetak(160, coupon_code=coupon.kod, cart_items=self.items()[1:])
        self.assertEqual(summary['kupon_popust'], Decimal('0'))

    def test_quantity_deal_discounts_only_units_at_regular_price(self):
        coupon = Coupon.objects.create(kod='DEALCHECK', postotak=10)
        item = {'cijena_decimal': Decimal('100'), 'bazna_cijena_decimal': Decimal('100'), 'quantity': 3, 'ukupno_stavka': Decimal('250'), 'deal_info': {'has_discount': True, 'full_price_count': 2}}
        summary = izracunaj_sazetak(250, coupon_code=coupon.kod, cart_items=[item])
        self.assertEqual(summary['kupon_popust'], Decimal('20'))
        annotate_cart_coupon_prices([item], coupon)
        self.assertEqual(item['coupon_total'], Decimal('230'))
