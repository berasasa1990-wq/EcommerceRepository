# CarpologijaBH — Performance Optimization 2.0

Datum: 12.10.2026. Polazni commit: `759e9a97e34420c5434e0d5caa89550e4c21c78f`.

Prvi `git status --short` bio je prazan. Ranije WMS, SEO, pretraga i performance izmjene već su dio polaznog commita. Jedina izmjena postojećeg produkcijskog koda u ovom zadatku je analiza logotipa u JavaScriptu. Nema izmjena migracija, poslovne logike, CSS-a, template-a, cijena, lagera ili narudžbi. Nema staginga, git push-a, deploymenta ili produkcijskih migracija.

Korisnikov GTmetrix rezultat je 69%, LCP 2,2 s, TBT 254 ms, CLS 0 i HTML približno 1,1 s. Ciljevi 85–95%, LCP <2 s, TBT <150 ms i TTFB <500 ms **nisu potvrđeni ovim lokalnim patchom**.

## Dijagnostika i odluka

| Prioritet | Potvrđen nalaz | Postupak |
|---|---|---|
| P1, implementirano | `bera-brand-logo-align.js` ponavlja punu canvas analizu iste slike na svakoj kartici, uključujući ponovljene CORS SecurityError greške. CPU profiler pokazuje značajan trošak `align`. | Jedan rezultat po izvoru, prirodnim dimenzijama i CORS načinu. I pozitivni rezultat i trajno nedostupan canvas imaju cache samo za tu stranicu. |
| P1, daljnje mjerenje | Sticky header `measure()` je među najvećim browser hotspotovima: oko 184 ms desktop / 97 ms mobile self CPU u baseline uzorku pod 4× CPU usporavanjem. Geometry čitanje može uključivati trošak layouta. | Nije mijenjan: već grupiše čitanja i izbjegava iste CSS upise. Prije promjene treba trace layout događaja i provjera prvog prikaza/CLS-a. |
| P1, infrastruktura | Produkcijski mrežni TTFB mnogo je veći od lokalnog toplog Django zahtjeva. | Bez Render/production PostgreSQL telemetrije uzrok nije utvrđen. Ne uvoditi guessed SQL indekse ili shared HTML cache. |
| P2 | Facebook Pixel ima vidljiv CPU trošak; najveći pojedinačni callback oko 36 ms u baseline desktop profilu. | Tracking nije uklonjen niti odložen: nemamo dokaz da se može promijeniti bez gubitka događaja. Server-side PageView već koristi thread; HTTP poziv nije sinhrono čekanje početne stranice. |
| P2 | Font transfer oko 187 kB; CSS oko 135 kB. | Zadržani postojeći bundle-i, font metrike, latin/latin-ext subseti i swap. Nema sigurnog dokaza za uklanjanje stilova ili fontova. |

### Django, upiti i cache

Izolovani fixture: 72 proizvoda, 6 brendova, 6 brand showcase sekcija i 1 category showcase. Koristi `scripts.bera_home_qa_settings`, testnu SQLite bazu i isključene stvarne vanjske integracije. Ovo nije produkcijski PostgreSQL benchmark.

| Dio početne | Prije: cold ms / SQL | Poslije: cold ms / SQL | Poslije: warm ms / SQL |
|---|---:|---:|---:|
| Najnoviji proizvodi | 4,114 / 4 | 3,614 / 4* | oko 3 / 4 |
| Izdvojeni proizvodi | 2,571 / 2 | oko 2 / 2 | <0,1 / 0 |
| Akcije | 3,496 / 2 | oko 2 / 2 | <0,1 / 0 |
| Trust sadržaj | 0,403 / 1 | <1 / 1 | <0,1 / 0 |
| Category showcase | 5,143 / 5 | oko 4 / 5 | <1 / 0 |
| Brand showcase | 21,061 / 19 | oko 16 / 19 | oko 1 / 0 |
| Vlog | 0,039 / 0 | <0,1 / 0 | <0,1 / 0 |

\* Tačne vrijednosti svih sekcija su u `optimization-2/app-before.json` i `app-after.json`; navedena zaokruživanja služe preglednosti. Ne pripisivati vremenske razlike JS patchu: Django kod nije mijenjan.

