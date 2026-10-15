from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth.models import AnonymousUser
from django.test import SimpleTestCase, RequestFactory, override_settings
from django.urls import reverse

from .warehouse_import_check import parse_excel_codes, missing_name_codes, MAX_ROWS
from . import views_magacin


class ExcelCodeCheckTests(SimpleTestCase):
    def test_excel_header_empty_rows_case_duplicates_and_leading_zeroes(self):
        rows = '\ufeffŠifra\tKoličina\tFakturna\r\n0012\t3\t1,50\r\n\r\nMT42\t2\t1\r\nmt42\t2\t1'
        self.assertEqual(parse_excel_codes(rows), ['0012', 'MT42'])

    def test_excel_quoted_cells_and_empty_trailing_columns(self):
        self.assertEqual(parse_excel_codes('ABC\t"Opis\tsa tabom"\t'), ['ABC'])

    def test_missing_codes_match_only_names_and_keep_order(self):
        codes = ['0012', 'MT42', 'AB', 'ABC', 'A+B', 'XZ']
        self.assertEqual(missing_name_codes(codes, iter(['Štap mt42 crni', 'ABC model', 'Model A+B'])), ['0012', 'XZ'])

    def test_unicode_casefold(self):
        self.assertEqual(missing_name_codes(['Č42'], ['Proizvod č42']), [])

    def test_malformed_rows_fail_instead_of_reporting_false_missing_codes(self):
        for pasted in ['', 'A\tB', 'A\tB\tC\tD', '\tB\tC', 'Šifra\tB\tC']:
            with self.subTest(pasted=pasted), self.assertRaises(ValueError):
                parse_excel_codes(pasted)

    def test_row_limit_with_and_without_header(self):
        rows = '\n'.join(f'{i}\tB\tC' for i in range(MAX_ROWS))
        self.assertEqual(len(parse_excel_codes(rows)), MAX_ROWS)
        self.assertEqual(len(parse_excel_codes('Šifra\tB\tC\n' + rows)), MAX_ROWS)
        with self.assertRaises(ValueError):
            parse_excel_codes(rows + '\nEXTRA\tB\tC')

    @override_settings(ALLOWED_HOSTS=['testserver'])
    def test_route_access_and_read_only_all_names_query(self):
        url = reverse('staff_magacin_novi_artikli_uvoz')
        request = RequestFactory().post(url, {'excel_rows': 'ABC\t1\t2\nNEW\t1\t2'})
        request.user = SimpleNamespace(is_authenticated=True, is_active=True, is_superuser=True)
        with patch.object(views_magacin, 'Product') as product, patch.object(views_magacin, '_magacin_context', return_value={}), patch.object(views_magacin, 'render', side_effect=lambda request, template, context: context):
            product.objects.values_list.return_value.iterator.return_value = iter(['Artikal ABC'])
            result = views_magacin.magacin_novi_artikli_uvoz(request)
            self.assertEqual(result['missing_codes'], ['NEW'])
            self.assertEqual(result['missing_codes_text'], 'NEW')
            product.objects.values_list.assert_called_once_with('naziv', flat=True)
            product.objects.filter.assert_not_called()
            product.objects.create.assert_not_called()
            product.objects.update.assert_not_called()
        request.user = AnonymousUser()
        self.assertEqual(views_magacin.magacin_novi_artikli_uvoz(request).status_code, 302)
        request.user = SimpleNamespace(is_authenticated=True, is_active=True, is_superuser=False)
        self.assertEqual(views_magacin.magacin_novi_artikli_uvoz(request).status_code, 302)

    def test_invalid_paste_does_not_query_products(self):
        request = RequestFactory().post(reverse('staff_magacin_novi_artikli_uvoz'), {'excel_rows': 'ABC'})
        request.user = SimpleNamespace(is_authenticated=True, is_active=True, is_superuser=True)
        with patch.object(views_magacin, 'Product') as product, patch.object(views_magacin, '_magacin_context', return_value={}), patch.object(views_magacin, 'render', side_effect=lambda request, template, context: context):
            result = views_magacin.magacin_novi_artikli_uvoz(request)
            self.assertIsNone(result['missing_codes'])
            self.assertTrue(result['import_check_error'])
            product.objects.values_list.assert_not_called()


