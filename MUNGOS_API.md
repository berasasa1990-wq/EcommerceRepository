# Mungos — audit i prvi korak

Obuhvat: izolovana konfiguracija, ručni GET liveness i potvrđeni single-product STAGING POST. Nema automatske sinhronizacije,
novih modela, migracija, taskova, webhookova ili poziva iz webshop requestova.

## Audit postojećeg projekta

1. **Artikal:** `EcommerceApp/models.py`, model `Product`.
   Vidljivost određuju, između ostalog, `aktivan` i `sakriven_do_stanja`;
   dostupnost je predstavljena kroz `na_stanju` i `stanje`.
2. **SKU:** `Product.sifra`, nullable/blank i unique unutar tabele.
   `barkod` je zasebno polje. Varijante imaju `ProductVariation.sifra`, također
   unique unutar svoje tabele. Jedinstvenost između ove dvije tabele nije
   garantovana ovim poljima. Prije budućeg izvoza treba dogovoriti identitet
   artikla/varijante i postupanje sa praznim šiframa.
3. **Naziv/opis:** `Product.naziv` i `Product.opis`; varijanta ima svoj `naziv`,
   ali nema zaseban opis. SEO polja su odvojena od opisa artikla.
4. **Cijene:** redovna `Product.cijena` / `bazna_cijena`.
   `akcijska_cijena` može biti ručno unesena ili izračunata iz
   `akcija_postotak` pri snimanju. `akcija_do` ograničava važenje.
   `prikazna_cijena` uzima minimum redovne, važeće akcijske i aktivne flash
   cijene (`flash_sale_price`, veze `akcija_flash_lines` na akcije).
   `na_akciji` provjerava da li je prikazna cijena niža od redovne.
   Varijanta koristi svoju redovnu cijenu, ili cijenu roditelja ako je prazna.
   `efektivna_akcijska_cijena` bira najnižu od akcije varijante, proporcionalne
   važeće akcije roditelja i flash akcije. Za katalog postoje dodatna
   `katalog_bazna_cijena` / `katalog_prikazna_cijena` pravila za varijante.
   Popusti korpe, kuponi i ponude su dodatna logika (`cart.py`, `upsell.py`);
   nisu automatski univerzalna akcijska cijena za izvoz. Ništa nije mijenjano.
5. **Zalihe:** `WarehouseStock` veže proizvod, opcionalnu varijantu i
   `WarehouseLocation`; ima `kolicina` i `rezervisano`. Dostupno po zapisu je
   `max(0, kolicina - max(0, rezervisano))`.
   `magacin.py` razlikuje `countable_stock_qs` (magacin) i `recorded_stock_qs`
   (evidentirane lokacije, uključujući maloprodaju, bez transfer lokacija).
   `display_stock_totals` dodaje maloprodaju i računa dostupnu količinu.
   `refresh_catalog_qty` ažurira kataloška `stanje` / `na_stanju` iz
   magacina. Postoje i artikli koji koriste samo kataloško stanje.
   Postojeći read-only `views_partner_stock._serialize` računa dostupno po
   lokacijama; za artikle bez magacinskih zapisa i bez `magacin_sync_at`
   koristi katalog. Količina roditelja već obuhvata varijante: ne sabirati ih
   ponovo. Budući izvoz mora dogovoriti isti obuhvat lokacija i rezervacija.
6. **Kategorije:** `Category.naziv`, `slug`, `roditelj` (self FK), obratna veza
   `podkategorije`, `redoslijed`, `aktivan`, `prikazi_u_meniju`.
   `Product.kategorija` je FK na jednu kategoriju, može biti prazna;
   podkategorije su isti model i mogu imati više nivoa.
7. **Slike:** glavna `Product.slika`; galerija `ProductImage.product`,
   `slika`, `redoslijed` preko `dodatne_slike`; varijanta ima svoju `slika`.
   `Product.prikazna_slika` koristi glavnu sliku ili prvu sliku varijante.
   URL dolazi iz `ImageField.url` (npr. `product.prikazna_slika.url`).
   `settings.py` bira lokalni storage ili Cloudflare R2; `storage_backends.py`
   za R2 koristi javne URL-ove bez potpisanog queryja (`querystring_auth=False`).
   Lokalni `/media/...` URL treba pretvoriti u apsolutni URL prema javnoj
   domeni webshopa. `MEDIA_URL`, R2 javna domena i storage određuju adresu;
   javnu dostupnost stvarnih slika treba provjeriti pri budućem izvozu.
8. **Varijante:** postoje u `ProductVariation`, FK `artikal`, veza
   `Product.varijacije`, naziv, SKU, slika, cijene, stanje i pakovanje.
   Cijena može biti za pakovanje (`pakovanje_komada`), pa jedinicu prodaje
   treba uskladiti sa Mungos specifikacijom.
9. **Promjene lagera:** `magacin.apply_movement` pod transakcijom i zaključavanjem
   mijenja zalihu i evidentira `WarehouseMovement`: prijem povećava,
   prodaja smanjuje, korekcija postavlja količinu, transfer premješta,
   rezervacija mijenja rezervisano. `reserve_web_order_stock` rezerviše na
   checkoutu; `validate_order_stock` provodi skidanje nakon pickinga;
   `cancel_order_stock` oslobađa rezervacije. `restore_unfinished_web_stock`
   obrađuje starija prerana skidanja. Uvoz, popis i dnevno MP skidanje
   imaju svoje funkcije u `magacin.py`; B2B ima
   `b2b_orders.finish_pick`. Mungos ih ne poziva niti mijenja.
