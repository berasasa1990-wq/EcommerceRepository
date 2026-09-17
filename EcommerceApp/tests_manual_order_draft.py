import json
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from .magacin import MagacinError
from .models import ManualOrderDraft, ManualOrderDraftRevision, Order, Product


@override_settings(STORAGES={'default': {'BACKEND': 'django.core.files.storage.InMemoryStorage'}, 'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class ManualOrderDraftTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser('draft-owner', '', 'test-password')
        self.client.force_login(self.user)
        self.draft = ManualOrderDraft.objects.create(owner=self.user, token='draft-123')
        self.payload = {'fields': [['ime_prezime', 'Kupac'], ['telefon', '061123456'], ['napomena', 'Prva']], 'inputs': {}, 'lines': []}
        self.url = reverse('staff_magacin_draft_save')

    def save(self, payload=None, version=0, event='save'):
        return self.client.post(self.url, json.dumps({'token': self.draft.token, 'version': version, 'event': event, 'payload': payload or self.payload}), content_type='application/json')

    def test_history_keeps_every_previous_value(self):
        self.assertEqual(self.save().status_code, 200)
        newer = dict(self.payload, fields=[['napomena', 'Druga']])
        self.assertEqual(self.save(newer, version=1).status_code, 200)
        self.draft.refresh_from_db()
        self.assertEqual(self.draft.payload, newer)
        self.assertEqual(list(self.draft.revisions.order_by('pk').values_list('payload', flat=True)), [self.payload, newer])

    def test_conflicting_tab_is_preserved_without_overwriting_current(self):
        self.save()
        stale = dict(self.payload, fields=[['napomena', 'Drugi tab']])
        self.assertEqual(self.save(stale).status_code, 409)
        self.draft.refresh_from_db()
        self.assertEqual(self.draft.payload, self.payload)
        self.assertEqual(self.draft.revisions.first().payload, stale)

    def test_cancel_archives_without_deleting_and_late_save_does_not_reactivate(self):
        self.save()
        self.assertEqual(self.save(version=1, event='cancel').status_code, 200)
        self.assertEqual(self.save(version=1).status_code, 409)
        self.draft.refresh_from_db()
        self.assertEqual(self.draft.status, 'cancelled')
        self.assertEqual(self.draft.payload, self.payload)
        self.assertGreaterEqual(self.draft.revisions.count(), 2)

    def test_other_owner_cannot_read_or_write_draft(self):
        other = get_user_model().objects.create_superuser('other-owner', '', 'password')
        self.client.force_login(other)
        self.assertEqual(self.save().status_code, 404)
        self.assertEqual(self.client.get(reverse('staff_magacin_narudzba_nova'), {'nacrt': self.draft.token}).status_code, 404)

    def test_resume_from_database_without_browser_storage(self):
        self.save()
        response = self.client.get(reverse('staff_magacin_narudzba_nova'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['form']['napomena'], 'Prva')
        self.assertEqual(response.context['draft_token'], self.draft.token)

    def test_successful_save_and_reservation_archive_original(self):
        for action, status in [('sacuvaj', Order.Status.NOVA), ('rezervacija', Order.Status.REZERVACIJA)]:
            with self.subTest(action=action):
                draft = ManualOrderDraft.objects.create(owner=self.user, token=action)
                with patch('EcommerceApp.views_magacin._create_manual_order', return_value=SimpleNamespace(status=status, broj='123')):
                    response = self.client.post(reverse('staff_magacin_narudzba_nova'), {
                        'action': action, 'draft_token': draft.token, 'draft_version': '0', 'napomena': 'Sačuvaj i historiju',
                    })
                self.assertEqual(response.status_code, 302)
                draft.refresh_from_db()
                self.assertEqual(draft.status, 'reserved' if action == 'rezervacija' else 'completed')
                self.assertEqual(draft.revisions.count(), 2)

    def test_failed_save_keeps_draft(self):
        with patch('EcommerceApp.views_magacin._create_manual_order', side_effect=MagacinError('Nema zalihe')):
            response = self.client.post(reverse('staff_magacin_narudzba_nova'), {
                'action': 'sacuvaj', 'draft_token': self.draft.token, 'draft_version': '0', 'napomena': 'Ne izgubi',
            })
        self.assertEqual(response.status_code, 200)
        self.draft.refresh_from_db()
        self.assertEqual(self.draft.status, 'active')
        self.assertIn(['napomena', 'Ne izgubi'], self.draft.payload['fields'])

    def test_restore_revision_creates_copy(self):
        self.save()
        revision = self.draft.revisions.first()
        response = self.client.post(reverse('staff_magacin_draft_restore', args=[revision.pk]))
        self.assertEqual(response.status_code, 302)
        copy = ManualOrderDraft.objects.exclude(pk=self.draft.pk).get()
        self.assertEqual(copy.payload, self.payload)
        self.assertTrue(ManualOrderDraftRevision.objects.filter(pk=revision.pk).exists())

    def test_browser_only_draft_is_imported_without_losing_later_variants(self):
        payload = dict(self.payload, token='legacy-draft')
        url = reverse('staff_magacin_draft_import')
        self.assertEqual(self.client.post(url, json.dumps(payload), content_type='application/json').status_code, 200)
        payload['fields'] = [['napomena', 'Kasniji lokalni unos']]
        self.assertEqual(self.client.post(url, json.dumps(payload), content_type='application/json').status_code, 200)
        imported = ManualOrderDraft.objects.get(token='legacy-draft')
        self.assertEqual(imported.revisions.count(), 2)
        self.assertEqual(imported.revisions.first().payload['fields'], payload['fields'])

    def test_duplicate_successful_submit_does_not_create_another_order(self):
        data = {'action': 'sacuvaj', 'draft_token': self.draft.token, 'draft_version': '0', 'napomena': 'Jednom'}
        with patch('EcommerceApp.views_magacin._create_manual_order', return_value=SimpleNamespace(status=Order.Status.NOVA, broj='123')) as create:
            self.client.post(reverse('staff_magacin_narudzba_nova'), data)
            self.client.post(reverse('staff_magacin_narudzba_nova'), data)
        self.assertEqual(create.call_count, 1)
