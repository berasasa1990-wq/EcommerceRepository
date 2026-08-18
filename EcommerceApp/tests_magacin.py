import json
import time
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .magacin import (
    MAGACIN_SYNC_SESSION_KEY,
    MagacinError,
    NOVI_UVOZ_NAZIV,
    _apply_quant_batch,
    _odoo_id_to_local,
    apply_magacin_uvoz,
    apply_movement,
    attach_site_odoo_products_to_magacin,
    cancel_order_stock,
    deduct_for_order,
    local_odoo_template_ids,
    location_rows,
    magacin_products_qs,
    reserve_for_order,
    seed_default_locations,
    stock_totals,
    run_sync_chunk,
    sync_catalog_chunk,
    validate_order_stock,
)
from .models import (
    MagacinPopis,
    MagacinVpNarudzba,
    Order,
    OrderItem,
    Product,
    ProductVariation,
    ProductWarehouseMeta,
    Uvoz,
    UvozStavka,
    NivelacijaOznaka,
    WarehouseCustomer,
    WarehouseLocation,
    WarehouseMovement,
    WarehouseStock,
    WarehouseSyncLog,
)


class MagacinStockTests(TestCase):
    def setUp(self):
        self.product = Product.objects.create(
            naziv='Fox Submerge Sinking Braid',
            sifra='FOX12345',
            cijena=Decimal('29.90'),
            stanje=0,
            na_stanju=False,
        )
        self.a10 = WarehouseLocation.objects.create(sifra='A-10', naziv='Glavni magacin', redoslijed=10)
        self.b03 = WarehouseLocation.objects.create(sifra='B-03', naziv='Maloprodaja Sarajevo', redoslijed=20)

    def test_prijem_and_prodaja(self):
        apply_movement(product=self.product, location=self.a10, tip='prijem', kolicina=50, napomena='Prijem robe')
        self.product.refresh_from_db()
        self.assertEqual(self.product.stanje, 50)
        self.assertTrue(self.product.na_stanju)
        apply_movement(product=self.product, location=self.a10, tip='prodaja', kolicina=3, napomena='Maloprodaja')
        self.product.refresh_from_db()
        self.assertEqual(self.product.stanje, 47)
        totals = stock_totals(self.product)
        self.assertEqual(totals['na_stanju'], 47)

    def test_transfer_moves_stock(self):
        apply_movement(product=self.product, location=self.a10, tip='prijem', kolicina=20)
        apply_movement(
            product=self.product,
            location=self.a10,
            to_location=self.b03,
            tip='transfer',
            kolicina=8,
            napomena='Premještaj',
        )
        here = WarehouseStock.objects.get(product=self.product, location=self.a10, variation__isnull=True)
        there = WarehouseStock.objects.get(product=self.product, location=self.b03, variation__isnull=True)
        self.assertEqual(here.kolicina, 12)
        self.assertEqual(there.kolicina, 8)
        move = WarehouseMovement.objects.filter(tip='transfer').first()
        self.assertEqual(move.to_location_id, self.b03.pk)

    def test_prodaja_insufficient(self):
        apply_movement(product=self.product, location=self.a10, tip='prijem', kolicina=2)
        with self.assertRaises(MagacinError):
            apply_movement(product=self.product, location=self.a10, tip='prodaja', kolicina=5)

    def test_korekcija_sets_absolute(self):
        apply_movement(product=self.product, location=self.a10, tip='prijem', kolicina=10)
        apply_movement(product=self.product, location=self.a10, tip='korekcija', kolicina=7)
        stock = WarehouseStock.objects.get(product=self.product, location=self.a10)
        self.assertEqual(stock.kolicina, 7)

    def test_variation_stock_updates_variation_qty(self):
        var = ProductVariation.objects.create(
            artikal=self.product, naziv='0.30mm 300m', sifra='FOX12345-030', cijena=Decimal('29.90'),
        )
        apply_movement(product=self.product, variation=var, location=self.a10, tip='prijem', kolicina=87)
        var.refresh_from_db()
        self.product.refresh_from_db()
        self.assertEqual(var.stanje, 87)
        self.assertEqual(self.product.stanje, 87)

    def test_location_rows_only_where_article_has_stock(self):
        apply_movement(product=self.product, location=self.a10, tip='prijem', kolicina=10)
        rows, totals = location_rows(self.product)
        self.assertEqual(totals['na_stanju'], 10)
        self.assertEqual([row['location'].sifra for row in rows], ['A-10'])
        self.assertTrue(all(row['kolicina'] > 0 for row in rows))

    def test_prenos_mp_is_not_counted_as_stock(self):
        mp = WarehouseLocation.objects.create(
            sifra='Prenos u MP', naziv='Prenos u MP', odoo_location_path='WH/Prenos u MP',
        )
        apply_movement(product=self.product, location=self.a10, tip='prijem', kolicina=4)
        WarehouseStock.objects.create(product=self.product, location=mp, kolicina=20)
        rows, totals = location_rows(self.product)
        self.assertEqual(totals['na_stanju'], 4)
        self.assertEqual([row['location'].sifra for row in rows], ['A-10'])
        self.assertFalse(any('prenos' in row['location'].sifra.casefold() for row in rows))

    def test_maloprodaja_is_not_counted_as_stock(self):
        apply_movement(product=self.product, location=self.a10, tip='prijem', kolicina=4)
        apply_movement(product=self.product, location=self.b03, tip='prijem', kolicina=20)
        rows, totals = location_rows(self.product)
        self.assertEqual(totals['na_stanju'], 4)
        self.assertEqual(stock_totals(self.product)['dostupno'], 4)
        self.assertEqual([row['location'].sifra for row in rows], ['A-10'])
        self.assertFalse(any('maloprodaja' in row['location'].naziv.casefold() for row in rows))

    def test_seed_default_locations(self):
        created = seed_default_locations()
        self.assertGreaterEqual(created, 3)
        self.assertTrue(WarehouseLocation.objects.filter(sifra='A-10').exists())

    def test_deduct_for_order_takes_available_and_returns_leftover(self):
        apply_movement(product=self.product, location=self.a10, tip='prijem', kolicina=4)
        leftover = deduct_for_order(self.product, 10, napomena='Ručna narudžba')
        self.assertEqual(leftover, 6)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stanje, 0)
        self.assertEqual(
            WarehouseMovement.objects.filter(tip=WarehouseMovement.Tip.PRODAJA).count(),
            1,
        )

    def test_reserve_validate_and_cancel(self):
        apply_movement(product=self.product, location=self.a10, tip='prijem', kolicina=10)
        order = Order.objects.create(
            ime_prezime='Test', email='t@example.com', telefon='061',
            adresa='A', grad='S', ukupno=Decimal('10.00'),
            izvor=Order.Izvor.MAGACIN,
        )
        leftover = reserve_for_order(order, self.product, 4)
        self.assertEqual(leftover, 0)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stanje, 10)
        self.assertEqual(stock_totals(self.product)['dostupno'], 6)
        self.assertEqual(stock_totals(self.product)['rezervisano'], 4)

        leftover2 = reserve_for_order(order, self.product, 8)
        self.assertEqual(leftover2, 2)
        self.assertEqual(stock_totals(self.product)['dostupno'], 0)

        validate_order_stock(order)
        order.refresh_from_db()
        self.product.refresh_from_db()
        self.assertEqual(order.lager_status, Order.LagerStatus.VALIDIRANO)
        self.assertEqual(order.status, Order.Status.ZAVRSENA)
        self.assertEqual(order.get_status_label(), 'Validatovana')
        self.assertEqual(self.product.stanje, 0)
        self.assertEqual(stock_totals(self.product)['rezervisano'], 0)

    def test_cancel_releases_reservation(self):
        apply_movement(product=self.product, location=self.a10, tip='prijem', kolicina=5)
        order = Order.objects.create(
            ime_prezime='Test', email='t@example.com', telefon='061',
            adresa='A', grad='S', ukupno=Decimal('10.00'),
            izvor=Order.Izvor.MAGACIN, lager_status=Order.LagerStatus.REZERVISANO,
        )
        reserve_for_order(order, self.product, 3)
        cancel_order_stock(order)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.OTKAZANA)
        self.assertEqual(order.lager_status, Order.LagerStatus.OTKAZANO)
        self.assertEqual(stock_totals(self.product)['rezervisano'], 0)
        self.assertEqual(stock_totals(self.product)['dostupno'], 5)


class FakeOdooClient:
    def __init__(self, templates):
        self.templates = {int(row['id']): row for row in templates}
        self.image_requests = []

    def get_templates_by_ids(self, template_ids):
        return [self.templates[int(tid)] for tid in template_ids if int(tid) in self.templates]

    def get_template_images(self, template_ids, *, batch_size=5):
        self.image_requests.extend(int(tid) for tid in template_ids)
        return {}

    def get_product_variants(self, variant_ids, *, with_images=False):
        return []


