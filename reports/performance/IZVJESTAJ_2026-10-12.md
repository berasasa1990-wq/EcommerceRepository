# Početna CarpologijaBH — analiza performansi, 12.10.2026.

## Dokazi i granice mjerenja

Korisnički GTmetrix: Performance 48%, Structure 94%, LCP 12,7 s, TBT 222 ms, CLS 0, Seattle. Bez originalnog waterfall-a i Lighthouse trace-a ne može se pripisati svih 12,7 s jednoj komponenti. Ovdje su nova, nezavisna mjerenja iz ove mreže, bez GTmetrix throttlinga; nisu porediva kao prije/poslije.

Read-only HTTP provjera https://carpologijabh.ba/: 200, TTFB 1,354 s, ukupno 1,370 s, HTML 302.179 B bez kompresije u curl zahtjevu. Cloudflare HTML: DYNAMIC, Vary Cookie/Accept-Encoding, Render/Gunicorn origin. Nije korišten produkcijski DB niti admin pristup.

Stvarni LCP utvrđen PerformanceObserver-om u izolovanom Brave/Chromium browseru (podaci u live-browser.json):

| Uređaj | Viewport/DPR | LCP | Browser TTFB | LCP element |
|---|---|---:|---:|---|
| Desktop | 1365×900 / 1 | 4,832 s | 2,793 s | #heroCarousel .carousel-slide.active .slide-image-img |
| Mobilni | 390×844 / 3 | 2,608 s | 1,221 s | isti img, mobilni source iz picture |

Desktop LCP URL: https://media.opremazaribolov.ba/banners/Cline_carpologija_Travel_torba.webp
Mobilni LCP URL: https://media.opremazaribolov.ba/banners/mobile/slika_torbe_cline.jpg
Prije hero-a logo je privremeni LCP kandidat; konačni kandidat je hero. Ovo potvrđuje sadašnji prikaz, ne historijski GTmetrix element ako su baneri tada bili drugačiji.

## Uzroci

1. **P1: TTFB je značajan.** Novi browser zahtjevi 1,22–2,79 s. Ovo obuhvata mrežu, Cloudflare, Render, middleware, DB i render, pa se ne može tvrditi da je sve Django/DB vrijeme. Lokalni izolovani QA (40 sintetičkih artikala, jedan hero; SQLite): cold 251,74 ms / 30 upita; warm 12,47 ms / 16 upita. Cold uključuje inicijalizaciju/template kompilaciju. Potrebno je produkcijsko agregatno server mjerenje za konačnu atribuciju.
2. **P1: render-blocking CSS.** 12 stylesheet linkova u produkcijskom HTML-u, dvije datoteke s desktop media uslovom. style.min.css ima čak 547.707 B lokalnog nekompresovanog sadržaja. U browser mjerenju CSS resursi oko 0,9–1,0 s. Desktop hero kreće na 2,827 s, završava približno 3,009 s, ali LCP dolazi na 4,832 s: značajan ostatak je čekanje prikaza, ne samo slika. Zadržati critical layout CSS dok se ne napravi coverage i vizuelno poređenje; min u imenu ne znači mali bundle.
3. **P1/P2: mobilni hero metapodaci nisu tačni.** Stvarna mobilna slika je 670×884, ali remote fallback daje srcset descriptor 1080w; desktop stvarno 2173×724, fallback 2172w. Remote responsive helper namjerno ne koristi varijante bez manifesta, što izbjegava 404 i mrežne HEAD zahtjeve pri renderu. Trenutno desktop/mobilni srcset imaju samo original, ne stvarne responsive varijante. Nemoj izmišljati -480w URL-ove.
4. **P2: preload se nalazi poslije dijela CSS-a.** Prvi hero već ima eager/high i matching picture/preload breakpoint 768px. sizes je 100vw, što odgovara hero-u pune širine. Preload se može pomjeriti odmah poslije viewport meta oznake. Mobilni browser uzorak pokazao je i desktop zahtjev prije mobilnog; provjeriti ponovljenim testom sa viewport/media selekcijom, ne tvrditi da je to stalno ponašanje svih uređaja.
5. **R2 cache radi.** Obje slike dolaze preko custom domena media.opremazaribolov.ba, ne r2.dev. Obje 200/HIT, public max-age=31536000 immutable; Age mobilni 44631, desktop 45194. Mobilna JPEG 193.094 B, desktop WebP 191.426 B. Jednokratni curl transfer 125/54 ms iz ove mreže. Nema dokaza da je loš R2 cache glavni uzrok. Godišnji immutable zahtijeva nova imena pri zamjeni slika, što postojeći pipeline već razmatra.
6. **JS:** glavni main.min.js 71.349 B, footer skripte imaju defer. Nema dokaza da sinhroni vanjski JS blokira parser početne. Google tag se učitava kasnije. TBT 222 ms zahtijeva long-task trace za precizno smanjenje; ne uklanjati funkcionalnosti na osnovu samog rezultata.
7. **Cache početne:** LocMemCache, 180 s, odvojen po procesu/workeru. Selektivno se keširaju featured/sale/category/brand sekcije; latest nasumični izbor namjerno ostaje svjež. Nema javnog full-page cache-a. To je sigurno za personalizovane korpe, wishlist, CSRF i staff mode. Ne uvoditi blanket Cloudflare Cache Everything ili zajednički HTML cache bez razdvajanja personalizacije. U QA postoje dupli PageSEO upiti; ovo je mali dio, ne dokaz glavnog uzroka.

