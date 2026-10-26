import json
from unittest.mock import patch
from django.test import TestCase, Client
from .models import ChatBotSettings, ChatBotUsage, Category, Product
from .forms import ChatBotSettingsForm


class GuidedChatBotTests(TestCase):
    def setUp(self):
        from bera_control.services import issue_module_code, display_activation_code, redeem_module_code
        activation = issue_module_code('chatbot')
        redeem_module_code('chatbot', display_activation_code(activation))
        self.config = ChatBotSettings.objects.create(pk=1, guided_enabled=True, category_question='Koji uređaj tražite?', budget_question='Koliki vam je budžet?', results_message='{category}: do {budget} KM ({count} artikala)')
        self.phone = Category.objects.create(name='Telefon')
        self.tv = Category.objects.create(name='Televizor')
        Category.objects.create(name='Lap Top')
        Category.objects.create(name='Sat')
        self.cheap = Product.objects.create(name='Telefon 300', category=self.phone, price=300)
        self.exact = Product.objects.create(name='Telefon 500', category=self.phone, price=500)
        Product.objects.create(name='Telefon skup', category=self.phone, price=501)
        Product.objects.create(name='Telefon skriven', category=self.phone, price=100, is_active=False)
        Product.objects.create(name='Telefon rasprodan', category=self.phone, price=100, is_out_of_stock=True)
        Product.objects.create(name='Televizor 100', category=self.tv, price=100)

    def send(self, action=None, value=None, message=None, client=None):
        payload = {'action': action, 'value': value} if action else {'message': message}
        return (client or self.client).post('/api/chatbot/', json.dumps(payload), content_type='application/json')

    @patch('shop.chatbot.generate_reply')
    def test_guided_purchase_uses_all_categories_and_real_budget_without_api(self, api):
        response = self.send('guide_start').json()
        self.assertEqual(response['reply'], 'Koji uređaj tražite?')
        self.assertEqual({choice['label'] for choice in response['choices']}, {'Telefon', 'Televizor', 'Lap Top', 'Sat'})
        response = self.send('guide_category', self.phone.pk).json()
        self.assertEqual(response['step'], 'budget')
        self.assertIn('Koliki vam je budžet?', response['reply'])
        response = self.send(message='do 500 KM').json()
        self.assertEqual([item['name'] for item in response['products']], ['Telefon 300', 'Telefon 500'])
        self.assertIn('Telefon: do 500.00 KM (2 artikala)', response['reply'])
        self.assertTrue(all(item['available'] for item in response['products']))
        api.assert_not_called()
        self.assertFalse(ChatBotUsage.objects.exists())

    def test_custom_amount_and_no_matches_can_change_category_or_budget(self):
        self.send('guide_category', self.phone.pk)
        response = self.send(message='299,99 KM').json()
        self.assertEqual(response['products'], [])
        self.assertIn('Trenutno nema dostupnih', response['reply'])
        self.assertEqual({c['action'] for c in response['choices']}, {'guide_change_budget', 'guide_start'})
        self.assertEqual(self.send('guide_change_budget').json()['step'], 'budget')
        response = self.send(message='1.500,50 KM').json()
        self.assertEqual(len(response['products']), 3)
        self.assertEqual(self.send(message='Televizor').json()['step'], 'budget')
        self.assertEqual(self.send('guide_budget', 100).json()['products'][0]['name'], 'Televizor 100')

    def test_sessions_are_isolated_and_invalid_ids_and_budgets_are_rejected(self):
        self.send('guide_category', self.phone.pk)
        response = self.send('guide_budget', 500, client=Client()).json()
        self.assertEqual(response['step'], 'category')
        for value in ('-1', 'NaN', 'Infinity', '9999999999', '500; DROP TABLE', None):
            self.assertEqual(self.send('guide_budget', value).status_code, 400)
        self.assertEqual(self.send('guide_category', 'not-an-id').status_code, 400)
        self.assertEqual(self.send('guide_category', True).status_code, 400)
        self.assertEqual(self.send('guide_category', 999999).json()['step'], 'category')

    def test_more_results_and_reset(self):
        for number in range(8):
            Product.objects.create(name=f'Telefon dodatni {number}', category=self.phone, price=400)
        self.send('guide_category', self.phone.pk)
        first = self.send('guide_budget', 500).json()
        self.assertEqual(len(first['products']), 6)
        self.assertIn('guide_more', [c['action'] for c in first['choices']])
        second = self.send('guide_more').json()
        self.assertEqual(len(second['products']), 4)
        self.assertFalse({p['url'] for p in first['products']} & {p['url'] for p in second['products']})
        self.send('reset')
        self.assertNotIn('chatbot_guide', self.client.session)
        self.assertEqual(self.send('guide_budget', 500).json()['step'], 'category')

    def test_questions_and_budget_presets_are_editable_and_validated(self):
        data = {'enabled': 'on', 'guided_enabled': 'on', 'name': 'Vodič', 'welcome_message': 'Zdravo', 'delay_seconds': 5,
                'model_name': 'gpt-4.1-mini', 'daily_request_limit': 200, 'category_question': 'Šta želite?',
                'budget_question': 'Do koliko KM?', 'budget_presets': '100, 750, 1500', 'results_message': 'Ponuda do {budget}', 'no_results_message': 'Nema rezultata'}
        form = ChatBotSettingsForm(data, instance=self.config)
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        self.assertEqual(self.send('guide_start').json()['reply'], 'Šta želite?')
        choices = self.send('guide_category', self.phone.pk).json()['choices']
        self.assertEqual(choices[0]['label'], 'Do 100 KM')
        data['budget_presets'] = 'NaN, -1'
        self.assertFalse(ChatBotSettingsForm(data, instance=self.config).is_valid())
        self.assertContains(self.client.get('/'), 'data-guided="true"')
