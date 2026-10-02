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
   obrađuje starija prerana skidanja. Uvoz, popis, dnevno MP skidanje i
   Odoo sinhronizacija imaju svoje funkcije u `magacin.py`; B2B ima
   `b2b_orders.finish_pick`. Mungos ih ne poziva niti mijenja.
10. **Vanjske integracije:** `odoo_client.py` je zaseban XML-RPC klijent sa
    settings konfiguracijom, timeoutom i `OdooError`; `olx_api.py` koristi
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
Brand se ne izmišlja; postojeći barkod se opcionalno izvozi kao `ean`.

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
a `ean` prenosi iz buildera ili koristi prazan string ako barkod nedostaje.
`categoryUuid=null` ostaje uz potvrđeni `categoryCode`. Warranty/return vrijednosti
ostaju iz buildera bez izmišljanja. CREATE builder i POST komanda nisu mijenjani.
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
