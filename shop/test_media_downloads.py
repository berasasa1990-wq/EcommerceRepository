from pathlib import Path
from tempfile import TemporaryDirectory

from django.test import TestCase, override_settings

from .models import SEOSettings


class MediaDownloadTests(TestCase):
    def test_existing_image_download_uses_site_and_first_meta_sentence(self):
        SEOSettings.objects.create(site_name='Moj Šop', description='Najbolja ponuda za vas. Druga rečenica.')
        with TemporaryDirectory() as directory, override_settings(MEDIA_ROOT=directory):
            Path(directory, 'old-name.avif').write_bytes(b'image content')
            response = self.client.get('/media/old-name.avif')
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response['Content-Disposition'], 'inline; filename="moj-sop-najbolja-ponuda-za-vas.avif"')
            self.assertEqual(b''.join(response.streaming_content), b'image content')
            self.assertTrue(Path(directory, 'old-name.avif').exists())

    def test_empty_description_and_missing_files(self):
        SEOSettings.objects.create(site_name='Moj shop', description='')
        with TemporaryDirectory() as directory, override_settings(MEDIA_ROOT=directory):
            Path(directory, 'logo.png').write_bytes(b'image')
            response = self.client.get('/media/logo.png')
            self.assertEqual(response['Content-Disposition'], 'inline; filename="moj-shop.png"')
            response.close()
            self.assertEqual(self.client.get('/media/missing.png').status_code, 404)
            Path(directory, 'private.txt').write_text('private')
            self.assertEqual(self.client.get('/media/private.txt').status_code, 404)
