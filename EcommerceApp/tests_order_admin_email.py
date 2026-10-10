from decimal import Decimal
from unittest.mock import patch
from django.test import TestCase
from .models import Order, OrderItem, Product
from .emails import _render_admin_order_html


class AdminOrderEmailTests(TestCase):
    def test_admin_template_contains_order_items_customer_and_panel_link(self):
        product = Product.objects.create(naziv='Email artikal', cijena=25)
        order = Order.objects.create(ime_prezime='Test kupac', email='test@example.invalid', telefon='061000000', adresa='Test 10', grad='Sarajevo', medjuzbir=50, dostava=5, ukupno=55)
        OrderItem.objects.create(narudzba=order, artikal=product, naziv=product.naziv, product_naziv=product.naziv, sifra='TEST-1', cijena=Decimal('25'), kolicina=2)
        with patch('EcommerceApp.emails.settings.SITE_URL', 'https://shop.example.invalid'):
            html = _render_admin_order_html(order)
        self.assertIn('Stigla je nova narudžba!', html)
        self.assertIn('Email artikal', html)
        self.assertIn('Test kupac', html)
        self.assertIn('TEST-1', html)
        self.assertIn(f'https://shop.example.invalid/nalog/provjera-narudzbi/{order.broj}/?preview=email', html)
        self.assertNotIn('/podesavanja/sekcije/', html)
        self.assertIn('55', html)
        self.assertNotIn('None', html)

    def test_discount_precedes_shipping_and_customer_uses_blue_layout(self):
        from .emails import _render_order_html
        order = Order.objects.create(ime_prezime='Kupac', email='test@example.invalid', medjuzbir=100, popust=10, dostava=11, ukupno=101)
        html = _render_admin_order_html(order)
        self.assertLess(html.index('Popust:</td>'), html.index('Iznos sa popustom:</td>'))
        self.assertLess(html.index('Iznos sa popustom:</td>'), html.index('Dostava:</td>'))
        self.assertIn('90', html)
        self.assertIn('101', html)
        customer = _render_order_html(order)
        self.assertIn('Hvala na narudžbi!', customer)
        self.assertIn('Podaci o kupcu', customer)
        self.assertNotIn('Otvori narudžbu u admin panelu', customer)
