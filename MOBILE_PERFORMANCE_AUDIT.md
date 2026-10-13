# Mobilni performance audit — CarpologijaBH

Datum: 13.10.2026. Opseg: javna početna stranica, mobilni početni prikaz i aktivni Django kod. Nisu mijenjani aplikacija, dizajn, baza, migracije ni deployment. Prethodne lokalne izmjene ostavljene su netaknute.

## Sažetak

Najvažniji nalazi su konkurencija resursa prije prikaza mobilnog hero bannera, količina render-blocking CSS-a i fontova, te rad headera i Meta Pixela na glavnom threadu. Stare arhivske datoteke same po sebi ne usporavaju browser. Aktivna stranica sada učitava samo `brand-logo-normalize.js`, bez druge skripte za iste logotipe.

90+ je razuman cilj za kontrolisani Lighthouse test, ali nije garantovan. Najprije treba popraviti potvrđene troškove, ponoviti najmanje tri testa u istim uslovima i zadržati CLS 0. Nije opravdano obećati konkretan dobitak bez A/B mjerenja.

## Metodologija i granice dokaza

Korisnički Lighthouse rezultat: desktop 92; mobile 67, FCP 2,6 s, LCP 5,1 s, TBT 350 ms, Speed Index 4,5 s, CLS 0. Nije dostavljen Lighthouse JSON/trace, pa iz samih tih brojeva nije moguće rekonstruisati taj test.

Novo nezavisno javno browser mjerenje: Chromium/Brave, izolovan kontekst bez prijave, viewport 390×844, DPR 3, mobilna emulacija, CPU usporenje 4×, mrežna latencija 150 ms, download 200.000 B/s, upload 93.750 B/s. Jedan hladan browser prolaz, bez skrolovanja; posmatranje do četiri sekunde nakon load. Ovo je dijagnostički uzorak, **nije Lighthouse**, nije fizički telefon i ne koristi Lighthouse simulaciju mreže. Server/Cloudflare cache nije namjerno pražnjen.

Dokaz prikupljen PerformanceObserverom za LCP i long tasks, Resource Timing API-jem i Chrome DevTools CPU profilerom. Sirovi lokalni trag: `/private/tmp/carp-mobile-audit.json`; javni HTML: `/private/tmp/carp-mobile-live.html`. Privremene datoteke nisu trajni repozitorijski artefakti. Nisu pokretane Django view funkcije nad produkcijskom bazom. Raniji lokalni SQL profili su jasno odvojeni od sadašnjih javnih mjerenja.

| Mjera novog prolaza | Rezultat |
|---|---:|
| Navigation responseStart od početka navigacije | 1.182 ms |
| Load | 7.806 ms |
| Posljednji posmatrani LCP | 6.372 ms |
| Long tasks | 108, 144 i 128 ms |
| Zbir dijelova ovih taskova preko 50 ms | 230 ms |
| HTML encodedBodySize | 33.678 B |
| CSS zahtjevi / transferSize | 9 / 135.761 B |
| JS zahtjevi / transferSize | 14 / 152.179 B |
| Font zahtjevi / transferSize | 7 / 186.692 B |

230 ms nije Lighthouse TBT: prozor prikupljanja nije Lighthouse FCP–TTI prozor. Resource Timing podaci za cross-origin slike imaju nulte veličine bez potrebnog Timing-Allow-Origin pristupa; nula **ne znači** da nisu preuzete. Broj/font transfer uključuje stvarno opažene resurse; nije teorijska veličina svih fajlova na disku.

## P1 — Stvarni LCP, hero i konkurencija mreže

**Potvrđeni LCP element:** `IMG.slide-image-img` u `#heroCarousel`, izvor `https://media.opremazaribolov.ba/banners/mobile/cline_travel_bag.jpg`. Browser naturalWidth×naturalHeight: **670×884**, loading=eager, fetchpriority=high. Kandidati prije njega bili su naslov sekcije i logo; posljednji posmatrani LCP je hero slika.

Tačne lokacije:

- `EcommerceApp/template/bera_reference/home.html:126`: blok preload; mobilni preload na liniji 130, desktop preload na 132.
- Isti fajl, linije 151–169: picture/source za mobile ≤768 px; source URL odgovara odabranoj slici. Prvi slide ima eager/high na liniji 166. Sljedeći slideovi imaju lazy. Hero HTML je serverski generisan i ne čeka JavaScript da bi postojao.
- `EcommerceApp/views.py:2378`: `_banner_to_hero_slide`, mobilni meta podaci 2387–2399.
- `EcommerceApp/utils/images.py:785`: `banner_image_responsive_meta`; linija 796 postavlja `hero_mobile: ()`, tj. mobilne responsive varijante se ne generišu kroz ovu konfiguraciju.
- `EcommerceApp/template/bera_reference/base.html:7`: R2 preconnect; viewport je prije preload oznaka.

