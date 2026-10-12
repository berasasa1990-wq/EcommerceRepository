import os
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import Product, WarehouseLocation, WarehouseMovement, WarehouseStock, WMSOrder


class WMSMobileHistoryTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_superuser('mobile-wms', 'mobile@example.test', 'test-only')
        cls.product = Product.objects.create(naziv='Shimano Ultegra test dugog naziva artikla za mobilni prikaz', sifra='SH-5500-TEST', cijena=20, stanje=8)
        cls.location = WarehouseLocation.objects.create(sifra='MOBILE-A', naziv='Lokacija sa dužim nazivom police')
        WarehouseStock.objects.create(product=cls.product, location=cls.location, kolicina=8)
        cls.ids = [WarehouseMovement.objects.create(product=cls.product, location=cls.location,
                    tip='prijem', kolicina=1, napomena='Duga napomena ' * 10).pk for _ in range(45)]
        # Verify IDs determine order even when timestamps differ.
        WarehouseMovement.objects.filter(pk=cls.ids[0]).update(kreiran=timezone.now())
        for i in range(3):
            WMSOrder.objects.create(kupac=f'Kupac sa dugim imenom {i}', tip='online')

    def setUp(self):
        self.client.force_login(self.user)
        p = patch('EcommerceApp.views_wms.module_locked', return_value=False)
        p.start()
        self.addCleanup(p.stop)
        self.url = reverse('staff_wms_section', args=['istorija'])

    def test_history_twenty_per_page_descending_ids_and_no_filters(self):
        response = self.client.get(self.url)
        page = response.context['history']
        self.assertEqual(page.paginator.per_page, 20)
        self.assertEqual([row.pk for row in page], list(reversed(self.ids))[:20])
        self.assertEqual(page.paginator.num_pages, 3)
        self.assertNotContains(response, 'wms-history-filters')
        self.assertContains(response, 'data-label="ID"')
        self.assertContains(response, '?page=2')
        next_page = self.client.get(self.url, {'page': 2}).context['history']
        self.assertEqual([row.pk for row in next_page], list(reversed(self.ids))[20:40])
        self.assertEqual(len(self.client.get(self.url, {'page': 3}).context['history']), 5)

    def test_obsolete_filters_do_not_hide_history(self):
        response = self.client.get(self.url, {'q': 'nepostojeci', 'tip': 'prodaja', 'date_from': '2099-01-01'})
        self.assertEqual(response.context['history'].paginator.count, 45)
        self.assertContains(response, '?page=2')

    def test_mobile_pages_render(self):
        for section in ('istorija', 'narudzbe', 'lokacije', 'zalihe'):
            response = self.client.get(reverse('staff_wms_section', args=[section]), {'q': 'Shimano'} if section == 'zalihe' else {})
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, 'wmsMobileSidebar')
            fixture_dir = os.environ.get('WMS_MOBILE_QA_DIR')
            if fixture_dir:
                folder = Path(fixture_dir)
                folder.mkdir(parents=True, exist_ok=True)
                (folder / f'{section}.html').write_bytes(response.content)
