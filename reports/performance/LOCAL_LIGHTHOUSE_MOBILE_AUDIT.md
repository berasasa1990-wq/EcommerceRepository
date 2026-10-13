# Lokalni Lighthouse Mobile audit — CarpologijaBH

Datum: 13.10.2026. Testirani URL, potvrđen od korisnika: `http://127.0.0.1:8000/`.

## Zaključak

**Potvrđeno usko grlo lokalnog prikaza je nekomprimiran render-blocking CSS koji služi Django razvojni staticfiles handler.** HTML kompresija već radi. Lokalni izmjereni TTFB nije glavni uzrok višesekundnog čekanja. Trenutna početna nema hero banner; LCP u novom prolazu je slika artikla FT-1 push-lock Rigs 12#, pa ovo nije isti sadržaj kao raniji produkcijski hero audit.

Ne treba iz lokalne ocjene 62 zaključiti da produkcija ima iste performanse. Potvrđeno je lokalno razvojno posluživanje, ali postoji i stvarni problem u aplikaciji: bez hero bannera preload koristi hardkodirani hero `imagesizes` umjesto kontekstualne širine odabrane slike proizvoda. To je odvojeno od manjka kompresije i zaslužuje zasebnu, ciljanu provjeru.

Nisu mijenjani aplikacija, stilovi, funkcionalnosti, baza, migracije ili deployment. Izmijenjeni su samo izvještaj i dokazni JSON artefakti. Prethodne lokalne izmjene ostavljene su netaknute.

## 1. Šta tačno znamo o Lighthouse rezultatu

Korisnik navodi Performance 62, FCP 5,3 s, LCP 9,2 s, TBT 0 ms, CLS 0; render-blocking procjena 4.280 ms, cache lifetimes 1.144 KiB, image delivery 191 KiB. Originalni Lighthouse JSON/HAR/trace nije dostavljen niti pronađen pretragom projekta. Ne možemo izračunati tačne podfaze baš tog LCP-a 9,2 s ili identifikovati sve stavke baš njegovih audit lista.

Prikupljeno je novo nezavisno mobilno mjerenje u izolovanom Chromium/Brave kontekstu: viewport 390×844, DPR3, mobilna emulacija, CPU 4×, mreža 150 ms latency, download 200.000 B/s, upload 93.750 B/s. GET početne i resursa, bez prijave; POST zahtjevi su blokirani. PerformanceObserver i Resource Timing daju LCP i faze, CDP daje HTTP zaglavlja. Ovo nije Lighthouse niti njegov simulirani throttling model. Rezultati služe dijagnozi, ne zamjeni dostavljene ocjene.

Dokazi: `local-lighthouse/browser-measurement.json`, `css-compression-experiment.json`. Originalni lokalni HTML i zaglavlja su u privremenim `/private/tmp/local-mobile-home.html` i `/private/tmp/local-mobile-html.headers`; nisu dodani u repozitorij jer sadrže sesijske/CSRF vrijednosti.

## 2. Lokalno okruženje — potvrđeno

- Server proces: `Python manage.py runserver`, PID 42720 u trenutku provjere.
- HTTP Server: `WSGIServer/0.2 CPython/3.14.5`; HTTP/1.1.
- `.env:32`: `DEBUG=True`; `EcommerceProject/settings.py:85` učitava tu vrijednost. Nisu čitane/izlagane druge tajne niti uvezeni default Django settings u novi Python proces.
- HTML GET s `Accept-Encoding: gzip`: 200, `Content-Encoding: gzip`, približno 24.589 B preko mreže, curl TTFB 106,365 ms. Dekompresirani HTML je oko 161 KB; browser uzorak je 163.051 B uz uobičajene dinamičke razlike.
- Browser navigation responseStart: 128,1 ms. To nije posebno izmjeren Django SQL/template time; uključuje cijeli browser/server put.
- CSS HEAD uz `Accept-Encoding: gzip, br`: 553.036 B, nema Content-Encoding, Cache-Control ni Vary: Accept-Encoding.
- Font HEAD: 21.564 B, nema Cache-Control. WOFF2 je već komprimiran font format; problem nije odsustvo dodatnog gzipa za WOFF2.

### Zašto WhiteNoise postavke ne pomažu ovdje

