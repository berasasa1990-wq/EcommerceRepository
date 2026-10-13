# Mobilne optimizacije — 13.10.2026.

## Ishod i ograničenje

Pročitan je `MOBILE_PERFORMANCE_AUDIT.md`. Prihvaćena je optimizacija koja smanjuje ponovljena mjerenja headera. Hero i CSS kandidati su testirani i povučeni jer nije potvrđeno pouzdano poboljšanje LCP-a. Fontovi i raspored analitike nisu mijenjani zbog rizika za izgled i događaje. Cilj Lighthouse Mobile 90+ nije ostvaren niti potvrđen ovim radom. U skladu s uputom, ne zadržavaju se automatski rizične optimizacije.

Baza, modeli, podaci, migracije, cijene, lager i narudžbe nisu mijenjani. Prethodne lokalne izmjene uklanjanja rezervnog bannera i prikaza kartične naplate sačuvane su. Nema git add, push ili deploymenta. Django provjere koriste `scripts.bera_home_qa_settings`, izolovanu SQLite testnu bazu i isključene stvarne integracije. Browser test blokira sve zahtjeve osim GET i ne koristi prijavu.

## Faza 1 — Hero: potvrđeno ponašanje, kandidat povučen

CDP Network.requestWillBeSent potvrđuje desktop zahtjev iz parsera na desktop preload oznaci čak i kada je JavaScript isključen. JS-enabled javni prolaz bilježi završen desktop zahtjev s 191.977 prenesenih bajtova i mobilni sa 182.352. Dokaz: `mobile-optimization/hero-initiators.json`.

Na identičnom javnom HTML-u testirani su:

1. Ekvivalentni media uslov `not all and (max-width:768px)` — drugi zahtjev ostaje.
2. Uklanjanje desktop preloada — u jednom no-JS uzorku ostaje samo mobile, ali kasniji JS-enabled trace pokazuje desktop zahtjev iz picture/img fallbacka.
3. Runtime desktop preload nakon matchMedia provjere, plus eksplicitni desktop source i mobilni img fallback — runtime width je 390 i desktop media false, ali konačni CDP trace i dalje vidi završen spekulativni desktop zahtjev iz picture markup-a.

Nijedan kandidat nije prihvaćen. Nije uvedena JS zavisnost hero prikaza. Potrebna je native Lighthouse/HAR provjera bez interceptiona i mobilni Safari test prije nove strategije. Mrežna instrumentacija može uticati na ponašanje skenera; ne treba jedan uspješan uzorak predstavljati kao konačno rješenje. Slike, kvalitet, dimenzije, picture, srcset/sizes, eager/high i postojeći preloadi ostaju.

## Faza 2 — CSS i fontovi: analiza i odbijeni kandidat

Analizirano je svih devet CSS resursa. Encoded veličine iz auditnog Resource Timing uzorka:

| CSS | B | Zaključak |
|---|---:|---|
| home-core.bundle | 90.305 | Glavni aktivni render-blocking CSS; zadržan |
| home-ui.bundle | 10.235 | Mobilni meni/wishlist; zadržan |
| bera-home | 14.630 | Izgled početne; zadržan |
| bera-offer-popup | 1.975 | Popup override-i; zadržan |
| scratch-game | 1.954 | Modal može biti odmah prikazan; nije odgođen |
| navigation.min | 545 | Kritični meni; zadržan |
| storefront-extras.min | 2.245 | Zajednički stilovi; zadržan |
| desktop-reference | 9.266 | Desktop media, nije aktivni mobile render-blocker |
| header-reference | 1.906 | Desktop media; zadržan |

Testiran je byte-preserving spoj susjednih bera-home/popup CSS fajlova na istom mjestu u cascade-u. Broj CSS listova pao je 9→8; screenshotovi su bili isti. Međutim, 390 px kontrolisani LCP medijan bio je prije 1.400 ms, poslije 1.744 ms. To nije konačan dokaz uzroka regresije zbog vanjske mrežne varijacije, ali nije dovoljan dokaz poboljšanja. **Bundle je zato uklonjen**, build skripta vraćena na prethodni sadržaj. Dokaz odbijenog eksperimenta ostaje u `css-candidate-comparison.json` i `css-candidate-summary.json`. U konačnom kodu početna i dalje koristi devet listova.

