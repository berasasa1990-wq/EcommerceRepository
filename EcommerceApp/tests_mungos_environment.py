"""Environment selection and production writes, with mocked HTTP only."""
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings

from .models import Category, Product, MungosProductMapping
from .mungos_client import MungosClient, MungosError

UUID = 'a9241b59-9e45-4840-9730-c93cb8ad9517'


@override_settings(MUNGOS_ENABLED=True, MUNGOS_ENVIRONMENT='production',
    MUNGOS_BASE_URL='https://mungos.ba/api/v1/connector',
    MUNGOS_API_KEY='production-key', MUNGOS_ECOMMERCE_ACCESS_CODE='production-access', SITE_URL='https://example.com')
class MungosEnvironmentTests(TestCase):
    def setUp(self):
        self.product = Product.objects.create(naziv='Reel', sifra='env-test', cijena=20,
            akcijska_cijena=15, opis='Opis', stanje=9, aktivan=True, na_stanju=True,
            kategorija=Category.objects.create(naziv='Mašinice'))
        MungosProductMapping.objects.create(product=self.product, mungos_uuid=UUID,
                                           last_sync_status='REGISTERED')
        http = patch('EcommerceApp.mungos_client.requests.Session')
        self.factory = http.start()
        self.addCleanup(http.stop)
        self.session = self.factory.return_value.__enter__.return_value
        response = self.session.put.return_value.__enter__.return_value
        response.status_code = 200
        response.iter_content.return_value = [b'{}']
        sleep = patch('EcommerceApp.management.commands.mungos_bulk_sync.time.sleep')
        sleep.start()
        self.addCleanup(sleep.stop)

    def run_command(self, command, **options):
        output = StringIO()
        call_command(command, stdout=output, **options)
        return output.getvalue()

    def test_production_sale_dry_run_zero_http_and_zero_database_writes(self):
        before = list(Product.objects.values())
        mappings = list(MungosProductMapping.objects.values())
        output = self.run_command('mungos_sale_sync', limit=5)
        self.assertIn('DRY RUN', output)
        self.factory.assert_not_called()
        self.assertEqual(list(Product.objects.values()), before)
        self.assertEqual(list(MungosProductMapping.objects.values()), mappings)

    def test_all_production_bulk_and_partial_dry_runs_make_zero_http_requests(self):
        before = list(MungosProductMapping.objects.values())
        for command in ('mungos_bulk_sync', 'mungos_price_sync', 'mungos_quantity_sync'):
            self.assertIn('DRY RUN', self.run_command(command, limit=5))
        for command in ('mungos_product_send', 'mungos_product_update'):
            args = [self.product.pk] + ([UUID] if command.endswith('update') else [])
            output = StringIO()
            call_command(command, *args, stdout=output)
            self.assertIn('DRY RUN', output.getvalue())
        self.factory.assert_not_called()
        self.assertEqual(list(MungosProductMapping.objects.values()), before)

    def test_environment_settings_default_and_fixed_endpoints(self):
        import ast
        from pathlib import Path
        source = Path(__file__).resolve().parent.parent / 'EcommerceProject' / 'settings.py'
        tree = ast.parse(source.read_text())
        assignments = [node for node in tree.body if isinstance(node, ast.Assign)
                       and any(isinstance(target, ast.Name) and target.id.startswith('MUNGOS_')
                               for target in node.targets)]
        module = ast.Module(body=assignments, type_ignores=[])
        for environment, url in ((None, 'https://staging.mungos.ba/api/v1/connector'),
                                 ('production', 'https://mungos.ba/api/v1/connector')):
            values = {}
            if environment:
                values['MUNGOS_ENVIRONMENT'] = environment
                values['MUNGOS_BASE_URL'] = url
            namespace = {'_mungos_env': lambda key, default='': values.get(key, default)}
            exec(compile(module, str(source), 'exec'), namespace)
            self.assertEqual(namespace['MUNGOS_ENVIRONMENT'], environment or 'staging')
            self.assertEqual(namespace['MUNGOS_BASE_URL'], url)

    def test_confirmed_sale_production_endpoint_auth_and_price_pair(self):
        before = list(Product.objects.values())
        output = self.run_command('mungos_sale_sync', limit=5, confirm=True)
        self.assertIn('CONFIRMED PRODUCTION', output)
        self.session.put.assert_called_once()
        request = self.session.put.call_args
        self.assertEqual(request.args[0], 'https://mungos.ba/api/v1/connector/standard/product/' + UUID)
        self.assertEqual(request.kwargs['headers'], {'X-Api-Key': 'production-key',
                                                   'ecommerceaccesscode': 'production-access'})
        self.assertEqual(request.kwargs['json']['ProductPrice']['Price'], 20.0)
        self.assertEqual(request.kwargs['json']['ProductPrice']['SellingPrice'], 15.0)
        self.session.post.assert_not_called()
        self.assertEqual(list(Product.objects.values()), before)
        for secret in ('production-key', 'production-access', 'staging-key', 'staging-access'):
            self.assertNotIn(secret, output)

    def test_staging_endpoint_and_existing_auth(self):
        with override_settings(MUNGOS_ENVIRONMENT='staging',
                MUNGOS_BASE_URL='https://staging.mungos.ba/api/v1/connector',
                MUNGOS_API_KEY='staging-key', MUNGOS_ECOMMERCE_ACCESS_CODE='staging-access'):
            self.run_command('mungos_sale_sync', limit=5, confirm=True)
        request = self.session.put.call_args
        self.assertEqual(request.args[0], 'https://staging.mungos.ba/api/v1/connector/standard/product/' + UUID)
        self.assertEqual(request.kwargs['headers'], {'X-Api-Key': 'staging-key',
                                                   'ecommerceaccesscode': 'staging-access'})

    def test_missing_production_credentials_never_write_or_claim_mapping(self):
        before = list(MungosProductMapping.objects.values())
        for missing in ('MUNGOS_API_KEY', 'MUNGOS_ECOMMERCE_ACCESS_CODE'):
            with override_settings(**{missing: ''}):
                for command in ('mungos_sale_sync', 'mungos_bulk_sync', 'mungos_price_sync', 'mungos_quantity_sync'):
                    with self.assertRaises(CommandError):
                        self.run_command(command, limit=5, confirm=True)
        self.factory.assert_not_called()
        self.assertEqual(list(MungosProductMapping.objects.values()), before)

    def test_production_normal_and_sale_price_commands_keep_both_prices(self):
        for sale in (15, None):
            Product.objects.filter(pk=self.product.pk).update(akcijska_cijena=sale)
            self.factory.reset_mock()
            self.run_command('mungos_price_sync', product_id=self.product.pk)
            self.factory.assert_not_called()
            self.run_command('mungos_price_sync', product_id=self.product.pk, confirm=True)
            request = self.session.put.call_args
            self.assertEqual(request.args[0], 'https://mungos.ba/api/v1/connector/standard/product/' + UUID)
            self.assertEqual(request.kwargs['json']['ProductPrice']['Price'], 20.0)
            self.assertEqual(request.kwargs['json']['ProductPrice']['SellingPrice'], 15.0 if sale else 20.0)

    def test_invalid_environment_or_mismatched_endpoint_fail_closed(self):
        for config in ({'MUNGOS_ENVIRONMENT': 'invalid'},
                       {'MUNGOS_BASE_URL': 'https://staging.mungos.ba/api/v1/connector'},
                       {'MUNGOS_BASE_URL': 'https://evil.example/api/v1/connector'},
                       {'MUNGOS_BASE_URL': ''},
                       {'MUNGOS_BASE_URL': 'https://mungos.ba/api/v1/connector/'}):
            with override_settings(**config), self.assertRaises(CommandError):
                self.run_command('mungos_sale_sync', confirm=True, limit=5)
        self.factory.assert_not_called()

    def test_invalid_production_credentials_fail_closed(self):
        for name in ('MUNGOS_API_KEY', 'MUNGOS_ECOMMERCE_ACCESS_CODE'):
            for value in (' bad', 'bad\r\nheader', 'badč'):
                with override_settings(**{name: value}), self.assertRaises(MungosError):
                    MungosClient()
        self.factory.assert_not_called()
