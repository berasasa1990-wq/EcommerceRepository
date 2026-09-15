from django.test import SimpleTestCase
from django.urls import Resolver404, resolve


class RegistrationPopupRemovalTests(SimpleTestCase):
    def test_retired_customer_and_staff_endpoints_are_removed(self):
        paths = [
            '/api/chat/config/', '/api/chat/', '/api/chat/send/', '/api/chat/poll/',
            '/api/chat/staff/inbox/', '/api/chat/staff/1/send/',
            '/nalog/pregled-sajta/', '/nalog/olx-poruke/', '/savjetnik/', '/savjetnik/kupi-set/',
            '/online-nagrada/otkrij/', '/online-nagrada/status/', '/online-nagrada/zatvori/',
            '/ponuda/status/', '/ponuda/dodaj/', '/ponuda/aktiviraj/', '/ponuda/zatvori/',
            '/ai-dwell/aktiviraj/', '/preporuka/dodaj/', '/preporuka/zatvori/',
            '/nalog/uzivo-analitika/ponuda/', '/nalog/uzivo-analitika/nagrada/',
            '/nalog/uzivo-analitika/nagrada-auto/', '/nalog/uzivo-analitika/registracija/',
        ]
        for path in paths:
            with self.subTest(path=path):
                try:
                    match = resolve(path)
                except Resolver404:
                    continue
                # Existing product-offer slugs share /ponuda/; retired API handlers must not resolve.
                self.assertEqual(match.url_name, 'ponuda_javna')
