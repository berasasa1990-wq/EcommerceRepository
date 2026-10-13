# Privremena TTFB dijagnostika

Implementacija je pripremljena lokalno, podrazumijevano **isključena**. Nije urađen push, deployment, promjena baze ili Render postavki.

## Uključivanje i isključivanje

Nakon odobrenog objavljivanja koda, u Render Environment postaviti:

```
TTFB_DIAGNOSTICS_ENABLED=true
```

Postavka se čita pri pokretanju Django procesa; potrebno je ponovno pokretanje servisa nakon promjene environmenta. Ne uključivati DEBUG radi ove dijagnostike.

Za isključivanje postaviti `TTFB_DIAGNOSTICS_ENABLED=false` ili ukloniti varijablu, zatim ponovo pokrenuti servis. Isključeni middleware samo prosljeđuje zahtjev; ne postavlja mjerače, headere niti dijagnostičke logove. Ovi koraci nisu automatski primijenjeni.

## Šta se mjeri

Za javne GET/HEAD rute, uključujući `/` i `/healthz/`:

- `django`: vrijeme od početka mjerenja u prvom middlewareu do povratka odgovora; uključuje naredne middlewaree, view, template render i HTML kompresiju.
- `sql`: zbir trajanja Django DB `execute`/`executemany` poziva i broj tih poziva. Parametri i tekst SQL-a se ne pohranjuju. Fetch rezultata, ORM obrada i direktni driver pozivi/health checks nisu zasebno obuhvaćeni ovom metrikom; pripadaju ukupnom Django vremenu.
- `db_connect`: vrijeme `get_new_connection` na thread-local DB wrapperu; uključuje driver povezivanje (DNS/TCP/TLS/autentifikaciju). Postojeća konekcija se ne otvara ponovo. `0 attempts` znači da se u tom zahtjevu nije otvarala nova konekcija, ne da je povezivanje instant. Mjeri se i neuspjeli pokušaj koji Django obradi u odgovor; middleware ne mijenja obradu grešaka.

Streaming body nije obuhvaćen: mjerač završava kad se vrati response objekat. Ne mjeri se mreža niti čekanje prije ulaska zahtjeva u Django.

Primjer response headera:

```
Server-Timing: django;dur=1234.56, sql;dur=800.00;desc="14 queries", db_connect;dur=120.00;desc="1 attempts"
X-TTFB-Request-ID: <novi slučajni UUID hex>
```

SQL i konekcija su uključeni u ukupno Django vrijeme: metrike se ne sabiraju s `django`. Postojeći Server-Timing se čuva. Privatne `/nalog`, `/wms`, `/panel`, `/admin`, `/veleprodaja` rute, API, statika, media i write zahtjevi nisu instrumentirani.

## Render logovi i korelacija

Samo zahtjevi s izmjerenim Django vremenom **strogo iznad 1000 ms** daju jednu WARNING liniju iz novog loggera:

```
Slow Django request request_id=<isti UUID> route=home method=GET status=200 django_ms=... sql_execute_ms=... sql_count=... db_connect_ms=... db_connect_count=...
```

U Chrome Network otvoriti konkretni Document zahtjev, zabilježiti Timing i `X-TTFB-Request-ID`, pa u Render logovima tražiti taj ID. Brzi zahtjevi imaju headere, ali nemaju dijagnostičku log liniju. Ako je browser čekao 8,90 s, a header kaže `django;dur=20`, odsustvo slow loga je očekivano.

ID generiše aplikacija; dolazni X-Request-ID se ne kopira. Logger ne sadrži putanju/slugs, query string, request body, IP, user-agent, cookies, lozinke, tokene, SQL, SQL parametre niti podatke kupca. Route je ime iz Django URL konfiguracije. Postojeći logovi drugih modula nisu mijenjani.

## Gunicorn i čekanje

Blueprint start command ostaje neizmijenjen:

```
gunicorn EcommerceProject.wsgi:application --workers 1 --threads 4 --timeout 60 --graceful-timeout 20 --max-requests 400 --max-requests-jitter 50
```

Instalirani Gunicorn za ovu konfiguraciju bira `ThreadWorker` (gthread). To daje jedan proces s četiri request threada; zagušenje je moguće kada su svi zauzeti. Stvarna Render start konfiguracija, broj instanci, CPU/memory i queue stanje nisu dostupni ovom middlewareu i nisu potvrđeni.

- Veliki `django`, veliki `sql`: profilisati PostgreSQL upite/waitove i planove.
- Veliki `db_connect`: provjeriti otvaranje/reotvaranje konekcija i mrežu prema PostgreSQL-u.
- Veliki `django`, mali `sql` i `db_connect`: dalje mjeriti CPU, template/context, fetch, driver health-check ili druge sinhrone operacije; samo ovaj zbir ne određuje uzrok.
- Mali `django`, veliki browser Waiting: provjeriti Cloudflare/Render putanju, region, worker/thread čekanje i restart/cold start, uz Render CPU/memory i request logove iz istog vremena. Razlika nije automatski čisto queue vrijeme; uključuje i druge mrežne/proxy faze.
- Uporediti `/` i `/healthz/` u isto vrijeme bez agresivnog load testa. Korelirati i postojeće `rndr-id`/`cf-ray` response headere s dostupnim provider logovima, ali oni se ne kopiraju u novi logger.

Nisu dodati globalni access logovi koji bi bilježili sve zahtjeve, osjetljive URL-ove ili query stringove. Nisu povećani worker/thread brojevi niti Render resursi.

## Provjere i datoteke

18 relevantnih Django testova prolazi, uključujući 8 novih dijagnostičkih testova: enabled/disabled, javni/private/write zahtjevi, stvarni SQL count, reused konekcija, neuspjeli connect, čišćenje wrappera poslije izuzetka, granica 1000 ms, čuvanje headera i odsustvo tajnih podataka. Django check i git diff --check prolaze. Testiranje koristi izolovanu SQLite QA konfiguraciju, bez produkcijske PostgreSQL konekcije.

Datoteke: `EcommerceApp/middleware/ttfb_diagnostics.py`, `EcommerceApp/tests_ttfb_diagnostics.py`, `EcommerceProject/settings.py` (opt-in flag, prvi middleware, poseban stderr logger), ovaj dokument. Postojeće lokalne izmjene nisu prepisane.
