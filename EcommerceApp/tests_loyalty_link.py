from decimal import Decimal
from django.contrib.auth.models import User
from django.test import TestCase
from .models import LoyaltyCard, LoyaltyPurchase, Coupon
from .loyalty import povezi_rucnu_karticu_sa_nalogom


class LoyaltyLinkTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user('admin_link', is_superuser=True)
        self.source = User.objects.create_user('loy_061123456', first_name='Kupac')
        self.target = User.objects.create_user('registered_link', email='kupac@example.com')
        self.card = LoyaltyCard.objects.create(user=self.source, kod='123456', barkod='123456')
        self.online = LoyaltyCard.objects.create(user=self.target, kod='654321', barkod='654321')

    def test_merge_keeps_physical_code_and_purchases(self):
        LoyaltyPurchase.objects.create(kartica=self.card, iznos=Decimal('40'), kreirao=self.admin)
        purchase = LoyaltyPurchase.objects.create(kartica=self.online, iznos=Decimal('60'), kreirao=self.admin)
        coupon = Coupon.objects.create(kod='654321', postotak=3, vlasnik=self.target, loyalty_kartica=self.online)
        linked = povezi_rucnu_karticu_sa_nalogom('123456', 'kupac@example.com', actor=self.admin)
        self.assertEqual(linked.user_id, self.target.pk)
        self.assertEqual(linked.kod, '123456')
        self.assertEqual(linked.ukupna_potrosnja, Decimal('100'))
        purchase.refresh_from_db()
        self.assertEqual(purchase.kartica_id, linked.pk)
        coupon.refresh_from_db()
        self.assertFalse(coupon.aktivan)
        self.assertIsNone(coupon.loyalty_kartica_id)
        self.assertEqual(linked.kupon.vlasnik_id, self.target.pk)

    def test_staff_cannot_link(self):
        self.target.is_staff = True
        with self.assertRaises(ValueError):
            povezi_rucnu_karticu_sa_nalogom('123456', 'kupac@example.com', actor=self.target)
        self.card.refresh_from_db()
        self.assertEqual(self.card.user_id, self.source.pk)

    def test_registered_card_cannot_be_taken(self):
        with self.assertRaises(ValueError):
            povezi_rucnu_karticu_sa_nalogom('654321', 'kupac@example.com', actor=self.admin)
        self.assertEqual(LoyaltyCard.objects.count(), 2)


class ManualLoyaltyRegistrationTests(TestCase):
    def setUp(self):
        from .models import UserProfile
        self.manual = User.objects.create_user('loy_065111222')
        UserProfile.objects.create(user=self.manual, telefon='065111222')
        LoyaltyCard.objects.create(user=self.manual, kod='111222', barkod='111222')

    def test_registration_accepts_manual_card_phone(self):
        from .forms import RegisterForm
        form = RegisterForm(data={
            'ime_prezime': 'Novi Kupac',
            'email': 'novi.kupac@example.com',
            'telefon': '065111222',
            'lozinka': 'lozinka12',
            'lozinka_potvrda': 'lozinka12',
        })
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['telefon'], '065111222')

    def test_open_card_uses_existing_site_card(self):
        from .models import UserProfile
        admin = User.objects.create_superuser('desk_admin', 'desk@example.com', 'pass')
        customer = User.objects.create_user('web_open', email='web.open@example.com')
        UserProfile.objects.create(user=customer, telefon='065444555')
        LoyaltyCard.objects.create(user=customer, kod='444555', barkod='444555')
        self.client.force_login(admin)
        response = self.client.post('/nalog/loyalty/', {
            'action': 'open_card',
            'channel': 'admin',
            'telefon': '065444555',
            'ime': 'Web',
            'prezime': 'Kupac',
        })
        self.assertEqual(response.status_code, 302)
        self.assertIn('/nalog/loyalty/clan/444555/', response['Location'])

    def test_issue_card_allowed_when_site_account_has_phone(self):
        from .loyalty import izdaj_loyalty_karticu
        from .models import UserProfile
        customer = User.objects.create_user('web_issue', email='web.issue@example.com')
        UserProfile.objects.create(user=customer, telefon='065222333')
        LoyaltyCard.objects.create(user=customer, kod='333444', barkod='333444')
        card, user = izdaj_loyalty_karticu('Stari', 'Kupac', '065222333')
        self.assertTrue(user.username.startswith('loy_'))
        self.assertEqual(card.user.profil.telefon, '065222333')
        self.assertNotEqual(card.user_id, customer.pk)

    def test_profile_form_accepts_manual_card_phone(self):
        from .forms import ProfileForm
        from .models import UserProfile
        customer = User.objects.create_user('web_profile', email='web.profile@example.com')
        UserProfile.objects.create(user=customer, telefon='')
        form = ProfileForm(data={
            'ime_prezime': 'Web Kupac',
            'email': 'web.profile@example.com',
            'telefon': '065111222',
        }, exclude_user_id=customer.pk)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['telefon'], '065111222')

    def test_registered_duplicate_is_rejected_even_with_manual_match(self):
        from django.core.exceptions import ValidationError
        from .forms import RegisterForm
        from .models import UserProfile
        customer = User.objects.create_user('web_customer')
        UserProfile.objects.create(user=customer, telefon='+38765111222')
        form = RegisterForm()
        form.cleaned_data = {'telefon': '065111222'}
        with self.assertRaises(ValidationError):
            form.clean_telefon()

    def test_notice_disappears_after_linking(self):
        from .loyalty import potrebna_loyalty_veza
        from .models import UserProfile
        customer = User.objects.create_user('web_customer', email='web@example.com')
        UserProfile.objects.create(user=customer, telefon='065111222')
        admin = User.objects.create_user('link_admin', is_superuser=True)
        self.assertTrue(potrebna_loyalty_veza(customer))
        povezi_rucnu_karticu_sa_nalogom('111222', 'web@example.com', actor=admin)
        self.assertFalse(potrebna_loyalty_veza(customer))

    def test_registration_accepts_manual_card_email(self):
        from .forms import RegisterForm
        self.manual.email = 'stari@example.com'
        self.manual.save(update_fields=['email'])
        form = RegisterForm()
        form.cleaned_data = {'email': 'stari@example.com'}
        self.assertEqual(form.clean_email(), 'stari@example.com')

    def test_email_match_requires_sync(self):
        from .loyalty import potrebna_loyalty_veza
        self.manual.email = 'stari@example.com'
        self.manual.save(update_fields=['email'])
        customer = User.objects.create_user('web_email', email='stari@example.com')
        self.assertTrue(potrebna_loyalty_veza(customer))


