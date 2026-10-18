# Dodatna optimizacija — 18.10.2026.

Nastavak izvještaja `PERFORMANCE_AUDIT_2026-10-18.md`. Prethodne izmjene nisu ponavljane. Ovaj prolaz mijenja dohvat podataka, bez izmjena prikaza, poslovnih pravila, migracija ili produkcijske konfiguracije.

## Dodatno potvrđeni i ispravljeni problemi

| Mjesto | Uzrok | Izmjena i provjera |
| --- | --- | --- |
| Globalni template context | I dalje se računao sadržaj dva ranije uklonjena exit popupa. Builder je mogao dohvatiti proizvod i izmijeniti sesiju iako se UI ne renderuje. | Uklonjeni pozivi buildera; postojeći context ključevi ostaju `None`. Test potvrđuje da se oba buildera ne pozivaju. Ostale aktivne ponude ostaju. |
| Početna, „Novo u ponudi“ | ORM je materijalizovao i prefetchovao sve prihvatljive proizvode posljednjeg uvoza, pa tek zatim uzimao prvih 10. | Prvo se određuju prihvatljivi ID-jevi, zatim učitavaju samo odabrane kartice. Test sa 22 proizvoda potvrđuje najviše 10 konstruisanih Product objekata i isti redoslijed. Dodan je jedan lagani ID upit; dobit je ograničen broj objekata i povezanih podataka kod velikih uvoza. |
| WMS rezultati artikala | Uklanjanje svih prefetchova ostavljalo je N+1 pri `prikazna_slika` fallbacku na varijaciju. | Samo za trenutnu stranicu učitavaju se ID, veza na artikal i slika varijacije. Test sa pet proizvoda potvrđuje jedan upit varijacija i nula dodatnih upita pri čitanju fallback slika. Izračun zaliha nije promijenjen. |
| Django admin artikli | Ispis kategorije koristi i njenog roditelja; te veze nisu bile eksplicitno učitane u ProductAdmin querysetu. | `select_related` za brend, kategoriju i njenog roditelja. Test ispisuje kategorije pet artikala jednim SQL upitom. |
| Potomci kategorije | Rekurzija je izvršavala upit za svaku kategoriju, uključujući listove. | Dohvat po nivoima, zatim isti dubinski redoslijed rezultata u memoriji. Test stabla sa osam grana i listom prolazi sa tri SQL upita; neaktivne grane su izostavljene. Poseban test čuva ponašanje namjerno filtriranog prefetcha. |

Izmijenjeni fajlovi ovog prolaza: `EcommerceApp/context_processors.py`, `EcommerceApp/views.py` (samo izbor početnih noviteta u ovom prolazu), `EcommerceApp/views_magacin.py`, `EcommerceApp/admin.py`, `EcommerceApp/models.py`, `EcommerceApp/tests_performance_followup.py` i ovaj izvještaj. Ostale postojeće lokalne izmjene pripadaju ranijem prolazu.

## Dodatne provjere i granice nalaza

- Pregledani su ORM pozivi/petlje u views modulima, adminu i context processorima te postojeći batched dohvat korpe, picking podataka i zaliha. Statički pregled svih modula nije isto što i mjerenje svake moguće poslovne putanje na produkcijskim podacima.
- Middleware već izostavlja statiku, medije, sitemap i pozadinske putanje iz odgovarajućeg trackinga; HTML gzip i WhiteNoise ostaju. Meta CAPI koristi pozadinsku nit i mrežni timeout. Analitika nije uklonjena niti je njeno trajanje proglašeno uzrokom trzanja.
- Korpa već ima single-flight polling na 15 sekundi i preskače skrivene tabove. Prethodne zaštite heartbeat/public polling i autocomplete provjerene su Node harnessom. WMS draft i trajni unos imaju namjenske tajmere; nisu uklonjeni jer čuvaju funkcionalnost.
- CSS bundleovi i kompresovane kopije provjereni su postojećim testovima. Dodatno odlaganje osnovnog CSS-a bez izmjerenog critical CSS-a može promijeniti prvi prikaz; nije primijenjeno. Nema novih CSS/JS promjena u ovom prolazu.
- Slike imaju postojeću podršku za AVIF, responsive varijante i eksplicitne dimenzije/lazy loading u templateima. Za udaljeni R2/S3 storage helper namjerno ne radi `exists/open` pri renderovanju i koristi original kada nema potvrđene varijante. Ne treba naslijepo generisati srcset URL-ove: stariji importi ne moraju imati te fajlove. Manifest varijanti je mogući zaseban projekat, uz provjeru sadržaja storagea.
- Cache je LocMem (lokalan procesu); nema uvedenog cacheiranja personalizovanog HTML-a. Cloudflare DYNAMIC za HTML iz prethodnog mjerenja nije dovoljan razlog za javni cache korpe/sesije. Pojedinačni media HIT/MISS i stvarne Dashboard postavke nisu izmjereni u ovom prolazu.
- Render blueprint pregledan je bez izmjene: Gunicorn 1 worker / 4 threads, timeout 60, graceful timeout 20, max requests 400 + jitter 50. Optimalan broj workera zahtijeva stvarnu RAM/CPU i concurrency metriku. Blueprint ne dokazuje trenutno stanje Render Dashboarda.

