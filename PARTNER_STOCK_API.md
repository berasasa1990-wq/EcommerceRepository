# API za firmu — artikli i količine

Pristup je samo za čitanje. Ne vraća cijene, kupce, narudžbe, lične podatke,
magacinske lokacije ili interne evidencije. Ne omogućava izmjene.

## Aktivacija na serveru

Generišite odvojen tajni token: `python -c "import secrets; print(secrets.token_urlsafe(48))"`.
Postavite ga kao `PARTNER_STOCK_API_KEY` na serveru i restartujte aplikaciju.
Bez podešenog tokena API je isključen (503). Token mora biti različit od
`SYNC_API_KEY` i `CATALOG_SYNC_API_KEY`. Firmi predajte samo ovaj novi token
privatnim kanalom. Promjenom ili brisanjem vrijednosti opozivate pristup.
Ova izmjena koda sama ne postavlja token niti objavljuje API na produkciji.

## Pozivi

Koristite HTTPS i zaglavlje `Authorization: Bearer TOKEN`.
Token se ne prihvata kroz URL, cookie ili prijavu na sajt.

- `GET /api/partner/v1/products/?page=1&page_size=100`
- `GET /api/partner/v1/products/?sku=SIFRA` — tačna šifra osnovnog proizvoda.
- `GET /api/partner/v1/products/123/` — po ID-u proizvoda.

Primjer (zamijenite DOMENA stvarnom domenom sajta):

```sh
curl 'https://DOMENA/api/partner/v1/products/?page=1&page_size=100' \
  -H "Authorization: Bearer $PARTNER_STOCK_API_KEY"
```

Primjer odgovora liste:

```json
{
  "count": 1,
  "page": 1,
  "page_size": 100,
  "next_page": null,
  "results": [{
    "id": 123,
    "name": "Varalica",
    "sku": "VAR-01",
    "quantity": 4,
    "variants": [{"id": 456, "name": "Crvena", "sku": "VAR-01-R", "quantity": 2}]
  }]
}
```

Količine su raspoložive: evidentirana zaliha umanjena za rezervacije,
uključujući maloprodaju, bez virtuelnih transfer lokacija. Varijacije imaju
svoje količine; ne sabirajte osnovni proizvod i njegove varijacije jer
količina osnovnog proizvoda već obuhvata varijacije.
Za artikle koji se ne vode u lokalnom magacinu koristi se stanje kataloga.
Artikli bez zalihe vraćaju količinu 0. Neaktivni i skriveni artikli se ne vraćaju.

Straničenje je po stabilnom ID-u, najviše 100 proizvoda po pozivu.
Pratite `next_page` do `null`. Količine se čitaju pri svakom pozivu;
stranice nisu jedinstven transakcijski snimak. Pri potpunoj sinhronizaciji
uklonite/deaktivirajte ranije preuzete artikle kojih više nema u listi.

Statusi: 200 uspjeh, 400 neispravni parametri, 401 neispravan token,
404 nepostojeći artikal/stranica, 405 zabranjena metoda, 503 API nije podešen.
