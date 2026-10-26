import json
from unittest.mock import patch, MagicMock
from django.contrib.auth import get_user_model
from django.test import TestCase, Client
from .models import ChatBotSettings, ChatBotUsage, Product
from .chatbot import api_key, key_cipher, generate_reply
from .forms import ChatBotSettingsForm


class ChatBotTests(TestCase):
    def setUp(self):
        from bera_control.services import issue_module_code, display_activation_code, redeem_module_code
        activation = issue_module_code('chatbot')
        redeem_module_code('chatbot', display_activation_code(activation))
        self.config = ChatBotSettings.objects.create(pk=1, guided_enabled=False, encrypted_api_key=key_cipher().encrypt(b'test-key-not-real').decode())

    def send(self, text='Zdravo', client=None):
        return (client or self.client).post('/api/chatbot/', json.dumps({'message': text}), content_type='application/json')

    def test_widget_is_home_only_and_private_config_is_not_exposed(self):
        response = self.client.get('/')
        self.assertContains(response, 'data-chatbot')
        self.assertContains(response, 'data-delay="5"')
        self.assertContains(response, 'chatbot.js')
        self.assertNotContains(response, 'test-key-not-real')
        self.assertNotContains(response, self.config.encrypted_api_key)
        self.assertNotContains(self.client.get('/noviteti/'), 'data-chatbot')
        self.config.enabled = False
        self.config.save()
        self.assertNotContains(self.client.get('/'), 'data-chatbot')
        self.assertEqual(self.send().status_code, 404)

    @patch('shop.chatbot.generate_reply', return_value='Pozdrav, kako mogu pomoći?')
    def test_chat_uses_server_history_and_public_catalog(self, reply):
        Product.objects.create(name='Štap premium', price=50)
        Product.objects.create(name='Štap skriveni', price=10, is_active=False)
        response = self.send('Imate li štap?')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['products'][0]['name'], 'Štap premium')
        self.assertNotIn('Štap skriveni', response.content.decode())
        self.assertEqual(reply.call_args.args[2], [])
        self.send('A koliko košta?')
        self.assertEqual(reply.call_args.args[2][0]['role'], 'user')
        other = Client()
        self.send(client=other)
        self.assertEqual(reply.call_args.args[2], [])
        self.assertEqual(self.client.post('/api/chatbot/', json.dumps({'action': 'reset'}), content_type='application/json').status_code, 200)
        self.assertNotIn('chatbot_history', self.client.session)
        self.assertTrue(ChatBotUsage.objects.exists())

    @patch('shop.chatbot.generate_reply', return_value='Odgovor')
    def test_rate_limit_and_daily_limit_are_persistent(self, reply):
        for _ in range(6):
            self.assertEqual(self.send().status_code, 200)
        self.assertEqual(self.send().status_code, 429)
        self.assertEqual(reply.call_count, 6)
        self.config.daily_request_limit = 6
        self.config.save()
        self.assertEqual(self.send(client=Client(REMOTE_ADDR='192.0.2.1')).status_code, 429)

    @patch.dict('os.environ', {'OPENAI_API_KEY': ''})
    @patch('shop.chatbot.generate_reply')
    def test_unconfigured_key_and_provider_error_fail_without_secrets(self, reply):
        self.config.encrypted_api_key = ''
        self.config.save()
        self.assertEqual(self.send().status_code, 503)
        reply.assert_not_called()
        self.config.encrypted_api_key = key_cipher().encrypt(b'test-key-not-real').decode()
        self.config.save()
        reply.side_effect = ValueError('test-key-not-real')
        response = self.send()
        self.assertEqual(response.status_code, 503)
        self.assertNotIn('test-key-not-real', response.content.decode())

    def test_csrf_post_only_and_input_limits(self):
        self.assertEqual(self.client.get('/api/chatbot/').status_code, 405)
        self.assertEqual(self.send(client=Client(enforce_csrf_checks=True)).status_code, 403)
        for value in ('', 'x'*1001, None, ['bad']):
            response = self.client.post('/api/chatbot/', json.dumps({'message': value}), content_type='application/json')
            self.assertEqual(response.status_code, 400)
        self.assertEqual(self.client.post('/api/chatbot/', '[1]', content_type='application/json').status_code, 400)

    def test_panel_is_staff_only_and_key_is_encrypted_preserved_and_not_rendered(self):
        self.assertEqual(self.client.get('/panel/chatbot/').status_code, 302)
        staff = get_user_model().objects.create_user('chatbot-manager', is_staff=True)
        self.client.force_login(staff)
        form = ChatBotSettingsForm({'enabled': 'on', 'name': 'Moj asistent', 'welcome_message': 'Dobro došli', 'delay_seconds': 5, 'model_name': 'gpt-4.1-mini', 'daily_request_limit': 50, 'category_question': 'Kategorija?', 'budget_question': 'Budžet?', 'results_message': 'Ponuda', 'no_results_message': 'Nema ponude', 'api_key': 'new-test-key'}, instance=self.config)
        self.assertTrue(form.is_valid(), form.errors)
        cfg = form.save()
        self.assertNotEqual(cfg.encrypted_api_key, 'new-test-key')
        self.assertEqual(api_key(cfg), 'new-test-key')
        response = self.client.get('/panel/chatbot/')
        self.assertNotContains(response, 'new-test-key')
        self.assertNotContains(response, cfg.encrypted_api_key)
        form = ChatBotSettingsForm({'name': 'Asistent', 'welcome_message': 'Zdravo', 'delay_seconds': 5, 'model_name': 'gpt-4.1-mini', 'daily_request_limit': 50, 'category_question': 'Kategorija?', 'budget_question': 'Budžet?', 'results_message': 'Ponuda', 'no_results_message': 'Nema ponude'}, instance=cfg)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(api_key(form.save()), 'new-test-key')

    @patch('shop.chatbot.build_opener')
    def test_real_client_payload_uses_responses_api_without_storage(self, opener):
        response = MagicMock()
        response.read.return_value = json.dumps({'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': 'Odgovor asistenta'}]}]}).encode()
        opener.return_value.open.return_value.__enter__.return_value = response
        self.assertEqual(generate_reply(self.config, 'test-key', [], 'Zdravo', []), 'Odgovor asistenta')
        request = opener.return_value.open.call_args.args[0]
        payload = json.loads(request.data)
        self.assertEqual(request.full_url, 'https://api.openai.com/v1/responses')
        self.assertFalse(payload['store'])
        self.assertEqual(payload['max_output_tokens'], 600)
        self.assertEqual(payload['input'][0]['role'], 'user')
        self.assertNotIn('test-key', request.data.decode())