`EcommerceProject/settings.py:264–266` već registruje HTML gzip i WhiteNoise; linije 413–417 i STORAGES oko 526/543 koriste CompressedManifestStaticFilesStorage i godinu dana static cachea.

Međutim, instalirani Django kod u `venv/lib/python3.14/site-packages/django/contrib/staticfiles/management/commands/runserver.py:34–35` umotava aplikaciju u StaticFilesHandler kada je DEBUG uključen. `staticfiles/handlers.py:78–81` presreće static putanju prije poziva aplikacije. Zato ti static odgovori zaobilaze aplikacijski middleware/produkcijski put.

`EcommerceApp/middleware/gzip_html.py:9–11` namjerno komprimira samo HTML. To nije dokaz da produkcijski CSS nema kompresiju: za njega je konfigurisan WhiteNoise compressed static storage. U development runserver putu kompresija statike se ne ostvaruje automatski samo zbog te konfiguracije.

Raniji javni audit je za CSS vidio oko 133 KB encoded sadržaja, dok isti lokalni skup sada ima oko 800 KB. To je historijsko poređenje, ne novi istovremeni production benchmark.

## 3. Konkretni CSS resursi i render blokiranje

Vrijeme je od početka navigacije u novom direktnom lokalnom browser uzorku. Svi navedeni odgovori imaju encodedBodySize=decodedBodySize: CSS nije komprimiran.

| Resurs u `/static/bera_reference/css/` | Bajtovi | Početak ms | Kraj ms | Aktivno mobile blokiranje |
|---|---:|---:|---:|---|
| scratch-game.css | 7.015 | 226,5 | 564,0 | da, stylesheet bez media uslova |
| home-core.bundle.css | **553.036** | 229,7 | **5.439,8** | da, dominantan resurs |
| navigation.min.css | 1.396 | 229,8 | 458,7 | da |
| storefront-extras.min.css | 9.703 | 229,9 | 707,0 | da |
| home-ui.bundle.css | 66.507 | 229,9 | 2.252,9 | da |
| bera-home.css | 87.460 | 230,6 | 2.537,0 | da |
| bera-offer-popup.css | 9.194 | 230,6 | 1.103,4 | da |
| desktop-reference.css | 58.052 | 569,0 | 7.007,2 | **ne**: media min-width 1025 px |
| header-reference.css | 8.023 | 571,8 | 6.016,8 | **ne**: media min-width 1025 px |

Ukupno devet CSS resursa: **800.386 B** (~781,6 KiB). Aktivnih sedam: 734.311 B. Desktop media CSS može trošiti mrežu iako se ne primjenjuje; ne treba ga pogrešno označiti kao aktivni mobile render blocker. Preload + stylesheet za isti home-core nije dokaz dvostrukog downloada; resource lista bilježi jedan home-core resurs.

Lokacije: `template/bera_reference/base.html:15,88,173–186,225,233,369–370`. Stilovi u headu prethode prvom prikazu. Postojeći inline `site-chrome-layout` oko linije 333 pokriva dio geometrije, ali ne omogućava identičan početni izgled bez cijelog aktivnog CSS-a. Inline stilovi početne su i u `home.html:7`; nema dokaza da je sigurno ukloniti bilo koji potrebni blok.

Nije urađeno naslijepo spajanje ili brisanje CSS-a. Zbog isporuke 553 KB preko emulirane spore mreže, lokalno čekanje glavnog CSS-a samo po sebi objašnjava velik dio render-blocking upozorenja. Lighthouse procijenjenih 4.280 ms nije precizno sabiranje trajanja datoteka niti garantovana ušteda.

## 4. LCP element i breakdown novog mjerenja

Hero: `document.querySelector('#heroCarousel')` ne postoji. LCP kandidati su logo na 5.896 ms i konačno slika artikla:

`/media/products/ft-1-push-lock-rigs-12.avif`, IMG data-main-image, 600×600 deklarisano, eager/high, sizes `(max-width:768px) 50vw, 20vw`. Lokalni HTML linija 1826; isti artikal postoji i kasnije u drugoj sekciji, lazy/low, na 3116. LCP URL se jednom pojavljuje u Resource Timing listi; ponovljeno pojavljivanje proizvoda u HTML-u nije samo po sebi dupli mrežni download.