10. **Vanjske integracije:** Odoo klijent i sync su uklonjeni; `olx_api.py` koristi
    `requests.Session`. Postoje i API-ji za katalog/sync te zaseban
    `views_partner_stock.py` read-only partner API. Mungos koristi vlastiti
    klijent i vlastiti ključ, bez povezivanja sa ovim tokovima.

## Render Environment i lokalna konfiguracija

Postaviti privatno u Render Environment:

```dotenv
MUNGOS_BASE_URL=https://staging.mungos.ba/api/v1/connector
MUNGOS_API_KEY=<staging ključ koji je dao Mungos>
MUNGOS_ECOMMERCE_ACCESS_CODE=<access code koji je dao Mungos>
MUNGOS_ENABLED=true
```

URL mora biti HTTPS, bez credentials, query parametara ili fragmenta. Ne
dodavati liveness putanju: komanda je dodaje. Ako Mungos ima osnovni prefiks
u URL-u, on se zadržava. Ključ se ne stavlja u kod, dokumentaciju, URL ili
komandnu liniju. Lokalno koristiti neversionisani `.env`; `.env.example`
sadrži samo prazne vrijednosti. Postojeći loader projekta daje `.env`
vrijednostima prednost nad procesnim environmentom; na Renderu ne držati
konfliktne vrijednosti u `.env`.

Podrazumijevano je `MUNGOS_ENABLED=false`. Uključivanje omogućava ručni test,
ne pokreće automatsku komunikaciju. Za production zamijeniti `MUNGOS_BASE_URL`
production URL-om bez izmjene koda; ako Mungos izdaje zaseban production ključ,
zamijeniti i `MUNGOS_API_KEY` njihovim odgovarajućim ključem.

STAGING nije javno okruženje: svaki zahtjev mora uz `X-Api-Key` slati
`ecommerceaccesscode`, dodatnu STAGING zaštitu. Vrijednost se čita isključivo
iz `MUNGOS_ECOMMERCE_ACCESS_CODE` koristeći postojeći env loader projekta.
Obavezno je postaviti za STAGING; prazna vrijednost izostavlja dodatni header.
Produkcija može imati drugačiju konfiguraciju: prema uputama Mungosa postaviti
odgovarajući production access code ili ostaviti prazno ako zaštita nije potrebna.
Access code se ne hardkodira, ne loguje i ne prikazuje u exception porukama ili
outputu komande. Ne unositi stvarne vrijednosti u versionisane fajlove.

## Pokretanje i rezultat

```sh
python manage.py mungos_liveness
```

Lokalno sa postojećim virtualenvom:

```sh
venv/bin/python manage.py mungos_liveness
venv/bin/python manage.py check
venv/bin/python manage.py test EcommerceApp.tests_mungos --verbosity 2
```

Šalje jedan `GET {MUNGOS_BASE_URL}/Liveness/check/hello`,
headere `X-Api-Key: MUNGOS_API_KEY` i, kada je konfigurisan,
`ecommerceaccesscode: MUNGOS_ECOMMERCE_ACCESS_CODE`, connect timeout 5 s i read
timeout 10 s. Base URL već sadrži `/api/v1/connector`; kod dodaje samo
`/Liveness/check/hello` i ne duplira osnovni prefiks. TLS provjera je
uključena. Nema automatskih retryja ni praćenja redirecta, tako da se ključ
ne prosljeđuje preusmjerenom hostu. Tijelo odgovora se ne čita niti ispisuje.
Nema upita ili upisa u bazu iz Mungos komande/klijenta.

- HTTP 2xx: `SUCCESS | HTTP status: 200` (ili drugi primljeni 2xx), exit 0.
- Ostali statusi: `FAILED | HTTP status: 401` (primljeni status), exit 1.
- Konfiguracija/mreža/timeout: `FAILED | HTTP status: N/A` i sigurna poruka,
  exit 1. Tekst transportnog exceptiona, headers i credentials se ne ispisuju.

Greška završava samo management komandu; klijent nije uključen u web tokove.
Testovi koriste mock HTTP, zabranjuju DB upite kroz `SimpleTestCase` i
provjeravaju GET/oba headera/timeout, uspjeh, HTTP greške, redirect, transportne
greške, isključenu ili neispravnu konfiguraciju i promjenu base URL-a.

Mungos je potvrdio da HTTP 403 uz postojeći API ključ uzrokuje nedostajuća
STAGING zaštita `ecommerceaccesscode`. Testovi koriste isključivo izmišljene
vrijednosti; stvarni mrežni test nije dio automatskih testova. Ručno
product slanje je opisano u nastavku; automatska product/category sinhronizacija
nije implementirana.

## Izolovani product dry-run (Postman specifikacija)

```sh
python manage.py mungos_product_dry_run 3215
# Lokalno:
venv/bin/python manage.py mungos_product_dry_run 3215
venv/bin/python manage.py test EcommerceApp.tests_mungos EcommerceApp.tests_mungos_product --noinput
```