Brand showcase ima upite po sekciji na cache missu, ali ukupno oko 16–21 ms u ovom fixture-u; cache hit ne izvršava njegove SQL upite. Najnoviji proizvodi namjerno ponovo biraju proizvode i imaju četiri grupisana upita, uključujući prefetch varijacija/akcija. Nema potvrđenog warm N+1 problema koji bi objasnio produkcijski TTFB.

Warm SQL lista pokazuje i pojedinačna čitanja bannera, PageSEO, appearance/permissions/WMS postavki, bestseller i popup sadržaja te schema introspection. Ranija request-scope memoizacija ostaje. Nema dokaza da njihov lokalni trošak opravdava dodatnu invalidaciju ili promjenu poslovnog ponašanja.

Postojeći cache je `LocMemCache`, timeout 180 s, po procesu. Cache javnih sekcija već radi. Više Gunicorn workera ima nezavisno zagrijavanje cache-a; stvaran uticaj na Render nije izmjeren. HTML trenutno vraća `cf-cache-status: DYNAMIC`, `Vary: Cookie, Accept-Encoding` i CSRF cookie. Kompletan HTML nije keširan jer korpa i korisnik moraju ostati izolovani.

SQL vrijeme prije iz CaptureQueriesContext ima milisekundnu rezoluciju i prikazuje 1 ms cold / 0 ms warm; to **ne znači da je SQL besplatan**. Završni profiler koristi `connection.execute_wrapper` i `perf_counter` za preciznije mjerenje: oko 5,3 ms cold / 0,7 ms warm. Ove dvije SQL vremenske kolone nisu međusobno uporedive. PostgreSQL `EXPLAIN (ANALYZE, BUFFERS)`, indeks selektivnost i Render response time nisu dostupni; nema novih indeksa niti migracija.

### JavaScript, CSS, fontovi i hero

Inventar produkcijskog HTML-a je u `optimization-2/asset-inventory.json`. Svih 13 vanjskih first-party skripti početne već imaju `defer`: home-mobile-reference, bera-brands, newsletter-subscribe, mobile-search, mobile-dock, csrf-session, main.min, brand-logo-normalize, desktop-reference, stock-notify, set-builder-gate, scratch-game i bera-brand-logo-align. Pixel se dodaje dinamički. Inline inicijalizacije i njihov layout trošak ostaju dio profila; samo dodavanje `defer` ne bi riješilo njihovu CPU potrošnju.

`brand-logo-normalize.js` već ima cache i IntersectionObserver. Nije uklonjen jer koristi drugačije granice/threshold i dodaje izrezani canvas, dok alignment podešava wrapper. Spajanje ova dva algoritma bez identičnog vizuelnog rezultata nije opravdano. Nema potvrđene duple inicijalizacije menija, korpe ili pretrage koja je uklonjena. Mobilna pretraga već koristi debounce i prekid prethodnog zahtjeva.

PerformanceObserver potvrđuje stvarni LCP kao prvi `#heroCarousel .slide-image-img`: desktop `Cline_carpologija_Travel_torba.webp`, mobile `banners/mobile/slika_torbe_cline.jpg`. Oba imaju eager/high. Picture i medijski ograničeni preload odgovaraju desktop/mobile izboru; preload je prije CSS-a. Hero je u server-rendered HTML-u. `sizes` je 100vw; udaljenoj originalnoj slici se ne izmišljaju responsive varijante nepoznatih dimenzija. Slike, kvalitet, proporcije i način prikaza nisu mijenjani.

Aktuelni desktop hero HEAD: 191.426 B, `cf-cache-status: HIT`, `cache-control: public, max-age=31536000, immutable`. Cache već radi; infrastruktura R2/Cloudflare nije mijenjana. Zaglavlja su sačuvana, CSRF vrijednost je redigovana.

CSS ima 9 stylesheet linkova, među njima postojeći home-core i home-ui bundle. Desktop-only media pravila ostaju. Fontovi ostaju self-hosted Inter latin/latin-ext; samo regular subseti su preloaded. Nema promjene CSS cascade-a, kritičnog CSS-a ili font metrike bez dokaza i šire vizuelne provjere.

## Implementacija i mjerljiva korist

Cache čuva samo izračunate granice ili null, ne cijeli pixel buffer. Ključ uključuje `currentSrc || src`, naturalWidth/naturalHeight i crossOrigin, pa različita rezolucija ili CORS način ne dijele rezultat. Samo SecurityError se pamti kao trajni neuspjeh; ostale iznimke zadržavaju prethodno ponašanje. Prag boje/alfa, puna rezolucija skeniranja, skaliranje 72×20 i konačni inline stilovi ostaju isti.

