"""Behavior and SQL budgets for the second storefront performance audit."""
import statistics
import time
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.core.cache import cache
from django.db import connection
from django.http import HttpResponse
from django.template.loader import render_to_string
from django.test import RequestFactory, TestCase
from django.test.utils import CaptureQueriesContext

from .models import (
    Akcija, AkcijaFlashLine, B2BAccount, B2BSubmission, Brand, CardPayment,
    Category, Coupon, HomeBrandShowcase, HomeFeaturedProduct, Order, OrderItem,
    Product, ProductVariation, Tag,
)
from .request_performance import RequestReadMiddleware
from .social_proof import _pick_product, build_social_proof_payload
from .views import (
    _catalog_filter_brand, _catalog_filter_category, _catalog_filter_category_ids,
    _home_brand_showcases_uncached,
    _home_product_queryset, _order_qs_by_lager_priority,
    _product_ids_for_product_tag_query, _home_featured_products,
)


class CatalogRequestReadTests(TestCase):
    def setUp(self):
        cache.clear()
        self.brand = Brand.objects.create(naziv='Audit brend', slug='audit-brand')
        self.category = Category.objects.create(naziv='Audit kategorija', slug='audit-category')
        self.product = Product.objects.create(
            naziv='Audit artikal', slug='audit-product', cijena=20,
            stanje=10, na_stanju=True, brend=self.brand, kategorija=self.category,
        )

    def test_filtered_home_reads_brand_once_and_keeps_unknown_brand_empty(self):
        self.client.get('/')  # Warm unrelated public menu and settings.
        with CaptureQueriesContext(connection) as captured:
            response = self.client.get('/', {'brend': self.brand.slug})
        self.assertEqual(response.status_code, 200)
        self.assertEqual([p.pk for p in response.context['search_products']], [self.product.pk])
        self.assertEqual(response.context['selected_brand'].pk, self.brand.pk)
        lookups = [q for q in captured if 'FROM "EcommerceApp_brand"' in q['sql']
                   and '"slug" =' in q['sql']]
        self.assertEqual(len(lookups), 1)
        response = self.client.get('/', {'brend': 'does-not-exist'})
        self.assertEqual(response.context['search_products'], [])
        self.assertIsNone(response.context['selected_brand'])

    def test_entity_reads_end_with_get_and_writes_read_current_values(self):
        def read(request):
            first = _catalog_filter_brand(self.brand.slug)
            second = _catalog_filter_brand(self.brand.slug)
            self.assertIs(first, second)
            first_category = _catalog_filter_category(self.category.slug)
            self.assertIs(first_category, _catalog_filter_category(self.category.slug))
            self.assertIsNone(_catalog_filter_brand('absent'))
            self.assertIsNone(_catalog_filter_brand('absent'))
            return HttpResponse(first.naziv)
        middleware = RequestReadMiddleware(read)
        with self.assertNumQueries(3):
            before = middleware(RequestFactory().get('/'))
        Brand.objects.filter(pk=self.brand.pk).update(naziv='Updated between requests')
        with self.assertNumQueries(3):
            after = middleware(RequestFactory().get('/'))
        self.assertNotEqual(before.content, after.content)

        def write(request):
            before = _catalog_filter_brand(self.brand.slug).naziv
            Brand.objects.filter(pk=self.brand.pk).update(naziv='Updated during POST')
            after = _catalog_filter_brand(self.brand.slug).naziv
            self.assertNotEqual(before, after)
            return HttpResponse(after)
        with self.assertNumQueries(3):
            RequestReadMiddleware(write)(RequestFactory().post('/'))

    def test_tag_results_read_once_per_get_and_refresh_between_requests(self):
        tag = Tag.objects.create(naziv='Audit exact tag', slug='audit-tag')
        self.product.tagovi.add(tag)
        def read(request):
            first = _product_ids_for_product_tag_query('Audit exact tag')
            self.assertEqual(first, _product_ids_for_product_tag_query('Audit exact tag'))
            request.result_ids = first
            return HttpResponse('OK')
        request = RequestFactory().get('/')
        with self.assertNumQueries(2):
            RequestReadMiddleware(read)(request)
        self.assertEqual(request.result_ids, {self.product.pk})
        self.product.tagovi.clear()
        request = RequestFactory().get('/')
        with self.assertNumQueries(2):
            RequestReadMiddleware(read)(request)
        self.assertEqual(request.result_ids, set())

    def test_category_subtree_is_traversed_once_per_get_and_refreshes(self):
        child = Category.objects.create(naziv='Audit child', slug='audit-child', roditelj=self.category)
        leaf = Category.objects.create(naziv='Audit leaf', slug='audit-leaf', roditelj=child)
        blocked = Category.objects.create(naziv='Inactive branch', slug='inactive-branch',
                                          roditelj=self.category, aktivan=False)
        Category.objects.create(naziv='Blocked leaf', slug='blocked-leaf', roditelj=blocked)
        expected = tuple(self.category.get_descendant_ids())
        def read(request):
            request.ids = _catalog_filter_category_ids(self.category)
            self.assertIs(request.ids, _catalog_filter_category_ids(self.category))
            return HttpResponse('OK')
        request = RequestFactory().get('/')
        with self.assertNumQueries(3):
            RequestReadMiddleware(read)(request)
        self.assertEqual(request.ids, expected)
        Category.objects.filter(pk=child.pk).update(aktivan=False)
        request = RequestFactory().get('/')
        with self.assertNumQueries(1):
            RequestReadMiddleware(read)(request)
        self.assertEqual(request.ids, (self.category.pk,))
        self.assertNotIn(leaf.pk, request.ids)


class HomeBrandBatchTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.category = Category.objects.create(naziv='Audit kartice')
        cls.brands = []
        cls.offer = Akcija.objects.create(naziv='Audit akcija', tip=Akcija.Tip.AKCIJSKA, popust_postotak=10)
        for index in range(6):
            brand = Brand.objects.create(naziv=f'Audit brand {index}', slug=f'audit-brand-{index}')
            cls.brands.append(brand)
            HomeBrandShowcase.objects.create(brend=brand, redoslijed=5-index)
            for row in range(16):
                product = Product.objects.create(
                    naziv=f'Audit product {index}-{row}', slug=f'audit-card-{index}-{row}',
                    cijena=20 + row, prioritet_lagera=row % 3, stanje=5, na_stanju=True,
                    brend=brand, kategorija=cls.category,
                )
                if row % 4 == 0:
                    ProductVariation.objects.create(artikal=product, naziv='M', cijena=18, stanje=4, na_stanju=True)
                    AkcijaFlashLine.objects.create(akcija=cls.offer, product=product)
            # Hidden and unavailable products must not enter the public window.
            Product.objects.filter(brend=brand, naziv__endswith='-15').update(sakriven_do_stanja=True)
            Product.objects.filter(brend=brand, naziv__endswith='-14').update(na_stanju=False, stanje=0)
        empty = Brand.objects.create(naziv='Empty audit brand', slug='empty-audit-brand')
        HomeBrandShowcase.objects.create(brend=empty)
        inactive = Brand.objects.create(naziv='Inactive audit brand', slug='inactive-audit-brand')
        HomeBrandShowcase.objects.create(brend=inactive, aktivan=False)

    def request(self, staff=False):
        request = RequestFactory().get('/')
        request.user = get_user_model()(is_superuser=True, is_staff=True) if staff else AnonymousUser()
        request.session = {'staff_edit_mode': staff}
        return request

    def baseline(self, request):
        sections = []
        for entry in HomeBrandShowcase.objects.filter(aktivan=True).select_related('brend').order_by('redoslijed', 'id'):
            products = list(_order_qs_by_lager_priority(
                _home_product_queryset(request).filter(brend_id=entry.brend_id), '-kreiran',
            )[:10])
            if products:
                sections.append({'title': entry.display_title(), 'brand': entry.brend,
                                 'brand_url': f'/?brend={entry.brend.slug}#product-showcase',
                                 'products': products})
        return sections

    def snapshot(self, sections):
        return [(section['title'], section['brand'].pk, section['brand_url'],
                 [(p.pk, p.katalog_prikazna_cijena, p.katalog_bazna_cijena,
                   p.katalog_na_akciji, p.prikazna_slika) for p in section['products']])
                for section in sections]

    def test_batched_sections_preserve_order_prices_hidden_rules_and_limits(self):
        for staff in (False, True):
            with self.subTest(staff=staff):
                request = self.request(staff)
                before = self.snapshot(self.baseline(request))
                with CaptureQueriesContext(connection) as captured:
                    sections = _home_brand_showcases_uncached(request)
                    after = self.snapshot(sections)
                self.assertEqual(after, before)
                self.assertEqual(len(sections), 6)
                self.assertTrue(all(len(section['products']) == 10 for section in sections))
                self.assertEqual(len(captured), 4)
                self.assertTrue(any('ROW_NUMBER()' in q['sql'] for q in captured))

    def test_batch_cost_and_identical_card_html(self):
        request = self.request()
        def measure(loader):
            timings = []
            for _ in range(5):
                started = time.perf_counter()
                with CaptureQueriesContext(connection) as queries:
                    sections = loader(request)
                timings.append((time.perf_counter() - started) * 1000)
            html = '\n'.join(render_to_string(
                'bera_reference/partials/home_product_card.html', {'product': p},
            ) for section in sections for p in section['products'])
            return sections, len(queries), statistics.median(timings), html
        before, before_count, before_ms, before_html = measure(self.baseline)
        after, after_count, after_ms, after_html = measure(_home_brand_showcases_uncached)
        self.assertEqual(self.snapshot(before), self.snapshot(after))
        self.assertEqual(before_html, after_html)
        self.assertEqual(before_count, 20)
        self.assertEqual(after_count, 4)
        print(f'HOME_BRAND_BATCH SQL {before_count} -> {after_count}; local median 5 runs {before_ms:.2f} -> {after_ms:.2f} ms')


class AccountOrderSQLTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = get_user_model().objects.create_user('audit-user', 'audit@example.test', 'test-password')
        self.other = get_user_model().objects.create_user('audit-other', 'other@example.test', 'test-password')
        self.client.force_login(self.user)

    def make_order(self, user=None, number=None):
        order = Order.objects.create(korisnik=user or self.user, broj=number or str(uuid4()),
                                     ime_prezime='Audit customer', ukupno=20)
        OrderItem.objects.create(narudzba=order, naziv='Audit item', cijena=20, kolicina=1)
        return order

    def test_account_order_count_does_not_add_payment_queries_or_leak_orders(self):
        for _ in range(2):
            self.make_order()
        self.make_order(self.other, 'PRIVATE-AUDIT-ORDER')
        self.client.get('/nalog/')
        with CaptureQueriesContext(connection) as captured:
            small = self.client.get('/nalog/')
        small_count = len(captured)
        for _ in range(12):
            self.make_order()
        with CaptureQueriesContext(connection) as captured:
            large = self.client.get('/nalog/')
        self.assertEqual(len(large.context['orders']), 14)
        self.assertLessEqual(len(captured), small_count + 1)
        self.assertNotContains(large, 'PRIVATE-AUDIT-ORDER')
        self.assertEqual(small.status_code, 200)
        self.assertEqual(large.status_code, 200)
        self.assertFalse(any('FROM "EcommerceApp_cardpayment"' in q['sql']
                             or 'FROM "EcommerceApp_b2bsubmission"' in q['sql'] for q in captured))
        print(f'ACCOUNT SQL 2 orders={small_count}, 14 orders={len(captured)}')

    def test_account_keeps_cash_bank_and_card_payment_labels(self):
        cash = self.make_order(number='AUDIT-CASH')
        bank = self.make_order(number='AUDIT-BANK')
        paid = self.make_order(number='AUDIT-CARD-PAID')
        pending = self.make_order(number='AUDIT-CARD-PENDING')
        account = B2BAccount.objects.create(username='audit-company', company='Audit Company')
        B2BSubmission.objects.create(account=account, order=bank, token=uuid4(), payment='ziralno', netto_total=20)
        for order, status in ((paid, 'paid'), (pending, 'pending')):
            CardPayment.objects.create(order=order, amount=2000, status=status,
                                       checkout_snapshot=[{'large': 'unused snapshot' * 1000}])
        response = self.client.get('/nalog/')
        self.assertEqual(response.status_code, 200)
        orders = {order.pk: order for order in response.context['orders']}
        with self.assertNumQueries(0):
            self.assertEqual(orders[cash.pk].payment_label, 'Gotovinski')
            self.assertEqual(orders[bank.pk].payment_label, 'Žiralno')
            self.assertEqual(orders[paid.pk].payment_label, 'Karticom')
            self.assertEqual(orders[pending.pk].payment_label, 'Karticom — čeka uplatu')
        self.assertIn('checkout_snapshot', orders[paid.pk].card_payment.get_deferred_fields())


