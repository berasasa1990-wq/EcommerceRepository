# Izolovana produkcijska isporuka statike — 13.10.2026.

## Rezultat

Na istom zamrznutom lokalnom sadržaju, tri Lighthouse Mobile prolaza po varijanti daju medijan **61 → 81**. FCP je **5,26 → 2,26 s**, LCP **7,96 → 4,60 s**. Glavna potvrđena korist je ispravna komprimovana isporuka statike, bez promjene CSS deklaracija, fontova, slika ili dizajna. Ocjena 90+ nije potvrđena.

Implementirano je **samo izolovano lokalno testno okruženje**. Produkcijska konfiguracija, PostgreSQL, modeli, migracije, aplikacijski templatei, Meta/CAPI/Google i postojeće funkcije nisu mijenjani ovim zadatkom. Prethodne lokalne WMS, banner i header izmjene ostaju. Nema git add, push ili Render deploymenta.

## 1. Produkcija i postojeća konfiguracija

Pregledan je `LOCAL_LIGHTHOUSE_MOBILE_AUDIT.md`, Django settings, Render blueprint i WhiteNoise implementacija.

- `EcommerceProject/settings.py:264–266`: HTML gzip i WhiteNoise middleware.
- Settings 413–417, STORAGES oko 526/543: CompressedManifestStaticFilesStorage, static cache i media cache.
- `render.yaml:10–20`: minifikacija, collectstatic i Gunicorn 1 worker/4 threada. Blueprint uključuje i migrate; **build/deploy komande nisu pokretane**. Nije mijenjan taj fajl.
- Lokalni runserver/DEBUG=True staticfiles put presreće statiku prije aplikacijskog WhiteNoisea, kako je dokazano u prethodnom auditu.

Javni produkcijski HEAD za `home-core.bundle.03814f4f0871.css?v=20261012`:

- HTTP/2 200, Server cloudflare, x-render-origin-server gunicorn.
- Accept-Encoding gzip,br: **Content-Encoding gzip**, Content-Length **90.305 B**.
- Accept-Encoding br: **Content-Encoding br**. Content-Length nije dostavljen, pa Brotli veličina nije nagađana.
- `Cache-Control: max-age=315360000, public, immutable`, `Vary: Accept-Encoding`.
- `CF-Cache-Status: DYNAMIC` u oba uzorka.

Produkcija dakle već pruža gzip/Brotli i jak browser cache. DYNAMIC nije isto što i odsustvo browser cachea. Nema pristupa Cloudflare dashboard pravilima ili potvrde je li Brotli proizveden na edgeu ili originu; zabilježeno je stvarno HTTP ponašanje. Ne treba lokalni problem rješavati novim produkcijskim deploymentom koji ponovno „uključuje gzip”.

## 2. Izolovano okruženje — bez baze

Novi `scripts/mobile_static_qa_settings.py` koristi postojeće izolovane QA settings, DEBUG=False i CompressedManifestStaticFilesStorage. `STATIC_ROOT` je **/private/tmp/carp-mobile-static-qa/collected**, izvan projektnog staticfiles direktorija. collectstatic: 558 copied, 1368 post-processed, 428 conflicts skipped; standardno dupliranje finder imena nije prepravljano. Nema migracija. Stvarne integracije nisu učitane; eventualni Django testovi koriste samo izolovanu SQLite testnu bazu.

`scripts/mobile_static_snapshot_server.py` ne radi Django setup i ne uvozi modele. Poslužuje prethodno zamrznuti anonimni HTML, lokalnu statiku i media fajlove. Portovi:

- **8093 raw**: izvorni CSS/JS/font URL-ovi, bez komprimovanih static varijanti i eksplicitnog static cache lifetime-a.
- **8094 compressed**: stvarno prikupljena WhiteNoise statika, hashed URL-ovi iz manifesta, gzip pregovaranje i immutable cache za hashirane datoteke.

Obje varijante imaju isti gzip HTML i slike, HTML `no-store`, isti kod stranice. Jedine sadržajne zamjene su localhost port i static hash URL-ovi. Nema sesijske obrade, CAPI poziva ili poslovnih backend ruta. GET/HEAD su dozvoljeni; ostalo 405, nepostojeće rute 404. To je fixture za prvi prikaz, **nije funkcionalna zamjena webshopa**. Korpa/checkout i ostale funkcije provjeravaju se zasebno kroz stvarne Django QA testove.

