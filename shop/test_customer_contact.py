from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from .forms import CustomerRegistrationForm, CustomerProfileForm
from .models import CustomerContact


class CustomerContactTests(TestCase):
    def payload(self, **changes):
        data = dict(first_name='Test', last_name='Kupac',
                    email='Buyer@Example.com', phone='061 123-456',
                    password1='River!Stone-2934', password2='River!Stone-2934')
        return {**data, **changes}

    def test_registration_persists_phone_in_personal_data(self):
        response = self.client.post(reverse('customer_register'), self.payload())
        self.assertRedirects(response, reverse('account'))
        contact = CustomerContact.objects.get()
        self.assertEqual(contact.phone, '+38761123456')
        self.assertEqual(contact.customer.email, 'buyer@example.com')
        response = self.client.get(reverse('account'), {'tab': 'podaci'})
        self.assertContains(response, 'value="+38761123456"')
        self.assertContains(response, 'Telefon')

    def test_duplicate_email_and_formatted_phone_are_rejected(self):
        form = CustomerRegistrationForm(self.payload())
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        for phone in ['+387 61 123 456', '0038761123456', '061/123-456']:
            duplicate = CustomerRegistrationForm(self.payload(username='other', email='other@example.com', phone=phone))
            self.assertFalse(duplicate.is_valid())
            self.assertIn('phone', duplicate.errors)
        duplicate = CustomerRegistrationForm(self.payload(username='other', email='BUYER@example.COM', phone='062123456'))
        self.assertFalse(duplicate.is_valid())
        self.assertIn('email', duplicate.errors)

    def test_existing_user_email_is_reserved_and_phone_required(self):
        get_user_model().objects.create_user('legacy', email='Buyer@example.com')
        form = CustomerRegistrationForm(self.payload(phone=''))
        self.assertFalse(form.is_valid())
        self.assertIn('email', form.errors)
        self.assertIn('phone', form.errors)

    def test_profile_cannot_take_another_customers_contact(self):
        form = CustomerRegistrationForm(self.payload())
        self.assertTrue(form.is_valid(), form.errors)
        user = form.save()
        same = CustomerProfileForm(self.payload(), instance=user)
        self.assertTrue(same.is_valid(), same.errors)
        same.save()
        other = get_user_model().objects.create_user('another')
        duplicate = CustomerProfileForm(self.payload(), instance=other)
        self.assertFalse(duplicate.is_valid())
        self.assertIn('email', duplicate.errors)
        self.assertIn('phone', duplicate.errors)

    def test_registration_has_no_username_and_email_login_is_case_insensitive(self):
        self.assertNotIn('username', CustomerRegistrationForm().fields)
        self.client.post(reverse('customer_register'), self.payload())
        self.client.post(reverse('customer_logout'))
        response = self.client.post(reverse('customer_login'), {
            'username': 'BUYER@EXAMPLE.COM', 'password': 'River!Stone-2934',
        })
        self.assertRedirects(response, reverse('account'))

    def test_existing_customer_can_login_with_email(self):
        get_user_model().objects.create_user('legacy-login', email='legacy@example.com', password='River!Stone-2934')
        response = self.client.post(reverse('customer_login'), {
            'username': 'legacy@example.com', 'password': 'River!Stone-2934',
        })
        self.assertRedirects(response, reverse('account'))

    def test_ambiguous_email_does_not_login_to_wrong_account(self):
        for username in ['first-legacy', 'second-legacy']:
            get_user_model().objects.create_user(username, email='duplicate@example.com', password='River!Stone-2934')
        response = self.client.post(reverse('customer_login'), {
            'username': 'duplicate@example.com', 'password': 'River!Stone-2934',
        })
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('_auth_user_id', self.client.session)
