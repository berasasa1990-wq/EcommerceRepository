import json
import time
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .cart import izracunaj_pdv
from .magacin import (
    MAGACIN_SYNC_SESSION_KEY,
    MagacinError,
    NOVI_UVOZ_NAZIV,
    parse_vp_bulk_text,
    _apply_quant_batch,
    _odoo_id_to_local,
    apply_magacin_uvoz,
    apply_movement,
    attach_site_odoo_products_to_magacin,
    cancel_order_stock,
    create_prenos_mp_pick,
    drop_prenos_mp_item,
    deduct_for_order,
    deduct_mp_daily_stock,
    local_odoo_template_ids,
    location_rows,
    maloprodaja_location_rows,
    display_stock_totals,
    save_mp_daily_skidanje,
    parse_mp_daily_datum,
    parse_mp_daily_text,
    preview_mp_daily_rows,
    _normalize_mp_vision_text,
    magacin_products_qs,
    ponuda_totals,
    reserve_for_order,
    seed_default_locations,
    stock_totals,
    run_sync_chunk,
    start_price_sync,
    start_sifra_sync,
    start_stock_sync,
    sync_catalog_chunk,
    sync_price_chunk,
    sync_sifra_chunk,
    validate_order_stock,
)
from .models import (
    MagacinMpDnevnoSkidanje,
    MagacinMpDnevnoStavka,
    MagacinDeklaracijaBrend,
    MagacinPopis,
    MagacinPonuda,
    MagacinPonudaStavka,
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
    OrderStockHold,
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

    def test_delete_variations_keeps_location_stock(self):
        var = ProductVariation.objects.create(
            artikal=self.product, naziv='8#', sifra='FOX12345-8', cijena=Decimal('29.90'),
        )
        apply_movement(product=self.product, variation=var, location=self.a10, tip='prijem', kolicina=25)
        var.delete()
        self.assertFalse(self.product.varijacije.exists())
        stock = WarehouseStock.objects.get(product=self.product, location=self.a10)
        self.assertIsNone(stock.variation_id)
        self.assertEqual(stock.variation_key, 0)
        self.assertEqual(stock.kolicina, 25)
        leftover = deduct_for_order(self.product, 3)
        self.assertEqual(leftover, 0)
        stock.refresh_from_db()
        self.product.refresh_from_db()
        self.assertEqual(stock.kolicina, 22)
        self.assertEqual(self.product.stanje, 22)
        self.assertEqual(stock_totals(self.product)['dostupno'], 22)

    def test_prodaja_uses_orphaned_variation_key_stock(self):
        leftover = WarehouseStock.objects.create(
            product=self.product, location=self.a10, kolicina=25, rezervisano=0,
        )
        WarehouseStock.objects.filter(pk=leftover.pk).update(variation_key=999)
        WarehouseStock.objects.create(
            product=self.product, location=self.a10, kolicina=0, rezervisano=0,
        )
        leftover.refresh_from_db()
        self.assertEqual(leftover.variation_key, 999)
        self.assertEqual(stock_totals(self.product)['dostupno'], 25)
        leftover_qty = deduct_for_order(self.product, 4)
        self.assertEqual(leftover_qty, 0)
        stock = WarehouseStock.objects.get(product=self.product, location=self.a10)
        self.assertEqual(stock.variation_key, 0)
        self.assertEqual(stock.kolicina, 21)
        self.assertEqual(WarehouseStock.objects.filter(product=self.product, location=self.a10).count(), 1)

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
        mp_rows = maloprodaja_location_rows(self.product)
        self.assertEqual(len(mp_rows), 1)
        self.assertEqual(mp_rows[0]['location'].sifra, 'B-03')
        self.assertEqual(mp_rows[0]['kolicina'], 20)
        self.assertTrue(mp_rows[0]['is_mp'])

    def test_missing_maloprodaja_rows_skips_retail_stock(self):
        from .magacin import missing_maloprodaja_rows

        apply_movement(product=self.product, location=self.a10, tip='prijem', kolicina=4)
        rows = missing_maloprodaja_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['product'].pk, self.product.pk)
        self.assertEqual(rows[0]['max_qty'], 4)
        self.assertEqual(rows[0]['locations'][0]['location'].sifra, 'A-10')
        apply_movement(product=self.product, location=self.b03, tip='prijem', kolicina=1)
        self.assertEqual(missing_maloprodaja_rows(), [])

    def test_order_location_rows_puts_maloprodaja_last(self):
        from .magacin import order_location_rows, reserve_for_order

        apply_movement(product=self.product, location=self.a10, tip='prijem', kolicina=4)
        apply_movement(product=self.product, location=self.b03, tip='prijem', kolicina=6)
        rows, totals = order_location_rows(self.product)
        self.assertEqual(totals['na_stanju'], 10)
        self.assertEqual([row['location'].sifra for row in rows], ['A-10', 'B-03'])
        self.assertTrue(rows[-1].get('is_mp'))
        order = Order.objects.create(
            ime_prezime='MP Reserve', email='a@b.c', telefon='061',
            adresa='x', grad='y', medjuzbir=0, dostava=0, ukupno=0,
        )
        leftover = reserve_for_order(order, self.product, 5)
        self.assertEqual(leftover, 0)
        mag = WarehouseStock.objects.get(product=self.product, location=self.a10)
        mp = WarehouseStock.objects.get(product=self.product, location=self.b03)
        self.assertEqual(mag.rezervisano, 4)
        self.assertEqual(mp.rezervisano, 1)

    def test_maloprodaja_only_keeps_in_stock_status(self):
        apply_movement(product=self.product, location=self.b03, tip='prijem', kolicina=5)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stanje, 5)
        self.assertTrue(self.product.na_stanju)
        self.assertEqual(stock_totals(self.product)['na_stanju'], 0)
        self.assertEqual(display_stock_totals(self.product)['dostupno'], 5)

    def test_prenos_mp_moves_qty_to_maloprodaja(self):
        apply_movement(product=self.product, location=self.a10, tip='prijem', kolicina=10)
        order = create_prenos_mp_pick(
            product=self.product, location=self.a10, qty=3,
        )
        validate_order_stock(order)
        magacin = WarehouseStock.objects.get(product=self.product, location=self.a10)
        mp = WarehouseStock.objects.get(product=self.product, location=self.b03)
        self.assertEqual(magacin.kolicina, 7)
        self.assertEqual(magacin.rezervisano, 0)
        self.assertEqual(mp.kolicina, 3)
        self.assertEqual(stock_totals(self.product)['dostupno'], 7)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stanje, 10)
        rows, totals = location_rows(self.product)
        self.assertEqual([row['location'].sifra for row in rows], ['A-10'])
        self.assertEqual(totals['na_stanju'], 7)
        mp_rows = maloprodaja_location_rows(self.product)
        self.assertEqual(mp_rows[0]['kolicina'], 3)
        self.assertEqual(display_stock_totals(self.product)['dostupno'], 10)

    def test_prenos_all_to_mp_stays_na_stanju(self):
        apply_movement(product=self.product, location=self.a10, tip='prijem', kolicina=3)
        order = create_prenos_mp_pick(
            product=self.product, location=self.a10, qty=3,
        )
        validate_order_stock(order)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stanje, 3)
        self.assertTrue(self.product.na_stanju)
        self.assertEqual(stock_totals(self.product)['dostupno'], 0)
        self.assertEqual(display_stock_totals(self.product)['dostupno'], 3)
        self.assertEqual(
            WarehouseStock.objects.get(product=self.product, location=self.b03).kolicina,
            3,
        )

    def test_prenos_mp_groups_items_into_one_picking(self):
        other = Product.objects.create(
            naziv='Drugi artikal', sifra='DRG-1', cijena=Decimal('4.00'),
            stanje=0, na_stanju=False,
        )
        apply_movement(product=self.product, location=self.a10, tip='prijem', kolicina=6)
        apply_movement(product=other, location=self.a10, tip='prijem', kolicina=4)
        first = create_prenos_mp_pick(product=self.product, location=self.a10, qty=2)
        second = create_prenos_mp_pick(product=other, location=self.a10, qty=3)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(Order.objects.filter(ime_prezime='Prenos u MP').count(), 1)
        self.assertEqual(first.stavke.count(), 2)
        again = create_prenos_mp_pick(product=self.product, location=self.a10, qty=1)
        self.assertEqual(again.pk, first.pk)
        first.refresh_from_db()
        self.assertEqual(first.stavke.get(artikal=self.product).kolicina, 3)
        self.assertEqual(first.stavke.get(artikal=other).kolicina, 3)
        self.assertEqual(first.dostava, Decimal('0.00'))
        dropped = drop_prenos_mp_item(first, first.stavke.get(artikal=other))
        self.assertFalse(dropped)
        self.assertEqual(first.stavke.count(), 1)
        create_prenos_mp_pick(product=other, location=self.a10, qty=3)
        first.refresh_from_db()
        self.assertEqual(first.stavke.count(), 2)
        validate_order_stock(first)
        first.refresh_from_db()
        self.assertEqual(first.lager_status, Order.LagerStatus.VALIDIRANO)
        nxt = create_prenos_mp_pick(product=other, location=self.a10, qty=1)
        self.assertNotEqual(nxt.pk, first.pk)
        self.assertEqual(nxt.stavke.count(), 1)

    def test_zero_ukupno_takes_product_off_site(self):
        apply_movement(product=self.product, location=self.b03, tip='prijem', kolicina=2)
        self.product.refresh_from_db()
        self.assertTrue(self.product.na_stanju)
        self.assertEqual(self.product.stanje, 2)
        apply_movement(product=self.product, location=self.b03, tip='prodaja', kolicina=2)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stanje, 0)
        self.assertFalse(self.product.na_stanju)

    def test_sync_takes_off_site_without_location_stock(self):
        from .magacin import sync_site_visibility_from_locations

        ghost = Product.objects.create(
            naziv='Bez lokacije',
            sifra='NO-LOC-1',
            cijena=Decimal('1.00'),
            stanje=12,
            na_stanju=True,
        )
        stats = sync_site_visibility_from_locations()
        ghost.refresh_from_db()
        self.assertFalse(ghost.na_stanju)
        self.assertEqual(ghost.stanje, 0)
        self.assertGreaterEqual(stats['off'], 1)

    def test_any_location_qty_puts_product_back_on_site(self):
        from .magacin import sync_site_visibility_from_locations

        self.product.na_stanju = False
        self.product.stanje = 0
        self.product.save(update_fields=['na_stanju', 'stanje'])
        WarehouseStock.objects.create(
            product=self.product, location=self.a10, kolicina=4, rezervisano=0,
        )
        stats = sync_site_visibility_from_locations(product_ids=[self.product.pk])
        self.product.refresh_from_db()
        self.assertTrue(self.product.na_stanju)
        self.assertEqual(self.product.stanje, 4)
        self.assertEqual(stats['on'], 1)

    def test_parse_mp_daily_text_reads_sifra_and_qty(self):
        rows = parse_mp_daily_text(
            'Šifra\tNaziv\tKoličina\nFOX12345\tFox braid\t2\nFOX12345\tFox braid\t1\nTST-1 4'
        )
        by_sifra = {row['sifra']: row['qty'] for row in rows}
        self.assertEqual(by_sifra['FOX12345'], 3)
        self.assertEqual(by_sifra['TST-1'], 4)
        column_rows = parse_mp_daily_text(
            'SIFRA\tNaziv\tKolicina\nFOX12345\tFox braid\t2\nTST-1\tTest\t5'
        )
        by_col = {row['sifra']: row['qty'] for row in column_rows}
        self.assertEqual(by_col['FOX12345'], 2)
        self.assertEqual(by_col['TST-1'], 5)
        vision = _normalize_mp_vision_text(
            '{"stavke":[{"sifra":"FOX12345","kolicina":2},{"sifra":"TST-1","kolicina":4}]}'
        )
        by_vision = {row['sifra']: row['qty'] for row in parse_mp_daily_text(vision)}
        self.assertEqual(by_vision['FOX12345'], 2)
        self.assertEqual(by_vision['TST-1'], 4)
        promet = parse_mp_daily_text(
            'AS252 AS Feeder hranilica pl. 9.903.300422761541 3.00026/08/2026 0.000\n'
            'AS252 AS Feeder hranilica pl. 6.603.300422711542 2.00026/08/2026 0.000\n'
            '10829NP-BN-6-U10MUSTAD 6.506.50078511548 1.00026/08/2026 0.000\n'
        )
        by_promet = {row['sifra']: row['qty'] for row in promet}
        self.assertEqual(by_promet['785'], 1)
        self.assertEqual(by_promet['4227'], 5)
        high_rbr = parse_mp_daily_text(
            'Cline Minnow 70 Silver 4.754.7504647591533 1.00025/08/2026 0.000\n'
            'MT14251 MATE SLIM 10.5910.5909742451533 1.00025/08/2026 0.000\n'
            'MT14255 MATE SLIM 10.5910.5909745431534 1.00025/08/2026 0.000\n'
        )
        by_high = {row['sifra']: row['qty'] for row in high_rbr}
        self.assertEqual(by_high['4647'], 1)
        self.assertEqual(by_high['9742'], 1)
        self.assertEqual(by_high['9745'], 1)
        self.assertNotIn('46475', by_high)
        self.assertNotIn('97424', by_high)
        self.assertNotIn('97454', by_high)
        Product.objects.create(
            naziv='Mustad 785', sifra='785', cijena=Decimal('1.00'),
        )
        three_digit = parse_mp_daily_text(
            'MUSTAD 6.506.500785121548 1.00025/08/2026 0.000\n'
            'MUSTAD 6.506.500785131548 2.00025/08/2026 0.000\n'
        )
        self.assertEqual({row['sifra'] for row in three_digit}, {'785'})
        self.assertEqual(three_digit[0]['qty'], 3)
        self.assertEqual(
            parse_mp_daily_datum(
                'DATUM : 26.08.2026\n'
                'AS252 AS Feeder hranilica pl. 9.903.300422761541 3.00026/08/2026 0.000\n'
            ),
            date(2026, 8, 26),
        )
        vision_dated = _normalize_mp_vision_text(
            '{"datum":"26/08/2026","stavke":[{"sifra":"785","kolicina":1}]}'
        )
        self.assertEqual(parse_mp_daily_datum(vision_dated), date(2026, 8, 26))
        self.assertIn('785', vision_dated)

    def test_mp_daily_deducts_only_from_retail_location(self):
        apply_movement(product=self.product, location=self.a10, tip='prijem', kolicina=10)
        apply_movement(product=self.product, location=self.b03, tip='prijem', kolicina=5)
        result = deduct_mp_daily_stock('FOX12345 2\nNEMA-XYZ 1')
        self.assertEqual(result['taken'][0]['taken'], 2)
        self.assertEqual(result['taken'][0]['sifra'], 'FOX12345')
        skipped_sifre = [row['sifra'] for row in result['skipped']]
        self.assertIn('NEMA-XYZ', skipped_sifre)
        magacin = WarehouseStock.objects.get(product=self.product, location=self.a10)
        mp = WarehouseStock.objects.get(product=self.product, location=self.b03)
        self.assertEqual(magacin.kolicina, 10)
        self.assertEqual(mp.kolicina, 3)
        batch = save_mp_daily_skidanje(result, raw_text='DATUM : 26.08.2026\nFOX12345 2')
        self.assertEqual(batch.skinuto_komada, 2)
        self.assertEqual(batch.datum, date(2026, 8, 26))
        self.assertEqual(MagacinMpDnevnoStavka.objects.filter(skidanje=batch).count(), 1)
        self.assertFalse(MagacinMpDnevnoStavka.objects.filter(sifra='NEMA-XYZ').exists())
        apply_movement(product=self.product, location=self.b03, tip='prijem', kolicina=2)
        preview = preview_mp_daily_rows([{'sifra': 'FOX12345', 'qty': 6}])
        self.assertEqual(preview[0]['mp_dostupno'], 5)
        self.assertEqual(preview[0]['ostaje'], 0)
        over = deduct_mp_daily_stock(parsed=[{'sifra': 'FOX12345', 'qty': 6}])
        self.assertEqual(over['taken'][0]['taken'], 5)
        mp.refresh_from_db()
        self.assertEqual(mp.kolicina, 0)

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
    def __init__(self, templates, variants=None):
        self.templates = {int(row['id']): row for row in templates}
        self.variants = {int(row['id']): row for row in (variants or [])}
        self.image_requests = []

    def get_templates_by_ids(self, template_ids):
        return [self.templates[int(tid)] for tid in template_ids if int(tid) in self.templates]

    def get_template_images(self, template_ids, *, batch_size=5):
        self.image_requests.extend(int(tid) for tid in template_ids)
        return {}

    def get_product_variants(self, variant_ids, *, with_images=False):
        return [self.variants[int(vid)] for vid in variant_ids if int(vid) in self.variants]

    def get_all_sale_template_ids(self):
        return list(self.templates)


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

    def test_updates_product_matched_by_sifra_even_with_other_odoo_id(self):
        product = Product.objects.create(
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
        self.assertEqual(stats['azurirano'], 1)
        self.assertEqual(Product.objects.count(), 1)
        product.refresh_from_db()
        self.assertEqual(product.odoo_template_id, 88)
        self.assertEqual(product.naziv, 'Novi iz Odoo')
        self.assertEqual(product.sifra, 'SAME-CODE')
        self.assertEqual(product.cijena, Decimal('2.00'))

    def test_updates_existing_by_name_from_odoo(self):
        product = Product.objects.create(
            naziv='Gift Card',
            sifra='GC-1',
            cijena=Decimal('1.00'),
        )
        client = FakeOdooClient([{
            'id': 88,
            'name': 'gift card',
            'default_code': 'GC-NEW',
            'barcode': 'B-88',
            'list_price': '2.00',
            'qty_available': 0,
            'product_variant_ids': [88],
        }])
        stats = sync_catalog_chunk(client, [88], start=0, limit=10)
        self.assertEqual(stats['kreirano'], 0)
        self.assertEqual(stats['azurirano'], 1)
        self.assertEqual(Product.objects.filter(naziv__iexact='gift card').count(), 1)
        product.refresh_from_db()
        self.assertEqual(product.odoo_template_id, 88)
        self.assertEqual(product.sifra, 'GC-NEW')
        self.assertEqual(product.barkod, 'B-88')
        self.assertEqual(product.cijena, Decimal('2.00'))

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

    def test_quant_sync_sets_qty_from_odoo_id(self):
        product = Product.objects.create(
            naziv='Fox braid Odoo',
            sifra='FOX-OD-1',
            cijena=Decimal('12.00'),
            odoo_template_id=88,
            stanje=3,
            na_stanju=True,
            magacin_sync_at=timezone.now(),
        )
        loc = WarehouseLocation.objects.create(
            sifra='A-10',
            naziv='Glavni magacin',
            odoo_location_id=10,
            odoo_location_path='WH/Stock/A-10',
        )
        WarehouseStock.objects.create(product=product, location=loc, kolicina=3)

        class QuantClient:
            def get_internal_stock_quants(self, product_ids, *, for_packing=False):
                return {
                    88: [{
                        'location_id': 10,
                        'location_name': 'A-10',
                        'location_path': 'WH/Stock/A-10',
                        'quantity': 25,
                        'on_hand': 25,
                        'reserved_quantity': 2,
                    }],
                }

            def get_template_ids_for_variants(self, variant_ids):
                return {88: 88}

        updated, touched = _apply_quant_batch(
            QuantClient(), [88], variant_to_template={88: 88},
        )
        self.assertIn(product.pk, touched)
        self.assertGreaterEqual(updated, 1)
        stock = WarehouseStock.objects.get(product=product, location=loc)
        self.assertEqual(stock.kolicina, 25)
        self.assertEqual(stock.rezervisano, 2)
        product.refresh_from_db()
        self.assertEqual(product.stanje, 25)

    def test_quant_sync_maps_by_name_when_odoo_id_missing(self):
        product = Product.objects.create(
            naziv='Fox braid Odoo',
            sifra='STARA-SIFRA',
            cijena=Decimal('12.00'),
            stanje=3,
            na_stanju=True,
            magacin_sync_at=timezone.now(),
        )
        loc = WarehouseLocation.objects.create(
            sifra='A-10',
            naziv='Glavni magacin',
            odoo_location_id=10,
            odoo_location_path='WH/Stock/A-10',
        )
        WarehouseStock.objects.create(product=product, location=loc, kolicina=3)

        class QuantClient:
            def get_internal_stock_quants(self, product_ids, *, for_packing=False):
                return {
                    88: [{
                        'location_id': 10,
                        'location_name': 'A-10',
                        'location_path': 'WH/Stock/A-10',
                        'quantity': 25,
                        'on_hand': 25,
                        'reserved_quantity': 0,
                    }],
                }

            def get_template_ids_for_variants(self, variant_ids):
                return {88: 88}

            def get_templates_by_ids(self, template_ids):
                return [{
                    'id': 88,
                    'name': 'Fox braid Odoo',
                    'default_code': 'FOX-OD-1',
                    'barcode': '',
                    'list_price': '12.00',
                    'product_variant_ids': [88],
                }]

        updated, touched = _apply_quant_batch(
            QuantClient(), [88], variant_to_template={88: 88},
        )
        self.assertIn(product.pk, touched)
        self.assertGreaterEqual(updated, 1)
        product.refresh_from_db()
        self.assertEqual(product.odoo_template_id, 88)
        stock = WarehouseStock.objects.get(product=product, location=loc)
        self.assertEqual(stock.kolicina, 25)

    def test_start_stock_sync_skips_catalog(self):
        with patch('EcommerceApp.odoo_client.odoo_je_konfigurisan', return_value=True), patch(
            'EcommerceApp.magacin.attach_site_odoo_products_to_magacin',
        ):
            job = start_stock_sync()
        self.assertEqual(job['phase'], 'locations')
        self.assertTrue(job.get('stock_only'))
        self.assertFalse(job.get('incremental'))
        log = WarehouseSyncLog.objects.get(pk=job['log_id'])
        self.assertEqual(log.izvor, 'Odoo zalihe')

    def test_price_sync_updates_cijena_from_odoo_id(self):
        product = Product.objects.create(
            naziv='Fox braid Odoo',
            sifra='FOX-OD-P',
            cijena=Decimal('10.00'),
            odoo_template_id=88,
            magacin_sync_at=timezone.now(),
        )
        var = ProductVariation.objects.create(
            artikal=product,
            naziv='300m',
            sifra='FOX-OD-P-300',
            cijena=Decimal('10.00'),
            odoo_variant_id=881,
        )
        client = FakeOdooClient([{
            'id': 88,
            'name': 'Ime se ne dira',
            'default_code': 'DRUGA-SIFRA',
            'barcode': 'XXX',
            'list_price': '19.90',
            'product_variant_ids': [881, 882],
        }])
        client.get_product_variants = lambda ids, with_images=False: [
            {'id': 881, 'lst_price': '21.50', 'display_name': '300m', 'default_code': 'FOX-OD-P-300'},
        ]
        stats = sync_price_chunk(client, [88])
        self.assertEqual(stats['azurirano'], 1)
        product.refresh_from_db()
        var.refresh_from_db()
        self.assertEqual(product.cijena, Decimal('19.90'))
        self.assertEqual(product.naziv, 'Fox braid Odoo')
        self.assertEqual(product.sifra, 'DRUGA-SIFRA')
        self.assertEqual(product.barkod, 'XXX')
        self.assertEqual(var.cijena, Decimal('21.50'))

    def test_start_price_sync_skips_catalog(self):
        Product.objects.create(
            naziv='Ima odoo', sifra='HAS-P', cijena=Decimal('1.00'),
            odoo_template_id=10, magacin_sync_at=timezone.now(),
        )
        with patch('EcommerceApp.odoo_client.odoo_je_konfigurisan', return_value=True), patch(
            'EcommerceApp.magacin.attach_site_odoo_products_to_magacin',
        ):
            job = start_price_sync()
        self.assertEqual(job['phase'], 'prices')
        self.assertTrue(job.get('price_only'))
        self.assertEqual(job['template_ids'], [10])
        log = WarehouseSyncLog.objects.get(pk=job['log_id'])
        self.assertEqual(log.izvor, 'Odoo cijene')

    def test_sifra_sync_updates_existing_by_name_without_duplicate(self):
        product = Product.objects.create(
            naziv='Fox Submerge Sinking Braid',
            sifra='STARA-1',
            cijena=Decimal('10.00'),
        )
        other = Product.objects.create(
            naziv='Drugi artikal',
            sifra='OSTAJE',
            cijena=Decimal('4.00'),
        )
        client = FakeOdooClient([{
            'id': 77,
            'name': 'Fox Submerge Sinking Braid',
            'default_code': 'FOX-OD-77',
            'list_price': '99.00',
            'product_variant_ids': [77],
        }])
        stats = sync_sifra_chunk(client, [77])
        self.assertEqual(stats['azurirano'], 1)
        self.assertEqual(Product.objects.count(), 2)
        product.refresh_from_db()
        other.refresh_from_db()
        self.assertEqual(product.sifra, 'FOX-OD-77')
        self.assertEqual(product.naziv, 'Fox Submerge Sinking Braid')
        self.assertEqual(product.cijena, Decimal('10.00'))
        self.assertEqual(product.odoo_template_id, 77)
        self.assertEqual(other.sifra, 'OSTAJE')

    def test_sifra_sync_matches_name_case_insensitive(self):
        product = Product.objects.create(
            naziv='fox SUBMERGE sinking braid',
            sifra='STARA-CI',
            cijena=Decimal('10.00'),
        )
        client = FakeOdooClient([{
            'id': 78,
            'name': 'Fox Submerge Sinking Braid',
            'default_code': 'FOX-CI-78',
            'product_variant_ids': [78],
        }])
        stats = sync_sifra_chunk(client, [78])
        self.assertEqual(stats['azurirano'], 1)
        self.assertEqual(Product.objects.count(), 1)
        product.refresh_from_db()
        self.assertEqual(product.sifra, 'FOX-CI-78')

    def test_sifra_sync_does_not_create_when_name_missing(self):
        Product.objects.create(
            naziv='Lokalni artikal',
            sifra='LOK-1',
            cijena=Decimal('3.00'),
        )
        client = FakeOdooClient([{
            'id': 90,
            'name': 'Samo u Odoo',
            'default_code': 'ODOO-90',
            'product_variant_ids': [90],
        }])
        stats = sync_sifra_chunk(client, [90])
        self.assertEqual(stats['azurirano'], 0)
        self.assertEqual(stats['preskoceno'], 1)
        self.assertEqual(Product.objects.count(), 1)
        self.assertFalse(Product.objects.filter(sifra='ODOO-90').exists())
        self.assertFalse(Product.objects.filter(naziv='Samo u Odoo').exists())

    def test_sifra_sync_skips_when_odoo_code_already_taken(self):
        Product.objects.create(
            naziv='Fox braid',
            sifra='STARA-FOX',
            cijena=Decimal('10.00'),
        )
        taken = Product.objects.create(
            naziv='Drugi',
            sifra='FOX-TAKEN',
            cijena=Decimal('2.00'),
        )
        client = FakeOdooClient([{
            'id': 91,
            'name': 'Fox braid',
            'default_code': 'FOX-TAKEN',
            'product_variant_ids': [91],
        }])
        stats = sync_sifra_chunk(client, [91])
        self.assertEqual(stats['azurirano'], 0)
        self.assertEqual(Product.objects.count(), 2)
        self.assertEqual(Product.objects.get(naziv='Fox braid').sifra, 'STARA-FOX')
        taken.refresh_from_db()
        self.assertEqual(taken.sifra, 'FOX-TAKEN')

    def test_sifra_sync_uses_odoo_reference_on_variant(self):
        product = Product.objects.create(
            naziv='Fox braid',
            sifra='STARA-VAR',
            cijena=Decimal('10.00'),
        )
        client = FakeOdooClient(
            [{
                'id': 92,
                'name': 'Fox braid',
                'default_code': False,
                'product_variant_ids': [920],
            }],
            variants=[{
                'id': 920,
                'default_code': 'REF-920',
                'display_name': '[REF-920] Fox braid',
            }],
        )
        stats = sync_sifra_chunk(client, [92])
        self.assertEqual(stats['azurirano'], 1)
        self.assertEqual(Product.objects.count(), 1)
        product.refresh_from_db()
        self.assertEqual(product.sifra, 'REF-920')

    def test_sifra_sync_matches_name_ignoring_spaces_and_ref_prefix(self):
        product = Product.objects.create(
            naziv='  [OLD]  Fox   Submerge Sinking Braid ',
            sifra='STARA-WS',
            cijena=Decimal('10.00'),
        )
        client = FakeOdooClient([{
            'id': 93,
            'name': 'Fox Submerge Sinking Braid',
            'default_code': False,
            'reference': 'FOX-REF-93',
            'product_variant_ids': [93],
        }])
        stats = sync_sifra_chunk(client, [93])
        self.assertEqual(stats['azurirano'], 1)
        self.assertEqual(Product.objects.count(), 1)
        product.refresh_from_db()
        self.assertEqual(product.sifra, 'FOX-REF-93')

    def test_start_sifra_sync_skips_catalog(self):
        with patch('EcommerceApp.odoo_client.odoo_je_konfigurisan', return_value=True), patch(
            'EcommerceApp.magacin.attach_site_odoo_products_to_magacin',
        ):
            job = start_sifra_sync()
        self.assertEqual(job['phase'], 'discover')
        self.assertTrue(job.get('sifra_only'))
        self.assertEqual(job['template_ids'], [])
        log = WarehouseSyncLog.objects.get(pk=job['log_id'])
        self.assertEqual(log.izvor, 'Odoo šifre')

    def test_discover_sifra_only_uses_all_odoo_ids(self):
        Product.objects.create(
            naziv='Već tu',
            sifra='HAS-1',
            cijena=Decimal('1.00'),
            odoo_template_id=10,
            magacin_sync_at=timezone.now(),
        )
        log = WarehouseSyncLog.objects.create(
            status=WarehouseSyncLog.Status.U_TOKU,
            izvor='Odoo šifre',
        )

        class PageClient:
            def get_sale_template_ids_page(self, *, offset=0, limit=250):
                ids = [10, 88]
                return ids[offset:offset + limit]

        job = {
            'log_id': log.pk,
            'started': time.time(),
            'phase': 'discover',
            'discovered_ids': [],
            'discover_offset': 0,
            'sifra_only': True,
        }
        with patch('EcommerceApp.odoo_client.OdooClient.from_settings', return_value=PageClient()):
            job = run_sync_chunk(job)
        self.assertEqual(job['phase'], 'sifre')
        self.assertEqual(job['template_ids'], [10, 88])
        self.assertEqual(job['position'], 0)

    def test_sifre_phase_never_creates_products(self):
        product = Product.objects.create(
            naziv='Fox braid',
            sifra='STARA-PH',
            cijena=Decimal('10.00'),
        )
        log = WarehouseSyncLog.objects.create(
            status=WarehouseSyncLog.Status.U_TOKU,
            izvor='Odoo šifre',
        )
        client = FakeOdooClient([{
            'id': 77,
            'name': 'Fox braid',
            'default_code': 'FOX-PH-77',
            'product_variant_ids': [77],
        }, {
            'id': 88,
            'name': 'Samo u Odoo',
            'default_code': 'NEW-88',
            'product_variant_ids': [88],
        }])
        job = {
            'log_id': log.pk,
            'started': time.time(),
            'phase': 'sifre',
            'template_ids': [77, 88],
            'position': 0,
            'azurirano': 0,
            'preskoceno': 0,
        }
        with patch('EcommerceApp.odoo_client.OdooClient.from_settings', return_value=client):
            job = run_sync_chunk(job)
        self.assertTrue(job['done'])
        self.assertEqual(job['phase'], 'done')
        self.assertEqual(job['azurirano'], 1)
        self.assertEqual(Product.objects.count(), 1)
        product.refresh_from_db()
        self.assertEqual(product.sifra, 'FOX-PH-77')
        self.assertFalse(Product.objects.filter(naziv='Samo u Odoo').exists())

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

    def test_mp_daily_page_deducts_only_retail(self):
        self.client.force_login(self.user)
        page = self.client.get(reverse('staff_magacin_mp_dnevno'))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'Dnevno skidanje MP lagera')
        self.assertContains(page, 'name="fajl"')
        self.assertContains(page, 'Urađena skidanja')
        self.assertContains(page, 'Još nema sačuvanih skidanja')
        mp = WarehouseLocation.objects.filter(naziv__icontains='maloprodaja').first()
        if mp is None:
            mp = WarehouseLocation.objects.create(sifra='B-03', naziv='Maloprodaja Sarajevo')
        apply_movement(product=self.product, location=mp, tip='prijem', kolicina=5)
        mag = WarehouseLocation.objects.get(sifra='T-1')
        from unittest.mock import patch
        from django.core.files.uploadedfile import SimpleUploadedFile

        fake = SimpleUploadedFile('izvjestaj.png', b'not-an-image', content_type='image/png')
        with patch(
            'EcommerceApp.views_magacin.extract_mp_daily_text_from_upload',
            return_value='DATUM : 26.08.2026\nŠifra\tKoličina\nTST-1\t2\nNEMA-XYZ\t1',
        ):
            posted = self.client.post(
                reverse('staff_magacin_mp_dnevno'),
                {'fajl': fake, 'action': 'ocitaj'},
            )
        self.assertEqual(posted.status_code, 200)
        self.assertContains(posted, 'TST-1')
        self.assertContains(posted, 'mg-sifra-ok')
        self.assertContains(posted, 'NEMA-XYZ')
        self.assertContains(posted, 'mg-sifra-bad')
        self.assertContains(posted, 'Skini sa stanja')
        self.assertContains(posted, 'Ukloni unos')
        self.assertContains(posted, 'KOLIČINA U MP-u')
        self.assertContains(posted, 'OSTAJE NA MP')
        self.assertContains(posted, 'Datum dokumenta')
        self.assertContains(posted, '26.08.2026')
        self.assertContains(posted, 'još nije skinuto')
        self.assertNotContains(posted, 'class="mg-mp-date"')
        self.assertEqual(
            WarehouseStock.objects.get(product=self.product, location=mp).kolicina,
            5,
        )
        skini = self.client.post(reverse('staff_magacin_mp_dnevno'), {'action': 'skini'})
        self.assertEqual(skini.status_code, 302)
        self.assertIn('datum=2026-08-26', skini['Location'])
        detail = self.client.get(reverse('staff_magacin_mp_dnevno') + '?datum=2026-08-26')
        self.assertEqual(detail.status_code, 200)
        self.assertContains(detail, 'Skidanje 26.08.2026')
        self.assertContains(detail, 'TST-1')
        self.assertNotContains(detail, 'NEMA-XYZ')
        dash = self.client.get(reverse('staff_magacin_mp_dnevno'))
        self.assertContains(dash, 'class="mg-mp-date"')
        self.assertContains(dash, '26.08.2026')
        self.assertTrue(MagacinMpDnevnoStavka.objects.filter(sifra='TST-1', kolicina=2).exists())
        self.assertEqual(MagacinMpDnevnoSkidanje.objects.get().datum, date(2026, 8, 26))
        self.assertFalse(MagacinMpDnevnoStavka.objects.filter(sifra='NEMA-XYZ').exists())
        self.assertEqual(
            WarehouseStock.objects.get(product=self.product, location=mp).kolicina,
            3,
        )
        self.assertEqual(
            WarehouseStock.objects.get(product=self.product, location=mag).kolicina,
            8,
        )

    def test_mp_daily_ukloni_preview_does_not_deduct(self):
        self.client.force_login(self.user)
        mp = WarehouseLocation.objects.filter(naziv__icontains='maloprodaja').first()
        if mp is None:
            mp = WarehouseLocation.objects.create(sifra='B-03', naziv='Maloprodaja Sarajevo')
        apply_movement(product=self.product, location=mp, tip='prijem', kolicina=5)
        from unittest.mock import patch
        from django.core.files.uploadedfile import SimpleUploadedFile

        fake = SimpleUploadedFile('pogresan.png', b'not-an-image', content_type='image/png')
        with patch(
            'EcommerceApp.views_magacin.extract_mp_daily_text_from_upload',
            return_value='DATUM : 26.08.2026\nŠifra\tKoličina\nTST-1\t2',
        ):
            posted = self.client.post(
                reverse('staff_magacin_mp_dnevno'),
                {'fajl': fake, 'action': 'ocitaj'},
            )
        self.assertContains(posted, 'TST-1')
        self.assertContains(posted, 'Ukloni unos')
        ukloni = self.client.post(reverse('staff_magacin_mp_dnevno'), {'action': 'ukloni'})
        self.assertEqual(ukloni.status_code, 302)
        page = self.client.get(reverse('staff_magacin_mp_dnevno'))
        self.assertContains(page, 'Unos je uklonjen')
        self.assertNotContains(page, 'Prepoznato sa dokumenta')
        self.assertNotContains(page, 'Ukloni unos')
        self.assertNotContains(page, 'TST-1')
        self.assertFalse(MagacinMpDnevnoStavka.objects.filter(sifra='TST-1').exists())
        self.assertEqual(
            WarehouseStock.objects.get(product=self.product, location=mp).kolicina,
            5,
        )

    def test_admin_panel_has_magacin_not_carts(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse('staff_admin_panel'))
        self.assertContains(response, 'Magacin')
        self.assertContains(response, reverse('staff_magacin'))
        self.assertNotContains(response, 'Aktivne korpe')
        self.assertContains(response, 'Poklon Vaučer')
        self.assertContains(response, reverse('staff_gift_voucher'))
        self.assertNotContains(response, 'Online narudžbe')

    def test_gift_voucher_prints_a4(self):
        self.client.force_login(self.user)
        form = self.client.get(reverse('staff_gift_voucher'))
        self.assertEqual(form.status_code, 200)
        self.assertContains(form, 'name="ime"')
        self.assertContains(form, 'name="prezime"')
        self.assertContains(form, '50 KM')
        self.assertContains(form, '100 KM')
        self.assertContains(form, '500 KM')
        self.assertContains(form, reverse('staff_gift_voucher_print'))
        printed = self.client.get(reverse('staff_gift_voucher_print'), {
            'ime': 'Ana', 'prezime': 'Ribić', 'iznos': '100',
        })
        self.assertEqual(printed.status_code, 200)
        self.assertContains(printed, 'Ana Ribić')
        self.assertContains(printed, '100 KM')
        self.assertContains(printed, 'Poklon vaučer')
        self.assertContains(printed, 'size: A4 portrait')
        blocked = self.client.get(reverse('staff_gift_voucher_print'), {
            'ime': 'Ana', 'prezime': 'Ribić', 'iznos': '75',
        })
        self.assertEqual(blocked.status_code, 302)

    def test_ponuda_catalog_manual_discount_and_public_pdf(self):
        self.client.force_login(self.user)
        listed = self.client.get(reverse('staff_magacin_ponude'))
        self.assertEqual(listed.status_code, 200)
        self.assertContains(listed, 'Kreiraj ponudu')
        self.assertNotContains(listed, 'catalog-sidebar')
        created = self.client.post(reverse('staff_magacin_ponude'), {'action': 'nova'})
        self.assertEqual(created.status_code, 302)
        ponuda = MagacinPonuda.objects.get()
        self.assertEqual(ponuda.status, MagacinPonuda.Status.NACRT)
        add = self.client.post(
            reverse('staff_magacin_ponuda_detail', args=[ponuda.pk]),
            {
                'action': 'dodaj',
                'product_id': str(self.product.pk),
                'kolicina': '2',
            },
        )
        self.assertEqual(add.status_code, 302)
        ajax = self.client.post(
            reverse('staff_magacin_ponuda_detail', args=[ponuda.pk]),
            {
                'action': 'dodaj',
                'product_id': str(self.product.pk),
                'kolicina': '1',
            },
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )
        self.assertEqual(ajax.status_code, 200)
        payload = ajax.json()
        self.assertTrue(payload['ok'])
        self.assertEqual(len(payload['stavke']), 1)
        self.assertEqual(payload['stavke'][0]['kolicina'], 3)
        self.assertIn('ukupno_sa_pdv', payload['totals'])
        manual = self.client.post(
            reverse('staff_magacin_ponuda_detail', args=[ponuda.pk]),
            {
                'action': 'dodaj_rucno',
                'naziv': 'Ručni štap',
                'sifra': 'RUC-1',
                'kolicina': '1',
                'cijena': '20,00',
            },
        )
        self.assertEqual(manual.status_code, 302)
        disc = self.client.post(
            reverse('staff_magacin_ponuda_detail', args=[ponuda.pk]),
            {'action': 'popust', 'popust_postotak': '10', 'popust_iznos': '0'},
        )
        self.assertEqual(disc.status_code, 302)
        ponuda.refresh_from_db()
        totals = ponuda_totals(ponuda)
        self.assertEqual(totals['osnova'], Decimal('50.00'))
        self.assertEqual(totals['popust'], Decimal('5.00'))
        self.assertEqual(totals['ukupno_sa_pdv'], Decimal('45.00'))
        split = izracunaj_pdv(Decimal('45.00'))
        self.assertEqual(totals['net'], split['bez_pdv'])
        self.assertEqual(totals['pdv'], split['pdv'])
        self.client.post(
            reverse('staff_magacin_ponuda_detail', args=[ponuda.pk]),
            {
                'action': 'kupac',
                'ime_prezime': 'Ana Ribic',
                'grad': 'Tuzla',
                'telefon': '061111222',
            },
        )
        published = self.client.post(
            reverse('staff_magacin_ponuda_detail', args=[ponuda.pk]),
            {'action': 'objavi'},
        )
        self.assertEqual(published.status_code, 302)
        self.assertTrue((published.get('Location') or '').endswith('#pnLink'))
        ponuda.refresh_from_db()
        self.assertEqual(ponuda.status, MagacinPonuda.Status.OBJAVLJENA)
        before_pub = self.client.get(reverse('staff_magacin_ponuda_detail', args=[ponuda.pk]))
        self.assertContains(before_pub, 'Dodaj artikle')
        self.assertContains(before_pub, 'Katalog')
        self.assertContains(before_pub, 'Skeniraj')
        self.assertContains(before_pub, 'id="pnQtyModal"')
        detail = self.client.get(reverse('staff_magacin_ponuda_detail', args=[ponuda.pk]))
        self.assertContains(detail, 'Copy link')
        self.assertContains(detail, 'id="pnLink"')
        self.assertNotContains(detail, 'Prihvaćena ponuda')
        self.assertContains(detail, reverse('ponuda_javna', args=[ponuda.token]))
        listed_pub = self.client.get(reverse('staff_magacin_ponude'))
        self.assertContains(listed_pub, 'Prihvaćena ponuda')
        self.assertContains(listed_pub, reverse('staff_magacin_ponuda_prihvati', args=[ponuda.pk]))
        self.assertContains(detail, 'Iznos bez PDV')
        self.assertContains(detail, 'PDV 17%')
        self.assertContains(detail, 'Ukupno sa PDV')
        public = self.client.get(reverse('ponuda_javna', args=[ponuda.token]))
        self.assertEqual(public.status_code, 200)
        self.assertContains(public, ponuda.broj)
        self.assertContains(public, 'Ana Ribic')
        self.assertContains(public, 'Test braid')
        self.assertContains(public, 'Ručni štap')
        self.assertContains(public, 'Iznos bez PDV')
        self.assertContains(public, 'PDV 17%')
        self.assertContains(public, 'Ukupno sa PDV')
        self.assertContains(public, 'Štampaj / PDF')
        self.assertNotContains(public, 'Prihvaćena ponuda')
        missing = self.client.get(reverse('ponuda_javna', args=['nema-tog-tokena']))
        self.assertEqual(missing.status_code, 404)

    def test_ponuda_accept_goes_to_picking_and_deducts_location(self):
        self.client.force_login(self.user)
        created = self.client.post(reverse('staff_magacin_ponude'), {'action': 'nova'})
        self.assertEqual(created.status_code, 302)
        ponuda = MagacinPonuda.objects.get()
        self.client.post(
            reverse('staff_magacin_ponuda_detail', args=[ponuda.pk]),
            {'action': 'dodaj', 'product_id': str(self.product.pk), 'kolicina': '2'},
        )
        self.client.post(
            reverse('staff_magacin_ponuda_detail', args=[ponuda.pk]),
            {'action': 'objavi'},
        )
        loc = WarehouseLocation.objects.get(sifra='T-1')
        stock = WarehouseStock.objects.get(product=self.product, location=loc, variation__isnull=True)
        self.assertEqual(stock.kolicina, 8)
        self.assertEqual(stock.rezervisano, 0)
        accepted = self.client.post(reverse('staff_magacin_ponuda_prihvati', args=[ponuda.pk]))
        self.assertEqual(accepted.status_code, 302)
        self.assertEqual(accepted['Location'], reverse('staff_magacin_ponude'))
        ponuda.refresh_from_db()
        self.assertEqual(ponuda.status, MagacinPonuda.Status.PRIHVACENA)
        order = ponuda.order
        self.assertIsNotNone(order)
        hold = order.magacin_holds.get()
        self.assertEqual(hold.location_id, loc.pk)
        self.assertEqual(hold.kolicina, 2)
        self.assertEqual(hold.status, OrderStockHold.Status.REZERVISANO)
        stock.refresh_from_db()
        self.assertEqual(stock.rezervisano, 2)
        validate_order_stock(order, user=self.user)
        stock.refresh_from_db()
        self.assertEqual(stock.kolicina, 6)
        self.assertEqual(stock.rezervisano, 0)
        hold.refresh_from_db()
        self.assertEqual(hold.status, OrderStockHold.Status.VALIDIRANO)
        order.refresh_from_db()
        self.assertEqual(order.lager_status, Order.LagerStatus.VALIDIRANO)
        listed = self.client.get(reverse('staff_magacin_ponude'))
        self.assertContains(listed, 'Prihvaćene')
        self.assertNotContains(listed, reverse('staff_magacin_ponuda_prihvati', args=[ponuda.pk]))
        public = self.client.get(reverse('ponuda_javna', args=[ponuda.token]))
        self.assertEqual(public.status_code, 200)

    def test_artikli_and_detail_ok(self):
        self.client.force_login(self.user)
        list_res = self.client.get(reverse('staff_magacin_artikli'))
        self.assertEqual(list_res.status_code, 200)
        self.assertContains(list_res, 'mgArticleScanBtn')
        self.assertContains(list_res, 'Zadnje izmjene količina')
        self.assertContains(list_res, 'KORISNIK')
        self.assertNotContains(list_res, 'class="mg-product-row"')
        self.assertContains(list_res, 'Test braid')
        detail = self.client.get(reverse('staff_magacin_artikal', args=[self.product.pk]))
        self.assertEqual(detail.status_code, 200)
        self.assertContains(detail, 'Zalihe po lokacijama')
        self.assertContains(detail, 'Istorija kretanja')
        self.assertContains(detail, 'KUPAC')
        self.assertContains(detail, reverse('home'))
        self.assertContains(detail, 'class="header"')
        self.assertContains(detail, 'Nazad na rezultate')
        self.assertContains(detail, 'id="mgArticleSearch"')
        self.assertNotContains(detail, 'class="mg-top"')
        self.assertContains(detail, 'Izmijeni artikal')
        self.assertContains(detail, 'Zalihe')
        self.assertContains(detail, 'Skini sa stanja')
        self.assertContains(detail, 'Dodaj na stanje')
        self.assertContains(detail, '>Cijena<')
        self.assertContains(detail, '>Vpc<')
        self.assertContains(detail, 'mg-zalihe-panel')
        self.assertContains(detail, '10.00 KM')
        self.assertContains(detail, '7.25 KM')
        self.assertContains(detail, 'Vpc 7.25 KM')
        self.assertNotContains(detail, '· VPC')
        self.assertContains(detail, 'Prenos u MP')
        self.assertContains(detail, 'Transfer')
        self.assertContains(detail, 'Dodaj u novu lokaciju')
        self.assertNotContains(detail, 'Ažuriraj postojeću')
        self.assertNotContains(detail, 'Ažuriraj lokacije')
        self.assertNotContains(detail, 'Dodaj na novu')
        self.assertContains(detail, reverse('staff_magacin_stampa_cijena_ista'))
        self.assertContains(detail, f'artikal={self.product.pk}')
        self.assertContains(detail, 'Štampaj')
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

    def test_artikal_history_shows_customer_from_order(self):
        self.client.force_login(self.user)
        created = self.client.post(reverse('staff_magacin_narudzba_nova'), {
            'ime_prezime': 'Ana Ribic',
            'telefon': '061222333',
            'email': 'ana@example.com',
            'adresa': 'Ulica 8',
            'grad': 'Tuzla',
            'product_id': [str(self.product.pk)],
            'variation_id': [''],
            'kolicina': ['1'],
            'mp_ok': ['0'],
        })
        self.assertEqual(created.status_code, 302)
        order = Order.objects.get(ime_prezime='Ana Ribic')
        validate_order_stock(order, user=self.user)
        detail = self.client.get(reverse('staff_magacin_artikal', args=[self.product.pk]))
        self.assertEqual(detail.status_code, 200)
        self.assertContains(detail, 'KUPAC')
        self.assertContains(detail, 'Ana Ribic')
        self.assertContains(detail, f'Validacija #{order.broj}')
        history = self.client.get(reverse('staff_magacin_istorija', args=[self.product.pk]))
        self.assertEqual(history.status_code, 200)
        self.assertContains(history, 'Ana Ribic')
        self.assertContains(history, 'KUPAC')

    def test_artikli_recent_moves_show_staff_not_order_user(self):
        import re as _re

        self.client.force_login(self.user)
        loc = WarehouseLocation.objects.get(sifra='T-1')
        staff = User.objects.create_superuser(
            'lagerist', 'lager@example.com', 'pass',
            first_name='Marko', last_name='Ivic',
        )
        WarehouseMovement.objects.all().delete()
        apply_movement(
            product=self.product,
            location=loc,
            tip='prijem',
            kolicina=2,
            napomena='Dodano na lokaciju',
            user=staff,
        )
        move = WarehouseMovement.objects.order_by('-id').first()
        self.assertEqual(move.korisnik_id, staff.pk)
        page = self.client.get(reverse('staff_magacin_artikli'))
        self.assertRegex(page.content.decode(), _re.compile(r'mg-move-user[^>]*>\s*Marko I\.'))

        WarehouseMovement.objects.all().delete()
        apply_movement(
            product=self.product,
            location=loc,
            tip='prodaja',
            kolicina=1,
            napomena='Validacija #88',
            user=staff,
        )
        order_page = self.client.get(reverse('staff_magacin_artikli'))
        self.assertNotRegex(order_page.content.decode(), _re.compile(r'mg-move-user[^>]*>\s*Marko I\.'))
        self.assertRegex(order_page.content.decode(), _re.compile(r'mg-move-user[^>]*>\s*—'))
        self.assertContains(order_page, 'Prodaja')

        WarehouseMovement.objects.all().delete()
        apply_movement(
            product=self.product,
            location=loc,
            tip='prodaja',
            kolicina=1,
            napomena='Ručna narudžba',
            user=staff,
        )
        manual_order = self.client.get(reverse('staff_magacin_artikli'))
        self.assertNotRegex(manual_order.content.decode(), _re.compile(r'mg-move-user[^>]*>\s*Marko I\.'))

    def test_artikal_etiketa_print(self):
        import base64
        import io

        from PIL import Image

        self.client.force_login(self.user)
        self.product.barkod = '3870123456789'
        self.product.save(update_fields=['barkod'])
        page = self.client.get(reverse('staff_magacin_artikal_stampa', args=[self.product.pk]))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'size: A4 portrait')
        self.assertContains(page, 'margin: 10mm')
        self.assertContains(page, 'grid-template-columns: repeat(4, 45mm)')
        self.assertContains(page, 'grid-template-rows: repeat(7, 36mm)')
        self.assertContains(page, 'class="label"', count=28)
        self.assertContains(page, 'Test braid', count=28)
        self.assertContains(page, 'font-size: 3mm')
        self.assertContains(page, 'font-size: 2.2mm')
        self.assertContains(page, 'ŠIFRA: TST-1')
        self.assertContains(page, '3870123456789')
        self.assertContains(page, '10,00')
        self.assertContains(page, 'cijena-km')
        self.assertContains(page, 'data:image/png;base64,')
        self.assertContains(page, 'window.print()')
        self.assertNotContains(page, 'ZD421')
        html = page.content.decode()
        marker = 'data:image/png;base64,'
        start = html.index(marker) + len(marker)
        end = html.index('"', start)
        png = base64.b64decode(html[start:end])
        self.assertGreater(len(png), 40)
        self.assertEqual(png[:8], b'\x89PNG\r\n\x1a\n')
        img = Image.open(io.BytesIO(png))
        self.assertEqual(img.mode, 'RGB')
        self.assertGreater(img.width, 40)
        self.assertGreater(img.height, 10)

        variation = ProductVariation.objects.create(
            artikal=self.product,
            naziv='Crvena',
            sifra='TST-1-R',
            cijena=Decimal('12.50'),
        )
        var_page = self.client.get(
            reverse('staff_magacin_artikal_stampa', args=[self.product.pk]),
            {'varijacija': variation.pk},
        )
        self.assertEqual(var_page.status_code, 200)
        self.assertContains(var_page, 'Test braid — Crvena')
        self.assertContains(var_page, 'TST-1-R')
        self.assertContains(var_page, '12,50')
        self.assertContains(var_page, '3870123456789')

        no_bar = self.client.get(reverse('staff_magacin_artikal_stampa', args=[self.zero.pk]))
        self.assertContains(no_bar, 'Prazan lager')
        self.assertContains(no_bar, 'ZERO-1')
        self.assertContains(no_bar, '2,00')
        self.assertContains(no_bar, 'data:image/png;base64,')

        missing = self.client.get(
            reverse('staff_magacin_artikal_stampa', args=[self.product.pk]),
            {'varijacija': '999999'},
        )
        self.assertEqual(missing.status_code, 302)
        self.assertEqual(missing['Location'], reverse('staff_magacin_artikal', args=[self.product.pk]))

    def test_stampa_cijena_menu_same_and_mixed(self):
        self.client.force_login(self.user)
        nav = self.client.get(reverse('staff_magacin_artikli'))
        html = nav.content.decode()
        brzi = html.find(reverse('staff_magacin_brzi_unos'))
        stampa = html.find(reverse('staff_magacin_stampa_cijena'))
        self.assertNotEqual(brzi, -1)
        self.assertNotEqual(stampa, -1)
        self.assertLess(brzi, stampa)
        self.assertContains(nav, 'Štampaj cijenu')

        home = self.client.get(reverse('staff_magacin_stampa_cijena'))
        self.assertEqual(home.status_code, 200)
        self.assertContains(home, '4 u redu')
        self.assertContains(home, '28 etiketa')
        self.assertContains(home, 'Ista cijena')
        self.assertContains(home, 'Različite cijene')
        self.assertContains(home, reverse('staff_magacin_stampa_cijena_ista'))
        self.assertContains(home, reverse('staff_magacin_stampa_cijena_razlicite'))

        ista = self.client.get(reverse('staff_magacin_stampa_cijena_ista'), {'artikal': self.product.pk})
        self.assertEqual(ista.status_code, 200)
        self.assertContains(ista, 'Test braid')
        self.assertContains(ista, 'Koliko cijena da odstampa')

        missing_n = self.client.get(reverse('staff_magacin_stampa_cijena_print'), {
            'mod': 'ista',
            'artikal': self.product.pk,
        })
        self.assertEqual(missing_n.status_code, 302)

        same = self.client.get(reverse('staff_magacin_stampa_cijena_print'), {
            'mod': 'ista',
            'artikal': self.product.pk,
            'n': '4',
        })
        self.assertEqual(same.status_code, 200)
        self.assertContains(same, 'size: A4 portrait')
        self.assertContains(same, 'class="label"', count=4)
        self.assertContains(same, 'Test braid', count=4)
        self.assertContains(same, '10,00')

        mixed_page = self.client.get(reverse('staff_magacin_stampa_cijena_razlicite'))
        self.assertEqual(mixed_page.status_code, 200)
        self.assertContains(mixed_page, 'Dodaj artikal')
        self.assertContains(mixed_page, 'Katalog')
        self.assertContains(mixed_page, 'scCatalog')

        mixed = self.client.post(reverse('staff_magacin_stampa_cijena_print'), {
            'mod': 'razlicite',
            'stavka': [str(self.product.pk), str(self.zero.pk)],
        })
        self.assertEqual(mixed.status_code, 200)
        self.assertContains(mixed, 'class="label"', count=2)
        self.assertContains(mixed, 'Test braid', count=1)
        self.assertContains(mixed, 'Prazan lager', count=1)
        self.assertContains(mixed, '10,00')
        self.assertContains(mixed, '2,00')

    def test_stampa_deklaracije_brands_and_print(self):
        self.client.force_login(self.user)
        nav = self.client.get(reverse('staff_magacin_artikli'))
        html = nav.content.decode()
        cijena = html.find(reverse('staff_magacin_stampa_cijena'))
        dekl = html.find(reverse('staff_magacin_stampa_deklaracije'))
        self.assertNotEqual(cijena, -1)
        self.assertNotEqual(dekl, -1)
        self.assertLess(cijena, dekl)
        self.assertContains(nav, 'Štampaj deklaracije')

        page = self.client.get(reverse('staff_magacin_stampa_deklaracije'))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'Štampaj deklaracije')
        self.assertContains(page, '65 deklaracija')
        self.assertContains(page, '38 × 21,2 mm')
        self.assertContains(page, 'Tip Top Office')
        self.assertContains(page, 'Novi brend')
        self.assertContains(page, 'Naziv')
        self.assertContains(page, 'Uvoznik')
        self.assertContains(page, 'Adresa')
        self.assertContains(page, 'Zemlja izvoza')
        self.assertContains(page, 'Zemlja porijekla')
        self.assertContains(page, 'Godina uvoza')
        self.assertContains(page, 'Telefon')

        missing = self.client.post(reverse('staff_magacin_stampa_deklaracije'), {
            'action': 'save',
            'naziv': '',
            'uvoznik': 'Oprema za ribolov',
        })
        self.assertEqual(missing.status_code, 302)
        self.assertFalse(MagacinDeklaracijaBrend.objects.exists())

        payload = {
            'action': 'save',
            'naziv': 'Fox',
            'uvoznik': 'Oprema za ribolov',
            'adresa': 'Sarajevo, BiH',
            'zemlja_izvoza': 'UK',
            'zemlja_porijekla': 'Kina',
            'godina_uvoza': '2026',
            'telefon': '033 000 000',
        }
        created = self.client.post(reverse('staff_magacin_stampa_deklaracije'), payload)
        self.assertEqual(created.status_code, 302)
        brend = MagacinDeklaracijaBrend.objects.get(naziv='Fox')
        self.assertEqual(brend.uvoznik, 'Oprema za ribolov')
        self.assertEqual(brend.adresa, 'Sarajevo, BiH')
        self.assertEqual(brend.zemlja_izvoza, 'UK')
        self.assertEqual(brend.zemlja_porijekla, 'Kina')
        self.assertEqual(brend.godina_uvoza, '2026')
        self.assertEqual(brend.telefon, '033 000 000')

        listed = self.client.get(reverse('staff_magacin_stampa_deklaracije'))
        self.assertContains(listed, 'Fox')
        self.assertContains(listed, reverse('staff_magacin_stampa_deklaracije_print', args=[brend.pk]))
        self.assertContains(listed, 'Štampaj')
        self.assertContains(listed, 'Uvoznik: Oprema za ribolov')

        duplicate = self.client.post(reverse('staff_magacin_stampa_deklaracije'), payload)
        self.assertEqual(duplicate.status_code, 302)
        self.assertEqual(MagacinDeklaracijaBrend.objects.filter(naziv='Fox').count(), 1)

        printed = self.client.get(
            reverse('staff_magacin_stampa_deklaracije_print', args=[brend.pk]),
        )
        self.assertEqual(printed.status_code, 200)
        self.assertContains(printed, 'size: A4 portrait')
        self.assertContains(printed, 'margin: 0')
        self.assertContains(printed, 'padding: 10.7mm 10mm')
        self.assertContains(printed, 'grid-template-columns: repeat(5, 38mm)')
        self.assertContains(printed, 'grid-template-rows: repeat(13, 21.2mm)')
        self.assertContains(printed, 'column-gap: 0;')
        self.assertContains(printed, 'row-gap: 0;')
        self.assertContains(printed, 'width: 38mm')
        self.assertContains(printed, 'height: 21.2mm')
        self.assertContains(printed, '<article class="label"', count=65)
        self.assertContains(printed, 'Naziv:')
        self.assertContains(printed, 'Fox')
        self.assertContains(printed, 'Uvoznik:')
        self.assertContains(printed, 'Oprema za ribolov')
        self.assertContains(printed, 'Adresa:')
        self.assertContains(printed, 'Sarajevo, BiH')
        self.assertContains(printed, 'Zemlja izvoza:')
        self.assertContains(printed, 'Zemlja porijekla:')
        self.assertContains(printed, 'Godina uvoza:')
        self.assertContains(printed, '2026')
        self.assertContains(printed, 'Telefon:')
        self.assertContains(printed, '033 000 000')
        self.assertContains(printed, 'window.print()')
        self.assertContains(printed, 'margine nijedne')

        few = self.client.get(
            reverse('staff_magacin_stampa_deklaracije_print', args=[brend.pk]),
            {'n': '3'},
        )
        self.assertEqual(few.status_code, 200)
        self.assertContains(few, '<article class="label"', count=3)

        edited = self.client.get(
            reverse('staff_magacin_stampa_deklaracije'),
            {'id': brend.pk},
        )
        self.assertContains(edited, 'Izmijeni brend')
        self.assertContains(edited, 'Oprema za ribolov')
        self.assertContains(edited, 'value="2026"')

        updated = self.client.post(reverse('staff_magacin_stampa_deklaracije'), {
            'action': 'save',
            'brend_id': brend.pk,
            'naziv': 'Fox Rage',
            'uvoznik': 'Oprema za ribolov',
            'adresa': 'Sarajevo, BiH',
            'zemlja_izvoza': 'UK',
            'zemlja_porijekla': 'UK',
            'godina_uvoza': '2026',
            'telefon': '033 000 000',
        })
        self.assertEqual(updated.status_code, 302)
        brend.refresh_from_db()
        self.assertEqual(brend.naziv, 'Fox Rage')
        self.assertEqual(brend.zemlja_porijekla, 'UK')

        deleted = self.client.post(reverse('staff_magacin_stampa_deklaracije'), {
            'action': 'delete',
            'brend_id': brend.pk,
        })
        self.assertEqual(deleted.status_code, 302)
        self.assertFalse(MagacinDeklaracijaBrend.objects.filter(pk=brend.pk).exists())

    def test_brzi_unos_same_as_admin_flow(self):
        self.client.force_login(self.user)
        nav = self.client.get(reverse('staff_magacin_artikli'))
        self.assertContains(nav, 'Brzi unos / Aktivacija')
        self.assertContains(nav, reverse('staff_magacin_brzi_unos'))
        self.assertContains(nav, 'Štampaj cijenu')
        self.assertContains(nav, reverse('staff_magacin_stampa_cijena'))
        self.assertContains(nav, 'Štampaj deklaracije')
        self.assertContains(nav, reverse('staff_magacin_stampa_deklaracije'))

        page = self.client.get(reverse('staff_magacin_brzi_unos'))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'Skeniraj barkod kamerom')
        self.assertContains(page, 'Šifra, barkod ili naziv')
        self.assertContains(page, 'Pokreni kameru')
        self.assertContains(page, 'Savjeti')
        self.assertContains(page, 'Nazad na Artikle')
        self.assertContains(page, 'bu-tips')
        self.assertContains(page, 'bu-sheet')

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
        self.assertContains(act, reverse('staff_magacin_brzi_unos_novi'))

        missing = self.client.get(reverse('staff_magacin_brzi_unos'), {'q': 'XYZ-NOVI-99'})
        self.assertEqual(missing.status_code, 200)
        self.assertContains(missing, reverse('staff_magacin_brzi_unos_novi'))
        self.assertContains(missing, '+ Novi artikal')

        form = self.client.get(reverse('staff_magacin_brzi_unos_novi'), {'q': 'XYZ-NOVI-99'})
        self.assertEqual(form.status_code, 200)
        self.assertContains(form, 'Novi artikal')
        self.assertContains(form, 'value="XYZ-NOVI-99"')
        blocked = self.client.post(reverse('staff_magacin_brzi_unos_novi'), {
            'naziv': '',
            'cijena': '',
        })
        self.assertEqual(blocked.status_code, 200)
        self.assertContains(blocked, 'Naziv je obavezan')
        created = self.client.post(reverse('staff_magacin_brzi_unos_novi'), {
            'naziv': 'Novi test artikal',
            'cijena': '7,50',
            'sifra': 'XYZ-NOVI-99',
        })
        self.assertRedirects(created, reverse('staff_magacin_brzi_unos'))
        novi = Product.objects.get(sifra='XYZ-NOVI-99')
        self.assertEqual(novi.naziv, 'Novi test artikal')
        self.assertEqual(novi.cijena, Decimal('7.50'))
        self.assertTrue(novi.aktivan)
        self.assertFalse(novi.na_stanju)
        self.assertEqual(novi.stanje, 0)
        self.assertIsNone(novi.brend_id)
        self.assertIsNone(novi.kategorija_id)

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

    def test_search_shows_in_stock_when_any_location_has_qty(self):
        self.client.force_login(self.user)
        mp = WarehouseLocation.objects.create(sifra='B-03', naziv='Maloprodaja Sarajevo')
        apply_movement(product=self.zero, location=mp, tip='prijem', kolicina=3)
        found = self.client.get(reverse('staff_magacin_artikli'), {'pretraga': 'Prazan'})
        self.assertEqual(found.status_code, 200)
        self.assertContains(found, 'Prazan lager')
        self.assertContains(found, '>Na stanju<')
        self.assertNotContains(found, 'Nije na stanju')
        lookup = self.client.get(reverse('staff_magacin_artikli_lookup'), {'q': 'Prazan'})
        ids = [row['id'] for row in lookup.json()['results']]
        self.assertIn(self.zero.pk, ids)
        row = next(item for item in lookup.json()['results'] if item['id'] == self.zero.pk)
        self.assertGreater(row['na_stanju'], 0)

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
        self.assertContains(page, 'MPC')
        self.assertContains(page, 'Na stanju')
        self.assertContains(page, '10.00 KM')
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
            'staff_magacin_kupci',
            'staff_magacin_dobavljaci',
            'staff_magacin_ponude',
            'staff_magacin_uvoz',
            'staff_magacin_nivelacije',
            'staff_magacin_pakuj',
            'staff_magacin_izvjestaji',
            'staff_magacin_popis',
            'staff_magacin_fali_na_sajtu',
            'staff_magacin_vp_narudzba',
        ):
            response = self.client.get(reverse(name))
            self.assertEqual(response.status_code, 200, name)

    def test_fali_na_sajtu_lists_warehouse_only_and_prenos_mp(self):
        self.client.force_login(self.user)
        mp = WarehouseLocation.objects.filter(naziv__icontains='maloprodaja').first()
        if mp is None:
            mp = WarehouseLocation.objects.create(sifra='B-03', naziv='Maloprodaja Sarajevo')
        listed = self.client.get(reverse('staff_magacin_fali_na_sajtu'))
        self.assertEqual(listed.status_code, 200)
        self.assertContains(listed, 'Fali na sajtu')
        self.assertContains(listed, self.product.naziv)
        self.assertContains(listed, 'T-1')
        self.assertContains(listed, 'Max za prenos')
        self.assertContains(listed, 'Prenesi u MP')
        self.assertNotContains(listed, self.zero.naziv)
        src = WarehouseLocation.objects.get(sifra='T-1')
        sent = self.client.post(reverse('staff_magacin_fali_na_sajtu'), {
            'action': 'prenos_mp',
            'product_id': str(self.product.pk),
            'variation_id': '',
            'location_id': str(src.pk),
            'kolicina': '3',
        })
        self.assertEqual(sent.status_code, 302)
        order = Order.objects.get(ime_prezime='Prenos u MP')
        self.assertEqual(order.stavke.get().kolicina, 3)
        pending = self.client.get(reverse('staff_magacin_fali_na_sajtu'))
        self.assertContains(pending, 'Prenos je već na Pickingu')
        apply_movement(product=self.product, location=mp, tip='prijem', kolicina=1)
        gone = self.client.get(reverse('staff_magacin_fali_na_sajtu'))
        self.assertNotContains(gone, self.product.naziv)

    def test_popis_without_location_asks_for_location(self):
        self.client.force_login(self.user)
        MagacinPopis.objects.create(kreirao=self.user)
        page = self.client.get(reverse('staff_magacin_popis'))
        self.assertContains(page, 'Prvo izaberi lokaciju')
        self.assertContains(page, 'Šifra ili naziv lokacije')
        self.assertNotContains(page, 'Popisuj ovu lokaciju')
        self.assertNotContains(page, 'id="ppQuery"')
        loc = WarehouseLocation.objects.get(sifra='T-1')
        found = self.client.get(reverse('staff_magacin_popis'), {'q': 'T-1'})
        self.assertContains(found, 'Popisuj ovu lokaciju')
        self.assertContains(found, 'T-1')
        chosen = self.client.post(reverse('staff_magacin_popis'), {
            'action': 'novi', 'location_id': loc.pk,
        })
        self.assertEqual(chosen.status_code, 302)
        popis = MagacinPopis.objects.get()
        self.assertEqual(popis.location_id, loc.pk)
        live = self.client.get(reverse('staff_magacin_popis'))
        self.assertContains(live, 'Popisuješ lokaciju')
        self.assertContains(live, 'T-1')
        self.assertContains(live, 'id="ppQuery"')

    def test_popis_persists_until_delete_or_finish(self):
        self.client.force_login(self.user)
        loc = WarehouseLocation.objects.get(sifra='T-1')
        home = self.client.get(reverse('staff_magacin_popis'))
        self.assertContains(home, 'Prvo izaberi lokaciju')
        self.assertContains(home, 'Šifra ili naziv lokacije')
        self.assertNotContains(home, 'Popisuj ovu lokaciju')
        found = self.client.get(reverse('staff_magacin_popis'), {'q': 'T-1'})
        self.assertContains(found, 'Popisuj ovu lokaciju')
        self.assertContains(found, 'T-1')
        start = self.client.post(reverse('staff_magacin_popis'), {
            'action': 'novi', 'location_id': loc.pk,
        })
        self.assertEqual(start.status_code, 302)
        popis = MagacinPopis.objects.get()
        self.assertEqual(popis.status, MagacinPopis.Status.U_TOKU)
        self.assertEqual(popis.location_id, loc.pk)
        added = self.client.post(reverse('staff_magacin_popis'), {
            'action': 'dodaj',
            'product_id': self.product.pk,
            'kolicina': '3',
        })
        self.assertEqual(added.status_code, 302)
        again = self.client.get(reverse('staff_magacin_popis'))
        self.assertContains(again, 'Popisuješ lokaciju')
        self.assertContains(again, self.product.naziv)
        self.assertContains(again, 'Na stanju')
        self.assertContains(again, 'Popisano')
        self.assertContains(again, 'Razlika')
        self.assertContains(again, 'pp-count-box')
        self.assertContains(again, 'započet')
        self.assertContains(again, '3')
        self.assertEqual(MagacinPopis.objects.count(), 1)
        finished = self.client.post(reverse('staff_magacin_popis'), {'action': 'zavrsi'})
        self.assertEqual(finished.status_code, 302)
        popis.refresh_from_db()
        self.assertEqual(popis.status, MagacinPopis.Status.ZAVRSEN)
        stock = WarehouseStock.objects.get(product=self.product, location=loc)
        self.assertEqual(stock.kolicina, 3)
        done = self.client.get(reverse('staff_magacin_popis_detail', args=[popis.pk]))
        self.assertContains(done, 'Ažurirano na 3')
        self.assertContains(done, 'Štampaj')
        self.assertContains(done, 'Razlike')
        printed = self.client.get(reverse('staff_magacin_popis_stampa') + f'?id={popis.pk}')
        self.assertEqual(printed.status_code, 200)
        self.assertContains(printed, 'Popisano')
        self.assertContains(printed, self.product.naziv)
        self.assertContains(printed, self.product.sifra)
        self.assertContains(printed, '3')
        diffs = self.client.get(reverse('staff_magacin_popis_stampa') + f'?id={popis.pk}&razlike=1')
        self.assertEqual(diffs.status_code, 200)
        self.assertContains(diffs, 'Razlike popisa')
        self.assertContains(diffs, 'is-diff')
        html = diffs.content.decode()
        self.assertLess(html.find('Popisano'), html.find('Prije'))
        self.assertLess(html.find('Prije'), html.find('Razlika'))
        listing = self.client.get(finished['Location'])
        self.assertContains(listing, 'Završeni popisi')
        self.assertContains(listing, f'Popis #{popis.pk}')
        self.assertNotContains(listing, 'is-done')
        self.assertContains(listing, 'Štampaj')
        self.assertContains(listing, 'Razlike')
        self.assertContains(listing, reverse('staff_magacin_popis_detail', args=[popis.pk]))
        self.assertNotContains(listing, 'Čekiraj')
        popis.refresh_from_db()
        self.assertFalse(popis.odstampan)
        stamped = self.client.post(reverse('staff_magacin_popis_stampa'), {'id': str(popis.pk)})
        self.assertEqual(stamped.status_code, 200)
        popis.refresh_from_db()
        self.assertTrue(popis.odstampan)
        again_list = self.client.get(reverse('staff_magacin_popis') + '?nova=1')
        self.assertContains(again_list, 'is-done')
        self.assertContains(again_list, 'Završeno')
        self.assertContains(again_list, f'Popis #{popis.pk}')
        self.client.post(reverse('staff_magacin_popis'), {
            'action': 'novi', 'location_id': loc.pk,
        })
        self.assertEqual(MagacinPopis.objects.filter(status=MagacinPopis.Status.U_TOKU).count(), 1)
        live = MagacinPopis.objects.get(status=MagacinPopis.Status.U_TOKU)
        self.client.post(reverse('staff_magacin_popis'), {'action': 'obrisi'})
        self.assertFalse(MagacinPopis.objects.filter(pk=live.pk).exists())

    def test_popis_matching_qty_marked_correct(self):
        self.client.force_login(self.user)
        loc = WarehouseLocation.objects.get(sifra='T-1')
        self.client.post(reverse('staff_magacin_popis'), {
            'action': 'novi', 'location_id': loc.pk,
        })
        self.client.post(reverse('staff_magacin_popis'), {
            'action': 'dodaj',
            'product_id': self.product.pk,
            'kolicina': '8',
        })
        finished = self.client.post(reverse('staff_magacin_popis'), {'action': 'zavrsi'})
        self.assertEqual(finished.status_code, 302)
        stock = WarehouseStock.objects.get(product=self.product, location=loc)
        self.assertEqual(stock.kolicina, 8)
        home = self.client.get(finished['Location'])
        self.assertContains(home, 'Završeni popisi')
        self.assertNotContains(home, 'is-done')
        self.client.post(reverse('staff_magacin_popis_stampa'), {
            'id': str(MagacinPopis.objects.get(status=MagacinPopis.Status.ZAVRSEN).pk),
        })
        marked = self.client.get(reverse('staff_magacin_popis') + '?nova=1')
        self.assertContains(marked, 'is-done')
        self.assertContains(marked, 'Završeno')
        page = self.client.get(reverse('staff_magacin_popis_detail', args=[
            MagacinPopis.objects.get(status=MagacinPopis.Status.ZAVRSEN).pk
        ]))
        self.assertContains(page, 'Tačna količina')
        self.assertContains(page, 'Štampaj')
        self.assertContains(page, 'Razlike')
        diffs = self.client.get(reverse('staff_magacin_popis_stampa') + f'?id={MagacinPopis.objects.get(status=MagacinPopis.Status.ZAVRSEN).pk}&razlike=1')
        self.assertContains(diffs, 'class="is-ok"')
        self.assertNotContains(diffs, 'class="is-diff"')
        missing = self.client.post(reverse('staff_magacin_popis'), {'action': 'novi'})
        self.assertEqual(missing.status_code, 302)
        self.assertEqual(MagacinPopis.objects.filter(status=MagacinPopis.Status.U_TOKU).count(), 0)

    def test_popis_phone_scan_ajax_counts(self):
        self.client.force_login(self.user)
        url = reverse('staff_magacin_popis')
        page = self.client.get(url)
        self.assertContains(page, 'Šifra ili naziv lokacije')
        loc = WarehouseLocation.objects.get(sifra='T-1')
        self.client.post(url, {'action': 'novi', 'location_id': loc.pk})
        live = self.client.get(url)
        self.assertContains(live, 'id="ppQuery"')
        self.assertContains(live, 'Skeniraj')
        self.assertContains(live, 'data-mg-scan-target="ppQuery"')
        self.assertContains(live, 'pp-dock')
        self.assertContains(live, 'pp-foot')
        self.assertContains(live, 'form="ppFinishForm"')
        self.assertContains(live, 'Završeno')
        self.assertContains(live, 'Skeniraj barkod')
        added = self.client.post(url, {
            'action': 'dodaj',
            'product_id': self.product.pk,
            'kolicina': '1',
        }, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(added.status_code, 200)
        payload = added.json()
        self.assertTrue(payload['ok'])
        self.assertEqual(payload['count'], 1)
        self.assertEqual(payload['total_qty'], 1)
        self.assertEqual(payload['stavke'][0]['naziv'], self.product.naziv)
        self.assertEqual(payload['stavke'][0]['ocekivano'], 8)
        self.assertEqual(payload['stavke'][0]['kolicina'], 1)
        self.assertEqual(payload['stavke'][0]['razlika'], -7)
        stavka_id = payload['added_id']
        again = self.client.post(url, {
            'action': 'dodaj',
            'product_id': self.product.pk,
            'kolicina': '1',
        }, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(again.json()['total_qty'], 2)
        bumped = self.client.post(url, {
            'action': 'set_qty',
            'stavka_id': stavka_id,
            'kolicina': '5',
        }, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(bumped.json()['total_qty'], 5)
        gone = self.client.post(url, {
            'action': 'ukloni',
            'stavka_id': stavka_id,
        }, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(gone.json()['count'], 0)

    def test_popis_pause_then_new_and_resume(self):
        self.client.force_login(self.user)
        url = reverse('staff_magacin_popis')
        loc = WarehouseLocation.objects.get(sifra='T-1')
        self.client.post(url, {'action': 'novi', 'location_id': loc.pk})
        first = MagacinPopis.objects.get()
        self.client.post(url, {
            'action': 'dodaj',
            'product_id': self.product.pk,
            'kolicina': '2',
            'popis_id': first.pk,
        })
        live = self.client.get(reverse('staff_magacin_popis_detail', args=[first.pk]))
        self.assertContains(live, 'Pauziraj')
        paused = self.client.post(url, {'action': 'pauziraj', 'popis_id': first.pk})
        self.assertEqual(paused.status_code, 302)
        first.refresh_from_db()
        self.assertEqual(first.status, MagacinPopis.Status.PAUZIRAN)
        home = self.client.get(url)
        self.assertContains(home, 'Šifra ili naziv lokacije')
        self.assertContains(home, 'Pauzirani popisi')
        self.assertContains(home, f'Popis #{first.pk}')
        self.assertContains(home, 'Nastavi')
        created = self.client.post(url, {'action': 'novi', 'location_id': loc.pk})
        self.assertEqual(created.status_code, 302)
        second = MagacinPopis.objects.get(status=MagacinPopis.Status.U_TOKU)
        self.assertNotEqual(second.pk, first.pk)
        self.client.post(url, {
            'action': 'dodaj',
            'product_id': self.zero.pk,
            'kolicina': '1',
            'popis_id': second.pk,
        })
        resumed = self.client.post(
            reverse('staff_magacin_popis_detail', args=[first.pk]),
            {'action': 'nastavi', 'popis_id': first.pk},
        )
        self.assertEqual(resumed.status_code, 302)
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(first.status, MagacinPopis.Status.U_TOKU)
        self.assertEqual(second.status, MagacinPopis.Status.PAUZIRAN)
        self.assertEqual(MagacinPopis.objects.filter(status=MagacinPopis.Status.U_TOKU).count(), 1)
        opened = self.client.get(reverse('staff_magacin_popis_detail', args=[first.pk]))
        self.assertContains(opened, self.product.naziv)
        self.assertContains(opened, '2')
        self.assertContains(opened, 'id="ppQuery"')
        other = self.client.get(reverse('staff_magacin_popis_detail', args=[second.pk]))
        self.assertContains(other, 'Pauziran')
        self.assertContains(other, self.zero.naziv)

    def test_vp_order_persists_customer_and_vp_price(self):
        self.client.force_login(self.user)
        listed = self.client.get(reverse('staff_magacin_narudzbe'))
        self.assertContains(listed, 'VP narudžbe')
        self.assertContains(listed, reverse('staff_magacin_vp_narudzba'))
        start = self.client.post(reverse('staff_magacin_vp_narudzba'), {'action': 'novi'})
        self.assertEqual(start.status_code, 302)
        draft = MagacinVpNarudzba.objects.get()
        vp_form = self.client.get(reverse('staff_magacin_vp_narudzba'))
        self.assertContains(vp_form, 'Rezervacija')
        self.assertContains(vp_form, 'id="vpCatalog"')
        self.assertContains(vp_form, 'id="vpCatalogBtn"')
        self.assertContains(vp_form, 'Katalog')
        self.assertContains(vp_form, 'hidden')
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
        with_cust = self.client.get(reverse('staff_magacin_vp_narudzba'))
        self.assertContains(with_cust, 'is-customer-set')
        self.assertContains(with_cust, 'vpCustomerChange')
        self.assertContains(with_cust, 'vpCustomerEdit')
        self.assertContains(with_cust, 'Promijeni')
        self.assertContains(with_cust, 'Izmijeni')
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

    def test_vp_bulk_imports_matching_names_and_skips_unknown(self):
        paste = (
            '#,\tArtikal,\tKol.,\tCijena\t,Ukupno\n'
            '1,\tMT13982 MATE Goliath Camo Carp Line 1000m 0.31mm\n'
            'Šifra: 7943\t,3\t,19.20 KM\t,57.60 KM\n'
            '2,\tNepostojeci Artikal XYZ\n'
            'Šifra: 9999\t,1\t,10.00 KM\t,10.00 KM\n'
            '3,\tTest braid\t,2\t,8.50 KM\t,17.00 KM\n'
        )
        rows = parse_vp_bulk_text(paste)
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0]['naziv'], 'MT13982 MATE Goliath Camo Carp Line 1000m 0.31mm')
        self.assertEqual(rows[0]['sifra'], '7943')
        self.assertEqual(rows[0]['qty'], 3)
        self.assertEqual(rows[0]['cijena'], Decimal('19.20'))
        self.assertEqual(rows[2]['naziv'], 'Test braid')
        self.assertEqual(rows[2]['qty'], 2)
        self.assertEqual(rows[2]['cijena'], Decimal('8.50'))

        mate = Product.objects.create(
            naziv='MT13982 MATE Goliath Camo Carp Line 1000m 0.31mm',
            sifra='7943', cijena=Decimal('26.50'),
            stanje=10, na_stanju=True, magacin_sync_at=timezone.now(),
        )
        loc = WarehouseLocation.objects.get(sifra='T-1')
        apply_movement(product=mate, location=loc, tip='prijem', kolicina=10)

        self.client.force_login(self.user)
        self.client.post(reverse('staff_magacin_vp_narudzba'), {'action': 'novi'})
        form = self.client.get(reverse('staff_magacin_vp_narudzba'))
        self.assertContains(form, 'id="vpBulkBtn"')
        self.assertContains(form, 'id="vpBulkModal"')
        self.assertContains(form, 'Bulk unos')

        imported = self.client.post(
            reverse('staff_magacin_vp_narudzba'),
            {'action': 'bulk', 'tekst': paste},
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )
        self.assertEqual(imported.status_code, 200)
        data = imported.json()
        self.assertTrue(data['ok'])
        self.assertEqual(data['added'], 2)
        self.assertEqual(len(data['skipped']), 1)
        self.assertIn('Nepostojeci Artikal XYZ', data['skipped'][0]['naziv'])
        draft = MagacinVpNarudzba.objects.get(status=MagacinVpNarudzba.Status.U_TOKU)
        names = list(draft.stavke.values_list('naziv', flat=True))
        self.assertIn(mate.naziv, names)
        self.assertIn('Test braid', names)
        self.assertNotIn('Nepostojeci Artikal XYZ', names)
        mate_line = draft.stavke.get(product=mate)
        self.assertEqual(mate_line.kolicina, 3)
        self.assertEqual(mate_line.cijena, Decimal('19.20'))
        braid_line = draft.stavke.get(product=self.product)
        self.assertEqual(braid_line.kolicina, 2)
        self.assertEqual(braid_line.cijena, Decimal('8.50'))

        tsv = '1\tTest braid\t1\t6.00 KM\t6.00 KM'
        again = self.client.post(
            reverse('staff_magacin_vp_narudzba'),
            {'action': 'bulk', 'tekst': tsv},
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )
        self.assertEqual(again.status_code, 200)
        braid_line.refresh_from_db()
        self.assertEqual(braid_line.kolicina, 3)
        self.assertEqual(braid_line.cijena, Decimal('6.00'))
        again_data = again.json()
        self.assertTrue(again_data['bulk'])
        osnova = Decimal('19.20') * 3 + Decimal('6.00') * 3
        pdv = (osnova * Decimal('0.17')).quantize(Decimal('0.01'))
        self.assertEqual(Decimal(again_data['ukupno']), osnova)
        self.assertEqual(Decimal(again_data['pdv']), pdv)
        self.assertEqual(Decimal(again_data['ukupno_sa_pdv']), osnova + pdv)

        page = self.client.get(reverse('staff_magacin_vp_narudzba'))
        self.assertContains(page, 'PDV 17%')
        self.assertContains(page, 'Ukupno sa PDV')
        self.assertContains(page, f'{osnova + pdv} KM')

        customer = WarehouseCustomer.objects.create(
            ime_prezime='Bulk Pdv', telefon='061121314',
        )
        self.client.post(reverse('staff_magacin_vp_narudzba'), {
            'action': 'kupac', 'customer_id': customer.pk,
        })
        finished = self.client.post(reverse('staff_magacin_vp_narudzba'), {'action': 'zavrsi'})
        self.assertEqual(finished.status_code, 302)
        order = Order.objects.get(ime_prezime='Bulk Pdv')
        self.assertEqual(order.ukupno, osnova + pdv)
        self.assertEqual(order.medjuzbir, osnova + pdv)
        self.assertIn('PDV 17%', order.napomena)

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
        self.assertEqual(stock_totals(self.product)['dostupno'], 7)
        self.assertEqual(stock_totals(self.product)['rezervisano'], 1)
        self.assertEqual(self.product.stanje, 8)
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
        self.assertTrue(order.zapakovana)
        self.assertEqual(order.status, Order.Status.ZAVRSENA)
        self.assertEqual(order.get_status_label(), 'Validatovana')
        self.assertEqual(order.get_status_pill_class(), 'mg-st-zavrsena')
        self.product.refresh_from_db()
        self.assertEqual(self.product.stanje, 7)
        self.assertEqual(stock_totals(self.product)['dostupno'], 7)
        self.assertEqual(stock_totals(self.product)['rezervisano'], 0)
        self.assertTrue(
            WarehouseMovement.objects.filter(
                product=self.product,
                tip=WarehouseMovement.Tip.PRODAJA,
                napomena__contains=order.broj,
            ).exists()
        )
        still = self.client.get(reverse('staff_magacin_pakuj'), {'status': 'sve'})
        self.assertContains(still, 'Print Kupac')
        self.assertContains(still, 'Završeno')
        self.assertNotContains(still, 'Štampaj zapakovano')
        self.assertEqual(still.context['new_pack_orders_count'], 0)
        self.assertNotContains(still, reverse('staff_magacin_pakuj_stampaj_zapakovano', args=[order.broj]))
        open_list = [row.broj for row in self.client.get(reverse('staff_magacin_narudzbe')).context['orders']]
        self.assertNotIn(order.broj, open_list)
        validated_page = self.client.get(reverse('staff_magacin_narudzbe'), {'validirane': '1'})
        validated_list = [row.broj for row in validated_page.context['orders']]
        self.assertIn(order.broj, validated_list)
        self.assertGreaterEqual(validated_page.context['validated_count'], 1)
        self.assertContains(validated_page, 'Validatovana')
        self.assertContains(validated_page, 'mg-st-zavrsena')
        self.assertContains(validated_page, 'Štampaj količine za fakturu')
        self.assertContains(validated_page, reverse('staff_magacin_narudzbe_stampa_kolicine'))
        qty_page = self.client.get(
            reverse('staff_magacin_narudzbe_stampa_kolicine'),
            {'b': order.broj},
        )
        self.assertEqual(qty_page.status_code, 200)
        self.assertContains(qty_page, 'Količine za fakturu')
        self.assertContains(qty_page, self.product.naziv)
        self.assertContains(qty_page, '>1<')
        self.assertNotContains(qty_page, 'KM')
        self.assertContains(qty_page, '@page { size: A4 portrait; margin: 16mm 24mm; }')
        self.assertContains(qty_page, 'font-size: 12px')
        self.assertContains(qty_page, 'max-width: 100%')
        self.assertContains(qty_page, 'margine sa strana 24 mm')
        printed = self.client.get(reverse('staff_magacin_pakuj_stampaj_zapakovano', args=[order.broj]))
        self.assertEqual(printed.status_code, 302)
        self.assertIn(reverse('staff_magacin_narudzbe_stampa'), printed['Location'])
        order.refresh_from_db()
        self.assertTrue(order.zapakovana)
        self.assertEqual(order.status, Order.Status.ZAVRSENA)
        gone = self.client.get(reverse('staff_magacin_pakuj'), {'status': 'sve'})
        self.assertContains(gone, 'Print Kupac')
        self.assertContains(gone, 'Završeno')
        self.assertNotContains(gone, 'Štampaj zapakovano')
        self.assertEqual(gone.context['new_pack_orders_count'], 0)
        shown = [
            row.broj
            for row in self.client.get(reverse('staff_magacin_narudzbe'), {'validirane': '1'}).context['orders']
        ]
        self.assertIn(order.broj, shown)

    def test_vp_picking_finish_deducts_stock_and_status_is_green(self):
        self.client.force_login(self.user)
        self.client.post(reverse('staff_magacin_vp_narudzba'), {'action': 'novi'})
        customer = WarehouseCustomer.objects.create(
            ime_prezime='VP Stanje', telefon='061222333',
        )
        self.client.post(reverse('staff_magacin_vp_narudzba'), {
            'action': 'kupac', 'customer_id': customer.pk,
        })
        self.client.post(reverse('staff_magacin_vp_narudzba'), {
            'action': 'dodaj', 'product_id': self.product.pk, 'kolicina': '3',
        })
        self.client.post(reverse('staff_magacin_vp_narudzba'), {'action': 'zavrsi'})
        order = Order.objects.get(ime_prezime='VP Stanje')
        item = order.stavke.get()
        pick_json = json.dumps([{
            'key': f'{item.pk}:T-1',
            'item_id': item.pk,
            'loc': 'T-1',
            'got': 3,
            'need': 3,
            'done': True,
        }])
        finished = self.client.post(
            reverse('staff_magacin_pakuj_detail', args=[order.broj]),
            {'action': 'validiraj', 'pick_json': pick_json},
        )
        self.assertRedirects(finished, reverse('staff_magacin_pakuj'))
        order.refresh_from_db()
        self.product.refresh_from_db()
        loc_stock = WarehouseStock.objects.get(
            product=self.product, location__sifra='T-1', variation__isnull=True,
        )
        self.assertEqual(order.lager_status, Order.LagerStatus.VALIDIRANO)
        self.assertEqual(order.status, Order.Status.ZAVRSENA)
        self.assertEqual(order.get_status_pill_class(), 'mg-st-zavrsena')
        self.assertEqual(loc_stock.kolicina, 5)
        self.assertEqual(loc_stock.rezervisano, 0)
        self.assertEqual(self.product.stanje, 5)
        self.assertEqual(stock_totals(self.product)['dostupno'], 5)
        page = self.client.get(reverse('staff_magacin_narudzbe'), {'validirane': '1'})
        self.assertContains(page, f'class="mg-pill {order.get_status_pill_class()}"')
        self.assertContains(page, 'Validatovana')

    def test_unpicked_item_is_left_off_invoice(self):
        from .views import _order_print_job
        from .views_magacin import apply_order_pick

        self.client.force_login(self.user)
        extra = Product.objects.create(
            naziv='Drugi artikal', sifra='ADD-2', cijena=Decimal('13.80'),
            stanje=4, na_stanju=True, magacin_sync_at=timezone.now(),
        )
        loc = WarehouseLocation.objects.get(sifra='T-1')
        apply_movement(product=extra, location=loc, tip='prijem', kolicina=4)
        created = self.client.post(reverse('staff_magacin_narudzba_nova'), {
            'ime_prezime': 'Skip Kupac',
            'telefon': '061555666',
            'product_id': [str(self.product.pk), str(extra.pk)],
            'variation_id': ['', ''],
            'kolicina': ['1', '2'],
            'mp_ok': ['0', '0'],
        })
        self.assertEqual(created.status_code, 302)
        order = Order.objects.get(ime_prezime='Skip Kupac')
        page = self.client.get(reverse('staff_magacin_pakuj_detail', args=[order.broj]))
        self.assertContains(page, 'Izmijeni narudžbu')
        self.assertContains(page, 'pk-edit-remove')
        self.assertContains(page, 'Završi picking')
        braid = order.stavke.get(artikal=self.product)
        extra_item = order.stavke.get(artikal=extra)
        apply_order_pick(order, [
            {
                'key': f'{braid.pk}:T-1',
                'item_id': braid.pk,
                'loc': 'T-1',
                'got': 1,
                'need': 1,
                'done': True,
            },
            {
                'key': f'{extra_item.pk}:T-1',
                'item_id': extra_item.pk,
                'loc': 'T-1',
                'got': 0,
                'need': 2,
                'done': False,
            },
        ], finalize=True, user=self.user)
        order.refresh_from_db()
        self.assertFalse(order.stavke.filter(pk=extra_item.pk).exists())
        self.assertEqual(order.stavke.get().artikal_id, self.product.pk)
        job = _order_print_job(order)
        names = [row['naziv'] for row in job['stavke']]
        self.assertTrue(any('Test braid' in name for name in names))
        self.assertFalse(any('Drugi artikal' in name for name in names))
        extra_stock = WarehouseStock.objects.get(product=extra, location=loc)
        self.assertEqual(extra_stock.kolicina, 2)
        self.assertEqual(extra_stock.rezervisano, 0)
        validate_order_stock(order, user=self.user)
        self.assertEqual(stock_totals(extra)['dostupno'], 2)
        self.assertEqual(stock_totals(self.product)['dostupno'], 7)

    def test_finish_pick_with_zero_qty_removes_item(self):
        extra = Product.objects.create(
            naziv='Drugi artikal', sifra='ADD-FIN0', cijena=Decimal('4.00'),
            stanje=4, na_stanju=True, magacin_sync_at=timezone.now(),
        )
        loc = WarehouseLocation.objects.get(sifra='T-1')
        apply_movement(product=extra, location=loc, tip='prijem', kolicina=4)
        self.client.force_login(self.user)
        created = self.client.post(reverse('staff_magacin_narudzba_nova'), {
            'ime_prezime': 'Zavrsi Nula',
            'telefon': '061404042',
            'product_id': [str(self.product.pk), str(extra.pk)],
            'variation_id': ['', ''],
            'kolicina': ['1', '1'],
            'mp_ok': ['0', '0'],
        })
        self.assertEqual(created.status_code, 302)
        order = Order.objects.get(ime_prezime='Zavrsi Nula')
        braid = order.stavke.get(artikal=self.product)
        extra_item = order.stavke.get(artikal=extra)
        finished = self.client.post(
            reverse('staff_magacin_pakuj_detail', args=[order.broj]),
            {
                'action': 'validiraj',
                'pick_json': json.dumps([
                    {
                        'key': f'{braid.pk}:T-1',
                        'item_id': braid.pk,
                        'loc': 'T-1',
                        'got': 1,
                        'need': 1,
                        'done': True,
                    },
                    {
                        'key': f'{extra_item.pk}:T-1',
                        'item_id': extra_item.pk,
                        'loc': 'T-1',
                        'got': 0,
                        'need': 1,
                        'done': False,
                    },
                ]),
            },
        )
        self.assertRedirects(finished, reverse('staff_magacin_pakuj'))
        order.refresh_from_db()
        self.assertEqual(order.lager_status, Order.LagerStatus.VALIDIRANO)
        self.assertFalse(order.stavke.filter(artikal=extra).exists())
        self.assertEqual(order.stavke.get().artikal_id, self.product.pk)
        extra_stock = WarehouseStock.objects.get(product=extra, location=loc)
        self.assertEqual(extra_stock.kolicina, 3)
        self.assertEqual(extra_stock.rezervisano, 0)

    def test_finish_pick_zero_when_shelf_already_empty(self):
        extra = Product.objects.create(
            naziv='Nema na polici', sifra='ADD-EMPTY', cijena=Decimal('4.00'),
            stanje=1, na_stanju=True, magacin_sync_at=timezone.now(),
        )
        loc = WarehouseLocation.objects.get(sifra='T-1')
        apply_movement(product=extra, location=loc, tip='prijem', kolicina=1)
        self.client.force_login(self.user)
        created = self.client.post(reverse('staff_magacin_narudzba_nova'), {
            'ime_prezime': 'Prazna Polica',
            'telefon': '061404044',
            'product_id': [str(self.product.pk), str(extra.pk)],
            'variation_id': ['', ''],
            'kolicina': ['1', '1'],
            'mp_ok': ['0', '0'],
        })
        self.assertEqual(created.status_code, 302)
        order = Order.objects.get(ime_prezime='Prazna Polica')
        braid = order.stavke.get(artikal=self.product)
        extra_item = order.stavke.get(artikal=extra)
        stock = WarehouseStock.objects.get(product=extra, location=loc)
        stock.kolicina = 0
        stock.rezervisano = 2
        stock.save(update_fields=['kolicina', 'rezervisano', 'azurirano'])
        finished = self.client.post(
            reverse('staff_magacin_pakuj_detail', args=[order.broj]),
            {
                'action': 'validiraj',
                'pick_json': json.dumps([
                    {
                        'key': f'{braid.pk}:T-1',
                        'item_id': braid.pk,
                        'loc': 'T-1',
                        'got': 1,
                        'need': 1,
                        'done': True,
                    },
                    {
                        'key': f'{extra_item.pk}:T-1',
                        'item_id': extra_item.pk,
                        'loc': 'T-1',
                        'got': 0,
                        'need': 1,
                        'done': True,
                    },
                ]),
            },
        )
        self.assertRedirects(finished, reverse('staff_magacin_pakuj'))
        order.refresh_from_db()
        self.assertEqual(order.lager_status, Order.LagerStatus.VALIDIRANO)
        self.assertFalse(order.stavke.filter(artikal=extra).exists())
        self.assertEqual(order.stavke.get().artikal_id, self.product.pk)

    def test_finish_pick_only_zero_cancels_order(self):
        self.client.force_login(self.user)
        created = self.client.post(reverse('staff_magacin_narudzba_nova'), {
            'ime_prezime': 'Samo Nula',
            'telefon': '061404043',
            'product_id': [str(self.product.pk)],
            'variation_id': [''],
            'kolicina': ['1'],
            'mp_ok': ['0'],
        })
        self.assertEqual(created.status_code, 302)
        order = Order.objects.get(ime_prezime='Samo Nula')
        item = order.stavke.get()
        loc = WarehouseLocation.objects.get(sifra='T-1')
        before = WarehouseStock.objects.get(product=self.product, location=loc).kolicina
        finished = self.client.post(
            reverse('staff_magacin_pakuj_detail', args=[order.broj]),
            {
                'action': 'validiraj',
                'pick_json': json.dumps([{
                    'key': f'{item.pk}:T-1',
                    'item_id': item.pk,
                    'loc': 'T-1',
                    'got': 0,
                    'need': 1,
                    'done': False,
                }]),
            },
        )
        self.assertRedirects(finished, reverse('staff_magacin_pakuj'))
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.OTKAZANA)
        stock = WarehouseStock.objects.get(product=self.product, location=loc)
        self.assertEqual(stock.kolicina, before - 1)
        self.assertEqual(stock.rezervisano, 0)

    def test_brza_posta_list_copy_fields_and_mark_entered(self):
        self.client.force_login(self.user)
        listed = self.client.get(reverse('staff_magacin_narudzbe'))
        self.assertContains(listed, 'Unesi Brzu poštu')
        created = self.client.post(reverse('staff_magacin_narudzba_nova'), {
            'ime_prezime': 'Brza Kupac',
            'telefon': '065111222',
            'email': 'brza@example.com',
            'adresa': 'Ulica 12',
            'grad': 'Bijeljina',
            'postanski_broj': '76300',
            'product_id': [str(self.product.pk)],
            'variation_id': [''],
            'kolicina': ['1'],
            'mp_ok': ['0'],
        })
        self.assertEqual(created.status_code, 302)
        order = Order.objects.get(ime_prezime='Brza Kupac')
        validate_order_stock(order, user=self.user)
        page = self.client.get(reverse('staff_magacin_brza_posta'))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'Brza Kupac')
        self.assertContains(page, 'Čeka')
        yesterday = timezone.localdate() - timedelta(days=1)
        older = self.client.get(
            reverse('staff_magacin_brza_posta'),
            {'datum': yesterday.isoformat()},
        )
        self.assertNotContains(older, 'Brza Kupac')
        detail = self.client.get(
            reverse('staff_magacin_brza_posta_detail', args=[order.broj]),
        )
        self.assertEqual(detail.status_code, 200)
        self.assertContains(detail, 'Ime')
        self.assertContains(detail, 'Adresa')
        self.assertContains(detail, 'Grad')
        self.assertContains(detail, 'Kontakt')
        self.assertContains(detail, 'Telefon')
        self.assertContains(detail, 'Broj narudžbe')
        self.assertContains(detail, 'Iznos')
        self.assertContains(detail, 'Brza Kupac')
        self.assertContains(detail, 'Ulica 12')
        self.assertContains(detail, '76300 Bijeljina')
        self.assertContains(detail, 'brza@example.com')
        self.assertContains(detail, '065111222')
        self.assertContains(detail, order.broj)
        iznos_copy = f'{order.ukupno:.2f}'.replace('.', ',')
        self.assertIn(',', iznos_copy)
        self.assertContains(detail, f'data-copy="{iznos_copy}"')
        self.assertNotContains(detail, f'data-copy="{order.ukupno:.2f}"')
        self.assertNotContains(detail, f'{iznos_copy} KM')
        self.assertContains(detail, 'Copy')
        self.assertContains(detail, 'Unijeto')
        marked = self.client.post(
            reverse('staff_magacin_brza_posta_detail', args=[order.broj]),
            {'action': 'unesi', 'datum': timezone.localdate().isoformat()},
        )
        self.assertRedirects(
            marked,
            f"{reverse('staff_magacin_brza_posta')}?datum={timezone.localdate().isoformat()}",
        )
        order.refresh_from_db()
        self.assertTrue(order.brza_posta_unijeta)
        self.assertIsNotNone(order.brza_posta_unijeta_at)
        again = self.client.get(reverse('staff_magacin_brza_posta'))
        self.assertContains(again, 'Unijeto')

    def test_pick_zero_removes_item_and_location_qty(self):
        extra = Product.objects.create(
            naziv='Drugi artikal', sifra='ADD-0', cijena=Decimal('4.00'),
            stanje=4, na_stanju=True, magacin_sync_at=timezone.now(),
        )
        loc = WarehouseLocation.objects.get(sifra='T-1')
        apply_movement(product=extra, location=loc, tip='prijem', kolicina=4)
        self.client.force_login(self.user)
        created = self.client.post(reverse('staff_magacin_narudzba_nova'), {
            'ime_prezime': 'Nema Na Polici',
            'telefon': '061404040',
            'product_id': [str(self.product.pk), str(extra.pk)],
            'variation_id': ['', ''],
            'kolicina': ['3', '1'],
            'mp_ok': ['0', '0'],
        })
        self.assertEqual(created.status_code, 302)
        order = Order.objects.get(ime_prezime='Nema Na Polici')
        item = order.stavke.get(artikal=self.product)
        stock = WarehouseStock.objects.get(product=self.product, location=loc)
        self.assertEqual(stock.kolicina, 8)
        self.assertEqual(stock.rezervisano, 3)
        dropped = self.client.post(
            reverse('staff_magacin_pakuj_detail', args=[order.broj]),
            {
                'action': 'pick_nema',
                'item_id': str(item.pk),
                'loc': 'T-1',
                'need': '3',
            },
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )
        self.assertEqual(dropped.status_code, 200)
        self.assertTrue(dropped.json().get('ok'))
        self.assertTrue(dropped.json().get('removed'))
        self.assertFalse(dropped.json().get('cancelled'))
        order.refresh_from_db()
        self.assertFalse(order.stavke.filter(artikal=self.product).exists())
        self.assertEqual(order.stavke.get().artikal_id, extra.pk)
        stock.refresh_from_db()
        self.assertEqual(stock.rezervisano, 0)
        self.assertEqual(stock.kolicina, 5)
        self.assertEqual(stock_totals(self.product)['dostupno'], 5)

        only = self.client.post(reverse('staff_magacin_narudzba_nova'), {
            'ime_prezime': 'Samo Jedan',
            'telefon': '061404041',
            'product_id': [str(extra.pk)],
            'variation_id': [''],
            'kolicina': ['1'],
            'mp_ok': ['0'],
        })
        self.assertEqual(only.status_code, 302)
        last_order = Order.objects.get(ime_prezime='Samo Jedan')
        last_item = last_order.stavke.get()
        extra_stock = WarehouseStock.objects.get(product=extra, location=loc)
        before = extra_stock.kolicina
        gone = self.client.post(
            reverse('staff_magacin_pakuj_detail', args=[last_order.broj]),
            {
                'action': 'pick_nema',
                'item_id': str(last_item.pk),
                'loc': 'T-1',
                'need': '1',
            },
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )
        self.assertEqual(gone.status_code, 200)
        self.assertTrue(gone.json().get('cancelled'))
        last_order.refresh_from_db()
        self.assertEqual(last_order.status, Order.Status.OTKAZANA)
        extra_stock.refresh_from_db()
        self.assertEqual(extra_stock.kolicina, before - 1)

    def test_pick_ocisti_zeros_location_keeps_order_item(self):
        loc = WarehouseLocation.objects.get(sifra='T-1')
        loc2 = WarehouseLocation.objects.create(sifra='T-2', naziv='Druga loc', redoslijed=20)
        apply_movement(product=self.product, location=loc2, tip='prijem', kolicina=5)
        other = Product.objects.create(
            naziv='Ostaje na polici', sifra='STAY-1', cijena=Decimal('3.00'),
            stanje=4, na_stanju=True, magacin_sync_at=timezone.now(),
        )
        apply_movement(product=other, location=loc, tip='prijem', kolicina=4)
        self.client.force_login(self.user)
        created = self.client.post(reverse('staff_magacin_narudzba_nova'), {
            'ime_prezime': 'Ocisti Loc',
            'telefon': '061505050',
            'product_id': [str(self.product.pk)],
            'variation_id': [''],
            'kolicina': ['3'],
            'mp_ok': ['0'],
        })
        self.assertEqual(created.status_code, 302)
        order = Order.objects.get(ime_prezime='Ocisti Loc')
        item = order.stavke.get()
        stock = WarehouseStock.objects.get(product=self.product, location=loc)
        self.assertEqual(stock.kolicina, 8)
        self.assertEqual(stock.rezervisano, 3)
        denied = self.client.post(
            reverse('staff_magacin_pakuj_detail', args=[order.broj]),
            {
                'action': 'pick_ocisti',
                'item_id': str(item.pk),
                'loc': 'T-1',
            },
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )
        self.assertEqual(denied.status_code, 403)
        self.assertFalse(denied.json().get('ok'))
        stock.refresh_from_db()
        self.assertEqual(stock.kolicina, 8)
        wrong = self.client.post(
            reverse('staff_magacin_pakuj_detail', args=[order.broj]),
            {
                'action': 'pick_ocisti',
                'item_id': str(item.pk),
                'loc': 'T-1',
                'lozinka': 'pogresno',
            },
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )
        self.assertEqual(wrong.status_code, 403)
        stock.refresh_from_db()
        self.assertEqual(stock.kolicina, 8)
        cleared = self.client.post(
            reverse('staff_magacin_pakuj_detail', args=[order.broj]),
            {
                'action': 'pick_ocisti',
                'item_id': str(item.pk),
                'loc': 'T-1',
                'lozinka': 'admin',
            },
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )
        self.assertEqual(cleared.status_code, 200)
        payload = cleared.json()
        self.assertTrue(payload.get('ok'))
        self.assertEqual(payload.get('cleared'), 8)
        self.assertEqual(payload.get('relocated'), 3)
        order.refresh_from_db()
        self.assertNotEqual(order.status, Order.Status.OTKAZANA)
        self.assertTrue(order.stavke.filter(pk=item.pk).exists())
        self.assertEqual(order.stavke.get().kolicina, 3)
        stock.refresh_from_db()
        self.assertEqual(stock.kolicina, 0)
        self.assertEqual(stock.rezervisano, 0)
        stock2 = WarehouseStock.objects.get(product=self.product, location=loc2)
        self.assertEqual(stock2.kolicina, 5)
        self.assertEqual(stock2.rezervisano, 3)
        self.product.refresh_from_db()
        self.assertTrue(self.product.na_stanju)
        self.assertEqual(self.product.stanje, 5)
        other_stock = WarehouseStock.objects.get(product=other, location=loc)
        self.assertEqual(other_stock.kolicina, 4)
        move = WarehouseMovement.objects.filter(
            product=self.product, location=loc, tip='korekcija',
        ).latest('pk')
        self.assertIn('Usputni popis', move.napomena)
        self.assertFalse(
            order.magacin_holds.filter(
                location=loc, status='rezervisano',
            ).exists()
        )
        self.assertTrue(
            order.magacin_holds.filter(
                location=loc2, status='rezervisano', kolicina=3,
            ).exists()
        )

        blocked = self.client.post(
            reverse('staff_magacin_pakuj_detail', args=[order.broj]),
            {
                'action': 'pick_ocisti',
                'item_id': str(item.pk),
                'loc': 'MP',
                'lozinka': 'admin',
            },
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )
        self.assertEqual(blocked.status_code, 400)
        self.assertFalse(blocked.json().get('ok'))

        again = self.client.post(
            reverse('staff_magacin_pakuj_detail', args=[order.broj]),
            {
                'action': 'pick_ocisti',
                'item_id': str(item.pk),
                'loc': 'T-1',
                'lozinka': 'admin',
            },
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )
        self.assertEqual(again.status_code, 200)
        self.assertTrue(again.json().get('ok'))
        self.assertEqual(again.json().get('cleared'), 0)

        page = self.client.get(reverse('staff_magacin_pakuj_detail', args=[order.broj]))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'id="pkPickApp"')
        self.assertContains(page, 'T-2')

    def test_vp_add_uses_parent_stock_when_variation_has_none(self):
        self.client.force_login(self.user)
        product = Product.objects.create(
            naziv='BKK Ready Rig Diamond - Sode NI 8# 0.16mm 70cm vezane udice',
            sifra='BKK-SODE-8',
            cijena=Decimal('4.50'),
            stanje=25, na_stanju=True, magacin_sync_at=timezone.now(),
        )
        var_a = ProductVariation.objects.create(
            artikal=product, naziv='8#', sifra='BKK-SODE-8-A', cijena=Decimal('4.50'),
        )
        var_b = ProductVariation.objects.create(
            artikal=product, naziv='10#', sifra='BKK-SODE-8-B', cijena=Decimal('4.50'),
        )
        loc = WarehouseLocation.objects.get(sifra='T-1')
        apply_movement(product=product, location=loc, tip='prijem', kolicina=25)
        self.assertEqual(stock_totals(product)['dostupno'], 25)
        self.assertEqual(stock_totals(product, var_a)['dostupno'], 25)
        self.assertEqual(stock_totals(product, var_b)['dostupno'], 25)
        lookup = self.client.get(reverse('staff_magacin_artikli_lookup'), {
            'q': 'BKK Ready Rig Diamond', 'bez_zalihe': '1',
        })
        payload = lookup.json()['results'][0]
        self.assertEqual(payload['dostupno'], 25)
        self.assertEqual(payload['varijacije'][0]['na_stanju'], 25)
        self.client.post(reverse('staff_magacin_vp_narudzba'), {'action': 'novi'})
        WarehouseCustomer.objects.create(ime_prezime='VP Stock', telefon='061101010')
        customer = WarehouseCustomer.objects.get(ime_prezime='VP Stock')
        self.client.post(reverse('staff_magacin_vp_narudzba'), {
            'action': 'kupac', 'customer_id': customer.pk,
        })
        added = self.client.post(
            reverse('staff_magacin_vp_narudzba'),
            {
                'action': 'dodaj',
                'product_id': str(product.pk),
                'variation_id': str(var_a.pk),
                'kolicina': '2',
                'mp_ok': '0',
            },
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )
        self.assertEqual(added.status_code, 200)
        data = added.json()
        self.assertTrue(data.get('ok'))
        self.assertFalse(data.get('need_mp'))
        draft = MagacinVpNarudzba.objects.get(status=MagacinVpNarudzba.Status.U_TOKU)
        stavka = draft.stavke.get()
        self.assertFalse(stavka.mp_ok)
        self.assertEqual(stavka.kolicina, 2)
        self.assertIsNone(stavka.variation_id)

    def test_vp_without_variations_deducts_entered_qty(self):
        self.client.force_login(self.user)
        product = Product.objects.create(
            naziv='BKK Ready Rig Diamond - Sode NI 8# bez varijacija',
            sifra='BKK-SODE-NV',
            cijena=Decimal('4.50'),
            stanje=25, na_stanju=True, magacin_sync_at=timezone.now(),
        )
        var = ProductVariation.objects.create(
            artikal=product, naziv='8#', sifra='BKK-SODE-NV-A', cijena=Decimal('4.50'),
        )
        loc = WarehouseLocation.objects.get(sifra='T-1')
        apply_movement(product=product, variation=var, location=loc, tip='prijem', kolicina=25)
        var.delete()
        stock = WarehouseStock.objects.get(product=product, location=loc)
        self.assertIsNone(stock.variation_id)
        self.assertEqual(stock.kolicina, 25)
        self.assertEqual(stock_totals(product)['dostupno'], 25)
        lookup = self.client.get(reverse('staff_magacin_artikli_lookup'), {
            'q': 'BKK Ready Rig Diamond - Sode NI', 'bez_zalihe': '1',
        })
        payload = lookup.json()['results'][0]
        self.assertEqual(payload['dostupno'], 25)
        self.assertEqual(payload['varijacije'], [])
        self.client.post(reverse('staff_magacin_vp_narudzba'), {'action': 'novi'})
        customer = WarehouseCustomer.objects.create(
            ime_prezime='VP BezVar', telefon='061303030',
        )
        self.client.post(reverse('staff_magacin_vp_narudzba'), {
            'action': 'kupac', 'customer_id': customer.pk,
        })
        added = self.client.post(
            reverse('staff_magacin_vp_narudzba'),
            {
                'action': 'dodaj',
                'product_id': str(product.pk),
                'kolicina': '3',
                'mp_ok': '0',
            },
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )
        self.assertEqual(added.status_code, 200)
        self.assertTrue(added.json().get('ok'))
        self.assertFalse(added.json().get('need_mp'))
        self.client.post(reverse('staff_magacin_vp_narudzba'), {'action': 'zavrsi'})
        order = Order.objects.get(ime_prezime='VP BezVar')
        item = order.stavke.get()
        finished = self.client.post(
            reverse('staff_magacin_pakuj_detail', args=[order.broj]),
            {
                'action': 'validiraj',
                'pick_json': json.dumps([{
                    'key': f'{item.pk}:T-1',
                    'item_id': item.pk,
                    'loc': 'T-1',
                    'got': 3,
                    'need': 3,
                    'done': True,
                }]),
            },
        )
        self.assertRedirects(finished, reverse('staff_magacin_pakuj'))
        stock.refresh_from_db()
        product.refresh_from_db()
        order.refresh_from_db()
        self.assertEqual(order.lager_status, Order.LagerStatus.VALIDIRANO)
        self.assertEqual(stock.kolicina, 22)
        self.assertEqual(stock.rezervisano, 0)
        self.assertEqual(product.stanje, 22)
        self.assertEqual(stock_totals(product)['dostupno'], 22)

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
        self.assertIn('Nije popisan', order.napomena)
        self.assertEqual(finished['Location'], reverse('staff_magacin_narudzbe'))
        picking = self.client.get(reverse('staff_magacin_pakuj'))
        self.assertEqual(picking.status_code, 200)
        pick = self.client.get(reverse('staff_magacin_pakuj_detail', args=[order.broj]))
        self.assertEqual(pick.status_code, 200)
        queue = json.loads(pick.context['pick_queue_json'])
        self.assertTrue(queue)
        self.assertTrue(all(item.get('nije_popisan') for item in queue))
        self.assertTrue(all(item.get('loc') == 'Nije popisan' for item in queue))
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

    def test_artikli_has_stock_sync_button(self):
        self.client.force_login(self.user)
        page = self.client.get(reverse('staff_magacin_artikli'))
        self.assertContains(page, 'Sync zaliha')
        self.assertContains(page, 'Sync cijena')
        self.assertContains(page, 'Sync šifri')
        self.assertContains(page, 'name="action" value="stock"')
        self.assertContains(page, 'name="action" value="prices"')
        self.assertContains(page, 'name="action" value="sifre"')
        settings_page = self.client.get(reverse('staff_magacin_podesavanja'))
        self.assertContains(settings_page, 'Sync zaliha iz Odoo')
        self.assertContains(settings_page, 'Sync cijena iz Odoo')
        self.assertContains(settings_page, 'Sync šifri po nazivu')
        self.assertContains(settings_page, 'Backup baze')
        self.assertContains(settings_page, 'Preuzmi bazu na disk')
        self.assertContains(settings_page, 'Upload i restore')
        self.assertContains(settings_page, reverse('staff_magacin_backup_download_current'))
        self.assertContains(page, 'Backup baze')
        self.assertContains(page, reverse('staff_magacin_backup'))

    def test_backup_create_list_and_download(self):
        import sqlite3

        self.client.force_login(self.user)
        with TemporaryDirectory() as tmp:
            with override_settings(MAGACIN_BACKUP_DIR=tmp):
                created = self.client.post(
                    reverse('staff_magacin_backup'),
                    {'action': 'create'},
                )
                self.assertEqual(created.status_code, 302)
                files = sorted(Path(tmp).glob('db-*.sqlite3'))
                self.assertEqual(len(files), 1)
                name = files[0].name
                self.assertGreater(files[0].stat().st_size, 0)
                conn = sqlite3.connect(str(files[0]))
                try:
                    names = [
                        row[0]
                        for row in conn.execute(
                            'SELECT naziv FROM "EcommerceApp_product" WHERE sifra = ?',
                            ['TST-1'],
                        )
                    ]
                finally:
                    conn.close()
                self.assertEqual(names, ['Test braid'])
                listing = self.client.get(reverse('staff_magacin_backup'))
                self.assertContains(listing, name)
                self.assertContains(listing, 'Restore')
                download = self.client.get(
                    reverse('staff_magacin_backup_download', args=[name]),
                )
                self.assertEqual(download.status_code, 200)
                self.assertIn('attachment', download['Content-Disposition'])
                missing = self.client.get(
                    reverse('staff_magacin_backup_download', args=['db-19990101-000000.sqlite3']),
                )
                self.assertEqual(missing.status_code, 404)
                self.product.naziv = 'Promijenjeno ime'
                self.product.save(update_fields=['naziv'])
                denied = self.client.post(reverse('staff_magacin_backup'), {
                    'action': 'restore',
                    'name': name,
                    'lozinka': 'pogresno',
                })
                self.assertEqual(denied.status_code, 302)
                self.product.refresh_from_db()
                self.assertEqual(self.product.naziv, 'Promijenjeno ime')
                with patch('EcommerceApp.views_magacin.restore_backup') as mocked:
                    mocked.return_value = {'restored': name, 'safety': 'db-now.sqlite3'}
                    restored = self.client.post(reverse('staff_magacin_backup'), {
                        'action': 'restore',
                        'name': name,
                        'lozinka': 'admin',
                    })
                self.assertEqual(restored.status_code, 302)
                mocked.assert_called_once_with(name)

                settings_page = self.client.get(reverse('staff_magacin_podesavanja'))
                self.assertContains(settings_page, name)
                self.assertContains(settings_page, 'Preuzmi bazu na disk')
                self.assertContains(settings_page, 'name="fajl"')

                current = self.client.get(reverse('staff_magacin_backup_download_current'))
                self.assertEqual(current.status_code, 200)
                self.assertIn('attachment', current['Content-Disposition'])
                self.assertGreaterEqual(len(list(Path(tmp).glob('db-*.sqlite3'))), 2)

                from django.core.files.uploadedfile import SimpleUploadedFile
                payload = SimpleUploadedFile(
                    'db-upload-test.sqlite3',
                    files[0].read_bytes(),
                    content_type='application/octet-stream',
                )
                with patch('EcommerceApp.views_magacin.restore_backup') as uploaded_restore:
                    uploaded_restore.return_value = {
                        'restored': 'db-upload-test.sqlite3',
                        'safety': 'db-now.sqlite3',
                    }
                    uploaded = self.client.post(
                        reverse('staff_magacin_backup'),
                        {
                            'action': 'upload_restore',
                            'lozinka': 'admin',
                            'next': reverse('staff_magacin_podesavanja'),
                            'fajl': payload,
                        },
                    )
                self.assertEqual(uploaded.status_code, 302)
                self.assertEqual(uploaded['Location'], reverse('staff_magacin_podesavanja'))
                uploaded_restore.assert_called_once()
                self.assertTrue((Path(tmp) / 'db-upload-test.sqlite3').is_file())

    def test_backup_lists_all_and_never_deletes(self):
        from .db_backup import create_backup, list_backups

        self.client.force_login(self.user)
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            odd = root / 'db-prije-restore-20260723-1410.sqlite3'
            odd.write_bytes(b'sqlite')
            sidecar = root / 'db-20260807-113727.sqlite3-wal'
            sidecar.write_bytes(b'')
            with override_settings(MAGACIN_BACKUP_DIR=tmp):
                first = create_backup()
                second = create_backup()
                names = {row['name'] for row in list_backups()}
                self.assertIn(odd.name, names)
                self.assertIn(first['name'], names)
                self.assertIn(second['name'], names)
                self.assertNotIn(sidecar.name, names)
                self.assertTrue(odd.is_file())
                self.assertTrue(Path(first['path']).is_file())
                self.assertTrue(Path(second['path']).is_file())
                listing = self.client.get(reverse('staff_magacin_backup'))
                self.assertContains(listing, odd.name)
                self.assertContains(listing, first['name'])
                self.assertContains(listing, second['name'])
                self.assertContains(listing, 'backup-a')

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
        self.assertContains(page, 'data-mg-scan-target="mgInsertLocSearch"')
        self.assertContains(page, 'data-mg-scan-target="mgInsertSearch"')
        self.assertContains(page, 'Skeniraj')
        self.assertContains(page, 'Naziv, šifra ili barkod')
        self.assertContains(page, 'mg-transfer-page')
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
        self.assertContains(prenos, 'Šifra, naziv ili barkod lokacije')
        self.assertContains(prenos, 'data-mg-scan-target="mgMoveSearch"')
        self.assertContains(prenos, 'data-mg-scan-target="mgMoveToSearch"')
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

    def test_click_location_deducts_entered_qty(self):
        self.client.force_login(self.user)
        loc = WarehouseLocation.objects.get(sifra='T-1')
        page = self.client.get(reverse('staff_magacin_artikal', args=[self.product.pk]))
        self.assertContains(page, 'data-mode="remove"')
        self.assertContains(page, f'data-location-id="{loc.pk}"')
        self.assertContains(page, 'data-available="8"')
        response = self.client.post(
            reverse('staff_magacin_artikal', args=[self.product.pk]),
            {
                'action': 'kretanje',
                'mode': 'remove',
                'location_id': loc.pk,
                'kolicina': '3',
            },
        )
        self.assertEqual(response.status_code, 302)
        stock = WarehouseStock.objects.get(product=self.product, location=loc)
        self.product.refresh_from_db()
        self.assertEqual(stock.kolicina, 5)
        self.assertEqual(self.product.stanje, 5)
        self.assertEqual(stock_totals(self.product)['dostupno'], 5)
        history = self.client.get(reverse('staff_magacin_artikal', args=[self.product.pk]))
        self.assertContains(history, 'Skini sa T-1')
        blocked = self.client.post(
            reverse('staff_magacin_artikal', args=[self.product.pk]),
            {
                'action': 'kretanje',
                'mode': 'remove',
                'location_id': loc.pk,
                'kolicina': '9',
            },
        )
        self.assertEqual(blocked.status_code, 302)
        stock.refresh_from_db()
        self.assertEqual(stock.kolicina, 5)

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
        self.assertFalse(self.product.na_stanju)

    def test_skini_sa_stanja_zeros_maloprodaja_too(self):
        self.client.force_login(self.user)
        mp = WarehouseLocation.objects.create(sifra='B-03', naziv='Maloprodaja Sarajevo')
        apply_movement(product=self.product, location=mp, tip='prijem', kolicina=3)
        self.product.refresh_from_db()
        self.assertTrue(self.product.na_stanju)
        self.assertGreater(self.product.stanje, 0)
        response = self.client.post(
            reverse('staff_magacin_artikal', args=[self.product.pk]),
            {'action': 'skini'},
        )
        self.assertEqual(response.status_code, 302)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stanje, 0)
        self.assertFalse(self.product.na_stanju)
        self.assertEqual(
            WarehouseStock.objects.get(product=self.product, location=mp).kolicina,
            0,
        )

    def test_ubaci_na_sajt_requires_location_qty(self):
        self.client.force_login(self.user)
        self.zero.aktivan = False
        self.zero.save(update_fields=['aktivan'])
        page = self.client.get(reverse('staff_magacin_artikal', args=[self.zero.pk]))
        self.assertContains(page, 'Dodaj na stanje')
        self.assertContains(page, 'name="action" value="ubaci"')
        response = self.client.post(
            reverse('staff_magacin_artikal', args=[self.zero.pk]),
            {'action': 'ubaci'},
        )
        self.assertEqual(response.status_code, 302)
        self.zero.refresh_from_db()
        self.assertTrue(self.zero.aktivan)
        self.assertFalse(self.zero.na_stanju)
        self.assertEqual(self.zero.stanje, 0)

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
        if not WarehouseLocation.objects.filter(naziv__icontains='maloprodaja').exists():
            WarehouseLocation.objects.create(sifra='B-03', naziv='Maloprodaja Sarajevo')
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
        self.assertContains(listing, reverse('staff_magacin_pakuj_detail', args=[order.broj]))
        narudzbe = self.client.get(reverse('staff_magacin_narudzbe'))
        self.assertNotContains(narudzbe, order.broj)
        self.assertNotContains(narudzbe, self.product.naziv)
        pick = self.client.get(reverse('staff_magacin_pakuj_detail', args=[order.broj]))
        self.assertFalse(pick.context['can_edit_order'])
        self.assertNotContains(pick, 'Izmijeni narudžbu')
        self.assertContains(pick, 'Validatuj')
        self.assertContains(pick, 'Prenos u MP')
        self.assertContains(pick, 'T-1')
        self.assertContains(pick, 'Lokacija')
        self.assertContains(pick, 'Artikal')
        self.assertContains(pick, 'Količina')
        self.assertContains(pick, 'id="pkPrenosScanCam"')
        self.assertContains(pick, 'aria-label="Skener"')
        self.assertContains(pick, 'Ukloni iz lokacije')
        self.assertContains(pick, 'Otkaži prenos')
        self.assertContains(pick, 'id="pkPrenosGot"')
        self.assertContains(pick, 'Test braid')
        self.assertContains(pick, 'pk-prenos-sifra')
        self.assertContains(pick, 'TST-1')
        self.assertNotContains(pick, 'Odnio kod Slobe')
        other = Product.objects.create(
            naziv='Drugi prenos', sifra='DRG-MP', cijena=Decimal('4.00'),
            stanje=0, na_stanju=False, magacin_sync_at=timezone.now(),
        )
        apply_movement(product=other, location=src, tip='prijem', kolicina=5)
        second = self.client.post(
            reverse('staff_magacin_artikal', args=[other.pk]),
            {'action': 'kretanje', 'mode': 'mp', 'kolicina': '2'},
        )
        self.assertEqual(second.status_code, 302)
        self.assertEqual(Order.objects.filter(ime_prezime='Prenos u MP').count(), 1)
        order.refresh_from_db()
        self.assertEqual(order.stavke.count(), 2)
        grouped = self.client.get(reverse('staff_magacin_pakuj_detail', args=[order.broj]))
        self.assertContains(grouped, 'id="pkPickApp"')
        self.assertContains(grouped, 'Drugi prenos')
        self.assertContains(grouped, 'Test braid')
        self.assertContains(grouped, '2 stavki')
        listing2 = self.client.get(reverse('staff_magacin_pakuj'), {'status': 'sve'})
        self.assertContains(listing2, reverse('staff_magacin_pakuj_detail', args=[order.broj]))
        self.assertContains(listing2, '2 stavki')
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
        self.assertEqual(self.product.stanje, 8)
        self.assertEqual(stock_totals(self.product)['dostupno'], 5)
        self.assertEqual(display_stock_totals(self.product)['dostupno'], 8)
        self.assertEqual(stock_totals(self.product)['rezervisano'], 0)
        src_stock = WarehouseStock.objects.get(product=self.product, location=src)
        self.assertEqual(src_stock.kolicina, 5)
        mp = WarehouseLocation.objects.filter(naziv__icontains='maloprodaja').first()
        self.assertIsNotNone(mp)
        self.assertEqual(
            WarehouseStock.objects.get(product=self.product, location=mp).kolicina,
            3,
        )
        detail = self.client.get(reverse('staff_magacin_artikal', args=[self.product.pk]))
        self.assertContains(detail, 'is-mp')
        self.assertContains(detail, 'Maloprodaja')
        html = detail.content.decode()
        table_pos = html.find('Zalihe po lokacijama')
        mp_pos = html.find('mg-stock-row is-mp', table_pos)
        ukupno_pos = html.find('>UKUPNO<', table_pos)
        self.assertGreater(mp_pos, table_pos)
        self.assertGreater(ukupno_pos, mp_pos)
        self.assertContains(detail, '>8<')

    def test_prenos_mp_can_validate_less_qty(self):
        self.client.force_login(self.user)
        if not WarehouseLocation.objects.filter(naziv__icontains='maloprodaja').exists():
            WarehouseLocation.objects.create(sifra='B-03', naziv='Maloprodaja Sarajevo')
        response = self.client.post(
            reverse('staff_magacin_artikal', args=[self.product.pk]),
            {'action': 'kretanje', 'mode': 'mp', 'kolicina': '3'},
        )
        self.assertEqual(response.status_code, 302)
        order = Order.objects.get(ime_prezime='Prenos u MP')
        item = order.stavke.get()
        loc = WarehouseLocation.objects.get(sifra='T-1')
        pick_json = json.dumps([{
            'key': f'{item.pk}:{loc.sifra}',
            'item_id': item.pk,
            'loc': loc.sifra,
            'got': 1,
            'need': 3,
            'done': True,
        }])
        validated = self.client.post(
            reverse('staff_magacin_pakuj_detail', args=[order.broj]),
            {'action': 'validiraj', 'pick_json': pick_json},
        )
        self.assertEqual(validated.status_code, 302)
        order.refresh_from_db()
        item.refresh_from_db()
        self.product.refresh_from_db()
        self.assertEqual(order.lager_status, Order.LagerStatus.VALIDIRANO)
        self.assertEqual(item.kolicina_pokupljeno, 1)
        self.assertEqual(self.product.stanje, 8)
        self.assertEqual(stock_totals(self.product)['dostupno'], 7)
        self.assertEqual(display_stock_totals(self.product)['dostupno'], 8)
        self.assertEqual(stock_totals(self.product)['rezervisano'], 0)
        stock = WarehouseStock.objects.get(product=self.product, location=loc)
        self.assertEqual(stock.kolicina, 7)
        mp = WarehouseLocation.objects.filter(naziv__icontains='maloprodaja').first()
        self.assertEqual(
            WarehouseStock.objects.get(product=self.product, location=mp).kolicina,
            1,
        )

    def test_prenos_mp_grouped_validate_drops_unpicked_item(self):
        self.client.force_login(self.user)
        src = WarehouseLocation.objects.get(sifra='T-1')
        if not WarehouseLocation.objects.filter(naziv__icontains='maloprodaja').exists():
            WarehouseLocation.objects.create(sifra='B-03', naziv='Maloprodaja Sarajevo')
        self.client.post(
            reverse('staff_magacin_artikal', args=[self.product.pk]),
            {'action': 'kretanje', 'mode': 'mp', 'kolicina': '3'},
        )
        other = Product.objects.create(
            naziv='Preskoci prenos', sifra='SKIP-MP', cijena=Decimal('4.00'),
            stanje=0, na_stanju=False, magacin_sync_at=timezone.now(),
        )
        apply_movement(product=other, location=src, tip='prijem', kolicina=4)
        self.client.post(
            reverse('staff_magacin_artikal', args=[other.pk]),
            {'action': 'kretanje', 'mode': 'mp', 'kolicina': '2'},
        )
        order = Order.objects.get(ime_prezime='Prenos u MP')
        keep = order.stavke.get(artikal=self.product)
        skip = order.stavke.get(artikal=other)
        pick_json = json.dumps([
            {
                'key': f'{keep.pk}:{src.sifra}',
                'item_id': keep.pk,
                'loc': src.sifra,
                'got': 3,
                'need': 3,
                'done': True,
            },
            {
                'key': f'{skip.pk}:{src.sifra}',
                'item_id': skip.pk,
                'loc': src.sifra,
                'got': 0,
                'need': 2,
                'done': True,
            },
        ])
        validated = self.client.post(
            reverse('staff_magacin_pakuj_detail', args=[order.broj]),
            {'action': 'validiraj', 'pick_json': pick_json},
        )
        self.assertEqual(validated.status_code, 302)
        order.refresh_from_db()
        self.assertEqual(order.lager_status, Order.LagerStatus.VALIDIRANO)
        self.assertFalse(order.stavke.filter(pk=skip.pk).exists())
        self.assertEqual(order.stavke.get(pk=keep.pk).kolicina_pokupljeno, 3)
        other_stock = WarehouseStock.objects.get(product=other, location=src)
        self.assertEqual(other_stock.kolicina, 4)
        self.assertEqual(other_stock.rezervisano, 0)

    def test_prenos_mp_cancel_returns_stock_without_transfer(self):
        self.client.force_login(self.user)
        created = self.client.post(
            reverse('staff_magacin_artikal', args=[self.product.pk]),
            {'action': 'kretanje', 'mode': 'mp', 'kolicina': '3'},
        )
        self.assertEqual(created.status_code, 302)
        order = Order.objects.get(ime_prezime='Prenos u MP')
        loc = WarehouseLocation.objects.get(sifra='T-1')
        stock = WarehouseStock.objects.get(product=self.product, location=loc)
        self.assertEqual(stock.kolicina, 8)
        self.assertEqual(stock.rezervisano, 3)
        self.assertEqual(stock_totals(self.product)['dostupno'], 5)
        cancelled = self.client.post(
            reverse('staff_magacin_pakuj_detail', args=[order.broj]),
            {'action': 'otkazi'},
        )
        self.assertRedirects(cancelled, reverse('staff_magacin_pakuj'))
        order.refresh_from_db()
        stock.refresh_from_db()
        self.product.refresh_from_db()
        self.assertEqual(order.status, Order.Status.OTKAZANA)
        self.assertEqual(order.lager_status, Order.LagerStatus.OTKAZANO)
        self.assertEqual(stock.kolicina, 8)
        self.assertEqual(stock.rezervisano, 0)
        self.assertEqual(self.product.stanje, 8)
        self.assertEqual(stock_totals(self.product)['dostupno'], 8)
        listing = self.client.get(reverse('staff_magacin_pakuj'))
        self.assertNotContains(listing, reverse('staff_magacin_pakuj_detail', args=[order.broj]))

    def test_prenos_mp_clear_location_requires_admin_password(self):
        loc = WarehouseLocation.objects.get(sifra='T-1')
        other = Product.objects.create(
            naziv='Ostaje na polici', sifra='STAY-MP', cijena=Decimal('3.00'),
            stanje=4, na_stanju=True, magacin_sync_at=timezone.now(),
        )
        apply_movement(product=other, location=loc, tip='prijem', kolicina=4)
        self.client.force_login(self.user)
        created = self.client.post(
            reverse('staff_magacin_artikal', args=[self.product.pk]),
            {'action': 'kretanje', 'mode': 'mp', 'kolicina': '3'},
        )
        self.assertEqual(created.status_code, 302)
        order = Order.objects.get(ime_prezime='Prenos u MP')
        item = order.stavke.get()
        stock = WarehouseStock.objects.get(product=self.product, location=loc)
        self.assertEqual(stock.kolicina, 8)
        self.assertEqual(stock.rezervisano, 3)
        denied = self.client.post(
            reverse('staff_magacin_pakuj_detail', args=[order.broj]),
            {'action': 'pick_ocisti', 'item_id': str(item.pk), 'loc': 'T-1'},
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )
        self.assertEqual(denied.status_code, 403)
        stock.refresh_from_db()
        self.assertEqual(stock.kolicina, 8)
        wrong = self.client.post(
            reverse('staff_magacin_pakuj_detail', args=[order.broj]),
            {
                'action': 'pick_ocisti',
                'item_id': str(item.pk),
                'loc': 'T-1',
                'lozinka': 'pogresno',
            },
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )
        self.assertEqual(wrong.status_code, 403)
        stock.refresh_from_db()
        self.assertEqual(stock.kolicina, 8)
        cleared = self.client.post(
            reverse('staff_magacin_pakuj_detail', args=[order.broj]),
            {
                'action': 'pick_ocisti',
                'item_id': str(item.pk),
                'loc': 'T-1',
                'lozinka': 'admin',
            },
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )
        self.assertEqual(cleared.status_code, 200)
        payload = cleared.json()
        self.assertTrue(payload.get('ok'))
        self.assertTrue(payload.get('cancelled'))
        self.assertEqual(payload.get('cleared'), 8)
        self.assertIn(reverse('staff_magacin_pakuj'), payload.get('redirect') or '')
        order.refresh_from_db()
        self.product.refresh_from_db()
        stock.refresh_from_db()
        self.assertEqual(order.status, Order.Status.OTKAZANA)
        self.assertEqual(stock.kolicina, 0)
        self.assertEqual(stock.rezervisano, 0)
        self.assertEqual(self.product.stanje, 0)
        self.assertFalse(self.product.na_stanju)
        other_stock = WarehouseStock.objects.get(product=other, location=loc)
        self.assertEqual(other_stock.kolicina, 4)

    def test_pick_ocisti_keeps_on_site_when_other_location_has_qty(self):
        loc = WarehouseLocation.objects.get(sifra='T-1')
        mp = WarehouseLocation.objects.filter(naziv__icontains='maloprodaja').first()
        if mp is None:
            mp = WarehouseLocation.objects.create(sifra='B-03', naziv='Maloprodaja Sarajevo')
        apply_movement(product=self.product, location=mp, tip='prijem', kolicina=2)
        self.client.force_login(self.user)
        created = self.client.post(reverse('staff_magacin_narudzba_nova'), {
            'ime_prezime': 'Ocisti Ostaje',
            'telefon': '061505051',
            'product_id': [str(self.product.pk)],
            'variation_id': [''],
            'kolicina': ['1'],
            'mp_ok': ['0'],
        })
        self.assertEqual(created.status_code, 302)
        order = Order.objects.get(ime_prezime='Ocisti Ostaje')
        item = order.stavke.get()
        cleared = self.client.post(
            reverse('staff_magacin_pakuj_detail', args=[order.broj]),
            {
                'action': 'pick_ocisti',
                'item_id': str(item.pk),
                'loc': 'T-1',
                'lozinka': 'admin',
            },
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )
        self.assertEqual(cleared.status_code, 200)
        self.assertTrue(cleared.json().get('ok'))
        self.assertIn('ostaje na sajtu', (cleared.json().get('message') or '').casefold())
        stock = WarehouseStock.objects.get(product=self.product, location=loc)
        self.assertEqual(stock.kolicina, 0)
        self.assertEqual(
            WarehouseStock.objects.get(product=self.product, location=mp).kolicina,
            2,
        )
        self.product.refresh_from_db()
        self.assertTrue(self.product.na_stanju)
        self.assertEqual(self.product.stanje, 2)

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
        self.assertContains(page, 'data-loc-skini')
        self.assertContains(page, 'Štampaj popis')
        stampa = self.client.get(reverse('staff_magacin_lokacija_stampa', args=[loc.pk]))
        self.assertEqual(stampa.status_code, 200)
        self.assertContains(stampa, 'Popis lokacije T-1')
        self.assertContains(stampa, 'Test braid')
        self.assertContains(stampa, 'TST-1')
        self.assertContains(stampa, '8')
        self.assertContains(stampa, 'Naziv artikla')
        self.assertContains(stampa, 'Šifra')
        self.assertContains(stampa, 'Količina')
        zalihe = self.client.get(reverse('staff_magacin_zalihe'), {'lokacija': loc.pk})
        self.assertContains(zalihe, 'Test braid')
        self.assertContains(zalihe, 'TST-1')
        self.assertContains(zalihe, 'Štampaj popis')
        uvoz = WarehouseLocation.objects.create(sifra='UVOZ', naziv='Novi uvoz')
        for i in range(8):
            ghost = Product.objects.create(
                naziv=f'Uvoz ghost {i}', sifra=f'UVOZ-G-{i}', cijena=Decimal('1.00'),
            )
            WarehouseStock.objects.create(product=ghost, location=uvoz, kolicina=0)
        listed_uvoz = self.client.get(reverse('staff_magacin_lokacije'), {'pretraga': 'UVOZ'})
        self.assertContains(listed_uvoz, 'Novi uvoz')
        uvoz_row = next(
            row for row in listed_uvoz.context['locations']
            if row['location'].pk == uvoz.pk
        )
        self.assertEqual(uvoz_row['artikala'], 0)
        self.assertEqual(uvoz_row['na_stanju'], 0)
        uvoz_page = self.client.get(reverse('staff_magacin_lokacije'), {'lokacija': uvoz.pk})
        self.assertContains(uvoz_page, 'Nema artikala na ovoj lokaciji')
        self.assertEqual(other.sifra, 'Z-9')
        taken = self.client.post(reverse('staff_magacin_lokacije'), {
            'action': 'skini',
            'location_id': loc.pk,
            'product_id': self.product.pk,
            'kolicina': '3',
        })
        self.assertEqual(taken.status_code, 302)
        self.assertIn(f'lokacija={loc.pk}', taken['Location'])
        stock = WarehouseStock.objects.get(product=self.product, location=loc)
        self.product.refresh_from_db()
        self.assertEqual(stock.kolicina, 5)
        self.assertEqual(self.product.stanje, 5)
        blocked = self.client.post(reverse('staff_magacin_lokacije'), {
            'action': 'skini',
            'location_id': loc.pk,
            'product_id': self.product.pk,
            'kolicina': '9',
        })
        self.assertEqual(blocked.status_code, 302)
        stock.refresh_from_db()
        self.assertEqual(stock.kolicina, 5)

    def test_narudzbe_has_manual_entry(self):
        self.client.force_login(self.user)
        listed = self.client.get(reverse('staff_magacin_narudzbe'))
        self.assertEqual(listed.status_code, 200)
        self.assertContains(listed, 'Nova ručna narudžba')
        self.assertContains(listed, 'Pokaži validatovane')
        self.assertContains(listed, 'validirane=1&sve=1')
        self.assertContains(listed, 'Pretraži po imenu ili broju narudžbe')
        self.assertContains(listed, 'Ime ili broj narudžbe')
        self.assertNotContains(listed, 'Broj narudžbe, ime ili telefon')
        self.assertContains(listed, reverse('staff_magacin_narudzba_nova'))
        self.assertContains(listed, reverse('staff_magacin_narudzbe_stampa'))
        self.assertContains(listed, 'Validiraj odabrane')
        self.assertContains(listed, 'Štampaj packing')
        self.assertContains(listed, 'id="mgPackingSelected"')
        self.assertContains(listed, 'data-packing-count=')
        self.assertContains(listed, reverse('staff_magacin_narudzbe_validiraj'))
        self.assertContains(listed, reverse('staff_magacin_narudzbe_packing'))
        self.assertNotContains(listed, 'mg-nav-count')
        form = self.client.get(reverse('staff_magacin_narudzba_nova'))
        self.assertEqual(form.status_code, 200)
        self.assertContains(form, 'Nova ručna narudžba')
        self.assertContains(form, 'id="mgOrderSearch"')
        self.assertContains(form, 'id="mgSpareBtn"')
        self.assertContains(form, 'Slanje rezervnog dijela')
        self.assertContains(form, 'id="mgSpareModal"')
        self.assertContains(form, 'id="mgOrderSuggest"')
        self.assertContains(form, 'id="mgOrderQtyModal"')
        self.assertContains(form, 'Stavke narudžbe')
        self.assertContains(form, 'Sačuvaj narudžbu')
        self.assertContains(form, 'Rezervacija')
        self.assertContains(form, 'Očisti narudžbu')
        self.assertContains(form, 'id="mgCustomerSearch"')
        self.assertContains(form, 'Dodaj kupca')
        self.assertContains(form, 'id="mgCustomerModal"')
        self.assertContains(form, reverse('staff_magacin_kupci_lookup'))
        self.assertContains(form, reverse('staff_magacin_kupci_save'))
        self.assertContains(form, 'id="mgOrderCatalog"')
        self.assertContains(form, 'id="mgOrderCatalogBtn"')
        self.assertContains(form, 'Katalog')
        self.assertContains(form, reverse('staff_magacin_artikli_lookup'))
        self.assertContains(form, 'data-mg-scan-target="mgOrderSearch"')
        self.assertContains(form, 'id="mgMpModal"')
        self.assertContains(form, 'id="mgMpAdd"')
        self.assertContains(form, 'Izbaci')
        self.assertContains(form, 'Nema dostupnog artikla')
        self.assertContains(form, 'Nije popisan — dodaj')
        self.assertContains(form, 'name="popust_pct"')
        self.assertContains(form, 'Popust %')
        self.assertContains(form, 'id="mgOrderDisc"')
        self.assertContains(form, 'Skini dostavu')
        self.assertContains(form, 'name="bez_dostave"')
        self.assertContains(form, 'name="placanje"')
        self.assertContains(form, 'Gotovina')
        self.assertContains(form, 'Kartica')
        self.assertContains(form, 'value="gotovina"')
        self.assertContains(form, 'checked')

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
        self.assertContains(listing, order.ime_prezime)
        self.assertContains(listing, reverse('staff_magacin_pakuj_detail', args=[order.broj]))
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
        self.assertEqual(pick_after['Location'], reverse('staff_magacin_pakuj'))
        opened_num = self.client.get(reverse('staff_magacin_pakuj_sken'), {'q': order.broj})
        self.assertEqual(opened_num['Location'], reverse('staff_magacin_pakuj_detail', args=[order.broj]))
        opened_hash = self.client.get(reverse('staff_magacin_pakuj_sken'), {'q': f'#{order.broj}'})
        self.assertEqual(opened_hash['Location'], reverse('staff_magacin_pakuj_detail', args=[order.broj]))
        missing = self.client.get(reverse('staff_magacin_pakuj_sken'), {'q': 'OZB9999'})
        self.assertEqual(missing.status_code, 302)
        self.assertEqual(missing['Location'], reverse('staff_magacin_pakuj'))

    def test_picking_home_defaults_to_waiting(self):
        self.client.force_login(self.user)
        self.client.post(reverse('staff_magacin_narudzba_nova'), {
            'ime_prezime': 'Ceka Kupac',
            'telefon': '061111111',
            'product_id': [str(self.product.pk)],
            'variation_id': [''],
            'kolicina': ['1'],
            'mp_ok': ['0'],
        })
        self.client.post(reverse('staff_magacin_narudzba_nova'), {
            'ime_prezime': 'Toku Kupac',
            'telefon': '061222222',
            'product_id': [str(self.product.pk)],
            'variation_id': [''],
            'kolicina': ['1'],
            'mp_ok': ['0'],
        })
        waiting = Order.objects.get(ime_prezime='Ceka Kupac')
        active = Order.objects.get(ime_prezime='Toku Kupac')
        home = self.client.get(reverse('staff_magacin_pakuj'))
        self.assertEqual(home.context['pick_status_filter'], 'ceka')
        self.assertContains(home, 'pk-refresh')
        self.assertContains(home, 'Ceka Kupac')
        self.assertContains(home, 'Toku Kupac')
        self.assertContains(home, 'is-wait-pick')
        self.assertContains(home, 'mg-table-stack-pick')
        self.assertContains(home, 'status=u_toku')
        self.assertContains(home, 'status=zavrseno')
        self.assertNotContains(home, 'Ukupno pick lista')
        self.assertNotContains(home, 'pk-home-stats')
        self.assertNotContains(home, 'pk-tab-n')
        empty_toku = self.client.get(reverse('staff_magacin_pakuj'), {'status': 'u_toku'})
        self.assertContains(empty_toku, 'Nema pickinga u toku')
        self.assertNotContains(empty_toku, 'Ceka Kupac')
        self.client.get(reverse('staff_magacin_pakuj_sken'), {'q': active.broj})
        waiting_only = self.client.get(reverse('staff_magacin_pakuj'))
        self.assertEqual(waiting_only.context['pick_status_filter'], 'ceka')
        self.assertContains(waiting_only, 'Ceka Kupac')
        self.assertNotContains(waiting_only, 'Toku Kupac')
        self.assertContains(waiting_only, 'class="pk-tab-n">1</i>')
        in_progress = self.client.get(reverse('staff_magacin_pakuj'), {'status': 'u_toku'})
        self.assertContains(in_progress, 'Toku Kupac')
        self.assertNotContains(in_progress, 'Ceka Kupac')
        all_jobs = self.client.get(reverse('staff_magacin_pakuj'), {'status': 'sve'})
        self.assertContains(all_jobs, 'Ceka Kupac')
        self.assertContains(all_jobs, 'Toku Kupac')
        self.assertContains(all_jobs, reverse('staff_magacin_pakuj_detail', args=[waiting.broj]))

    def test_picking_completed_shows_older_jobs(self):
        self.client.force_login(self.user)
        old = Order.objects.create(
            ime_prezime='Stari Picking', telefon='061000000', email='s@example.com',
            adresa='A 1', grad='Sarajevo', ukupno=Decimal('10.00'),
            izvor=Order.Izvor.MAGACIN,
            status=Order.Status.ZAVRSENA,
            lager_status=Order.LagerStatus.VALIDIRANO,
            zapakovana=True,
            zapakovana_at=timezone.now() - timedelta(days=2),
        )
        Order.objects.filter(pk=old.pk).update(kreirana=timezone.now() - timedelta(days=2))
        home = self.client.get(reverse('staff_magacin_pakuj'))
        self.assertEqual(home.context['pick_counts']['zavrseno'], 1)
        self.assertNotContains(home, 'Stari Picking')
        done = self.client.get(reverse('staff_magacin_pakuj'), {'status': 'zavrseno'})
        listed = [row.broj for row in done.context['pick_jobs']]
        self.assertIn(old.broj, listed)
        self.assertContains(done, 'Stari Picking')
        self.assertContains(done, 'class="pk-tab-n">1</i>')

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
        listed = self.client.get(reverse('staff_magacin_kupci_lookup'))
        names = [row['ime_prezime'] for row in listed.json()['results']]
        self.assertIn('Marko Savić', names)
        by_phone = self.client.get(reverse('staff_magacin_kupci_lookup'), {'q': '065 555'})
        self.assertEqual(by_phone.json()['results'][0]['ime_prezime'], 'Marko Savić')
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

        listed = self.client.get(reverse('staff_magacin_kupci'))
        self.assertEqual(listed.status_code, 200)
        self.assertContains(listed, 'Marko Savić')
        self.assertContains(listed, 'Izmijeni')
        edited = self.client.post(reverse('staff_magacin_kupci_save'), {
            'customer_id': str(customer.pk),
            'ime_prezime': 'Marko Savić',
            'telefon': '065555555',
            'adresa': 'Nova 8',
            'grad': 'Sarajevo',
            'email': 'marko@example.com',
        })
        self.assertEqual(edited.status_code, 200)
        customer.refresh_from_db()
        self.assertEqual(customer.adresa, 'Nova 8')
        self.assertEqual(customer.grad, 'Sarajevo')
        self.assertEqual(customer.email, 'marko@example.com')
        page_edit = self.client.post(reverse('staff_magacin_kupci'), {
            'action': 'save',
            'customer_id': str(customer.pk),
            'ime_prezime': 'Marko Ivić',
            'telefon': '065555555',
            'adresa': 'Nova 8',
            'grad': 'Sarajevo',
        })
        self.assertEqual(page_edit.status_code, 302)
        customer.refresh_from_db()
        self.assertEqual(customer.ime_prezime, 'Marko Ivić')
        from django.contrib import admin as django_admin
        self.assertIn(WarehouseCustomer, django_admin.site._registry)

        form = self.client.get(reverse('staff_magacin_narudzba_nova'))
        self.assertContains(form, 'id="mgCustomerEdit"')
        self.assertContains(form, 'Izmijeni')

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

    def test_order_from_maloprodaja_stock_without_nije_popisan(self):
        self.client.force_login(self.user)
        mp = WarehouseLocation.objects.create(sifra='B-03', naziv='Maloprodaja Sarajevo')
        only_mp = Product.objects.create(
            naziv='Samo MP', sifra='MP-ONLY', cijena=Decimal('7.00'),
            stanje=0, na_stanju=False, magacin_sync_at=timezone.now(),
        )
        apply_movement(product=only_mp, location=mp, tip='prijem', kolicina=4)
        created = self.client.post(reverse('staff_magacin_narudzba_nova'), {
            'ime_prezime': 'MP Kupac Nar',
            'telefon': '061909090',
            'product_id': [str(only_mp.pk)],
            'variation_id': [''],
            'kolicina': ['2'],
            'mp_ok': ['0'],
        })
        self.assertEqual(created.status_code, 302)
        order = Order.objects.get(ime_prezime='MP Kupac Nar')
        self.assertNotIn('Nije popisan', order.napomena or '')
        hold = OrderStockHold.objects.get(narudzba=order)
        self.assertEqual(hold.location_id, mp.pk)
        self.assertEqual(hold.kolicina, 2)
        pick = self.client.get(reverse('staff_magacin_pakuj_detail', args=[order.broj]))
        self.assertEqual(pick.status_code, 200)
        queue = json.loads(pick.context['pick_queue_json'])
        self.assertEqual(queue[0]['loc'], 'B-03')
        self.assertFalse(queue[0].get('nije_popisan'))

    def test_spare_part_line_on_order_and_picking(self):
        self.client.force_login(self.user)
        created = self.client.post(reverse('staff_magacin_narudzba_nova'), {
            'ime_prezime': 'Spare Kupac',
            'telefon': '061777888',
            'product_id': [''],
            'variation_id': [''],
            'kolicina': ['2'],
            'mp_ok': ['0'],
            'rezervni': ['1'],
            'spare_naziv': ['MATE Rage Feeder GORNJA SEKCIJA'],
            'spare_cijena': ['25.00'],
            'bez_dostave': '1',
        })
        self.assertEqual(created.status_code, 302)
        order = Order.objects.get(ime_prezime='Spare Kupac')
        item = order.stavke.get()
        self.assertTrue(item.rezervni_dio)
        self.assertIsNone(item.artikal_id)
        self.assertEqual(item.naziv, 'MATE Rage Feeder GORNJA SEKCIJA')
        self.assertEqual(item.kolicina, 2)
        self.assertEqual(item.cijena, Decimal('25.00'))
        self.assertEqual(order.dostava, Decimal('0.00'))
        self.assertEqual(order.ukupno, Decimal('50.00'))
        self.assertEqual(stock_totals(self.product)['rezervisano'], 0)
        listed = self.client.get(reverse('staff_magacin_pakuj'))
        self.assertContains(listed, reverse('staff_magacin_pakuj_detail', args=[order.broj]))
        pick = self.client.get(reverse('staff_magacin_pakuj_detail', args=[order.broj]))
        self.assertEqual(pick.status_code, 200)
        queue = json.loads(pick.context['pick_queue_json'])
        self.assertTrue(queue)
        self.assertTrue(queue[0]['rezervni'])
        self.assertEqual(queue[0]['need'], 2)
        self.assertIn('MATE Rage Feeder GORNJA SEKCIJA', queue[0]['naziv'])
        mixed = self.client.post(reverse('staff_magacin_narudzba_nova'), {
            'ime_prezime': 'Spare Mix',
            'telefon': '061777889',
            'product_id': [str(self.product.pk), ''],
            'variation_id': ['', ''],
            'kolicina': ['1', '1'],
            'mp_ok': ['0', '0'],
            'rezervni': ['0', '1'],
            'spare_naziv': ['', 'Donja sekcija'],
            'spare_cijena': ['', '10'],
        })
        self.assertEqual(mixed.status_code, 302)
        mix_order = Order.objects.get(ime_prezime='Spare Mix')
        self.assertEqual(mix_order.stavke.count(), 2)
        self.assertTrue(mix_order.stavke.filter(rezervni_dio=True, naziv='Donja sekcija').exists())
        self.assertEqual(stock_totals(self.product)['rezervisano'], 1)

    def test_manual_order_percent_discount_reduces_total(self):
        self.client.force_login(self.user)
        created = self.client.post(reverse('staff_magacin_narudzba_nova'), {
            'ime_prezime': 'Popust Kupac',
            'telefon': '061222333',
            'product_id': [str(self.product.pk)],
            'variation_id': [''],
            'kolicina': ['2'],
            'mp_ok': ['0'],
            'popust_pct': '10',
        })
        self.assertEqual(created.status_code, 302)
        order = Order.objects.get(ime_prezime='Popust Kupac')
        self.assertEqual(order.medjuzbir, Decimal('20.00'))
        self.assertEqual(order.popust, Decimal('2.00'))
        self.assertEqual(order.dostava, Decimal('11.00'))
        self.assertEqual(order.ukupno, Decimal('29.00'))
        self.assertEqual(order.popust_detalji[0]['postotak'], '10')
        self.assertIn('Ručni popust 10%', order.popust_detalji[0]['opis'])
        reserved = self.client.post(reverse('staff_magacin_narudzba_nova'), {
            'ime_prezime': 'Popust Rez',
            'telefon': '061222334',
            'product_id': [str(self.product.pk)],
            'variation_id': [''],
            'kolicina': ['1'],
            'mp_ok': ['0'],
            'popust_pct': '20',
            'action': 'rezervacija',
        })
        self.assertEqual(reserved.status_code, 302)
        rez = Order.objects.get(ime_prezime='Popust Rez')
        edit = self.client.get(reverse('staff_magacin_narudzba_nova'), {'broj': rez.broj})
        self.assertContains(edit, 'value="20"')
        blocked = self.client.post(reverse('staff_magacin_narudzba_nova'), {
            'ime_prezime': 'Popust Los',
            'telefon': '061222335',
            'product_id': [str(self.product.pk)],
            'variation_id': [''],
            'kolicina': ['1'],
            'mp_ok': ['0'],
            'popust_pct': '150',
        })
        self.assertEqual(blocked.status_code, 200)
        self.assertContains(blocked, 'Popust mora biti od 0 do 100')

    def test_manual_order_can_waive_shipping(self):
        from .pricing import sazetak_iz_narudzbe

        self.client.force_login(self.user)
        created = self.client.post(reverse('staff_magacin_narudzba_nova'), {
            'ime_prezime': 'Bez Dostave',
            'telefon': '061444555',
            'product_id': [str(self.product.pk)],
            'variation_id': [''],
            'kolicina': ['2'],
            'mp_ok': ['0'],
            'bez_dostave': '1',
        })
        self.assertEqual(created.status_code, 302)
        order = Order.objects.get(ime_prezime='Bez Dostave')
        self.assertEqual(order.medjuzbir, Decimal('20.00'))
        self.assertEqual(order.dostava, Decimal('0.00'))
        self.assertEqual(order.ukupno, Decimal('20.00'))
        self.assertTrue(any(row.get('bez_dostave') for row in order.popust_detalji))
        summary = sazetak_iz_narudzbe(order)
        self.assertEqual(summary['dostava'], Decimal('0.00'))
        self.assertEqual(summary['ukupno'], Decimal('20.00'))
        reserved = self.client.post(reverse('staff_magacin_narudzba_nova'), {
            'ime_prezime': 'Bez Dostave Rez',
            'telefon': '061444556',
            'product_id': [str(self.product.pk)],
            'variation_id': [''],
            'kolicina': ['1'],
            'mp_ok': ['0'],
            'bez_dostave': '1',
            'action': 'rezervacija',
        })
        self.assertEqual(reserved.status_code, 302)
        rez = Order.objects.get(ime_prezime='Bez Dostave Rez')
        edit = self.client.get(reverse('staff_magacin_narudzba_nova'), {'broj': rez.broj})
        self.assertContains(edit, 'id="mgOrderNoShip"')
        self.assertRegex(edit.content.decode(), r'id="mgOrderNoShip"[^>]*value="1"')

    def test_manual_order_card_payment_zeros_invoice(self):
        from .pricing import sazetak_iz_narudzbe

        self.client.force_login(self.user)
        created = self.client.post(reverse('staff_magacin_narudzba_nova'), {
            'ime_prezime': 'Kartica Kupac',
            'telefon': '061555666',
            'product_id': [str(self.product.pk)],
            'variation_id': [''],
            'kolicina': ['2'],
            'mp_ok': ['0'],
            'placanje': 'kartica',
        })
        self.assertEqual(created.status_code, 302)
        order = Order.objects.get(ime_prezime='Kartica Kupac')
        self.assertEqual(order.medjuzbir, Decimal('20.00'))
        self.assertEqual(order.popust, Decimal('20.00'))
        self.assertEqual(order.dostava, Decimal('0.00'))
        self.assertEqual(order.ukupno, Decimal('0.00'))
        self.assertIn('Plaćeno karticom', order.napomena)
        self.assertTrue(any(row.get('placanje') == 'kartica' for row in order.popust_detalji))
        summary = sazetak_iz_narudzbe(order)
        self.assertEqual(summary['dostava'], Decimal('0.00'))
        self.assertEqual(summary['ukupno'], Decimal('0.00'))
        self.assertEqual(summary['popust'], Decimal('20.00'))
        self.assertIn('Plaćeno karticom', summary['pogodnosti'])
        reserved = self.client.post(reverse('staff_magacin_narudzba_nova'), {
            'ime_prezime': 'Kartica Rez',
            'telefon': '061555667',
            'product_id': [str(self.product.pk)],
            'variation_id': [''],
            'kolicina': ['1'],
            'mp_ok': ['0'],
            'placanje': 'kartica',
            'action': 'rezervacija',
        })
        self.assertEqual(reserved.status_code, 302)
        rez = Order.objects.get(ime_prezime='Kartica Rez')
        edit = self.client.get(reverse('staff_magacin_narudzba_nova'), {'broj': rez.broj})
        self.assertRegex(
            edit.content.decode(),
            r'name="placanje" value="kartica"[^>]*checked|value="kartica"[^>]*checked',
        )
        cash = self.client.post(reverse('staff_magacin_narudzba_nova'), {
            'ime_prezime': 'Gotovina Kupac',
            'telefon': '061555668',
            'product_id': [str(self.product.pk)],
            'variation_id': [''],
            'kolicina': ['1'],
            'mp_ok': ['0'],
        })
        self.assertEqual(cash.status_code, 302)
        cash_order = Order.objects.get(ime_prezime='Gotovina Kupac')
        self.assertEqual(cash_order.dostava, Decimal('11.00'))
        self.assertEqual(cash_order.ukupno, Decimal('21.00'))
        self.assertNotIn('Plaćeno karticom', cash_order.napomena)

    def test_manual_reservation_holds_stock_and_stays_editable(self):
        self.client.force_login(self.user)
        created = self.client.post(reverse('staff_magacin_narudzba_nova'), {
            'ime_prezime': 'Reza Kupac',
            'telefon': '061999888',
            'product_id': [str(self.product.pk)],
            'variation_id': [''],
            'kolicina': ['2'],
            'mp_ok': ['0'],
            'action': 'rezervacija',
        })
        self.assertEqual(created.status_code, 302)
        order = Order.objects.get(ime_prezime='Reza Kupac')
        self.assertEqual(order.status, Order.Status.REZERVACIJA)
        self.assertEqual(order.lager_status, Order.LagerStatus.REZERVISANO)
        self.assertEqual(stock_totals(self.product)['rezervisano'], 2)
        self.assertEqual(stock_totals(self.product)['dostupno'], 6)
        self.assertIn('broj=' + order.broj, created['Location'])
        picking = self.client.get(reverse('staff_magacin_pakuj'))
        self.assertNotContains(picking, 'Reza Kupac')
        listed = self.client.get(reverse('staff_magacin_narudzbe'))
        self.assertContains(listed, 'Reza Kupac')
        self.assertContains(listed, 'Rezervacija')
        edit = self.client.get(reverse('staff_magacin_narudzba_nova'), {'broj': order.broj})
        self.assertContains(edit, 'Rezervacija #')
        self.assertContains(edit, 'Test braid')
        saved = self.client.post(reverse('staff_magacin_narudzba_nova'), {
            'order_broj': order.broj,
            'ime_prezime': 'Reza Kupac',
            'telefon': '061999888',
            'product_id': [str(self.product.pk)],
            'variation_id': [''],
            'kolicina': ['3'],
            'mp_ok': ['0'],
            'action': 'sacuvaj',
        })
        self.assertEqual(saved.status_code, 302)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.NOVA)
        self.assertEqual(order.stavke.get().kolicina, 3)
        self.assertEqual(stock_totals(self.product)['rezervisano'], 3)
        picking_ready = self.client.get(reverse('staff_magacin_pakuj'))
        self.assertContains(picking_ready, 'Reza Kupac')

    def test_created_order_stays_editable_and_updates_picking(self):
        from .views_magacin import apply_order_pick

        extra = Product.objects.create(
            naziv='Drugi artikal', sifra='TST-2', cijena=Decimal('5.00'),
            stanje=4, na_stanju=True, magacin_sync_at=timezone.now(),
        )
        loc = WarehouseLocation.objects.get(sifra='T-1')
        apply_movement(product=extra, location=loc, tip='prijem', kolicina=4)

        self.client.force_login(self.user)
        created = self.client.post(reverse('staff_magacin_narudzba_nova'), {
            'ime_prezime': 'Greska Unos',
            'telefon': '061121212',
            'product_id': [str(self.product.pk)],
            'variation_id': [''],
            'kolicina': ['1'],
            'mp_ok': ['0'],
        })
        self.assertEqual(created.status_code, 302)
        order = Order.objects.get(ime_prezime='Greska Unos')
        old_item = order.stavke.get()
        apply_order_pick(order, [{
            'key': f'{old_item.pk}:T-1',
            'item_id': old_item.pk,
            'got': 1,
            'need': 1,
            'done': True,
        }])
        order.refresh_from_db()
        self.assertTrue(order.pick_state)

        detail = self.client.get(reverse('staff_order_detail', args=[order.broj]))
        self.assertContains(detail, 'Izmijeni narudžbu')
        self.assertContains(detail, reverse('staff_magacin_narudzba_nova'))

        form = self.client.get(reverse('staff_magacin_narudzba_nova'), {'broj': order.broj})
        self.assertEqual(form.status_code, 200)
        self.assertContains(form, 'Narudžba #')
        self.assertContains(form, 'Test braid')
        self.assertContains(form, 'picking se usklađuje')

        saved = self.client.post(reverse('staff_magacin_narudzba_nova'), {
            'order_broj': order.broj,
            'ime_prezime': 'Greska Unos',
            'telefon': '061121212',
            'product_id': [str(extra.pk)],
            'variation_id': [''],
            'kolicina': ['2'],
            'mp_ok': ['0'],
            'action': 'sacuvaj',
        })
        self.assertEqual(saved.status_code, 302)
        self.assertEqual(saved['Location'], reverse('staff_magacin_pakuj'))
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.NOVA)
        self.assertEqual(order.lager_status, Order.LagerStatus.REZERVISANO)
        names = list(order.stavke.values_list('naziv', flat=True))
        self.assertEqual(names, ['Drugi artikal'])
        self.assertEqual(order.stavke.get().kolicina, 2)
        self.assertFalse(order.pick_state)
        self.assertEqual(stock_totals(self.product)['rezervisano'], 0)
        self.assertEqual(stock_totals(extra)['rezervisano'], 2)

        pick = self.client.get(reverse('staff_magacin_pakuj_detail', args=[order.broj]))
        self.assertEqual(pick.status_code, 200)
        self.assertTrue(pick.context['can_edit_order'])
        self.assertContains(pick, 'Izmijeni narudžbu')
        self.assertContains(pick, 'id="pkEditQuery"')
        queue = json.loads(pick.context['pick_queue_json'])
        nazivi = [row['naziv'] for row in queue]
        self.assertIn('Drugi artikal', nazivi)
        self.assertNotIn('Test braid', nazivi)
        self.assertEqual(sum(row['need'] for row in queue), 2)

    def test_pick_page_add_and_remove_syncs_queue(self):
        extra = Product.objects.create(
            naziv='Zamjena', sifra='TST-3', cijena=Decimal('7.00'),
            stanje=5, na_stanju=True, magacin_sync_at=timezone.now(),
        )
        loc = WarehouseLocation.objects.get(sifra='T-1')
        apply_movement(product=extra, location=loc, tip='prijem', kolicina=5)

        self.client.force_login(self.user)
        created = self.client.post(reverse('staff_magacin_narudzba_nova'), {
            'ime_prezime': 'Pick Edit',
            'telefon': '061343434',
            'product_id': [str(self.product.pk)],
            'variation_id': [''],
            'kolicina': ['1'],
            'mp_ok': ['0'],
        })
        self.assertEqual(created.status_code, 302)
        order = Order.objects.get(ime_prezime='Pick Edit')
        old_item = order.stavke.get()
        pick = self.client.get(reverse('staff_magacin_pakuj_detail', args=[order.broj]))
        self.assertEqual(pick.status_code, 200)
        self.assertTrue(pick.context['can_edit_order'])

        added = self.client.post(
            reverse('staff_magacin_pakuj_detail', args=[order.broj]),
            {
                'action': 'dodaj',
                'product_id': str(extra.pk),
                'kolicina': '1',
                'mp_ok': '0',
            },
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )
        self.assertEqual(added.status_code, 200)
        payload = added.json()
        self.assertTrue(payload.get('ok'))
        order.refresh_from_db()
        self.assertEqual(order.stavke.count(), 2)
        new_item = order.stavke.get(artikal=extra)

        again = self.client.get(reverse('staff_magacin_pakuj_detail', args=[order.broj]))
        queue = json.loads(again.context['pick_queue_json'])
        item_ids = {row['item_id'] for row in queue}
        self.assertIn(old_item.pk, item_ids)
        self.assertIn(new_item.pk, item_ids)

        removed = self.client.post(
            reverse('staff_magacin_pakuj_detail', args=[order.broj]),
            {'action': 'ukloni', 'stavka_id': str(old_item.pk)},
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )
        self.assertEqual(removed.status_code, 200)
        self.assertTrue(removed.json().get('ok'))
        order.refresh_from_db()
        self.assertEqual(list(order.stavke.values_list('artikal_id', flat=True)), [extra.pk])
        self.assertFalse(
            any(
                (isinstance(row, dict) and row.get('item_id') == old_item.pk)
                for row in (order.pick_state or {}).values()
            )
        )

        final = self.client.get(reverse('staff_magacin_pakuj_detail', args=[order.broj]))
        queue = json.loads(final.context['pick_queue_json'])
        item_ids = {row['item_id'] for row in queue}
        self.assertNotIn(old_item.pk, item_ids)
        self.assertIn(new_item.pk, item_ids)
        self.assertNotIn('Test braid', [row['naziv'] for row in queue])
        self.assertIn('Zamjena', [row['naziv'] for row in queue])

        validate_order_stock(order, user=self.user)
        blocked = self.client.get(reverse('staff_magacin_narudzba_nova'), {'broj': order.broj})
        self.assertEqual(blocked.status_code, 302)
        self.assertEqual(blocked['Location'], reverse('staff_order_detail', args=[order.broj]))

    def test_reservation_can_be_cancelled(self):
        self.client.force_login(self.user)
        self.client.post(reverse('staff_magacin_narudzba_nova'), {
            'ime_prezime': 'Predomislio',
            'telefon': '061777666',
            'product_id': [str(self.product.pk)],
            'variation_id': [''],
            'kolicina': ['2'],
            'mp_ok': ['0'],
            'action': 'rezervacija',
        })
        order = Order.objects.get(ime_prezime='Predomislio')
        self.assertEqual(stock_totals(self.product)['rezervisano'], 2)
        edit = self.client.get(reverse('staff_magacin_narudzba_nova'), {'broj': order.broj})
        self.assertContains(edit, 'Otkaži narudžbu')
        cancelled = self.client.post(reverse('staff_magacin_narudzba_nova'), {
            'order_broj': order.broj,
            'action': 'otkazi',
        })
        self.assertEqual(cancelled.status_code, 302)
        self.assertEqual(cancelled['Location'], reverse('staff_magacin_narudzbe'))
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.OTKAZANA)
        self.assertEqual(order.lager_status, Order.LagerStatus.OTKAZANO)
        self.assertEqual(stock_totals(self.product)['rezervisano'], 0)
        self.assertEqual(stock_totals(self.product)['dostupno'], 8)
        listed = self.client.get(reverse('staff_magacin_narudzbe'))
        self.assertNotContains(listed, 'Predomislio')
        picking = self.client.get(reverse('staff_magacin_pakuj'))
        self.assertNotContains(picking, 'Predomislio')

    def test_packing_print_shows_picked_validated_qty_and_locations(self):
        from .magacin import validate_order_stock
        from .views_magacin import apply_order_pick

        self.client.force_login(self.user)
        first = self.client.post(reverse('staff_magacin_narudzba_nova'), {
            'ime_prezime': 'Ana Ribić',
            'telefon': '061111111',
            'product_id': [str(self.product.pk)],
            'variation_id': [''],
            'kolicina': ['3'],
            'mp_ok': ['0'],
        })
        self.assertEqual(first.status_code, 302)
        second = self.client.post(reverse('staff_magacin_narudzba_nova'), {
            'ime_prezime': 'Marko Savić',
            'telefon': '062222222',
            'product_id': [str(self.product.pk)],
            'variation_id': [''],
            'kolicina': ['1'],
            'mp_ok': ['0'],
        })
        self.assertEqual(second.status_code, 302)
        ana = Order.objects.get(ime_prezime='Ana Ribić')
        marko = Order.objects.get(ime_prezime='Marko Savić')
        item = ana.stavke.get()
        apply_order_pick(ana, [{
            'key': f'{item.pk}:T-1',
            'item_id': item.pk,
            'got': 2,
            'need': 3,
            'done': True,
        }])
        validate_order_stock(ana, user=self.user)
        validate_order_stock(marko, user=self.user)
        open_list = self.client.get(reverse('staff_magacin_narudzbe'))
        self.assertContains(open_list, 'Štampaj packing')
        self.assertContains(open_list, reverse('staff_magacin_narudzbe_packing'))
        self.assertEqual(open_list.context['packing_ready_count'], 1)
        empty = self.client.get(reverse('staff_magacin_narudzbe_packing'))
        self.assertEqual(empty.status_code, 200)
        self.assertContains(empty, 'Ana Ribić')
        self.assertContains(empty, 'GOTOVINSKI')
        self.assertNotContains(empty, 'KARTICA')
        self.assertContains(empty, 'class="pk-loc"')
        self.assertContains(empty, '2× T-1')
        self.assertContains(empty, 'class="col-qty">2<')
        self.assertNotContains(empty, 'class="col-qty">3<')
        self.assertNotContains(empty, 'page-break-before: always')
        self.assertNotContains(empty, 'Marko Savić')
        self.assertNotContains(empty, 'class="invoice-sheet"')
        ana.refresh_from_db()
        marko.refresh_from_db()
        self.assertTrue(ana.packing_odstampana)
        self.assertFalse(marko.packing_odstampana)
        validated = self.client.get(reverse('staff_magacin_narudzbe'), {'validirane': '1'})
        listed = [row.broj for row in validated.context['orders']]
        self.assertIn(ana.broj, listed)
        self.assertIn(marko.broj, listed)
        again = self.client.get(reverse('staff_magacin_narudzbe_packing'))
        self.assertEqual(again.status_code, 302)
        self.assertEqual(again['Location'], reverse('staff_magacin_narudzbe'))
        idle = self.client.get(reverse('staff_magacin_narudzbe'))
        self.assertEqual(idle.context['packing_ready_count'], 0)
        self.assertContains(idle, 'is-packing-idle')
        self.assertContains(idle, 'data-packing-reprint="1"')
        self.assertContains(idle, 'id="mgPackingReprintForm"')
        self.assertNotContains(idle, 'id="mgPackingSelected" aria-disabled')

    def test_packing_print_revives_for_new_picked_order(self):
        from .views_magacin import apply_order_pick, packing_ready_orders

        self.client.force_login(self.user)

        def enter_pick_validate(name, phone):
            created = self.client.post(reverse('staff_magacin_narudzba_nova'), {
                'ime_prezime': name,
                'telefon': phone,
                'product_id': [str(self.product.pk)],
                'variation_id': [''],
                'kolicina': ['1'],
                'mp_ok': ['0'],
            })
            self.assertEqual(created.status_code, 302)
            order = Order.objects.get(ime_prezime=name)
            item = order.stavke.get()
            apply_order_pick(order, [{
                'key': f'{item.pk}:T-1',
                'item_id': item.pk,
                'got': 1,
                'need': 1,
                'done': True,
            }], finalize=True)
            validate_order_stock(order, user=self.user)
            order.refresh_from_db()
            return order

        first = enter_pick_validate('Prvi Pack', '061111111')
        printed = self.client.get(reverse('staff_magacin_narudzbe_packing'))
        self.assertEqual(printed.status_code, 200)
        self.assertContains(printed, 'Prvi Pack')
        first.refresh_from_db()
        self.assertTrue(first.packing_odstampana)
        self.assertEqual(len(packing_ready_orders()), 0)

        second = enter_pick_validate('Drugi Pack', '062222222')
        revived = self.client.get(reverse('staff_magacin_narudzbe'))
        self.assertEqual(revived.context['packing_ready_count'], 1)
        self.assertNotContains(revived, 'is-packing-idle')
        ready = packing_ready_orders()
        self.assertEqual([order.pk for order in ready], [second.pk])

        again = self.client.get(reverse('staff_magacin_narudzbe_packing'))
        self.assertEqual(again.status_code, 200)
        self.assertContains(again, 'Drugi Pack')
        self.assertNotContains(again, 'Prvi Pack')
        second.refresh_from_db()
        first.refresh_from_db()
        self.assertTrue(second.packing_odstampana)
        self.assertTrue(first.packing_odstampana)

    def test_packing_ready_skips_old_unpicked_validated(self):
        from .views_magacin import apply_order_pick, packing_ready_orders

        self.client.force_login(self.user)
        olds = [
            Order(
                broj=f'S{i:04d}',
                ime_prezime=f'Stari {i}',
                email='s@example.com',
                telefon='061000000',
                adresa='A 1',
                grad='Sarajevo',
                ukupno=Decimal('10.00'),
                izvor=Order.Izvor.MAGACIN,
                status=Order.Status.ZAVRSENA,
                lager_status=Order.LagerStatus.VALIDIRANO,
            )
            for i in range(80)
        ]
        Order.objects.bulk_create(olds)
        Order.objects.filter(broj__startswith='S').update(
            kreirana=timezone.now() - timedelta(days=10),
        )
        OrderItem.objects.bulk_create([
            OrderItem(
                narudzba=order,
                artikal=self.product,
                naziv='Test braid',
                cijena=Decimal('10.00'),
                kolicina=1,
            )
            for order in Order.objects.filter(broj__startswith='S')
        ])
        created = self.client.post(reverse('staff_magacin_narudzba_nova'), {
            'ime_prezime': 'Nova Pack',
            'telefon': '063333333',
            'product_id': [str(self.product.pk)],
            'variation_id': [''],
            'kolicina': ['1'],
            'mp_ok': ['0'],
        })
        self.assertEqual(created.status_code, 302)
        nova = Order.objects.get(ime_prezime='Nova Pack')
        item = nova.stavke.get()
        apply_order_pick(nova, [{
            'key': f'{item.pk}:T-1',
            'item_id': item.pk,
            'got': 1,
            'need': 1,
            'done': True,
        }], finalize=True)
        validate_order_stock(nova, user=self.user)
        ready = packing_ready_orders()
        self.assertEqual([order.ime_prezime for order in ready], ['Nova Pack'])
        listed = self.client.get(reverse('staff_magacin_narudzbe'))
        self.assertEqual(listed.context['packing_ready_count'], 1)
        page = self.client.get(reverse('staff_magacin_narudzbe_packing'))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'Nova Pack')
        self.assertNotContains(page, 'Stari 0')

    def test_packing_reprint_today_requires_admin_password(self):
        from .views_magacin import apply_order_pick, packing_ready_orders

        self.client.force_login(self.user)

        def enter_pick_validate(name, phone):
            created = self.client.post(reverse('staff_magacin_narudzba_nova'), {
                'ime_prezime': name,
                'telefon': phone,
                'product_id': [str(self.product.pk)],
                'variation_id': [''],
                'kolicina': ['1'],
                'mp_ok': ['0'],
            })
            self.assertEqual(created.status_code, 302)
            order = Order.objects.get(ime_prezime=name)
            item = order.stavke.get()
            apply_order_pick(order, [{
                'key': f'{item.pk}:T-1',
                'item_id': item.pk,
                'got': 1,
                'need': 1,
                'done': True,
            }], finalize=True)
            validate_order_stock(order, user=self.user)
            order.refresh_from_db()
            return order

        today_order = enter_pick_validate('Danas Pack', '061111111')
        printed = self.client.get(reverse('staff_magacin_narudzbe_packing'))
        self.assertEqual(printed.status_code, 200)
        self.assertContains(printed, 'Danas Pack')
        today_order.refresh_from_db()
        self.assertTrue(today_order.packing_odstampana)
        self.assertEqual(len(packing_ready_orders()), 0)

        yesterday_order = enter_pick_validate('Juce Pack', '062222222')
        Order.objects.filter(pk=yesterday_order.pk).update(
            kreirana=timezone.now() - timedelta(days=1),
            zapakovana_at=timezone.now() - timedelta(days=1),
            packing_odstampana=True,
            packing_odstampana_at=timezone.now() - timedelta(days=1),
        )

        blocked = self.client.post(reverse('staff_magacin_narudzbe_packing_izbor'), {
            'lozinka': 'pogresno',
        })
        self.assertEqual(blocked.status_code, 302)
        self.assertEqual(blocked['Location'], reverse('staff_magacin_narudzbe'))
        sneak = self.client.get(reverse('staff_magacin_narudzbe_packing_izbor'))
        self.assertEqual(sneak.status_code, 302)
        sneak_print = self.client.post(reverse('staff_magacin_narudzbe_packing'), {
            'action': 'stampaj',
            'datum': timezone.localdate().isoformat(),
            'b': [today_order.broj],
        })
        self.assertEqual(sneak_print.status_code, 302)

        unlocked = self.client.post(reverse('staff_magacin_narudzbe_packing_izbor'), {
            'lozinka': 'admin',
        })
        self.assertRedirects(unlocked, reverse('staff_magacin_narudzbe_packing_izbor'))
        picker = self.client.get(reverse('staff_magacin_narudzbe_packing_izbor'))
        self.assertEqual(picker.status_code, 200)
        self.assertContains(picker, 'Reprint packinga')
        self.assertContains(picker, 'name="datum"')
        self.assertContains(picker, 'Danas Pack')
        self.assertNotContains(picker, 'Juce Pack')
        yesterday = timezone.localdate() - timedelta(days=1)
        older = self.client.get(
            reverse('staff_magacin_narudzbe_packing_izbor'),
            {'datum': yesterday.isoformat()},
        )
        self.assertEqual(older.status_code, 200)
        self.assertContains(older, 'Juce Pack')
        self.assertNotContains(older, 'Danas Pack')
        printed = self.client.post(reverse('staff_magacin_narudzbe_packing'), {
            'action': 'stampaj',
            'datum': yesterday.isoformat(),
            'b': [yesterday_order.broj],
        })
        self.assertEqual(printed.status_code, 200)
        self.assertContains(printed, 'Juce Pack')
        self.assertNotContains(printed, 'Danas Pack')
        skipped = self.client.post(reverse('staff_magacin_narudzbe_packing'), {
            'action': 'stampaj',
            'datum': timezone.localdate().isoformat(),
            'b': [],
        })
        self.assertEqual(skipped.status_code, 302)
        idle = self.client.get(reverse('staff_magacin_narudzbe'))
        self.assertEqual(idle.context['packing_ready_count'], 0)
        self.assertContains(idle, 'data-packing-reprint="1"')
        self.assertContains(idle, 'id="mgPackingReprintForm"')
        self.assertContains(idle, reverse('staff_magacin_narudzbe_packing_izbor'))

    def test_packing_print_marks_card_vs_cash(self):
        from .magacin import validate_order_stock
        from .views_magacin import apply_order_pick

        self.client.force_login(self.user)
        created = self.client.post(reverse('staff_magacin_narudzba_nova'), {
            'ime_prezime': 'Kartica Pack',
            'telefon': '061888777',
            'product_id': [str(self.product.pk)],
            'variation_id': [''],
            'kolicina': ['1'],
            'mp_ok': ['0'],
            'placanje': 'kartica',
        })
        self.assertEqual(created.status_code, 302)
        order = Order.objects.get(ime_prezime='Kartica Pack')
        item = order.stavke.get()
        apply_order_pick(order, [{
            'key': f'{item.pk}:T-1',
            'item_id': item.pk,
            'got': 1,
            'need': 1,
            'done': True,
        }])
        validate_order_stock(order, user=self.user)
        page = self.client.get(reverse('staff_magacin_narudzbe_packing'))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'Kartica Pack')
        self.assertContains(page, 'KARTICA')
        self.assertNotContains(page, 'GOTOVINSKI')

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
        self.assertContains(blocked, 'Nije popisan')
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
        self.assertIn('Nije popisan', order.napomena)
        self.assertEqual(order.stavke.get().artikal_id, self.zero.pk)
        self.assertEqual(allowed['Location'], reverse('staff_magacin_narudzbe'))
        orders_list = self.client.get(reverse('staff_magacin_narudzbe'))
        self.assertNotContains(orders_list, 'is-mp-lock')
        self.assertContains(orders_list, reverse('staff_order_detail', args=[order.broj]))
        print_ok = self.client.get(reverse('staff_magacin_narudzbe_stampa'), {'b': [order.broj]})
        self.assertEqual(print_ok.status_code, 200)
        self.assertContains(print_ok, 'print-job')
        self.assertContains(print_ok, 'Nije popisan')
        opened = self.client.get(reverse('staff_magacin_pakuj_detail', args=[order.broj]))
        self.assertEqual(opened.status_code, 200)
        queue = json.loads(opened.context['pick_queue_json'])
        self.assertTrue(queue)
        self.assertTrue(any(item.get('loc') == 'Nije popisan' for item in queue))
        self.assertTrue(any(item.get('nije_popisan') for item in queue))
        self.assertFalse(any(item.get('is_mp') for item in queue))
        unlocked = self.client.get(reverse('staff_magacin_pakuj'), {'status': 'sve'})
        self.assertContains(unlocked, reverse('staff_magacin_pakuj_detail', args=[order.broj]))
        self.assertEqual(unlocked.context['new_pack_orders_count'], 1)

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
        self.assertContains(page, 'Pošalji u X-Express')
        self.assertContains(page, reverse('staff_order_xexpress', args=[order.broj]))

    def test_xexpress_send_from_order_detail(self):
        import requests
        from unittest.mock import Mock

        from .xexpress_service import XExpressError, build_shipment_payload, extract_sifra

        self.client.force_login(self.user)
        order = Order.objects.create(
            ime_prezime='Ana Ribić',
            email='ana@example.com',
            telefon='061111111',
            adresa='Test 1',
            grad='Sarajevo',
            postanski_broj='71000',
            ukupno=Decimal('30.00'),
            izvor=Order.Izvor.MAGACIN,
        )
        payload = build_shipment_payload(order)
        self.assertEqual(payload['sifraExt'], order.broj)
        self.assertEqual(payload['nazivPrim'], 'Ana Ribić')
        self.assertEqual(payload['kontaktPrim'], 'Ana Ribić')
        self.assertEqual(payload['adresaPrim'], 'Test 1')
        self.assertEqual(payload['mjestoPrim'], 'Sarajevo')
        self.assertEqual(payload['pttPrim'], '71000')
        self.assertEqual(payload['telefonPrim'], '061111111')
        self.assertEqual(payload['vrednostPosiljke'], 30.0)

        no_zip = Order(
            broj='0099',
            ime_prezime='Ana Ribić',
            telefon='061111111',
            adresa='Ulica 12',
            grad='Sarajevo',
            postanski_broj='',
            ukupno=Decimal('30.00'),
        )
        from_city = build_shipment_payload(no_zip)
        self.assertEqual(from_city['pttPrim'], '71000')
        self.assertEqual(from_city['mjestoPrim'], 'Sarajevo')

        in_city = Order(
            broj='0100',
            ime_prezime='Ana Ribić',
            telefon='061111111',
            adresa='Ulica 12',
            grad='71000 Tuzla',
            postanski_broj='',
            ukupno=Decimal('12.00'),
        )
        parsed = build_shipment_payload(in_city)
        self.assertEqual(parsed['pttPrim'], '71000')
        self.assertEqual(parsed['mjestoPrim'], 'Tuzla')
        self.assertEqual(parsed['vrednostPosiljke'], 12.0)

        blank = Order(
            broj='0101',
            ime_prezime='Ana Ribić',
            telefon='061111111',
            adresa='Ulica 12',
            grad='',
            postanski_broj='',
            ukupno=Decimal('12.00'),
        )
        with self.assertRaises(XExpressError) as raised:
            build_shipment_payload(blank)
        self.assertIn('poštanski broj', str(raised.exception))
        self.assertEqual(payload['opisPosiljke'], 'Ribolovačka oprema')
        self.assertEqual(payload['brojPaketa'], 1)
        self.assertEqual(payload['tezina'], 2)
        self.assertEqual(payload['uslugaSifra'], 1)
        self.assertEqual(payload['obveznikPlacanja'], 1)
        self.assertEqual(payload['nacinPlacanja'], 1)
        self.assertEqual(payload['vrednostPosiljke'], 30.0)
        self.assertTrue(payload['otkupnina'])
        self.assertEqual(payload['iznosOtkupnine'], 30.0)
        self.assertEqual(extract_sifra([{'sifra': 'XE-1'}]), 'XE-1')
        self.assertEqual(extract_sifra({'data': {'sifra': 'XE-2'}}), 'XE-2')
        self.assertEqual(extract_sifra({'Posiljke': [{'Sifra': '99001'}]}), '99001')
        self.assertEqual(
            extract_sifra({'sifraExt': '0133', 'SifraPosiljke': 556677}, ignore={'0133'}),
            '556677',
        )
        self.assertEqual(extract_sifra({'sifraExt': '0133'}, ignore={'0133'}), '')

        order.popust_detalji = [{'placanje': 'kartica'}]
        order.save(update_fields=['popust_detalji'])
        card_payload = build_shipment_payload(order)
        self.assertFalse(card_payload['otkupnina'])
        self.assertEqual(card_payload['iznosOtkupnine'], 0)

        missing = self.client.post(reverse('staff_order_xexpress', args=[order.broj]))
        self.assertEqual(missing.status_code, 302)
        order.refresh_from_db()
        self.assertEqual(order.xexpress_sifra, '')

        fake = Mock()
        fake.status_code = 200
        fake.content = b'[{"sifra":"XE-999"}]'
        fake.json.return_value = [{'sifra': 'XE-999'}]
        with override_settings(
            XEXPRESS_USERNAME='xe-user',
            XEXPRESS_PASSWORD='xe-pass',
            XEXPRESS_API_URL='https://api.x-express.ba/v1',
        ):
            with patch('EcommerceApp.xexpress_service.requests.post', return_value=fake) as mocked:
                sent = self.client.post(reverse('staff_order_xexpress', args=[order.broj]))
            mocked.assert_called_once()
            called_url = mocked.call_args.args[0] if mocked.call_args.args else mocked.call_args.kwargs.get('url')
            kwargs = mocked.call_args.kwargs
            self.assertTrue(str(called_url).endswith('/najava/v2'))
            self.assertEqual(kwargs['auth'], ('xe-user', 'xe-pass'))
            self.assertEqual(kwargs['timeout'], 20)
            body = kwargs['json']
            self.assertIsInstance(body, list)
            self.assertEqual(body[0]['sifraExt'], order.broj)
        self.assertEqual(sent.status_code, 302)
        order.refresh_from_db()
        self.assertEqual(order.xexpress_sifra, 'XE-999')
        self.assertIsNotNone(order.xexpress_poslano_at)

        done = self.client.get(reverse('staff_order_detail', args=[order.broj]))
        self.assertContains(done, 'X-Express XE-999')
        self.assertNotContains(done, '>Pošalji u X-Express<')

        with override_settings(XEXPRESS_USERNAME='xe-user', XEXPRESS_PASSWORD='xe-pass'):
            with patch('EcommerceApp.xexpress_service.requests.post') as blocked:
                again = self.client.post(reverse('staff_order_xexpress', args=[order.broj]))
            blocked.assert_not_called()
        self.assertEqual(again.status_code, 302)
        order.refresh_from_db()
        self.assertEqual(order.xexpress_sifra, 'XE-999')

        order.xexpress_sifra = ''
        order.xexpress_poslano_at = None
        order.save(update_fields=['xexpress_sifra', 'xexpress_poslano_at'])
        with override_settings(XEXPRESS_USERNAME='xe-user', XEXPRESS_PASSWORD='xe-pass'):
            with patch(
                'EcommerceApp.xexpress_service.requests.post',
                side_effect=requests.Timeout('slow'),
            ):
                timed = self.client.post(reverse('staff_order_xexpress', args=[order.broj]))
        self.assertEqual(timed.status_code, 302)
        order.refresh_from_db()
        self.assertEqual(order.xexpress_sifra, '')

        err = Mock()
        err.status_code = 400
        err.content = b'{"message":"Neispravna adresa"}'
        err.text = '{"message":"Neispravna adresa"}'
        err.json.return_value = {'message': 'Neispravna adresa'}
        with override_settings(XEXPRESS_USERNAME='xe-user', XEXPRESS_PASSWORD='xe-pass'):
            with patch('EcommerceApp.xexpress_service.requests.post', return_value=err):
                failed = self.client.post(
                    reverse('staff_order_xexpress', args=[order.broj]),
                    {'next': reverse('staff_order_detail', args=[order.broj])},
                    follow=True,
                )
        self.assertContains(failed, 'Neispravna adresa')
        order.refresh_from_db()
        self.assertEqual(order.xexpress_sifra, '')

        dup = Mock()
        dup.status_code = 420
        dup.content = b'{"message":"duplicate key value violates unique constraint \\"xo_posiljka_ix1\\" Detail: Key (ibp, ugovor_id)=(0134, 4147) already exists."}'
        dup.text = dup.content.decode()
        dup.json.return_value = {
            'message': 'duplicate key value violates unique constraint "xo_posiljka_ix1" '
            'Detail: Key (ibp, ugovor_id)=(0134, 4147) already exists.',
        }
        looked = Mock()
        looked.status_code = 200
        looked.content = b'{"Sifra":"X00998877"}'
        looked.json.return_value = {'Sifra': 'X00998877'}
        with override_settings(XEXPRESS_USERNAME='xe-user', XEXPRESS_PASSWORD='xe-pass'):
            with patch('EcommerceApp.xexpress_service.requests.post', return_value=dup):
                with patch('EcommerceApp.xexpress_service.requests.get', return_value=looked):
                    exists = self.client.post(
                        reverse('staff_order_xexpress', args=[order.broj]),
                        follow=True,
                    )
        self.assertContains(exists, 'već postoji')
        order.refresh_from_db()
        self.assertEqual(order.xexpress_sifra, 'X00998877')

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
        self.assertContains(pack_list, order.ime_prezime)
        pack_detail = self.client.get(reverse('staff_magacin_pakuj_detail', args=[order.broj]))
        self.assertEqual(pack_detail.status_code, 200)
        self.assertContains(pack_detail, order.ime_prezime)
        self.assertContains(pack_detail, order.broj)
        self.assertContains(pack_detail, 'T-1')
        self.assertContains(pack_detail, '01')
        self.assertContains(pack_detail, 'Lokacija (odakle se uzima)')
        self.assertContains(pack_detail, 'Pokupi manje')
        self.assertContains(pack_detail, 'Pokupi sve')
        self.assertContains(pack_detail, 'Otkaži')
        self.assertContains(pack_detail, 'name="action" value="otkazi"')
        self.assertContains(pack_detail, 'Validatuj')
        self.assertContains(pack_detail, 'Završi picking')
        self.assertContains(pack_detail, 'Stavke za pakovanje')
        self.assertContains(pack_detail, 'Trenutno')
        self.assertContains(pack_detail, 'Pokupljeno')
        self.assertContains(pack_detail, 'Za pokupiti')
        self.assertContains(pack_detail, 'data-pk-view="now"')
        self.assertNotContains(pack_detail, 'Po redu. Skeniraj ili pokupi')
        self.assertNotContains(pack_detail, 'Ukupno stavki')
        self.assertNotContains(pack_detail, 'id="pkSeparatedWrap"')
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
        self.assertEqual(stock_totals(self.product)['dostupno'], 6)

    def test_webshop_validate_deducts_local_stock(self):
        self.client.force_login(self.user)
        order = Order.objects.create(
            ime_prezime='Online Kupac', telefon='061444555', email='o@example.com',
            adresa='Web 1', grad='Sarajevo', ukupno=Decimal('20.00'),
            izvor=Order.Izvor.WEBSHOP,
        )
        OrderItem.objects.create(
            narudzba=order, naziv='Test braid', cijena=Decimal('10.00'), kolicina=2,
            artikal=self.product,
        )
        self.assertEqual(stock_totals(self.product)['dostupno'], 8)
        validated = self.client.post(reverse('staff_order_detail', args=[order.broj]), {
            'action': 'validiraj',
        })
        self.assertEqual(validated.status_code, 302)
        order.refresh_from_db()
        self.product.refresh_from_db()
        loc_stock = WarehouseStock.objects.get(
            product=self.product, location__sifra='T-1', variation__isnull=True,
        )
        self.assertEqual(order.lager_status, Order.LagerStatus.VALIDIRANO)
        self.assertEqual(order.status, Order.Status.ZAVRSENA)
        self.assertEqual(loc_stock.kolicina, 6)
        self.assertEqual(self.product.stanje, 6)
        self.assertEqual(stock_totals(self.product)['dostupno'], 6)

    def test_webshop_order_with_warehouse_stock_picks_location_not_mp(self):
        from .views_magacin import order_needs_mp_check

        self.client.force_login(self.user)
        order = Order.objects.create(
            ime_prezime='Online Ima Lager', telefon='061777888', email='lager@example.com',
            adresa='Web 2', grad='Sarajevo', ukupno=Decimal('20.00'),
            izvor=Order.Izvor.WEBSHOP,
        )
        OrderItem.objects.create(
            narudzba=order, naziv='Test braid', cijena=Decimal('10.00'), kolicina=2,
            artikal=self.product, sifra=self.product.sifra,
        )
        self.assertEqual(stock_totals(self.product)['dostupno'], 8)
        self.assertFalse(order_needs_mp_check(order))
        listing = self.client.get(reverse('staff_magacin_pakuj'))
        self.assertEqual(listing.status_code, 200)
        self.assertContains(listing, 'Online Ima Lager')
        self.assertNotContains(listing, 'pk-mp-cta')
        pick = self.client.get(reverse('staff_magacin_pakuj_detail', args=[order.broj]))
        self.assertEqual(pick.status_code, 200)
        queue = json.loads(pick.context['pick_queue_json'])
        self.assertEqual(len(queue), 1)
        self.assertEqual(queue[0]['loc'], 'T-1')
        self.assertFalse(queue[0].get('is_mp'))
        self.assertEqual(queue[0]['need'], 2)

        empty = Product.objects.create(
            naziv='Nema magacin', sifra='NO-LOC', cijena=Decimal('3.00'),
            stanje=0, na_stanju=False, magacin_sync_at=timezone.now(),
        )
        mp_order = Order.objects.create(
            ime_prezime='Online Nema Lager', telefon='061777889', email='nema@example.com',
            adresa='Web 3', grad='Sarajevo', ukupno=Decimal('3.00'),
            izvor=Order.Izvor.WEBSHOP,
        )
        OrderItem.objects.create(
            narudzba=mp_order, naziv=empty.naziv, cijena=Decimal('3.00'), kolicina=1,
            artikal=empty, sifra=empty.sifra,
        )
        self.assertTrue(order_needs_mp_check(mp_order))

    def test_validate_deducts_from_picking_location(self):
        from .views_magacin import apply_order_pick

        loc_a = WarehouseLocation.objects.get(sifra='T-1')
        loc_b = WarehouseLocation.objects.create(sifra='T-2', naziv='Druga loc', redoslijed=20)
        apply_movement(product=self.product, location=loc_b, tip='prijem', kolicina=4)
        self.client.force_login(self.user)
        created = self.client.post(reverse('staff_magacin_narudzba_nova'), {
            'ime_prezime': 'Pick Lokacija',
            'telefon': '061333222',
            'product_id': [str(self.product.pk)],
            'variation_id': [''],
            'kolicina': ['3'],
            'mp_ok': ['0'],
        })
        self.assertEqual(created.status_code, 302)
        order = Order.objects.get(ime_prezime='Pick Lokacija')
        item = order.stavke.get()
        apply_order_pick(order, [{
            'key': f'{item.pk}:T-2',
            'item_id': item.pk,
            'loc': 'T-2',
            'got': 3,
            'need': 3,
            'done': True,
        }], finalize=True, user=self.user)
        validate_order_stock(order, user=self.user)
        stock_a = WarehouseStock.objects.get(product=self.product, location=loc_a, variation__isnull=True)
        stock_b = WarehouseStock.objects.get(product=self.product, location=loc_b, variation__isnull=True)
        self.assertEqual(stock_b.kolicina, 1)
        self.assertEqual(stock_a.rezervisano, 0)
        self.assertEqual(stock_totals(self.product)['dostupno'], 9)

    def test_validate_sells_hold_when_pick_location_has_no_stock(self):
        from .views_magacin import apply_order_pick

        loc_a = WarehouseLocation.objects.get(sifra='T-1')
        loc_b = WarehouseLocation.objects.create(sifra='H02', naziv='H02', redoslijed=20)
        self.client.force_login(self.user)
        created = self.client.post(reverse('staff_magacin_narudzba_nova'), {
            'ime_prezime': 'VP H02 prazno',
            'telefon': '061333223',
            'product_id': [str(self.product.pk)],
            'variation_id': [''],
            'kolicina': ['3'],
            'mp_ok': ['0'],
        })
        self.assertEqual(created.status_code, 302)
        order = Order.objects.get(ime_prezime='VP H02 prazno')
        item = order.stavke.get()
        stock_a = WarehouseStock.objects.get(product=self.product, location=loc_a)
        self.assertEqual(stock_a.kolicina, 8)
        self.assertEqual(stock_a.rezervisano, 3)
        apply_order_pick(order, [{
            'key': f'{item.pk}:H02',
            'item_id': item.pk,
            'loc': 'H02',
            'got': 3,
            'need': 3,
            'done': True,
        }], finalize=True, user=self.user)
        validate_order_stock(order, user=self.user)
        order.refresh_from_db()
        stock_a.refresh_from_db()
        self.assertEqual(order.lager_status, Order.LagerStatus.VALIDIRANO)
        self.assertEqual(stock_a.kolicina, 5)
        self.assertEqual(stock_a.rezervisano, 0)
        sale = WarehouseMovement.objects.filter(
            product=self.product,
            tip=WarehouseMovement.Tip.PRODAJA,
            napomena__contains=order.broj,
        ).first()
        self.assertIsNotNone(sale)
        self.assertEqual(sale.location_id, loc_a.pk)
        self.assertEqual(sale.kolicina, -3)

    def test_vp_validate_deducts_reserved_qty_after_order_edit(self):
        self.client.force_login(self.user)
        self.client.post(reverse('staff_magacin_vp_narudzba'), {'action': 'novi'})
        customer = WarehouseCustomer.objects.create(
            ime_prezime='VP Validacija', telefon='061444555',
        )
        self.client.post(reverse('staff_magacin_vp_narudzba'), {
            'action': 'kupac', 'customer_id': customer.pk,
        })
        self.client.post(reverse('staff_magacin_vp_narudzba'), {
            'action': 'dodaj', 'product_id': self.product.pk, 'kolicina': '2',
        })
        self.client.post(reverse('staff_magacin_vp_narudzba'), {'action': 'zavrsi'})
        order = Order.objects.get(ime_prezime='VP Validacija')
        loc = WarehouseLocation.objects.get(sifra='T-1')
        stock = WarehouseStock.objects.get(product=self.product, location=loc)
        self.assertEqual(stock.rezervisano, 2)
        validated = self.client.post(
            reverse('staff_order_detail', args=[order.broj]),
            {'action': 'validiraj'},
        )
        self.assertRedirects(validated, reverse('staff_magacin_narudzbe'))
        stock.refresh_from_db()
        order.refresh_from_db()
        self.assertEqual(order.lager_status, Order.LagerStatus.VALIDIRANO)
        self.assertEqual(stock.kolicina, 6)
        self.assertEqual(stock.rezervisano, 0)
        self.assertTrue(
            WarehouseMovement.objects.filter(
                product=self.product,
                tip=WarehouseMovement.Tip.PRODAJA,
                napomena__contains=order.broj,
            ).exists()
        )

    def test_narudzbe_click_opens_detail_not_picking(self):
        self.client.force_login(self.user)
        created = self.client.post(reverse('staff_magacin_narudzba_nova'), {
            'ime_prezime': 'Pregled Kupac',
            'telefon': '061000111',
            'product_id': [str(self.product.pk)],
            'variation_id': [''],
            'kolicina': ['1'],
            'mp_ok': ['0'],
        })
        self.assertEqual(created.status_code, 302)
        order = Order.objects.get(ime_prezime='Pregled Kupac')
        listed = self.client.get(reverse('staff_magacin_narudzbe'))
        detail_url = reverse('staff_order_detail', args=[order.broj])
        self.assertContains(listed, f'data-order-url="{detail_url}"')
        self.assertNotContains(listed, reverse('staff_magacin_pakuj_detail', args=[order.broj]))
        page = self.client.get(detail_url)
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'Pregled Kupac')
        self.assertContains(page, 'Picking')
        self.assertContains(page, reverse('staff_magacin_pakuj_detail', args=[order.broj]))

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
        self.assertContains(blocked, 'Nije popisan')
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

    def test_pick_queue_skips_unchecked_mp(self):
        from .views_magacin import _packing_location_groups, _pick_queue

        groups = _packing_location_groups([
            {
                'rb': 1, 'item_id': 1, 'naziv': 'Clip A', 'sifra': 'A', 'kolicina': 1,
                'picks': [{'location_name': 'T-1', 'take': 1}], 'check_mp': False,
            },
            {
                'rb': 2, 'item_id': 2, 'naziv': 'Clip MP', 'sifra': 'M', 'kolicina': 1,
                'picks': [], 'check_mp': True, 'shortfall': 1,
            },
        ])
        self.assertEqual([row['label'] for row in groups], ['T-1', 'Provjeri u MP'])
        queue = _pick_queue(groups)
        self.assertEqual([item['loc'] for item in queue], ['T-1'])

    def test_mixed_order_picking_waits_for_mp_check(self):
        self.client.force_login(self.user)
        created = self.client.post(reverse('staff_magacin_narudzba_nova'), {
            'ime_prezime': 'Mjesovita',
            'telefon': '062333333',
            'product_id': [str(self.product.pk), str(self.zero.pk)],
            'variation_id': ['', ''],
            'kolicina': ['1', '1'],
            'mp_ok': ['0', '1'],
        })
        self.assertEqual(created.status_code, 302)
        order = Order.objects.get(ime_prezime='Mjesovita')
        self.assertEqual(created['Location'], reverse('staff_magacin_narudzbe'))
        listing = self.client.get(reverse('staff_magacin_pakuj'))
        self.assertEqual(listing.status_code, 200)
        pick = self.client.get(reverse('staff_magacin_pakuj_detail', args=[order.broj]))
        self.assertEqual(pick.status_code, 200)
        queue = json.loads(pick.context['pick_queue_json'])
        self.assertTrue(queue)
        self.assertFalse(any(item.get('loc') == 'Provjeri u MP' for item in queue))
        self.assertTrue(any(item.get('loc') == 'Nije popisan' for item in queue))
        self.assertTrue(any(item.get('loc') == 'T-1' for item in queue))

    def test_picking_blocked_until_all_mp_checks_done(self):
        self.client.force_login(self.user)
        stocked = self.client.post(reverse('staff_magacin_narudzba_nova'), {
            'ime_prezime': 'Skladiste Pack',
            'telefon': '061444444',
            'product_id': [str(self.product.pk)],
            'variation_id': [''],
            'kolicina': ['1'],
            'mp_ok': ['0'],
        })
        self.assertEqual(stocked.status_code, 302)
        mp_created = self.client.post(reverse('staff_magacin_narudzba_nova'), {
            'ime_prezime': 'Mp Pack',
            'telefon': '061555555',
            'product_id': [str(self.zero.pk)],
            'variation_id': [''],
            'kolicina': ['1'],
            'mp_ok': ['1'],
        })
        self.assertEqual(mp_created.status_code, 302)
        warehouse = Order.objects.get(ime_prezime='Skladiste Pack')
        mp_order = Order.objects.get(ime_prezime='Mp Pack')
        opened_stock = self.client.get(reverse('staff_magacin_pakuj_detail', args=[warehouse.broj]))
        self.assertEqual(opened_stock.status_code, 200)
        opened_np = self.client.get(reverse('staff_magacin_pakuj_detail', args=[mp_order.broj]))
        self.assertEqual(opened_np.status_code, 200)
        queue = json.loads(opened_np.context['pick_queue_json'])
        self.assertTrue(any(item.get('loc') == 'Nije popisan' for item in queue))

    def test_mp_nema_removes_item_from_order(self):
        from .views_magacin import apply_order_pick
        from .magacin import drop_missing_pick_line

        self.client.force_login(self.user)
        created = self.client.post(reverse('staff_magacin_narudzba_nova'), {
            'ime_prezime': 'Izbaci Mp',
            'telefon': '061666666',
            'product_id': [str(self.product.pk), str(self.zero.pk)],
            'variation_id': ['', ''],
            'kolicina': ['1', '1'],
            'mp_ok': ['0', '1'],
        })
        self.assertEqual(created.status_code, 302)
        order = Order.objects.get(ime_prezime='Izbaci Mp')
        self.assertEqual(order.stavke.count(), 2)
        zero_item = order.stavke.get(artikal=self.zero)
        drop_missing_pick_line(order, zero_item, loc='Nije popisan', qty=1, user=self.user)
        order.refresh_from_db()
        names = list(order.stavke.values_list('naziv', flat=True))
        self.assertEqual(len(names), 1)
        self.assertIn('Test braid', names)
        self.assertNotIn('Prazan lager', names)

    def test_mp_partial_found_goes_on_invoice(self):
        from .views import _order_print_job
        from .views_magacin import apply_order_pick

        self.client.force_login(self.user)
        created = self.client.post(reverse('staff_magacin_narudzba_nova'), {
            'ime_prezime': 'Nasao Manje',
            'telefon': '061121314',
            'product_id': [str(self.zero.pk)],
            'variation_id': [''],
            'kolicina': ['5'],
            'mp_ok': ['1'],
        })
        self.assertEqual(created.status_code, 302)
        order = Order.objects.get(ime_prezime='Nasao Manje')
        item = order.stavke.get()
        self.assertEqual(item.kolicina, 5)
        pick = self.client.get(reverse('staff_magacin_pakuj_detail', args=[order.broj]))
        self.assertEqual(pick.status_code, 200)
        queue = json.loads(pick.context['pick_queue_json'])
        self.assertEqual(sum(row['need'] for row in queue if row.get('nije_popisan')), 5)
        apply_order_pick(order, [{
            'key': f'{item.pk}:Nije popisan',
            'item_id': item.pk,
            'got': 2,
            'need': 5,
            'done': True,
        }])
        item.refresh_from_db()
        self.assertEqual(item.kolicina_pokupljeno, 2)
        job = _order_print_job(order)
        self.assertEqual(job['stavke'][0]['kolicina'], 2)
        self.assertEqual(job['stavke'][0]['ukupno'], Decimal('4.00'))

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
        home = self.client.get(reverse('staff_magacin_narudzbe'))
        self.assertEqual(home.context['validated_count'], 2)
        self.assertContains(home, 'validirane=1&sve=1')
        page = self.client.get(reverse('staff_magacin_narudzbe'), {'validirane': '1'})
        listed = [row.broj for row in page.context['orders']]
        self.assertIn(today.broj, listed)
        self.assertNotIn(old.broj, listed)
        self.assertContains(page, 'Prikaži sve')
        all_page = self.client.get(reverse('staff_magacin_narudzbe'), {'validirane': '1', 'sve': '1'})
        all_listed = [row.broj for row in all_page.context['orders']]
        self.assertIn(today.broj, all_listed)
        self.assertIn(old.broj, all_listed)
        self.assertContains(page, 'Pretraži po imenu ili broju narudžbe')
        self.assertContains(page, 'Ime ili broj narudžbe')
        self.assertContains(page, 'name="pretraga"')
        self.assertContains(page, 'Štampaj račune')
        self.assertContains(page, reverse('staff_magacin_narudzbe_stampa') + '?b=' + today.broj)
        reprint = self.client.get(reverse('staff_magacin_narudzbe_stampa'), {'b': [today.broj]})
        self.assertEqual(reprint.status_code, 200)
        self.assertContains(reprint, today.ime_prezime)
        self.assertContains(reprint, 'data-already-printed="0"')
        by_name = self.client.get(reverse('staff_magacin_narudzbe'), {
            'validirane': '1', 'pretraga': 'Stara',
        })
        named = [row.broj for row in by_name.context['orders']]
        self.assertIn(old.broj, named)
        self.assertNotIn(today.broj, named)
        by_phone = self.client.get(reverse('staff_magacin_narudzbe'), {
            'validirane': '1', 'pretraga': '061 000 000',
        })
        phoned = [row.broj for row in by_phone.context['orders']]
        self.assertIn(old.broj, phoned)
        self.assertNotIn(today.broj, phoned)
        by_num = self.client.get(reverse('staff_magacin_narudzbe'), {
            'validirane': '1', 'pretraga': f'#{old.broj}',
        })
        numbered = [row.broj for row in by_num.context['orders']]
        self.assertEqual(numbered, [old.broj])
        missing = self.client.get(reverse('staff_magacin_narudzbe'), {
            'validirane': '1', 'pretraga': 'NemaOvogKupca',
        })
        self.assertEqual(list(missing.context['orders']), [])
        self.assertContains(missing, 'Nema narudžbi za')

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
        self.assertContains(detail, reverse('staff_magacin_uvoz_stampa', args=[uvoz.pk]))
        self.assertContains(detail, 'Štampaj')
        printed = self.client.get(reverse('staff_magacin_uvoz_stampa', args=[uvoz.pk]))
        self.assertEqual(printed.status_code, 200)
        self.assertContains(printed, 'Fox Edges Lead Clip')
        self.assertContains(printed, 'Excel Novi Artikal')
        self.assertContains(printed, 'Šifra')
        self.assertContains(printed, 'FOX-UVOZ-1')
        self.assertContains(printed, 'Količina')
        self.assertContains(printed, 'Fakturna')
        self.assertContains(printed, 'VPC netto')
        self.assertContains(printed, 'MPC brutto')
        self.assertContains(printed, '4.10')
        self.assertContains(printed, '7.50')
        self.assertContains(printed, 'window.print()')
        self.assertEqual(uvoz.stavke.count(), 2)
        clip = uvoz.stavke.get(artikal_naziv='Fox Edges Lead Clip')
        self.assertEqual(clip.status, UvozStavka.Status.UPDATED)
        self.assertEqual(clip.fakturna, Decimal('3.0000'))
        self.assertEqual(clip.vpc_netto, Decimal('4.10'))

        listing = self.client.get(reverse('staff_magacin_uvoz'))
        self.assertContains(listing, 'Uvoz test 13.08')
        self.assertContains(listing, 'Uvoz lokacija u MP')
        self.assertContains(listing, 'PROMJENA MPC')
        self.assertContains(listing, 'UKUPNA FAKTURNA')
        self.assertContains(listing, 'KREIRANO')
        self.assertContains(listing, 'AŽURIRANO')
        self.assertContains(listing, reverse('staff_magacin_uvoz_stampa', args=[uvoz.pk]))
        self.assertContains(listing, 'Štampaj')
        html = listing.content.decode()
        self.assertIn('22.00 KM', html)
        uvoz = Uvoz.objects.get(izvor=Uvoz.Izvor.MAGACIN, naziv='Uvoz test 13.08')
        self.assertEqual(uvoz.broj_kreirano, 1)
        self.assertEqual(uvoz.broj_azurirano, 1)
        self.assertEqual(uvoz.broj_mpc_promjena, 1)
        self.assertEqual(uvoz.ukupna_fakturna, Decimal('22.00'))

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

    def test_uvoz_location_to_mp_keeps_imports(self):
        from .magacin import create_magacin_uvoz_from_rows

        self.client.force_login(self.user)
        leftover_uvoz, leftover_stats = create_magacin_uvoz_from_rows(
            [{'artikal': 'Samo na Uvoz MP', 'kolicina': 6, 'mpc_brutto': Decimal('11.00')}],
            naziv='Uvoz samo UVOZ',
            user=self.user,
        )
        mixed_uvoz, _stats = create_magacin_uvoz_from_rows(
            [{'artikal': 'Fox Edges Lead Clip', 'kolicina': 4, 'mpc_brutto': Decimal('7.50')}],
            naziv='Uvoz vec u magacinu',
            user=self.user,
        )
        leftover = Product.objects.get(naziv='Samo na Uvoz MP')
        novi = WarehouseLocation.objects.get(naziv=NOVI_UVOZ_NAZIV)
        mp = WarehouseLocation.objects.create(
            sifra='B-03', naziv='Maloprodaja Sarajevo', redoslijed=20,
        )
        self.assertEqual(WarehouseStock.objects.get(product=leftover, location=novi).kolicina, 6)
        listing = self.client.get(reverse('staff_magacin_uvoz'))
        self.assertContains(listing, 'Uvoz lokacija u MP')
        moved = self.client.post(reverse('staff_magacin_uvoz'), {'action': 'uvoz_u_mp'})
        self.assertEqual(moved.status_code, 302)
        leftover.refresh_from_db()
        self.existing.refresh_from_db()
        self.assertEqual(WarehouseStock.objects.get(product=leftover, location=novi).kolicina, 0)
        self.assertEqual(WarehouseStock.objects.get(product=leftover, location=mp).kolicina, 6)
        self.assertTrue(leftover.na_stanju)
        self.assertTrue(leftover.aktivan)
        self.assertEqual(leftover.stanje, 6)
        self.assertEqual(stock_totals(leftover)['na_stanju'], 0)
        self.assertEqual(display_stock_totals(leftover)['dostupno'], 6)
        from .magacin import refresh_catalog_qty
        refresh_catalog_qty(leftover)
        leftover.refresh_from_db()
        self.assertTrue(leftover.na_stanju)
        self.assertEqual(leftover.stanje, 6)
        self.assertEqual(
            WarehouseStock.objects.get(product=self.existing, location=self.a10).kolicina,
            3,
        )
        self.assertEqual(
            WarehouseStock.objects.get(product=self.existing, location=novi).kolicina,
            4,
        )
        leftover_uvoz.refresh_from_db()
        mixed_uvoz.refresh_from_db()
        self.assertEqual(leftover_uvoz.stavke.count(), 1)
        self.assertEqual(mixed_uvoz.stavke.count(), 1)
        detail = self.client.get(reverse('staff_magacin_uvoz_detail', args=[leftover_uvoz.pk]))
        self.assertContains(detail, 'Samo na Uvoz MP')
        self.assertContains(detail, '6')
