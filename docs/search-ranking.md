# Pretraga artikala — 16.11.2026.

Izmjene su lokalne, bez deploya i bez upita prema produkcijskoj bazi.

## Uzrok

- `/?q=...` ide kroz `home` → `_apply_product_filters`. API `/api/pretraga/` koristi `search_suggest`. Desktop (`main.js` / `main.min.js`) i mobilni dijalog (`mobile-search.js`) oba koriste ovaj API, s limitom 5.
- API je sortirao SQL bodovima, a puna pretraga je zatim do 400 rezultata ponovo sortirala drugom Python funkcijom, uključujući cijenu. Zato ni prvih pet nije moralo biti isto.
- SQL `CASE` je naziv i tipfelere stavljao ispred tačne šifre. Sirovi naziv i `naziv_normalized` imali su različite bodove, a postojeće normalizirane kolone nisu dosljedno popunjene/foldane nakon uvoza. Ni šifre s različitim separatorima nisu imale jedinstven ključ.
- API nije primjenjivao izbor kategorije iz forme. Desktop i mobilni fetch/link za sve rezultate nisu prenosili iste filtere.
- Fuzzy cache je pri svakom hladnom punjenju čitao sve aktivne nazive.

## Ponašanje nakon izmjene

Jedan SQL redoslijed za punu pretragu i prijedloge: tačna kompaktna šifra artikla/varijacije (200), proizvođačka šifra kao zaseban token u nazivu (190), tačan naziv (150), sve bitne riječi (140), djelimična šifra (120), tipfeler (110), tag (10). Dostupnost, novitet, prioritet lagera, normaliziran naziv i PK razrješavaju izjednačenja. Eksplicitno korisničko sortiranje po cijeni ostaje dostupno.

`MT12221`, `mt12221`, `MT-12221`, `MT 12221` koriste isti ključ. `12221` i `ODOO-T5672` ostaju podržani. Koriste se postojeća polja `sifra` na artiklu/varijaciji te `naziv`; nema novog poslovnog polja za šifru. `š/č/ć/ž` i `đ/dj`, velika/mala slova i Unicode NFC obrađuju se jednako u upitu i izvornim podacima. Izvorne kolone se normaliziraju u SQL-u: bulk uvoz ne može ostaviti zastarjeli search ključ.

Broj `4000` ne zadovoljava broj `14000`; `SP4000-A` i `4000P` zadovoljavaju veličinu 4000. Riječi mogu biti u obrnutom redoslijedu. Fuzzy fallback radi kada nema direktnih pogodaka, bira najviše 300 kandidata u bazi, pa na njima koristi postojeću edit-distance logiku. PostgreSQL koristi i `pg_trgm` operator; SQLite prefix/token predizbor. Ograničenje od 300 znači da veoma širok tipfeler može propustiti dio pogodaka; nema učitavanja svih naziva u Python.

Za osnovni upit, kategoriju, brend i dostupnost API radi SQL `LIMIT 6`, vraća najviše 5 i zadržava `has_more`. Za filtere koji zavise od efektivne cijene/varijacija ili posebno sortiranje koristi se postojeći puni filter pipeline radi identičnog rezultata; taj put zadržava postojeći plafon od 5000 artikala. Paginacija ostaje 49, rezultati pretrage `noindex, follow`. Prijedlozi ne bilježe `SiteSearchEvent`.

## Izmijenjene datoteke za ovaj zadatak

- `EcommerceApp/views.py`: zajedničko rangiranje, šifre, predizbor za tipfelere, filteri i uklanjanje drugog sortiranja pune pretrage.
- `EcommerceApp/search_expressions.py`: zajednički ključevi i ORM izrazi.
- `EcommerceApp/apps.py`: ekvivalentne SQLite funkcije, uklanjanje zastarjelog fuzzy cache invalidatora.
- `EcommerceApp/migrations/0353_search_expression_indexes.py`: PostgreSQL funkcije i indeksi.
- `EcommerceApp/static/js/main.js`, `main.min.js`, `mobile-search.js`: isti filteri za fetch, Enter i link za sve rezultate; osvježavanje nakon promjene kategorije.
- `EcommerceApp/template/base.html`, `template/partials/mobile_search_dialog.html`: verzije promijenjenih JS resursa.
- `EcommerceApp/tests_search_ranking.py`: ciljani regresijski testovi.
- `scripts/profile_catalog_search.py`, ovaj izvještaj i `search-ranking-local-profile.txt`: ponovljiva mjerenja.

Ranije lokalne izmjene „+ %” akcije ostavljene su na mjestu.

## Izmjerena razlika na lokalnom katalogu

Baza je SQLite, 5872 artikla; sadrži navedene MATE čizme. „Prije” je izvršen kod iz `git HEAD`, „poslije” radni kod, na istim podacima. Ovo ne tvrdi da je produkcijski redoslijed identičan lokalnom.

| Upit | Prije: puna / API | Poslije: puna i API |
|---|---|---|
| `mašinica`, `masinica` | Prvi `CL8212 Cline Compo 4000` / `CL8203 Cline Solid Black 4500` | Oba: `CL8203`, `CL8212`, `CL8213`, `CL8222`, `CL8502` |
| `MT12221`, `MT-12221`, `MT 12221`, `12221`, `ODOO-T5672` | MATE HP WINTER BOOTS 41 | Isti artikal prvi za sve varijante |
| `boots winter`, `wintr boots` | MATE čizme, veličine 42, 43, 44, 45, 41 | Isti redoslijed u oba endpointa |
| `okuma mašinica 4000` | API je na 2. mjestu prikazivao AZORES **14000P**, puna imala drugi redoslijed | Oba: AZORES 4000P, ITX-4000, ITX-4000H, Safina Pro SNP-4000, Safina SN-4000 |