`mungos_product.py` proizvodi kandidat body za **POST /standard/product**,
ali komanda ne šalje HTTP niti instancira Mungos klijenta. Ne koristi bulk
`/standard/products/create_or_update`. Radi i uz `MUNGOS_ENABLED=false`, bez
ključeva. Nema novih URL-ova, modela, migracija, signala ili automatskog synca.

Output je JSON sa `status`, `reviewReasons`, `carpologijaProductId`, `name`,
`sku`, `price`, `quantity`, `category`, `categoryCode`, `images`,
`variantCount` i `payload`. `READY_FOR_REVIEW` označava uspješno lokalno
mapiranje, ne potvrdu server validacije niti dozvolu slanja.
`NEEDS_REVIEW` je uspješan dijagnostički dry-run (exit 0); nepostojeći
proizvod završava sa exit 1. Konfigurisane string credentials se redaktuju
iz kompletnog outputa čak i ako se nađu u opisu ili SKU-u.

### Identitet, cijena i stock

- `id` i `sku` su postojeći `Product.sifra`, kao string. Lokalni PK je samo
  `carpologijaProductId` izvan body-a. Prazan SKU daje `NEEDS_REVIEW`, bez
  izmišljanja identiteta. Ovo je eksplicitni kandidat mapiranja externog ID-a;
  Mungos semantiku identiteta treba potvrditi prije prvog budućeg slanja.
- Cijena je **Product.prikazna_cijena**, a za varijante
  **ProductVariation.prikazna_cijena**. Postojeća logika obrađuje akciju,
  istekao datum i flash cijene; builder ne računa novi popust. JSON ima
  numerički `price` (BAM), a varijanta isti iznos u `sellingPrice`.
  Ne primjenjuju se personalizovani kuponi i popusti korpe.
- Stock se dobija direktnim read-only pozivom **Cart.availability()** nad
  privremenom korpom u memoriji. Za proizvode sa bilo kojim WarehouseStock
  zapisom ili `magacin_sync_at`, koristi sumu
  `max(0, kolicina - max(0, rezervisano))` po konkretnom SKU-u, samo na
  aktivnim lokacijama koje korpa ne smatra ignorisanim. Nema fallbacka na
  `Product.stanje` kod takvih proizvoda. Za ostale koristi postojeći
  `stock_on_hand` (`stanje` artikla ili varijante), uz `na_stanju` zastavice.
  Neaktivan/sakriven proizvod ima dostupnost 0. Ne oduzima se sadržaj
  korisničke korpe jer je preview globalan. Roditelj i varijante ostaju
  zasebni SKU-ovi prema korpi; količine varijanti se ne dodaju roditelju.
  Nema rezervacija, osvježavanja kataloga ili snimanja modela.

### Kategorije, slike i varijante

Tabela `CATEGORY_CODES` sadrži samo 11 potvrđenih naziva i kodova iz zahtjeva.
Poređenje ignoriše velika/mala slova i rubne razmake; nema fuzzy matching-a.
Traži se najbliža kategorija ili roditelj sa tačno potvrđenim nazivom.
Bez mapiranja: `categoryCode=null` i `NEEDS_REVIEW`; nikada automatski
Accessories. Originalne kategorije ostaju netaknute.

Glavna slika koristi postojeći `prikazna_slika` (uključujući njegov postojeći
fallback na sliku varijante), galerija postojeći redoslijed. Relativni storage
URL se pretvara u apsolutni preko `SITE_URL`; apsolutni javni URL se zadržava.
Duplikati se izostavljaju. URL sa credentials, query ili fragmentom se
izostavlja uz `NEEDS_REVIEW`. Ne provjerava se dostupnost preko mreže,
ne čitaju se image datoteke, nema kopiranja ili uploada.

Varijante čuvaju svoje `sku`, količinu, prikaznu cijenu i vlastitu sliku kada
postoji. `attributes=[]`: nijedan Color/Size kod se ne izmišlja. Svaki proizvod
sa varijantama zato dobija `NEEDS_REVIEW` sa lokalnim nazivom i ID-em varijante.
Prazan/ponovljen SKU takođe traži pregled. Pakovanja se ne preračunavaju;
jedinicu prodaje i attribute codes treba potvrditi prije budućeg slanja.
Brand se ne izmišlja; postojeći barkod se izvozi kao `ean` samo ako ima
tačno 8 ili 13 ASCII cifara, inače se šalje prazan string.

Koristi se `condition`, prema novijim/update primjerima, nikada `condidtion`.
Ostala fiksna polja preuzeta su iz dostavljenog Postman primjera, uključujući
`shippmentDeliveryMethod` spelling i return/shipping zastavice. Ta komercijalna
pravila i server prihvatanje treba pregledati prije budućeg slanja.

### Tačan primjer body-a

Za artikal `sifra=ROD-1`, naziv `Test štap`, opis `Opis`, cijenu 100 BAM,
kataloško stanje 8, kategoriju `Štapovi`, bez slika, barkoda i varijanti:

```json
{
  "id": "ROD-1",
  "sku": "ROD-1",
  "ean": "",
  "name": "Test štap",
  "hasQuantities": true,
  "quantityRemaining": 8,
  "shortDescription": "Opis",
  "details": "Opis",
  "productType": "Product",
  "price": 100.0,
  "currencyIsoCode": "BAM",
  "isNegotiable": false,
  "isFree": false,
  "warrantyMonthsCount": null,
  "warrantyDescription": null,
  "returnDaysCount": null,
  "returnDescription": null,
  "sellerPaysForReturnShipping": true,
  "exchangeAcceptable": false,
  "exchangeComment": null,
  "shippmentDeliveryMethod": "DeliveryByMe",
  "condition": "New",
  "countryCode": "BA",
  "cityCode": "Bijeljina",
  "streetName": null,
  "postalCode": null,
  "longitude": null,
  "latitude": null,
  "categoryUuid": null,
  "categoryCode": "SportRecreation_Equipment_FishingEquipment_FishingRods",
  "productAttributes": {},
  "images": [],
  "HasVariants": false,
  "Variants": []
}
```

## Ručno slanje jednog proizvoda na STAGING

```sh
python manage.py mungos_product_send 4455
# NOT_SENT: bez HTTP-a, DB upita i payload buildera.
python manage.py mungos_product_send 4455 --confirm
```

Druga komanda eksplicitno dozvoljava jedan POST. Neposredno prije slanja čita
jedan proizvod i ponovo poziva postojeći `build_product_preview`; šalje tačno
`preview['payload']`, bez izmjene cijene, stocka, kategorije ili slika.
Status mora biti `READY_FOR_REVIEW` i `reviewReasons` prazna lista.
Nema snimanja modela niti drugih DB upisa; CarpologijaBH ostaje source of truth.

Endpoint je `{MUNGOS_BASE_URL}/standard/product`, odnosno za dokumentovani
STAGING `https://staging.mungos.ba/api/v1/connector/standard/product`.
Slanje odbija svaki host osim `staging.mungos.ba`, zahtijeva `MUNGOS_ENABLED=true`
i oba postojeća headera: `X-Api-Key` i `ecommerceaccesscode`. Liveness ponašanje
ostaje isto. Nema novih konfiguracionih vrijednosti.

Timeout je connect 5 s / read 10 s, TLS provjera uključena, redirecti isključeni.
Nema retryja, bulk slanja, signala, taskova ili automatskog synca.
Komanda prikazuje HTTP status i JSON/text response sa redaktovanim API ključem
i access codeom, uključujući echoed JSON keys/escaped vrijednosti. Odgovor se
ograničava na 65536 bytes; skraćivanje se označava. Kontrolni znakovi se JSON
escapeuju. Tekst transportnih exceptiona i auth headeri se ne ispisuju.
HTTP 2xx daje SUCCESS; 3xx/4xx/5xx završava FAILED (exit 1), uključujući 409.
429 posebno navodi rate limit i ne ponavlja poziv.

Timeout/mrežna greška pri POST-u ili čitanju odgovora daje
`UNKNOWN_REMOTE_STATE` (exit 1). Ako je status već primljen, ostaje prikazan;
inače je N/A. Ne pokretati novi POST dok se ručno ne provjeri da li je Mungos
kreirao proizvod. I nakon HTTP greške provjeriti udaljeno stanje prije novog
POST-a. Komanda nema trajnu evidenciju ni zaštitu od ponovnog ručnog pokretanja.

Testovi: `EcommerceApp.tests_mungos`, `EcommerceApp.tests_mungos_product`,
`EcommerceApp.tests_mungos_send`. HTTP je mockovan; ne koristiti potvrđenu
komandu kao implementacioni ili automatski test.

## Ručni full product update postojećeg STAGING oglasa

```sh
python manage.py mungos_product_update 4455 a9241b59-9e45-4840-9730-c93cb8ad9517
# NOT_SENT; nema HTTP-a, DB upita ni buildera.
python manage.py mungos_product_update 4455 a9241b59-9e45-4840-9730-c93cb8ad9517 --confirm
```

Potvrđena komanda validira UUID, ponovo čita proizvod iz baze i poziva postojeći
`build_product_preview`, zatim zaseban `build_mungos_update_payload`. Šalje
adaptirani puni PUT `payload` kroz
jedan `PUT /standard/product/{uuid}`. Za postojeću konfiguraciju endpoint je:
`https://staging.mungos.ba/api/v1/connector/standard/product/a9241b59-9e45-4840-9730-c93cb8ad9517`.
UUID je samo u putanji; `id`/`sku` u body-ju ostaju Carpologija SKU.
Adapter koristi potvrđeni PUT primjer: zadržava samo njegova polja i casing,
izostavlja CREATE polja `HasVariants`/`Variants`, dodaje `brandCode=null`,
a `ean` prolazi zajedničku sanitizaciju (8/13 ASCII cifara ili prazan string).
`categoryUuid=null` ostaje uz potvrđeni `categoryCode`. Warranty/return vrijednosti
ostaju iz buildera bez izmišljanja. CREATE i UPDATE imaju zasebne scheme i
zajedničku sanitizaciju/validaciju outbound podataka.
Varijante su nezavisno blokirane kao NEEDS_REVIEW jer njihova PUT schema nije
potvrđena, čak i ako bi CREATE preview bio READY_FOR_REVIEW.
Server prihvatanje body-ja ostaje za naknadni, eksplicitno potvrđeni STAGING test.