Lokalni HEAD dokazi:

| Isporuka home-core | Encoded veličina | Encoding | Cache |
|---|---:|---|---|
| Raw 8093 | 553.036 B | nema | nema eksplicitnog Cache-Control |
| WhiteNoise 8094 | 90.305 B | gzip | public, immutable, max-age 315360000 |

Nema Playwright `fulfill` zamjene u Lighthouse mjerenju: browser stvarno preuzima HTTP odgovore lokalnih servera. WSGI transport je jednostavniji od Render/Gunicorn/Cloudflare HTTP/2 i nema TLS/CDN mreže. Backend TTFB je fixture trošak, bez SQL-a. Ovo izoluje statičku isporuku; ne predstavlja cjelokupnu produkcijsku brzinu.

## 3. Lighthouse Mobile prije/poslije

Alat je Lighthouse **13.5.0**, instaliran samo u `/private/tmp/carp-mobile-lighthouse`. Nisu promijenjeni requirements/npm projektni paketi. Svaki prolaz koristi novi izolovani headless Chromium/Brave profil; isti HTML/podaci, interleaved raw/compressed, tri uzorka po varijanti, bez runWarnings/runtimeError.

Lighthouse standardni mobile simulate: 412×823, DPR1,75, RTT 150 ms, throughput 1638,4 Kbps, CPU slowdown 4×. Ovo se razlikuje od prethodnog direktnog browser audita 390 px; unutar ovog poređenja uslovi su isti. JSON dokazi: `static-delivery/lighthouse-{raw,compressed}-{0,1,2}.json` i `median-summary.json`.

| Medijan 3 uzorka | Raw | WhiteNoise |
|---|---:|---:|
| Performance | **61** | **81** |
| FCP | 5.257,8 ms | 2.260,6 ms |
| LCP | 7.958,9 ms | 4.596,0 ms |
| TBT | 101,5 ms | 63,5 ms |
| Speed Index | 5.257,8 ms | 2.260,6 ms |
| CLS | 0,00006479 | 0,00006479 |
| Render-blocking insight: procijenjena FCP ušteda | 4.500 ms | 1.450 ms |

Insight uštede su Lighthouse procjene i ne sabiraju se s izmjerenim razlikama. Preostalo blokiranje i dalje postoji: CSS se ne parsira besplatno samo zato što je komprimovan. Novi CLS se zaokružuje na 0, ali nije matematička nula; medijan se nije povećao. Pojedinačni prolazi variraju i nalaze se u sirovom summary JSON-u.

Primjer render-blocking prvog para: home-core modelirano trajanje **4203 → 1204 ms**; bera-home **1803 → 604 ms**. To je dokaz da je lokalna razvojna isporuka značajan dio problema.

## 4. Cache — posebno mjerenje

`scripts/mobile_static_cache.cjs`: direktno browser throttling, 390×844 DPR3, CPU4×, latency150 ms, download200.000 B/s. Cold i odmah ponovljeni warm odlazak, po jedan uzorak, odvojeno od Lighthouse simulated rezultata.

| Varijanta | Cold FCP/LCP | Warm FCP/LCP |
|---|---|---|
| Raw | 5.544 / 5.740 ms | 232 / 372 ms |
| Compressed | 1.760 / 1.868 ms | 208 / 368 ms |

Compressed cold static transfer: 361.429 B uključujući fontove i JS; warm static transfer 0. Raw warm je također 0, jer browser može heuristički keširati Last-Modified/validator odgovore i bez eksplicitnog max-age. Zato se **ne tvrdi da samo dodavanje cache zaglavlja uzrokuje cijeli warm dobitak**. Eksplicitni immutable lifetime pruža jasnu dugoročnu politiku za hashed fajlove; glavni potvrđeni cold dobitak ovdje je kompresija. Single warm uzorci nisu Lighthouse ocjena ni garancija za korisnika.

## 5. CSS: kritična pravila i sigurnost

Broj fajlova ostaje **9**. Nisu spojeni niti uklonjeni. Byte sadržaj pravila ostaje isti, osim collectstatic URL hash zamjena.

Početni 390 px CDP CSS rule coverage, nakon font-ready, bez otvaranja modala/menija:

