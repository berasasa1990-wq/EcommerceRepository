# CarpologijaBH — provedene optimizacije i provjere

Početna verzija ovog audita je commit `aa05c621`, koji već sadrži ranije
optimizacije CSS-a, hero bannera i logotipa. Ova izmjena nije pushana niti
deployana. Produkcijski podaci, modeli, migracije, URL-ovi i konfiguracija
Cloudflare/R2 nisu mijenjani.

## Potvrđeni uzroci i granice produkcijskog mjerenja

Pet običnih GET zahtjeva prema produkcijskoj početnoj, razmaknutih deset sekundi,
dalo je TTFB 958–4359 ms. Postojeći `Server-Timing` pokazuje 882–1414 ms Django
obrade, od čega 484–984 ms za 16–17 SQL upita. Nijedan od tih zahtjeva nije
otvarao novu DB konekciju. To ne isključuje sporost konekcije pri hladnom startu.

Najsporiji uzorak ima TTFB 4359 ms uz 882 ms Django obrade: približno 3477 ms
čekanja je izvan instrumentirane Django obrade. Nije moguće razdvojiti mrežu,
proxy/origin i čekanje na worker bez Render logova. Ranijih 8–9 sekundi nije
reprodukovano niti je potvrđeno uklanjanje tog povremenog problema.

Produkcijski PostgreSQL pristup i Render logovi/RAM nisu dostupni. Zato nije
rađen PostgreSQL EXPLAIN, pregled lockova, test opterećenja ili promjena broja
workera. Projektni start command ostaje jedan worker sa četiri threada.

## Implementirano

- Početni brend karuseli grupno učitavaju najviše deset artikala po brendu,
  uz očuvan redoslijed, vidljivost, varijacije i akcijske cijene. Šest brendova:
  20 → 4 SQL upita, identičan HTML kartica.
- Ručno istaknuti proizvodi imaju unaprijed učitane flash akcije; zagrijani
  keš više ne pokreće dodatni upit po kartici. Novi javni cache key i invalidacija
  sprečavaju korištenje starih objekata bez prefetcha.
- Pretraga/filteri dijele ponovljena čitanja samo unutar trenutnog GET zahtjeva.
- Korpa, njen sažetak, checkout i analitika koriste već učitane stavke iz
  trenutnog zahtjeva. Nema globalnog keširanja korpe, sesija ili checkouta.
- Upsell izuzima postojeće stavke bez ponovnog učitavanja korpe.
- Nalog unaprijed učitava podatke za oznake plaćanja i preskače velika neiskorištena
  JSON polja; broj upita ne raste s brojem narudžbi i podaci ostaju vezani za korisnika.
- Social proof bira isti tip javnog proizvoda jednim uskim upitom umjesto dva.
- Preload slike proizvoda koristi isti src/srcset/sizes kao glavna slika.
  Responsive test: dva zahtjeva → jedan. Trenutni udaljeni original-only storage
  nema responsive kandidate, pa ovaj test ne dokazuje produkcijski dobitak za R2 slike.
- Carousel koristi već izračunatu širinu: šest suvišnih computed-style čitanja
  → nula u testiranom ciklusu, bez promjene geometrije.
- Opt-in TTFB dijagnostika sada bilježi najduži SQL upit i broj upita ≥100 ms,
  bez SQL teksta, parametara ili privatnih podataka.
- Lokalni Lighthouse runner odbija uzorke s neuspješnim statičkim zahtjevima;
  backlog QA snapshot servera povećan je da browser preloads ne gube konekcije.

CSS, fontovi, hero slike i pravila logotipa ostaju isti u ovom auditu. Ranije
rezervisani okviri, contain i uklanjanje potrebe za runtime normalizacijom
logotipa su sačuvani. Dvanaest hero/nohero provjera odgođenog CSS-a ne pokazuje
promjenu computed stilova. Render build već generiše minificirane assete prije
collectstatic; završni prikupljeni JS odgovara izvoru, uz gzip/Brotli verzije.
Produkcijski kritični CSS ima Brotli i immutable cache; mobilni hero ima CDN HIT.

## Uporedivi rezultati