class MagacinCatalogSyncTests(TestCase):
    def test_updates_existing_by_odoo_id_without_duplicate(self):
        product = Product.objects.create(
            naziv='Stari naziv',
            sifra='FOX-OLD',
            barkod='111',
            cijena=Decimal('10.00'),
            odoo_template_id=501,
            stanje=4,
        )
        client = FakeOdooClient([{
            'id': 501,
            'name': 'Novi naziv',
            'default_code': 'FOX-NEW',
            'barcode': '999',
            'list_price': '19.50',
            'qty_available': 8,
            'product_variant_ids': [501],
        }])
        stats = sync_catalog_chunk(client, [501], start=0, limit=10)
        self.assertEqual(stats['azurirano'], 1)
        self.assertEqual(stats['kreirano'], 0)
        self.assertEqual(Product.objects.filter(odoo_template_id=501).count(), 1)
        product.refresh_from_db()
        self.assertEqual(product.naziv, 'Novi naziv')
        self.assertEqual(product.sifra, 'FOX-NEW')
        self.assertEqual(product.barkod, '999')
        self.assertEqual(product.cijena, Decimal('19.50'))
        self.assertEqual(Product.objects.count(), 1)

    def test_skips_unchanged_existing_product(self):
        product = Product.objects.create(
            naziv='Isti naziv',
            sifra='SAME-1',
            barkod='B1',
            cijena=Decimal('10.00'),
            odoo_template_id=444,
            magacin_sync_at=timezone.now(),
        )
        product.slika = 'products/vec-tu.jpg'
        product.save(update_fields=['slika'])
        before = product.azuriran
        client = FakeOdooClient([{
            'id': 444,
            'name': 'Isti naziv',
            'default_code': 'SAME-1',
            'barcode': 'B1',
            'list_price': '10.00',
            'qty_available': 3,
            'product_variant_ids': [444],
        }])
        stats = sync_catalog_chunk(client, [444], start=0, limit=10)
        self.assertEqual(stats['preskoceno'], 1)
        self.assertEqual(stats['azurirano'], 0)
        product.refresh_from_db()
        self.assertEqual(product.azuriran, before)

    def test_discover_phase_queues_missing_odoo_products(self):
        Product.objects.create(
            naziv='Već tu',
            sifra='HAS-1',
            cijena=Decimal('1.00'),
            odoo_template_id=10,
            magacin_sync_at=timezone.now(),
        )
        log = WarehouseSyncLog.objects.create(
            status=WarehouseSyncLog.Status.U_TOKU,
            izvor='Odoo',
        )

        class PageClient:
            def get_sale_template_ids_page(self, *, offset=0, limit=250):
                ids = [10, 88]
                return ids[offset:offset + limit]  # 2 < 300 → discover gotov

        job = {
            'log_id': log.pk,
            'started': time.time(),
            'phase': 'discover',
            'discovered_ids': [],
            'discover_offset': 0,
            'changed_ids': [],
            'incremental': True,
        }
        with patch('EcommerceApp.odoo_client.OdooClient.from_settings', return_value=PageClient()):
            job = run_sync_chunk(job)
        self.assertEqual(job['phase'], 'catalog')
        self.assertEqual(job['template_ids'], [88])

    def test_does_not_reuse_product_with_other_odoo_id(self):
        Product.objects.create(
            naziv='Drugi artikal',
            sifra='SAME-CODE',
            cijena=Decimal('1.00'),
            odoo_template_id=10,
        )
        client = FakeOdooClient([{
            'id': 88,
            'name': 'Novi iz Odoo',
            'default_code': 'SAME-CODE',
            'barcode': '',
            'list_price': '2.00',
            'qty_available': 1,
            'product_variant_ids': [88],
        }])
        stats = sync_catalog_chunk(client, [88], start=0, limit=10)
        self.assertEqual(stats['kreirano'], 0)
        self.assertEqual(stats['preskoceno'], 1)
        self.assertEqual(Product.objects.filter(odoo_template_id=10).count(), 1)
        self.assertEqual(Product.objects.filter(odoo_template_id=88).count(), 0)
        self.assertEqual(Product.objects.count(), 1)

    def test_skips_create_when_name_already_exists(self):
        Product.objects.create(
            naziv='Gift Card',
            sifra='GC-1',
            cijena=Decimal('1.00'),
            odoo_template_id=10,
        )
        client = FakeOdooClient([{
            'id': 88,
            'name': 'gift card',
            'default_code': 'GC-NEW',
            'barcode': '',
            'list_price': '2.00',
            'qty_available': 0,
            'product_variant_ids': [88],
        }])
        stats = sync_catalog_chunk(client, [88], start=0, limit=10)
        self.assertEqual(stats['kreirano'], 0)
        self.assertEqual(stats['preskoceno'], 1)
        self.assertEqual(Product.objects.filter(naziv__iexact='gift card').count(), 1)

    def test_cleanup_deletes_duplicate_names(self):
        from EcommerceApp.magacin import cleanup_duplicate_identities

        keep = Product.objects.create(
            naziv='Gift Card',
            sifra='GC-KEEP',
            barkod='111',
            cijena=Decimal('1.00'),
            odoo_template_id=10,
            stanje=2,
        )
        extra = Product.objects.create(
            naziv='gift card',
            sifra='ODOO-T88',
            cijena=Decimal('1.00'),
            odoo_template_id=88,
            stanje=0,
        )
        result = cleanup_duplicate_identities()
        self.assertEqual(len(result['obrisano']), 1)
        self.assertEqual(result['obrisano'][0]['pk'], extra.pk)
        self.assertTrue(Product.objects.filter(pk=keep.pk).exists())
        self.assertFalse(Product.objects.filter(pk=extra.pk).exists())

    def test_variation_other_odoo_id_does_not_block_new_product(self):
        parent = Product.objects.create(
            naziv='Parent',
            sifra='PAR-88',
            cijena=Decimal('1.00'),
            odoo_template_id=10,
        )
        ProductVariation.objects.create(
            artikal=parent,
            naziv='var',
            sifra='PAR-88-V',
            odoo_template_id=88,
        )
        client = FakeOdooClient([{
            'id': 88,
            'name': 'Treba novi artikal',
            'default_code': 'NEW-88-T',
            'barcode': '',
            'list_price': '3.00',
            'qty_available': 0,
            'product_variant_ids': [88],
        }])
        stats = sync_catalog_chunk(client, [88], start=0, limit=10)
        self.assertEqual(stats['kreirano'], 1)
        self.assertEqual(Product.objects.filter(odoo_template_id=88).count(), 1)
        self.assertEqual(Product.objects.filter(odoo_template_id=10).count(), 1)

    def test_persist_and_load_running_sync_job(self):
        from EcommerceApp.magacin import load_running_sync_job, persist_sync_job

        log = WarehouseSyncLog.objects.create(
            status=WarehouseSyncLog.Status.U_TOKU,
            izvor='Odoo',
        )
        job = {
            'log_id': log.pk,
            'phase': 'catalog',
            'template_ids': [88, 99],
            'position': 1,
            'done': False,
        }
        persist_sync_job(job)
        loaded = load_running_sync_job()
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded['template_ids'], [88, 99])
        self.assertEqual(loaded['position'], 1)
        job['done'] = True
        persist_sync_job(job)
        self.assertIsNone(load_running_sync_job())

    def test_creates_unknown_odoo_product(self):
        client = FakeOdooClient([{
            'id': 777,
            'name': 'Novi iz Odoo',
            'default_code': 'EMPTY-1',
            'barcode': 'B777',
            'list_price': '5.00',
            'qty_available': 0,
            'product_variant_ids': [777],
        }])
        stats = sync_catalog_chunk(client, [777], start=0, limit=10)
        self.assertEqual(stats['kreirano'], 1)
        self.assertEqual(stats['preskoceno'], 0)
        product = Product.objects.get(odoo_template_id=777)
        self.assertEqual(product.naziv, 'Novi iz Odoo')
        self.assertEqual(product.sifra, 'EMPTY-1')
        self.assertEqual(product.barkod, 'B777')
        self.assertIsNotNone(product.magacin_sync_at)
        self.assertEqual(Product.objects.count(), 1)

    def test_does_not_duplicate_when_creating_existing_odoo_id(self):
        Product.objects.create(
            naziv='Već tu',
            sifra='EMPTY-1',
            cijena=Decimal('5.00'),
            odoo_template_id=777,
        )
        client = FakeOdooClient([{
            'id': 777,
            'name': 'Već tu',
            'default_code': 'EMPTY-1',
            'barcode': '',
            'list_price': '5.00',
            'qty_available': 2,
            'product_variant_ids': [777],
        }])
        stats = sync_catalog_chunk(client, [777], start=0, limit=10)
        self.assertEqual(stats['kreirano'], 0)
        self.assertEqual(Product.objects.filter(odoo_template_id=777).count(), 1)

    def test_creates_variation_without_normalized_null(self):
        product = Product.objects.create(
            naziv='Parent',
            sifra='PAR-1',
            cijena=Decimal('1.00'),
            odoo_template_id=900,
        )
        variation = ProductVariation.objects.create(
            artikal=product,
            naziv='0.30mm',
            sifra='PAR-1-030',
            odoo_variant_id=901,
        )
        self.assertEqual(variation.naziv_normalized, '0.30mm')
        self.assertEqual(variation.sifra_normalized, 'par-1-030')

    def test_odoo_variant_id_maps_to_template_product(self):
        product = Product.objects.create(
            naziv='Fox braid',
            sifra='FOX-V',
            cijena=Decimal('10.00'),
            odoo_template_id=501,
            magacin_sync_at=timezone.now(),
        )
        mapping = _odoo_id_to_local([9001], variant_to_template={9001: 501})
        self.assertIn(9001, mapping)
        self.assertEqual(mapping[9001][0].pk, product.pk)
        self.assertIsNone(mapping[9001][1])

    def test_odoo_template_id_is_not_treated_as_variant_id(self):
        shorts = Product.objects.create(
            naziv='CFX343 FOX LW Combat Short Khaki XXL',
            sifra='ODOO-T5117',
            cijena=Decimal('79.90'),
            odoo_template_id=5117,
            magacin_sync_at=timezone.now(),
        )
        feeder = Product.objects.create(
            naziv='AS221 feeder 60gr',
            sifra='4626',
            cijena=Decimal('3.00'),
            odoo_template_id=5080,
            magacin_sync_at=timezone.now(),
        )
        mapping = _odoo_id_to_local(
            [5117],
            variant_to_template={5117: 5080},
        )
        self.assertEqual(mapping[5117][0].pk, feeder.pk)
        self.assertNotEqual(mapping[5117][0].pk, shorts.pk)
        unmapped = _odoo_id_to_local([5117])
        self.assertNotIn(5117, unmapped)

    def test_quant_sync_zeros_stale_odoo_stock(self):
        product = Product.objects.create(
            naziv='CFX343 FOX LW Combat Short Khaki XXL',
            sifra='ODOO-T5117',
            cijena=Decimal('79.90'),
            odoo_template_id=5117,
            stanje=150,
            na_stanju=True,
            magacin_sync_at=timezone.now(),
        )
        loc = WarehouseLocation.objects.create(
            sifra='Magacin',
            naziv='Magacin',
            odoo_location_id=327,
            odoo_location_path='WH/VP/Magacin',
        )
        WarehouseStock.objects.create(product=product, location=loc, kolicina=150)
        local = WarehouseLocation.objects.create(sifra='RUCNA', naziv='Ručna polica')
        WarehouseStock.objects.create(product=product, location=local, kolicina=2)

        class QuantClient:
            def get_internal_stock_quants(self, product_ids, *, for_packing=False):
                return {}

            def get_template_ids_for_variants(self, variant_ids):
                return {5154: 5117}

        updated, touched = _apply_quant_batch(
            QuantClient(), [5154], variant_to_template={5154: 5117},
        )
        self.assertIn(product.pk, touched)
        self.assertGreaterEqual(updated, 1)
        product.refresh_from_db()
        self.assertEqual(
            WarehouseStock.objects.get(product=product, location=loc).kolicina,
            0,
        )
        self.assertEqual(
            WarehouseStock.objects.get(product=product, location=local).kolicina,
            2,
        )
        self.assertEqual(product.stanje, 2)
        self.assertTrue(product.na_stanju)

    def test_updates_imported_product_by_sifra_without_duplicate(self):
        product = Product.objects.create(
            naziv='Stari import',
            sifra='NEW-88',
            cijena=Decimal('3.00'),
        )
        client = FakeOdooClient([{
            'id': 888,
            'name': 'Novi na stanju',
            'default_code': 'NEW-88',
            'barcode': 'BAR-88',
            'list_price': '12.00',
            'qty_available': 6,
            'product_variant_ids': [888],
        }])
        stats = sync_catalog_chunk(client, [888], start=0, limit=10)
        self.assertEqual(stats['kreirano'], 0)
        self.assertEqual(stats['azurirano'], 1)
        self.assertEqual(Product.objects.count(), 1)
        product.refresh_from_db()
        self.assertEqual(product.naziv, 'Novi na stanju')
        self.assertEqual(product.sifra, 'NEW-88')
        self.assertEqual(product.barkod, 'BAR-88')
        self.assertEqual(product.odoo_template_id, 888)

    def test_site_odoo_products_are_magacin_without_duplicate(self):
        site = Product.objects.create(
            naziv='Fox braid shop',
            sifra='FOX-SHOP',
            cijena=Decimal('11.00'),
            odoo_template_id=1201,
        )
        web_only = Product.objects.create(
            naziv='Samo web',
            sifra='WEB-ONLY',
            cijena=Decimal('2.00'),
        )
        self.assertIn(site, magacin_products_qs())
        self.assertNotIn(web_only, magacin_products_qs())
        self.assertEqual(local_odoo_template_ids(), [1201])
        marked = attach_site_odoo_products_to_magacin()
        self.assertEqual(marked, 1)
        site.refresh_from_db()
        self.assertIsNotNone(site.magacin_sync_at)
        client = FakeOdooClient([{
            'id': 1201,
            'name': 'Fox braid shop',
            'default_code': 'FOX-SHOP',
            'barcode': '',
            'list_price': '11.00',
            'qty_available': 4,
            'product_variant_ids': [1201],
        }, {
            'id': 9999,
            'name': 'Nepoznat u shopu',
            'default_code': 'GHOST-9',
            'barcode': '',
            'list_price': '1.00',
            'qty_available': 9,
            'product_variant_ids': [9999],
        }])
        stats = sync_catalog_chunk(client, [1201, 9999], start=0, limit=10)
        self.assertEqual(stats['kreirano'], 1)
        self.assertEqual(Product.objects.filter(sifra='GHOST-9').count(), 1)
        ghost = Product.objects.get(odoo_template_id=9999)
        self.assertEqual(ghost.naziv, 'Nepoznat u shopu')
        self.assertEqual(Product.objects.filter(odoo_template_id=1201).count(), 1)
        self.assertEqual(Product.objects.count(), 3)

    def test_skips_image_download_when_product_already_has_image(self):
        product = Product.objects.create(
            naziv='Ima sliku',
            sifra='IMG-1',
            cijena=Decimal('3.00'),
            odoo_template_id=333,
        )
        product.slika = 'products/vec-tu.jpg'
        product.save(update_fields=['slika'])
        client = FakeOdooClient([{
            'id': 333,
            'name': 'Ima sliku',
            'default_code': 'IMG-1',
            'barcode': '',
            'list_price': '3.00',
            'qty_available': 2,
            'product_variant_ids': [333],
        }])
        sync_catalog_chunk(client, [333], start=0, limit=10)
        product.refresh_from_db()
        self.assertEqual(product.slika.name, 'products/vec-tu.jpg')
        self.assertEqual(client.image_requests, [])


class MagacinViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser('admin', 'admin@example.com', 'pass')
        self.product = Product.objects.create(
            naziv='Test braid', sifra='TST-1', cijena=Decimal('10.00'),
            stanje=8,
            na_stanju=True,
            magacin_sync_at=timezone.now(),
        )
        self.zero = Product.objects.create(
            naziv='Prazan lager', sifra='ZERO-1', cijena=Decimal('2.00'),
            stanje=0,
            na_stanju=False,
            magacin_sync_at=timezone.now(),
        )
        self.unsynced = Product.objects.create(
            naziv='Samo web artikal', sifra='WEB-99', cijena=Decimal('5.00'),
        )
        loc = WarehouseLocation.objects.create(sifra='T-1', naziv='Test loc')
        apply_movement(product=self.product, location=loc, tip='prijem', kolicina=8)

    def test_admin_panel_has_magacin_not_carts(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse('staff_admin_panel'))
        self.assertContains(response, 'Magacin')
        self.assertContains(response, reverse('staff_magacin'))
        self.assertNotContains(response, 'Aktivne korpe')

    def test_artikli_and_detail_ok(self):
        self.client.force_login(self.user)
        list_res = self.client.get(reverse('staff_magacin_artikli'))
        self.assertEqual(list_res.status_code, 200)
        self.assertContains(list_res, 'mgArticleScanBtn')
        self.assertContains(list_res, 'Zadnje izmjene količina')
        self.assertNotContains(list_res, 'class="mg-product-row"')
        self.assertContains(list_res, 'Test braid')
        detail = self.client.get(reverse('staff_magacin_artikal', args=[self.product.pk]))
        self.assertEqual(detail.status_code, 200)
        self.assertContains(detail, 'Zalihe po lokacijama')
        self.assertContains(detail, 'Istorija kretanja')
        self.assertContains(detail, reverse('home'))
        self.assertContains(detail, 'class="header"')
        self.assertContains(detail, 'Nazad na rezultate')
        self.assertNotContains(detail, 'id="mgArticleSearch"')
        self.assertNotContains(detail, 'class="mg-top"')
        self.assertContains(detail, 'Izmijeni artikal')
        self.assertContains(detail, 'Zalihe')
        self.assertContains(detail, 'Skini sa stanja')
        self.assertContains(detail, '>Cijena<')
        self.assertContains(detail, 'mg-zalihe-panel')
        self.assertContains(detail, '10.00 KM')
        self.assertContains(detail, 'Prenos u MP')
        self.assertContains(detail, 'Transfer')
        self.assertContains(detail, 'Dodaj u novu lokaciju')
        self.assertNotContains(detail, 'Ažuriraj postojeću')
        self.assertNotContains(detail, 'Ažuriraj lokacije')
        self.assertNotContains(detail, 'Dodaj na novu')
        self.assertContains(detail, reverse('staff_magacin_artikal_izmjena', args=[self.product.pk]))
        self.assertContains(detail, 'Promjene cijena i marže')
        self.assertNotContains(detail, 'Osnovne informacije')
        self.assertNotContains(detail, 'Sačuvaj info')
        self.assertContains(detail, 'T-1')
        self.assertNotContains(detail, 'T-1 (Test loc)')
        self.assertContains(detail, 'mg-hero is-stock')
        self.assertContains(detail, 'mg-phone-artikal')
        self.assertContains(detail, 'mg-phone-price')
        out_page = self.client.get(reverse('staff_magacin_artikal', args=[self.zero.pk]))
        self.assertContains(out_page, 'mg-hero is-out')
        self.assertNotContains(out_page, 'mg-hero is-stock')

    def test_brzi_unos_same_as_admin_flow(self):
        self.client.force_login(self.user)
        nav = self.client.get(reverse('staff_magacin_artikli'))
        self.assertContains(nav, 'Brzi unos / Aktivacija')
        self.assertContains(nav, reverse('staff_magacin_brzi_unos'))

        page = self.client.get(reverse('staff_magacin_brzi_unos'))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'Skeniraj barkod kamerom')
        self.assertContains(page, 'Šifra, barkod ili naziv')

        found = self.client.get(reverse('staff_magacin_brzi_unos'), {'q': 'TST-1'})
        self.assertRedirects(
            found,
            reverse('staff_magacin_brzi_unos_aktivacija', args=[self.product.pk]),
        )
        act = self.client.get(reverse('staff_magacin_brzi_unos_aktivacija', args=[self.product.pk]))
        self.assertEqual(act.status_code, 200)
        self.assertContains(act, 'Aktiviraj artikal')
        self.assertContains(act, 'Skini sa stanja')
        self.assertContains(act, 'Test braid')
        self.assertContains(act, 'Google slike')
        self.assertContains(act, 'Otvori u ChatGPT-u')

    def test_product_edit_updates_fields(self):
        self.client.force_login(self.user)
        page = self.client.get(reverse('staff_magacin_artikal_izmjena', args=[self.product.pk]))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'Izmjena artikla')
        self.assertContains(page, 'data-mg-scan-target="id_barkod"')
        saved = self.client.post(reverse('staff_magacin_artikal_izmjena', args=[self.product.pk]), {
            'naziv': 'Novi naziv braid',
            'sifra': 'TST-1',
            'barkod': '123456',
            'opis': 'Novi opis',
            'cijena': '19.50',
            'aktivan': '1',
            'prikazi_na_pocetnoj': '1',
            'jedinica_mjere': 'kom',
            'min_zaliha': '2',
        })
        self.assertEqual(saved.status_code, 302)
        self.product.refresh_from_db()
        self.assertEqual(self.product.naziv, 'Novi naziv braid')
        self.assertEqual(self.product.barkod, '123456')
        self.assertEqual(self.product.opis, 'Novi opis')
        self.assertEqual(str(self.product.cijena), '19.50')
        meta = self.product.magacin_meta
        self.assertEqual(meta.min_zaliha, 2)

    def test_default_list_hides_zero_stock(self):
        self.client.force_login(self.user)
        listed = self.client.get(reverse('staff_magacin_artikli'))
        self.assertNotContains(listed, 'class="mg-product-row"')
        found = self.client.get(reverse('staff_magacin_artikli'), {'pretraga': 'braid'})
        self.assertContains(found, 'Test braid')
        self.assertNotContains(found, 'Prazan lager')
        with_zero = self.client.get(reverse('staff_magacin_artikli'), {
            'pretraga': 'Prazan', 'bez_zalihe': '1',
        })
        self.assertContains(with_zero, 'Prazan lager')
        self.assertContains(with_zero, 'Prikaži i bez zalihe')
        self.assertContains(with_zero, 'is-out')
        self.assertContains(with_zero, 'Nije na stanju')
        shop_only_qty = Product.objects.create(
            naziv='Samo shop kolicina',
            sifra='SHOP-Q',
            cijena=Decimal('3.00'),
            stanje=20,
            na_stanju=True,
            magacin_sync_at=timezone.now(),
        )
        listed2 = self.client.get(reverse('staff_magacin_artikli'))
        self.assertNotContains(listed2, 'Samo shop kolicina')
        shown = self.client.get(reverse('staff_magacin_artikli'), {
            'pretraga': 'Samo shop', 'bez_zalihe': '1',
        })
        self.assertContains(shown, 'Samo shop kolicina')
        miss = self.client.get(reverse('staff_magacin_artikli'), {'pretraga': 'ZERO-1'})
        self.assertEqual(miss.status_code, 302)
        self.assertIn(f'/nalog/magacin/artikli/{self.zero.pk}/', miss['Location'])

    def test_search_only_synced_magacin_articles(self):
        self.client.force_login(self.user)
        listed = self.client.get(reverse('staff_magacin_artikli'))
        self.assertNotContains(listed, 'class="mg-product-row"')
        self.assertNotContains(listed, 'Samo web artikal')
        missed = self.client.get(reverse('staff_magacin_artikli'), {'pretraga': 'WEB-99'})
        self.assertEqual(missed.status_code, 200)
        self.assertNotContains(missed, 'Samo web artikal')
        hidden = self.client.get(reverse('staff_magacin_artikal', args=[self.unsynced.pk]))
        self.assertEqual(hidden.status_code, 404)

    def test_search_exact_sifra_redirects(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse('staff_magacin_artikli'), {'pretraga': 'TST-1'})
        self.assertEqual(response.status_code, 302)
        self.assertIn(f'/nalog/magacin/artikli/{self.product.pk}/', response['Location'])
        self.assertIn('pretraga=TST-1', response['Location'])
        self.assertNotIn('?q=', response['Location'])
        self.assertNotIn('&q=', response['Location'])
        by_name = self.client.get(reverse('staff_magacin_artikli'), {'pretraga': self.product.naziv})
        self.assertEqual(by_name.status_code, 302)
        self.assertIn(f'/nalog/magacin/artikli/{self.product.pk}/', by_name['Location'])

    def test_magacin_search_does_not_fill_site_search(self):
        self.client.force_login(self.user)
        page = self.client.get(reverse('staff_magacin_artikli'), {'pretraga': 'braid'})
        self.assertEqual(page.status_code, 200)
        html = page.content.decode()
        self.assertIn('id="mgArticleSearch"', html)
        self.assertIn('name="pretraga"', html)
        self.assertIn('value="braid"', html)
        self.assertIn('id="searchInput" value=""', html)
        self.assertNotIn('id="searchInput" value="braid"', html)
        self.assertEqual(page.context['search_query'], '')
        self.assertEqual(page.context['magacin_search'], 'braid')
        self.assertContains(page, 'STANJE')
        self.assertContains(page, 'Na stanju')
        self.assertNotContains(page, 'LOKACIJE I DOSTUPNO')
        self.assertNotContains(page, '<b>T-1</b>')
        self.assertContains(page, 'mg-loc-inline')
        self.assertNotContains(page, 'is-stock')

    def test_other_sections_ok(self):
        self.client.force_login(self.user)
        for name in (
            'staff_magacin_pregled',
            'staff_magacin_brzi_unos',
            'staff_magacin_lokacije',
            'staff_magacin_zalihe',
            'staff_magacin_transferi',
            'staff_magacin_dobavljaci',
            'staff_magacin_uvoz',
            'staff_magacin_nivelacije',
            'staff_magacin_pakuj',
            'staff_magacin_izvjestaji',
            'staff_magacin_popis',
            'staff_magacin_vp_narudzba',
        ):
            response = self.client.get(reverse(name))
            self.assertEqual(response.status_code, 200, name)

    def test_popis_persists_until_delete_or_finish(self):
        self.client.force_login(self.user)
        start = self.client.post(reverse('staff_magacin_popis'), {'action': 'novi'})
        self.assertEqual(start.status_code, 302)
        popis = MagacinPopis.objects.get()
        self.assertEqual(popis.status, MagacinPopis.Status.U_TOKU)
        added = self.client.post(reverse('staff_magacin_popis'), {
            'action': 'dodaj',
            'product_id': self.product.pk,
            'kolicina': '3',
        })
        self.assertEqual(added.status_code, 302)
        again = self.client.get(reverse('staff_magacin_popis'))
        self.assertContains(again, self.product.naziv)
        self.assertContains(again, '3')
        self.assertEqual(MagacinPopis.objects.count(), 1)
        printed = self.client.get(reverse('staff_magacin_popis_stampa'))
        self.assertContains(printed, self.product.naziv)
        self.assertContains(printed, self.product.sifra)
        self.assertContains(printed, '3')
        finished = self.client.post(reverse('staff_magacin_popis'), {'action': 'zavrsi'})
        self.assertEqual(finished.status_code, 302)
        popis.refresh_from_db()
        self.assertEqual(popis.status, MagacinPopis.Status.ZAVRSEN)
        self.client.post(reverse('staff_magacin_popis'), {'action': 'novi'})
        self.assertEqual(MagacinPopis.objects.filter(status=MagacinPopis.Status.U_TOKU).count(), 1)
        live = MagacinPopis.objects.get(status=MagacinPopis.Status.U_TOKU)
        self.client.post(reverse('staff_magacin_popis'), {'action': 'obrisi'})
        self.assertFalse(MagacinPopis.objects.filter(pk=live.pk).exists())

    def test_vp_order_persists_customer_and_vp_price(self):
        self.client.force_login(self.user)
        listed = self.client.get(reverse('staff_magacin_narudzbe'))
        self.assertContains(listed, 'VP narudžbe')
        self.assertContains(listed, reverse('staff_magacin_vp_narudzba'))
        start = self.client.post(reverse('staff_magacin_vp_narudzba'), {'action': 'novi'})
        self.assertEqual(start.status_code, 302)
        draft = MagacinVpNarudzba.objects.get()
        self.assertEqual(draft.status, MagacinVpNarudzba.Status.U_TOKU)
        customer = WarehouseCustomer.objects.create(
            ime_prezime='VP Kupac', telefon='061111222', grad='Sarajevo',
        )
        other = WarehouseCustomer.objects.create(
            ime_prezime='Drugi Kupac', telefon='061333444', grad='Mostar',
        )
        set_cust = self.client.post(reverse('staff_magacin_vp_narudzba'), {
            'action': 'kupac',
            'customer_id': customer.pk,
        })
        self.assertEqual(set_cust.status_code, 302)
        added = self.client.post(reverse('staff_magacin_vp_narudzba'), {
            'action': 'dodaj',
            'product_id': self.product.pk,
            'kolicina': '2',
        })
        self.assertEqual(added.status_code, 302)
        change = self.client.post(reverse('staff_magacin_vp_narudzba'), {
            'action': 'kupac',
            'customer_id': other.pk,
        })
        self.assertEqual(change.status_code, 302)
        page = self.client.get(reverse('staff_magacin_vp_narudzba'))
        self.assertContains(page, 'Drugi Kupac')
        self.assertContains(page, 'Test braid')
        self.assertContains(page, '2')
        self.assertContains(page, '7.25 KM')
        self.assertEqual(MagacinVpNarudzba.objects.count(), 1)
        draft.refresh_from_db()
        line = draft.stavke.get()
        self.assertEqual(line.kolicina, 2)
        self.assertEqual(line.cijena, Decimal('7.25'))
        self.assertEqual(line.mpc, Decimal('10.00'))
        self.assertEqual(draft.ime_prezime, 'Drugi Kupac')
        self.assertContains(page, 'name="kolicina"')
        self.assertContains(page, 'value="ukloni"')
        self.assertContains(page, 'id="vpQtyModal"')
        self.assertContains(page, 'id="vpScanBtn"')
        ajax = self.client.post(
            reverse('staff_magacin_vp_narudzba'),
            {'action': 'kolicina', 'stavka_id': line.pk, 'kolicina': '4'},
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )
        self.assertEqual(ajax.status_code, 200)
        self.assertEqual(ajax.json()['stavke'][0]['kolicina'], 4)
        line.refresh_from_db()
        self.assertEqual(line.kolicina, 4)
        qty = self.client.post(reverse('staff_magacin_vp_narudzba'), {
            'action': 'kolicina',
            'stavka_id': line.pk,
            'kolicina': '5',
        })
        self.assertEqual(qty.status_code, 302)
        line.refresh_from_db()
        self.assertEqual(line.kolicina, 5)
        extra = Product.objects.create(
            naziv='Drugi artikal', sifra='VP-2', cijena=Decimal('13.80'),
            stanje=4, na_stanju=True, magacin_sync_at=timezone.now(),
        )
        loc = WarehouseLocation.objects.get(sifra='T-1')
        apply_movement(product=extra, location=loc, tip='prijem', kolicina=4)
        self.client.post(reverse('staff_magacin_vp_narudzba'), {
            'action': 'dodaj',
            'product_id': extra.pk,
            'kolicina': '1',
        })
        extra_line = draft.stavke.get(product=extra)
        removed = self.client.post(reverse('staff_magacin_vp_narudzba'), {
            'action': 'ukloni',
            'stavka_id': extra_line.pk,
        })
        self.assertEqual(removed.status_code, 302)
        self.assertFalse(draft.stavke.filter(pk=extra_line.pk).exists())
        self.assertEqual(draft.stavke.count(), 1)
        finished = self.client.post(reverse('staff_magacin_vp_narudzba'), {'action': 'zavrsi'})
        self.assertEqual(finished.status_code, 302)
        self.assertEqual(finished['Location'], reverse('staff_magacin_narudzbe'))
        draft.refresh_from_db()
        self.assertEqual(draft.status, MagacinVpNarudzba.Status.ZAVRSENA)
        order = Order.objects.get(pk=draft.order_id)
        self.assertEqual(order.ime_prezime, 'Drugi Kupac')
        self.assertEqual(order.izvor, Order.Izvor.MAGACIN)
        item = order.stavke.get()
        self.assertEqual(item.kolicina, 5)
        self.assertEqual(item.cijena, Decimal('7.25'))
        self.assertEqual(order.ukupno, Decimal('36.25'))
        picking = self.client.get(reverse('staff_magacin_pakuj'))
        self.assertContains(picking, 'VP narudžbe')
        self.assertContains(picking, 'Drugi Kupac')
        self.assertContains(picking, reverse('staff_magacin_pakuj_detail', args=[order.broj]))
        self.client.post(reverse('staff_magacin_vp_narudzba'), {'action': 'novi'})
        live = MagacinVpNarudzba.objects.get(status=MagacinVpNarudzba.Status.U_TOKU)
        self.client.post(reverse('staff_magacin_vp_narudzba'), {'action': 'obrisi'})
        self.assertFalse(MagacinVpNarudzba.objects.filter(pk=live.pk).exists())

    def test_vp_validate_stays_until_print_packed(self):
        self.client.force_login(self.user)
        self.client.post(reverse('staff_magacin_vp_narudzba'), {'action': 'novi'})
        customer = WarehouseCustomer.objects.create(
            ime_prezime='Print Kupac', telefon='061999888',
        )
        self.client.post(reverse('staff_magacin_vp_narudzba'), {
            'action': 'kupac', 'customer_id': customer.pk,
        })
        self.client.post(reverse('staff_magacin_vp_narudzba'), {
            'action': 'dodaj', 'product_id': self.product.pk, 'kolicina': '1',
        })
        finished = self.client.post(reverse('staff_magacin_vp_narudzba'), {'action': 'zavrsi'})
        self.assertEqual(finished.status_code, 302)
        order = Order.objects.get(ime_prezime='Print Kupac')
        picking = self.client.get(reverse('staff_magacin_pakuj'))
        self.assertContains(picking, 'Print Kupac')
        self.assertNotContains(picking, 'Štampaj zapakovano')
        self.assertEqual(len(picking.context['vp_orders']), 1)
        self.assertEqual(picking.context['vp_orders'][0].vp_stavki, 1)
        self.assertEqual(picking.context['new_pack_orders_count'], 1)
        self.assertContains(picking, '1 stavk')
        validated = self.client.post(
            reverse('staff_magacin_pakuj_detail', args=[order.broj]),
            {'action': 'validiraj'},
        )
        self.assertRedirects(validated, reverse('staff_magacin_pakuj'))
        order.refresh_from_db()
        self.assertEqual(order.lager_status, Order.LagerStatus.VALIDIRANO)
        self.assertFalse(order.zapakovana)
        self.assertNotEqual(order.status, Order.Status.ZAVRSENA)
        still = self.client.get(reverse('staff_magacin_pakuj'))
        self.assertContains(still, 'Print Kupac')
        self.assertContains(still, 'Štampaj zapakovano')
        self.assertEqual(len(still.context['vp_orders']), 1)
        self.assertEqual(still.context['new_pack_orders_count'], 1)
        self.assertContains(still, reverse('staff_magacin_pakuj_stampaj_zapakovano', args=[order.broj]))
        open_list = [row.broj for row in self.client.get(reverse('staff_magacin_narudzbe')).context['orders']]
        self.assertNotIn(order.broj, open_list)
        validated_list = [
            row.broj
            for row in self.client.get(reverse('staff_magacin_narudzbe'), {'validirane': '1'}).context['orders']
        ]
        self.assertNotIn(order.broj, validated_list)
        printed = self.client.get(reverse('staff_magacin_pakuj_stampaj_zapakovano', args=[order.broj]))
        self.assertEqual(printed.status_code, 302)
        self.assertIn(reverse('staff_magacin_narudzbe_stampa'), printed['Location'])
        order.refresh_from_db()
        self.assertTrue(order.zapakovana)
        self.assertEqual(order.status, Order.Status.ZAVRSENA)
        gone = self.client.get(reverse('staff_magacin_pakuj'))
        self.assertNotContains(gone, 'Print Kupac')
        self.assertNotContains(gone, 'Štampaj zapakovano')
        self.assertEqual(gone.context['new_pack_orders_count'], 0)
        shown = [
            row.broj
            for row in self.client.get(reverse('staff_magacin_narudzbe'), {'validirane': '1'}).context['orders']
        ]
        self.assertIn(order.broj, shown)

    def test_picking_can_add_and_remove_item_and_updates_invoice(self):
        self.client.force_login(self.user)
        extra = Product.objects.create(
            naziv='Drugi artikal', sifra='ADD-2', cijena=Decimal('13.80'),
            stanje=4, na_stanju=True, magacin_sync_at=timezone.now(),
        )
        loc = WarehouseLocation.objects.get(sifra='T-1')
        apply_movement(product=extra, location=loc, tip='prijem', kolicina=4)
        created = self.client.post(reverse('staff_magacin_narudzba_nova'), {
            'ime_prezime': 'Edit Kupac',
            'telefon': '061555666',
            'product_id': [str(self.product.pk)],
            'variation_id': [''],
            'kolicina': ['1'],
            'mp_ok': ['0'],
        })
        self.assertEqual(created.status_code, 302)
        order = Order.objects.get(ime_prezime='Edit Kupac')
        first_total = order.ukupno
        page = self.client.get(reverse('staff_magacin_pakuj_detail', args=[order.broj]))
        self.assertContains(page, 'Izmijeni')
        self.assertContains(page, 'Test braid')
        added = self.client.post(reverse('staff_magacin_pakuj_detail', args=[order.broj]), {
            'action': 'dodaj',
            'product_id': extra.pk,
            'kolicina': '2',
        })
        self.assertRedirects(added, reverse('staff_magacin_pakuj_detail', args=[order.broj]))
        order.refresh_from_db()
        self.assertEqual(order.stavke.count(), 2)
        extra_item = order.stavke.get(artikal=extra)
        self.assertEqual(extra_item.kolicina, 2)
        self.assertGreater(order.ukupno, first_total)
        from .views import _order_print_job
        job = _order_print_job(order)
        names = [row['naziv'] for row in job['stavke']]
        self.assertTrue(any('Drugi artikal' in name for name in names))
        self.assertEqual(job['summary']['ukupno'], order.ukupno)
        removed = self.client.post(reverse('staff_magacin_pakuj_detail', args=[order.broj]), {
            'action': 'ukloni',
            'stavka_id': extra_item.pk,
        })
        self.assertRedirects(removed, reverse('staff_magacin_pakuj_detail', args=[order.broj]))
        order.refresh_from_db()
        self.assertEqual(order.stavke.count(), 1)
        self.assertEqual(order.stavke.get().artikal_id, self.product.pk)
        job2 = _order_print_job(order)
        names2 = [row['naziv'] for row in job2['stavke']]
        self.assertFalse(any('Drugi artikal' in name for name in names2))

    def test_vp_picking_add_uses_vp_price_on_invoice(self):
        self.client.force_login(self.user)
        extra = Product.objects.create(
            naziv='VP dodatak', sifra='VP-ADD', cijena=Decimal('13.80'),
            stanje=4, na_stanju=True, magacin_sync_at=timezone.now(),
        )
        loc = WarehouseLocation.objects.get(sifra='T-1')
        apply_movement(product=extra, location=loc, tip='prijem', kolicina=4)
        self.client.post(reverse('staff_magacin_vp_narudzba'), {'action': 'novi'})
        customer = WarehouseCustomer.objects.create(
            ime_prezime='VP Edit', telefon='061777888',
        )
        self.client.post(reverse('staff_magacin_vp_narudzba'), {
            'action': 'kupac', 'customer_id': customer.pk,
        })
        self.client.post(reverse('staff_magacin_vp_narudzba'), {
            'action': 'dodaj', 'product_id': self.product.pk, 'kolicina': '1',
        })
        self.client.post(reverse('staff_magacin_vp_narudzba'), {'action': 'zavrsi'})
        order = Order.objects.get(ime_prezime='VP Edit')
        added = self.client.post(reverse('staff_magacin_pakuj_detail', args=[order.broj]), {
            'action': 'dodaj',
            'product_id': extra.pk,
            'kolicina': '1',
        })
        self.assertRedirects(added, reverse('staff_magacin_pakuj_detail', args=[order.broj]))
        order.refresh_from_db()
        extra_item = order.stavke.get(artikal=extra)
        self.assertEqual(extra_item.cijena, Decimal('10.00'))
        self.assertEqual(order.dostava, Decimal('0.00'))
        self.assertEqual(order.ukupno, Decimal('17.25'))
        from .views import _order_print_job
        job = _order_print_job(order)
        self.assertEqual(job['summary']['dostava'], Decimal('0.00'))
        self.assertEqual(job['summary']['ukupno'], Decimal('17.25'))

    def test_vp_out_of_stock_asks_mp_like_regular(self):
        self.client.force_login(self.user)
        self.client.post(reverse('staff_magacin_vp_narudzba'), {'action': 'novi'})
        customer = WarehouseCustomer.objects.create(
            ime_prezime='MP Kupac', telefon='062000111',
        )
        self.client.post(reverse('staff_magacin_vp_narudzba'), {
            'action': 'kupac', 'customer_id': customer.pk,
        })
        blocked = self.client.post(
            reverse('staff_magacin_vp_narudzba'),
            {'action': 'dodaj', 'product_id': self.zero.pk, 'kolicina': '1'},
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )
        self.assertEqual(blocked.status_code, 409)
        self.assertTrue(blocked.json()['need_mp'])
        self.assertEqual(MagacinVpNarudzba.objects.get().stavke.count(), 0)
        added = self.client.post(
            reverse('staff_magacin_vp_narudzba'),
            {'action': 'dodaj', 'product_id': self.zero.pk, 'kolicina': '1', 'mp_ok': '1'},
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )
        self.assertEqual(added.status_code, 200)
        self.assertTrue(added.json()['ok'])
        self.assertTrue(added.json()['stavke'][0]['mp_ok'])
        finished = self.client.post(reverse('staff_magacin_vp_narudzba'), {'action': 'zavrsi'})
        self.assertEqual(finished.status_code, 302)
        order = Order.objects.get(ime_prezime='MP Kupac')
        self.assertIn('Maloprodaja', order.napomena)
        picking = self.client.get(reverse('staff_magacin_pakuj'))
        self.assertContains(picking, 'MP Kupac')
        self.assertContains(picking, 'Provjera MP')
        check = self.client.get(reverse('staff_magacin_pakuj_provjera'), {
            'narudzba': order.broj, 'next': 'pick',
        })
        self.assertEqual(check.status_code, 200)
        groups = check.context['groups']
        self.assertTrue(groups)
        confirmed = self.client.post(reverse('staff_magacin_pakuj_provjera'), {
            'group': groups[0]['key'],
            'action': 'ima',
            'narudzba': order.broj,
            'next': 'pick',
        })
        self.assertEqual(confirmed.status_code, 302)
        self.assertIn(reverse('staff_magacin_pakuj_detail', args=[order.broj]), confirmed['Location'])
        pick = self.client.get(reverse('staff_magacin_pakuj_detail', args=[order.broj]))
        self.assertEqual(pick.status_code, 200)
        queue = json.loads(pick.context['pick_queue_json'])
        self.assertTrue(queue)
        self.assertTrue(all(item.get('is_mp') for item in queue))
        self.assertTrue(all(item.get('loc') == 'MP' for item in queue))
        self.assertFalse(any((item.get('loc') or '').startswith('T-') for item in queue))
        self.assertContains(pick, 'id="pkLocKicker"')

    def test_sync_can_be_cancelled(self):
        self.client.force_login(self.user)
        log = WarehouseSyncLog.objects.create(
            status=WarehouseSyncLog.Status.U_TOKU,
            izvor='Odoo',
            poruka='Katalog: 20 / 400',
            artikala=12,
        )
        session = self.client.session
        session[MAGACIN_SYNC_SESSION_KEY] = {
            'log_id': log.pk,
            'started': timezone.now().timestamp(),
            'phase': 'catalog',
            'template_ids': [1, 2, 3],
            'position': 20,
            'artikala': 12,
            'azurirano': 5,
            'preskoceno': 15,
            'done': False,
        }
        session.save()
        listed = self.client.get(reverse('staff_magacin_artikli'))
        self.assertContains(listed, 'Prekini sync')
        self.assertContains(listed, 'mgSyncCancel')
        stopped = self.client.post(reverse('staff_magacin_sync'), {
            'action': 'cancel',
            'next': reverse('staff_magacin_artikli'),
        })
        self.assertEqual(stopped.status_code, 302)
        log.refresh_from_db()
        self.assertEqual(log.status, WarehouseSyncLog.Status.PREKINUT)
        self.assertIn('prekinut', log.poruka.lower())
        self.assertNotIn(MAGACIN_SYNC_SESSION_KEY, self.client.session)
        after = self.client.get(reverse('staff_magacin_artikli'))
        self.assertNotContains(after, 'mgSyncCancel')
        self.assertContains(after, 'Sinhronizacija je prekinuta')

    def test_pregled_shows_order_stats_and_chart(self):
        self.client.force_login(self.user)
        order = Order.objects.create(
            ime_prezime='Ana Ribić',
            telefon='061111111',
            email='ana@example.com',
            adresa='Test 1',
            grad='Sarajevo',
            ukupno=Decimal('30.00'),
            izvor=Order.Izvor.MAGACIN,
        )
        OrderItem.objects.create(
            narudzba=order, naziv='Test braid', cijena=Decimal('10.00'), kolicina=3,
            artikal=self.product,
        )
        page = self.client.get(reverse('staff_magacin_pregled'))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'Narudžbe — Danas')
        self.assertContains(page, '30.00 KM')
        self.assertContains(page, 'Prosječna cijena artikla')
        self.assertContains(page, 'mgPregledChart')
        self.assertContains(page, 'mg-chart-wrap')
        self.assertContains(page, 'mg-table-stack')
        self.assertContains(page, 'Zadnje izmjene artikala')
        self.assertContains(page, 'Verzija')
        self.assertContains(page, order.broj)
        month = self.client.get(reverse('staff_magacin_pregled'), {'period': 'month', 'graf': 'mjeseci'})
        self.assertContains(month, 'Ovaj mjesec')
        ranged = self.client.get(reverse('staff_magacin_pregled'), {
            'period': 'range',
            'from': timezone.localdate().isoformat(),
            'to': timezone.localdate().isoformat(),
            'graf': 'godine',
        })
        self.assertEqual(ranged.status_code, 200)
        self.assertContains(ranged, '30.00 KM')
        self.assertIn('godine', ranged.context['chart_json'])

    def test_izvjestaji_finds_order_by_name_phone_or_number(self):
        self.client.force_login(self.user)
        order = Order.objects.create(
            ime_prezime='Ana Ribić',
            telefon='061111111',
            email='ana@example.com',
            adresa='Test 1',
            grad='Sarajevo',
            ukupno=Decimal('20.00'),
            izvor=Order.Izvor.MAGACIN,
        )
        page = self.client.get(reverse('staff_magacin_izvjestaji'))
        self.assertContains(page, 'Pronađi narudžbu')
        self.assertNotContains(page, order.ime_prezime)
        by_name = self.client.get(reverse('staff_magacin_izvjestaji'), {'narudzba': 'Ana'})
        self.assertContains(by_name, order.broj)
        self.assertContains(by_name, 'Ana Ribić')
        by_phone = self.client.get(reverse('staff_magacin_izvjestaji'), {'narudzba': '061111111'})
        self.assertContains(by_phone, order.broj)
        by_broj = self.client.get(reverse('staff_magacin_izvjestaji'), {'narudzba': order.broj})
        self.assertContains(by_broj, reverse('staff_order_detail', args=[order.broj]))

    def test_transfer_insert_and_move(self):
        self.client.force_login(self.user)
        dest = WarehouseLocation.objects.create(sifra='T-2', naziv='Druga loc')
        src = WarehouseLocation.objects.get(sifra='T-1')
        page = self.client.get(reverse('staff_magacin_transferi'))
        self.assertContains(page, 'Ubaci u lokaciju')
        self.assertContains(page, 'Prenos iz lokacije u lokaciju')
        inserted = self.client.post(reverse('staff_magacin_transferi'), {
            'action': 'ubaci',
            'tab': 'ubaci',
            'location_id': str(src.pk),
            'product_id': [str(self.product.pk)],
            'variation_id': [''],
            'kolicina': ['5'],
        })
        self.assertEqual(inserted.status_code, 302)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stanje, 13)
        locs = self.client.get(reverse('staff_magacin_lokacije_lookup'), {
            'sa_zalihom': '1',
            'product_id': str(self.product.pk),
        })
        self.assertEqual(locs.status_code, 200)
        ids = [row['id'] for row in locs.json()['results']]
        self.assertIn(src.pk, ids)
        self.assertNotIn(dest.pk, ids)
        prenos = self.client.get(reverse('staff_magacin_transferi'), {'tab': 'prenos'})
        self.assertContains(prenos, 'odaberi gdje ima količine')
        self.assertContains(prenos, 'Kucaj šifru ili naziv lokacije')
        moved = self.client.post(reverse('staff_magacin_transferi'), {
            'action': 'transfer',
            'tab': 'prenos',
            'product_id': str(self.product.pk),
            'variation_id': '',
            'location_id': str(src.pk),
            'to_location_id': str(dest.pk),
            'kolicina': '4',
        })
        self.assertEqual(moved.status_code, 302)
        here = WarehouseStock.objects.get(product=self.product, location=src, variation__isnull=True)
        there = WarehouseStock.objects.get(product=self.product, location=dest, variation__isnull=True)
        self.assertEqual(here.kolicina, 9)
        self.assertEqual(there.kolicina, 4)

    def test_staff_cannot_open_magacin(self):
        staff = User.objects.create_user('staff', 'staff@example.com', 'pass', is_staff=True)
        self.client.force_login(staff)
        response = self.client.get(reverse('staff_magacin_artikli'))
        self.assertEqual(response.status_code, 302)

    def test_post_prijem_updates_detail(self):
        self.client.force_login(self.user)
        loc = WarehouseLocation.objects.create(sifra='X-1', naziv='Test')
        apply_movement(product=self.product, location=loc, tip='prijem', kolicina=1)
        response = self.client.post(
            reverse('staff_magacin_artikal', args=[self.product.pk]),
            {
                'action': 'kretanje',
                'mode': 'update',
                'location_id': loc.pk,
                'kolicina': '12',
                'napomena': 'Izmjena lokacije',
            },
        )
        self.assertEqual(response.status_code, 302)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stanje, 20)
        page = self.client.get(reverse('staff_magacin_artikal', args=[self.product.pk]))
        self.assertContains(page, 'Zalihe')
        self.assertContains(page, 'Izmjena lokacije')
        self.assertContains(page, 'Transfer')
        self.assertNotContains(page, 'Ažuriraj postojeću')

    def test_skini_sa_stanja_zeros_stock_and_hides_from_shop(self):
        self.client.force_login(self.user)
        loc2 = WarehouseLocation.objects.create(sifra='T-9', naziv='Druga')
        apply_movement(product=self.product, location=loc2, tip='prijem', kolicina=4)
        response = self.client.post(
            reverse('staff_magacin_artikal', args=[self.product.pk]),
            {'action': 'skini'},
        )
        self.assertEqual(response.status_code, 302)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stanje, 0)
        self.assertFalse(self.product.na_stanju)
        self.assertEqual(stock_totals(self.product)['dostupno'], 0)
        self.assertEqual(stock_totals(self.product)['na_stanju'], 0)

    def test_article_transfer_between_locations(self):
        self.client.force_login(self.user)
        src = WarehouseLocation.objects.get(sifra='T-1')
        dest = WarehouseLocation.objects.create(sifra='T-2', naziv='Druga polica')
        response = self.client.post(
            reverse('staff_magacin_artikal', args=[self.product.pk]),
            {
                'action': 'kretanje',
                'mode': 'transfer',
                'location_id': src.pk,
                'add_location_id': dest.pk,
                'kolicina': '3',
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            WarehouseStock.objects.get(product=self.product, location=src).kolicina,
            5,
        )
        self.assertEqual(
            WarehouseStock.objects.get(product=self.product, location=dest).kolicina,
            3,
        )

    def test_prenos_mp_opens_picking_and_validate_deducts(self):
        self.client.force_login(self.user)
        src = WarehouseLocation.objects.get(sifra='T-1')
        response = self.client.post(
            reverse('staff_magacin_artikal', args=[self.product.pk]),
            {
                'action': 'kretanje',
                'mode': 'mp',
                'kolicina': '3',
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('staff_magacin_artikal', args=[self.product.pk]), response['Location'])
        order = Order.objects.get(ime_prezime='Prenos u MP')
        self.assertEqual(order.lager_status, Order.LagerStatus.REZERVISANO)
        self.assertEqual(stock_totals(self.product)['dostupno'], 5)
        self.assertEqual(stock_totals(self.product)['rezervisano'], 3)
        listing = self.client.get(reverse('staff_magacin_pakuj'))
        self.assertContains(listing, 'Prenos u MP')
        self.assertContains(listing, self.product.naziv)
        self.assertContains(listing, reverse('staff_magacin_pakuj_detail', args=[order.broj]))
        narudzbe = self.client.get(reverse('staff_magacin_narudzbe'))
        self.assertNotContains(narudzbe, order.broj)
        self.assertNotContains(narudzbe, self.product.naziv)
        pick = self.client.get(reverse('staff_magacin_pakuj_detail', args=[order.broj]))
        self.assertContains(pick, 'Validatuj')
        self.assertContains(pick, 'Prenos u MP')
        self.assertContains(pick, 'T-1')
        self.assertContains(pick, 'Lokacija')
        self.assertContains(pick, 'Količina')
        self.assertNotContains(pick, 'Odnio kod Slobe')
        self.assertNotContains(pick, 'Provjera')
        self.assertNotContains(pick, 'Skeniraj narudžbu')
        validated = self.client.post(reverse('staff_magacin_pakuj_detail', args=[order.broj]), {
            'action': 'validiraj',
        })
        self.assertEqual(validated.status_code, 302)
        order.refresh_from_db()
        self.product.refresh_from_db()
        self.assertEqual(order.lager_status, Order.LagerStatus.VALIDIRANO)
        self.assertEqual(self.product.stanje, 5)
        self.assertEqual(stock_totals(self.product)['dostupno'], 5)
        self.assertEqual(stock_totals(self.product)['rezervisano'], 0)

    def test_location_click_lists_articles(self):
        self.client.force_login(self.user)
        loc = WarehouseLocation.objects.get(sifra='T-1')
        other = WarehouseLocation.objects.create(sifra='Z-9', naziv='Zadnja polica')
        empty = self.client.get(reverse('staff_magacin_lokacije'))
        self.assertContains(empty, 'Ukucaj šifru ili naziv')
        self.assertNotContains(empty, 'Zadnja polica')
        found = self.client.get(reverse('staff_magacin_lokacije'), {'pretraga': 'T-1'})
        self.assertContains(found, 'T-1')
        self.assertNotContains(found, 'Zadnja polica')
        page = self.client.get(reverse('staff_magacin_lokacije'), {'lokacija': loc.pk})
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'Artikli u')
        self.assertContains(page, 'Test braid')
        self.assertContains(page, '8')
        self.assertEqual(other.sifra, 'Z-9')

    def test_narudzbe_has_manual_entry(self):
        self.client.force_login(self.user)
        listed = self.client.get(reverse('staff_magacin_narudzbe'))
        self.assertEqual(listed.status_code, 200)
        self.assertContains(listed, 'Nova ručna narudžba')
        self.assertContains(listed, 'Pokaži validatovane')
        self.assertContains(listed, reverse('staff_magacin_narudzba_nova'))
        self.assertContains(listed, reverse('staff_magacin_narudzbe_stampa'))
        self.assertContains(listed, 'Validiraj odabrane')
        self.assertContains(listed, reverse('staff_magacin_narudzbe_validiraj'))
        self.assertNotContains(listed, 'mg-nav-count')
        form = self.client.get(reverse('staff_magacin_narudzba_nova'))
        self.assertEqual(form.status_code, 200)
        self.assertContains(form, 'Nova ručna narudžba')
        self.assertContains(form, 'id="mgOrderSearch"')
        self.assertContains(form, 'id="mgOrderSuggest"')
        self.assertContains(form, 'id="mgOrderQtyModal"')
        self.assertContains(form, 'Dodato na narudžbu')
        self.assertContains(form, 'id="mgCustomerSearch"')
        self.assertContains(form, 'Dodaj kupca')
        self.assertContains(form, 'id="mgCustomerModal"')
        self.assertContains(form, reverse('staff_magacin_kupci_lookup'))
        self.assertContains(form, reverse('staff_magacin_kupci_save'))
        self.assertNotContains(form, 'id="mgOrderCatalog"')
        self.assertContains(form, reverse('staff_magacin_artikli_lookup'))
        self.assertContains(form, 'data-mg-scan-target="mgOrderSearch"')
        self.assertContains(form, 'id="mgMpModal"')
        self.assertContains(form, 'id="mgMpAdd"')
        self.assertContains(form, 'Izbaci')
        self.assertContains(form, 'Provjeri u maloprodaji')

    def test_order_barcode_opens_picking(self):
        self.client.force_login(self.user)
        created = self.client.post(reverse('staff_magacin_narudzba_nova'), {
            'ime_prezime': 'Ana Ribić',
            'telefon': '061111111',
            'product_id': [str(self.product.pk)],
            'variation_id': [''],
            'kolicina': ['1'],
            'mp_ok': ['0'],
        })
        self.assertEqual(created.status_code, 302)
        order = Order.objects.get(izvor=Order.Izvor.MAGACIN)
        self.assertEqual(order.barkod, f'OZB{order.broj}')
        listing = self.client.get(reverse('staff_magacin_pakuj'))
        self.assertContains(listing, 'pkOrderScan')
        self.assertContains(listing, 'data-mg-scan-target="pkListSearch"')
        self.assertContains(listing, reverse('staff_magacin_pakuj_sken'))
        self.assertContains(listing, 'Skeniraj narudžbu')
        self.assertContains(listing, 'id="pkOrderNo"')
        self.assertContains(listing, 'value="#"')
        self.assertNotContains(listing, order.ime_prezime)
        png = self.client.get(reverse('staff_magacin_narudzba_barkod', args=[order.broj]))
        self.assertEqual(png.status_code, 200)
        self.assertEqual(png['Content-Type'], 'image/png')
        self.assertGreater(len(png.content), 40)
        print_page = self.client.get(reverse('staff_magacin_narudzbe_stampa'), {'b': [order.broj]})
        self.assertContains(print_page, reverse('staff_magacin_narudzba_barkod', args=[order.broj]))
        self.assertContains(print_page, order.barkod)
        opened = self.client.get(reverse('staff_magacin_pakuj_sken'), {'q': order.barkod})
        self.assertEqual(opened.status_code, 302)
        self.assertEqual(opened['Location'], reverse('staff_magacin_pakuj_detail', args=[order.broj]))
        order.refresh_from_db()
        self.assertEqual(order.pick_claimed_by_id, self.user.pk)
        self.assertTrue(order.pick_claimed_name)
        pick_page = self.client.get(reverse('staff_magacin_pakuj_detail', args=[order.broj]))
        self.assertContains(pick_page, 'Preuzeo')
        listed = self.client.get(reverse('staff_magacin_narudzbe'))
        self.assertContains(listed, 'Preuzeo')
        other = User.objects.create_superuser('picker', 'picker@example.com', 'pass')
        other_client = self.client_class()
        other_client.force_login(other)
        stolen = other_client.get(reverse('staff_magacin_pakuj_sken'), {'q': order.barkod})
        self.assertEqual(stolen.status_code, 302)
        self.assertIn('zauzeto=', stolen['Location'])
        blocked_detail = other_client.get(reverse('staff_magacin_pakuj_detail', args=[order.broj]))
        self.assertEqual(blocked_detail.status_code, 302)
        self.assertIn('zauzeto=', blocked_detail['Location'])
        blocked_page = other_client.get(blocked_detail['Location'])
        self.assertContains(blocked_page, 'Skini preuzimanje')
        released = other_client.post(reverse('staff_magacin_pakuj_oslobodi', args=[order.broj]))
        self.assertEqual(released.status_code, 302)
        order.refresh_from_db()
        self.assertIsNone(order.pick_claimed_by_id)
        self.assertEqual(order.pick_claimed_name, '')
        taken = other_client.get(reverse('staff_magacin_pakuj_sken'), {'q': order.barkod})
        self.assertEqual(taken.status_code, 302)
        self.assertEqual(taken['Location'], reverse('staff_magacin_pakuj_detail', args=[order.broj]))
        order.refresh_from_db()
        self.assertEqual(order.pick_claimed_by_id, other.pk)
        self.client.post(reverse('staff_magacin_pakuj_oslobodi', args=[order.broj]))
        pick_after = self.client.post(reverse('staff_magacin_pakuj_provjera'), {
            'group': 'noop',
            'action': 'ima',
            'narudzba': order.broj,
            'next': 'pick',
        })
        self.assertEqual(pick_after.status_code, 302)
        self.assertEqual(pick_after['Location'], reverse('staff_magacin_pakuj_detail', args=[order.broj]))
        opened_num = self.client.get(reverse('staff_magacin_pakuj_sken'), {'q': order.broj})
        self.assertEqual(opened_num['Location'], reverse('staff_magacin_pakuj_detail', args=[order.broj]))
        opened_hash = self.client.get(reverse('staff_magacin_pakuj_sken'), {'q': f'#{order.broj}'})
        self.assertEqual(opened_hash['Location'], reverse('staff_magacin_pakuj_detail', args=[order.broj]))
        missing = self.client.get(reverse('staff_magacin_pakuj_sken'), {'q': 'OZB9999'})
        self.assertEqual(missing.status_code, 302)
        self.assertEqual(missing['Location'], reverse('staff_magacin_pakuj'))

    def test_save_customer_persists_without_order(self):
        self.client.force_login(self.user)
        saved = self.client.post(reverse('staff_magacin_kupci_save'), {
            'ime_prezime': 'Marko Savić',
            'telefon': '065555555',
            'adresa': 'Titova 12',
            'grad': 'Mostar',
        })
        self.assertEqual(saved.status_code, 200)
        payload = saved.json()
        self.assertTrue(payload['ok'])
        self.assertEqual(payload['customer']['ime_prezime'], 'Marko Savić')
        self.assertEqual(payload['customer']['telefon'], '065555555')
        customer = WarehouseCustomer.objects.get(telefon='065555555')
        self.assertEqual(customer.ime_prezime, 'Marko Savić')
        self.assertEqual(customer.adresa, 'Titova 12')
        self.assertEqual(customer.grad, 'Mostar')
        self.assertFalse(Order.objects.filter(izvor=Order.Izvor.MAGACIN).exists())
        found = self.client.get(reverse('staff_magacin_kupci_lookup'), {'q': 'Marko'})
        self.assertEqual(found.json()['results'][0]['ime_prezime'], 'Marko Savić')
        again = self.client.post(reverse('staff_magacin_kupci_save'), {
            'ime_prezime': 'Marko Savić',
            'telefon': '065555555',
            'adresa': 'Kralja Tvrtka 3',
            'grad': 'Mostar',
        })
        self.assertEqual(again.status_code, 200)
        customer.refresh_from_db()
        self.assertEqual(WarehouseCustomer.objects.filter(telefon='065555555').count(), 1)
        self.assertEqual(customer.adresa, 'Kralja Tvrtka 3')

    def test_lookup_returns_synced_and_zero_stock(self):
        self.client.force_login(self.user)
        found = self.client.get(reverse('staff_magacin_artikli_lookup'), {'q': 'TST-1'})
        self.assertEqual(found.status_code, 200)
        payload = found.json()
        ids = [row['id'] for row in payload['results']]
        self.assertIn(self.product.pk, ids)
        self.assertNotIn(self.unsynced.pk, ids)
        hidden_zero = self.client.get(reverse('staff_magacin_artikli_lookup'), {'q': 'ZERO-1'})
        self.assertNotIn(self.zero.pk, [row['id'] for row in hidden_zero.json()['results']])
        empty = self.client.get(reverse('staff_magacin_artikli_lookup'), {'q': 'ZERO-1', 'bez_zalihe': '1'})
        empty_ids = [row['id'] for row in empty.json()['results']]
        self.assertIn(self.zero.pk, empty_ids)

    def test_create_manual_order_deducts_stock(self):
        self.client.force_login(self.user)
        response = self.client.post(reverse('staff_magacin_narudzba_nova'), {
            'ime_prezime': 'Ana Ribić',
            'telefon': '061111111',
            'email': 'ana@example.com',
            'adresa': 'Test 1',
            'grad': 'Sarajevo',
            'product_id': [str(self.product.pk)],
            'variation_id': [''],
            'kolicina': ['3'],
            'mp_ok': ['0'],
        })
        self.assertEqual(response.status_code, 302)
        order = Order.objects.get(izvor=Order.Izvor.MAGACIN)
        self.assertEqual(order.ime_prezime, 'Ana Ribić')
        self.assertEqual(order.status, Order.Status.NOVA)
        self.assertEqual(len(order.broj), 4)
        self.assertTrue(order.broj.isdigit())
        self.assertEqual(order.dostava, Decimal('11.00'))
        self.assertEqual(order.ukupno, Decimal('41.00'))
        self.assertEqual(order.lager_status, Order.LagerStatus.REZERVISANO)
        self.assertEqual(order.stavke.count(), 1)
        item = order.stavke.get()
        self.assertEqual(item.artikal_id, self.product.pk)
        self.assertEqual(item.kolicina, 3)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stanje, 8)
        self.assertEqual(stock_totals(self.product)['rezervisano'], 3)
        self.assertEqual(stock_totals(self.product)['dostupno'], 5)
        customer = WarehouseCustomer.objects.get(telefon='061111111')
        self.assertEqual(customer.ime_prezime, 'Ana Ribić')
        self.assertEqual(customer.grad, 'Sarajevo')
        found = self.client.get(reverse('staff_magacin_kupci_lookup'), {'q': 'Ana'})
        self.assertEqual(found.status_code, 200)
        self.assertEqual(found.json()['results'][0]['ime_prezime'], 'Ana Ribić')
        self.assertIn('/nalog/magacin/narudzbe/', response['Location'])
        listed = self.client.get(reverse('staff_magacin_artikli'))
        self.assertContains(listed, 'mg-nav-count')
        self.assertContains(listed, '1 za pakovanje')
        self.assertContains(listed, 'is-blink-pack')
        self.assertNotContains(listed, 'novih narudžbi')

    def test_manual_order_without_stock_requires_mp(self):
        self.client.force_login(self.user)
        blocked = self.client.post(reverse('staff_magacin_narudzba_nova'), {
            'ime_prezime': 'Marko',
            'telefon': '062222222',
            'product_id': [str(self.zero.pk)],
            'variation_id': [''],
            'kolicina': ['1'],
            'mp_ok': ['0'],
        })
        self.assertEqual(blocked.status_code, 200)
        self.assertContains(blocked, 'maloprodaju')
        self.assertFalse(Order.objects.filter(izvor=Order.Izvor.MAGACIN).exists())

        allowed = self.client.post(reverse('staff_magacin_narudzba_nova'), {
            'ime_prezime': 'Marko',
            'telefon': '062222222',
            'product_id': [str(self.zero.pk)],
            'variation_id': [''],
            'kolicina': ['1'],
            'mp_ok': ['1'],
        })
        self.assertEqual(allowed.status_code, 302)
        order = Order.objects.get(izvor=Order.Izvor.MAGACIN)
        self.assertIn('Maloprodaja', order.napomena)
        self.assertEqual(order.stavke.get().artikal_id, self.zero.pk)
        orders_list = self.client.get(reverse('staff_magacin_narudzbe'))
        self.assertContains(orders_list, 'is-mp-lock')
        self.assertContains(orders_list, 'Prvo Provjera MP')
        self.assertContains(orders_list, f"narudzba={order.broj}")
        print_page = self.client.get(reverse('staff_magacin_narudzbe_stampa'), {'b': [order.broj]})
        self.assertEqual(print_page.status_code, 302)
        self.assertIn('/nalog/magacin/pakuj/provjera/', print_page['Location'])
        self.assertIn(f'narudzba={order.broj}', print_page['Location'])
        focused = self.client.get(reverse('staff_magacin_pakuj_provjera'), {'narudzba': order.broj, 'next': 'stampa'})
        self.assertEqual(focused.status_code, 200)
        self.assertContains(focused, 'Prazan lager')
        self.assertContains(focused, f'Provjera #{order.broj}')

        listing = self.client.get(reverse('staff_magacin_pakuj'))
        self.assertContains(listing, 'pkOrderScan')
        self.assertContains(listing, 'Skeniraj narudžbu')
        self.assertNotContains(listing, 'is-locked')
        self.assertNotContains(listing, reverse('staff_magacin_pakuj_detail', args=[order.broj]))
        blocked = self.client.get(reverse('staff_magacin_pakuj_detail', args=[order.broj]))
        self.assertEqual(blocked.status_code, 302)
        self.assertIn(reverse('staff_magacin_pakuj_provjera'), blocked['Location'])
        self.assertIn(f'narudzba={order.broj}', blocked['Location'])
        self.assertIn('next=pick', blocked['Location'])
        scanned = self.client.get(reverse('staff_magacin_pakuj_sken'), {'q': order.barkod})
        self.assertEqual(scanned.status_code, 302)
        self.assertIn(reverse('staff_magacin_pakuj_provjera'), scanned['Location'])
        self.assertIn(f'narudzba={order.broj}', scanned['Location'])
        self.assertIn('next=pick', scanned['Location'])
        popup = self.client.get(scanned['Location'])
        self.assertEqual(popup.status_code, 200)
        self.assertContains(popup, 'Prvo Provjera MP')
        self.assertContains(popup, 'Ima u MP')
        self.assertContains(popup, 'prije pakovanja')
        check = self.client.get(reverse('staff_magacin_pakuj_provjera'))
        self.assertEqual(check.status_code, 200)
        self.assertContains(check, 'Prazan lager')
        self.assertContains(check, 'Provjera MP')
        self.assertContains(check, order.broj)
        groups = check.context['groups']
        self.assertTrue(groups)
        found = self.client.post(reverse('staff_magacin_pakuj_provjera'), {
            'group': groups[0]['key'],
            'action': 'ima',
            'narudzba': order.broj,
            'next': 'stampa',
        })
        self.assertEqual(found.status_code, 302)
        self.assertIn('/nalog/magacin/narudzbe/stampa/', found['Location'])
        order.refresh_from_db()
        self.assertTrue(order.pick_state)
        print_ok = self.client.get(reverse('staff_magacin_narudzbe_stampa'), {'b': [order.broj]})
        self.assertEqual(print_ok.status_code, 200)
        self.assertContains(print_ok, 'print-job')
        self.assertContains(print_ok, 'Online Narudžbe br.')
        self.assertContains(print_ok, 'd.o.o. CarpologijaBH')
        self.assertContains(print_ok, 'carpologijabh@gmail.com')
        self.assertContains(print_ok, 'sajt www.opremazaribolov.ba')
        self.assertNotContains(print_ok, 'class="doc-logo"')
        gone = self.client.get(reverse('staff_magacin_pakuj_provjera'))
        self.assertEqual(gone.context['groups'], [])
        self.assertIsNone(order.stavke.get().kolicina_pokupljeno)
        opened = self.client.get(reverse('staff_magacin_pakuj_detail', args=[order.broj]))
        self.assertEqual(opened.status_code, 200)
        self.assertContains(opened, order.broj)
        unlocked = self.client.get(reverse('staff_magacin_pakuj'))
        self.assertContains(unlocked, 'Skeniraj narudžbu')
        self.assertNotContains(unlocked, reverse('staff_magacin_pakuj_detail', args=[order.broj]))

    def test_order_detail_uses_magacin_look_and_back_link(self):
        self.client.force_login(self.user)
        created = self.client.post(reverse('staff_magacin_narudzba_nova'), {
            'ime_prezime': 'Ana Ribić',
            'telefon': '061111111',
            'product_id': [str(self.product.pk)],
            'variation_id': [''],
            'kolicina': ['1'],
            'mp_ok': ['0'],
        })
        self.assertEqual(created.status_code, 302)
        order = Order.objects.get(izvor=Order.Izvor.MAGACIN)
        page = self.client.get(reverse('staff_order_detail', args=[order.broj]))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'Nazad na Magacin narudžbe')
        self.assertContains(page, reverse('staff_magacin_narudzbe'))
        self.assertContains(page, 'class="mg-sidebar"')
        self.assertContains(page, 'class="mg-card mg-order-invoice-card"')
        self.assertContains(page, 'staff-magacin')
        self.assertNotContains(page, 'Nazad na Online narudžbe')
        self.assertNotContains(page, 'preko 250 KM besplatna')
        self.assertContains(page, '11.00 KM')

    def test_validated_orders_hidden_until_validated_list(self):
        self.client.force_login(self.user)
        created = self.client.post(reverse('staff_magacin_narudzba_nova'), {
            'ime_prezime': 'Ana Ribić',
            'telefon': '061111111',
            'product_id': [str(self.product.pk)],
            'variation_id': [''],
            'kolicina': ['1'],
            'mp_ok': ['0'],
        })
        self.assertEqual(created.status_code, 302)
        order = Order.objects.get(izvor=Order.Izvor.MAGACIN)
        open_list = self.client.get(reverse('staff_magacin_narudzbe'))
        self.assertContains(open_list, order.broj)
        self.assertEqual(order.lager_status, Order.LagerStatus.REZERVISANO)
        self.assertEqual(stock_totals(self.product)['dostupno'], 7)

        print_page = self.client.get(reverse('staff_magacin_narudzbe_stampa'), {'b': [order.broj]})
        self.assertEqual(print_page.status_code, 200)
        html = print_page.content.decode()
        self.assertContains(print_page, 'print-job')
        self.assertContains(print_page, 'class="invoice-sheet"')
        self.assertNotContains(print_page, 'class="print-page-break"')
        self.assertNotContains(print_page, 'class="packing-section"')
        self.assertNotContains(print_page, 'Pakovanje — poseban list')
        self.assertContains(print_page, 'class="order-footer"')
        self.assertContains(print_page, 'invoice-warranty-note')
        self.assertContains(print_page, 'Ovaj papir je garantni list')
        self.assertContains(print_page, 'size: 210mm 297mm')
        self.assertNotContains(print_page, 'invoice-stamp')
        self.assertContains(print_page, 'Potpis prodavca')
        self.assertContains(print_page, 'Saša B.')
        self.assertNotContains(print_page, 'class="pk-loc"')
        self.assertNotContains(print_page, 'Zapakovano')
        self.assertNotContains(print_page, 'print-color-adjust')
        packing_page = self.client.get(reverse('staff_order_packing', args=[order.broj]))
        self.assertEqual(packing_page.status_code, 200)
        self.assertContains(packing_page, 'class="pk-loc"')
        self.assertContains(packing_page, 'T-1')
        self.assertNotContains(packing_page, 'Zapakovano')
        pack_list = self.client.get(reverse('staff_magacin_pakuj'))
        self.assertEqual(pack_list.status_code, 200)
        self.assertContains(pack_list, 'Picking')
        self.assertContains(pack_list, 'Skeniraj narudžbu')
        self.assertNotContains(pack_list, order.ime_prezime)
        pack_detail = self.client.get(reverse('staff_magacin_pakuj_detail', args=[order.broj]))
        self.assertEqual(pack_detail.status_code, 200)
        self.assertContains(pack_detail, order.ime_prezime)
        self.assertContains(pack_detail, order.broj)
        self.assertContains(pack_detail, 'T-1')
        self.assertContains(pack_detail, '01')
        self.assertContains(pack_detail, 'Idi na lokaciju')
        self.assertContains(pack_detail, 'Uzmi manje')
        self.assertContains(pack_detail, 'Pokupi sve')
        self.assertContains(pack_detail, 'Validatuj')
        self.assertNotContains(pack_detail, 'Odnio kod Slobe')
        self.assertNotContains(pack_detail, 'ostavi kod slobe')
        self.assertContains(pack_detail, 'Test braid')
        self.assertContains(pack_detail, 'pkQueue')
        self.assertNotContains(pack_detail, order.adresa)
        self.assertNotContains(pack_detail, order.telefon)
        still_open = self.client.get(reverse('staff_magacin_narudzbe'))
        self.assertContains(still_open, order.broj)

        validated = self.client.post(reverse('staff_order_detail', args=[order.broj]), {
            'action': 'validiraj',
        })
        self.assertEqual(validated.status_code, 302)
        self.assertIn('/nalog/magacin/narudzbe/', validated['Location'])
        order.refresh_from_db()
        self.assertEqual(order.lager_status, Order.LagerStatus.VALIDIRANO)
        self.assertEqual(order.status, Order.Status.ZAVRSENA)
        self.assertEqual(order.get_status_label(), 'Validatovana')
        self.product.refresh_from_db()
        self.assertEqual(stock_totals(self.product)['dostupno'], 7)
        self.assertEqual(self.product.stanje, 7)
        hidden = self.client.get(reverse('staff_magacin_narudzbe'))
        listed = [row.broj for row in hidden.context['orders']]
        self.assertNotIn(order.broj, listed)
        packed_gone = self.client.get(reverse('staff_magacin_pakuj'))
        self.assertNotContains(packed_gone, order.broj)
        self.assertEqual(
            self.client.get(reverse('staff_magacin_pakuj_detail', args=[order.broj])).status_code,
            404,
        )
        shown = self.client.get(reverse('staff_magacin_narudzbe'), {'validirane': '1'})
        self.assertContains(shown, order.broj)
        self.assertContains(shown, 'Validatovane narudžbe')
        self.assertContains(shown, 'Prikaži sve')
        self.assertTrue(order.zapakovana)
        packing = self.client.get(reverse('staff_magacin_pakovanje'))
        self.assertContains(packing, 'Pretraga narudžbi')
        self.assertEqual(list(packing.context['orders']), [])
        by_name = self.client.get(reverse('staff_magacin_pakovanje'), {'pretraga': order.ime_prezime})
        self.assertContains(by_name, order.broj)
        self.assertContains(by_name, 'Naručeno')
        self.assertContains(by_name, 'class="packing-section"')
        self.assertContains(by_name, 'class="pk-loc"')
        self.assertContains(by_name, 'T-1')
        by_phone = self.client.get(reverse('staff_magacin_pakovanje'), {'pretraga': order.telefon})
        self.assertContains(by_phone, order.broj)

    def test_bulk_validate_selected_orders(self):
        self.client.force_login(self.user)
        first = Order.objects.create(
            ime_prezime='Ana Ribić', telefon='061111111', email='a@example.com',
            adresa='A 1', grad='Sarajevo', ukupno=Decimal('10.00'),
            izvor=Order.Izvor.MAGACIN,
        )
        reserve_for_order(first, self.product, 2)
        second = Order.objects.create(
            ime_prezime='Marko', telefon='062222222', email='m@example.com',
            adresa='B 2', grad='Tuzla', ukupno=Decimal('8.00'),
            izvor=Order.Izvor.WEBSHOP,
        )
        posted = self.client.post(reverse('staff_magacin_narudzbe_validiraj'), {
            'b': [first.broj, second.broj],
            'next': reverse('staff_magacin_narudzbe'),
        })
        self.assertEqual(posted.status_code, 302)
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(first.lager_status, Order.LagerStatus.VALIDIRANO)
        self.assertEqual(second.lager_status, Order.LagerStatus.VALIDIRANO)
        self.assertEqual(first.status, Order.Status.ZAVRSENA)
        listed = [row.broj for row in self.client.get(reverse('staff_magacin_narudzbe')).context['orders']]
        self.assertNotIn(first.broj, listed)
        self.assertNotIn(second.broj, listed)

    def test_new_order_cannot_take_reserved_qty_without_mp(self):
        self.client.force_login(self.user)
        first = self.client.post(reverse('staff_magacin_narudzba_nova'), {
            'ime_prezime': 'Ana',
            'telefon': '061111111',
            'product_id': [str(self.product.pk)],
            'variation_id': [''],
            'kolicina': ['8'],
            'mp_ok': ['0'],
        })
        self.assertEqual(first.status_code, 302)
        self.assertEqual(stock_totals(self.product)['dostupno'], 0)
        blocked = self.client.post(reverse('staff_magacin_narudzba_nova'), {
            'ime_prezime': 'Marko',
            'telefon': '062222222',
            'product_id': [str(self.product.pk)],
            'variation_id': [''],
            'kolicina': ['1'],
            'mp_ok': ['0'],
        })
        self.assertEqual(blocked.status_code, 200)
        self.assertContains(blocked, 'maloprodaju')
        self.assertEqual(Order.objects.filter(izvor=Order.Izvor.MAGACIN).count(), 1)

    def test_pack_locations_sorted_alpha_and_numbered(self):
        from .views_magacin import _packing_location_groups

        groups = _packing_location_groups([
            {
                'rb': 1, 'naziv': 'Clip B', 'sifra': 'B', 'kolicina': 1,
                'picks': [{'location_name': 'B-03', 'take': 1}], 'check_mp': False,
            },
            {
                'rb': 2, 'naziv': 'Clip A10', 'sifra': 'A10', 'kolicina': 1,
                'picks': [{'location_name': 'A-10', 'take': 1}], 'check_mp': False,
            },
            {
                'rb': 3, 'naziv': 'Clip A2', 'sifra': 'A2', 'kolicina': 1,
                'picks': [{'location_name': 'A-2', 'take': 1}], 'check_mp': False,
            },
        ])
        self.assertEqual([row['label'] for row in groups], ['A-2', 'A-10', 'B-03'])
        self.assertEqual([row['rb_label'] for row in groups], ['01', '02', '03'])

    def test_pick_less_qty_prints_on_invoice(self):
        from .views import _order_print_job
        from .views_magacin import apply_order_pick

        self.client.force_login(self.user)
        created = self.client.post(reverse('staff_magacin_narudzba_nova'), {
            'ime_prezime': 'Ana Ribić',
            'telefon': '061111111',
            'product_id': [str(self.product.pk)],
            'variation_id': [''],
            'kolicina': ['3'],
            'mp_ok': ['0'],
        })
        self.assertEqual(created.status_code, 302)
        order = Order.objects.get(izvor=Order.Izvor.MAGACIN)
        item = order.stavke.get()
        apply_order_pick(order, [{
            'key': f'T-1-1-1',
            'item_id': item.pk,
            'got': 1,
            'need': 3,
            'done': True,
        }])
        item.refresh_from_db()
        self.assertEqual(item.kolicina, 3)
        self.assertEqual(item.kolicina_pokupljeno, 1)
        self.assertEqual(item.kolicina_faktura, 1)
        job = _order_print_job(order)
        self.assertEqual(job['stavke'][0]['kolicina'], 1)
        self.assertEqual(job['stavke'][0]['ukupno'], Decimal('10.00'))
        self.assertEqual(job['summary']['medjuzbir'], Decimal('10.00'))
        page = self.client.get(reverse('staff_magacin_narudzbe_stampa'), {'b': [order.broj]})
        self.assertContains(page, 'class="col-qty">1<')
        self.assertNotContains(page, 'class="col-qty">3<')
        listed = self.client.get(reverse('staff_magacin_narudzbe'))
        self.assertContains(listed, 'Manje pokupljeno')
        self.assertContains(listed, '1/3')
        apply_order_pick(order, [])
        order.refresh_from_db()
        self.assertEqual(order.stavke.get().kolicina_pokupljeno, 1)
        self.assertTrue(order.pick_state)
        saved = self.client.post(
            reverse('staff_magacin_pakuj_detail', args=[order.broj]),
            {
                'action': 'pick_save',
                'pick_json': json.dumps([{
                    'key': f'{item.pk}:T-1',
                    'item_id': item.pk,
                    'got': 2,
                    'need': 3,
                    'done': False,
                }]),
            },
        )
        self.assertEqual(saved.status_code, 200)
        again = self.client.get(reverse('staff_magacin_pakuj_detail', args=[order.broj]))
        self.assertIn('"got": 2', again.context['pick_state_json'])

    def test_validated_list_defaults_to_today(self):
        self.client.force_login(self.user)
        old = Order.objects.create(
            ime_prezime='Stara', telefon='061000000', email='s@example.com',
            adresa='A 1', grad='Sarajevo', ukupno=Decimal('10.00'),
            izvor=Order.Izvor.MAGACIN,
            status=Order.Status.ZAVRSENA,
            lager_status=Order.LagerStatus.VALIDIRANO,
            zapakovana=True,
            zapakovana_at=timezone.now() - timedelta(days=1),
        )
        today = Order.objects.create(
            ime_prezime='Danas', telefon='061111111', email='d@example.com',
            adresa='B 2', grad='Sarajevo', ukupno=Decimal('8.00'),
            izvor=Order.Izvor.MAGACIN,
            status=Order.Status.ZAVRSENA,
            lager_status=Order.LagerStatus.VALIDIRANO,
            zapakovana=True,
            zapakovana_at=timezone.now(),
        )
        page = self.client.get(reverse('staff_magacin_narudzbe'), {'validirane': '1'})
        listed = [row.broj for row in page.context['orders']]
        self.assertIn(today.broj, listed)
        self.assertNotIn(old.broj, listed)
        self.assertContains(page, 'Prikaži sve')
        all_page = self.client.get(reverse('staff_magacin_narudzbe'), {'validirane': '1', 'sve': '1'})
        all_listed = [row.broj for row in all_page.context['orders']]
        self.assertIn(today.broj, all_listed)
        self.assertIn(old.broj, all_listed)

    def test_pack_validate_from_detail(self):
        self.client.force_login(self.user)
        created = self.client.post(reverse('staff_magacin_narudzba_nova'), {
            'ime_prezime': 'Ana Ribić',
            'telefon': '061111111',
            'product_id': [str(self.product.pk)],
            'variation_id': [''],
            'kolicina': ['1'],
            'mp_ok': ['0'],
        })
        self.assertEqual(created.status_code, 302)
        order = Order.objects.get(izvor=Order.Izvor.MAGACIN)
        validated = self.client.post(
            reverse('staff_magacin_pakuj_detail', args=[order.broj]),
            {'action': 'validiraj'},
        )
        self.assertRedirects(validated, reverse('staff_magacin_pakuj'))
        order.refresh_from_db()
        self.assertEqual(order.lager_status, Order.LagerStatus.VALIDIRANO)
        self.assertEqual(order.status, Order.Status.ZAVRSENA)
        gone = self.client.get(reverse('staff_magacin_pakuj'))
        self.assertNotContains(gone, order.broj)


