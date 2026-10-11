# Faza B — implementacija i rezultati

## Rezultati na istom izolovanom sintetičkom SQLite fixtureu

Tri uzorka, medijana vremena. Nisu produkcijska mjerenja. Vrijeme varira s lokalnim CPU/opterećenjem, template/cache warmupom i nasumičnim izborom početnih artikala. SQL broj je jači dokaz od malih razlika u vremenu. Product/admin nisu izmjereno ubrzani; ne predstavljamo ih kao ubrzanje.

| Putanja | SQL prije → poslije | Medijana ms prije → poslije |
|---|---:|---:|
| `/wms/` | 48 → 18 | 10.82 → 7.49 |
| `/wms/zalihe/?q=QA` | 67 → 37 | 15.28 → 11.56 |
| `/wms/narudzbe/` | 52 → 21 | 11.73 → 8.32 |
| `/wms/pakovanje/` | 52 → 21 | 11.62 → 7.93 |
| `/wms/lokacije/` | 49 → 19 | 11.44 → 9.41 |
| `/wms/kupci/` | 64 → 31 | 13.81 → 8.69 |
| `/wms/istorija/` | 48 → 18 | 10.4 → 7.08 |
| `/wms/narudzbe/artikli/?q=QA` | 17 → 16 | 3.9 → 3.72 |
| `/wms/narudzbe/kupci/?q=QA` | 19 → 18 | 3.63 → 4.03 |
| `/` | 100 → 25 | 55.97 → 24.54 |
| `/?q=QA` | 20 → 19 | 19.41 → 17.55 |
| `/artikal/qa-artikal-8/` | 26 → 25 | 18.88 → 19.99 |
| `/korpa/` | 16 → 15 | 7.92 → 7.91 |
| `/nalog/` | 132 → 22 | 54.5 → 16.14 |
| `/panel` | 51 → 8 | 11.11 → 5.72 |
| `/admin/` | 13 → 13 | 4.76 → 5.69 |

## Izmijenjeni fajlovi

- `EcommerceApp/request_performance.py` (novi), `EcommerceProject/settings.py`: middleware i ContextVar čuvaju ponovljene GET/HEAD read rezultate samo do kraja jednog zahtjeva. Nema cacheiranja privatnih HTML odgovora, sesija ili podataka između korisnika. POST ne memoizuje; token se resetuje i pri izuzetku.
- `EcommerceApp/bera_panel_modules.py`: jedan schema SELECT + jedan dohvat modula po GET umjesto ponovljenih EXISTS. Novi zahtjev vidi promjene dozvola.
- `EcommerceApp/bera_home.py`: ponovo koristi schema introspekciju iz istog zahtjeva.
- `EcommerceApp/wms_legacy.py`: postojeća mapiranja lokacija dohvaća jednim upitom; zadržava prvi PK kod duplih mapiranja i originalni fallback kreiranja. Permission-only GET polling preskače sync i write transakciju, uz postojeće auth/module/schema provjere. Normalni WMS zahtjevi i dalje sinhronizuju lager.
- `EcommerceApp/models.py`: flash-sale relation read memoizovan po artiklu unutar GET; formula, prioritet i provjera trajanja ponude nisu izmijenjeni. U početnoj pronađeni identični SELECT-i ponavljani 12–19 puta po artiklu.
- `EcommerceApp/views_wms.py`: eager-load oba tipa picking stavke; štampa prefetcha stavke i manual/legacy picking reference. Paginacija narudžbi i prenosnica 50 redova, stabilan sort, sačuvani GET filteri.
- `EcommerceApp/template/bera_reference/staff/wms/workspace.html`, `prenosnica.html`, novi `pagination.html`: postojeće tabele/stilovi i navigacija stranica. Lazy/async slike u zatvorenom popupu nedostataka.
- `scripts/carp_performance_measure.py`, `scripts/carp_performance_regression.py`: izolovana mjerenja i ciljane regresije.
- `reports/performance/`: dijagnostika, raw before/after JSON, ovaj izvještaj.