class BulkArticlePreparationTests(SimpleTestCase):
    def test_excel_names_keep_first_column_and_remove_duplicates(self):
        from .warehouse_import_check import parse_bulk_names
        self.assertEqual(parse_bulk_names('Naziv\tKoličina\nFOX jakna\t2\nMATE štap\t3\nfox jakna\t1'), ['FOX jakna', 'MATE štap'])

    def test_brand_matching_respects_word_boundaries_and_longer_brand(self):
        from .warehouse_import_check import detect_brand
        brands = [{'id': 1, 'naziv': 'FOX'}, {'id': 2, 'naziv': 'FOX RAGE'}, {'id': 3, 'naziv': 'MATE'}]
        self.assertEqual(detect_brand('MT13704 mate ITANA', brands), 3)
        self.assertEqual(detect_brand('FOX RAGE varalica', brands), 2)
        self.assertIsNone(detect_brand('Ultimate varalica', brands))
        self.assertIsNone(detect_brand('FOX MATE kombinacija', brands))

    def test_invalid_names_are_rejected(self):
        from .warehouse_import_check import parse_bulk_names
        for value in ['', 'A' * 201, '\n'.join(str(i) for i in range(401))]:
            with self.subTest(value=value[:20]), self.assertRaises(ValueError):
                parse_bulk_names(value)

    def test_bulk_preview_does_not_save(self):
        from . import views_magacin
        request = RequestFactory().post(reverse('staff_magacin_artikli_bulk'), {'action': 'preview', 'names_text': 'FOX jakna'}, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        request.user = SimpleNamespace(is_authenticated=True, is_active=True, is_superuser=True)
        with patch.object(views_magacin, 'Brand') as brand, patch('EcommerceApp.warehouse_import_check.save_bulk_products') as save:
            brand.objects.order_by.return_value.values.return_value = [{'id': 1, 'naziv': 'FOX'}]
            response = views_magacin.magacin_artikli_bulk(request)
            import json
            self.assertEqual(json.loads(response.content)['rows'], [{'naziv': 'FOX jakna', 'brand_id': 1}])
            save.assert_not_called()


class BulkDescriptionTests(SimpleTestCase):
    def test_description_preserves_later_hyphens(self):
        from .warehouse_import_check import parse_bulk_descriptions
        self.assertEqual(parse_bulk_descriptions('K0340051 - Mašinica - veličina 5000.\nA0430110 - Torbica 24 × 16,5 × 9 cm.'), [('K0340051', 'Mašinica - veličina 5000.'), ('A0430110', 'Torbica 24 × 16,5 × 9 cm.')])

    def test_invalid_and_duplicate_codes_rejected(self):
        from .warehouse_import_check import parse_bulk_descriptions
        for pasted in ['', 'ABC bez crtice', 'ABC - ', 'ABC - Opis\nabc - Drugi', '\n'.join(f'{i} - opis' for i in range(401))]:
            with self.subTest(pasted=pasted[:30]), self.assertRaises(ValueError):
                parse_bulk_descriptions(pasted)

    def test_code_matches_field_or_complete_name_token_without_partial_matches(self):
        from .warehouse_import_check import match_description_product
        products = [SimpleNamespace(pk=1, sifra='123', naziv='K0340051 KORUM mašinica'), SimpleNamespace(pk=2, sifra='K0310381', naziv='Držači'), SimpleNamespace(pk=3, sifra='', naziv='K03400510 drugo')]
        self.assertEqual(match_description_product('k0340051', products), products[:1])
        self.assertEqual(match_description_product('K0310381', products), products[1:2])
        self.assertEqual(match_description_product('K034005', products), [])

    def test_missing_code_prevents_any_save(self):
        from .warehouse_import_check import save_bulk_descriptions
        with patch('django.db.transaction.atomic'), patch('EcommerceApp.models.Product') as product:
            product.objects.only.return_value.order_by.return_value = [SimpleNamespace(pk=1, sifra='', naziv='ABC artikal')]
            with self.assertRaisesMessage(ValueError, 'Ništa nije spremljeno'):
                save_bulk_descriptions('ABC - opis\nMISSING - opis')
            product.objects.select_for_update.assert_not_called()

    def test_save_updates_only_description(self):
        from .warehouse_import_check import save_bulk_descriptions
        with patch('django.db.transaction.atomic'), patch('EcommerceApp.models.Product') as product:
            product.objects.only.return_value.order_by.return_value = [SimpleNamespace(pk=1, sifra='', naziv='ABC artikal')]
            saved = product.objects.select_for_update.return_value.get.return_value
            self.assertEqual(save_bulk_descriptions('ABC - Praktičan izbor - 5000.'), 1)
            self.assertEqual(saved.opis, 'Praktičan izbor - 5000.')
            saved.save.assert_called_once_with(update_fields=['opis'])


class BulkDraftWorkflowTests(SimpleTestCase):
    def test_descriptions_match_draft_and_require_every_article(self):
        from .warehouse_import_check import prepare_bulk_descriptions
        rows = [{'naziv': 'K0340051 KORUM mašinica', 'brand_id': '1'}, {'naziv': 'A0430110 PRESTON torbica', 'brand_id': '2'}]
        prepared = prepare_bulk_descriptions(rows, 'A0430110 - Torbica.\nK0340051 - Mašinica.')
        self.assertEqual([row['opis'] for row in prepared], ['Mašinica.', 'Torbica.'])
        self.assertNotIn('opis', rows[0])
        with self.assertRaisesMessage(ValueError, 'Nedostaje opis'):
            prepare_bulk_descriptions(rows, 'K0340051 - Mašinica.')
        with self.assertRaisesMessage(ValueError, 'nije pronađena'):
            prepare_bulk_descriptions(rows, 'UNKNOWN - Opis.')

    def request(self, data):
        request = RequestFactory().post(reverse('staff_magacin_opisi_bulk'), data)
        request.user = SimpleNamespace(is_authenticated=True, is_active=True, is_superuser=True)
        request.session = {'warehouse_bulk_draft': [{'naziv': 'ABC FOX jakna', 'brand_id': '1'}]}
        return request

    def test_save_descriptions_only_prepares_session_then_final_post_creates(self):
        request = self.request({'descriptions_text': 'ABC - Opis jakne.'})
        with patch('EcommerceApp.warehouse_import_check.save_bulk_products') as save:
            response = views_magacin.magacin_opisi_bulk(request)
            self.assertEqual(response.status_code, 302)
            save.assert_not_called()
        ready = request.session['warehouse_bulk_ready']
        final = self.request({'action': 'create', 'draft_token': ready['token']})
        final.session = request.session
        with patch('EcommerceApp.warehouse_import_check.save_bulk_products', return_value=([object()], [])) as save, patch('EcommerceApp.warehouse_import_check.save_bulk_import_history') as history, patch('django.db.transaction.atomic'), patch.object(views_magacin.messages, 'success'):
            response = views_magacin.magacin_opisi_bulk(final)
            self.assertEqual(response.status_code, 302)
            save.assert_called_once_with([{'naziv': 'ABC FOX jakna', 'brand_id': '1', 'opis': 'Opis jakne.'}])
            self.assertNotIn('warehouse_bulk_ready', final.session)
            self.assertNotIn('warehouse_bulk_draft', final.session)

    def test_names_and_brands_step_does_not_create_products(self):
        request = RequestFactory().post(reverse('staff_magacin_artikli_bulk'), {'action': 'save', 'product_name': ['ABC FOX jakna'], 'brand_id': ['1']})
        request.user = SimpleNamespace(is_authenticated=True, is_active=True, is_superuser=True)
        request.session = {}
        with patch.object(views_magacin, 'Brand') as brand, patch('EcommerceApp.warehouse_import_check.save_bulk_products') as save:
            brand.objects.order_by.return_value.values.return_value = [{'id': 1, 'naziv': 'FOX'}]
            response = views_magacin.magacin_artikli_bulk(request)
            self.assertEqual(response.status_code, 302)
            save.assert_not_called()
            self.assertEqual(request.session['warehouse_bulk_draft'], [{'naziv': 'ABC FOX jakna', 'brand_id': '1'}])


class BulkExistingDescriptionTests(SimpleTestCase):
    def test_existing_product_receives_description_without_duplicate_creation(self):
        from .warehouse_import_check import save_bulk_products
        with patch('django.db.transaction.atomic'), patch('EcommerceApp.models.Product') as product, patch('EcommerceApp.models.Brand') as brand:
            brand.objects.values_list.return_value = [1]
            product.objects.order_by.return_value.values_list.return_value.iterator.return_value = iter([(42, 'ABC FOX jakna')])
            saved = product.objects.select_for_update.return_value.get.return_value
            created, existing = save_bulk_products([{'naziv': 'ABC FOX jakna', 'brand_id': '1', 'opis': 'Novi opis.'}])
            self.assertEqual(created, [])
            self.assertEqual(existing, ['ABC FOX jakna'])
            self.assertEqual(saved.opis, 'Novi opis.')
            saved.save.assert_called_once_with(update_fields=['opis'])
            product.objects.create.assert_not_called()

    def test_new_product_is_created_with_description(self):
        from .warehouse_import_check import save_bulk_products
        with patch('django.db.transaction.atomic'), patch('EcommerceApp.models.Product') as product, patch('EcommerceApp.models.Brand') as brand:
            brand.objects.values_list.return_value = [1]
            product.objects.order_by.return_value.values_list.return_value.iterator.return_value = iter([])
            save_bulk_products([{'naziv': 'ABC FOX jakna', 'brand_id': '1', 'opis': 'Opis nove jakne.'}])
            self.assertEqual(product.objects.create.call_args.kwargs['opis'], 'Opis nove jakne.')

    def test_duplicate_existing_names_are_rejected(self):
        from .warehouse_import_check import save_bulk_products
        with patch('django.db.transaction.atomic'), patch('EcommerceApp.models.Product') as product, patch('EcommerceApp.models.Brand') as brand:
            brand.objects.values_list.return_value = [1]
            product.objects.order_by.return_value.values_list.return_value.iterator.return_value = iter([(42, 'ABC FOX jakna'), (43, 'ABC FOX jakna')])
            with self.assertRaisesMessage(ValueError, 'više artikala'):
                save_bulk_products([{'naziv': 'ABC FOX jakna', 'brand_id': '1', 'opis': 'Opis.'}])
            product.objects.select_for_update.assert_not_called()