Konačni LCP: **6.256 ms**, poslije CSS-a koji završava na 5.439,8 ms.

Resource Timing detalji: fetch start 277,3 ms; requestStart 947,4 ms; responseStart 948,2 ms; responseEnd 1.809,3 ms. Slika je 24.809 B. LCP observer image loadTime je 6.066,1 ms, renderTime 6.256 ms; loadTime događaja elementa nije isto što i mrežni responseEnd.

### Mrežna definicija s requestStart

| Podfaza | Trajanje |
|---|---:|
| TTFB: navigation responseStart | **128,1 ms** |
| Load delay: image requestStart − TTFB | **819,3 ms** |
| Load duration: responseEnd − requestStart | **861,9 ms** |
| Render delay: LCP − responseEnd | **4.446,7 ms** |
| Zbir | **6.256,0 ms** |

Ako se kao početak zahtjeva koristi fetch start/kreiranje zahtjeva umjesto requestStart, load delay je 149,2 ms, a load duration 1.532,0 ms jer uključuje oko 670,1 ms mrežnog queueing-a. Zbir je isti. Navedene su obje konvencije da se ne pomiješa čekanje reda s prijenosom. Za podfaze originalnog Lighthouse rezultata treba njegov trace i ista konvencija.

**Interpretacija:** najveći dio vremena je nakon dovršetka mrežnog preuzimanja slike. Render delay uključuje čekanje stilova, dekodiranje, fontove i layout/paint; nije dokazan isključivo kao SQL, JavaScript ili cijeli trošak CSS-a. Ipak, glavni CSS završava tek 3.630,5 ms nakon image responseEnd, što je konkretan dokaz da se prije toga ne može očekivati kompletan stilizovani prikaz.

Novo mjerenje ima long tasks 57/170/121 ms; to nije kontradikcija korisnikovom Lighthouse TBT 0 jer uslovi i TBT prozor nisu identični. Korisnikov rezultat snažno upućuje na mrežu/render blokiranje, a ne na potrebu da se ovaj lokalni audit usmjeri primarno na JS. Novi CLS je 0,00007664, što se zaokružuje na 0, ali nije matematička nula.

## 5. Hero i preload — stvarni problem nezavisan od DEBUG-a

Mobilni hero ne preuzima se jer ga nema u trenutnom lokalnom sadržaju. Zato početak njegovog preuzimanja nije primjenjiv za ovaj prolaz. Ne navodi se produkcijska Cline slika kao lokalni LCP.

Django `views.py:2908–2949` bira preload fallback: hero, grid banner ili prvi proizvod. Za proizvod priprema `lcp_image_sizes='(max-width:768px) 50vw, 16vw'` na 2944. Međutim, `home.html:132` koristi hardkodirane hero imagesizes `(…desktop… , calc(100vw - 40px))` za sve te slučajeve.

Konkretan lokalni head preload je slika proizvoda G9 Lemax Braid, s responsive varijantama 120/200/320/480/600w. Konačni LCP je drugi proizvod FT-1. Preload nije dokazano koristan za stvarni LCP, a width izbor nije jednak širini njegove kartice. Ovo treba riješiti ciljano: odvojiti hero/grid/product sizes i provjeriti srcset kandidat koji browser zaista bira. Ne preloadaj svaku proizvodnu sliku high i ne mijenjaj sadržaj bannera.

## 6. Fontovi i druga mrežna konkurencija

Sedam fontova ima ukupno encoded 184.592 B. U HTML-u su preload Latin 400 i Latin-ext 400 (`base.html:170–171`). `fonts.css` ima font-display swap, unicode-range i težine 400/500/600/700. Swap smanjuje čekanje samog fonta, ali @font-face definicije i layout stilovi i dalje zavise od CSS učitavanja.

Nije potvrđen suvišan font. Ne uklanjati Latin-ext, jer sajt koristi č/ć/š/ž/đ. WOFF2 je već komprimiran; cache zaglavlja su lokalno razvojni problem. Novi uzorak ima JS encoded oko 242.285 B; script defer/idle ponašanje ostaje. Brojke ne treba miješati s ranijim javnim JS transferom jer je lokalni razvojni asset često neminificiran/nekomprimiran, a sadržaj i konfiguracija se razlikuju.

## 7. Cache lifetime i image delivery upozorenja

