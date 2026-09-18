import json
from tempfile import TemporaryDirectory

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.db import connection, transaction
from django.test import TestCase, override_settings
from django.urls import reverse

from .cart_tracking import cleanup_stale_active_cart_items
from .live_visitors import cleanup_stale_live_visitors
from .models import Product, SavedFormInput, SystemDataRevision, PreservedMediaFile, SiteSettings
from .storage_backends import RetainingFileSystemStorage


@override_settings(STORAGES={'default': {'BACKEND': 'django.core.files.storage.InMemoryStorage'}, 'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class PermanentHistoryTests(TestCase):
    def test_bulk_update_and_delete_preserve_previous_values(self):
        product = Product.objects.create(naziv='Original', cijena=10)
        Product.objects.filter(pk=product.pk).update(naziv='Izmijenjen')
        table = Product._meta.db_table
        revision = SystemDataRevision.objects.filter(table_name=table, operation='UPDATE').first()
        self.assertEqual(revision.before['naziv'], 'Original')
        self.assertEqual(revision.after['naziv'], 'Izmijenjen')
        # Direct SQL bypasses all Django signals.
        with connection.cursor() as cursor:
            cursor.execute(f'DELETE FROM {connection.ops.quote_name(table)} WHERE id = %s', [product.pk])
        deleted = SystemDataRevision.objects.filter(table_name=table, operation='DELETE').first()
        self.assertEqual(deleted.before['naziv'], 'Izmijenjen')
        self.assertIsNone(deleted.after)

    def test_rollback_does_not_record_uncommitted_change(self):
        product = Product.objects.create(naziv='Original', cijena=10)
        before = SystemDataRevision.objects.count()
        try:
            with transaction.atomic():
                Product.objects.filter(pk=product.pk).update(naziv='Rollback')
                raise ValueError
        except ValueError:
            pass
        self.assertEqual(SystemDataRevision.objects.count(), before)
        product.refresh_from_db()
        self.assertEqual(product.naziv, 'Original')

    def test_wide_settings_table_keeps_all_fields_including_nulls(self):
        row, _ = SiteSettings.objects.get_or_create(pk=1)
        SiteSettings.objects.filter(pk=row.pk).update(kontakt_telefon='Retention test')
        revision = SystemDataRevision.objects.filter(table_name=SiteSettings._meta.db_table, record_key=str(row.pk)).first()
        self.assertIsNotNone(revision)
        self.assertEqual(set(revision.after), {field.column for field in SiteSettings._meta.local_fields})

    def test_age_cleanup_is_disabled(self):
        with self.assertNumQueries(0):
            self.assertEqual(cleanup_stale_active_cart_items(), 0)
            self.assertEqual(cleanup_stale_live_visitors(), 0)

    def test_unsaved_input_retries_are_idempotent_and_exclude_secrets(self):
        owner = get_user_model().objects.create_superuser('history-owner', '', 'password')
        self.client.force_login(owner)
        payload = {'event_id': 'event-123', 'path': '/nalog/', 'form_key': 'edit', 'payload': [
            {'name': 'naziv', 'value': 'Nedovršen unos', 'type': 'text'},
            {'name': 'password', 'value': 'secret', 'type': 'password'},
            {'name': 'csrfmiddlewaretoken', 'value': 'secret', 'type': 'hidden'},
        ]}
        for _ in range(2):
            response = self.client.post(reverse('staff_magacin_save_input'), json.dumps(payload), content_type='application/json')
            self.assertEqual(response.status_code, 200)
        self.assertEqual(SavedFormInput.objects.count(), 1)
        self.assertEqual(SavedFormInput.objects.get().payload, [payload['payload'][0]])
        self.assertEqual(self.client.get(reverse('staff_magacin_data_history')).status_code, 200)
        self.assertEqual(self.client.get(reverse('staff_magacin_data_history'), {'unos': '1'}).status_code, 200)

    def test_input_batch_preserves_each_version_and_retries(self):
        owner = get_user_model().objects.create_superuser('batch-owner', '', 'password')
        self.client.force_login(owner)
        events = [{'event_id': f'batch-{index}', 'path': '/nalog/', 'payload': [
            {'name': 'naziv', 'type': 'text', 'value': str(index)}]} for index in range(50)]
        for _ in range(2):
            response = self.client.post(reverse('staff_magacin_save_input'),
                json.dumps({'events': events}), content_type='application/json')
            self.assertEqual(response.status_code, 200)
        self.assertEqual(SavedFormInput.objects.count(), 50)
        self.assertEqual({row.payload[0]['value'] for row in SavedFormInput.objects.all()},
                         {str(index) for index in range(50)})

    def test_invalid_batch_does_not_acknowledge_or_save_partial_input(self):
        owner = get_user_model().objects.create_superuser('invalid-owner', '', 'password')
        self.client.force_login(owner)
        valid = {'event_id': 'valid-event', 'path': '/nalog/', 'payload': []}
        for invalid, status in [({'event_id': '!'}, 400), (dict(valid, owner='someone-else'), 403)]:
            response = self.client.post(reverse('staff_magacin_save_input'),
                json.dumps({'events': [valid, invalid]}), content_type='application/json')
            self.assertEqual(response.status_code, status)
            self.assertFalse(SavedFormInput.objects.exists())

    def test_non_owner_cannot_access_history(self):
        user = get_user_model().objects.create_user('customer', password='password')
        self.client.force_login(user)
        self.assertEqual(self.client.get(reverse('staff_magacin_data_history')).status_code, 302)

    def test_deleted_media_content_remains_in_database(self):
        with TemporaryDirectory() as folder:
            storage = RetainingFileSystemStorage(location=folder)
            name = storage.save('original.txt', ContentFile(b'original data'))
            storage.delete(name)
        self.assertEqual(bytes(PreservedMediaFile.objects.get(name=name).content), b'original data')

    def test_file_deletion_waits_for_archive_commit(self):
        with TemporaryDirectory() as folder:
            storage = RetainingFileSystemStorage(location=folder)
            name = storage.save('keep.txt', ContentFile(b'keep me'))
            try:
                with transaction.atomic():
                    storage.delete(name)
                    raise ValueError('rollback')
            except ValueError:
                pass
            self.assertTrue(storage.exists(name))
            with self.captureOnCommitCallbacks(execute=True):
                storage.delete(name)
            self.assertFalse(storage.exists(name))
            self.assertEqual(bytes(PreservedMediaFile.objects.get(name=name).content), b'keep me')