Lighthouse 13.5.0: lokalni zamrznuti hero fixture, bez Django obrade i vanjskih
zahtjeva, tri naizmjenična uzorka po verziji na 390 i 1440 px. Prikazani su
medijani. Produkcijski raniji rezultat 75 nije uporediv s ovim lokalnim parom.

| Metrika | Mobilni prije → poslije | Desktop prije → poslije |
|---|---:|---:|
| Performance | 86 → 90 | 99 → 99 |
| FCP | 1,656 → 1,655 s | 0,447 → 0,446 s |
| LCP | 4,055 → 3,605 s | 0,925 → 0,886 s |
| TBT | 0 → 0 ms | 0 → 0 ms |
| CLS | 0,000065 → isto | 0,000191 → isto |
| Preneseni CSS | 126228 → isto B | 126228 → isto B |
| Preneseni JS | 32022 → 32003 B | 32022 → 32003 B |

LCP element je prva hero slika. Simulirani LCP i posmatrani trace breakdown
imaju različite vremenske skale; ne treba ih sabirati. Posmatrani mobilni
render delay iznosi 94 → 88 ms. Mali uzorak i lokalna simulacija ne potvrđuju
produkcijsko ubrzanje. Mobilni cilj LCP <2,5 s još nije postignut.

Lokalni Django profiler: iste sintetičke SQLite kopije, pet uzoraka po ruti,
prvi nakon pražnjenja aplikacijskog keša i medijan naredna četiri. Inicijalizacija
middlewarea/static indeksa nije uključena u vrijeme zahtjeva. Ovo nije mrežni TTFB.

| Ruta | SQL prije → poslije | Obrada prije → poslije |
|---|---:|---:|
| Početna | 22 → 15 | 22,55 → 20,28 ms |
| Pretraga | 22 → 18 | 17,84 → 16,94 ms |
| Kategorija | 19 → 18 | 14,73 → 14,55 ms |
| Proizvod | 26 → 25 | 18,14 → 18,20 ms |
| Popunjena korpa | 57 → 35 | 21,47 → 14,96 ms |
| Checkout | 70 → 20 | 25,61 → 9,59 ms |
| Nalog | 23 → 15 | 15,36 → 12,31 ms |

Admin/panel/WMS su provjereni i broj upita je isti; sva testirana HTTP stanja
su 200. Najduži lokalni SQL upit, hladni uzorci i vremena templatea su u JSON
prilozima. SQLite vremena ne predviđaju PostgreSQL vremena. Peak RSS lokalnog
procesa je 107,73 → 113,42 MB; produkcijska memorija nije izmjerena.

## Testovi i spremnost

- Django check i diff check prolaze.
- Završnih 143 relevantna testa prolaze, uključujući 14 novih backend i četiri
  preload testa, cijene/popuste/loyalty/greb-greb i WMS provjere.
- Tokom audita izvršen puni paket: 1072 testa, 74 pada i tri greške. Svih 67
  pogođenih metoda ponovo je izvršeno na početnom commitu: isti 74 pada i tri
  greške, uključujući iste subtestove. Puni paket nije zelen; završni relevantni
  paket provjerava finalne izmjene.
- Deset stranica na dvije širine: ista geometrija i bez JS grešaka. Sitne raster
  razlike, uključujući Visa Secure grafiku, zapisane su u frontend-verification.
- Živi lokalni browser testovi menija, dodavanja u korpu, favorita, newslettera
  i pretrage prolaze na obje širine. Vanjski zahtjevi su blokirani.
- Testovi koriste sintetičke SQLite baze; profiler session/cart promjene vraća
  rollbackom. Produkcijska baza nije korištena za write ili testove opterećenja.

Izmjene su pripremljene za pregled/push. Potpuno potvrđena spremnost za deploy
ostaje ograničena postojećim padovima punog paketa i nemjerenim produkcijskim
rezultatom nakon izmjena. Push/deploy nisu izvršeni.

Potpun spisak izmijenjenih i novih datoteka: [changed-files.txt](changed-files.txt).
Mjerenja: [runtime-comparison.json](runtime-comparison.json),
[lighthouse-marginal-summary.json](lighthouse-marginal-summary.json),
[production-ttfb.json](production-ttfb.json), [test-results.json](test-results.json).
