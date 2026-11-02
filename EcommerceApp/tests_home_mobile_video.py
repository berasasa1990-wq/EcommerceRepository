import struct

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase

from .utils.videos import validate_home_mobile_video


def video(seconds, name='promo.mp4'):
    def atom(kind, data):
        return struct.pack('>I4s', len(data) + 8, kind) + data
    header = bytes(12) + struct.pack('>II', 1000, int(seconds * 1000))
    content = atom(b'ftyp', b'isom' + bytes(4)) + atom(b'moov', atom(b'mvhd', header))
    return SimpleUploadedFile(name, content, content_type='video/mp4')


class HomeMobileVideoTests(SimpleTestCase):
    def test_accepts_ten_seconds_and_restores_upload_position(self):
        upload = video(10)
        upload.seek(2)
        validate_home_mobile_video(upload)
        self.assertEqual(upload.tell(), 2)

    def test_rejects_long_video(self):
        with self.assertRaisesMessage(ValidationError, 'najviše 10 sekundi'):
            validate_home_mobile_video(video(10.1))

    def test_rejects_other_formats(self):
        with self.assertRaisesMessage(ValidationError, 'MP4 formatu'):
            validate_home_mobile_video(video(5, 'promo.mov'))

    def test_rejects_invalid_mp4(self):
        with self.assertRaisesMessage(ValidationError, 'nije moguće provjeriti'):
            validate_home_mobile_video(SimpleUploadedFile('bad.mp4', b'bad'))

    def test_video_template_keeps_link_and_has_no_controls(self):
        from types import SimpleNamespace
        from django.template.loader import render_to_string
        html = render_to_string('partials/home_mobile_video.html', {
            'video_file': SimpleNamespace(url='/media/second.mp4'),
            'video_link': '/?akcija=1',
        })
        self.assertIn('src="/media/second.mp4"', html)
        self.assertIn('href="/?akcija=1"', html)
        self.assertIn('autoplay muted loop playsinline', html)
        self.assertNotIn('controls', html)

    def test_second_upload_has_same_validation(self):
        from .forms import HomeMobileVideoForm
        from .models import SiteSettings
        form = HomeMobileVideoForm(data={}, files={'home_mobile_video_2': video(11)}, instance=SiteSettings())
        self.assertFalse(form.is_valid())
        self.assertIn('home_mobile_video_2', form.errors)