| CSS | Aktivni/used rasponi u ovom toku |
|---|---:|
| home-core | 9,11% |
| home-ui | 49,30% |
| bera-home | 60,63% |
| navigation | 2,58% |
| scratch-game | 4,79% |
| offer-popup | 25,33% |
| storefront-extras | 0% |
| desktop-reference/header-reference | 0% na mobilnom |

Dokaz: `initial-css-coverage.json`, s raw used ranges i primjerima stvarnih selektora. Ovo mjeri matched/applied rule raspon kroz početni tok, **nije tačan minimalni critical CSS**: obuhvata pravila primijenjena i na skrivene/below-fold elemente, a propušta interakcije. Nije dokaz da je preostalo pravilo sigurno obrisati.

Kritične grupe koje moraju biti očuvane:

- Sticky chrome, spacer/offset CSS varijable i promo/header box geometrija: inline `site-chrome-layout` u `bera_reference/base.html` oko 333, mjerenje oko 764.
- Logo, search, header ikonice i akcije: home-core/header-base i mobile override-i u home-ui/bera-home.
- Prvi mobilni category track i vidljive benefit kartice: `partials/home_mobile_navigation.html`, home-ui, bera-home.
- Prve product kartice kada nema hero: `.product-card`, `.product-image`, cijena/naslov, badge i CTA; home-core i bera-home. Lighthouse vidi LCP na kartici top421 px, pa bez-hero critical CSS mora uključiti kartice, ne samo banner.
- Kada hero postoji: `.hero-carousel/.slide-image/picture` geometrija iz home.html i bera-home, s odgovarajućim rezervisanim dimenzijama.

Preporuka, još ne implementirana: zaseban critical CSS za hero/no-hero slučaj, tek nakon svih interakcija i computed-style/screenshot provjere; ne izdvajati trenutno neaktivne rule range-ove kao „nepotrebne”. Fontovi ostaju isti, uključujući bosanske znakove i sve težine.

## 6. LCP bez hero bannera

Lighthouse potvrđuje IMG `FT-1 push-lock Rigs 12#`, selector `div.product-card__media > div.product-image > a.product-card__media-link > img`, prirodni izvor `/media/products/ft-1-push-lock-rigs-12.avif`. Bounding rect u LH mobile: top421, width163, height120.

LCP discovery checklist prolazi sve tri provjere: eager, fetchpriority high, discoverable u početnom HTML-u. Zato prioritet nije naslijepo dodatno podizan. Nije pronađen preload skrivenog hero fajla u ovom lokalnom HTML-u. Postoji **product fallback preload za drugi proizvod G9**, s hardkodiranim hero imagesizes; to je stvarni zaseban problem iz prethodnog audita.

Nije globalno brisan preload niti mijenjan `home.html`. Backend bira preload prije curated homepage zamjena, a njegov image_sizes se ne koristi u ovom head bloku. Preporuka: uskladiti preload s konačnim prvim vidljivim proizvodom i istim srcset/sizes kao kartica, uz poseban hero/grid/product put. Na nekim širinama LCP kandidat može biti drugi element, pa to treba testirati na svim širinama prije runtime izmjene.

Lighthouse `lcp-breakdown-insight` u simulate modu prikazuje podfaze iz stvarnog prikupljenog, nethrottlovanog tracea; one se ne sabiraju u simulated LCP 4,60 s. Primjer compressed prvog uzorka: TTFB13,379 ms, resource delay15,187 ms, duration19,398 ms, render106,219 ms. Te brojke nisu zamjena za prethodni direktni throttled breakdown 6,26 s. Nema tvrdnje da je sam preload uzrok svih preostalih 4,60 s.

## 7. Vizuelna i funkcionalna provjera

360/390/430/1440 px: geometrija poređenih headera, spacera, kategorija, product sekcija i kartica je jednaka. Screenshotovi su identični na 360/430/1440. Prvi 390 par imao je 1.342 različita piksela bez geometrijske razlike; ponovljeno nakon dodatnog stabilizovanja fontova ostala su **dva piksela na jednom redu obruba**. Pregledana su oba 390 screenshot-a: nema promjene boje, teksta, rasporeda ili vidljivog dizajna. Ne predstavlja se kao doslovna jednakost svakog piksela. Dokazi: `visual-summary.json`, `390-recheck/`.