| Kontrolisana mjera | Prije | Poslije |
|---|---:|---:|
| Stvarni desktop HTML, getImageData pokušaji alignmenta | 18 | 5 |
| Isti mobile HTML, učitane slike tokom istog scroll testa | 12 | 4 |
| Sintetički test: 18 kartica / 6 istih logotipa | 18 analiza | 6 analiza |
| Stilovi svih 18 stvarnih logotipa i wrappera | baseline | identični |
| Warm Django SQL | 14 | 14 |
| Cold Django SQL, isti izolovani fixture | 50 | 50 |
| Warm lokalni Django zahtjev | 16,287 ms | 12,936 ms |
| Cold lokalni Django zahtjev | 294,381 ms | 268,604 ms |
| Alignment JS gzip, ista lokalna kompresija | 814 B | 1.058 B |

Django vremenske razlike su varijacija uzorka, ne dobit od frontend izmjene. Cache dodaje 244 B gzip da bi uklonio ponovljen CPU/canvas rad. VM benchmark vremena su u `logo-benchmark.json`; nisu browser TBT niti rezultat na korisnikovom telefonu.

### Nekontrolisani mrežni browser uzorci

Brave/Chromium headless, viewport 1365×900 i 390×900, DPR 1, CPU throttle 4×, izolovani konteksti, javni GET zahtjevi. After koristi lokalni JS response override; produkcija ostaje neizmijenjena. Warm mreža, nasumični proizvodi i raspored zadataka nisu kontrolisani. Broj logotipa je 18/17 prije i 16/17 poslije, pa CPU vrijednosti nisu strogo uparene.

| Mjera | Desktop prije / poslije | Mobile prije / poslije |
|---|---:|---:|
| Mrežni TTFB | 3.007 / 1.247 ms | 881 / 1.242 ms |
| Posljednji hero LCP kandidat | 3.616 / 2.320 ms | 2.116 / 1.764 ms |
| Početni Long Tasks >50 ms | 5 / 4 | 4 / 3 |
| Zbir njihovog trajanja | 611 / 404 ms | 422 / 349 ms |
| Zbir dijelova preko 50 ms | 361 / 204 ms | 222 / 199 ms |
| `align` sampled self CPU, uključuje scroll | 82,181 / 27,103 ms | 28,694 / 21,009 ms |
| CSS transfer | 134.875 / 134.875 B | 134.875 / 134.875 B |
| Font transfer | 186.692 / 186.692 B | 186.692 / 186.692 B |
| JS transfer | 151.998 / 156.441 B | 151.998 / 156.441 B |

Zbir dijelova Long Tasks nije Lighthouse TBT: nije ograničen Lighthouse FCP–TTI prozorom. Mrežni LCP/TTFB i navedene ukupne razlike **nisu dokaz uzročne dobiti patcha**. Response override šalje nekompresovane lokalne logo skripte, pa JS transfer nakon override-a nije predviđanje produkcijske kompresije; za veličinu izmjene koristiti gore navedeni gzip delta. CLS nije zasebno izmjeren ovim profilerom; GTmetrix 0 je korisnikov baseline, ne novi potvrđeni rezultat.

Kontrolisani browser test koristi isti HTML snapshot i kešira tačne image response bajtove za oba prikaza. Desktop screenshot-i na scroll 0 i 1800 i mobile na scroll 0 su piksel-identični. Mobile na scroll 1800 ima razliku jednog piksela u zaglavlju (58,59), izvan izmijenjenih logotipa. Svih 18 logo/wrapper inline stilova je identično prije i poslije na oba viewporta. Detalji: `visual.json`, `pixel-comparison.json` i osam PNG screenshot-a. To ne predstavlja pregled svih drugih stranica, ali algoritam zadržava iste izračunate granice i CORS fallback.

## Datoteke ove izmjene

