# SEO audit: CarpologijaBH — stari WooCommerce URL-ovi

Datum: 12.10.2026. Faza: izvještaj i prijedlog, bez izmjena aplikacije ili produkcije i bez git push-a.

## Zaključak

Django trenutno ne prepoznaje osam navedenih WooCommerce parametara. Na postojećim javnim putanjama ih ignoriše i vraća normalnu stranicu sa HTTP 200. To ostavlja veliki broj dostupnih URL varijanti za isti sadržaj. Međutim, canonical **već postoji** na početnoj, kategorijama i proizvodima. Lokalna provjera korisnikovih GSC primjera potvrđuje jedan canonical na `https://carpologijabh.ba/`.

Zato nije dokazano da sadašnji Django HTML nema canonical, niti da svih 131.146 prijavljenih URL-ova predstavljaju sadašnje greške. Broj je podatak korisnika iz GSC, a ne broj artikala ili broj URL-ova koje smo preuzeli. Moguće je da GSC prikazuje ranije crawlove, odgovor starog WooCommerce sajta ili drugačiji produkcijski odgovor. Za potvrdu trebaju datumi crawlova, URL Inspection (user-declared/Google-selected canonical), kompletan GSC izvoz i produkcijski HTTP odgovori.

**Preporuka:** trajno preusmjeravati samo prepoznate, zastarjele query varijante postojećih javnih stranica na istu putanju bez tih parametara. Važeće proizvode, kategorije i sadašnje funkcionalne parametre sačuvati. Nepoznate putanje zadržati kao 404; poznate stare putanje preusmjeravati samo uz provjerenu mapu.

## Obim i dokaz

Pregledani su URLconf, middleware, filteri, canonical logika u views i oba osnovna šablona, sitemap, robots.txt i postojeći SEO testovi.

Korisnik je poslao pet spojenih URL zapisa, od kojih je jedan identičan drugom. Četiri jedinstvena URL-a razdvojena su na granicama `https://carpologijabh.ba/` i testirana pojedinačno. Datumi posljednjeg crawla nisu dostavljeni. Spajanje URL-ova u poruci nije tretirano kao dokaz da Google crawling prima takav spojeni URL.

Dodat je odvojeni dijagnostički test modul `reports/seo/test_legacy_url_audit.py`. On dokumentuje **sadašnje ponašanje**, ne implementira novu politiku. Koristi Django test bazu sa sintetičkim artiklima, kategorijom i brendom, uz postojeće offline QA postavke koje ne učitavaju `.env` i blokiraju vanjske zahtjeve.

Rezultat: **14 novih dijagnostičkih testova prošlo**, 51 zabilježeni HTTP odgovor. Zajedno sa pet postojećih SEO testova: **18/19 prolazi**. Postojeći `test_home_product_and_category_use_apex_canonical` pada jer cijeli HTML sadrži testni Render domen u atributu `<body data-site-url="https://render-host.onrender.com">`. Canonical provjera tog testa prolazi prije pada; ovo nije dokaz pogrešnog canonicala. Aplikacijski kod nije mijenjan, pa pad nije posljedica ovog audita. Postojeći test nije mijenjan da bi se sakrio pad.

Pokušaji čitanja produkcijskog `robots.txt`, sitemap-a i jednog legacy query URL-a nisu uspjeli: lokalni HTTP klijent nije mogao razriješiti domen iz okruženja, a web alat nije mogao pristupiti tim URL-ovima. To nije dokaz da je domen globalno nedostupan. Nalazi u nastavku potvrđeni su u kodu i izoliranom lokalnom Django izvršavanju, ne kao produkcijski deployment audit.

## 1. Obrada starih URL-ova

`_get_filter_params` u `EcommerceApp/views.py` čita `q`, `kategorija`, `brend`, `velicina`, `cijena_od`, `cijena_do`, `sort`, `akcija`, `noviteti`, `izdvojeno` i staff `nedostaje`. Paginacija koristi `page`; kategorije dodatno koriste `all=1`.

Nema posebnog legacy WooCommerce handlera u pregledanom URLconf-u ni konfigurisanom middleware-u za:

- `filter_color`, `filter_status`, `filter_tax_product_cat`, `filter_by_rating`;
- `add-to-cart`, `in-stock`, `on-sale`, `post_type=product`.