class SocialProofSQLTests(TestCase):
    def setUp(self):
        self.product = Product.objects.create(naziv='Audit proof', slug='audit-proof', cijena=20,
                                              aktivan=True, na_stanju=True, stanje=5)
        Product.objects.create(naziv='Hidden proof', slug='hidden-proof', cijena=20,
                               aktivan=True, na_stanju=True, sakriven_do_stanja=True)
        Product.objects.create(naziv='Inactive proof', slug='inactive-proof', cijena=20,
                               aktivan=False, na_stanju=True)

    def test_public_random_payload_uses_one_narrow_query(self):
        with CaptureQueriesContext(connection) as captured:
            payload = build_social_proof_payload()
        self.assertEqual(len(captured), 1)
        self.assertEqual(payload['product_id'], self.product.pk)
        self.assertEqual(payload['product_name'], self.product.naziv)
        self.assertEqual(payload['product_url'], self.product.get_absolute_url())
        self.assertNotIn('"opis"', captured[0]['sql'])

    def test_out_of_stock_fallback_and_excluded_products_are_preserved(self):
        Product.objects.filter(pk=self.product.pk).update(na_stanju=False, stanje=0)
        with self.assertNumQueries(2):
            chosen = _pick_product()
        self.assertEqual(chosen.pk, self.product.pk)
        with self.assertNumQueries(2):
            self.assertIsNone(_pick_product(exclude_ids=[str(self.product.pk)]))


class CartSummaryReuseTests(TestCase):
    def setUp(self):
        from .cart import Cart
        cache.clear()
        self.products = [Product.objects.create(naziv=f'Summary audit {i}', cijena=10 + 10*i,
                                                stanje=20, na_stanju=True) for i in range(2)]
        session = self.client.session
        session['cart'] = {f'{p.pk}:0': {
            'product_id': p.pk, 'variation_id': None, 'quantity': 3 if i == 0 else 1,
            'cijena': str(p.cijena), 'bazna_cijena': str(p.cijena), 'na_akciji': False,
            'naziv': p.naziv, 'product_naziv': p.naziv,
        } for i, p in enumerate(self.products)}
        self.request = SimpleNamespace(session=session, user=AnonymousUser())
        self.cart = Cart(self.request)
        Akcija.objects.create(naziv='Summary quantity deal', tip=Akcija.Tip.X_PLUS_1,
                              artikal=self.products[0], deal_vrsta='2+1', popust_postotak=50)
        Akcija.objects.create(naziv='Summary threshold', tip=Akcija.Tip.USLOV,
                              artikal=self.products[1], popust_postotak=20, prag_korpe_km=20)
        Coupon.objects.create(kod='AUDIT-CART10', postotak=10)
        self.cart.set_coupon_code('AUDIT-CART10')
        self.cart.set_recovery_discount(5)

    def legacy_summary(self):
        from .live_visitor_offer import has_free_shipping_reward, registration_reward_coupon_code
        from .online_gift import (reward_discount_km, reward_discount_percent,
                                 reward_free_shipping, scratch_cart_reward)
        from .pricing import izracunaj_sazetak
        coupon_code = self.cart.get_coupon_code() or registration_reward_coupon_code(self.request.user)
        free_shipping = self.cart.has_free_shipping_first() or has_free_shipping_reward(self.request, self.request.user)
        reward = scratch_cart_reward(self.request, self.cart.ukupno)
        summary = izracunaj_sazetak(self.cart.ukupno, user=self.request.user,
            coupon_code=coupon_code, cart_items=list(self.cart),
            recovery_discount_percent=self.cart.get_recovery_discount_percent(),
            free_shipping_reward=free_shipping,
            prize_discount_percent=reward_discount_percent(self.request) + reward['percent'],
            prize_discount_km=reward_discount_km(self.request),
            prize_free_shipping=reward_free_shipping(self.request) or reward['free_shipping'])
        summary['scratch_reward'] = reward
        return summary

    def test_single_summary_preserves_deals_conditions_coupon_and_reward_totals(self):
        self.cart.sazetak(user=self.request.user)  # Warm public settings.
        with CaptureQueriesContext(connection) as captured:
            baseline = self.legacy_summary()
        baseline_count = len(captured)
        with CaptureQueriesContext(connection) as captured:
            optimized = self.cart.sazetak(user=self.request.user)
        self.assertEqual(optimized, baseline)
        self.assertEqual(optimized['medjuzbir'], Decimal('41.00'))
        self.assertGreaterEqual(baseline_count - len(captured), 14)
        print(f'CART_SUMMARY SQL {baseline_count} -> {len(captured)}')

        reward = {'percent': Decimal('5'), 'free_shipping': True, 'active': True}
        with patch('EcommerceApp.online_gift.scratch_cart_reward', return_value=reward) as scratch:
            reused = self.cart.sazetak(user=self.request.user, cart_items=list(self.cart))
        scratch.assert_called_once_with(self.request, Decimal('41.00'))
        self.assertEqual(reused['dostava'], Decimal('0.00'))
        self.assertEqual(reused['scratch_reward'], reward)

    def test_supplied_lines_avoid_reload_and_subsequent_summary_reads_new_cart(self):
        from .views import _cart_context
        expected = self.cart.sazetak(user=self.request.user)
        with CaptureQueriesContext(connection) as captured:
            context = _cart_context(self.request, self.cart)
        self.assertEqual(context['summary'], expected)
        product_reads = [q for q in captured if 'FROM "EcommerceApp_product"' in q['sql']
                         and not '"brend__naziv"' in q['sql']]
        self.assertLessEqual(len(product_reads), 2)
        self.cart.cart[f'{self.products[0].pk}:0']['quantity'] = 1
        changed = self.cart.sazetak(user=self.request.user)
        self.assertNotEqual(changed['medjuzbir'], expected['medjuzbir'])
        self.assertEqual(changed['medjuzbir'], Decimal('30.00'))

    def test_checkout_analytics_keep_item_ids_quantities_prices_and_upsell_exclusion(self):
        from .upsell import _exclude_cart_products
        session = self.request.session
        session.save()
        # Use the same synthetic customer session as the real checkout request.
        self.client.cookies['sessionid'] = session.session_key
        with patch('EcommerceApp.meta_conversions.send_event') as event:
            response = self.client.get('/narudzba/')
        self.assertEqual(response.status_code, 200)
        call = next(call for call in event.call_args_list if call.args[1] == 'InitiateCheckout')
        data = call.kwargs['custom_data']
        self.assertEqual(data['num_items'], 4)
        self.assertEqual(data['value'], 50.0)
        self.assertEqual(data['content_ids'], [str(p.pk) for p in self.products])
        self.assertEqual(response.context['meta_initiate_num_items'], 4)
        self.assertEqual(response.context['meta_initiate_value'], Decimal('50.00'))
        offer = {'products': [{'id': self.products[0].pk}, {'id': 999999}]}
        with self.assertNumQueries(0):
            self.assertEqual(_exclude_cart_products(offer, self.cart)['products'], [{'id': 999999}])


