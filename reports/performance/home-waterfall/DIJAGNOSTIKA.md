# Dijagnostika početne prije optimizacije — 12.10.2026.

Korisnički waterfall: 89 zahtjeva, 1,18 MB, Fully Loaded 5,5 s, onload 5,2 s, HTML oko 2,5 s, 307 + 301, CSS oko 1,3 s, Cline hero 187 KB. Prethodni LCP 12,7 s nije nužno isti run. Bez HAR-a ne povezivati te vrijednosti kao jednu navigaciju.

## Preusmjeravanja

Read-only curl HEAD: `http://carpologijabh.ba/` vraća 301 na `https://carpologijabh.ba/`; `https://www.carpologijabh.ba/` vraća 301 na isti canonical URL. U projektu SecurityMiddleware ima SECURE_SSL_REDIRECT i proxy HTTPS header; Django SSL redirect je 301. Nije pronađena aplikacijska 307 ruta za početnu. 307 u waterfall-u vjerovatno je Chromium interni HSTS/HTTPS upgrade, ali bez Non-Authoritative-Reason zaglavlja nije potvrđen. Ne isključivati HSTS/HTTPS. Za benchmark i vanjske linkove koristiti direktno `https://carpologijabh.ba/`. WWW redirect se vidi na javnom edge-u; bez Cloudflare/Render pristupa nije potvrđeno gdje je pravilo postavljeno, pa se ne mijenja infrastruktura.

## Server i cache

Prethodna stvarna browser mjerenja: desktop TTFB 2,793 s, mobile 1,221 s; ukupno mreža+edge+origin, nije samo SQL. Profil 40 sintetičkih artikala: 29–30 cold / 16 warm SQL upita, warm oko 12–14 ms na SQLite. Nema dokaza da lokalni render od 14 ms objašnjava produkcijskih 2,5 s; potrebni su production server timing i Render/DB mjerenje. Otkriveni dupli PageSEO upit i duplo schema introspection čitanje u istom zahtjevu mogu se sigurno spojiti request-scope memoizacijom bez stale cache-a.

HTML je Cloudflare DYNAMIC, Vary Cookie, CSRF cookie; zadržati personalizovanu korpu i wishlist. Selektivni javni podaci imaju LocMemCache po workeru (180 s). Ne uvoditi shared HTML cache; Redis je kasniji infrastrukturni prijedlog. Random latest artikli ostaju svježi.

R2 custom domain media.opremazaribolov.ba: hero slike HIT, godišnji public immutable cache. Cache nije potrebno gasiti/mijenjati. Treba mjeriti više geografskih lokacija prije zaključka o Seattle RTT-u.

## CSS, fontovi i LCP

Stvarni LCP potvrđen PerformanceObserver-om: prvi #heroCarousel img, desktop Cline WebP / mobile JPEG. Hero već eager/high, preload sada prije CSS-a (prethodni lokalni patch). Prethodni browser desktop hero preuzet približno na 3,0 s, prikazan tek 4,83 s: render delay je bitan.

12 stylesheet linkova, dvije desktop media datoteke. Glavni style.min.css 547.707 B raw, produkcijski transfer oko 89.740 B. Sve funkcionalne stilove sačuvati. Sigurno objediniti samo susjedne CSS linkove, ne pomjerati ih preko inline override-a ili template blokova jer se tako mijenja cascade. Dva core fajla i tri susjedna mobile/sheet fajla postaju dva bundle-a: tri zahtjeva manje. CSS pravila i relativni URL-ovi ostaju istim redom i iz istog direktorija. Ne uklanjati pravila na osnovu coverage-a početnog viewport-a: meniji, korpa i modali koriste ih kasnije.

Inter fontovi su self-hosted, latin + latin-ext za bosanska slova, font-display swap već postoji. Zadržati iste font fajlove i metriku. Preload samo regular 400 subseta koji se već koriste; ne preloadovati sve težine niti mijenjati font-family/font-display. Font-face ostaje u ranije otkrivenom core bundle-u; browser normalno učitava potrebne težine. Lokalni static server pri poređenju mora slati cache zaglavlja i kompresiju; ne zamijeniti realni GTmetrix dokaz sintetičkim benchmarkom.

## Minimalni patch i validacija

