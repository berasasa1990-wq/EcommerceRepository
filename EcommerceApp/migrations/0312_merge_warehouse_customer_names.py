from django.db import migrations


def merge_customer_names(apps, schema_editor):
    Customer = apps.get_model('EcommerceApp', 'WarehouseCustomer')
    Partner = apps.get_model('EcommerceApp', 'WarehousePartner')
    Entry = apps.get_model('EcommerceApp', 'WarehouseLedgerEntry')
    db = schema_editor.connection.alias
    survivors = {}
    for customer in Customer.objects.using(db).order_by('pk').iterator():
        key = ' '.join((customer.ime_prezime or '').split()).casefold()
        if not key:
            continue
        if key not in survivors:
            survivors[key] = customer.pk
            continue
        survivor_id = survivors[key]
        survivor = Customer.objects.using(db).get(pk=survivor_id)
        updates = {}
        for field in ('telefon', 'adresa', 'grad', 'email', 'postanski_broj'):
            if not getattr(survivor, field) and getattr(customer, field):
                updates[field] = getattr(customer, field)
        for field in ('vp_kupac', 'odbio_posiljku'):
            updates[field] = getattr(survivor, field) or getattr(customer, field)
        Customer.objects.using(db).filter(pk=survivor_id).update(**updates)
        for model_name in ('MagacinVpNarudzba', 'MagacinPonuda'):
            apps.get_model('EcommerceApp', model_name).objects.using(db).filter(customer_id=customer.pk).update(customer_id=survivor_id)
        old_partner = Partner.objects.using(db).filter(customer_id=customer.pk).first()
        if old_partner:
            new_partner = Partner.objects.using(db).filter(customer_id=survivor_id).first()
            if not new_partner:
                Partner.objects.using(db).filter(pk=old_partner.pk).update(customer_id=survivor_id)
            else:
                Entry.objects.using(db).filter(partner_id=old_partner.pk).update(partner_id=new_partner.pk)
                fields = {}
                for field in ('grad', 'adresa', 'telefon', 'pdv_broj', 'kontakt'):
                    if not getattr(new_partner, field) and getattr(old_partner, field):
                        fields[field] = getattr(old_partner, field)
                if old_partner.napomena and old_partner.napomena != new_partner.napomena:
                    fields['napomena'] = '\n'.join(filter(None, [new_partner.napomena, old_partner.napomena]))
                if fields:
                    Partner.objects.using(db).filter(pk=new_partner.pk).update(**fields)
                Partner.objects.using(db).filter(pk=old_partner.pk).delete()
        Customer.objects.using(db).filter(pk=customer.pk).delete()


class Migration(migrations.Migration):
    dependencies = [('EcommerceApp', '0311_unique_b2b_customer_names')]
    operations = [migrations.RunPython(merge_customer_names, migrations.RunPython.noop)]