Naziv, SKU, prikazna cijena, dostupna količina, opis, slike, mapiranje kategorije
i varijante dolaze iz trenutnih Carpologija podataka. `NEEDS_REVIEW` ili bilo koji
`reviewReasons` blokiraju slanje. Varijante sa nepotvrđenim attribute codes zato
ostaju blokirane prema postojećem builderu. Nema promjena modela ili business logike.
Za prvi ručni test ne mijenjati Product 4455: očekivano 138 BAM i količina 43.

PUT dijeli sigurni transport sa POST-om: samo STAGING host, oba auth headera,
connect/read timeout 5/10 s, bez redirecta i retryja, ograničen i redaktovan
response. HTTP 429 posebno navodi rate limit; ostali ne-2xx statusi daju FAILED.
Timeout, mrežna greška ili prekid čitanja daju UNKNOWN_REMOTE_STATE. Provjeriti
Mungos prije ponavljanja. Nema POST fallbacka čak ni za 404; nema kreiranja
novog oglasa, DB mappinga, automatskog synca ili upisa u Carpologija bazu.
Komanda adresira navedeni UUID; stvarno stanje oglasa potvrđuje se tek ručnim
STAGING testom, koji nije izvršen tokom implementacije.

Testovi `EcommerceApp.tests_mungos_update` mockuju sav HTTP i provjeravaju jedan
PUT, isti UUID, aktuelni builder payload, sigurnosne blokade, HTTP greške,
UNKNOWN_REMOTE_STATE, redakciju secreta i SELECT-only DB upite. DB fixture zapisi
postoje isključivo u izolovanoj Django test bazi.

## Lokalni hardening i finalni payload pregled

Izvori za lokalnu provjeru su POST primjer iz ovog dokumenta i postojeći PUT
primjer (`EXPECTED_PUT` u `tests_mungos_update.py`). Originalna Postman kolekcija
ili formalna OpenAPI specifikacija nije prisutna u repozitoriju. Provjera prema
tim primjerima nije potvrda kompletne server scheme ili prihvatanja body-ja.
Operator je prijavio STAGING PUT HTTP 400 za EAN `14587589654` (11 cifara).
Adapter sada za taj izvorni podatak šalje `"ean": ""`; original se ne popravlja
i ne snima. Lokalni Product 4455 nije dokaz produkcijskih podataka.

```sh
# Bez auth ključeva, MUNGOS_ENABLED može ostati false; samo SELECT upiti.
python manage.py mungos_product_dry_run <lokalni_product_id> --operation create
python manage.py mungos_product_dry_run <lokalni_product_id> --operation update
# Opcionalna lokalna validacija UUID-a; nema HTTP-a ni provjere postojanja oglasa.
python manage.py mungos_product_dry_run <lokalni_product_id> --operation update --mungos-uuid a9241b59-9e45-4840-9730-c93cb8ad9517
```

CREATE pregled sadrži finalni sanitized POST kandidat; UPDATE pregled sadrži
finalni PUT kandidat ili `payload=null` kada je blokiran. `NEEDS_REVIEW` i
`reviewReasons` označavaju zabranu slanja. Output redaktuje konfigurisane secrets;
može se razlikovati od body-ja samo po toj redakciji. Pregled nije rezervacija
stanja: potvrđena send/update komanda ponovo čita aktuelne podatke prije slanja.

### Odvojene outbound scheme

Zajednička 32 polja su: `id`, `sku`, `name`, `ean`, `categoryUuid`,
`categoryCode`, `hasQuantities`, `quantityRemaining`, `shortDescription`,
`details`, `productType`, `price`, `currencyIsoCode`, `isNegotiable`, `isFree`,
`warrantyMonthsCount`, `warrantyDescription`, `returnDaysCount`,
`returnDescription`, `sellerPaysForReturnShipping`, `exchangeAcceptable`,
`exchangeComment`, `shippmentDeliveryMethod`, `condition`, `countryCode`,
`cityCode`, `streetName`, `postalCode`, `longitude`, `latitude`,
`productAttributes`, `images`.

- CREATE: 34 polja, zajednička polja + `HasVariants=false`, `Variants=[]`;
  bez `brandCode`. Endpoint `POST /standard/product`.
- UPDATE: 33 polja, zajednička polja + `brandCode=null`; bez CREATE variant
  polja. Endpoint `PUT /standard/product/{uuid}`. UUID je isključivo u putanji.
- `categoryUuid=null` uz potvrđeni `categoryCode`; `condition` i
  `shippmentDeliveryMethod` koriste casing/spelling dostupnih primjera.

### Transformacije i blokade

`mungos_payload.py` centralizuje potvrđena polja, category kodove i zajedničke
provjere. Builder i UPDATE adapter ga koriste, a HTTP transport ponovo provjerava
finalni payload prije otvaranja Session-a. Funkcije vraćaju odvojene podatke;
nema `save`, stock promjena, modela, migracija ili poziva iz webshop toka.

- EAN: samo string od 8 ili 13 ASCII cifara; sve drugo `""`. Bez trimovanja,
  dopunjavanja, skraćivanja, checksum računanja ili izmjena barkoda u bazi.
- `id`/`sku`/`name`: neprazni stringovi bez kontrolnih znakova; identitet se
  ne izmišlja, `id` mora biti jednak SKU-u. Nevalidan podatak blokira slanje.
- Cijena: postojeća `prikazna_cijena`, konverzija Decimal u konačan JSON broj;
  nula dozvoljena, negativna, None, string, bool, NaN/Infinity blokirani.