1. Generated CSS bundle-i od neizmijenjenih source datoteka, samo za nefiltriranu početnu; originalni linkovi za ostale stranice ostaju.
2. Jedno request-scope čitanje PageSEO i schema liste; bez novog globalnog cache-a i bez promjene admin invalidacije.
3. Test determinističkog CSS builda, svih font URL-ova, preload/cascade redoslijeda, SQL broja i izolacije između zahtjeva.
4. Prije/poslije isti HTML snapshot i javni resursi u browseru, desktop/mobile screenshot, computed styles i stylesheet request count. Ne garantovati 3×1,3 s uštede: HTTP/2 zahtjevi se preklapaju.

Očekivanje: tri manje početna CSS zahtjeva i dva manje warm SQL čitanja. Stvarna LCP/TTFB dobit mora biti ponovno izmjerena nakon odobrenog deploymenta; ovdje samo lokalni patch. Bez produkcijskih migracija, git push-a, deploymenta ili izmjena Cloudflare pravila.

Izvori: https://docs.djangoproject.com/en/dev/ref/middleware/ (SSL 301), https://groups.google.com/a/chromium.org/g/blink-dev/c/cAS525en8XE/m/5DZp8G5TAgAJ (interni 307), https://web.dev/articles/optimize-lcp (LCP faze), https://developers.cloudflare.com/cache/interaction-cloudflare-products/r2/ (R2 cache).

## Lokalni rezultati poslije izmjena

| Mjera | Prije | Poslije |
|---|---:|---:|
| Warm SQL upiti, isti QA fixture | 16 | 14 |
| Warm render, jedan lokalni uzorak | 12,92 ms | 11,11 ms |
| Cold SQL upiti | 30 | 27 |
| Cold render, jedan lokalni uzorak | 252,92 ms | 158,21 ms |
| Core CSS gzip, lokalna ista kompresija | 90.537 B | 90.185 B |
| Tri UI CSS gzip, lokalna ista kompresija | 11.507 B | 10.235 B |

Cold razlika uključuje template/compiler/cache inicijalizaciju; ne predstavlja pouzdan procenat ubrzanja. Warm SQL redukcija za dva upita je potvrđena: PageSEO i schema tables dijele request-scope rezultate. Broj upita ne može biti direktno prenesen na stvarni produkcijski katalog bez njegovog fixture-a.

11 ciljanih Django testova i system check prolaze. Test čuva ažurnost generated bundle-a bajt-po-bajt, gzip poređenje, relativne font reference, SEO svježinu između zahtjeva, originalni CSS za pretragu i hero preload redoslijed. Build komanda: `python3 scripts/build_home_css.py`, prije budućeg collectstatic-a ili kad se promijeni bilo koji source CSS iz BUNDLES. Na ostalim stranicama originalne datoteke ostaju.

Font optimizacija je zadržavanje postojećeg self-hosted/swap ponašanja i istog ranog otkrivanja font-face-a kroz bundle, uz hero prioritet prije font preloada. Nema manje font fajlova: to bi zahtijevalo novu verziju fonta/subseta i detaljniji test metrike. Ne tvrdi se ušteda na font bajtovima.

Direktni HTTPS URL potvrđeno vraća 200 bez HTTP redirecta, HTML DYNAMIC i HSTS. 307 ostaje pretpostavka dok se ne dobije originalni HAR. Cache pravila Cloudflare/R2 nisu mijenjana. Novi produkcijski LCP ili Fully Loaded nisu izmjereni jer nije urađen deployment.

Browser poređenje sa istim HTML snapshotom i lokalno zamijenjenim CSS-om potvrđuje **12 → 9 stylesheet zahtjeva** na desktopu i mobilnom. Aktivni render-blocking stylesheet linkovi: desktop 12 → 9, mobile 10 → 7. Font familije/učitane težine i konačni hero URL su isti. Viewport screenshot-i 390×900 i 1365×900 su **piksel-identični** kada oba prikaza koriste iste preuzete hero bajtove. Prvo poređenje s odvojenim mrežnim preuzimanjima hero-a imalo je male razlike samo unutar slike; kontrolisana provjera uklonila je taj faktor. Ovo je lokalni HTML/CSS patch uz javne resurse, ne produkcijski deployment niti mjerenje novog GTmetrix LCP-a.

Artefakti: `sql-before.json`, `sql-after.json`, `css-bytes.json`, `browser-comparison.json`, `visual-comparison.json`.