Sva četiri dostavljena jedinstvena primjera koriste postojeću putanju `/`, pa vraćaju početnu sa 200, a ne zato što postoji catch-all redirect. Parametri ne aktiviraju Django filtere. `add-to-cart=33218` ne dodaje artikal: važeći endpoint je POST `/artikal/<slug>/dodaj/`; GET na taj endpoint vraća 405.

| Zahtjev — lokalni fixture | Sadašnji status | Canonical / ponašanje |
|---|---:|---|
| `/?filter_color=light-blue&…&add-to-cart=33218` | 200 | `https://carpologijabh.ba/` |
| `/kategorija/seo-audit/?filter_color=light-blue` | 200 | ista kategorija bez parametra |
| `/artikal/seo-audit-stap/?add-to-cart=33218` | 200 | isti proizvod bez parametra; korpa se ne mijenja |
| `/shop/?post_type=product` | 404 | nema redirecta na početnu |
| `/product/seo-audit-stap/?filter_color=light-blue` | 404 | stara putanja nije mapirana |
| `/product-category/seo-audit/?filter_color=light-blue` | 404 | stara putanja nije mapirana |
| `/artikal/nepostojeci/?filter_color=light-blue` | 404 | nema redirecta na početnu |
| `/artikal/seo-audit-stap?filter_color=light-blue` | 301 | CommonMiddleware dodaje slash, ali zadržava legacy parametar |

## 2. Canonical i paginacija

- Kanonski domen je fiksiran na `https://carpologijabh.ba` u `EcommerceProject/settings.py`.
- Oba osnovna HTML šablona ispisuju canonical u `<head>`. Fallback koristi kanonski `site_url` iz context processora i `request.path`, bez query stringa.
- Početna `/`: jedan canonical na `/`. `/?page=2` također canonicalizira na `/`; obična početna u sadašnjem view-u nema paginirani katalog.
- Proizvod: jedan canonical na `/artikal/<slug>/`, bez dodatnih query parametara. Dostupan aktivan proizvod ima index/follow. Neaktivan ili skriven nedostupan proizvod za običnog posjetioca vraća 404; postojeći out-of-stock proizvod može vratiti 200 sa noindex/follow. Ta politika ostaje izvan ovog prijedloga.
- Kategorija: canonical na `/kategorija/<slug>/`; roditeljska kategorija sa podkategorijama i `all=1` dobija poseban canonical `?all=1`. Postojeći SEO test tu razliku potvrđuje.
- Stvarna paginacija kategorije: `page=1` canonical bez parametra, `page=2` canonical sa `?page=2`. Ispravan smjer za različite stranice kataloga.
- Samostalni homepage filteri `brend`, `akcija` i `noviteti`: mogu biti indexable, s vlastitim canonicalom i zadržanim `page` kada su jedini glavni filter.
- Pretraga i složeni filteri na početnoj: noindex/follow, canonical na `/`. Kategorijski filteri/sort: noindex/follow, canonical na osnovnu kategoriju i stvarni page broj. Ovo nije isto što i potpuno identičan sadržaj; eventualno usklađivanje canonical politike za različite rezultate treba biti odvojena izmjena, bez mijenjanja filtera u legacy popravci.

**Dodatni nalaz:** `_paginate_catalog_products` za neispravan page vraća prvu, a za page izvan raspona posljednju stranicu sa 200. U fixture-u od 52 proizvoda, `?page=999999` vraća page 2 i canonical `?page=2`. To stvara nove dostupne duplikate. Predložiti zasebno 404 za nemoguće brojeve ili jasno definisanu normalizaciju, tek uz odobrenu promjenu ponašanja `page` parametra. Ne mijenjati to usput jer je korisnik tražio očuvanje trenutnih funkcionalnih parametara.

## 3. Sitemap i robots.txt

`/sitemap.xml` lokalno vraća 200, sa HTTPS kanonskim domenom. Sadrži statične stranice, aktivne/popunjene kategorije, aktivne vidljive proizvode koji su na stanju ili imaju varijaciju na stanju, vlogove i brendove sa artiklima (`/?brend=<slug>`). Nema navedenih starih parametara. Broj 131.146 iz GSC ne pokazuje da sitemap ima toliko zapisa.

