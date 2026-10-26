from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from .models import Brand, Product


class PanelAccessTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        users = get_user_model()
        cls.manager = users.objects.create_user('manager', password='test-panel-pass', is_staff=True)
        cls.customer = users.objects.create_user('customer', password='test-panel-pass')
        cls.owner = users.objects.create_superuser('owner', password='test-panel-pass')

    def test_panel_requires_its_own_login(self):
        for path in ['/panel', '/panel/logo/', '/panel/brendovi/dodaj/']:
            response = self.client.get(path)
            self.assertEqual(response.status_code, 302)
            self.assertTrue(response.url.startswith('/panel/prijava/?next='))
        response = self.client.post('/panel/logo/', {})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith('/panel/prijava/'))

    def test_manager_login_returns_to_panel_form(self):
        target = '/panel/brendovi/dodaj/'
        response = self.client.post(reverse('panel_login'), {
            'username': 'manager', 'password': 'test-panel-pass', 'next': target,
        })
        self.assertRedirects(response, target)
        response = self.client.post(target, {'name': 'Uneseni brend'})
        self.assertRedirects(response, '/panel/brendovi/')
        brand = Brand.objects.get(name='Uneseni brend')
        response = self.client.post(reverse('panel_edit', args=['brendovi', brand.pk]), {'name': 'Izmijenjeni brend'})
        self.assertRedirects(response, '/panel/brendovi/')
        brand.refresh_from_db()
        self.assertEqual(brand.name, 'Izmijenjeni brend')

    def test_customer_cannot_login_to_panel(self):
        response = self.client.post(reverse('panel_login'), {'username': 'customer', 'password': 'test-panel-pass'})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Ovaj nalog nema pristup panelu.')
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_admin_only_for_superuser(self):
        self.client.force_login(self.manager)
        response = self.client.get('/admin/')
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith('/admin/login/'))
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get('/admin/').status_code, 200)
        self.assertEqual(self.client.get('/panel').status_code, 200)

    def test_panel_has_no_admin_links_and_logout_works(self):
        self.client.force_login(self.manager)
        product = Product.objects.create(name='Artikal', price=10)
        for section in ['artikli', 'brendovi', 'kategorije', 'banneri', 'akcije', 'logo']:
            response = self.client.get(reverse('panel_section', args=[section]))
            self.assertEqual(response.status_code, 200)
            self.assertNotContains(response, '/admin/')
        response = self.client.get(reverse('panel_edit', args=['artikli', product.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, '/admin/')
        self.assertRedirects(self.client.post(reverse('panel_logout')), reverse('panel_login'))
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_login_rejects_external_redirect(self):
        response = self.client.post(reverse('panel_login'), {
            'username': 'manager', 'password': 'test-panel-pass', 'next': 'https://example.org/',
        })
        self.assertRedirects(response, '/panel')

    def test_superuser_can_manage_accounts_in_admin(self):
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get('/admin/auth/user/').status_code, 200)
        self.assertEqual(self.client.get('/admin/auth/group/').status_code, 200)
        self.client.force_login(self.manager)
        response = self.client.get('/admin/auth/user/')
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith('/admin/login/'))

    def _superuser_request(self):
        from django.test import RequestFactory
        request = RequestFactory().get('/admin/')
        request.user = self.owner
        return request


class CategoryPageTests(TestCase):
    def test_category_links_filter_active_products(self):
        from .models import Category
        category = Category.objects.create(name='Laptopi')
        other = Category.objects.create(name='Telefoni')
        Product.objects.create(name='Laptop u kategoriji', price=100, category=category)
        Product.objects.create(name='Telefon druga kategorija', price=80, category=other)
        Product.objects.create(name='Skriveni laptop', price=50, category=category, is_active=False)
        self.assertContains(self.client.get('/'), f'href="{category.get_absolute_url()}"')
        response = self.client.get(category.get_absolute_url())
        self.assertContains(response, 'Laptop u kategoriji')
        self.assertContains(response, '<h1>Laptopi</h1>', html=True)
        self.assertNotContains(response, 'Telefon druga kategorija')
        self.assertNotContains(response, 'Skriveni laptop')
        self.assertNotContains(response, 'bera-hero-inner')
        self.assertRedirects(self.client.get(f'/?kategorija={category.pk}'), category.get_absolute_url())
        self.assertNotContains(self.client.get(category.get_absolute_url(), {'q': 'nepostoji'}), 'Laptop u kategoriji')

    def test_empty_and_missing_category(self):
        from .models import Category
        category = Category.objects.create(name='Prazna kategorija')
        self.assertContains(self.client.get(category.get_absolute_url()), 'Trenutno nema artikala u ovoj kategoriji.')
        self.assertEqual(self.client.get('/kategorija/999999/').status_code, 404)


