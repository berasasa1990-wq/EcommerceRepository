# Preostale WMS optimizacije

## Implementacija

- `wms_customer_query.py`: tri izvora kupaca spajaju se preko UNION ALL;
  window funkcija bira prvi zapis po normalizovanom imenu i telefonu uz
  postojeći prioritet WMS → webshop → ručna narudžba. Pretraga se primjenjuje
  prije izbora pobjednika, kao ranije. Soft-deleted identiteti isključuju se
  u SQL-u. SELECT vraća samo kontaktne kolone i najviše 30 redova preko
  LIMIT/OFFSET, uz COUNT OVER za ukupan broj rezultata. Nema učitavanja svih
  ORM modela. Izuzetni brojevi stranice zadržavaju ponašanje Paginator.get_page.
- `views_wms.py`: koristi novi directory query. Provjera webshop dokumenata
  odvija se prije izrade sidebar konteksta; više nema dvostrukog brojanja
  pending narudžbi. Pravila kreiranja dokumenata nisu mijenjana.
- `wms_orders.py`: nedostajući dokumenti koriste iterator(chunk_size=200)
  i samo sedam potrebnih Order kolona. Get_or_create i OneToOne zaštita od
  duplih dokumenata ostaju; provjera nije cacheirana.
- `location_detail.html`: odredišne opcije nalaze se jednom u inertnom
  templateu, a kopiraju se u select tek prilikom otvaranja popupa. Svaka
  lokacija ostaje dostupna, već izabrana vrijednost ostaje pri ponovnom
  otvaranju, opcije se ne dupliraju. View čita samo ID/naziv odredišta.
  Dizajn i transfer forma ostaju isti. Nema potrebe za novim API pozivom
  za svako otvaranje; na zahtjev pretraga lokacija nije uvedena.

SQL i dalje mora obraditi odgovarajuće identitete u bazi da bi tačno spojio
izvore i izračunao ukupan broj. Ovo nije tvrdnja da se baza dotiče samo 30
redova. Nije cacheiran lager, rezervacije ili korisnički podaci.

## Mjerenja

Offline sintetički SQLite; rollback svih fixture promjena, bez produkcijske
baze. Script `wms_customer_regression.py` poredi stari algoritam i novi upit
na identičnim podacima sa 1.200 dodatnih kupaca.

| Metrika kupaca | Prije | Poslije |
| --- | --- | --- |
| SQL upiti | 4 | 1 |
| Python peak sa tracemalloc | 1.355.139 B | 45.240 B |
| Vrijeme sa uključenim tracemalloc | 50,663 ms | 23,601 ms |
| JSON veličina | 3.549 B | 3.549 B |
| Vraćeni redovi | 30 | 30 |

Mjerenje bez tracemalloc u ranijem prolazu bilo je 10,647 → 14,678 ms:
novi SQL je u tom SQLite fixtureu bio sporiji, iako znatno manje opterećuje
Python memoriju i smanjuje broj SQL roundtripova. Tracemalloc dodatno usporava
stari algoritam zbog broja alokacija. Zato se ne tvrdi garantovano smanjenje
latencije na PostgreSQL-u. Sirovi heap rezultati:
`wms-customers-measurements.json`.

Prethodni profil `wms-profiler/after.json` i novi `remaining-after.json`
koriste isti mali fixture/profiler:
- Narudžbe: 11 → 10 SQL; 28,411 → 31,471 ms. Lokalno vrijeme varira;
  potvrđeno je uklanjanje jednog ponovljenog count upita.
- Lokacija: HTML 49.255 → 48.015 B; statički tagovi 617 → 584;
  server 28,929 → 27,006 ms. Pravi efekat dijeljenja opcija raste sa brojem
  lokacija i redova. Live DOM i vrijeme izvršavanja mobilnog JS nisu mjereni.

## Provjere

Prolaze:
- Novi regression script: potpuna jednakost sa starim algoritmom za praznu
  pretragu, ime, telefon, grad, Unicode imena, stranice 1/2/izvan raspona,
  neispravan broj i 0, te soft-deleted identitet preko izvora.
- `test_wms_location_options.cjs`: sva odredišta prenesena u select, kopiranje
  samo pri prvom otvaranju i bez dupliranja na ponovnom otvaranju.
- `wms_performance_regression.py`, canonical stock checks i canonical WMS
  regresija: lager, više lokacija, barkod, transfer, rezervacije, odvajanje,
  webshop/ručna narudžba bez dvostrukog skidanja, štampa i dozvole.
- `manage.py check` i `git diff --check`.

## PostgreSQL i spremnost za Render

CTE/UNION ALL/ROW_NUMBER/COUNT OVER/LIMIT/OFFSET koriste PostgreSQL podržanu
sintaksu. MATERIALIZED zahtijeva PostgreSQL 12+. SQLite funkcije za Unicode
normalizaciju koriste Python strip/lower; PostgreSQL koristi LOWER/BTRIM sa
whitespace skupom i C collation za sortiranje. Locale/Unicode razlike
PostgreSQL-a moraju se dodatno provjeriti na izolovanoj bazi.

Nije rađen stvarni PostgreSQL execution plan, SQL parity ili konkurentni
test. Prije Render deploya treba to izvršiti na izolovanom PostgreSQL-u sa
reprezentativnim imenima, duplikatima, velikom historijom i soft-deleted kupcima.
Kod zato nije označen kao potpuno spreman za produkcijski deploy.
Nema dokaza za nove indekse iz PostgreSQL planova: nisu dodani indeksi niti
migracije. Render/Cloudflare/Git konfiguracija nije mijenjana.

Preostalo: liste lokacija/postavke i shortage popup i dalje mogu imati mnogo
zapisa; dalje parcijalno učitavanje zahtijeva zaseban UI tok. Mobilna vizuelna
provjera nije izvršena. Kontakti sa jednakim vremenom narudžbe sada imaju
deterministički pk tie-breaker; prethodna SQL naredba nije definisala taj red.
Nema brisanja podataka, promjene obračuna, pusha ili deploya.

## Izmijenjeni fajlovi ove faze

`EcommerceApp/wms_customer_query.py`, `views_wms.py`, `wms_orders.py`,
`template/bera_reference/staff/wms/location_detail.html`,
`scripts/wms_customer_regression.py`, `scripts/test_wms_location_options.cjs`,
ovaj izvještaj i dva JSON mjerenja navedena iznad.
