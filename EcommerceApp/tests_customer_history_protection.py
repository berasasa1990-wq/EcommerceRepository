from django.db import DatabaseError, connection, transaction
from django.test import TestCase

from .models import Order, OrderItem, Product, SystemDataRevision, WarehouseCustomer


class CustomerHistoryProtectionTests(TestCase):
    def test_online_and_manual_order_changes_and_deletion_keep_full_history(self):
        product = Product.objects.create(naziv='Artikal', cijena=10)
        for source in (Order.Izvor.WEBSHOP, Order.Izvor.MAGACIN):
            order = Order.objects.create(ime_prezime='Kupac', email='kupac@example.test',
                                         telefon='061000000', adresa='Adresa', grad='Grad',
                                         izvor=source, ukupno=20)
            item = OrderItem.objects.create(narudzba=order, artikal=product,
                                            naziv=product.naziv, kolicina=2, cijena=10)
            key, item_key = str(order.pk), str(item.pk)
            Order.objects.filter(pk=order.pk).update(telefon='062000000', status=Order.Status.OTKAZANA)
            order.delete()
            history = SystemDataRevision.objects.filter(table_name=Order._meta.db_table, record_key=key)
            self.assertTrue(history.filter(operation='INSERT').exists())
            change = history.get(operation='UPDATE')
            self.assertEqual(change.before['telefon'], '061000000')
            self.assertEqual(change.after['telefon'], '062000000')
            self.assertEqual(change.after['izvor'], source)
            deleted = history.get(operation='DELETE')
            self.assertEqual(deleted.before['ime_prezime'], 'Kupac')
            deleted_item = SystemDataRevision.objects.get(table_name=OrderItem._meta.db_table,
                                                          record_key=item_key, operation='DELETE')
            self.assertEqual(deleted_item.before['kolicina'], 2)

    def test_customer_bulk_updates_and_deletes_remain_in_history(self):
        customer = WarehouseCustomer.objects.create(ime_prezime='Kupac historija', telefon='061000000')
        key = str(customer.pk)
        WarehouseCustomer.objects.filter(pk=customer.pk).update(telefon='062000000')
        WarehouseCustomer.objects.filter(pk=customer.pk).delete()
        history = SystemDataRevision.objects.filter(table_name=WarehouseCustomer._meta.db_table, record_key=key)
        self.assertEqual(history.get(operation='UPDATE').before['telefon'], '061000000')
        self.assertEqual(history.get(operation='DELETE').before['telefon'], '062000000')

    def test_history_cannot_be_changed_or_deleted_even_by_raw_sql(self):
        product = Product.objects.create(naziv='Sačuvaj trag', cijena=10)
        revision = SystemDataRevision.objects.get(table_name=Product._meta.db_table,
                                                  record_key=str(product.pk), operation='INSERT')
        table = connection.ops.quote_name(SystemDataRevision._meta.db_table)
        for sql in (f'UPDATE {table} SET operation = %s WHERE id = %s',
                    f'DELETE FROM {table} WHERE id = %s'):
            params = ['DELETE', revision.pk] if sql.startswith('UPDATE') else [revision.pk]
            with self.assertRaises(DatabaseError), transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute(sql, params)
        revision.refresh_from_db()
        self.assertEqual(revision.operation, 'INSERT')

    def test_baseline_refresh_does_not_duplicate_existing_history(self):
        from .retention_triggers import install_history
        product = Product.objects.create(naziv='Postojeći trag', cijena=10)
        history = SystemDataRevision.objects.filter(table_name=Product._meta.db_table,
                                                    record_key=str(product.pk))
        before = history.count()
        install_history(connection, baseline=True)
        install_history(connection, baseline=True)
        self.assertEqual(history.count(), before)

    def test_postgres_truncate_cannot_bypass_history(self):
        if connection.vendor != 'postgresql':
            self.skipTest('PostgreSQL-only TRUNCATE protection')
        for model in (Order, OrderItem, WarehouseCustomer, SystemDataRevision):
            with self.assertRaises(DatabaseError), transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute(f'TRUNCATE {connection.ops.quote_name(model._meta.db_table)} CASCADE')
