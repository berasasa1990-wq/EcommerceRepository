from django.test import TestCase, override_settings
from django.urls import reverse
from .models import Product


@override_settings(STORAGES={
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
})
class CatalogLoadMoreTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        for index in range(25):
            Product.objects.create(
                naziv=f'Loadmorefixture {index:02}', cijena=10,
                aktivan=True, na_stanju=True, stanje=10,
            )

    def test_batches_keep_search_and_stop_after_last_products(self):
        url = reverse('home')
        first = self.client.get(url, {'q': 'Loadmorefixture'})
        self.assertEqual(first.status_code, 200)
        self.assertEqual(len(first.context['page_obj'].object_list), 20)
        self.assertContains(first, 'Prikaži još')
        self.assertContains(first, 'q=Loadmorefixture')
        self.assertContains(first, 'page=2')
        self.assertNotContains(first, 'catalog-page-link')
        second = self.client.get(url, {'q': 'Loadmorefixture', 'page': 2})
        self.assertEqual(second.status_code, 200)
        self.assertEqual(len(second.context['page_obj'].object_list), 5)
        self.assertNotContains(second, 'data-catalog-next=')
        first_ids = {item.pk for item in first.context['page_obj'].object_list}
        self.assertTrue(first_ids.isdisjoint(item.pk for item in second.context['page_obj'].object_list))
