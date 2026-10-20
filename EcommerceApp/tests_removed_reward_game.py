from django.apps import apps
from django.template.loader import get_template
from django.test import SimpleTestCase
from django.urls import Resolver404, resolve


class RemovedRewardGameTests(SimpleTestCase):
    def test_retired_urls_no_longer_resolve(self):
        for path in ('/nagrade/greb-greb/status/', '/nagrade/greb-greb/otkrij/',
                     '/nagrade/greb-greb/dodaj-na-narudzbu/', '/nalog/greb-greb/'):
            with self.subTest(path=path), self.assertRaises(Resolver404):
                resolve(path)

    def test_templates_still_compile(self):
        for name in ('account/index.html', 'staff/orders_validation.html', 'staff/admin_panel.html',
                     'partials/coupon_form.html', 'order_success.html'):
            with self.subTest(template=name):
                get_template(name)

    def test_game_model_removed(self):
        with self.assertRaises(LookupError):
            apps.get_model('EcommerceApp', 'ScratchPrize')
