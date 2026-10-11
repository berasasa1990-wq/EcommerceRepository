# Popravak 0315: PostgreSQL konflikt FK indeksa

## Dijagnoza

Korisnik je potvrdio produkcijsko početno stanje: 0314 applied, 0315 unapplied,
indeks `EcommerceApp_bera_wmspickline_location_id_8247fec8` pripada tabeli
WMSPickLine i koloni location_id. To je upravo stanje koje patch očekuje
prije RenameField; poslije preimenovanja provjerava archived_location_id.
Produkcijska baza nije kontaktirana za ovu potvrdu.

Django RenameField generiše ALTER TABLE ... RENAME COLUMN, dok PostgreSQL
zadržava ime običnog indeksa te kolone. AddField za ponovo uvedeno staro ime
registruje CREATE INDEX sa istim automatski generisanim nazivom. Kreiranje
indeksa može se izvršiti tek pri izlasku iz schema_editor, kao deferred SQL.
To objašnjava grešku iako je kolona već dobila arhivsko ime.

Provjeren lokalni Django backend: base/schema.py _alter_field,
_field_indexes_sql, _create_index_name; PostgreSQL koristi isti generator.
Offline PostgreSQL generator proizvodi tačno prijavljeno ime
EcommerceApp_bera_wmspickline_location_id_8247fec8.

Isti rizik imaju:
- WMSPickLine.location → archived_location → novi location.
- WMSTransfer.source → archived_source → novi source.
- WMSTransfer.destination → archived_destination → novi destination.
- WMSSettings.transfer_location → archived_transfer_location → novi transfer_location.
- WMSSettings.picking_last_location → archived_picking_last_location → novi picking_last_location.

WMSOrderItem.varijacija i WMSTransfer.variation ne ponavljaju preimenovana
imena i nemaju ovaj konflikt. AlterField arhivskih kolona samo dozvoljava
NULL; uklanjanje/dodavanje unique pick constrainta ostaje postojeće.

## Minimalni patch

U samoj 0315 pet RenameField operacija zamijenjeno je podklasom
RenameFKWithRetainedIndex. Django state i preimenovanje kolone ostaju isti.
Poslije kolone provjerava se indeks pod originalnim automatskim imenom:
običan ne-unique/non-PK indeks na tačno arhivskoj koloni. Ako postoji,
preimenuje se u arhivsko ime dobijeno istim Django generatorom.

Preimenovanje je schema-qualified, prema namespace-u stvarne tabele.
Target naziv provjerava se u pg_class, uključujući druge tipove relacija.
Neočekivani indeks ili zauzet target izaziva RuntimeError i atomic rollback.
Ako stari indeks ne postoji, ništa se ne briše niti kreira naslijepo.
Custom imena indeksa ostaju ista. Reverse rename ima istu zaštitu; reverse
podatkovnog mapiranja i dalje nije obećanje da je povratak poslovno siguran.

Nema promjene map_locations, skladišnih tabela, količina, rezervacija ili
logike narudžbi. Indeks se ne rebuilda i ne briše. PostgreSQL ALTER INDEX
uzima kratkotrajni DDL lock; migracija ipak uključuje ostale postojeće DDL
operacije i backfill, pa treba planirati prozor slabijeg prometa.

## Provjere i granice

- Offline name generator: svih pet parova u 0315-index-renames.sql.
  Ovo je referentni SQL, ne skripta za ručno izvršavanje: patch provjerava
  stanje i namespace prije izvršavanja.
- test_0315_index_guard.py: rename, nedostajući indeks/noop, pogrešna kolona
  i zauzet target/refusal; prolazi.
- Izolovana SQLite kopija: 0314 → patched 0315 → 0316 prolazi, stock i
  rezervacije ostaju isti. PostgreSQL grana se tu ne izvršava.
- manage.py check i git diff --check prolaze.
- Nije izvršen stvarni PostgreSQL SQL migration test: dostupni su libpq
  klijentski alati, ali postgres server i docker nisu pronađeni.

scripts/test_0315_postgresql.py je pripremljen za eksplicitno zadanu lokalnu
PostgreSQL test_* bazu. Kreira vlastiti nasumični schema, istorijske tabele
0314 potrebne za WMS i povezane modele, podatke/indekse, izvršava cijelu
0315 i provjerava svih pet arhivskih/canonical veza i indeksa te lager.
U finally uklanja samo svoj schema. Ne učitava .env ili produkcijski URL.
Za pokretanje:

PostgreSQL test dodatno provjerava atomic rollback ako referencirana stara
lokacija nema mapiranje, zatim uspješno mapiranje svih pick redova i cijeli
lanac do 0316. Test je pripremljen, ali i dalje nije izvršen na PostgreSQL-u.

    PYTHONPATH=scripts:. DJANGO_SETTINGS_MODULE=canonical_qa_settings \
    WMS_MIGRATION_TEST_DATABASE_URL='<URL lokalne izolovane test_* baze>' \
    venv/bin/python scripts/test_0315_postgresql.py

Ne stavljati stvarne credentials u izvještaj ili Git. Preferirati sigurno
postavljanje varijable u shellu. Cijeli PostgreSQL test tek treba izvršiti.

## 0314 / 0316 i postojeća baza

Zavisnosti nisu mijenjane: 0314 → 0315 → 0316. Model state nakon 0315 ostaje
identičan. 0316 CreateOrAdoptHomeBestseller ne zavisi od imena ovih indeksa.
Ako je 0315 već uspješno primijenjena, Django neće ponovo izvršiti patch;
ne vraćati migraciju samo zbog ovog popravka.

0315 je po defaultu atomic. Kod normalnog PostgreSQL neuspjeha njene DDL
promjene i zapis migracije trebalo bi da budu rollbackovani. To treba
provjeriti na stvarnoj bazi, ne pretpostaviti nakon ručnog SQL-a/importa.
Ako je baza djelimično izmijenjena mimo migracije, zaustaviti i pregledati
kolone/indekse/migration history; ne koristiti --fake.

## Sigurno objavljivanje na Renderu (ručno)

1. Napraviti backup/snapshot i provjeriti da može biti vraćen.
2. Testirati patch na izolovanom PostgreSQL-u sa reprezentativnom 0314
   kopijom WMS podataka, uključujući postojeće indekse i mapiranje lokacija.
3. Read-only provjeriti showmigrations EcommerceApp: potvrditi 0314 applied,
   0315 unapplied i pregledati stvarne kolone/indekse; na testnoj kopiji
   pregledati sqlmigrate EcommerceApp 0315 i migrate --plan.
4. Objaviti popravljenu 0315 uz kontrolisani deploy/prozor održavanja.
   Migracije pokrenuti samo jednom postojećim kontrolisanim deploy procesom
   ili ručnim migrate, bez paralelnog pokretanja i bez --fake.
5. Provjeriti da su 0315/0316 applied, WMS lokacije i stare/arhivske veze
   ispravne, te da se ukupne količine/rezervacije podudaraju s backupom.
6. Ako provjera stanja nije očekivana, zaustaviti; ne brisati indekse ručno.

Agent nije radio produkcijske migracije, commit, push ili deploy.

## Fajlovi

Obavezan runtime patch: EcommerceApp/migrations/0315_wms_canonical_warehouse.py.
Testovi: scripts/test_0315_index_guard.py, scripts/test_0315_postgresql.py.
Dokumentacija: ovaj fajl i 0315-index-renames.sql.
0314 i 0316 nije potrebno mijenjati za ovaj konflikt.
