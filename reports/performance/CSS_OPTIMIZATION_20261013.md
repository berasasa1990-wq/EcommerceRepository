# CSS optimizacija — 13.10.2026.

Sačuvane su prethodne lokalne izmjene. Backend, URL-ovi, modeli, migracije, produkcijska baza, analitika i deployment nisu mijenjani.

## Primijenjeno

- Render već poziva `scripts/build_home_css.py` prije collectstatic. Skripta sada regeneriše active-theme style/header/navigation/extras iz izvornih CSS fajlova, zatim minificira bundleove i bera-home.min.css. Izvori ostaju sačuvani.
- Novi `scripts/css_safe_minify.py` uklanja samo ranije kopije potpuno identičnih top-level style pravila. Posljednja kopija ostaje na originalnom mjestu. At-rules, njihova nested pravila, različite deklaracije i fallbackovi ostaju; CSS s @layer ili @import ne deduplicira se.
- base.html učitava bera-home.min.css umjesto izvorne verzije, na istom mjestu. Minificirani su home-ui, core/rest i inline popup stilovi. Critical CSS ostaje kontinuirani prefiks istog konačnog core bundlea; nema dupliciranja corea u HTML-u.
- Homepage grana koristi core-rest i home-ui; ostale stranice koriste originalne odvojene stilove. Preload + stylesheet iste datoteke nisu dupli stylesheet elementi. Puni home-core služi build/split provjeri i nije dodatno učitan u homepage HTML-u.

## Veličine (UTF-8 bytes)

| Resurs | Prije | Poslije |
|---|---:|---:|
| home-core-rest.bundle.css | 491034 | 489836 |
| bera-home.css → bera-home.min.css | 87460 | 82649 |
| home-ui.bundle.css | 66507 | 55305 |
| Ukupno | 645001 | 627790 |

Ušteda: 17211 B, oko 2,7%. gzip u izolovanom collectstatic/WhiteNoise okruženju postoji za sva tri hashed resursa; HEAD vraća Content-Encoding:gzip i Cache-Control: public, immutable, max-age=315360000. Lokalni Brotli modul nije instaliran; nije potvrđena nova produkcijska Brotli konfiguracija.

## Lighthouse poređenje

Lighthouse 13.5.0, mobile simulated throttling, po tri interleaved cold testa svake verzije. DB-free snapshot, isti HTML/podaci/JS/media, isti server setup; CSS se u ovom razvojnom poređenju šalje bez kompresije u obje verzije, HTML gzip u obje. Ovo nisu produkcijska mjerenja ni produkcijski TTFB. Sirovi rezultati: /private/tmp/carp-css-comparison/lighthouse/.

| Medijana | Prije | Poslije |
|---|---:|---:|
| Performance | 62 | 63 |
| FCP | 4,956 s | 4,806 s |
| LCP | 7,654 s | 7,281 s |
| TBT | 124,5 ms | 120,5 ms |
| CLS | 0,000065 | 0,000065 |
| Procijenjena render-blocking FCP ušteda | 3750 ms | 3600 ms |

## Provjere

Django check, DEBUG=False check i izolovani collectstatic prolaze. 43 relevantna Django testa prolaze nakon prilagođavanja dva zastarjela product-offer testa: prethodno su očekivali tačan class atribut bez već postojećeg modifiera. Produkt template i ponašanje ponude nisu mijenjani.

Snimke prije/poslije: početna na 360/390/430/1440 px, još 40 kombinacija kategorije/proizvoda/korpe/checkouta/pretrage i širine, otvoreni meni i popup. Nema nedostajućih CSS/JS resursa. Tri početne razlike na sadržaju ispod ekrana nestaju poslije učitavanja fontova/lazy elemenata. Preostale razlike su do jednog intenzitetskog nivoa jednog/dva antialias piksela; nema materijalnih razlika u izgledu. Sadržaj testnih fixtures je sintetički ili zamrznut, ne potpuni pregled svake produkcijske ponude.

## Granice

Veliko render-blocking usporavanje nije riješeno. Nisu brisana pravila zbog coverage-a samo na prvom ekranu: JS generisane klase, otvorena navigacija, modali, footer, varijacije i druge stranice mogu ih koristiti. Nije uvedeno asinhrono učitavanje glavnog CSS-a jer trenutno nema dokazano potpunog minimalnog critical seta za sva ta stanja; time se čuva izgled i izbjegava nova mogućnost FOUC-a. Za veći rez potreban je zaseban pregled page/component zavisnosti i critical extraction kroz sva relevantna responsive/interactive stanja. Nema garancije 90+ niti uklanjanja svih 3750 ms.

## Datoteke

scripts/build_home_css.py, scripts/css_safe_minify.py; active CSS home-core.bundle.css, home-core-rest.bundle.css, home-ui.bundle.css, novi bera-home.min.css; base.html; home_critical_css.html i home_popup_css.html; tests_home_critical_delivery.py i tests_product_bundle_placement.py; ovaj izvještaj. Prethodne lokalne WMS/pretraga/korpa izmjene ostaju netaknute.
