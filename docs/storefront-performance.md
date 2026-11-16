# Ubrzanje webshopa i otvaranja artikala

Izmjene su u radnom kodu; deploy nije pokrenut. Nisu potrebne nove migracije.

## Šta je izmijenjeno

- `EcommerceApp/views.py`: otvoreni artikal, povezani artikli i slični modeli sada dijele jedan paket učitavanja stanja, akcija i varijacija. Uklonjen je prefetch koji se odmah ponavljao. Predložak koristi već pripremljene podatke umjesto ponovnog osvježavanja povezanih kartica. Kategorija i njen roditelj učitavaju se zajedno.
- `EcommerceApp/storefront_stock.py`: svježe učitani artikli mogu preskočiti ponovni SELECT istih cijena (`refresh_prices=False`). Cached kartice i dalje podrazumijevano osvježavaju cijene. Zbir lagera računa se jednim prolazom, umjesto pretraživanja svih količina za svaki artikal. Stanje, varijacije i akcije i dalje se čitaju iz baze na svakom pozivu.
- `EcommerceApp/plus_percent.py`: učitavaju se samo relevantne aktivne akcije i ID-evi odabranih artikala; više se ne učitavaju puni opisi svih artikala vezanih za druge „+ %” akcije.
- `EcommerceApp/social_proof.py`: prikaz obavijesti koristi jedan SELECT artikla umjesto prvo ID-a pa istog artikla. Pravila vidljivosti, dostupnosti i isključivanja ostaju ista.
- `EcommerceApp/template/product_detail.html`: preload glavne slike koristi isti `src`, `srcset` i `sizes` kao prikazana slika. Time se izbjegava nepotrebno paralelno preuzimanje originala kada preglednik izabere manju responsive sliku.
- `EcommerceApp/tests_storefront_stock.py`: regresije za zajedničko osvježavanje kartica i zaliha; postojeća provjera teksta stanja prilagođena je postojećem `<span>` oko količine pomoću `strip_tags`.

Ranija lokalna izmjena mobilnog skrolanja u `base.html` nije dio ovog mjerenja.

## Lokalno mjerenje

Isti SQLite katalog, 5872 artikla, dva otvaranja svake stranice. Upoređen je drugi prolaz nakon inicijalizacije cachea i predložaka. Mjeren je stvarni render viewa kroz RequestFactory uz CaptureQueriesContext i cProfile; ViewContent/analitički zapis su isključeni u oba mjerenja radi jednakih uslova i bez vanjskih zahtjeva.

| Stranica | SQL prije | SQL poslije |
|---|---:|---:|
| MATE HP WINTER BOOTS | 32 | 21 |
| Pretraga `masinica` | 20 | 19 |
| Početna | 47 | 46 |

Za stranicu artikla to je približno **34% manje SQL upita**. Brojevi zavise od artikla, preporuka i aktivnih akcija. Lokalno vrijeme toplog rendera artikla bilo je približno 60 ms prije i 59 ms poslije, uz instrumentaciju: ovo nije dokaz 34% bržeg otvaranja. PostgreSQL/Render TTFB, mrežni prijenos slika i vizuelni LCP nisu mjereni. Glavni dokaz poboljšanja je uklanjanje ponovljenih SQL obilazaka i nepotrebnog preload zahtjeva.

## Provjere

Pokrenuti su testovi lagera i promjena cijena, grupnog lookupa, „+ %”, rangiranja pretrage, dijeljenja slika, bundle prikaza i sličnih modela. Testno okruženje koristi izvorne statičke resurse (`StaticFilesStorage`) jer lokalni manifest nije osvježen za postojeći `product-sale-cards.css`.

Nove provjere potvrđuju da glavni i povezani artikli koriste jedan refresh paket, da promjena lagera važi već pri sljedećem otvaranju i da cached kartice i dalje dobijaju svježu cijenu. Django provjera, provjera nedostajućih migracija i `git diff --check` također su pokrenuti.

Završni rezultat: **39/39 testova prolazi**, Django provjera je uredna i nema nedostajućih migracija.