Critical CSS nije izdvojen naslijepo. Postojeći `site-chrome-layout` ostaje. Za narednu fazu treba CSS coverage pri svim interakcijama i provjera ne samo prvog viewporta nego modala, korpe, mobilnog menija i prijavljenog staff korisnika. Brisanje svih pravila koja se ne koriste na prvom ekranu nije sigurno.

Sedam fontova ostaje: Inter Latin 400/500/600/700 i Latin-ext 400/600/700, zajedno oko 187 KB prenesenih podataka u auditnom uzorku. Unicode-range razlikuje osnovnu latinicu i bosanska slova. Nije potvrđeno da su pojedine težine/subsetovi suvišni za sve sadržaje i interakcije. Varijabilni font nije uveden bez verificiranog identičnog font artefakta, metrika i screenshot provjere. Font-display swap i postojeća tipografija ostaju.

## Faza 3 — Prihvaćena header optimizacija

Datoteka: `EcommerceApp/template/bera_reference/base.html`, inline header mjerenje približno linije 764–814.

Prije: početno mjerenje i ResizeObserver početna obavijest, load i font-ready događaji ponavljali su tri getBoundingClientRect čitanja i kada su visine ostale iste.

Poslije: početno mjerenje ostaje da se sačuva spacer. Zaokružene izmjerene visine se pamte. ResizeObserver koristi borderBoxSize iz obavijesti i zakazuje čitanje samo kada se visina promijeni. Dodatni resize/load/font-ready slušaoci ostaju samo kao fallback kada browser nema ResizeObserver. Ručni `window.syncSiteChromeOffset`, zaokruživanje CSS offseta i requestAnimationFrame grupisanje ostaju.

Browser potvrđuje 12→3 početna geometrijska čitanja. Node regresija provjerava dvadeset nepromijenjenih obavijesti bez dodatnih čitanja, promjenu visine, grupisanje dva callbacka u jedan frame, ručni refresh i fallback bez observera. To je potvrđena ušteda rada, ne dokaz proporcionalnog ubrzanja cijele stranice.

Meta Pixel, CAPI, Google Analytics/Ads, payload-i, event_id i Purchase deduplikacija nisu mijenjani. Daljnje odgađanje Meta skripte može izgubiti događaje kratkih sesija; zato je ostavljen postojeći idle raspored. Audit potvrđuje njen trošak, ali sigurna promjena zahtijeva odvojeni A/B i testove redoslijeda/deduplikacije događaja.

## Faza 4 — Poređenje i testovi

Reproduktivni alat: `scripts/mobile_performance_regression.cjs`. Zamrznuti BEFORE_HTML/AFTER_HTML razlikuju se samo u prihvaćenom header kodu. Slike su zamrznute za identične piksele; ostali vanjski JS/CSS/font odgovori nisu potpuno zamrznuti. CPU 4×, mobilni 390×844 DPR3, 150 ms latency, download 200.000 B/s, upload 93.750 B/s. Tri mobile para i po jedan par 360/430/1440 px. Posmatranje 4,5 s nakon DOMContentLoaded i font-ready. Jedan prethodni pokušaj čekanja load završio je vanjskim timeoutom; završni uspješni protokol isti je za obje verzije.

Ovo nije Lighthouse niti mjerenje Django TTFB-a: HTML i slike su presretnuti. Rezultati se ne smiju porediti s auditnim javnim LCP-om 6,372 s ili korisnikovim Lighthouse LCP-om 5,1 s. Long-task proxy je zbir max(0,duration−50) u prozoru posmatranja, nije Lighthouse FCP–TTI TBT. Nije potvrđena ocjena 90+ niti ukupno LCP poboljšanje.

