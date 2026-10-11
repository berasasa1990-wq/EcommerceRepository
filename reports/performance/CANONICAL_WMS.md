# Direktni WMS — lokalna implementacija

WMS koristi Product, WarehouseLocation i WarehouseStock, isti lager kao /nalog/magacin/. Nema import_legacy_stock/apply_wms_stock poziva iz WMS requesta, panel +/- niti Excel importa. Stare mirror tabele su sačuvane, ali nisu aktivni izvor zaliha. Stari magacin nije izmijenjen.

## Šta je promijenjeno

views_wms.py: direktna pretraga i lokacije; prenos i manual odvajanje preko apply_movement; webshop odvajanje preko validate_order_stock u vanjskoj transakciji; zaštita rezervacija prije prenosa i čišćenja; varijacija prenosa; izbori lokacija iz originalne tabele. wms_legacy.py: uklonjen full sync iz wrappera, POST transaction i legacy fulfillment ostaju; pozitivna provjera spremnosti šeme pamti se po bazi/procesu (samo schema metadata, dozvole i količine nisu cacheirane).

bera_panel_stock.py: originalne lokacije i postojeći stock servisi, bez synca. wms_excel.py: originalni lager kroz ledger i provjera rezervacija, podržan prethodni format datoteke. Excel nema izbor varijacije pa odbija stock unos na artiklima s varijacijama umjesto pogrešnog raspoređivanja; ta zaštita je postojala i u starom bridgeu. Upis istorije dobija prijavljenog korisnika.

models.py i 0315_wms_canonical_warehouse.py: originalni FK-ovi za picking, transfer i settings. Arhivirani FK-ovi ostaju sačuvani; novi FK-ovi se popunjavaju preko postojećeg legacy_location_id. Originalne stock količine i rezervacije migracija ne mijenja. Varijacije su sačuvane u novim snapshot stavkama za zero-picked istoriju. Template stock_search/location_transfer_row/workspace/location_barcode_print prikazuje originalne lokacije, šifre i stock redove.

## Provjere

- Django check, diff check i makemigrations --check --dry-run prolaze.
- scripts/carp_canonical_wms_regression.py: prenos, ponovljena potvrda, manual/web odvajanje, bez dvostrukog skidanja, blokada rezervisanog prenosa, računi, lokacija/barkod, svih 7 sekcija, stari magacin, prava pristupa.
- scripts/carp_canonical_stock_checks.py: nema čitanja/upisa ProductWMSStock ili sync poziva, originalna promjena odmah vidljiva, prenos varijacije, backfill stare istorije bez promjene originalnog lagera.
- Migracija testirana na kopiji sintetičke QA baze; dodatno nepovezana istorijska lokacija blokira backfill umjesto nagađanja.
- Raw benchmark canonical-after.json: pretraga zaliha 11 upita naspram početnih 67 (nakon prvih optimizacija bilo je 37). Testni fixture je mali SQLite, nije PostgreSQL produkcijski benchmark niti obećanje vremena. Nakon warmupa schema introspekcija više ne radi višestruke FK provjere.
- Nema stvarnih emailova, plaćanja, R2 ili produkcijskog DB pristupa. Testovi sekvencijalni i sve stock promjene u testovima rollback.

## Aktivacija i preostale provjere

Migracija 0315 NIJE izvršena na projektnoj/produkcijskoj bazi. Dok nije izvršena, WMS vraća jasnu 503 poruku, a stari magacin ostaje dostupan. Ne objavljivati samo Python promjene bez planirane migracije. Prije aktivacije: backup, zasebna testna PostgreSQL baza, preflight svih WMS lokacija koje koristi picking/prenos/settings, provjera konkurentnog komisioniranja i browser smoke.

Migracija se atomarno zaustavlja ako istorijski WMS location nije povezan s originalnim WarehouseLocation. Ne kreira lokacije naslijepo niti kopira količine; nepovezane lokacije treba eksplicitno mapirati. Starim pending prenosnicama varijacija nije bila zapisana; pre potvrde prenosa s varijacijama potrebno je pregledati te dokumente. Arhivirani FK-ovi omogućavaju očuvanje prethodne istorije, ali rollback novih dokumenata na stari softver zahtijeva poseban pregled i nije automatski poslovni rollback.

Nije urađen commit, push, deploy ili promjena environmenta. Lokacije bez naziv-a imaju originalnu šifru i korisnički label; novi barcode print koristi originalnu šifru lokacije, ne mirror PK. Postojeće odštampane WMSLOC etikete treba pregledati prije zamjene na skladištu.
