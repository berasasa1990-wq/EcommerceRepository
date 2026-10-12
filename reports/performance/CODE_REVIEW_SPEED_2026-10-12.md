# Pregled koda i sigurne optimizacije — 12.10.2026.

## Opseg i zaštita postojećeg rada

Pregledani su glavni tokovi početne, kategorija, pretrage, korpe, checkouta,
naloga i WMS-a, zajednički context/cache, SQL prefetch i Python sortiranje,
aktivne skripte, postavke baze i Render blueprint. Sintaksno je parsiran
251 Python fajl iz EcommerceApp (bez migracija); nema syntax grešaka.
Ovo nije potvrda svake poslovne grane niti svih produkcijskih performansi.

Prije rada git status već je sadržao izmjene poravnanja headera, kategorije
Mašinice i fragment odgovora. One su sačuvane. Nema git add, push,
deploymenta, produkcijskih migracija ni promjene lagera/cijena/narudžbi.
Django testovi koriste scripts.bera_home_qa_settings i izolovanu SQLite bazu;
vanjske integracije su isključene.

## Potvrđena usporavanja i nove izmjene

### Najprodavaniji na početnoj

bera_home.homepage_context je učitavao pune Product objekte, opise i povezane
podatke za sve odabrane najprodavanije, a tek zatim izdvaja šest. Nova pomoćna
funkcija prvo čita vidljive ID-jeve, bira prvih šest u originalnom redoslijedu,
pa samo za njih učitava pune kartice. Sačuvani su dupli unosi, neaktivnost,
dostupnost i pravilo sakriven_do_stanja; nema dijeljenog cachea HTML-a.

Kontrolisani fixture: 80 dostupnih proizvoda s opisima od oko 20.000 znakova.

| Mjera | Prije | Poslije |
| --- | ---: | ---: |
| Puni Product objekti | 80 | 6 |
| SQL upiti | 3 | 4 |
| Lokalno vrijeme jednog uzorka | 8,29 ms | 3,18 ms |

Jedan dodatni ID upit zamjenjuje nepotrebno učitavanje 74 pune kartice.
Vremena su pojedinačni lokalni SQLite uzorci, ne produkcijski TTFB ili garancija.

### Osvježavanje lagera u korpi i checkoutu

cart-stock.js je za svaki artikal ponovo tražio i prolazio kroz sve DOM
redove korpe. Sada pravi indeks redova po ključu jednom po odgovoru.
Više redova s istim ključem i dalje se obrađuje. Interval, dostupnost,
quantity/max, blokiranje rasprodatih artikala i checkout kontrola ostaju isti.
Izmijenjene su obje kopije skripte; aktivni cart/checkout templatei imaju novu
verziju asseta.

Offline regresija, 40 artikala:

| Mjera | Prije | Poslije |
| --- | ---: | ---: |
| document.querySelectorAll za redove | 40 | 1 |
| Čitanje ključeva redova | 1.600 | 40 |
| Krajnje oznake lagera, quantity/max i kontrole | baseline | identične |

### Ranije pripremljena optimizacija

„Prikaži više” koristi fragment HTML-a umjesto kompletne stranice.
Regresija u ovom prolazu: pretraga 128.288 → 70.861 B, kategorija
125.418 → 70.867 B. To je oko 44–45% manje nekomprimiranog HTML-a u fixture-u.
Vary razdvaja fragment i punu stranicu. To nije nova izmjena ovog pregleda.

## Ostali nalazi, bez nagađanja

- Pretraga i rangiranje kataloga materijalizuju rezultate prije Python
  sortiranja i paginacije. Tu su posebna pravila cijena varijacija, lagera,
  akcija i typo pretrage; zamjena SQL sortiranjem bez provjere jednakosti
  može promijeniti rezultate. Lagani ranking već postoji za obične kategorije.
- Brand showcase ima upite po sekciji na cache missu; javne sekcije već imaju
  LocMemCache. Cache je po procesu i prazni se pri restartu.
- Render blueprint navodi jedan Gunicorn worker, četiri threada, max-requests
  400 + jitter 50 i free plan. Stvarna Render konfiguracija nije potvrđena;
  nije mijenjan start command ni plan.
- PostgreSQL konfiguracija već koristi conn_max_age=600; SQLite vrijednost 0
  nije dokaz da produkcija otvara novu konekciju pri svakom zahtjevu.
- WMS sinhronizuje nove dokumente pri prikazu; kombinovani izbor kupaca
  materijalizuje podatke. To nije uklonjeno jer pripada poslovnom toku.
- Sticky header ima ResizeObserver i requestAnimationFrame grupisanje;
  raniji profiler pokazuje trošak mjerenja layouta. Nije prepravljan bez
  browser tracea i provjere CLS-a.
- Public online visitors je ugašena ruta i skripta nije uključena u aktivne
  templatee. Nakon provjere nije izmijenjena i nije predstavljena kao ubrzanje.
- CSS/font bundle-i, hero prioritet, postojeće memoizacije logotipa i zaštite
  od preklopljenih pollova korpe/staff obavijesti već postoje.

## Provjere

15 testova: bestseller performance, catalog load more, category performance,
noviteti selection, button appearance, reels category — prolaze.

29 dodatnih testova: cart availability, checkout customers, account complaints,
product search scope, WMS tools — prolaze. Ukupno 44 Django testa.

node scripts/test_cart_stock_performance.cjs — obje kopije prolaze i daju
identične rezultate prije/poslije. JavaScript syntax, manage.py check i
git diff --check prolaze. Nije pokrenut kompletan test suite projekta.

## Datoteke ovog prolaza

- EcommerceApp/bera_home.py
- EcommerceApp/static/bera_reference/js/cart-stock.js
- EcommerceApp/static/js/cart-stock.js
- EcommerceApp/template/bera_reference/cart.html
- EcommerceApp/template/bera_reference/checkout.html
- EcommerceApp/tests_bestseller_performance.py
- scripts/test_cart_stock_performance.cjs
- ovaj izvještaj

## Preostala mjerenja

Za zaključak o općem štekanju potrebni su Render response/CPU/memory/restart
podaci, PostgreSQL query planovi i latencije, te browser Long Tasks i LCP
trace na istim ekranima i uslovima prije/poslije. Lokalne izmjene još nisu
objavljene. Nema dokaza da su ukupni produkcijski zastoji riješeni.