Tačna šifra rasprodatog artikla ispred dostupnog djelimičnog pogotka provjerena je testnim podacima, kao i šifra upisana samo u naziv i šifra varijacije.

## SQL i plan

Jedan lokalni uzorak, nije benchmark produkcije:

| Upit | Rangiranje: SQL upiti / vrijeme | Cijeli API: SQL upiti / vrijeme |
|---|---|---|
| `masinica` | 3 / 44,52 ms | 7 / 211,33 ms |
| `MT12221` | 3 / 81,42 ms | 7 / 78,11 ms |
| `wintr boots` | 4 / 52,71 ms | 8 / 48,34 ms |

Vrijeme uključuje instrumentaciju i zagrijavanje aplikacije te varira. Broj upita ne raste po prikazanom artiklu/varijaciji. Fuzzy put dodaje ograničeni kandidat SELECT. Sačuvan stvarni SQLite `EXPLAIN QUERY PLAN` u `search-ranking-local-profile.txt`: skeniranje artikala, list subqueries za šifre varijacija i privremeni B-tree za ORDER BY. SQLite ne dobija PostgreSQL trigram indekse.

PostgreSQL plan nije izmjeren: dostupan je libpq klijent, ali nema lokalnog `postgres` servera; `initdb` zato nije mogao napraviti izoliranu bazu. Produkcijska baza nije kontaktirana i njeni postojeći indeksi nisu potvrđeni. Repozitorij ima obične indekse normaliziranih kolona, ali nema odgovarajuće trigram indekse nad novim kanonskim izrazima.

Ponoviti na lokalnoj ili testnoj bazi:

```sh
venv/bin/python manage.py shell -c "from scripts.profile_catalog_search import run; run()"
```

Na PostgreSQL-u skripta ispisuje stvarni `EXPLAIN (ANALYZE, BUFFERS)` i `pg_indexes`; ne mijenja katalog. Dostupnost indeksa ne garantuje da će planer izabrati svaki indeks za svaki širok upit.

## Migracija

Primjena: `venv/bin/python manage.py migrate --noinput`. Migracija 0353 lokalno je prošla na SQLite-u (bez PostgreSQL DDL-a). Ne zahtijeva backfill kolona niti učitavanje kataloga u aplikaciju.

Na PostgreSQL-u postavlja `pg_trgm`, dvije `IMMUTABLE` funkcije, GIN nad foldanim nazivom, te B-tree + GIN nad kompaktnom šifrom artikla i varijacije. B-tree je za tačnu šifru; GIN za podstring, regex i trigram predizbor. Indeksi koriste izraze istovjetne ORM upitima. Funkcije rade nad izvornim kolonama i zato pokrivaju postojeće i buduće SQL/bulk uvoze. Migracija je `atomic=False`, indeksi se grade `CONCURRENTLY`; nije izvršena na produkciji.

Reference: [PostgreSQL pg_trgm](https://www.postgresql.org/docs/current/pgtrgm.html), [expression indexes i CONCURRENTLY](https://www.postgresql.org/docs/current/sql-createindex.html).

## Provjere

Ciljani testovi pokrivaju šifre, varijacije, šifru u nazivu, dijakritiku/NFC, sve riječi i veličinu 4000, obrnuti redoslijed, tipfeler, rasprodati tačan pogodak, filtere, prvih pet API/punih rezultata, limit i has_more, bulk uvoz, paginaciju te robots oznaku. Postojeći testovi pretrage i URL-a slika pokrenuti su zajedno s njima. Dodatno se pokreću postojeći testovi naziva kataloga, performansi lookupa, sortiranja kategorija/brendova i izbora noviteta.

Rezultati završnih pokretanja:

```sh
venv/bin/python manage.py test EcommerceApp.tests_search_ranking EcommerceApp.tests_product_search_scope EcommerceApp.tests_search_image_urls --noinput
# 20/20 OK, sa standardnim migracijama

venv/bin/python manage.py test EcommerceApp.tests.CatalogNameSearchTests EcommerceApp.tests_lookup_performance EcommerceApp.tests_catalog_newest EcommerceApp.tests_noviteti_selection --noinput
# 8 OK, 3 postojeća pada u tests_noviteti_selection
```

Ukupno 28 prolaznih testova. Tri pada noviteta (`test_latest_import_is_stable_and_deduplicated`, `test_unfinished_import_is_ignored`, `test_see_all_uses_latest_import`) reproducirana su i izvršavanjem neizmijenjenih funkcija iz `git HEAD`: isti rezultat `[] != [2]`. Testovi očekuju posljednji uvoz, dok postojeća `_selected_new_product_ids` namjerno čita ručni izbor `HomeNovoProduct`. To nije mijenjano ovim zadatkom.

Postojeći lookup test potvrđuje isti broj upita za 1 i 8 artikala (12/12), a storefront test potvrđuje batch obradu cijena/varijacija i limit 5. `manage.py check`, `makemigrations --check --dry-run`, `git diff --check` i Node syntax provjere za tri izmijenjena JS fajla prolaze.