**Preload je sada ispravno usmjeren:** javni HTML ima mobile `(max-width: 768px)` i desktop `(min-width: 768.01px)`; mobilni URL jednak je currentSrc. Zato nije potvrđen pogrešan preload URL.

**Ipak postoje zahtjevi za obje hero slike u mobilnom tragu:** desktop WebP počinje na 1.230 ms i završava na 3.579 ms; mobile JPEG počinje na 1.264 ms i završava na 6.352 ms. Desktop slika nije currentSrc konačnog mobilnog elementa. Nema ponavljanja identičnog URL-a u Resource Timing listi, ali postoji konkurentno učitavanje dvije varijante. Ne treba automatski okriviti preload: za konačan uzrok potrebni su Network initiator i CDP loadingFinished/encodedDataLength, te ponovljen test. Mogući rani fallback/speculative zahtjev mora se dokazati prije prepravke markup-a. Postojeći trag `reports/performance/hero-selection-check.json` također je ranije evidentirao oba hero URL-a na mobile viewportu; to nije samo zaključak iz statičkog koda.

**Cache stvarnog LCP URL-a**, javni HEAD 13.10.2026: HTTP 200; image/jpeg; Content-Length **181.797 B** (~177,5 KiB); `Cache-Control: public, max-age=31536000, immutable`; `CF-Cache-Status: HIT`; age 128.345 s. Ranije pregledani `slika_torbe_cline.jpg` ima 193.094 B, ali **nije** currentSrc ovog prolaza. Ne treba zamijeniti te dvije slike u zaključcima.

Preporuke:

1. P1: ponoviti cold-mobile trace s inicijatorima i stvarnim byte countom za desktop fallback; ukloniti desktop preuzimanje na mobile tek kada se potvrdi put koji ga pokreće. Bez promjene vidljive slike ili dimenzija.
2. P1: napraviti probni WebP/AVIF iste mobilne slike i vizuelno uporediti pri DPR 1/2/3. Trenutnih 670 px već je manje od 390×3, pa ne treba naslijepo dodatno smanjiti rezoluciju i pokvariti oštrinu. Izabrati format tek nakon dokaza veličine i vizuelne jednakosti.
3. P2: provjeriti isplativost mobilnog srcset-a za različite uređaje. Trenutno je jedna datoteka; samo dodavanje sizes bez srcset-a neće smanjiti download. Preload imagesrcset/imagesizes mora biti identičan source izboru.
4. P2: kategorijske ikonice u `partials/home_mobile_navigation.html:14` imaju loading=eager i deklarisano 256×256, iako su vidljivo male. Odložiti samo horizontalno nevidljive ikonice, zadržati vidljive odmah dostupne. Mjeriti dobitak i rad swipovanja; ne pretpostavljati da lazy automatski izbjegava sve offscreen zahtjeve.

## P2 — CSS, cascade i fontovi

Aktivna početna učitava devet CSS resursa. `desktop-reference.css` i `header-reference.css` imaju desktop media uslov: mogu biti preuzeti i na mobilnom, ali njihov nepodudarni media uslov ne treba opisivati kao isto render-blocking ponašanje kao aktivni mobilni stylesheeti.

Lokacije u `template/bera_reference/base.html`:

- 15: scratch-game stylesheet bez media uslova.
- 88: desktop-reference, desktop media.
- 170–171: dva preloada Inter 400 Latin/Latin-ext.
- 173–174: home-core bundle preload + stylesheet. To nisu dvije automatski nezavisne preuzete kopije; browser može iskoristiti preload.
- 184, 186: navigation i storefront-extras.
- 225: home-ui bundle; alternative na 227–229 su u else grani, ne učitavaju se zajedno na optimizovanoj početnoj.
- 233: header-reference s desktop media.
- 369–370: bera-home i popup CSS.
- 333: postojeći inline `site-chrome-layout`; critical CSS već djelimično postoji.

Lokalne veličine prije kompresije: home-core **553.036 B**, home-ui **66.507 B**, bera-home **87.460 B**. U browseru zbir CSS encodedBodySize je 133.061 B. Kompresija pomaže mreži, ali browser i dalje parsira dekompresirane stilove.

` scripts/build_home_css.py:6` definiše byte-preserving bundle sadržaj: home-core spaja style.min + header-base.min; home-ui spaja home-mobile-reference + mobile-dock + wishlist. Ne uklanjati source fajlove kao „duplikate” jer su ulaz builda i fallback drugih stranica.

