from django.db import migrations, models


def seed_from_magacin_orders(apps, schema_editor):
    Order = apps.get_model('EcommerceApp', 'Order')
    WarehouseCustomer = apps.get_model('EcommerceApp', 'WarehouseCustomer')
    seen = set()
    for order in Order.objects.filter(izvor='magacin').order_by('id'):
        ime = (order.ime_prezime or '').strip()
        telefon = (order.telefon or '').strip()
        if not ime or not telefon:
            continue
        key = (ime.casefold(), telefon)
        if key in seen:
            continue
        seen.add(key)
        WarehouseCustomer.objects.create(
            ime_prezime=ime[:200],
            telefon=telefon[:30],
            adresa=(order.adresa or '')[:300],
            grad=(order.grad or '')[:100],
            email=(order.email or '')[:254] if '@' in (order.email or '') else '',
            postanski_broj=(order.postanski_broj or '')[:20],
        )


class Migration(migrations.Migration):

    dependencies = [
        ('EcommerceApp', '0191_alter_order_status'),
    ]

    operations = [
        migrations.CreateModel(
            name='WarehouseCustomer',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('ime_prezime', models.CharField(max_length=200)),
                ('telefon', models.CharField(max_length=30)),
                ('adresa', models.CharField(blank=True, max_length=300)),
                ('grad', models.CharField(blank=True, max_length=100)),
                ('email', models.EmailField(blank=True, max_length=254)),
                ('postanski_broj', models.CharField(blank=True, max_length=20)),
                ('kreiran', models.DateTimeField(auto_now_add=True)),
                ('azuriran', models.DateTimeField(auto_now=True)),
            ],
            options={
                'verbose_name': 'Magacin kupac',
                'verbose_name_plural': 'Magacin kupci',
                'ordering': ['ime_prezime', 'id'],
            },
        ),
        migrations.AddIndex(
            model_name='warehousecustomer',
            index=models.Index(fields=['ime_prezime'], name='mg_customer_ime_idx'),
        ),
        migrations.AddIndex(
            model_name='warehousecustomer',
            index=models.Index(fields=['telefon'], name='mg_customer_tel_idx'),
        ),
        migrations.RunPython(seed_from_magacin_orders, migrations.RunPython.noop),
    ]