## Plan i očekivana poboljšanja

| Prioritet | Izmjena | Očekivanje / provjera |
|---|---|---|
| P1, minimalna | Preload prvog hero-a ranije u head-u, poslije viewport-a; eksplicitni komplementarni desktop media uslov | Ranije otkrivanje hero-a. Dobit zavisi od toka HTML-a; nije garantovan broj sekundi. |
| P1, minimalna | Ukloniti lažne width srcset descriptore remote originala bez manifesta | Tačan izbor izvora, bez novih 404, bez promjene slike i layouta. Samo ovo ne smanjuje bajtove. |
| P1, kasnija | CSS coverage i razdvajanje critical od neaktivnih modula | Manje čekanje prikaza; test mobilni/desktop, popup, wishlist, meni, cart i no-JS prije promjene. |
| P1, mjerenje | Server timing / postojeći profil upita, provjera Render cold start i DB latency | Odvojiti mrežni TTFB od app/SQL vremena. Cilj warm TTFB <800 ms, ne obećanje. |
| P2, kasnija | Verzijske WebP/AVIF mobilne slike i manifest stvarnih 390/670/1080 varijanti bez upscalinga | Manji transfer na sporoj mobilnoj mreži, izgled/crop isti; ne mijenjati produkcijske fajlove tokom audita. |
| P2, kasnija | Shared Redis cache isključivo javnih podataka s invalidacijom | Manje hladnih worker upita; ne keširati personalizovani HTML. |

Cilj nakon verifikacije: LCP <2,5 s na reprezentativnoj vezi, CLS ostaje 0. Za GTmetrix Seattle ponoviti isti uređaj, viewport, throttling i server najmanje tri puta; porediti medijanu i faze LCP-a. Ne očekivati da mali preload patch sam dovede 12,7 s ispod 2,5 s.

## Izvori

- https://web.dev/articles/optimize-lcp — odvajanje TTFB, resource delay/duration i render delay.
- https://developers.cloudflare.com/r2/buckets/public-buckets/ — custom domain/cache naspram r2.dev.
- https://developers.cloudflare.com/cache/interaction-cloudflare-products/r2/ — R2 CDN caching.

Izvještaj je sačuvan prije aplikacijskih izmjena ovog zadatka. Deployment, git push, promjene Cloudflare/Render/R2 konfiguracije nisu izvršeni.

## Pripremljene minimalne izmjene i provjere

- `bera_reference/base.html`: hero preload blok pomjeren odmah poslije viewport meta oznake, prije CSS-a. Media preconnect ostaje prije preloada.
- `bera_reference/home.html`: desktop preload uslov eksplicitno `(min-width: 768.01px)`; mobilni ostaje `max-width: 768px`.
- `utils/images.py`: remote originali bez manifesta ne emituju izmišljeni width descriptor. Originalni URL, layout dimenzije i postojeće lokalne responsive varijante ostaju.
- Nisu odgođeni niti uklonjeni CSS/JS moduli, nije uveden HTML cache, nisu promijenjene cijene, izgled, korpa ili hero fajlovi.

Browser provjera s istim produkcijskim HTML snapshotom i lokalno primijenjenim izmjenama: konačni izbor hero-a ostaje mobilni na 390px i desktop na 1365px. Screenshot poređenje samog hero-a je piksel-identično na oba viewporta (390×488 i 1365×455), zapis u hero-visual-comparison.json. Ovo je provjera ekvivalentnog HTML patcha uz javne produkcijske resurse, ne deployment i ne puni end-to-end lokalni server.

Mobilni dvostruki banner zahtjev ostaje prisutan u izolovanom browser testu i nakon minimalnog patcha. Nije proglašen riješenim; dodatno treba zabilježiti initiator/trace i provjeriti početni emulirani viewport te speculative loading. Ovaj patch ne obećava uštedu bajtova na hero-u niti novi GTmetrix rezultat.

Lokalni nakon-patch profil: cold 212,12 ms / 30 upita, warm 12,60 ms / 16 upita. Razlika hladnih vremena je šum inicijalizacije, nije dokaz ubrzanja Django-a; patch cilja tačnost i redoslijed učitavanja slika.

Završna provjera: 10 ciljanih testova za aktivni BERA homepage/preload/remote fallback i lokalni profil prolazi; Django system check i git diff --check prolaze. Postojeći preload testovi su upućeni na aktivni `bera_reference/home.html` jer je `home.html` sada samo extends wrapper; puni render koristi izolovani TestCase i eksplicitni media_origin. U ranijem širem prolazu testovi banner upload limita nisu imali greške; greške zastarjelih preload testova ispravljene su i ti testovi ponovo prolaze. Završni lokalni profil 185,92 ms/29 upita cold i 13,61 ms/16 upita warm; sačuvan kao local-profile-after.json. Razlike cold inicijalizacije nisu pripisane patchu.

Napomena o CSS-u: 547.707 B je lokalna nekompresovana veličina; browser je za produkcijski style.min resurs zabilježio oko 89.740 B transfera. I dalje postoji parser/render-blocking trošak sadržaja, ali ne tvrdi se da svaki posjetilac preuzima 547 KB preko mreže.