- Količina: rezultat postojeće `Cart.availability`, strogo integer;
  negativan integer postaje 0 samo outbound, pogrešan tip blokira slanje.
- Opisi: postojeći string bez generisanja; None/nedostajući opis postaje
  `""`, drugi tip blokira slanje.
- Kategorija: samo postojećih 11 potvrđenih kodova, bez generičkog fallbacka;
  nepotvrđen kod ili izmišljeni UUID blokiraju slanje.
- Slike: samo postojeći builder URL-ovi, postojeći redoslijed; bez uploada ili
  promjene storagea. Duplikati se uklanjaju, prva postojeća slika jedina je
  glavna (i kod galerije bez naslovne slike). `images=[]` se zadržava kada
  slika ne postoji. Nevalidan URL/schema blokiraju slanje; bez credentials,
  queryja, fragmenta, kontrolnih znakova ili nevalidnog porta.
- Fiksne vrijednosti: BAM, New, BA, Bijeljina, Product i postojeće bool/null
  vrijednosti iz primjera. Warranty/return/brand se ne izmišljaju,
  `productAttributes={}`; odstupanja od primjera blokirana su.
  `sellerPaysForReturnShipping=true` ostaje iz postojećeg potvrđenog primjera;
  nije novo komercijalno pravilo uvedeno ovim hardeningom.
- Varijante uvijek blokiraju CREATE i UPDATE dok kompletna schema, attribute
  kodovi i jedinica prodaje nisu potvrđeni. Nepotpuna/pogrešna polja i payload
  koji nije JSON-safe blokiraju transport. UPDATE ne radi POST fallback.

### Granice lokalne potvrde

Mungos može i dalje vratiti 400 za checksum EAN-a koji ima 8/13 cifara,
neprihvatanje praznog EAN-a/slika/opisa, ograničenje dužine stringova ili broja
slika, format/preciznost cijene ili dodatna category pravila. Ne popravljamo EAN
checksum i ne izmišljamo nedostajuće podatke. Kodovi/enumi, null/false vrijednosti,
semantika nulte cijene, polja iz nepotpune specifikacije i stvarna javna dostupnost
slika mogu se potpuno potvrditi tek kroz dokumentaciju Mungosa ili zaseban
eksplicitno odobren STAGING test. Jedinstvenost SKU-a i postojanje/vlasništvo
UPDATE UUID-a nisu provjerljivi lokalno. HTTP 401/403 zavise i od stvarnih
credentials, 404 od UUID-a, 409 od udaljenog stanja; 429 od rate limita.

Test skup uključuje `EcommerceApp.tests_mungos_payload` uz četiri postojeća
Mungos modula. HTTP je mockovan, a fixture upisi su samo u izolovanoj Django
test bazi. Transport zadržava oba staging auth headera, timeout 5/10 s,
staging-only write zaštitu, redakciju odgovora i zabranu redirecta/retryja.
Nema cron/Celery/signals, bulk slanja, DB mappinga ili automatskog synca.

## Izolovani STAGING bulk sync

`mungos_bulk_sync` koristi postojeći product preview, update adapter i payload
sanitizer. Ne mijenja Product, cijene, stock, rezervacije ili webshop flow.
Jedina nova tabela je `MungosProductMapping` (migration
`0304_mungos_product_mapping`). OneToOne product i unique UUID trajno povezuju
artikle; snapshot SKU, timestamps i status/error su isključivo integracijski podaci.
UUID je nullable radi trajnog čuvanja neizvjesnog CREATE pokušaja, a ne kao dokaz
uspješnog CREATE-a. `IN_FLIGHT` se snima prije HTTP-a i nakon prekida zahtijeva
ručni pregled; nema automatskog ponavljanja. Tek validan top-level `productUuid`
u kompletnom uspješnom odgovoru potvrđuje CREATE mapping.

Prije prvog potvrđenog bulka, primijeniti migration i registrovati već postojeći
MATE M8 (komanda ne šalje HTTP):

```sh
python manage.py migrate
python manage.py mungos_mapping_set 4455 a9241b59-9e45-4840-9730-c93cb8ad9517
python manage.py mungos_bulk_sync --limit 10
python manage.py mungos_bulk_sync --limit 10 --confirm
python manage.py mungos_bulk_sync --all --confirm
```

Bez `--confirm` je uvijek DRY RUN, uključujući `--all`. Default pregled obuhvata
prvih 10 ID-eva; `--limit N` i `--all` su međusobno isključivi. `--start-after-id N`
nastavlja nakon ID-a, a `--delay` je konačan broj najmanje 1.0 sekunda (default
1.0). Report uključuje pregledane neaktivne/sakrivene artikle kao SKIPPED;
postojeća webshop pravila `aktivan` / `sakriven_do_stanja` određuju slanje.
Potvrđene kategorije i proizvodi bez varijanti su obavezni; builder zadržava sve
postojeće validation gates. Nema fuzzy category mapiranja.

Bulk prihvata samo tačan URL `https://staging.mungos.ba/api/v1/connector`.
Postojeći UUID uvijek vodi na PUT, nikada POST. Pokušaji bez UUID-a i neizvjesni
PUT pokušaji se blokiraju do ručne provjere. `mungos_mapping_set` registruje
provjereni UUID ili otključava provjeren postojeći mapping, ali ne prepisuje drugi
UUID. Za neuspjeli CREATE bez UUID-a operator mora prvo provjeriti Mungos;
automatsko brisanje guard zapisa nije podržano.

