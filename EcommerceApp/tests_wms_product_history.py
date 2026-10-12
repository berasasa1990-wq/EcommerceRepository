from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from .models import Product, WarehouseLocation, WarehouseMovement


class WMSProductHistoryTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser('history-wms', 'history@example.test', 'test-only')
        self.client.force_login(self.user)
        self.product = Product.objects.create(naziv='Traženi artikal', sifra='HIST', cijena=10)
        other = Product.objects.create(naziv='Drugi artikal', cijena=10)
        source = WarehouseLocation.objects.create(sifra='HIST-A', naziv='Izvor')
        destination = WarehouseLocation.objects.create(sifra='HIST-B', naziv='Odredište')
        self.ids = [WarehouseMovement.objects.create(product=self.product, location=source if i % 2 else destination,
                    to_location=destination if i % 2 else None, tip='transfer' if i % 2 else 'korekcija', kolicina=1,
                    napomena='<script>opasan tekst</script>').pk for i in range(23)]
        WarehouseMovement.objects.create(product=other, location=source, tip='prijem', kolicina=5)
        self.url = reverse('staff_wms_product_history', args=[self.product.pk])
        p = patch('EcommerceApp.views_wms.module_locked', return_value=False)
        p.start()
        self.addCleanup(p.stop)

    def test_all_locations_and_actions_only_for_selected_product(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['history'].paginator.count, 23)
        self.assertEqual([r.pk for r in response.context['history']], list(reversed(self.ids))[:20])
        self.assertContains(response, 'Izvor')
        self.assertContains(response, 'Odredište')
        self.assertContains(response, 'Transfer')
        self.assertContains(response, 'Korekcija')
        self.assertNotContains(response, 'Drugi artikal')
        self.assertNotContains(response, '<script>opasan tekst</script>')
        self.assertEqual(response['Cache-Control'], 'private, no-store')
        self.assertEqual(len(self.client.get(self.url, {'page': 2}).context['history']), 3)

    def test_permissions_and_read_only(self):
        self.assertEqual(self.client.post(self.url).status_code, 405)
        with patch('EcommerceApp.views_wms.module_locked', return_value=True):
            self.assertEqual(self.client.get(self.url).status_code, 403)
        self.client.logout()
        self.assertEqual(self.client.get(self.url).status_code, 302)

    def test_stock_icon_and_empty_history(self):
        response = self.client.get(reverse('staff_wms_section', args=['zalihe']), {'q': 'Traženi'})
        self.assertContains(response, 'data-product-history="' + self.url + '"')
        WarehouseMovement.objects.filter(product=self.product).delete()
        self.assertContains(self.client.get(self.url), 'Nema evidentiranih promjena')
