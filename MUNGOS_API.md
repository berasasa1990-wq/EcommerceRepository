# Mungos — audit i prvi korak

Obuhvat: izolovana konfiguracija i ručni GET liveness test. Nema sinhronizacije,
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
MUNGOS_BASE_URL=https://STAGING-DOMENA-KOJU-JE-DAO-MUNGOS
MUNGOS_API_KEY=<staging ključ koji je dao Mungos>
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

Šalje jedan `GET {MUNGOS_BASE_URL}/api/v1/connector/liveness/check/hello`,
header `X-Api-Key`, connect timeout 5 s i read timeout 10 s. TLS provjera je
uključena. Nema automatskih retryja ni praćenja redirecta, tako da se ključ
ne prosljeđuje preusmjerenom hostu. Tijelo odgovora se ne čita niti ispisuje.
Nema upita ili upisa u bazu iz Mungos komande/klijenta.

- HTTP 2xx: `SUCCESS | HTTP status: 200` (ili drugi primljeni 2xx), exit 0.
- Ostali statusi: `FAILED | HTTP status: 401` (primljeni status), exit 1.
- Konfiguracija/mreža/timeout: `FAILED | HTTP status: N/A` i sigurna poruka,
  exit 1. Tekst transportnog exceptiona, headers i credentials se ne ispisuju.

Greška završava samo management komandu; klijent nije uključen u web tokove.
Testovi koriste mock HTTP, zabranjuju DB upite kroz `SimpleTestCase` i
provjeravaju GET/header/timeout, uspjeh, HTTP greške, redirect, transportne
greške, isključenu ili neispravnu konfiguraciju i promjenu base URL-a.

Stvarni STAGING rezultat nije potvrđen: u zadatku nisu dostavljeni stvarni
base URL i API ključ. Pokretanje bez konfiguracije sigurno vraća FAILED,
bez mrežnog zahtjeva. Za narednu fazu potrebna je Mungos specifikacija
endpointa i payloadova; sinhronizacija nije implementirana.
