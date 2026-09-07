from django.conf import settings
from django.db import migrations


def assign_basic(apps, schema_editor):
    alias = schema_editor.connection.alias
    User = apps.get_model(settings.AUTH_USER_MODEL)
    Plan = apps.get_model('EcommerceApp', 'MagacinPlan')
    Subscription = apps.get_model('EcommerceApp', 'MagacinSubscription')
    Owner = apps.get_model('EcommerceApp', 'MagacinSubscriptionOwner')
    basic = Plan.objects.using(alias).get(code='basic')
    owner_ids = Owner.objects.using(alias).values_list('user_id', flat=True)
    user_ids = User.objects.using(alias).filter(is_superuser=True).exclude(pk__in=owner_ids).values_list('pk', flat=True)
    for user_id in user_ids.iterator():
        Subscription.objects.using(alias).get_or_create(user_id=user_id, defaults={'plan_id': basic.pk})


class Migration(migrations.Migration):
    dependencies = [('EcommerceApp', '0235_seed_magacin_plans')]
    operations = [migrations.RunPython(assign_basic, migrations.RunPython.noop)]