Lokalni static CSS/font HEAD nema Cache-Control. CSS+font payload je oko 962 KiB, prije dodavanja JS-a, pa lokalna nekeshirana statika predstavlja veliki kandidat za Lighthouse 1.144 KiB upozorenje. Bez originalnog audit JSON-a to nije tačna identifikacija svih 1.144 KiB stavki.

Ne postoji dokaz da su sve lokalne slike nekeshirane: uzorci `/media/` odgovora već imaju `Cache-Control: public,max-age=31536000,immutable`. `views_media.py:5–10` to eksplicitno postavlja. R2/produkcijski cache ne testira se istim localhost media putom.

Image delivery 191 KiB je procijenjena ušteda, ne nužno veličina jedne slike. Novi najveći zabilježeni slikovni resurs je logo PNG 57.654 B; FT-1 je AVIF 24.809 B, druge product slike većinom AVIF. Postojeći moderni format nije dokaz da je resolution izbor optimalan. Provjeriti originalne audit stavke, rendered dimensions×DPR i srcset kandidata prije kompresije/smanjenja; ne mijenjati kvalitet naslijepo.

## 8. Kontrolisani test kompresije — šta dokazuje

U izolovanom test browseru poslužen je isti zamrznuti HTML i isti CSS, jednom raw i jednom gzip. Aplikacija/server nisu mijenjani. CSS encoded zbir: **800.386 → 134.524 B**, oko **83,2% manje**. Sadržaj stilova je isti nakon dekodiranja.

Eksperiment je koristio Playwright response fulfilment, pa emulacija mreže nije identična običnom server downloadu; ne treba njegove FCP/LCP milisekunde predstavljati kao realnu produkcijsku uštedu. FCP 1.020→604 ms, ali posljednji LCP element se promijenio i njegov timestamp je 1.080→2.108 ms; zato taj pojedinačni par nije dokaz poboljšanja ukupnog LCP-a. Pouzdan dokaz je smanjenje byte payload-a, uz već potvrđen nekomprimiran razvojni HTTP odgovor. Ne koristi se kao razlog za promjenu dizajna ili CSS pravila.

## 9. Najmanje rizičan redoslijed

1. **P0 — produkcijski sličan lokalni benchmark, bez poslovnih promjena:** izolovani settings i kopija/testni podaci; DEBUG=False, prikupljena compressed statika, WhiteNoise/application server put; posebno provjeriti Accept-Encoding, Content-Encoding, Cache-Control, hashed URL-ove. Ne prebacivati postojeći radni server na default produkcijske postavke i ne pokretati migracije. Ne očekivati da samo DEBUG=False automatski riješi sve bez pripremljenih asseta.
2. **P0 — zadržati postojeću HTML kompresiju.** Ne „dodavati gzip” koji već radi. Za statiku provjeriti pravilan server put. Ne keširati personalizovan HTML, session/CSRF ili korpu.
3. **P1 — ciljana preload korekcija kada nema hero bannera:** iskoristiti odgovarajući lcp_image_sizes i stvarnu responsive sliku; validirati desktop/mobile i stvarni LCP prije prihvatanja. Nije implementirano u ovom auditu.
4. **P1 — ponoviti Lighthouse na istom sadržaju i na produkciji**, najmanje tri uzorka, medijani, isti viewport/throttling/browser, cold i warm browser cache. Uporediti glavni CSS 553 KB raw naspram compressed odgovora, prije CSS rekonstrukcije.
5. **P2 — tek potom critical CSS/coverage** za header i stvarni prvi vidljivi sadržaj. Sačuvati iste computed stilove i screenshotove, mobilni meni, popupove, korpu i staff stanje. Ne spajati/brisati CSS bez tog dokaza.
6. **P2 — slike/fontovi prema originalnim audit stavkama.** Sačuvati dimenzije, kvalitet, čitljivost i CLS. Provjeriti izvore preload-a i visoko prioritetne eager slike prije globalnih promjena.

Backend upiti i PostgreSQL nisu profilisani niti mijenjani. Nema SQL query plana u ovim mjerenjima. Runserver/DEBUG doprinose razvojnom overheadu, ali izmjereni TTFB oko 0,1 s ne potvrđuje da su oni uzrok većine LCP-a. Najveća potvrđena razlika je razvojna statička isporuka.