**Potvrđeno preklapanje cascade-a:** `bera-home.css:198,220,228,238,242,256,273,278,299,301` ponavlja pravila za offer bannere kroz različite breakpoint-e i kasnije override-e. `partials/home_mobile_navigation.html:58` sadrži starije dvoredno mobile grid pravilo, dok završni bera-home stilovi regulišu trenutni raspored. Ovo su mjesta za konsolidaciju, ne dokaz da su sve deklaracije suvišne. Brisanje ranijih pravila bez izračunavanja konačnog stila može promijeniti dizajn.

Preporučeni critical CSS: isključivo konačna mobilna geometrija promo trake/headera, logo/search/cart prostora, sticky offseta, prvog hero picture-a i rezervisanih dimenzija. Izvesti kroz coverage + computed-style/screenshot provjeru, ne ručno nagađati. Odgoditi donje sekcije i popup/scratch stilove tek uz dokaz da nema FOUC, pomjeranja ili kasne prve interakcije. Ne prebacivati cijeli core bundle na async prije takve provjere.

Fontovi: `static/bera_reference/css/fonts.css:1` i naredna @font-face pravila imaju `font-display: swap`, unicode-range i odvojene težine. Sedam učitanih fontova prenijelo je oko 187 KB, više nego CSS. Predložiti test varijabilnog Inter fonta ili manjeg skupa stvarno korištenih težina/subsetova. Sačuvati metriku i bosanska slova; ne uklanjati Latin-ext samo zato što postoji Latin. Provjeriti CLS i bold izgled svih sekcija. Preload svih težina nije preporučen.

## P3 — JavaScript i long tasks

CPU profil mobilnog prolaza:

- Inline `measure`, javni HTML linija 3407: **74,0 ms sampled self time**. Lokalni izvor `template/bera_reference/base.html:771–799`: tri getBoundingClientRect čitanja, CSS varijable, ResizeObserver, resize/load/font ready. Već postoji requestAnimationFrame grupisanje i provjera promijenjene vrijednosti. Profil pokazuje trošak, ali ne daje pravo da se observer ukloni: sticky header i CLS zavise od njega.
- Meta `fbevents.js` funkcije u vodećim hotspotovima: pojedinačno 31,2, 28,9 i 13,7 ms; signal-config funkcija 11,7 ms. To su sampled self vremena različitih funkcija, ne ukupni wall time Meta skripte niti direktna atribucija svakog long taska.
- `main.min.js` visibleCount: 7,2 ms među vodećim lokalnim funkcijama.

Meta Pixel lokacije `base.html:247–329`; requestIdleCallback timeout 2.500 ms na 324. Google Analytics/Ads `base.html:68–81`; idle timeout 3.500 ms. Idle callback nije garancija izvršavanja nakon LCP-a: može se pokrenuti prije dovršetka slike, a timeout može forsirati rad na zauzetom uređaju. Ne odlagati naslijepo do prve interakcije jer time mijenjamo PageView/konverzije. Predložiti kontrolisani raspored s očuvanim redom i deduplikacijom događaja i testovima checkout/purchase toka.

`base.html:861–866` učitava defer core i logo/navigation/notify skripte; scratch-game je na 911; catalog-load-more na 920. Home mobile navigation uključuje svoju defer skriptu u `partials/home_mobile_navigation.html:27`. Defer čuva parsiranje, ali izvršavanje i inicijalizacija i dalje mogu stvarati taskove prije LCP-a.

`static/bera_reference/js/main.js:839,980` pokreće carousel intervale; ne uklanjati carousel funkcionalnost. `almost-cart.js:157` i `mobile-dock.js:336` imaju MutationObserver-e. Prisustvo observera/intervala samo po sebi nije dokaz da je baš to usko grlo; potrebna je atribucija na realnom toku dodavanja u korpu.

`brand-logo-normalize.js:3–37` ima cache po URL-u, ograničenu canvas analizu i IntersectionObserver na 55. Druga logo skripta nije u aktivnoj listi učitanih skripti. Ugrađeni tekstualni offer banneri imaju 871 B HTML-a, bez slika i JS-a; nisu glavni kandidat za optimizaciju.

Preporuke: P1 izmjeriti smanjenje header layout poziva uz isti sticky rezultat; P1 izdvojiti Meta trošak kontrolisanim A/B blokiranjem samo u test browseru, bez promjene produkcije; P2 lazy-init scratch/popup dijelova kada postanu relevantni, uz zadržavanje triggera i događaja. Search, meni, korpa i mobilna navigacija moraju odmah raditi. Ovdje nisu sprovedeni A/B eksperimenti ni funkcionalne izmjene.