POST se nikad automatski ne ponavlja, uključujući 429. Za PUT 429 dozvoljena su
najviše 3 ukupna pokušaja uz `Retry-After` (sekunde ili HTTP datum). Svaki 429
poštuje server čekanje; timeout/network, nepotpun CREATE response, redirect ili
5xx vode na UNKNOWN_REMOTE_STATE. 400/404/409 se evidentiraju i bulk nastavlja;
401/403 i globalna konfiguracijska greška zaustavljaju bulk uz završni report.
Jedinstveni zapis i zaključavanje reda sprečavaju ponovno preuzimanje CREATE-a.
Potvrđeni bulk drži PostgreSQL session advisory lock za cijeli run (SQLite koristi
lokalni file lock); drugi potvrđeni proces se odbija prije HTTP-a. Lock se oslobađa
na izlazu/prekidu procesa. Queryset se obrađuje iteratorom u chunkovima po 200.

Report i per-product log sadrže ID, redigovani SKU, CREATE/UPDATE, UUID, HTTP
status i rezultat. Za HTTP 400/404/409 `api_error` prikazuje sanitizovano
response tijelo uz `api_error_truncated`. Field validation errors se zadržavaju,
a auth/credentials/header polja, tekstualni credential parovi i konfigurisane
tajne (uključujući ključeve JSON-a) se rediguju. Exception tekst se ne ispisuje. READY_CREATE/READY_UPDATE označavaju preflight,
a CREATED/UPDATED samo potvrđene write rezultate. Skupni razlozi uključuju
missing_category, variants_not_supported, invalid_sku, invalid_price i api status.


### Unknown audit i siguran nastavak

`python manage.py mungos_unknown_state_audit` radi samo SELECT, bez HTTP-a.
Prikazuje Product ID, SKU, naziv, mapping ID, UUID, posljednji status/error i
created/updated/last-synced timestamps za IN_FLIGHT, UNKNOWN_REMOTE_STATE ili
mapping bez UUID-a. Error metadata se sanitizuje. Bulk summary broji već
blokirane unknown proizvode u UNKNOWN_REMOTE_STATE i NEEDS_REVIEW, nikad FAILED.

Nakon ručne provjere udaljenog proizvoda koristite
`python manage.py mungos_mapping_set PRODUCT_ID POTVRĐENI_UUID`.
Komanda mijenja samo Mungos mapping: čuva UUID, postavlja REGISTERED i čisti
last_sync_error. Product ostaje netaknut. Sljedeći spremni bulk koristi UPDATE;
postojeći drugi UUID se ne prepisuje. Nikad ne uklanjati unknown guard radi
ponovnog CREATE-a bez udaljene provjere.

Svaki uspješan CREATE odmah snima potvrđeni UUID prije sljedećeg proizvoda.
Nakon parcijalnog runa isti scope može se ponovo pregledati dry-runom:
već potvrđeni proizvodi prelaze na UPDATE, prekinuti IN_FLIGHT i unknown pokušaji
ostaju blokirani. HTTP 400/404/409 ne brišu UUID i ne pokreću CREATE fallback.
Uzrok ranijih UPDATE 400 nije dokazan lokalnim kodom/testovima; sanitizovani
stvarni response nakon zasebno odobrenog staging runa potreban je za dijagnozu.

## Ručni price / quantity sync postojećeg UUID-a

```sh
python manage.py mungos_price_sync --product-id 47
python manage.py mungos_quantity_sync --product-id 47
# PUT samo uz eksplicitni --confirm:
python manage.py mungos_price_sync --product-id 47 --confirm
python manage.py mungos_quantity_sync --product-id 47 --confirm
```

`--product-id` je opcioni lokalni Product ID: kada je naveden, obrađuje se samo
izabrani proizvod. Bez njega obrađuju se svi proizvodi s postojećim UUID mappingom,
uz preskakanje UNKNOWN/UNKNOWN_REMOTE_STATE/IN_FLIGHT i nepodržanih varijanti.
Bulk koristi iterator s chunkovima po 200 i najmanje 1 sekundu između početaka
HTTP zahtjeva. `--delay SECONDS` (default 1) mora biti konačan broj najmanje 1;
`--limit N` ograničava broj mapping kandidata, a `--start-after-id N` bira ID-eve
veće od N (default 0). Limit i start-after-id primjenjuju se na bulk; eksplicitni
product-id bira samo taj proizvod. HTTP 401/403 i konfiguracijska greška prekidaju cijeli run. Ostale pojedinačne
greške ne zaustavljaju obradu ostalih proizvoda; na kraju komanda prijavljuje broj neuspjelih pokušaja.

```sh
python manage.py mungos_price_sync
python manage.py mungos_quantity_sync --limit 10 --start-after-id 47
python manage.py mungos_price_sync --confirm --delay 1
python manage.py mungos_quantity_sync --confirm --delay 1
``` Bez `--confirm` komande rade
SELECT-only dry-run i prikazuju stvarni kandidat bez HTTP-a ili upisa.
Samo postojeći mapping s UUID-em je dopušten; unmapped, UNKNOWN,
UNKNOWN_REMOTE_STATE, IN_FLIGHT i varijante se preskaču. Postojeći builder
validation gates ostaju aktivni. Nikad nema CREATE fallbacka. PUT 429 ima najviše tri ukupna pokušaja, uz
postojeći Retry-After helper (sekunde ili HTTP datum) i delay najmanje 1s.
HTTP 400/404/409 prikazuje sanitizovani `api_error` i indikator truncation;
credential polja i konfigurisane tajne se uklanjaju/rediguju.