## Provjere

`manage.py check` i `git diff --check` prolaze. Postojeći WMS regresijski script prolazi: prikaz legacy lagera, prenos bez dvostrukog prenosa, manual/web odvajanje i skidanje lagera bez dupliranja, blokada prenosa rezervisanog lagera, računi/prikaz lokacija/barkod, autorizacija. Nova regresija prolazi: GET reuse, osvježavanje dozvola sljedećim zahtjevom, neposredna POST promjena, polling bez stock synca, identične prikazne cijene, paginacija 51 narudžbe, anoniman pristup.

Postojeći `RequestPerformanceTests` pokušani, ali blokirani šemom QA fixturea: dodatna NOT NULL kolona `Product.pracenje_zaliha` nije dio ovog modela. Nije aplikacijski assertion failure; nije popravljen fixture niti migrirana baza. Prvo istovremeno pokretanje dva QA DB procesa izazvalo je SQLite lock; ponovljeno sekvencijalno, WMS regresija prošla. Finalna mjerenja rađena sekvencijalno. Sve writes tokom QA rollback, nema pravih emailova/plaćanja/vanjske mreže.

## Preostalo i rizici

- Normalni WMS request i dalje skenira kompletan legacy lager. Najveći preostali rizik za veliki webshop; prelazak na scoped/event sync mijenja mehanizam svježine lagera i zahtijeva izričitu potvrdu, detaljnu specifikaciju i test konkurentnih rezervacija.
- `order_customers` i dalje deduplikuje historijske kupce u Pythonu. Za SQL zamjenu potrebna provjera semantike brisanja, historijskih brojeva i sortiranja.
- Liste lokacija/detaila i izbor računa mogu biti velike. Veća promjena UI/paginacije ovih specifičnih izbora zahtijeva test očuvanja multi-select/štampe.
- PostgreSQL indeksi nisu dodani. Kandidati za EXPLAIN: WMSOrder(status, kreirana), completed_at, WMSPickLine potvrda/datumi i WarehouseMovement sortiranje po kreiran. FK indeksi već postoje automatski. Datum lookup često pretvara timestamp pa običan indeks može biti nedovoljan; ne dodavati naslijepo. Potrebni planovi i odobrenje.
- Render 1 worker/4 threads ostavljen; PostgreSQL config već koristi conn_max_age=600 i conn_health_checks=True. RSS, stvarni broj konekcija i 502 uzrok nisu poznati. Ne tvrditi da je riješena 502.
- Frontend base već koristi deferred skripte, preload lokalnih Inter fontova, responsive banner srcset/sizes i lazy slike. WMS nasljeđuje veliku storefront bazu; uklanjanje shared asseta može prekinuti popupove/forme pa nije učinjeno naslijepo. Nisu urađena browser/RUM Core Web Vitals mjerenja ni vizuelna regresija.
- Analitika i signal side effects pregledani; postojeće evidentiranje posjeta i refresh zavisnih setova nisu isključeni. Nisu mijenjani Monri, PDV, popusti, sigurnost, env, produkcijski DB/R2 ili deployment.

## Spremnost za produkciju

Sigurne lokalne optimizacije i WMS funkcionalna regresija prolaze. Nije potvrđena potpuna produkcijska spremnost: prije objave potrebni čista testna PostgreSQL šema, puni relevantni testovi (uključujući flash-sale cases), realističan volumen, konkurentni stock test i browser smoke provjera. Nema migracija, novih indeksa, commita, staginga, pusha, mergea ili deploya u ovom zadatku.

Ponoviti mjerenja: `PYTHONPATH=scripts:. DJANGO_SETTINGS_MODULE=bera_wms_qa_settings venv/bin/python scripts/carp_performance_measure.py /private/tmp/performance.json`. Script odbija bilo koju drugu bazu.