Završni numerički rezultati i vizuelna provjera navedeni su u dodatku ispod; sirovi dokazi su `mobile-optimization/comparison.json`, `summary.json` i screenshotovi. Odbijeni CSS eksperiment je izdvojen, da se ne pomiješa s prihvaćenim kodom.

## Datoteke prihvaćenog rada

- `EcommerceApp/template/bera_reference/base.html`: manje ponovljenih header mjerenja.
- `EcommerceApp/tests_home_banner_preload.py` i `tests_home_lcp.py`: ispravljene zastarjele očekivane `sizes=100vw` vrijednosti na postojeće responsive sizes; nema novih hero runtime izmjena.
- `scripts/test_mobile_header_measurements.cjs`: geometrijska regresija.
- `scripts/mobile_performance_regression.cjs`: izolovano prije/poslije poređenje.
- `reports/performance/mobile-optimization/`: dokazi.
- ovaj izvještaj.

Novi CSS bundle, njegove izmjene build skripte i CSS delivery test uklonjeni su nakon odbijanja kandidata. Ranije izmjene WMS/email kartične naplate i uklanjanje ugrađenog bannera nisu dio ovog performance zadatka i ostaju netaknute.

## Preostalo

1. Native Lighthouse/Chrome HAR inicijatori hero zahtjeva i Safari provjera, bez objave neuspjelog kandidata.
2. Mobilni critical CSS i font optimizacija tek nakon coverage-a i provjere svih interakcija/težina/slova.
3. Meta A/B uz očuvane kratke sesije i Purchase deduplikaciju.
4. Render response/CPU/restart i PostgreSQL query latencije nisu izmjereni u ovom lokalnom radu; nema izmjene baze niti cachea personalizovanog HTML-a.
5. Najmanje tri standardizovana Lighthouse mobile testa i medijan prije objave. Ovdje nije urađen deployment.

## Završni dodatak — prihvaćen je samo header

| Mjera, 390 px | Prije | Poslije |
|---|---:|---:|
| Početna header čitanja | 12 | 3 |
| CSS listovi | 9 | 9 |
| Font zahtjevi | 7 | 7 |
| LCP medijan kontrolisanih 3 uzorka | 1.984 ms | 1.872 ms |
| Long-task proxy medijan | 299 ms | 172 ms |

LCP prije: 1.632/1.984/2.588 ms; poslije: 1.376/2.936/1.872 ms. Proxy prije: 365/262/299 ms; poslije: 172/166/263 ms. Rasponi su široki, pa ovaj mali LCP pomak nije dokaz istog dobitka u produkciji. Pouzdana ušteda su izbjegnuta geometrijska čitanja. Nije izmjeren novi Lighthouse score.

Na 360, 390, 430 i 1440 px završni screenshotovi su identični piksel po piksel; poređena geometrija headera, hero bannera i glavnih sekcija je jednaka. Pregledan je i mobilni screenshot. Ovo pokriva početni viewport, ne sve modalne/dinamičke situacije.

CLS identičan prije/poslije: 390 px 0,00006508; 360 px 0,00007051; desktop 0,00015969; 430 px 0. Zaokružuje se na 0 u uobičajenom prikazu, ali sve vrijednosti nisu matematički nula. Nema novog izmjerenog layout shifta.

Node test headera, JavaScript syntax, Django system check i git diff --check prolaze. Django regresiona baterija pokriva početni hero/preload, kategorije/noviteti/brend-filter tokove, pretragu i „Prikaži više”, korpu/dostupnost, checkout/kupce, nalog/reklamacije, Meta PageView/AddToCart/ViewContent/Purchase i event_id, WMS alate i tri osnovna Monri testa. Nije pokrenut kompletan projektni suite niti ručno otvorena svaka stranica. Nema stvarnog kartičnog plaćanja, email slanja ili vanjskih CAPI poziva u izolovanim Django testovima.

Završna baterija prihvaćenog koda: **65 Django testova prolazi**, 10,608 s. Raniji eksperiment s CSS bundle-om imao je 67 testova; dva testa tog odbačenog bundle-a uklonjena su zajedno s kandidatom. Važeći broj za završni kod je 65.