Price komanda koristi postojeći full PUT `/standard/product/{uuid}` i obavezni
`ProductPrice`: `Price = Product.bazna_cijena`,
`SellingPrice = Product.prikazna_cijena`. Za regularnu 20 KM i akcijsku 15 KM
šalje 20 i 15; bez popusta šalje 20 i 20. Koristi postojeću full UPDATE schemu,
uključujući postojeće opise, slike i lokalnu dostupnost; nema novog `/quantity`
poziva. Legacy `/price` transport je blokiran. Nema promjene lokalnih podataka.
Quantity putanja ostaje `/standard/product/{uuid}/quantity`, body tačno `id` i
`quantity`, iz postojećeg `Cart.availability()` buildera.

Potvrđeni run koristi postojeći bulk lock i trajno snima IN_FLIGHT prije
staging PUT-a. Mijenja samo integracijski mapping status/error/timestamp.
Timeout, greška čitanja odgovora, redirect i 5xx ostavljaju UNKNOWN_REMOTE_STATE;
sljedeći run preskače mapping do ručne provjere. Ostali neuspješni HTTP statusi
snimaju FAILED i čuvaju UUID. Webshop cijene, stock i rezervacije se ne mijenjaju.
Razvojni testovi koriste mockovani HTTP i izolovanu test bazu.


Automatski price i quantity sync nisu povezani. Postojeći email thread/on_commit
mehanizam nije opći pouzdani background scheduler; ove komande ostaju spremne
za zasebni scheduler/cron bez HTTP-a u Product.save(), checkoutu ili stock save-u.

Primjeri za cijeli quantity scope:
```sh
python manage.py mungos_quantity_sync
python manage.py mungos_quantity_sync --confirm
```

## Regularna i SALE cijena (ProductPrice)

CREATE i full product UPDATE sada dodaju potvrđenu Postman strukturu
`ProductPrice` s tačno `Price`, `SellingPrice`, `Currency`, `IsNegotiable`,
`IsFree`, `DiscountEndDate`. `Price` dolazi iz `Product.bazna_cijena` (postojeće
polje `cijena`), `SellingPrice` iz `Product.prikazna_cijena`. Normalan proizvod
ima jednaku regularnu i prodajnu cijenu. Postojeća legacy polja ostaju ista,
uključujući top-level `price` s trenutnom prikaznom cijenom, BAM, SKU, quantity,
kategoriju, opise i slike. Currency je null, IsNegotiable/IsFree false prema
potvrđenom ProductPrice primjeru. Sanitizer zahtijeva ovu strukturu za svaki CREATE i full PUT;
payload bez obje cijene se blokira prije HTTP-a.

`DiscountEndDate` je ISO datum iz `akcija_do` samo kada je datum važeći i obična
akcijska cijena određuje trenutnu prodajnu cijenu. Ako flash daje nižu ili jednaku
cijenu, datum je null: ne preuzimamo datum obične akcije za flash popust.
Bez pouzdanog datuma šaljemo null. Webshop discount logika nije mijenjana.
`mungos_price_sync` također koristi full product PUT s obje cijene, pa kasniji
price sync ne može prepisati akciju samo jednom cijenom. Quantity sync je nepromijenjen.

```sh
python manage.py mungos_sale_sync --limit 5
python manage.py mungos_sale_sync --limit 5 --confirm
python manage.py mungos_sale_sync --product-id 47 --confirm
```

Bez confirm je SELECT-only dry-run, bez HTTP-a i upisa. Limit broji samo spremne
SALE kandidate (`SellingPrice < Price`), ne sve pregledane proizvode. Komanda
preskače neaktivne, istekle popuste, nepodržane varijante/kategorije i neizvjesne
mappinge. Postojeći UUID uvijek vodi u full UPDATE; novi spremni proizvod koristi
postojeći bulk CREATE flow, lock, trajni IN_FLIGHT guard prije HTTP-a i neposredno
snimanje potvrđenog top-level productUuid. Nema novog POST-a za postojeći UUID.
Chunkovi od 200, najmanje 1s između zahtjeva i bulk error/retry pravila ostaju.

Report sadrži PRODUCT_ID, SKU, NAME, REGULAR_PRICE, SELLING_PRICE,
DISCOUNT_PERCENT, ACTION, MUNGOS_UUID, RESULT i REASON; sve prolazi postojeću
redakciju secrets. ACTION je CREATED/UPDATED tek nakon uspjeha; dry-run, greške i
nepodržani kandidati imaju SKIPPED uz detaljni RESULT. Jedini upisi su Mungos
mapping metadata; cijene, lager, checkout, frontend i admin ostaju netaknuti.
Razvojna provjera koristi isključivo mockovani HTTP i izolovanu test bazu.

Produkcijski hostname trenutno je blokiran postojećom STAGING ONLY zaštitom.
Ove komande nisu produkcijsko odobrenje niti pokreću automatski sync.
