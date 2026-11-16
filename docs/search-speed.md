# Ubrzanje pretrage webshopa

## Izmjene

- `EcommerceApp/search_results.py`: izvor za paginator koji broji rezultate jednom i učitava objekte, varijacije i akcije samo za traženu stranicu. Ostaje 49 rezultata po stranici i raniji plafon od 5000 pogodaka. Posebni filteri cijene, veličine, akcije i eksplicitno sortiranje zadržavaju postojeći puni obračun radi kompatibilnosti.
- `EcommerceApp/views.py`: priprema tipfelera i tagova dijeli se unutar jednog HTTP zahtjeva između pretrage, rangiranja i filtera veličina. Nema cacheiranja cijena ili rezultata među korisnicima.
- Brojanje varijacija više ne zahtijeva JOIN i GROUP BY svih kolona artikla. Za izbor filtera veličina koristi se EXISTS umjesto grupisanja cijelog rezultata.
- Uklonjen je dupli uslov pretrage šifri jer ih postojeća SQL funkcija već normalizira.
- API prijedloga koristi cijene iz upravo učitanih artikala bez drugog SELECT-a istih cijena; lager, varijacije i akcije i dalje se osvježavaju. Rangiranje, format odgovora i limit pet ostaju isti.
- `EcommerceApp/tests_search_ranking.py`: regresije za lazy paginaciju, jedno pripremanje upita i aktuelne cijene prijedloga.

## Mjerenje

Lokalni SQLite katalog od 5872 artikla. Kod iz `git HEAD` upoređen je s radnim kodom, na istim podacima. Medijana tri ponovljena topla poziva nakon jednog zagrijavanja, stvarni render viewa bez vanjskih servisa; zapis analitike pretrage mockiran u oba slučaja. Ovo nisu produkcijska mrežna vremena niti mjerenje na telefonu.

| Upit / prikaz | Prije | Poslije | SQL prije / poslije |
|---|---:|---:|---:|
| `masinica`, puna stranica | 86,13 ms | 93,49 ms | 18 / 17 |
| `mate`, puna stranica | 181,72 ms | 126,57 ms | 18 / 17 |
| `MT12221`, puna stranica | 172,42 ms | 146,93 ms | 18 / 17 |
| `masinica`, prijedlozi | 22,01 ms | 22,06 ms | 7 / 6 |
| `mate`, prijedlozi | 33,85 ms | 31,73 ms | 7 / 6 |
| `MT12221`, prijedlozi | 69,45 ms | 52,59 ms | 7 / 6 |

Najveća korist je kod široke pune pretrage: manje prenesenih redova, objekata i varijacija prije prve stranice. Lokalni mali upit `masinica` nije ubrzan; dodani COUNT može imati mali trošak kada rezultata nema mnogo. Za prijedloge je uklonjen jedan SQL obilazak. PostgreSQL/Render rezultat nije izmjeren; deploy nije pokrenut.

## Provjere

30 testova prolazi: nove regresije, rangiranje i paginacija, scope pretrage, slike, lookup performanse, nazivi kataloga i postojeći redoslijed kategorija/brendova. Nova provjera sa 120 pogodaka potvrđuje da čitanje stranice učitava samo 49 objekata, a ne sve rezultate. Provjeren je i isti prvi rezultat API-ja i pune pretrage. Django check i provjera nedostajućih migracija prolaze. Nove migracije nisu potrebne.

## Pojednostavljena pretraga: slika, naziv i cijena

Po novom zahtjevu i puna lista i desktop/mobilni prijedlozi prikazuju samo sliku, naziv i cijenu. Kartica/link otvara neizmijenjenu punu stranicu artikla. Iz prijedloga uklonjeni su šifra, kategorija, brend, stanje, pakovanje i direktno dodavanje u korpu; API red vraća samo `naziv`, `slug`, `url`, `image`, `price`. Ovo je namjerna promjena ranijeg API formata za nove pojednostavljene klijente.

Osnovni queryset nema JOIN na kategorije/brendove, opis ni pune podatke artikla. Odvojeno se grupno učitavaju samo polja varijacija i aktivnih akcija potrebna za tačnu cijenu; ne učitava se magacinski lager. Na punoj stranici pretrage ne pripremaju se više filteri veličina i bogate katalog-kartice niti se poziva osvježavanje lagera za njihove rezultate. Paginacija, podudaranje šifri, dijakritika i rangiranje ostaju. Filteri iz URL-a koji traže detaljan obračun i dalje koriste postojeći filter pipeline.

Izmijenjeni su `views.py`, novi `template/partials/simple_search_results.html`, `template/home.html`, oba klijenta prijedloga (`main.js`, generirani `main.min.js`, `mobile-search.js`) i verzije JS resursa u `base.html`/`mobile_search_dialog.html`. Testovi sada provjeravaju minimalan API, odsustvo čitanja lagera/kategorija/opisa i jednostavan prikaz bez dugmadi za korpu.

Lokalni završni uzorak pokazuje 11 SQL upita za punu listu umjesto ranijih 17, a 5 za prijedloge umjesto 6 (bez analitičkog zapisa u mjerenju). Vanjski header i navigacija na punoj stranici i dalje koriste svoj uobičajeni kontekst. Nema novih migracija i nije izvršen deploy.

Završna provjera pojednostavljenog prikaza: 32 testa prolaze u SQLite testnom okruženju s direktno kreiranom trenutnom shemom; JavaScript syntax provjere i Django check prolaze. Ovaj rezultat se odnosi na sinhroniziranu trenutnu testnu shemu, a ne na izvršavanje svih historijskih migracija. Zahvat nema izmjena modela ili migracija.
