from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from .bera_home import storefront_context
from .models import BeraAppearance


class ButtonAppearanceTests(TestCase):
    def setUp(self):
        self.client.force_login(get_user_model().objects.create_superuser(
            'appearance-owner', 'appearance@example.test', 'test-password'))

    def test_orange_preset_and_default_restore(self):
        url = reverse('staff_button_color')
        self.assertEqual(self.client.post(url, {'button_mode': 'orange'}).status_code, 302)
        self.assertEqual(BeraAppearance.objects.get(pk=1).button_color, 'orange')
        context = storefront_context()
        self.assertTrue(context['bera_button_orange'])
        self.assertEqual(context['bera_button_color'], '#fc730a')
        self.assertEqual(context['bera_button_background'],
            'linear-gradient(180deg, rgb(255, 148, 1) 0%, rgb(252, 115, 10) 50%, rgb(244, 92, 2) 100%)')
        self.client.post(url, {'button_mode': 'default'})
        self.assertNotIn('bera_button_color', storefront_context())

    def test_custom_color_and_invalid_input(self):
        url = reverse('staff_button_color')
        self.client.post(url, {'button_mode': 'custom', 'button_color': '#ABCDEF'})
        self.assertEqual(storefront_context()['bera_button_background'], '#abcdef')
        self.client.post(url, {'button_mode': 'custom', 'button_color': 'invalid'})
        self.assertEqual(BeraAppearance.objects.get(pk=1).button_color, '#abcdef')
