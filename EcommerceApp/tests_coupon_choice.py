from decimal import Decimal

from django.contrib.auth.models import User
from django.contrib.auth.models import AnonymousUser
from django.template.loader import render_to_string
from django.test import TestCase, override_settings
from django.urls import reverse

from .cart import Cart
from .loyalty import osiguraj_loyalty_karticu
from .models import Coupon


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