## P4 — Backend, template i cache

Javni HEAD početne: HTTP 200, `CF-Cache-Status: DYNAMIC`, `Vary: X-Catalog-Fragment, Cookie, Accept-Encoding`, CSRF Set-Cookie, x-render-origin-server gunicorn. Potvrđeno je da taj HTML odgovor nije Cloudflare HIT. **Ne keširati cijeli personalizovani HTML** niti ignorisati Cookie/Vary/CSRF.

Nav responseStart 1.182 ms uključuje mrežu, DNS/TLS gdje primjenjivo i odgovor servera; nije izmjereno čisto Django vrijeme. HEAD nije mjerenje punog GET template troška. Nema pristupa Render CPU/restart/queue podacima ili PostgreSQL EXPLAIN iz ovog audita.

Lokalni kod: `views.py:2771` home; grupni banner query 2774–2778; render 3076. `views.py:2438–2452` javne sekcije TTL 180 s. `EcommerceProject/settings.py:88–90`: LocMemCache, po procesu; cache se gubi restartom. `settings.py:327`: PostgreSQL conn_max_age 600 već postoji. `render.yaml:9,20` navodi free plan i 1 worker/4 threada, restart prag 400+jitter 50; to je deklaracija u repozitoriju, ne potvrda trenutno primijenjenog Render plana.

`views.py:2649–2655` izvršava product queryset po brand showcase sekciji na hladnom cacheu. Historijski izolovani SQLite profil `reports/performance/optimization-2/app-after.json`: cold 268,604 ms / 50 upita / 5,318 ms SQL; warm 12,936 ms / 14 upita / 0,712 ms SQL; HTML 90.145 B. Brand sekcije cold imaju 19 upita. Ovo su **raniji fixture podaci**, ne sadašnja produkcija ni novi backend benchmark. Pokazuju gdje mjeriti, ne da PostgreSQL traje jednako.

P1: prikupiti Render server-timing/APM i query latencije za anoniman cold/warm GET bez zapisivanja korisničkih podataka. P2: objediniti upite za brand sekcije uz očuvanje redoslijeda i dostupnosti, tek nakon potvrde troška. P2: procijeniti dijeljeni cache javnih sekcija uz eksplicitnu invalidaciju; nikakvo keširanje korpe/cijena po korisniku u zajednički HTML.

R2 `storage_backends.py:58–59` postavlja godinu dana immutable cache, potvrđeno na stvarnom hero URL-u. Izmijenjene slike moraju dobiti novi URL/ključ; prepisivanje istog immutable URL-a može ostaviti staru sliku godinu dana. Nije mijenjana storage konfiguracija.

## Prioriteti i prihvatni kriteriji

1. P1: inicijator oba hero zahtjeva, iste mobilne slike i prioriteta; cilj jedan potreban hero download na mobile. Zatim vizuelno jednaka efikasnija kodifikacija slike ako A/B pokaže dobitak.
2. P1: mobilni CSS coverage, critical header/hero i odgođeni sporedni CSS bez promjene computed stilova; smanjiti konkurenciju fontova/ikonica.
3. P1: header i Meta long-task A/B; očuvati sva mjerenja konverzija, sticky geometriju i odmah dostupne kontrole.
4. P2: cold/warm backend profil, brand sekcije i stabilnost cachea; bez promjene baze i migracija.

Za svaku buduću izmjenu: najmanje tri identična Lighthouse mobile testa, medijan FCP/LCP/TBT/Speed Index, ista verzija browsera, mreža i CPU; posebno hladan/topao browser i server cache. Screenshot poređenje na 360/390/430 px i desktopu; CLS 0; meni/search typo/kategorije/swipe/korpa/notify/checkout/Monri/nalog/SEO canonical regresije. Provjeriti inicijatore i byte count slika i fontova, te da nema izgubljenih Meta/Google događaja. Nema pouzdane procjene bodova pojedinačne optimizacije prije takvog eksperimenta.

## Šta nije urađeno

Nisu mijenjani kod, CSS/JS, dizajn, DB, podaci, migracije, infrastruktura niti deployment. Nije pokrenuta kompletna aplikacijska test baterija jer nema implementacije. Nije potvrđen Lighthouse LCP breakdown iz korisnikovog testa, CSS coverage po svim interakcijama, novi PostgreSQL query plan, niti fizički mobile Safari/Chrome rezultat. Audit navodi te granice umjesto nagađanja.