- Django system check s izolovanim production-static settings: prolazi. Postoji očekivana konfiguracijska email poruka jer je DEBUG=False a slanje u QA isključeno; nije runtime isporuka stvarnih emailova.
- **65 postojećih Django regresionih testova prolazi**, 9,766 s: početna/hero/preload, kategorije, pretraga, „Prikaži više”, korpa/dostupnost, checkout/kupci, nalog/reklamacije, Meta događaji i Purchase event_id, WMS alati i tri Monri testa.
- **4 read-only harness testa prolaze**: zabranjeni write methods/backend rute, manifest URL zamjene i HTML no-store/no-cookie, gzip ista CSS pravila/immutable cache, HEAD i HTML encoding pregovaranje.
- JS syntax i git diff --check prolaze. Nije pokrenut kompletan projektni suite niti testirano stvarno plaćanje.

Meta Pixel, CAPI i Google nisu mijenjani. Frozen fixture nema poslovni backend; nije upotrijebljen da se tvrdi stvarna isporuka Purchase događaja. Funkcionalna zaštita je u zasebnim Django QA testovima.

## 8. Tačne nove datoteke

- `scripts/mobile_static_qa_settings.py`: DEBUG=False, izolovani collectstatic /tmp cilj.
- `scripts/mobile_static_snapshot_server.py`: DB-free raw/WhiteNoise HTTP fixture.
- `scripts/mobile_static_lighthouse.mjs`: 3+3 stvarna Lighthouse Mobile testa.
- `scripts/mobile_static_cache.cjs`: cold/warm browser cache mjerenje.
- `scripts/mobile_static_visual.cjs`: četiri širine i CDP CSS coverage.
- `scripts/test_mobile_static_snapshot.py`: četiri safety/delivery regresije.
- `reports/performance/static-delivery/`: Lighthouse JSON, summary, coverage, cache i screenshot dokazi.
- ovaj izvještaj.

Nema novih izmjena runtime application templatea, settings.py, render.yaml, requirements, modela ili migracija iz ovog zadatka.

## 9. Reprodukcija

Snapshot HTML mora biti prethodno sačuvan anonimni lokalni home HTML, npr. postojeći `/private/tmp/local-mobile-home.html`. Ne koristiti fixture kao checkout/nalog server.

```sh
venv/bin/python manage.py collectstatic --settings=scripts.mobile_static_qa_settings --noinput
venv/bin/python scripts/mobile_static_snapshot_server.py --html /private/tmp/local-mobile-home.html
# U drugom terminalu; test dependencies ostaju isključivo u /tmp:
npm install --prefix /private/tmp/carp-mobile-lighthouse lighthouse --no-audit --no-fund
node scripts/mobile_static_lighthouse.mjs
node scripts/mobile_static_visual.cjs
node scripts/mobile_static_cache.cjs
venv/bin/python scripts/test_mobile_static_snapshot.py
```

Playwright/BROWSER_EXECUTABLE/LIGHTHOUSE_MODULE putanje imaju override kroz environment u skriptama. Nisu proizvodne start komande. Izolovani portovi poslije mjerenja se zaustavljaju; postojeći runserver 8000 ostaje netaknut.

## Preporuke, bez automatske primjene

1. Mjeriti stvarnu produkcijsku mobile stranicu s istim sadržajem kao lokalni fixture; produkcija već komprimuje CSS i stoga ne treba očekivati još +20 bodova od istog poteza.
2. Critical CSS/interaction coverage prije razdvajanja core-a. Najveći prostor je 553 KB dekompresiranog core-a, ali initial-unused pravila nisu automatski nepotrebna.
3. Ciljana preload korekcija poslije konačne curated selekcije proizvoda, s testom hero/no-hero i svih veličina; ne globalno uklanjanje.
4. Procijeniti Cloudflare cache pravilo samo za hashiranu javnu statiku ako DYNAMIC ostaje usko grlo; nikakvo cache-everything za sesiju/HTML/korpu.
5. Server SQL/Render TTFB ostaju zaseban problem koji DB-free fixture namjerno ne mjeri. Ne tvrditi da se ovim ubrzao Django/PostgreSQL.
6. Stvarna produkcijska ocjena 90+ i potpuni mobile Safari rezultat još nisu potvrđeni.
