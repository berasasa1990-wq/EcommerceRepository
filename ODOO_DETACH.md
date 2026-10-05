# Odvajanje Odoo-a bez promjene lokalnih podataka

Django/Render baza i postojeći Magacin lager su source of truth. Ovaj change
ne dodaje migracije, data migrations, import, sync, cleanup, stock refresh ili
rebuild. Historijske Odoo reference, lokacijske putanje, sync logovi i stare
sesije ostaju sačuvani. Lokalne putanje koriste ove sačuvane vrijednosti samo
kao lokalne podatke radi očuvanja postojećeg obuhvata/klasifikacije Magacina.

Uklonjeni su XML-RPC klijent, importer, sale.order servis, sync komanda,
admin import, staff slanje u Odoo, Odoo skidanje lagera i picking fallback.
Stari `/nalog/magacin/sync/` vraća 404; stari POST za Odoo narudžbu/skidanje
ne pokreće tu operaciju. Historija sync-a ostaje dostupna samo za čitanje.
Stare sync sesije i logovi se ne nastavljaju niti prepisuju.

## READ ONLY kontrola produkcije

```sh
python manage.py audit_odoo_detach > /var/data/odoo-detach-before.json
python manage.py audit_odoo_detach --compare /var/data/odoo-detach-before.json > /var/data/odoo-detach-after.json
```

Prvu komandu izvršiti na postojećoj produkcijskoj bazi prije deploya, drugu na
istoj bazi poslije deploya. Audit modul `EcommerceApp/warehouse_audit.py` i
management command mogu se zasebno staviti u postojeći checkout prije deploya;
oni rade i sa kodom prije odvajanja. Ako prije-snapshot nije napravljen,
kasniji fingerprint nije dokaz ranijeg stanja.

Između snapshotova privremeno zaustaviti operativne promjene (checkout,
picking, popis, unos/izmjene artikala, lokacija i rezervacija). Audit sam ne
zaustavlja poslovni promet. Svaka legitimna nova narudžba ili promjena
podataka također mijenja fingerprint; dozvoljena razlika za ovu provjeru je 0.
Čuvati prije-JSON izvan deployment filesystema. Shell redirekcija piše samo
izvještaj, management command ne piše u bazu niti fajlove.

PostgreSQL koristi jednu REPEATABLE READ, READ ONLY transakciju. SQLite
koristi konzistentnu DEFERRED transakciju i `PRAGMA query_only`; prethodne
postavke konekcije se vraćaju. Nepodržana baza, nedostajuća tabela/kolona ili
greška čitanja završava neuspjehom, nikada djelimičnim "uspješnim" auditom.

SHA-256 uključuje sve konkretne kolone relevantnih modela, poredane po
primarnom ključu, i M2M vezne tabele. Obuhvata Product, ProductVariation,
ProductImage, kategorije/brendove/tagove, Order/OrderItem/OrderStockHold,
Warehouse*/Magacin*/B2B*, uvoz, nivelacije, draftove, barkod konflikte,
CardPayment i Mungos mapiranja. Svaka tabela ima zaseban broj redova i hash.
Ne čita sadržaj slikovnih fajlova; čuva/provjerava postojeće putanje u bazi.
Izvještaj sadrži zbirne količine i rezervacije, količine po proizvodu/varijanti
odnosno lokaciji, sve postojeće holdove i dostupnost iz neizmijenjene
`Cart.availability()` logike. Nema čišćenja ili nagađanja o stale holdovima.

`--compare` ispisuje promijenjene tabele i dostupnost; svaka razlika vraća
nonzero exit. Fingerprint ne uključuje vrijeme izvršenja, environment,
verziju koda ili naziv/host baze. Mora se porediti ista baza i isti audit
schema `local-warehouse-v1`. JSON ne izlaže sirove kupce, ključeve ili cijene;
ti podaci su uključeni u hash svojih tabela.

## Environment i postojeće integracije

`ODOO_URL`, `ODOO_DB`, `ODOO_USERNAME`, `ODOO_API_KEY` nisu settings niti
runtime dependencies. Nakon deploya ukloniti ih iz Render Environment i
isključiti eventualni vanjski cron koji poziva staru `sync_odoo_magacin`
komandu (komanda više ne postoji). Lokalni privatni `.env` nije mijenjan.
Checkout, COD, Monri finalizacija, XExpress, Mungos, loyalty i analytics
nastavljaju koristiti svoje postojeće lokalne tokove i konfiguraciju.

Audit ne kontaktira Odoo i ne dokazuje kompletnost podataka koji postoje samo
u Odoo. Odoo database/account nisu promijenjeni niti obrisani. Prije trajnog
brisanja sačuvati zaseban kompletan Odoo database backup sa filestoreom i
potrebne poslovne dokumente/historiju. Ukidanje zavisnosti webshopa nije
dokaz da su svi ostali Odoo poslovni podaci preneseni.