class SalesPageTests(TestCase):
    def test_sales_page_only_shows_active_discounted_products(self):
        Product.objects.create(name='Sniženi artikal', price=80, original_price=100)
        Product.objects.create(name='Redovni artikal', price=100)
        Product.objects.create(name='Neaktivni artikal', price=80, original_price=100, is_active=False)
        response = self.client.get('/akcije/')
        self.assertContains(response, 'Sniženi artikal')
        self.assertContains(response, '-20%')
        self.assertNotContains(response, 'Redovni artikal')
        self.assertNotContains(response, 'Neaktivni artikal')
        self.assertContains(response, '<h1>Akcijska ponuda</h1>', html=True)
        self.assertContains(self.client.get('/'), 'href="/akcije/"')
        self.assertNotContains(self.client.get('/akcije/', {'q':'nepostoji'}), 'Sniženi artikal')

    def test_empty_sales_page(self):
        self.assertContains(self.client.get('/akcije/'), 'Trenutno nema sniženih artikala.')


class BestsellerTests(TestCase):
    def test_counts_distinct_orders_instead_of_units_or_lines(self):
        from .models import Order, OrderItem
        frequent = Product.objects.create(name='Često naručen', price=10)
        bulk = Product.objects.create(name='Velika količina', price=10)
        ignored = Product.objects.create(name='Otkazan artikal', price=10, is_bestselling=True)
        hidden = Product.objects.create(name='Skriven', price=10, is_active=False)
        first = Order.objects.create()
        second = Order.objects.create(status='completed')
        OrderItem.objects.create(order=first, product=frequent, quantity=1)
        OrderItem.objects.create(order=second, product=frequent, quantity=1)
        for _ in range(3):
            OrderItem.objects.create(order=first, product=bulk, quantity=100)
        OrderItem.objects.create(order=first, product=hidden)
        for status in ['draft', 'cancelled']:
            OrderItem.objects.create(order=Order.objects.create(status=status), product=ignored)
        ranked = list(Product.objects.filter(is_active=True).bestsellers())
        self.assertEqual([(p.pk, p.order_count) for p in ranked], [(frequent.pk, 2), (bulk.pk, 1)])
        response = self.client.get('/najprodavanije/')
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertLess(html.index('Često naručen'), html.index('Velika količina'))
        self.assertNotContains(response, 'Otkazan artikal')
        self.assertNotContains(response, '<h3>Skriven</h3>')
        self.assertContains(self.client.get('/'), 'href="/najprodavanije/"')
        self.assertNotContains(self.client.get('/najprodavanije/', {'q': 'nepostoji'}), 'Često naručen')

    def test_empty_orders_do_not_fall_back_to_manual_flag(self):
        Product.objects.create(name='Ručna oznaka', price=10, is_bestselling=True)
        response = self.client.get('/najprodavanije/')
        self.assertContains(response, 'Trenutno nema naručenih artikala.')
        self.assertNotContains(response, 'Ručna oznaka')


class ProductDetailTests(TestCase):
    def test_detail_and_links_use_saved_product_data(self):
        from .models import Category
        category = Category.objects.create(name='Test kategorija')
        product = Product.objects.create(name='Test detalja', price=80, original_price=100, category=category)
        response = self.client.get(product.get_absolute_url())
        self.assertContains(response, '<h1>Test detalja</h1>', html=True)
        self.assertContains(response, '-20%')
        self.assertContains(response, 'Na stanju')
        self.assertContains(response, category.get_absolute_url())
        self.assertContains(self.client.get('/noviteti/'), f'href="{product.get_absolute_url()}"')
        product.is_out_of_stock = True
        product.save()
        self.assertContains(self.client.get(product.get_absolute_url()), 'Nije na stanju')
        product.is_active = False
        product.save()
        self.assertEqual(self.client.get(product.get_absolute_url()).status_code, 404)
        self.assertEqual(self.client.get('/artikal/999999/').status_code, 404)


