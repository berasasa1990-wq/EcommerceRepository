from django.test import TestCase
from .models import Product
from .views import _apply_search_filter


class StorefrontSpellingSearchTests(TestCase):
    def test_short_word_errors_and_all_query_words(self):
        target = Product.objects.create(naziv='Slim Fish mamac', naziv_normalized='slim fish mamac', slug='slim-fish', cijena=10)
        Product.objects.create(naziv='Slim rod', slug='slim-rod', cijena=10)
        for query in ('slim fsh', 'slim fish', 'slim fosh', 'slim fihs', 'slim fiish', 'fish slim'):
            with self.subTest(query=query):
                self.assertEqual(list(_apply_search_filter(Product.objects.all(), query).values_list('pk', flat=True)), [target.pk])
        for query in ('slim xyz', 'slim 123', 'slim fh'):
            with self.subTest(query=query):
                self.assertFalse(_apply_search_filter(Product.objects.all(), query).exists())

    def test_homepage_live_search_endpoint_recognizes_missing_letter(self):
        Product.objects.create(naziv='Slim Fish mamac', naziv_normalized='slim fish mamac', slug='live-slim-fish', cijena=10, aktivan=True, sakriven_do_stanja=False)
        Product.objects.create(naziv='Slim rod', slug='live-slim-rod', cijena=10, aktivan=True, sakriven_do_stanja=False)
        response = self.client.get('/api/pretraga/', {'q': 'slim fsh', 'limit': 4})
        self.assertEqual(response.status_code, 200)
        self.assertEqual([row['slug'] for row in response.json()['results']], ['live-slim-fish'])
