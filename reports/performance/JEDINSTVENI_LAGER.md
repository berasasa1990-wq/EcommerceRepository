# WMS direktno na postojeći magacin — prijedlog promjene

## Ciljno stanje

Oba interfejsa koriste isti Product, WarehouseLocation, WarehouseStock i WarehouseMovement. WMS ne kopira količine u ProductWMSStock i ne poziva import_legacy_stock/apply_wms_stock pri otvaranju ili izmjeni. Postojeći izgled ostaje.

## Potrebne izmjene

- views_wms.workspace/zalihe i location_detail: originalne lokacije/zalihe, select_related i paginacija; identitet stock reda uključuje variation_key. Ne sabirati rezervisano i fizičku količinu.
- Prenos: postojeći magacin apply_movement TRANSFER uz zaključavanje originalnih zapisa i zaštitu rezervacija; WMSTransfer ostaje dokument, a lokacije vode na WarehouseLocation.
- Odvajanje: WMSPickLine ostaje istorijski zapis, ali koristi WarehouseLocation i odgovarajuću varijaciju. Završavanje webshop narudžbe ostaje validate_order_stock, bez dodatnog prethodnog skidanja istog lagera. Manual završavanje koristi originalni magacin servis u istoj transakciji.
- Očisti lokaciju: postojeći servis korekcije, provjera rezervacija i istorijska napomena, bez direktne izmjene kopije.
- WMSSettings transfer_location/picking_last_location, ProductWMSLocationMeta i panel +/-: svi izbori direktno WarehouseLocation; sačuvati modul provjere.
- wms_legacy.warehouse_compatible: ukloniti full sync i dirty mirrored-stock zapisivanje tek nakon kompletnog prebacivanja svih read/write putanja.

## Prijelaz podataka bez brisanja

Pripremiti aditivna FK polja za originalne lokacije. Mapirati postojeće reference preko WMSLocation.legacy_location_id; postojeća WMS istorija i tabele ostaju sačuvane. Napraviti read-only izvještaj nepovezanih lokacija prije backfilla. Ne pretpostavljati da su PK-jevi dvije tabele isti i ne mijenjati FK samo preimenovanjem modela. Nepovezane lokacije zahtijevaju pregled prije aktivacije. Stare tabele ne brisati. Migracije se ne izvršavaju automatski.

## Obavezne provjere

Originalne količine ostaju jednake prije/poslije prijelaza; stari /nalog/magacin ostaje funkcionalan. Testirati oba interfejsa, varijacije, manual/web narudžbe, rezervacije, djelimično/zero odvajanje, alternative, očisti/ostavi, prenos bez dvostrukog izvršavanja, istoriju, štampu i zaključavanje. Testirati konkurentne rezervacije na izolovanom PostgreSQL-u. Nema produkcijskog pristupa, pravih emailova ili plaćanja.

## Trenutni status

Korisnik je potvrdio da WMS treba biti glavni interfejs na originalnom lageru. Implementiran je direktni režim i pripremljena migracija 0315. Testirano na odvojenim sintetičkim kopijama baze. Migracija nije primijenjena na projektnu ili produkcijsku bazu; potrebna je prije aktivacije. Detalji u CANONICAL_WMS.md. Produkcijski lager nije diran.
