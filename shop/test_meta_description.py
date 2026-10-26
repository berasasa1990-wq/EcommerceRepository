from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from .models import SEOSettings


class MetaDescriptionSettingsTests(TestCase):
    def test_save_description_renders_meta_without_changing_other_settings(self):
        config = SEOSettings.objects.create(pk=1, site_name='Trgovina', home_title='Moj naslov')
        manager = get_user_model().objects.create_user('meta-manager', is_staff=True)
        self.client.force_login(manager)
        url = reverse('panel_section', args=['podesavanje'])
        self.assertContains(self.client.get(url), 'Spremi meta opis')
        self.assertRedirects(self.client.post(url, {'settings_section': 'meta_description', 'meta-description': 'Ponuda naše trgovine.'}), url)
        config.refresh_from_db()
        self.assertEqual(config.home_title, 'Moj naslov')
        self.assertContains(self.client.get('/'), '<meta name="description" content="Ponuda naše trgovine.">', html=True)
