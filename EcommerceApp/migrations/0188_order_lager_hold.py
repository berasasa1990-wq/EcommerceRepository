from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('EcommerceApp', '0187_order_izvor'),
    ]

    operations = [
        migrations.AddField(
            model_name='order',
            name='lager_status',
            field=models.CharField(
                choices=[
                    ('nije', '—'),
                    ('rezervisano', 'Rezervisano'),
                    ('validirano', 'Validirano'),
                    ('otkazano', 'Otkazano'),
                ],
                db_index=True,
                default='nije',
                max_length=20,
                verbose_name='Magacin lager',
            ),
        ),
        migrations.CreateModel(
            name='OrderStockHold',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('kolicina', models.PositiveIntegerField()),
                ('status', models.CharField(
                    choices=[
                        ('rezervisano', 'Rezervisano'),
                        ('validirano', 'Validirano'),
                        ('otkazano', 'Otkazano'),
                    ],
                    db_index=True,
                    default='rezervisano',
                    max_length=20,
                )),
                ('kreiran', models.DateTimeField(auto_now_add=True)),
                ('location', models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name='magacin_holds',
                    to='EcommerceApp.warehouselocation',
                )),
                ('narudzba', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='magacin_holds',
                    to='EcommerceApp.order',
                )),
                ('product', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='magacin_holds',
                    to='EcommerceApp.product',
                )),
                ('variation', models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='magacin_holds',
                    to='EcommerceApp.productvariation',
                )),
            ],
            options={
                'verbose_name': 'Rezervacija narudžbe',
                'verbose_name_plural': 'Rezervacije narudžbi',
            },
        ),
    ]