class SearchSuggestionTests(TestCase):
    def test_suggestions_include_image_price_and_detail_url(self):
        p = Product.objects.create(name='Laptop Test', price='99.90', image='products/test.png')
        Product.objects.create(name='Laptop skriven', price=20, is_active=False)
        response = self.client.get('/pretraga/prijedlozi/', {'q':'Laptop'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['products'], [{'name':p.name,'url':p.get_absolute_url(),'image':p.image.url,'price':'99.90 KM'}])
        self.assertEqual(self.client.get('/pretraga/prijedlozi/', {'q':''}).json()['products'], [])
        self.assertEqual(self.client.get('/pretraga/prijedlozi/', {'q':'nepostoji'}).json()['products'], [])
        self.assertEqual(self.client.get('/pretraga/prijedlozi/', {'q':'Laptop','sale':'1'}).json()['products'], [])

    def test_suggestions_limit_and_missing_image(self):
        for index in range(10):
            Product.objects.create(name=f'Test {index}', price=10)
        products = self.client.get('/pretraga/prijedlozi/', {'q':'Test'}).json()['products']
        self.assertEqual(len(products), 8)
        self.assertTrue(all(p['image']=='' for p in products))


class CartTests(TestCase):
    def setUp(self):
        self.product = Product.objects.create(name='Artikal za korpu', price='12.50', stock=3)
        self.url = reverse('cart_update', args=[self.product.pk])

    def test_add_update_remove_and_server_total(self):
        self.client.post(self.url, {'action': 'add', 'quantity': 2, 'price': '0.01'})
        response = self.client.get(reverse('cart'))
        self.assertEqual(response.context['cart_count'], 2)
        self.assertEqual(str(response.context['cart_total']), '25.00')
        self.assertContains(response, 'Artikal za korpu')
        self.client.post(self.url, {'quantity': 1})
        self.assertEqual(self.client.session['cart'][str(self.product.pk)], 1)
        self.client.post(self.url, {'quantity': 0})
        self.assertContains(self.client.get(reverse('cart')), 'Vaša korpa je prazna')

    def test_invalid_stock_and_unavailable_product(self):
        for quantity in ['bad', -1, 4]:
            self.assertEqual(self.client.post(self.url, {'quantity': quantity}).status_code, 400)
        self.product.is_out_of_stock = True
        self.product.save()
        self.assertEqual(self.client.post(self.url, {'action': 'add'}).status_code, 400)
        self.assertEqual(self.client.get(self.url).status_code, 405)

    def test_csrf_and_ajax_count(self):
        from django.test import Client
        client = Client(enforce_csrf_checks=True)
        self.assertEqual(client.post(self.url, {'action': 'add'}).status_code, 403)
        client.get(reverse('home'))
        response = client.post(self.url, {'action': 'add'}, HTTP_X_CSRFTOKEN=client.cookies['csrftoken'].value, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(response.json()['count'], 1)
        self.assertContains(client.get(reverse('home')), 'data-cart-count')

    def test_changed_availability_is_reflected(self):
        self.client.post(self.url, {'quantity': 3})
        self.product.stock = 1
        self.product.save()
        self.assertEqual(self.client.get(reverse('cart')).context['cart_count'], 1)
        self.product.is_active = False
        self.product.save()
        self.assertEqual(self.client.get(reverse('cart')).context['cart_count'], 0)


class CheckoutTests(TestCase):
    def setUp(self):
        self.product = Product.objects.create(name='Checkout artikal', price='20.00', stock=2)
        self.data = {'customer_name': 'Test Kupac', 'email': 'test@example.com', 'phone': '061123456', 'address': 'Test ulica 1', 'city': 'Sarajevo', 'postal_code': '71000'}

    def add(self):
        self.client.post(reverse('cart_update', args=[self.product.pk]), {'quantity': 2})

    def test_empty_checkout_redirects_to_cart(self):
        self.assertRedirects(self.client.get(reverse('checkout')), reverse('cart'))

    def test_cart_links_to_checkout_and_requires_delivery_details(self):
        self.add()
        self.assertContains(self.client.get(reverse('cart')), 'href="/checkout/"')
        response = self.client.post(reverse('checkout'), {})
        self.assertTrue(response.context['form'].errors)
        self.assertEqual(self.product.order_items.count(), 0)

    def test_confirm_order_snapshots_prices_and_clears_cart(self):
        self.add()
        response = self.client.post(reverse('checkout'), self.data)
        self.assertRedirects(response, '/checkout/?success=1')
        item = self.product.order_items.get()
        self.assertEqual(item.quantity, 2)
        self.assertEqual(str(item.unit_price), "20.00")
        self.assertEqual(item.order.total, 40)
        self.assertEqual(item.order.customer_name, 'Test Kupac')
        self.assertEqual(self.client.session['cart'], {})
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 0)
        self.assertTrue(self.product.is_out_of_stock)
        self.client.post(reverse('checkout'), self.data)
        self.assertEqual(self.product.order_items.count(), 1)

    def test_reject_changed_stock(self):
        self.add()
        self.product.stock = 1
        self.product.save()
        response = self.client.post(reverse('checkout'), self.data)
        self.assertTrue(response.context['form'].non_field_errors())
        self.assertEqual(self.product.order_items.count(), 0)


class DeliverySettingsTests(TestCase):
    def test_panel_delivery_price_and_checkout_snapshot(self):
        from .models import StoreSettings, Order
        manager = get_user_model().objects.create_user('delivery-manager', password='test-pass', is_staff=True)
        self.client.force_login(manager)
        url = reverse('panel_section', args=['podesavanje'])
        self.assertEqual(self.client.get(url).status_code, 200)
        self.client.post(url, {'delivery_price': '-1'})
        self.assertFalse(StoreSettings.objects.exists())
        self.assertRedirects(self.client.post(url, {'delivery_price': '8.50'}), url)
        product = Product.objects.create(name='Artikal', price='20.00', stock=2)
        self.client.post(reverse('cart_update', args=[product.pk]), {'quantity': 1})
        response = self.client.get(reverse('cart'))
        self.assertEqual(str(response.context['grand_total']), '28.50')
        self.client.post(reverse('checkout'), {'customer_name': 'Kupac', 'email': 'test@example.com', 'phone': '061123456', 'address': 'Ulica 1', 'city': 'Sarajevo', 'postal_code': '71000'})
        order = Order.objects.get()
        self.assertEqual(str(order.delivery_price), '8.50')
        self.assertEqual(str(order.total), '28.50')
        StoreSettings.objects.filter(pk=1).update(delivery_price=15)
        order.refresh_from_db()
        self.assertEqual(str(order.total), '28.50')

    def test_settings_requires_panel_access(self):
        self.assertEqual(self.client.post(reverse('panel_section', args=['podesavanje']), {'delivery_price': '5'}).status_code, 302)


class FreeDeliveryTests(TestCase):
    def test_threshold_boundaries_and_optional_setting(self):
        from decimal import Decimal
        from .models import StoreSettings
        from .forms import StoreSettingsForm
        store = StoreSettings(delivery_price=Decimal('8.50'), free_delivery_threshold=Decimal('100'))
        self.assertEqual(store.delivery_cost(Decimal('99.99')), Decimal('8.50'))
        self.assertEqual(store.delivery_cost(Decimal('100')), 0)
        self.assertEqual(store.delivery_cost(Decimal('100.01')), 0)
        store.free_delivery_threshold = None
        self.assertEqual(store.delivery_cost(Decimal('500')), Decimal('8.50'))
        self.assertFalse(StoreSettingsForm({'delivery_price': '8.50', 'free_delivery_threshold': '-1'}).is_valid())
        self.assertTrue(StoreSettingsForm({'delivery_price': '8.50', 'free_delivery_threshold': ''}).is_valid())

    def test_free_delivery_in_cart_and_saved_order(self):
        from .models import StoreSettings, Order
        StoreSettings.objects.create(pk=1, delivery_price='8.50', free_delivery_threshold='40')
        product = Product.objects.create(name='Artikal', price='20', stock=2)
        self.client.post(reverse('cart_update', args=[product.pk]), {'quantity': 2})
        response = self.client.get(reverse('cart'))
        self.assertEqual(response.context['delivery_price'], 0)
        self.assertEqual(response.context['grand_total'], 40)
        self.client.post(reverse('checkout'), {'customer_name': 'Kupac', 'email': 'test@example.com', 'phone': '061123456', 'address': 'Ulica 1', 'city': 'Sarajevo', 'postal_code': '71000'})
        order = Order.objects.get()
        self.assertEqual(order.delivery_price, 0)
        self.assertEqual(order.total, 40)


class FeaturedSettingsTests(TestCase):
    def test_selection_updates_home_without_changing_delivery(self):
        from .models import StoreSettings
        manager = get_user_model().objects.create_user('featured-manager', is_staff=True)
        self.client.force_login(manager)
        StoreSettings.objects.create(pk=1, delivery_price='8.50', free_delivery_threshold=205)
        previous = Product.objects.create(name='Prethodni', price=10, is_featured=True)
        selected = [Product.objects.create(name=f'Izdvojeni {i}', price=10) for i in range(7)]
        url = reverse('panel_section', args=['podesavanje'])
        self.assertContains(self.client.get(url), 'Izdvojeni artikli')
        self.assertRedirects(self.client.post(url, {'settings_section': 'featured', 'products': [p.pk for p in selected]}), url)
        previous.refresh_from_db()
        self.assertFalse(previous.is_featured)
        self.assertEqual(self.client.get(reverse('home')).context['featured_products'].count(), 7)
        self.assertEqual(str(StoreSettings.objects.get(pk=1).delivery_price), '8.50')
        self.client.post(url, {'settings_section': 'featured'})
        self.assertFalse(Product.objects.filter(is_featured=True).exists())

    def test_delivery_save_preserves_featured_selection(self):
        manager = get_user_model().objects.create_user('settings-manager', is_staff=True)
        self.client.force_login(manager)
        product = Product.objects.create(name='Izdvojeni', price=10, is_featured=True)
        self.client.post(reverse('panel_section', args=['podesavanje']), {'delivery_price': '5', 'free_delivery_threshold': '205'})
        product.refresh_from_db()
        self.assertTrue(product.is_featured)


class BrandListingTests(TestCase):
    def test_clickable_brands_and_filtered_search(self):
        brand = Brand.objects.create(name='Test brend')
        other = Brand.objects.create(name='Drugi brend')
        matching = Product.objects.create(name='Stap test', price=10, brand=brand)
        Product.objects.create(name='Stap drugi', price=10, brand=other)
        Product.objects.create(name='Stap skriven', price=10, brand=brand, is_active=False)
        self.assertContains(self.client.get(reverse('home')), f'href="{brand.get_absolute_url()}"')
        response = self.client.get(brand.get_absolute_url())
        self.assertEqual(list(response.context['products']), [matching])
        self.assertContains(response, '<h1>Test brend</h1>', html=True)
        self.assertNotContains(response, 'id="izdvojeno"')
        self.assertContains(response, f'action="{brand.get_absolute_url()}"')
        self.assertEqual(list(self.client.get(brand.get_absolute_url(), {'q': 'nema'}).context['products']), [])
        suggestions = self.client.get(reverse('search_suggestions'), {'q': 'Stap', 'brand': brand.pk}).json()
        self.assertEqual([p['name'] for p in suggestions['products']], [matching.name])
        self.assertEqual(self.client.get(reverse('brand', args=[99999])).status_code, 404)


class CustomerAccountTests(TestCase):
    def test_registration_profile_and_logout(self):
        self.assertRedirects(self.client.get(reverse('account')), '/nalog/prijava/?next=/nalog/')
        response = self.client.post(reverse('customer_register'), {
            'username': 'kupac', 'phone': '061123456', 'email': 'kupac@example.com', 'first_name': 'Test',
            'last_name': 'Kupac', 'password1': 'Kupac-Strong-582!', 'password2': 'Kupac-Strong-582!',
        })
        self.assertRedirects(response, reverse('account'))
        user = get_user_model().objects.get(email='kupac@example.com')
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)
        self.client.post(reverse('account'), {'phone': '061123456', 'first_name': 'Novo', 'last_name': 'Ime', 'email': 'novo@example.com', 'is_staff': 'true'})
        user.refresh_from_db()
        self.assertEqual(user.first_name, 'Novo')
        self.assertFalse(user.is_staff)
        self.assertEqual(self.client.get(reverse('customer_logout')).status_code, 405)
        self.assertRedirects(self.client.post(reverse('customer_logout')), reverse('customer_login'))
        self.assertRedirects(self.client.post(reverse('customer_login'), {'username': 'novo@example.com', 'password': 'Kupac-Strong-582!'}), reverse('account'))

    def test_login_rejects_wrong_password_and_post_requires_csrf(self):
        from django.test import Client
        user = get_user_model().objects.create_user('customer-auth', password='test-password')
        response = self.client.post(reverse('customer_login'), {'username': user.username, 'password': 'wrong'})
        self.assertTrue(response.context['form'].errors)
        self.assertNotIn('_auth_user_id', self.client.session)
        self.assertEqual(Client(enforce_csrf_checks=True).post(reverse('customer_login'), {}).status_code, 403)