## Uklonjeni `/uzivo-obavijesti/` i razlika prema produkciji

Aktivni `EcommerceApp` kod nema poziv ni rutu. Jedino pojavljivanje putanje u aplikaciji je regresijski test koji očekuje nepostojeću rutu. Stari JS postoji u `scripts/snapshots/pocetno/`; taj direktorij nije u `STATICFILES_DIRS` i ne učitava se kao aktivni asset.

`git diff HEAD -- EcommerceApp/urls.py` potvrđuje da je uklanjanje rute još lokalna, necommitovana izmjena. Prethodno izmjeren produkcijski 302 prema loginu odgovara starom endpointu. To podržava zaključak da produkcija još nema lokalno uklanjanje, ali bez deploy SHA nije moguće definitivno utvrditi koji commit Render izvršava. Otvoreni stari tabovi također mogu nastaviti izvršavati prethodno učitani JS. Nije urađen push, deploy ni brisanje arhivskih snapshotova.

## Testiranje

- 38 Django testova: `tests_performance_followup`, `tests_noviteti_selection`, `tests_lookup_performance`, `tests_checkout_customers`, `tests_web_stock`, `tests.StaffLiveAlertTests` — PASS.
- 8 dodatnih Django testova: `tests_request_performance` i četiri postojeća WMS testa za zbirne zalihe, prazne/negativne zalihe, navigacijske countove i picking listu — PASS. Korpa sa 20 artikala: 7 upita; picking sa 20 stavki: 1 upit. Lookup: isti broj upita za 1 i 8 proizvoda (12).
- 5 Django testova: `tests_remote_image_fallback` i `tests_product_search_scope` — PASS. Potvrđen fallback udaljenih slika bez storage mrežnih poziva i očuvan opseg/redoslijed pretrage. Ukupno: 51 Django test PASS.
- `node scripts/test_frontend_lifecycle.cjs` — PASS (paralelni heartbeat, skriveni tabovi, beacon fallback, public polling, zastarjeli autocomplete).
- `venv/bin/python -m unittest discover -s scripts -p 'test_css_*.py'` — 11 testova PASS.
- `manage.py check` i `git diff --check` — PASS.

Nije pokrenut kompletan projektni test suite. Dva starija WMS list testa očekuju raniji prikaz od 20 rezultata i učitavanje bez varijacija, dok trenutni UI koristi 10 i image fallback; ovim prolazom njihove pretpostavke nisu prepisivane. Aktuelni fallback pokriven je novim ciljanim testom.

## Preostala verifikacija

Novi INP/LCP/TTFB rezultat nije mjeren u pravom browser traceu; SQL regresijski testovi dokazuju uži dohvat, ne garantuju određeni broj milisekundi na Renderu. Za poređenje nakon korisnički odobrenog deploya koristiti isti uređaj, throttle, URL i stanje korpe, hladni/topli cache, provjeriti Network polling i konkretne media cache headere te ponoviti skrolanje/pretragu/navigaciju. Nema izmjena izgleda, narudžbi, plaćanja, WMS pravila, migracija, podataka ili produkcijskih postavki.
