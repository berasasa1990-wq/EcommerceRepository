from django.test import TestCase
from django.core.cache import cache
from .models import Category, Product


class ReelsCategoryTests(TestCase):
    def setUp(self):
        cache.clear()
        self.root = Category.objects.create(naziv='Mašinice', slug='masinice')
        self.spools = Category.objects.create(naziv='Rezervne špule', slug='rezervne-spule', roditelj=self.root)
        Product.objects.create(naziv='Mašinica QA', slug='masinica-qa', kategorija=self.root, cijena=10, na_stanju=True, stanje=3)
        Product.objects.create(naziv='Špula QA', slug='spula-qa', kategorija=self.spools, cijena=5, na_stanju=True, stanje=3)

    def test_choices_and_direct_products_preserve_all_and_spools(self):
        landing = self.client.get('/kategorija/masinice/')
        self.assertContains(landing, 'href="/kategorija/masinice/?direct=1"')
        self.assertContains(landing, 'href="/kategorija/rezervne-spule/"')
        direct = self.client.get('/kategorija/masinice/?direct=1')
        self.assertEqual([p.naziv for p in direct.context['products']], ['Mašinica QA'])
        self.assertIn('?direct=1', direct.context['canonical_url'])
        self.assertIn('direct=1', direct.context['catalog_query'])
        self.assertContains(direct, 'name="direct" value="1"')
        combined = self.client.get('/kategorija/masinice/?all=1')
        self.assertEqual({p.naziv for p in combined.context['products']}, {'Mašinica QA', 'Špula QA'})
        spools = self.client.get('/kategorija/rezervne-spule/')
        self.assertEqual([p.naziv for p in spools.context['products']], ['Špula QA'])