class AccountOrdersTests(TestCase):
    def test_orders_are_private_and_data_has_its_own_tab(self):
        from .models import Order
        customer = get_user_model().objects.create_user('order-owner')
        other = get_user_model().objects.create_user('other-owner')
        own_order = Order.objects.create(customer=customer, total=25)
        Order.objects.create(customer=other, total=99)
        Order.objects.create(email=customer.email, total=50)
        self.client.force_login(customer)
        response = self.client.get(reverse('account'))
        self.assertEqual(list(response.context['orders']), [own_order])
        self.assertContains(response, 'Moje narudžbe')
        self.assertNotContains(response, 'class="account-form"')
        response = self.client.get(reverse('account'), {'tab': 'podaci'})
        self.assertContains(response, 'Lični podaci')
        self.assertContains(response, 'class="account-form"')

    def test_checkout_attaches_authenticated_customer(self):
        from .models import Order
        customer = get_user_model().objects.create_user('checkout-owner')
        self.client.force_login(customer)
        product = Product.objects.create(name='Artikal', price=10, stock=1)
        self.client.post(reverse('cart_update', args=[product.pk]), {'quantity': 1})
        self.client.post(reverse('checkout'), {'customer_name': 'Kupac', 'email': 'kupac@example.com', 'phone': '061123456', 'address': 'Ulica 1', 'city': 'Sarajevo', 'postal_code': '71000'})
        self.assertEqual(Order.objects.get().customer, customer)


