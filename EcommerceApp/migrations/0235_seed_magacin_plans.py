from django.conf import settings
from django.db import migrations


def seed(apps, schema_editor):
    Plan = apps.get_model('EcommerceApp', 'MagacinPlan')
    for code, features in (
        ('basic', ['artikli', 'narudzbe', 'picking']),
        ('premium', ['artikli', 'narudzbe', 'picking', 'stampa_cijena', 'stampa_deklaracije']),
        ('ultimate', []),
    ):
        Plan.objects.using(schema_editor.connection.alias).get_or_create(code=code, defaults={'features': features})
    User = apps.get_model(settings.AUTH_USER_MODEL)
    owners = list(User.objects.using(schema_editor.connection.alias).filter(email__iexact='sasabera1990@gmail.com', is_active=True, is_superuser=True)[:2])
    if len(owners) == 1:
        Owner = apps.get_model('EcommerceApp', 'MagacinSubscriptionOwner')
        Owner.objects.using(schema_editor.connection.alias).get_or_create(pk=1, defaults={'user_id': owners[0].pk})


class Migration(migrations.Migration):
    dependencies = [('EcommerceApp', '0234_magacin_subscriptions')]
    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