class CustomerLoyaltySyncTests(TestCase):
    def setUp(self):
        from .models import UserProfile
        self.store = User.objects.create_user(
            'loy_061555666',
            email='loyalty@example.com',
            first_name='Stari',
        )
        UserProfile.objects.create(user=self.store, telefon='061555666')
        self.store_card = LoyaltyCard.objects.create(
            user=self.store, kod='555666', barkod='555666', ukupna_potrosnja=Decimal('40'),
        )
        LoyaltyPurchase.objects.create(kartica=self.store_card, iznos=Decimal('40'))
        self.customer = User.objects.create_user(
            'web_sync', email='web_sync@example.com', password='tajna1234',
        )
        UserProfile.objects.create(user=self.customer, telefon='061555666')
        self.site_card = LoyaltyCard.objects.create(
            user=self.customer, kod='999888', barkod='999888', ukupna_potrosnja=Decimal('25'),
        )

    def test_customer_sync_keeps_store_card_and_merges_spend(self):
        from .models import Order
        from .loyalty import sinhronizuj_loyalty_sa_nalogom
        Order.objects.create(
            korisnik=self.customer,
            ime_prezime='Web Kupac',
            email='web_sync@example.com',
            telefon='061555666',
            adresa='Ulica 1',
            grad='Sarajevo',
            ukupno=Decimal('25'),
        )
        Coupon.objects.create(
            kod='999888', postotak=3, vlasnik=self.customer, loyalty_kartica=self.site_card,
        )
        card = sinhronizuj_loyalty_sa_nalogom(self.customer)
        self.assertEqual(card.kod, '555666')
        self.assertEqual(card.user_id, self.customer.pk)
        self.assertEqual(card.ukupna_potrosnja, Decimal('65'))
        self.assertFalse(LoyaltyCard.objects.filter(pk=self.site_card.pk).exists())
        self.store.refresh_from_db()
        self.assertFalse(self.store.is_active)
        self.assertEqual(self.store.email, '')
        site_coupon = Coupon.objects.get(kod='999888')
        self.assertFalse(site_coupon.aktivan)
        self.assertEqual(card.kupon.vlasnik_id, self.customer.pk)

    def test_email_match_sync(self):
        from .models import UserProfile
        from .loyalty import sinhronizuj_loyalty_sa_nalogom
        other = User.objects.create_user('web_email_sync', email='loyalty@example.com', password='tajna1234')
        UserProfile.objects.create(user=other, telefon='061000111')
        LoyaltyCard.objects.create(user=other, kod='111000', barkod='111000')
        card = sinhronizuj_loyalty_sa_nalogom(other)
        self.assertEqual(card.kod, '555666')
        self.assertEqual(card.user_id, other.pk)

    def test_customer_sync_endpoint_is_removed(self):
        self.client.force_login(self.customer)
        response = self.client.post('/nalog/sinhronizuj-loyalty/')
        self.assertEqual(response.status_code, 404)
        self.store_card.refresh_from_db()
        self.assertEqual(self.store_card.user_id, self.store.pk)
        self.assertTrue(LoyaltyCard.objects.filter(pk=self.site_card.pk).exists())

    def test_phone_and_email_match_same_card(self):
        from .loyalty import loyalty_sync_pregled, sinhronizuj_loyalty_sa_nalogom
        self.customer.email = 'loyalty@example.com'
        self.customer.save(update_fields=['email'])
        preview = loyalty_sync_pregled(self.customer)
        self.assertEqual(set(preview['razlozi']), {'email', 'telefon'})
        self.assertEqual(preview['kartica'].kod, '555666')
        card = sinhronizuj_loyalty_sa_nalogom(self.customer)
        self.assertEqual(card.kod, '555666')
        self.assertEqual(card.user_id, self.customer.pk)

    def test_no_match_cannot_sync(self):
        from .loyalty import sinhronizuj_loyalty_sa_nalogom
        from .models import UserProfile
        stranger = User.objects.create_user('stranger', email='stranger@example.com', password='tajna1234')
        UserProfile.objects.create(user=stranger, telefon='061999888')
        LoyaltyCard.objects.create(user=stranger, kod='777666', barkod='777666')
        with self.assertRaises(ValueError):
            sinhronizuj_loyalty_sa_nalogom(stranger)
