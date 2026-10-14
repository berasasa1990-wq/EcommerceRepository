from django.db import migrations, models


def merge_b2b_names(apps, schema_editor):
    Account = apps.get_model('EcommerceApp', 'B2BAccount')
    Submission = apps.get_model('EcommerceApp', 'B2BSubmission')
    Rabat = apps.get_model('EcommerceApp', 'B2BAccountBrandRabat')
    db = schema_editor.connection.alias
    survivors = {}
    for account in Account.objects.using(db).order_by('pk').iterator():
        name = ' '.join((account.company or '').split())
        key = name.casefold() or None
        if key is None or key not in survivors:
            Account.objects.using(db).filter(pk=account.pk).update(company=name, company_key=key)
            if key:
                survivors[key] = account.pk
            continue
        survivor_id = survivors[key]
        survivor = Account.objects.using(db).get(pk=survivor_id)
        updates = {}
        for field in ('ime_prezime', 'telefon', 'adresa', 'grad', 'postanski_broj', 'email'):
            if not getattr(survivor, field) and getattr(account, field):
                updates[field] = getattr(account, field)
        for field in ('vp_kupac', 'odbio_posiljku', 'is_active'):
            updates[field] = getattr(survivor, field) or getattr(account, field)
        if updates:
            Account.objects.using(db).filter(pk=survivor_id).update(**updates)
        Submission.objects.using(db).filter(account_id=account.pk).update(account_id=survivor_id)
        for discount in Rabat.objects.using(db).filter(account_id=account.pk).order_by('pk'):
            if Rabat.objects.using(db).filter(account_id=survivor_id, brand_id=discount.brand_id).exists():
                discount.delete(using=db)
            else:
                Rabat.objects.using(db).filter(pk=discount.pk).update(account_id=survivor_id)
        Account.objects.using(db).filter(pk=account.pk).delete()


class Migration(migrations.Migration):
    dependencies = [('EcommerceApp', '0310_scratch_reward_coupons')]
    operations = [
        migrations.AddField(model_name='b2baccount', name='company_key',
                            field=models.CharField(max_length=200, null=True, editable=False)),
        migrations.RunPython(merge_b2b_names, migrations.RunPython.noop),
        migrations.AlterField(model_name='b2baccount', name='company_key',
                              field=models.CharField(max_length=200, unique=True, null=True, editable=False)),
    ]