Product sitemap ima limit 50.000. Prije eventualnog prelaska na sitemap index treba utvrditi stvaran broj sitemap URL-ova i veličinu XML-a; ovim auditom nije utvrđeno prekoračenje. Wrapper uklanja Django `X-Robots-Tag: noindex` sa sitemap odgovora, ali sam taj header na XML resursu nije nalaz koji objašnjava nedostajući canonical na HTML stranicama.

`/robots.txt` lokalno vraća 200, navodi ispravan sitemap i ne blokira sve query stringove. Blokira privatne/utility putanje. Komentar „ne indeksirati“ ne znači da robots sam garantuje deindeksiranje. WooCommerce parametri nisu blokirani.

**Ne dodavati blanket `Disallow: /*?`**: blokirao bi važeće filtere, paginaciju i mogućnost da Google vidi canonical ili redirect. Google navodi da robots.txt nije način canonicalizacije i da blokirani URL može ostati indeksiran. [Google: consolidate duplicate URLs](https://developers.google.com/search/docs/crawling-indexing/consolidate-duplicate-urls).

## 4. Sigurna strategija

### Prvi, ograničeni patch: legacy query parametri

1. Primjena samo na GET/HEAD javne početne, postojeće kategorije i postojeće proizvode, iza provjere putanje i dostupnosti resursa za javnog korisnika. Ne primjenjivati na POST, checkout, cart endpoint, Monri callback, B2B, admin, WMS, API, statiku ili medije.
2. Uklanjati samo tačno imenovane zastarjele ključeve: četiri `filter_*` ključa iz ovog izvještaja, `add-to-cart`, `in-stock`, `on-sale`; `post_type` samo za prepoznatu vrijednost `product`. Ne uklanjati sve `filter_*` ključeve niti sve nepoznate parametre.
3. Kada nešto uklanjamo, vratiti **301 na istu postojeću putanju** sa svim ostalim parametrima. Sačuvati njihove vrijednosti, ponovljene ključeve, URL encoding i funkcionalni page/all/filter sadržaj. Bez eksternih redirect meta iz query stringa.
4. Čisti važeći URL ostaje 200 sa svojim canonicalom. Ne mijenjati modele, slugove, rute ili katalog. Nepostojeći proizvod/kategorija ostaje 404, čak i sa legacy parametrima.
5. Ne dodavati ništa u korpu preko starog `add-to-cart` GET-a. WooCommerce numeric ID ne treba poistovjećivati sa Django product PK.
6. Ne prevoditi automatski `filter_tax_product_cat=133,141,…` u Django kategorije niti `on-sale` u `akcija`, bez potvrđene semantičke mape. Trenutno su ignorisani; promjena u novi filter promijenila bi sadržaj.
7. Redirect ne smije napraviti petlju, pogrešan host ili promijeniti trenutne query parametre. Već kanonska putanja treba jedan legacy redirect. Nedostajući slash trenutno dodaje jedan dodatni redirect; testirati lanac i po mogućnosti spojiti normalizaciju bez izmjene važećih URL-ova.

Za korisnikove primjere cilj bi bio `/`, jer njihove postojeće putanje već jesu `/` i svi query ključevi su zastarjeli. To **nije** pravilo za nepoznate putanje ili sve query URL-ove.

| Primjer | Predloženi odgovor |
|---|---|
| `/?filter_color=light-blue&post_type=product` | 301 → `/` |
| `/artikal/validni-slug/?add-to-cart=33218` | 301 → isti proizvod bez parametra |
| `/kategorija/validni-slug/?page=2&filter_status=instock` | 301 → ista kategorija sa `?page=2` |
| `/?brend=validni-brend&page=2&filter_color=light-blue` | 301 → `/?brend=validni-brend&page=2` |
| `/?q=stap&post_type=product` | 301 → `/?q=stap`, uz postojeći noindex/follow |
| `/nepostojeca/?filter_color=light-blue` | 404, bez redirecta |
| `/?post_type=druga-vrijednost` | izvan uske legacy politike; ne nagađati značenje |

Permanent redirect je jak canonical signal za napušteni duplikat. Canonical i sitemap treba da se slažu s ciljem redirecta. [Google: canonical methods](https://developers.google.com/search/docs/crawling-indexing/consolidate-duplicate-urls).

### Druga, zasebna faza: stari WooCommerce path URL-ovi

Napraviti provjerenu mapu iz starog izvoza/permalinkova i GSC/backlink podataka: stari proizvod → isti novi proizvod; stara kategorija → ista nova kategorija. Ako postoji potvrđen ekvivalent, 301 direktno na njegov važeći URL. Bez potvrđene mape ostaje 404. 410 koristiti samo za poznate trajno uklonjene resurse bez zamjene, ne automatski za svaku nepoznatu putanju. Google tretira 4xx kao nedostupan sadržaj. [Google: HTTP status codes](https://developers.google.com/crawling/docs/troubleshooting/http-status-codes).

### Paginacija i ostale SEO izmjene — odvojeno

Sačuvati self-canonical na svakoj stvarnoj indexable stranici paginacije; ne canonicalizirati sve na page 1. [Google: pagination](https://developers.google.com/search/docs/specialty/ecommerce/pagination-and-incremental-page-loading).

Promjenu statusa za nevažeći page, politiku indexiranja sort/filter varijanti, uklanjanje testnog Render domena iz vidljivog HTML-a i eventualni host/www redirect predložiti i testirati odvojeno. Nisu potrebni za minimalno čišćenje osam navedenih parametara.

## 5. Plan testova za implementaciju

Dijagnostički testovi su već sačuvani u ovom direktoriju. U implementacijskom patchu ih dopuniti/premjestiti u aplikacijski test paket i ažurirati očekivanje sa trenutnog 200 na 301 samo za usko dozvoljene legacy slučajeve:

- svih osam ključeva, pojedinačno i u kombinaciji; četiri jedinstvena GSC primjera;
- GET i HEAD, jedan redirect na odgovarajući čisti resurs, pa 200 i tačno jedan canonical u head-u;
- proizvodi i kategorije: važeći 200, nepoznati/neaktivni 404, bez redirecta na početnu;
- miješani novi/stari parametri: svi sadašnji filteri, `page`, `all=1`, ponovljene vrijednosti i tracking parametri ostaju netaknuti;
- `post_type` vrijednosti koje nisu `product`, nepoznati parametri i slična imena ne uklanjaju se;
- korpa ne dobija artikal preko legacy GET-a; postojeći POST dodavanja i 405 za GET ostaju;
- POST/callback/admin/B2B/WMS ne prolaze legacy normalizaciju;
- `page=1`, stvarni page 2, `all=1&page=2`, brend/akcija/noviteti i search noindex;
- slash + legacy: ispravan redirect lanac, bez petlje; nema otvorenog redirecta;
- sitemap 200: kanonski host, postojeći URL-ovi, bez legacy parametara;
- robots 200: sitemap i dostupnost canonical/redirect odgovora crawlerima.

Komanda trenutnog audita:

```sh
venv/bin/python manage.py test reports.seo.test_legacy_url_audit EcommerceApp.tests_seo_domain --settings=scripts.bera_home_qa_settings --noinput
```

## 6. Redoslijed realizacije i praćenje

Prvo pregled ovog izvještaja; zatim lokalni patch i ciljane regresije, pa pregled konkretne promjene. Produkcijski deploy nije izvršen ni odobren ovim auditom. Nema git push-a.

Prije rollouta provjeriti produkcijski HTML za dostavljene primjere, čiste kategorije/proizvode, page 2, sitemap, robots, CDN/cache i stare hostove. Nakon rollouta kontrolisati Location, canonical cilja, stanje filtera i broj 301/404 u logovima. U GSC pregledati nekoliko reprezentativnih URL-ova i ponovo poslati važeći sitemap. Promjena GSC broja zavisi od ponovnog crawla; nije trenutna i ne postoji potvrđen rok za uklanjanje svih starih zapisa.

## Relevantni izvori koda

- `EcommerceApp/views.py`: `_get_filter_params`, `_paginate_catalog_products`, `home`, `category_detail`, `product_detail`, POST `add_to_cart`.
- `EcommerceApp/urls.py`: `/`, `/kategorija/<slug>/`, `/artikal/<slug>/` i `/artikal/<slug>/dodaj/`.
- `EcommerceApp/template/base.html` i `EcommerceApp/template/bera_reference/base.html`: canonical u head-u.
- `EcommerceApp/context_processors.py`: kanonski `site_url`.
- `EcommerceApp/sitemaps.py`, `EcommerceApp/template/robots.txt`, `EcommerceProject/urls.py`.
- `EcommerceProject/settings.py`: `SEO_CANONICAL_URL`, middleware i hostovi.
- `EcommerceApp/tests_seo_domain.py`: postojeće SEO regresije.
