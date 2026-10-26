"""Deterministic category/budget shopping guide. No model or external requests."""
import re
from decimal import Decimal, InvalidOperation
from django.http import JsonResponse
from .models import Category, Product

MAX_BUDGET = Decimal('99999999.99')
PAGE_SIZE = 6


def amount(value):
    if not isinstance(value, (str, int)) or isinstance(value, bool):
        raise ValueError
    value = str(value).strip().lower()
    value = re.sub(r'^(do|maksimalno|max)\s+', '', value)
    value = re.sub(r'\s*(km|bam)\s*$', '', value).replace(' ', '')
    # Accept 1500,50 and regional thousands formatting such as 1.500,50.
    if ',' in value:
        value = value.replace('.', '').replace(',', '.')
    if not re.fullmatch(r'\d{1,8}(\.\d{1,2})?', value):
        raise ValueError
    try:
        number = Decimal(value)
    except InvalidOperation:
        raise ValueError from None
    if not number.is_finite() or not 0 <= number <= MAX_BUDGET:
        raise ValueError
    return number.quantize(Decimal('0.01'))


def choice(label, action, value=''):
    return {'label': label, 'action': action, 'value': str(value)}


def categories_reply(request, config, prefix=''):
    request.session['chatbot_guide'] = {'step': 'category'}
    categories = list(Category.objects.all().order_by('name', 'pk'))
    return JsonResponse({'guided': True, 'step': 'category',
        'reply': prefix + (config.category_question if categories else 'Trenutno nema dodanih kategorija. Kontaktirajte podršku za pomoć.'),
        'choices': [choice(category.name, 'guide_category', category.pk) for category in categories], 'products': []})


def budget_reply(request, config, category):
    request.session['chatbot_guide'] = {'step': 'budget', 'category': category.pk}
    presets = []
    for part in config.budget_presets.split(',')[:8]:
        try:
            budget = amount(part.strip())
        except ValueError:
            continue
        presets.append(choice(f'Do {budget:.0f} KM' if budget == budget.to_integral() else f'Do {budget:.2f} KM', 'guide_budget', budget))
    presets.append(choice('Promijeni kategoriju', 'guide_start'))
    return JsonResponse({'guided': True, 'step': 'budget', 'reply': f'{category.name}\n{config.budget_question}', 'choices': presets, 'products': []})


def results_reply(request, config, category, budget, offset=0):
    products = Product.objects.filter(category=category, is_active=True, is_out_of_stock=False, price__lte=budget).order_by('price', 'name', 'pk')
    count = products.count()
    if offset >= count:
        offset = 0
    items = list(products[offset:offset + PAGE_SIZE])
    template = config.results_message if count else config.no_results_message
    reply = template.replace('{category}', category.name).replace('{budget}', f'{budget:.2f}').replace('{count}', str(count))
    if count:
        reply += f'\nPrikaz {offset + 1}–{offset + len(items)} od {count}. Cijene su po komadu, bez dostave.'
    choices = []
    if offset + len(items) < count:
        choices.append(choice('Prikaži još artikala', 'guide_more'))
    choices.extend([choice('Promijeni budžet', 'guide_change_budget'), choice('Promijeni kategoriju', 'guide_start')])
    request.session['chatbot_guide'] = {'step': 'results', 'category': category.pk, 'budget': str(budget), 'offset': offset + len(items)}
    return JsonResponse({'guided': True, 'step': 'results', 'reply': reply, 'choices': choices,
        'products': [{'name': product.name, 'price': f'{product.price:.2f}', 'url': product.get_absolute_url(),
            'available': True, 'image': product.image.url if product.image else ''} for product in items]})


def guided_reply(request, config, data):
    action = data.get('action', 'guide_message')
    state = request.session.get('chatbot_guide', {})
    if action == 'guide_start':
        return categories_reply(request, config)
    if action == 'guide_category':
        value = data.get('value')
        if not isinstance(value, (int, str)) or isinstance(value, bool) or not re.fullmatch(r'\d{1,18}', str(value)):
            return JsonResponse({'error': 'Odaberite ispravnu kategoriju.'}, status=400)
        category = Category.objects.filter(pk=value).first()
        if category is None:
            return categories_reply(request, config, 'Kategorija više nije dostupna. ')
        return budget_reply(request, config, category)
    category = Category.objects.filter(pk=state.get('category')).first() if state.get('category') else None
    if action in ('guide_budget', 'guide_more', 'guide_change_budget'):
        if not category:
            return categories_reply(request, config)
        if action == 'guide_change_budget':
            return budget_reply(request, config, category)
        try:
            budget = amount(state.get('budget') if action == 'guide_more' else data.get('value'))
        except ValueError:
            return JsonResponse({'error': 'Unesite maksimalni budžet u KM, npr. 500 ili 1500,50.'}, status=400)
        return results_reply(request, config, category, budget, state.get('offset', 0) if action == 'guide_more' else 0)
    if action != 'guide_message':
        return JsonResponse({'error': 'Nepoznata opcija razgovora.'}, status=400)
    message = data.get('message')
    if not isinstance(message, str) or not message.strip() or len(message) > 1000:
        return JsonResponse({'error': 'Unesite poruku do 1000 znakova.'}, status=400)
    named = [item for item in Category.objects.all() if item.name.casefold() == message.strip().casefold()]
    if len(named) == 1:
        return budget_reply(request, config, named[0])
    if category and state.get('step') in ('budget', 'results'):
        try:
            budget = amount(message)
        except ValueError:
            return JsonResponse({'error': 'Upišite iznos u KM, npr. 500, ili odaberite ponuđenu opciju.'}, status=400)
        return results_reply(request, config, category, budget)
    return categories_reply(request, config)
