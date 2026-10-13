# Dijagnostika TTFB — 13.10.2026.

## Dokaz i granice

Prijavljeni Chrome Waiting 8,90 s nije reproduciran u dva read-only GET mjerenja canonical početne: TTFB 1,292375 s i 2,452383 s; total 1,318415 s i 2,473090 s. DNS/TCP/TLS i Cloudflare/Render putanja uključeni su u curl TTFB, pa to nije čisto Django vrijeme. Health GET /healthz/ TTFB 0,978040 s (TLS završen za 0,023080 s). Health view vraća samo HttpResponse('ok'); ne renderuje početnu ni njene context processors. Jedno health mjerenje nije dokaz uzroka niti se smije oduzeti od drugog requesta kao fiksna konstanta.

Nema pristupa produkcijskim Render logovima, PostgreSQL per-query mjerenjima, čekanju workera ni CPU/memory podacima. Stvarni uzrok 8,90 s zato nije potvrđen. Zatražen je tačan URL i Render log/graf iz vremena zastoja.

## Lokalna instrumentacija

Izolovani Django TestCase, SQLite, 80 sintetičkih artikala/8 kategorija/1 brend. Produkcijska baza nije povezivana. Dotenv i mrežne integracije ugašeni su QA postavkama. Jedan cold i tri warm GET zahtjeva; middleware, view callback, render, context helpers i SQL execute instrumentirani perf_counterom. Sirovi profil: /private/tmp/carp-ttfb-local-profile.json; skripta: /private/tmp/carp-ttfb-profile.py.

- Cold ukupno 275,85 ms; 29 SQL upita, SQL execute 2,72 ms. Ovo uključuje prvo učitavanje resolvera/templateova u testnom procesu.
- Warm medijana ukupno 17,82 ms; 14 SQL upita, SQL execute 0,67 ms.

Pet najvećih izmjerenih relevantnih lokalnih faza (ugnježdene su, NE sabirati):

| Faza | Warm medijana | Prijedlog koji treba potvrditi u produkciji |
|---|---:|---|
| views.render — template i context processors | 11,23 ms | Mjeriti template render i SQL iz templatea odvojeno; tek zatim keširati javne fragmente uz sve varijante i invalidaciju. |
| home callback prije rendera | 6,17 ms | Mjeriti stvarno izvršavanje/evaluaciju upita i cache misses; queryset konstrukcija nije SQL izvršavanje. |
| bera_home.homepage_context | 2,02 ms | Profilisati schema introspection, appearance, novo/bestseller izbor, SEO i JSON čitanje; cache samo javne konfiguracije/ID izbore uz invalidaciju. |
| context_processors.nav_categories | 0,70 ms | Provjeriti hit/miss menija i popup konfiguracije; korisničku korpu, sesiju, privatne popupe i CSRF ne dijeliti između korisnika. |
| SQL execute — svih 14 upita | 0,67 ms | PostgreSQL trajanja i planove pojedinačno izmjeriti na produkciji; lokalni SQLite nije dokaz brzine Postgresa ili potreban indeks. |

Root Template.render warm medijana je 11,11 ms, cold 73,16 ms. home.callback cold 112,84 ms. Izvan home.callback, instrumentirani RequestReadMiddleware pokazuje warm oko 0,38 ms. Ovo su lokalne vrijednosti, bez queue vremena prije Django aplikacije. Context procesori i SQL uključeni su u roditeljske faze.

## Pregled koda

- MetaPageViewMiddleware poziva track_page_view nakon rendera, ali send_event mrežni POST pokreće daemon thread; requests.post timeout=4 nije sinhrono čekanje HTML-a. Priprema user/session payload-a jeste na request threadu i treba je produkcijski izmjeriti. Analitika nije izmijenjena.
- image_field_dimensions za R2/S3 vraća dimenzije bez download/open. Nema dokaza sinhronog image download-a u pregledanoj putanji.
- RequestReadMiddleware javnu početnu trenutno NE profilira; detaljni log/Server-Timing postoje samo za /panel i /wms. Zbog toga postojeći kod ne daje traženi breakdown za prijavljeni request.
- Render blueprint: workers=1, threads=4, timeout=60, max-requests=400+jitter50, plan=free. Stvarna konfiguracija i iskorištenost nisu potvrđene. Ograničen worker broj nije dokaz zagušenja.
- PostgreSQL conn_max_age=600, conn_health_checks=True, connect_timeout=10. Poklapanje trajanja sa timeoutom nije dokaz problema konekcije; izmjeriti connect/health-check i SQL wait zasebno.
- Cache je LocMem, po procesu. Meniji/popupovi/javne sekcije već imaju TTL cache. Restart prazni taj cache, ali produkcijski cache-miss rate i restarts nisu izmjereni.
- CF-Cache-Status:DYNAMIC sam po sebi nije greška za HTML s korpom/sesijom/CSRF-om. Ne uvoditi blanket Cache Everything.

## Konkretan sljedeći korak

Korelirati originalni request (URL, vrijeme, rndr-id/cf-ray) s Render logovima i CPU/memory/restart grafom. U odobrenoj sljedećoj izmjeni dodati privatno/samplirano mjerenje javnog HTML zahtjeva: app_total, view_before_render, template, context, sql_count/sql_execute, DB connect i Meta payload preparation; bez SQL parametara, tokena ili korisničkih podataka. Za vrijeme prije Django-a potrebna je korelacija proxy/Gunicorn/Render podataka. Tek po nalazu izabrati izmjenu: query plan/N+1, cache javnih podataka, template hotspot ili worker/CPU queue. Ne povećavati resurse niti keširati kompletan HTML bez dokaza.

Nisu izmijenjeni dizajn, backend, URL-ovi, modeli, migracije, PostgreSQL podaci, deployment niti postojeće lokalne izmjene. Dijagnostički TestCase prolazi. Novi fajl je samo ovaj izvještaj; instrumentacija je u /private/tmp.
