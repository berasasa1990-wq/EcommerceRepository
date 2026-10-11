# Faza A — lokalna dijagnostika

Mjerenje: Django test Client, izolovana sintetička SQLite baza `/private/tmp/bera-wms-qa.sqlite3`, tri zahtjeva po putanji, medijana vremena i broj upita posljednjeg zahtjeva. Sve promjene testnih podataka rollback; vanjska mreža blokirana, locmem email. Produkcijska baza, Render metrike, PostgreSQL EXPLAIN/pg_stat_statements i R2 nisu korišteni. Rezultati nisu procjena produkcijskog TTFB-a. Nisu izmjereni LCP/INP/CLS, RSS ili pravi PostgreSQL planovi.

| Putanja | SQL | Schema SQL | Medijana ms |
|---|---:|---:|---:|
| `/wms/` | 48 | 23 | 10.82 |
| `/wms/zalihe/?q=QA` | 67 | 23 | 15.28 |
| `/wms/narudzbe/` | 52 | 24 | 11.73 |
| `/wms/pakovanje/` | 52 | 24 | 11.62 |
| `/wms/lokacije/` | 49 | 23 | 11.44 |
| `/wms/kupci/` | 64 | 31 | 13.81 |
| `/wms/istorija/` | 48 | 23 | 10.4 |
| `/wms/narudzbe/artikli/?q=QA` | 17 | 8 | 3.9 |
| `/wms/narudzbe/kupci/?q=QA` | 19 | 8 | 3.63 |
| `/` | 100 | 3 | 55.97 |
| `/?q=QA` | 20 | 2 | 19.41 |
| `/artikal/qa-artikal-8/` | 26 | 2 | 18.88 |
| `/korpa/` | 16 | 2 | 7.92 |
| `/nalog/` | 132 | 2 | 54.5 |
| `/panel` | 51 | 23 | 11.11 |
| `/admin/` | 13 | 4 | 4.76 |

## Potvrđena uska grla

- `wms_legacy.warehouse_compatible`: svaki autorizovan WMS zahtjev otvara transakciju, učitava sve WarehouseStock redove i osvježava WMS ogledalo. To radi i JSON poll dozvola svakih 5 sekundi (`staff/wms/base.html`). Trošak raste s cijelim lagerom, nezavisno od veličine rezultata. Potencijalno visoki CPU/DB/locking trošak; ne dokaz 502.
- `wms_legacy.import_legacy_stock`: po lokaciji jedan SELECT za mapiranje, pa prolaz kroz sve postojeće WMSStock. Sigurna optimizacija: učitati postojeća mapiranja jednim upitom, sačuvati isti postupak kreiranja nedostajućih lokacija. Visok uticaj kada ima mnogo lokacija; provjeriti WMS regresiju.
- `bera_panel_modules.module_locked`: svaka provjera ponavlja introspekciju i EXISTS. Panel i WMS ih pozivaju iz viewa, contexta i template tagova. Sigurno ponovo koristiti samo unutar GET zahtjeva; ne između korisnika ili zahtjeva. Admin izmjene moraju odmah važiti u narednom zahtjevu.
- `bera_home.storefront_context` i `homepage_context`: ponovljena introspekcija, čitanje JSON datoteke i provjera opcionalnih tabela. Zadržati default ponašanje ako tabele nedostaju.
- `views_wms.pick_order`, `pending_shortages`, `print_orders`: select/prefetch pokriva legacy item, a često ne pokriva wms_item; `picking_item` onda izaziva N+1. U štampi se stavke narudžbe dodatno učitavaju za svaku narudžbu.
- `views_wms.workspace`: liste narudžbi/prenosnica nisu paginirane; `order_customers` učitava i sortira historijske kupce u Pythonu prije paginacije. Izmjena upita mora sačuvati deduplikaciju i prikaz postojećih kupaca. Za ovaj veći zahvat potrebna dodatna funkcionalna provjera prije implementacije.
- `context_processors.nav_categories`: WMS i panel nisu light paths pa dobijaju storefront marketing context. Ne mijenjati generički context bez provjere skrivenih popupova i funkcija.

## Ostali pregledani dijelovi

Postojeći cart batching i `_magacin_stock_picks` već imaju performance testove; ne zamjenjivati njihove obračune. Početna ima 100 SQL na sintetičkoj bazi, nalog 132; potrebna dodatna analiza pojedinačnih SQL poziva prije agresivnih izmjena. Pretraga 20, product 26, korpa 16 na ovom fixtureu. Middleware live visitor/meta može evidentirati aktivnosti i ima integracijski trošak; testno su mrežni pozivi isključeni, ne isključivati funkcije u produkciji.

Render manifest: 1 worker/4 threads/60s timeout; ne povećavati workere. Nemamo RSS/CPU/worker exit/DB konekcije logove pa uzrok prethodne 502 nije dokazan. Sumnje: puni WMS sync tokom polling zahtjeva, dugi SQL/vanjski pozivi, memorija velikih lista. Ne mijenjati produkcijske env ili manifest. Potrebni Render logovi i PostgreSQL read-only planovi koje vlasnik može pribaviti.

## Plan sigurnih izmjena (Faza B)

1. Request-local memoizacija schema/GET module checks (bez cachea privatnih odgovora ili korisničkih podataka između zahtjeva).
2. Permission-only polling ne sinhronizuje lager i ne ulazi u write transakciju.
3. Batched postojeća mapiranja lokacija; isti fallback za nove lokacije.
4. Eager loading oba tipa picking stavki i njihovih artikala, posebno štampa.
5. Testovi prije/poslije i WMS funkcionalna regresija.

Rizične promjene nisu odobrene: preskakanje punog synca normalnih WMS zahtjeva, izmjena modela/indeksa, sinhronizacija u pozadinskim procesima, DB konekcije, caching privatnih stranica. Novi indeksi zahtijevaju PostgreSQL planove i potvrdu; ništa nije migrirano.
