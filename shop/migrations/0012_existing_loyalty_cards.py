from django.conf import settings
from django.db import migrations


def create_cards(apps, schema_editor):
    User = apps.get_model(settings.AUTH_USER_MODEL)
    Card = apps.get_model('shop', 'LoyaltyCard')
    for user in User.objects.using(schema_editor.connection.alias).all().iterator():
        Card.objects.using(schema_editor.connection.alias).get_or_create(customer_id=user.pk)


class Migration(migrations.Migration):
    dependencies = [('shop', '0011_loyaltytier_order_loyalty_discount_and_more'), migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations = [migrations.RunPython(create_cards, migrations.RunPython.noop)]