- `EcommerceApp/static/bera_reference/js/bera-brand-logo-align.js`: jedina izmjena runtime koda.
- `EcommerceApp/tests_performance_2.py`: section/SQL profiler, warm SQL budget i regresije početne, navigacije, pretrage, SEO canonicala, prazne/pune korpe i prijavljenog korisnika; provjera da drugi klijent nema tuđu korpu ili login.
- `scripts/test_brand_logo_alignment.cjs`: granice slike, identični stilovi, smanjenje pixel analiza i SecurityError fallback.
- `scripts/audit_home_browser.cjs`: ponovljivo javno browser profiliranje Long Tasks, LCP, transfera i sampled CPU.
- `scripts/audit_home_browser_visual.cjs`: kontrolisano browser poređenje istog HTML-a/slika i broj pixel čitanja.
- Ovaj izvještaj i `reports/performance/optimization-2/`: mjerenja, inventar, zaglavlja i vizuelni artefakti. SQL artefakti sadrže samo sintetički QA katalog.

## Testovi i ponavljanje

19 ciljanih Django testova prolazi, uključujući postojeće testove hero/preload-a, optimizovanog CSS-a, SEO izolacije, typo pretrage i lager ponašanja korpe. Nakon dodavanja warm SQL budget provjere ponovo prolaze sva 3 nova testa. Node regresija, JS syntax checks i `git diff --check` prolaze. Korisnički meni, korpa i pretraga nisu mijenjani; HTTP regresije i postojeći testovi pokrivaju njihovo ponašanje. Browser audit je provjera rendera i scrolla, ne potpuni interaktivni checkout test.

```sh
venv/bin/python manage.py test EcommerceApp.tests_performance_2 EcommerceApp.tests_home_optimization EcommerceApp.tests_home_lcp EcommerceApp.tests_home_banner_preload EcommerceApp.tests_search_typos EcommerceApp.tests_cart_availability --settings=scripts.bera_home_qa_settings --noinput
node scripts/test_brand_logo_alignment.cjs
node --check EcommerceApp/static/bera_reference/js/bera-brand-logo-align.js
```

Za benchmark poređenja koristiti originalni alignment script iz navedenog polaznog commita kao drugi argument Node testa. Browser skripte traže Playwright (`PLAYWRIGHT_MODULE`, opcionalno putanja do postojeće instalacije) i Chromium (`BROWSER_EXECUTABLE`). `audit_home_browser.cjs before|after` zapisuje JSON u direktorij artefakata. Visual skripta traži `SNAPSHOT_HTML` sa javnim HTML snapshotom i `BASELINE_LOGO_SCRIPT` sa polaznim JS-om. Produkcijski HTML nije upisan u repozitorij. Obje skripte samo čitaju javni sajt i lokalno zamjenjuju response u svom browser kontekstu.

## Rizici, preostali problemi i naredni koraci

1. Nakon eventualnog odobrenog deploymenta ponoviti GTmetrix s istog servera i direktnim HTTPS canonical URL-om, više uzoraka i odvojeno cold/warm. Ovdje deployment nije urađen i nije poznat novi Performance rezultat.
2. Prikupiti Render request/app/worker vremena i PostgreSQL slow queries/EXPLAIN za stvarni katalog. Tek tada odlučiti o indeksima, prefetch promjenama ili Redis cache-u javnih podataka. Ne keširati kompletan personalizovani HTML.
3. Napraviti Chrome trace sticky header measure/layout događaja; pokušati ResizeObserver geometriju bez prisilnog layouta samo uz dokaz da nema CLS-a, preklapanja menija ili pomjeranja sadržaja. Trenutni sync raspored ostaje radi dizajna.
4. Posebno mjeriti uticaj Facebook Pixela i drugih dinamičkih skripti; očuvati događaje pretrage/korpe/narudžbi prije razmatranja promjena rasporeda.
5. Novi logo cache traje do zatvaranja stranice. Kod zamjene sadržaja pod istim URL-om unutar iste stranice granice ostaju iz prvog čitanja; već postojeći normalize cache ima isti životni vijek. Koristiti verzionirane media URL-ove pri promjeni sadržaja. Ne mijenja se R2 immutable politika.
6. Nema novih CSS/font optimizacija jer bez coverage-a svih interakcija i testa font metrike nije dokazana sigurna ušteda. Ne uklanjati stylesheet samo zato što nije korišten u prvom viewportu.

Referentna dokumentacija: [Django local-memory cache](https://docs.djangoproject.com/en/5.2/topics/cache/#local-memory-caching) opisuje cache po procesu; [web.dev Long Tasks](https://web.dev/articles/optimize-long-tasks) definiše >50 ms i blocking period. Ovi izvori objašnjavaju metodologiju, ne potvrđuju rezultate našeg sajta.