class OdooCustomerAddressTests(TestCase):
    def test_phone_goes_to_street2_on_create(self):
        from .odoo_client import OdooClient

        class DummyClient:
            def __init__(self):
                self.created = None

            def execute(self, model, method, *args):
                self.created = args[0] if args else None
                return 42

        dummy = DummyClient()
        partner_id, created = OdooClient.find_or_create_customer(
            dummy,
            name='Ana Ribić',
            street='Ulica 12',
            city='Sarajevo',
            phone='061111111',
            email='ana@example.com',
            zip_code='71000',
        )
        self.assertTrue(created)
        self.assertEqual(partner_id, 42)
        self.assertEqual(dummy.created['name'], 'Ana Ribić')
        self.assertEqual(dummy.created['street'], 'Ulica 12')
        self.assertEqual(dummy.created['street2'], '061111111')
        self.assertEqual(dummy.created['phone'], '061111111')
        self.assertEqual(dummy.created['mobile'], '061111111')

    def test_repeated_phone_still_creates_new_partner_with_order_data(self):
        from .odoo_client import OdooClient, OdooError

        class DummyClient:
            def __init__(self):
                self.attempts = []

            def execute(self, model, method, *args):
                vals = args[0] if args else {}
                self.attempts.append(vals)
                if 'phone' in vals:
                    raise OdooError('Telefon već postoji')
                return 88

        dummy = DummyClient()
        partner_id, created = OdooClient.find_or_create_customer(
            dummy,
            name='Marko Novi',
            street='Druga 5',
            city='Mostar',
            phone='061111111',
            email='carpologijabh@gmail.com',
        )
        self.assertTrue(created)
        self.assertEqual(partner_id, 88)
        first, second = dummy.attempts
        self.assertEqual(first['name'], 'Marko Novi')
        self.assertEqual(first['street'], 'Druga 5')
        self.assertEqual(first['street2'], '061111111')
        self.assertNotIn('email', first)
        self.assertEqual(second['street2'], '061111111')
        self.assertNotIn('phone', second)
        self.assertNotIn('mobile', second)


class MagacinUvozTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser('admin', 'admin@example.com', 'pass')
        self.existing = Product.objects.create(
            naziv='Fox Edges Lead Clip',
            sifra='FOX-UVOZ-1',
            cijena=Decimal('4.50'),
            stanje=3,
            na_stanju=True,
            magacin_sync_at=timezone.now(),
        )
        self.site_only = Product.objects.create(
            naziv='Shimano Aero Reel',
            sifra='WEB-UVOZ-1',
            cijena=Decimal('80.00'),
            stanje=0,
            na_stanju=False,
        )
        self.a10 = WarehouseLocation.objects.create(
            sifra='A-10', naziv='Glavni magacin', redoslijed=10,
        )
        apply_movement(product=self.existing, location=self.a10, tip='prijem', kolicina=3)

    def test_updates_existing_price_and_adds_qty_to_novi_uvoz(self):
        stats = apply_magacin_uvoz([{
            'artikal': 'Fox Edges Lead Clip',
            'kolicina': Decimal('10'),
            'mpc_brutto': Decimal('6.90'),
            'vpc_netto': Decimal('5.50'),
        }])
        self.existing.refresh_from_db()
        self.assertEqual(stats['updated'], 1)
        self.assertEqual(stats['created'], 0)
        self.assertEqual(self.existing.cijena, Decimal('6.90'))
        self.assertEqual(
            ProductWarehouseMeta.objects.get(product=self.existing).veleprodajna_cijena,
            Decimal('5.50'),
        )
        novi = WarehouseLocation.objects.get(naziv=NOVI_UVOZ_NAZIV)
        self.assertEqual(
            WarehouseStock.objects.get(product=self.existing, location=novi).kolicina,
            10,
        )
        self.assertEqual(
            WarehouseStock.objects.get(product=self.existing, location=self.a10).kolicina,
            3,
        )
        self.assertEqual(self.existing.stanje, 13)

    def test_creates_missing_product_on_novi_uvoz(self):
        stats = apply_magacin_uvoz([{
            'artikal': 'Novi Pellet 4mm',
            'kolicina': 8,
            'mpc_brutto': Decimal('12.00'),
            'vpc_netto': Decimal('9.00'),
        }])
        self.assertEqual(stats['created'], 1)
        product = Product.objects.get(naziv='Novi Pellet 4mm')
        self.assertIsNotNone(product.magacin_sync_at)
        self.assertEqual(product.cijena, Decimal('12.00'))
        novi = WarehouseLocation.objects.get(naziv=NOVI_UVOZ_NAZIV)
        self.assertEqual(
            WarehouseStock.objects.get(product=product, location=novi).kolicina,
            8,
        )
        self.assertEqual(product.stanje, 8)

    def test_does_not_duplicate_site_product_by_name(self):
        stats = apply_magacin_uvoz([{
            'artikal': 'Shimano Aero Reel',
            'kolicina': 2,
            'mpc_brutto': Decimal('99.00'),
        }])
        self.assertEqual(stats['updated'], 1)
        self.assertEqual(stats['created'], 0)
        self.assertEqual(Product.objects.filter(naziv='Shimano Aero Reel').count(), 1)
        self.site_only.refresh_from_db()
        self.assertIsNotNone(self.site_only.magacin_sync_at)
        self.assertEqual(self.site_only.cijena, Decimal('99.00'))
        self.assertEqual(self.site_only.stanje, 2)

    def test_page_and_excel_post(self):
        from .uvoz_import import parse_uvoz_paste

        rows = parse_uvoz_paste(
            'Artikal\tKolicina\tFakturna\tNabavna\tVpc netto\tMpc brutto\tVpc marza\tUkupno Fakturna\n'
            'Fox Edges Lead Clip\t4\t3\t3.2\t4.1\t7.5\t0.2\t12\n'
            'Excel Novi Artikal\t5\t2\t2.5\t3\t8\t0.3\t10\n'
        )
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]['artikal'], 'Fox Edges Lead Clip')
        self.assertEqual(rows[0]['mpc_brutto'], Decimal('7.50'))

        self.client.force_login(self.user)
        page = self.client.get(reverse('staff_magacin_uvoz'))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'Novi uvoz')
        self.assertContains(page, reverse('staff_magacin_uvoz_novi'))

        form = self.client.get(reverse('staff_magacin_uvoz_novi'))
        self.assertEqual(form.status_code, 200)
        self.assertContains(form, 'Artikal')
        self.assertContains(form, 'Kolicina')
        self.assertContains(form, 'Fakturna')
        self.assertContains(form, 'Nabavna')
        self.assertContains(form, 'Vpc netto')
        self.assertContains(form, 'Mpc brutto')
        self.assertContains(form, 'Vpc marza')
        self.assertContains(form, 'Ukupno Fakturna')

        paste = (
            'Artikal\tKolicina\tFakturna\tNabavna\tVpc netto\tMpc brutto\tVpc marza\tUkupno Fakturna\n'
            'Fox Edges Lead Clip\t4\t3\t3.2\t4.1\t7.5\t0.2\t12\n'
            'Excel Novi Artikal\t5\t2\t2.5\t3\t8\t0.3\t10\n'
        )
        response = self.client.post(
            reverse('staff_magacin_uvoz_novi'),
            {'naziv': 'Uvoz test 13.08', 'paste_text': paste},
        )
        self.assertEqual(response.status_code, 302)
        uvoz = Uvoz.objects.get(izvor=Uvoz.Izvor.MAGACIN, naziv='Uvoz test 13.08')
        self.assertRedirects(response, reverse('staff_magacin_uvoz_detail', args=[uvoz.pk]))
        self.existing.refresh_from_db()
        self.assertEqual(self.existing.cijena, Decimal('7.50'))
        created = Product.objects.get(naziv='Excel Novi Artikal')
        self.assertEqual(created.cijena, Decimal('8.00'))
        novi = WarehouseLocation.objects.get(naziv=NOVI_UVOZ_NAZIV)
        self.assertEqual(
            WarehouseStock.objects.get(product=self.existing, location=novi).kolicina,
            4,
        )
        self.assertEqual(
            WarehouseStock.objects.get(product=created, location=novi).kolicina,
            5,
        )
        detail = self.client.get(reverse('staff_magacin_uvoz_detail', args=[uvoz.pk]))
        self.assertContains(detail, 'Excel Novi Artikal')
        self.assertContains(detail, 'Fox Edges Lead Clip')
        self.assertContains(detail, '7.50')
        self.assertEqual(uvoz.stavke.count(), 2)
        clip = uvoz.stavke.get(artikal_naziv='Fox Edges Lead Clip')
        self.assertEqual(clip.status, UvozStavka.Status.UPDATED)
        self.assertEqual(clip.fakturna, Decimal('3.0000'))
        self.assertEqual(clip.vpc_netto, Decimal('4.10'))

        listing = self.client.get(reverse('staff_magacin_uvoz'))
        self.assertContains(listing, 'Uvoz test 13.08')

        later = (
            'Artikal\tKolicina\tFakturna\tNabavna\tVpc netto\tMpc brutto\tVpc marza\tUkupno Fakturna\n'
            'Fox Edges Lead Clip\t2\t3\t2.8\t3.9\t6.5\t0.15\t6\n'
        )
        self.client.post(
            reverse('staff_magacin_uvoz_novi'),
            {'naziv': 'Uvoz test 14.08', 'paste_text': later},
        )

        article = self.client.get(reverse('staff_magacin_artikal', args=[self.existing.pk]))
        html = article.content.decode()
        self.assertContains(article, 'Promjene cijena i marže')
        self.assertContains(article, 'Uvoz test 13.08')
        self.assertNotContains(article, 'id="mgPriceChart"')
        self.assertContains(article, 'Mpc pad')
        self.assertContains(article, 'Nabavna')
        self.assertNotContains(article, 'Osnovne informacije')
        self.assertNotContains(article, 'Sačuvaj info')
        self.assertLess(
            html.find('Zalihe po lokacijama'),
            html.find('Promjene cijena i marže (uvoz)'),
        )

        nivelacije = self.client.get(reverse('staff_magacin_nivelacije'))
        self.assertEqual(nivelacije.status_code, 200)
        self.assertContains(nivelacije, 'Fox Edges Lead Clip')
        self.assertContains(nivelacije, '6.5')
        self.assertContains(nivelacije, 'Mpc pad')
        self.assertContains(nivelacije, 'Izmjenjen')
        self.assertNotContains(nivelacije, 'Excel Novi Artikal')
        found = self.client.get(reverse('staff_magacin_nivelacije'), {'pretraga': 'Lead'})
        self.assertContains(found, 'Fox Edges Lead Clip')
        miss = self.client.get(reverse('staff_magacin_nivelacije'), {'pretraga': 'nepostoji'})
        self.assertNotContains(miss, 'Fox Edges Lead Clip')

        later_uvoz = Uvoz.objects.get(izvor=Uvoz.Izvor.MAGACIN, naziv='Uvoz test 14.08')
        marked = self.client.post(reverse('staff_magacin_nivelacije'), {
            'action': 'oznaci',
            'kljuc': f'p:{self.existing.pk}',
            'uvoz_id': str(later_uvoz.pk),
            'product_id': str(self.existing.pk),
        })
        self.assertEqual(marked.status_code, 302)
        self.assertTrue(
            NivelacijaOznaka.objects.filter(kljuc=f'p:{self.existing.pk}', uvoz=later_uvoz).exists()
        )
        open_list = self.client.get(reverse('staff_magacin_nivelacije'))
        self.assertNotContains(open_list, 'Fox Edges Lead Clip')
        done_list = self.client.get(reverse('staff_magacin_nivelacije'), {'izmjenjene': '1'})
        self.assertContains(done_list, 'Fox Edges Lead Clip')
        self.assertContains(done_list, 'Vrati')
