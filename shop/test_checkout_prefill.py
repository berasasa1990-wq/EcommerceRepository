from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from .models import CustomerContact, Product


class CheckoutPrefillTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('prefill', email='buyer@example.com', first_name='Marko', last_name='Test')
        CustomerContact.objects.create(customer=self.user, email=self.user.email, phone='+38761123456')
        product = Product.objects.create(name='Artikal', price=10)
        session = self.client.session
        session['cart'] = {str(product.pk): 1}
        session.save()

    def test_registered_details_prefill_checkout(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse('checkout'))
        form = response.context['form']
        self.assertEqual(form['customer_name'].value(), 'Marko Test')
        self.assertEqual(form['email'].value(), 'buyer@example.com')
        self.assertEqual(form['phone'].value(), '+38761123456')

    def test_posted_details_take_priority_in_invalid_form(self):
        self.client.force_login(self.user)
        response = self.client.post(reverse('checkout'), {'customer_name': 'Drugi primalac', 'email': 'other@example.com', 'phone': '062123456'})
        self.assertEqual(response.context['form']['phone'].value(), '062123456')
        self.assertEqual(response.context['form']['customer_name'].value(), 'Drugi primalac')
        self.assertEqual(response.context['form']['email'].value(), 'other@example.com')

    def test_guest_has_no_prefilled_personal_details(self):
        response = self.client.get(reverse('checkout'))
        self.assertFalse(response.context['form']['email'].value())
        self.assertFalse(response.context['form']['phone'].value())

    def test_existing_customer_without_phone_can_checkout(self):
        self.user.shop_contact.delete()
        self.client.force_login(self.user)
        response = self.client.get(reverse('checkout'))
        self.assertEqual(response.context['form']['email'].value(), self.user.email)
        self.assertFalse(response.context['form']['phone'].value())