class PanelUsersTests(TestCase):
    def test_all_registered_users_visible_only_to_managers(self):
        users = get_user_model()
        manager = users.objects.create_user('users-manager', is_staff=True)
        customer = users.objects.create_user('registered-customer', email='customer@example.com', first_name='Test', last_name='Kupac')
        inactive = users.objects.create_user('inactive-customer', is_active=False)
        url = reverse('panel_section', args=['korisnici'])
        self.assertEqual(self.client.get(url).status_code, 302)
        self.client.force_login(customer)
        self.assertEqual(self.client.get(url).status_code, 302)
        self.client.force_login(manager)
        response = self.client.get(url)
        self.assertEqual(set(response.context['rows']), {manager, customer, inactive})
        self.assertContains(response, 'customer@example.com')
        self.assertContains(response, 'Test Kupac')
        self.assertContains(response, 'Neaktivan')
        self.assertNotContains(response, 'Dodaj</a>')


class WishlistPageTests(TestCase):
    def test_saved_products_only_and_purchase_controls(self):
        product = Product.objects.create(name='Sacuvani artikal', price=10)
        hidden = Product.objects.create(name='Skriven artikal', price=10, is_active=False)
        Product.objects.create(name='Drugi artikal', price=10)
        response = self.client.get(reverse('wishlist'), {'ids': f'{product.pk},{hidden.pk},abc,999999'})
        self.assertEqual(list(response.context['wishlist_products']), [product])
        self.assertContains(response, product.name)
        self.assertNotContains(response, hidden.name)
        self.assertContains(response, 'data-cart-add=')
        self.assertContains(response, 'data-wishlist-id=')

    def test_empty_and_invalid_ids(self):
        response = self.client.get(reverse('wishlist'), {'ids': 'abc,-1,99999999999999999999999999999'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(response.context['wishlist_products']), [])
        self.assertContains(response, 'Lista želja je prazna')


class LoyaltyTests(TestCase):
    def test_auto_card_and_completed_spending_discount_checkout(self):
        from .models import LoyaltyCard, LoyaltyTier, Order
        from .loyalty import loyalty_status
        user = get_user_model().objects.create_user('loyalty-customer')
        self.assertEqual(LoyaltyCard.objects.filter(customer=user).count(), 1)
        LoyaltyTier.objects.create(minimum=0, maximum=100, percent=2)
        LoyaltyTier.objects.create(minimum=100, maximum=None, percent=10)
        Order.objects.create(customer=user, status='draft', total=1000)
        Order.objects.create(customer=user, status='cancelled', total=1000)
        Order.objects.create(customer=user, status='completed', total=108, delivery_price=8)
        self.assertEqual(loyalty_status(user), (100, 10))
        self.client.force_login(user)
        product = Product.objects.create(name='Loyalty artikal', price=50, stock=1)
        self.client.post(reverse('cart_update', args=[product.pk]), {'quantity': 1})
        response = self.client.get(reverse('cart'))
        self.assertEqual(response.context['loyalty_discount'], 0)
        self.assertEqual(response.context['grand_total'], 50)
        self.client.post(reverse('cart_loyalty'), {'action': 'apply'})
        response = self.client.get(reverse('cart'))
        self.assertEqual(response.context['loyalty_discount'], 5)
        self.assertEqual(response.context['grand_total'], 45)
        self.client.post(reverse('checkout'), {'customer_name': 'Kupac', 'email': 'kupac@example.com', 'phone': '061123456', 'address': 'Ulica 1', 'city': 'Sarajevo', 'postal_code': '71000'})
        order = Order.objects.order_by('-pk').first()
        self.assertEqual(order.loyalty_percent, 10)
        self.assertEqual(order.loyalty_discount, 5)
        self.assertEqual(order.total, 45)
        self.assertContains(self.client.get(reverse('account')), str(user.loyalty_card.number))

    def test_panel_ranges_validate_and_access_is_restricted(self):
        from .forms import LoyaltySettingsFormSet
        from .models import LoyaltyTier
        data = {'form-TOTAL_FORMS': '2', 'form-INITIAL_FORMS': '0', 'form-0-minimum': '0', 'form-0-maximum': '100', 'form-0-percent': '5', 'form-1-minimum': '50', 'form-1-maximum': '', 'form-1-percent': '10'}
        self.assertFalse(LoyaltySettingsFormSet(data).is_valid())
        data['form-1-minimum'] = '100'
        self.assertTrue(LoyaltySettingsFormSet(data).is_valid())
        url = reverse('panel_section', args=['loyalty'])
        self.assertEqual(self.client.post(url, data).status_code, 302)
        self.assertFalse(LoyaltyTier.objects.exists())
        manager = get_user_model().objects.create_user('loyalty-manager', is_staff=True)
        self.client.force_login(manager)
        self.assertContains(self.client.get(url, {'tab': 'settings'}), 'Loyalty popusti')
        self.assertRedirects(self.client.post(url, data), url + '?tab=settings')
        self.assertEqual(LoyaltyTier.objects.count(), 2)
        data['form-0-percent'] = '101'
        self.assertFalse(LoyaltySettingsFormSet(data).is_valid())


class PlacedOrderLoyaltyTests(TestCase):
    def test_placed_orders_earn_discount_and_cancellation_removes_it(self):
        from .models import Order, LoyaltyTier
        from .loyalty import loyalty_status
        customer = get_user_model().objects.create_user('placed-loyalty')
        LoyaltyTier.objects.create(minimum=901, maximum=None, percent=10)
        order = Order.objects.create(customer=customer, status='placed', total=1999)
        self.assertEqual(loyalty_status(customer), (1999, 10))
        order.status = 'completed'
        order.save()
        self.assertEqual(loyalty_status(customer), (1999, 10))
        order.status = 'cancelled'
        order.save()
        self.assertEqual(loyalty_status(customer), (0, 0))


class ManualLoyaltyTests(TestCase):
    def test_apply_remove_and_checkout_without_card(self):
        from .models import LoyaltyTier, Order
        user = get_user_model().objects.create_user('manual-loyalty')
        LoyaltyTier.objects.create(minimum=0, percent=10)
        self.client.force_login(user)
        product = Product.objects.create(name='Artikal', price=100, stock=1)
        self.client.post(reverse('cart_update', args=[product.pk]), {'quantity': 1})
        self.assertEqual(self.client.get(reverse('checkout')).context['loyalty_discount'], 0)
        self.client.post(reverse('cart_loyalty'), {'action': 'apply'})
        self.assertEqual(self.client.get(reverse('checkout')).context['loyalty_discount'], 10)
        self.client.post(reverse('cart_loyalty'), {'action': 'remove'})
        self.assertEqual(self.client.get(reverse('checkout')).context['loyalty_discount'], 0)
        self.client.post(reverse('checkout'), {'customer_name': 'Kupac', 'email': 'kupac@example.com', 'phone': '061123456', 'address': 'Ulica 1', 'city': 'Sarajevo', 'postal_code': '71000'})
        self.assertEqual(Order.objects.get().total, 100)
        self.assertEqual(Order.objects.get().loyalty_discount, 0)


from django.test import override_settings


@override_settings(ADMIN_SSO_KEY='test-server-only-key', ADMIN_SSO_USERNAME='sso-owner')
class AdminSSOTests(TestCase):
    def setUp(self):
        from django.test import Client
        self.owner = get_user_model().objects.create_superuser('sso-owner', password='test-pass')
        self.client = Client(enforce_csrf_checks=True)
        self.url = reverse('admin_sso_login')

    def token(self, key='test-server-only-key'):
        import secrets
        from django.core import signing
        return signing.dumps({'aud': 'bera-shop-admin', 'nonce': secrets.token_urlsafe(32)}, key=key, salt='bera-admin-sso-v1')

    def test_login_and_replay_protection(self):
        token = self.token()
        self.assertRedirects(self.client.post(self.url, {'token': token}), reverse('admin:index'))
        self.assertEqual(int(self.client.session['_auth_user_id']), self.owner.pk)
        self.assertEqual(self.client.post(self.url, {'token': token}).status_code, 403)

    def test_reject_invalid_expired_and_non_superuser(self):
        from unittest.mock import patch
        import time
        self.assertEqual(self.client.get(self.url).status_code, 405)
        for token in ['', 'bad', self.token(key='wrong-key')]:
            self.assertEqual(self.client.post(self.url, {'token': token}).status_code, 403)
        with patch('django.core.signing.time.time', return_value=time.time() - 120):
            expired = self.token()
        self.assertEqual(self.client.post(self.url, {'token': expired}).status_code, 403)
        self.owner.is_superuser = False
        self.owner.save()
        self.assertEqual(self.client.post(self.url, {'token': self.token()}).status_code, 403)
        self.assertNotIn('_auth_user_id', self.client.session)

    @override_settings(ADMIN_SSO_KEY='')
    def test_missing_key_disables_access(self):
        self.assertEqual(self.client.post(self.url, {'token': self.token()}).status_code, 403)


@override_settings(MODULE_CONTROL_KEY='module-test-key')
class ModuleControlTests(TestCase):
    def send(self, enabled, revision, paid_until=None, key='module-test-key'):
        from django.core import signing
        from django.test import Client
        token = signing.dumps({'aud': 'bera-module-control', 'module': 'loyalty', 'enabled': enabled, 'revision': revision, 'paid_until': paid_until}, key=key, salt='bera-module-control-v1')
        return Client(enforce_csrf_checks=True).post(reverse('module_loyalty'), {'token': token})

    def test_signed_api_ordering_retries_and_expiry(self):
        from .modules import loyalty_enabled
        from django.utils import timezone
        from datetime import timedelta
        self.assertEqual(self.send(False, 1, key='bad').status_code, 403)
        self.assertEqual(self.send(False, 1).status_code, 200)
        self.assertFalse(loyalty_enabled())
        self.assertEqual(self.send(False, 1).status_code, 200)
        self.assertEqual(self.send(True, 1).status_code, 409)
        self.assertFalse(loyalty_enabled())
        self.assertEqual(self.send(True, 2).status_code, 200)
        self.assertTrue(loyalty_enabled())
        self.assertEqual(self.send(False, 1).status_code, 409)
        self.assertEqual(self.send(True, 3, (timezone.now() - timedelta(seconds=1)).isoformat()).status_code, 200)
        self.assertFalse(loyalty_enabled())
        self.assertEqual(self.send(True, 4, 'invalid').status_code, 400)
        self.assertEqual(self.client.get(reverse('module_loyalty')).status_code, 405)

    def test_disabled_module_hides_ui_and_prevents_checkout_discount(self):
        from .models import LoyaltyTier, LoyaltyCard, Order
        user = get_user_model().objects.create_user('module-customer', is_staff=True)
        LoyaltyTier.objects.create(minimum=0, percent=10)
        self.client.force_login(user)
        product = Product.objects.create(name='Artikal', price=100, stock=1)
        self.client.post(reverse('cart_update', args=[product.pk]), {'quantity': 1})
        self.client.post(reverse('cart_loyalty'), {'action': 'apply'})
        self.assertEqual(self.client.get(reverse('cart')).context['loyalty_discount'], 10)
        self.send(False, 1)
        response = self.client.get(reverse('cart'))
        self.assertEqual(response.context['loyalty_discount'], 0)
        self.assertNotContains(response, 'Primijeni loyalty karticu')
        self.assertNotContains(response, 'Kartica je primijenjena')
        self.assertNotContains(self.client.get(reverse('account')), 'BERA LOYALTY')
        self.assertNotContains(self.client.get(reverse('panel')), '/panel/loyalty/')
        self.assertEqual(self.client.get(reverse('panel_section', args=['loyalty'])).status_code, 404)
        self.client.post(reverse('checkout'), {'customer_name': 'Kupac', 'email': 'kupac@example.com', 'phone': '061123456', 'address': 'Ulica 1', 'city': 'Sarajevo', 'postal_code': '71000'})
        self.assertEqual(Order.objects.get().loyalty_discount, 0)
        self.assertEqual(Order.objects.get().total, 100)
        self.assertTrue(LoyaltyCard.objects.filter(customer=user).exists())
        self.send(True, 2)
        self.assertContains(self.client.get(reverse('account')), 'BERA LOYALTY')
