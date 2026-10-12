import json
import time
from pathlib import Path
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.db import connection
from django.core.cache import cache
from .models import Product, Category, Banner


class HomePerformanceAuditTests(TestCase):
    def test_home_cold_warm_profile(self):
        category = Category.objects.create(naziv='QA kategorija')
        for index in range(40):
            Product.objects.create(naziv=f'QA artikal {index}', cijena=10, kategorija=category, stanje=5, na_stanju=True)
        Banner.objects.create(tip='hero', aktivan=True, slika='qa/hero.webp', slika_mobilna='qa/mobile.jpg')
        cache.clear()
        samples = []
        for run in range(2):
            start = time.perf_counter()
            with CaptureQueriesContext(connection) as queries:
                response = self.client.get('/')
            self.assertEqual(response.status_code, 200)
            samples.append({'run': 'cold' if run == 0 else 'warm', 'ms': round((time.perf_counter()-start)*1000, 2), 'queries':len(queries), 'html_bytes':len(response.content), 'sql':[q['sql'] for q in queries]})
        Path('/private/tmp/carp-home-local-profile.json').write_text(json.dumps(samples, indent=2))
        print('HOME_PROFILE', [(s['run'],s['ms'],s['queries']) for s in samples])
