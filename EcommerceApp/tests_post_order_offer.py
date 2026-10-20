from decimal import Decimal
from types import SimpleNamespace

from django.contrib import admin
from django.test import TestCase, override_settings, RequestFactory
from django.urls import reverse
from django.utils import timezone

from .models import Akcija, AkcijaFlashLine, CardPayment, Order, OrderItem, OrderStockHold, Product, ProductVariation
from .post_order_offer import SESSION_KEY, grant_offer


@override_settings(SITE_PREP_ENABLED=False, STORAGES={
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
})
class PostOrderOfferTests(TestCase):
    def setUp(self):
        self.product = Product.objects.create(naziv='Specijalna ponuda', sifra='POST-OFFER', cijena=20, stanje=3, na_stanju=True)
        self.order = Order.objects.create(ime_prezime='Kupac', medjuzbir=50, ukupno=61, dostava=11,
                                          lager_status=Order.LagerStatus.REZERVISANO)
        self.offer = Akcija.objects.create(naziv='Poslije narudžbe', tip=Akcija.Tip.POSLIJE_NARUDZBE, aktivan=True, popust_postotak=50)
        self.line = AkcijaFlashLine.objects.create(akcija=self.offer, product=self.product)
        self.grant()

    def grant(self):
        session = self.client.session
        grant_offer(SimpleNamespace(session=session), self.order)
        session.save()

    def add(self, **data):
        return self.client.post(reverse('post_order_add', args=[self.order.broj]), {'line_id': self.line.pk, 'confirmed': '1', **data})

    def test_add_reserves_stock_updates_total_keeps_shipping_and_is_idempotent(self):
        response = self.add()
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(self.add().status_code, 200)
        item = OrderItem.objects.get(narudzba=self.order)
        self.assertEqual(item.cijena, Decimal('10.00'))
        self.assertEqual(item.popust_iznos, Decimal('10.00'))
        self.assertEqual(OrderStockHold.objects.get(narudzba=self.order).kolicina, 1)
        self.order.refresh_from_db()
        self.assertEqual((self.order.medjuzbir, self.order.ukupno, self.order.dostava), (60, 71, 11))

    def test_expired_offer_and_other_session_cannot_add(self):
        self.client.cookies.clear()
        self.assertEqual(self.add().status_code, 403)
        self.grant()
        session = self.client.session
        entries = session[SESSION_KEY]
        entries[str(self.order.pk)]['expires'] = timezone.now().timestamp() - 1
        session[SESSION_KEY] = entries
        session.save()
        self.assertEqual(self.add().status_code, 403)
        self.assertFalse(self.order.stavke.exists())

    def test_regrant_does_not_reset_timer(self):
        before = self.client.session[SESSION_KEY]
        self.grant()
        self.assertEqual(self.client.session[SESSION_KEY], before)

    def test_other_campaign_and_invalid_line_rejected(self):
        other = Akcija.objects.create(naziv='Druga', tip=Akcija.Tip.POSLIJE_NARUDZBE, aktivan=True)
        line = AkcijaFlashLine.objects.create(akcija=other, product=self.product)
        self.assertEqual(self.add(line_id=line.pk).status_code, 400)
        self.assertEqual(self.add(line_id='invalid').status_code, 400)

    def test_processed_order_and_disabled_offer_rejected(self):
        self.order.zapakovana = True
        self.order.save()
        self.assertEqual(self.add().status_code, 409)
        self.order.zapakovana = False
        self.order.save()
        self.offer.aktivan = False
        self.offer.save()
        self.assertEqual(self.add().status_code, 400)

    def test_no_stock_rolls_back_item_and_total(self):
        self.product.stanje = 0
        self.product.save()
        self.assertEqual(self.add().status_code, 409)
        self.order.refresh_from_db()
        self.assertEqual(self.order.ukupno, 61)
        self.assertFalse(self.order.stavke.exists())

    def test_line_discount_uses_regular_price(self):
        self.line.popust_postotak = 25
        self.line.save()
        self.product.akcijska_cijena = 12
        self.product.save()
        self.assertEqual(self.add().status_code, 200)
        self.assertEqual(self.order.stavke.get().cijena, 15)

    def test_variation_required_and_price_uses_variation(self):
        v = ProductVariation.objects.create(artikal=self.product, naziv='Velika', cijena=30, stanje=3, na_stanju=True)
        self.assertEqual(self.add().status_code, 400)
        result = self.add(variation_id=v.pk)
        self.assertEqual(result.status_code, 200, result.content)
        self.assertEqual(self.order.stavke.get().cijena, 15)
        self.assertEqual(self.order.stavke.get().varijacija, v)

    def test_card_payment_has_no_offer_even_for_free_products(self):
        CardPayment.objects.create(order=self.order, amount=61, status='paid')
        self.assertEqual(self.add().status_code, 409)
        self.assertFalse(self.order.stavke.exists())
        response = self.client.get(reverse('order_success', args=[self.order.broj]))
        self.assertNotContains(response, 'postOrderOffer')
        self.line.popust_postotak = 100
        self.line.save()
        self.assertEqual(self.add().status_code, 409)
        session = self.client.session
        session.pop(SESSION_KEY, None)
        session.save()
        self.grant()
        self.assertNotIn(SESSION_KEY, self.client.session)

    def test_confirmation_shows_authorized_offer(self):
        response = self.client.get(reverse('order_success', args=[self.order.broj]))
        self.assertContains(response, 'postOrderOffer')
        self.assertContains(response, 'Specijalna ponuda samo za vas!')
        self.assertIn('no-store', response.headers['Cache-Control'])
        self.client.cookies.clear()
        response = self.client.get(reverse('order_success', args=[self.order.broj]))
        self.assertNotContains(response, 'postOrderOffer')
        self.assertContains(response, 'Vaša narudžba je uspješno zaprimljena!')

    def test_admin_has_new_choice_and_line_editor(self):
        model_admin = admin.site._registry[Akcija]
        request = RequestFactory().post('/', {'tip': 'poslije_narudzbe'})
        from django.contrib.auth.models import User
        request.user = User(is_superuser=True, is_staff=True, is_active=True)
        choices = dict(model_admin.formfield_for_choice_field(Akcija._meta.get_field('tip'), request).choices)
        self.assertIn('poslije_narudzbe', choices)
        inlines = model_admin.get_inline_instances(request, self.offer)
        self.assertEqual(inlines[0].model, AkcijaFlashLine)
        fields = model_admin.get_fieldsets(request, self.offer)
        self.assertNotIn('popust_postotak', str(fields))
        self.assertEqual(inlines[0].max_num, 4)

    def test_legacy_card_payment_has_no_offer(self):
        self.order.napomena = 'Plaćeno karticom'
        self.order.save()
        response = self.client.get(reverse('order_success', args=[self.order.broj]))
        self.assertNotContains(response, 'postOrderOffer')
        self.assertEqual(self.add().status_code, 409)


    def test_admin_requires_discount_next_to_each_selected_product(self):
        from django.contrib.auth.models import User
        request = RequestFactory().post('/', {'tip': 'poslije_narudzbe'})
        request.user = User(is_superuser=True, is_staff=True, is_active=True)
        inline = admin.site._registry[Akcija].get_inline_instances(request, self.offer)[0]
        FormSet = inline.get_formset(request, self.offer)
        data = {'flash_lines-TOTAL_FORMS': '1', 'flash_lines-INITIAL_FORMS': '0',
                'flash_lines-0-product': str(self.product.pk), 'flash_lines-0-redoslijed': '0'}
        formset = FormSet(data, instance=self.offer, prefix='flash_lines')
        self.assertFalse(formset.is_valid())
        self.assertIn('popust_postotak', formset.forms[0].errors)
        data['flash_lines-0-popust_postotak'] = '35'
        formset = FormSet(data, instance=self.offer, prefix='flash_lines')
        self.assertTrue(formset.is_valid(), formset.errors)

    def test_add_requires_explicit_confirmation(self):
        response = self.client.post(reverse('post_order_add', args=[self.order.broj]), {'line_id': self.line.pk})
        self.assertEqual(response.status_code, 400)
        self.assertFalse(self.order.stavke.exists())

    def test_batch_confirmation_rolls_back_all_items_if_one_is_unavailable(self):
        import json
        unavailable = Product.objects.create(naziv='Nedostupan', sifra='NO-STOCK', cijena=12, stanje=0, na_stanju=True)
        line = AkcijaFlashLine.objects.create(akcija=self.offer, product=unavailable, popust_postotak=50)
        response = self.client.post(reverse('post_order_add', args=[self.order.broj]), {
            'confirmed': '1', 'items': json.dumps([{'line_id': self.line.pk}, {'line_id': line.pk}])})
        self.assertEqual(response.status_code, 409)
        self.assertFalse(self.order.stavke.exists())
        self.assertFalse(OrderStockHold.objects.filter(narudzba=self.order).exists())
        self.order.refresh_from_db()
        self.assertEqual(self.order.ukupno, 61)

    def test_batch_confirmation_adds_selection_once(self):
        import json
        product = Product.objects.create(naziv='Drugi artikal', sifra='SECOND-OFFER', cijena=12, stanje=2, na_stanju=True)
        line = AkcijaFlashLine.objects.create(akcija=self.offer, product=product, popust_postotak=50)
        data = {'confirmed': '1', 'items': json.dumps([{'line_id': self.line.pk}, {'line_id': line.pk}])}
        url = reverse('post_order_add', args=[self.order.broj])
        self.assertEqual(self.client.post(url, data).status_code, 200)
        self.assertEqual(self.client.post(url, data).status_code, 200)
        self.assertEqual(self.order.stavke.count(), 2)
        self.order.refresh_from_db()
        self.assertEqual(self.order.ukupno, 77)
        self.assertEqual(self.order.dostava, 11)