class FeaturedFallbackPriceTests(TestCase):
    def test_manual_featured_cached_prices_and_variations_do_not_query_per_card(self):
        cache.clear()
        category = Category.objects.create(naziv='Audit fallback category')
        offer = Akcija.objects.create(naziv='Audit fallback flash', tip=Akcija.Tip.AKCIJSKA,
                                     popust_postotak=10)
        expected_ids = []
        for index in range(8):
            product = Product.objects.create(naziv=f'Audit fallback {index}', cijena=20,
                                             kategorija=category, stanje=10, na_stanju=True)
            HomeFeaturedProduct.objects.create(artikal=product, redoslijed=index)
            AkcijaFlashLine.objects.create(product=product, akcija=offer)
            ProductVariation.objects.create(artikal=product, naziv='M', cijena=30,
                                             na_stanju=True, stanje=5)
            expected_ids.append(product.pk)
        request = RequestFactory().get('/')
        request.user, request.session = AnonymousUser(), {}
        cold = _home_featured_products(request)
        with self.assertNumQueries(0):
            warm = _home_featured_products(request)
            self.assertEqual([p.pk for p in warm], expected_ids)
            self.assertEqual([p.katalog_prikazna_cijena for p in warm], [Decimal('27.00')] * 8)
            self.assertEqual([p.prikazna_cijena for p in warm], [Decimal('18.00')] * 8)
            self.assertEqual([p.katalog_na_akciji for p in warm], [True] * 8)
        self.assertEqual([p.pk for p in cold], expected_ids)
