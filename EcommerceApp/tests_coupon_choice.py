from decimal import Decimal

from django.contrib.auth.models import User
from django.contrib.auth.models import AnonymousUser
from django.template.loader import render_to_string
from django.test import TestCase, override_settings
from django.urls import reverse

from .cart import Cart
from .loyalty import osiguraj_loyalty_karticu
from .models import Coupon, Product


@override_settings(STORAGES={
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
})
class CouponChoiceTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('buyer@example.com', 'buyer@example.com', 'secret12345')
        self.client.force_login(self.user)
        self.card = osiguraj_loyalty_karticu(self.user)

    def test_single_loyalty_reward_applies_immediately(self):
        coupon_box = render_to_string('partials/coupon_form.html', {'user': self.user, 'loyalty_card': self.card})
        self.assertIn('Primijeni kupon ili karticu', coupon_box)
        self.assertNotIn('Unesi kod', coupon_box)
        response = self.client.post(reverse('apply_coupon'), {'choose_reward': '1', 'next': 'cart'})
        self.assertRedirects(response, reverse('cart'))
        self.assertEqual(self.client.session[Cart.COUPON_KEY], self.card.kod)

    def test_multiple_rewards_show_choice_and_apply_selected_code(self):
        coupon = Coupon.objects.create(
            kod='LICNI10', naziv='Lični poklon', vlasnik=self.user,
            vrsta=Coupon.Vrsta.IZNOS, iznos=Decimal('10'), postotak=0,
        )
        response = self.client.post(reverse('apply_coupon'), {'choose_reward': '1', 'next': 'cart'})
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'coupon_choice.html')
        self.assertContains(response, self.card.kod)
        self.assertContains(response, '10.00 KM popusta')
        self.assertNotContains(response, 'Imate drugi kod kupona ili kartice')
        self.assertNotIn(Cart.COUPON_KEY, self.client.session)

        coupon_box = render_to_string('partials/coupon_form.html', {
            'user': self.user,
            'loyalty_card': self.card,
            'coupon_choices': [Coupon.objects.get(kod=self.card.kod), coupon],
        })
        self.assertIn('data-coupon-choice-open', coupon_box)
        modal = render_to_string('partials/coupon_choice_modal.html', {
            'coupon_choices': [Coupon.objects.get(kod=self.card.kod), coupon],
        })
        self.assertIn('id="couponChoiceDialog"', modal)
        self.assertIn('aria-modal="true"', modal)
        self.assertIn('KM popusta', modal)
        self.assertNotIn('Imate drugi kod kupona ili kartice', coupon_box)

        response = self.client.post(reverse('apply_coupon'), {'kod': coupon.kod, 'next': 'cart'})
        self.assertRedirects(response, reverse('cart'))
        self.assertEqual(self.client.session[Cart.COUPON_KEY], coupon.kod)

    def test_other_users_personal_coupon_cannot_be_selected(self):
        other = User.objects.create_user('other@example.com', 'other@example.com', 'secret12345')
        coupon = Coupon.objects.create(kod='OTHER10', naziv='Tuđi', vlasnik=other, postotak=10)
        response = self.client.post(reverse('apply_coupon'), {'kod': coupon.kod, 'next': 'cart'})
        self.assertRedirects(response, reverse('cart'))
        self.assertNotIn(Cart.COUPON_KEY, self.client.session)

    def test_guest_gets_one_cart_button_then_code_entry(self):
        self.client.logout()
        coupon_box = render_to_string('partials/coupon_form.html', {'user': AnonymousUser()})
        self.assertIn('Primijeni kupon ili karticu', coupon_box)
        self.assertNotIn('Unesi kod', coupon_box)
        response = self.client.post(reverse('apply_coupon'), {'choose_reward': '1', 'next': 'cart'})
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'coupon_choice.html')
        self.assertContains(response, 'Unesite kod kupona ili kartice')

    def test_coupon_is_rejected_for_sale_only_cart_and_applies_to_regular_items(self):
        sale = Product.objects.create(
            naziv='Artikal na akciji', cijena=Decimal('50'), akcijska_cijena=Decimal('30'),
            na_stanju=True, stanje=5,
        )
        regular = Product.objects.create(naziv='Redovni artikal', cijena=Decimal('20'), na_stanju=True, stanje=5)
        Coupon.objects.create(kod='PROC10', naziv='Postotni kupon', postotak=10)
        Coupon.objects.create(kod='IZNOS40', naziv='Fiksni kupon', vrsta=Coupon.Vrsta.IZNOS, iznos=40)
        apply = reverse('apply_coupon')

        self.client.post(reverse('add_to_cart', args=[sale.slug]), {'quantity': 1})
        rejected = self.client.post(apply, {'kod': 'PROC10'}, follow=True)
        self.assertContains(rejected, 'Artikli u korpi su već sniženi.')
        self.assertNotIn(Cart.COUPON_KEY, self.client.session)
        self.assertEqual(rejected.context['summary']['kupon_popust'], Decimal('0.00'))

        self.client.post(reverse('add_to_cart', args=[regular.slug]), {'quantity': 1})
        for code, expected in [('PROC10', Decimal('2.00')), ('IZNOS40', Decimal('20.00'))]:
            applied = self.client.post(apply, {'kod': code}, follow=True)
            self.assertContains(applied, 'Artikal je već snižen. Kupon se ne primjenjuje.')
            self.assertEqual(applied.context['summary']['kupon_popust'], expected)
            items = {item['product_id']: item for item in applied.context['cart_items']}
            self.assertNotIn('coupon_total', items[sale.pk])
            self.assertEqual(items[regular.pk]['coupon_total'], Decimal('20.00') - expected)
