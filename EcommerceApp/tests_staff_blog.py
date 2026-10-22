import io
import tempfile
from unittest.mock import patch

from PIL import Image
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import HomeVlog
from .views import HOME_VLOG_LIMIT


@override_settings(SECURE_SSL_REDIRECT=False, SITE_PREP_ENABLED=False,
    STORAGES={'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
              'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class StaffBlogTests(TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.settings_override = override_settings(MEDIA_ROOT=self.directory.name)
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)
        self.admin = get_user_model().objects.create_superuser('blog-admin', 'blog@example.com', 'password')
        self.client.force_login(self.admin)

    def test_create_upload_edit_and_refresh_home_cache(self):
        buffer = io.BytesIO()
        Image.new('RGB', (800, 500), 'green').save(buffer, format='PNG')
        image = SimpleUploadedFile('blog.png', buffer.getvalue(), content_type='image/png')
        key = f'home_vlogs_v2:{HOME_VLOG_LIMIT}'
        cache.set(key, ['old'])
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(reverse('staff_blog'), {
                'naslov': 'Novi blog', 'slika': image, 'sadrzaj': 'Tekst članka',
                'kratki_opis': 'Kratki opis', 'redoslijed': 0, 'aktivan': 'on',
            })
        self.assertEqual(response.status_code, 302)
        post = HomeVlog.objects.get()
        self.assertTrue(post.slika.storage.exists(post.slika.name))
        self.assertIsNone(cache.get(key))
        response = self.client.post(reverse('staff_blog'), {
            'post_id': post.pk, 'naslov': 'Izmijenjen naslov', 'sadrzaj': 'Novi sadržaj',
            'kratki_opis': 'Novi opis', 'redoslijed': 2,
        })
        self.assertEqual(response.status_code, 302)
        post.refresh_from_db()
        self.assertEqual(post.sadrzaj, 'Novi sadržaj')
        self.assertFalse(post.aktivan)
        self.assertTrue(post.slika)

    @patch('EcommerceApp.views._base_context', return_value={})
    def test_editor_renders(self, context):
        response = self.client.get(reverse('staff_blog'))
        self.assertContains(response, 'multipart/form-data')
        self.assertContains(response, '800 × 500')

    def test_non_superuser_cannot_edit(self):
        self.admin.is_superuser = False
        self.admin.save(update_fields=['is_superuser'])
        response = self.client.post(reverse('staff_blog'), {'naslov': 'Nedozvoljen'})
        self.assertEqual(response.status_code, 302)
        self.assertFalse(HomeVlog.objects.exists())
