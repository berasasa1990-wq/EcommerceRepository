import json
import time
from pathlib import Path
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import connection
from django.test import Client, TestCase
from django.test.utils import CaptureQueriesContext
from .models import Brand, Category, Product, HomeCategoryShowcase, HomeBrandShowcase
from . import views


class Performance2RegressionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.category = Category.objects.create(naziv='QA štapovi', slug='qa-stapovi')
        for index in range(6):
            brand = Brand.objects.create(naziv=f'QA brend {index}', slug=f'qa-brand-{index}')
            HomeBrandShowcase.objects.create(brend=brand, aktivan=True)
            for row in range(12):
                Product.objects.create(naziv=f'QA štap {index}-{row}', slug=f'qa-stap-{index}-{row}', cijena=20, stanje=5, na_stanju=True, kategorija=cls.category, brend=brand)
        HomeCategoryShowcase.objects.create(kategorija=cls.category, aktivan=True)
        cls.user = get_user_model().objects.create_user('perf2-customer', password='local-test-only')

    def test_00_profile_home_sections_and_sql(self):
        sections = ('_home_latest_products', '_home_featured_products', '_home_sale_products', '_home_trust_items', '_home_category_showcases', '_home_brand_showcases', '_home_vlogs')
        samples=[]
        cache.clear()
        for warm in (False, True):
            timings=[]
            sql_timings=[]
            def measure_sql(execute, sql, params, many, context):
                start=time.perf_counter()
                try:
                    return execute(sql, params, many, context)
                finally:
                    sql_timings.append((time.perf_counter()-start)*1000)

            def wrapped(name, fn):
                def run(*args, **kwargs):
                    start=time.perf_counter()
                    sql_start=len(sql_timings)
                    with CaptureQueriesContext(connection) as cap:
                        result=fn(*args,**kwargs)
                    timings.append({'section':name,'ms':round((time.perf_counter()-start)*1000,3),'queries':len(cap),'sql_ms':round(sum(sql_timings[sql_start:]),3)})
                    return result
                return run
            from contextlib import ExitStack
            with ExitStack() as stack:
                for name in sections:
                    stack.enter_context(patch.object(views,name,wrapped(name,getattr(views,name))))
                start=time.perf_counter()
                with connection.execute_wrapper(measure_sql), CaptureQueriesContext(connection) as cap:
                    response=self.client.get('/')
                self.assertEqual(response.status_code,200)
                samples.append({'warm':warm,'total_ms':round((time.perf_counter()-start)*1000,3),'sql_count':len(cap),'sql_ms':round(sum(sql_timings),3),'html_bytes':len(response.content),'sections':timings,'sql':[q['sql'] for q in cap]})
        self.assertLessEqual(samples[1]['sql_count'], 14, 'Warm homepage SQL budget')
        Path('/private/tmp/performance2-app.json').write_text(json.dumps(samples,indent=2))
        print('PERF2',[(s['warm'],s['total_ms'],s['sql_count'],s['sql_ms']) for s in samples])

    def test_public_search_cart_navigation_canonical_and_logged_in_user(self):
        from django.conf import settings
        response=self.client.get('/')
        self.assertEqual(response.status_code,200)
        self.assertContains(response, 'id="home-first-screen-css"')
        self.assertContains(response, f'<link rel="canonical" href="{settings.SEO_CANONICAL_URL}/"')
        self.assertContains(response,'searchInput')
        self.assertContains(response,'mobile-home-dock')
        self.assertContains(response,'/korpa/')
        self.assertContains(response,'/kategorija/qa-stapovi/')
        self.assertEqual(self.client.get('/api/pretraga/', {'q':'QA štap'}).status_code,200)
        self.assertEqual(self.client.get('/', {'q':'QA štap'}).status_code,200)
        self.assertEqual(self.client.get('/korpa/').status_code,200)
        self.client.force_login(self.user)
        self.assertEqual(self.client.get('/').status_code,200)
        self.assertEqual(self.client.get('/korpa/').status_code,200)
        self.assertEqual(self.client.get('/nalog/').status_code,200)

    def test_home_preserves_customer_cart_and_session_isolation(self):
        product = Product.objects.filter(brend__slug='qa-brand-0').first()
        key = f'{product.pk}:0'
        session = self.client.session
        session['cart'] = {key: {
            'product_id': product.pk, 'variation_id': None, 'quantity': 2,
            'cijena': '20.00', 'bazna_cijena': '20.00', 'na_akciji': False,
            'naziv': product.naziv, 'product_naziv': product.naziv,
        }}
        session.save()
        for signed_in in (False, True):
            if signed_in:
                self.client.force_login(self.user)
            response = self.client.get('/')
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.context['cart_count'], 2)
            self.assertEqual(self.client.session['cart'][key]['quantity'], 2)
            self.assertEqual(response.context['user'].is_authenticated, signed_in)
            other = Client().get('/')
            self.assertEqual(other.context['cart_count'], 0)
            self.assertFalse(other.context['user'].is_authenticated)
