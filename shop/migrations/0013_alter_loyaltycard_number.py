import secrets
import shop.models
from django.db import migrations, models


def convert_numbers(apps, schema_editor):
    Card = apps.get_model('shop', 'LoyaltyCard')
    cards = Card.objects.using(schema_editor.connection.alias)
    used = set(cards.values_list('number', flat=True))
    for card in cards.all().iterator():
        while True:
            digits = f'{secrets.randbelow(100_000_000):08d}'
            number = f'{digits[:4]}-{digits[4:]}'
            if number not in used:
                break
        used.add(number)
        cards.filter(pk=card.pk).update(number=number)


class Migration(migrations.Migration):
    dependencies = [('shop', '0012_existing_loyalty_cards')]
    operations = [
        migrations.AlterField(model_name='loyaltycard', name='number', field=models.CharField(max_length=36, unique=True, editable=False, default=shop.models.generate_loyalty_number)),
        migrations.RunPython(convert_numbers),
        migrations.AlterField(model_name='loyaltycard', name='number', field=models.CharField(max_length=9, unique=True, editable=False, default=shop.models.generate_loyalty_number)),
    ]
