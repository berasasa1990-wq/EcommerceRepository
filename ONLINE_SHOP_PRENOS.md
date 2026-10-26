# Novi izgled uz izvorni Ecommerce backend

Stanje nakon zahtjeva za povrat Monri/X-Express integracije, magacina, veleprodaje, API-ja i kompletnog kataloga — 26.10.2026.

Aktivna aplikacija ponovo je `EcommerceApp` s izvornim `EcommerceProject.settings`, rutama, autentikacijom i poslovnom logikom. Novi plavo-tirkizni dizajn iz OnlineShopProject prenesen je na početnu stranicu i javni header/footer. Magacin, administracija i veleprodaja zadržavaju svoje postojeće radne prikaze.

## Podaci

Koristi se izvorna baza `db.sqlite3` i izvorni media direktorij, prema ranijim postavkama. Sačuvano je svih **5.871 artikala**, **29 kategorija** i **6 brendova**, uz izvorne ID-ove, šifre, cijene, varijacije, korisnike i narudžbe. Nije rađena konverzija niti zamjena baze manjim katalogom iz OnlineShopProject.

Ranije kopije `online-shop.sqlite3` i `online-shop-media/` ostaju na disku, ali ih aktivna aplikacija ne koristi. Preneseni direktoriji `shop`, `OnlineShopProject`, `bera_control` i `bera_licensing` ostaju kao neaktivna referenca; nisu instalirane Django aplikacije ovog backenda.

## Vraćene funkcije

- Monri checkout, forme, potpisani callback, potvrda/status i povrat plaćanja.
- X-Express kreiranje pošiljki i ranije funkcije obrade narudžbi.
- Magacin, lokacije, zalihe, uvozi, narudžbe, izvještaji i ranije administrativne rute.
- Veleprodajni/B2B portal, zasebna prijava, katalog i korpa.
- Katalog API, partner stock API i ranije sync rute.
- Izvorna korpa, izbor varijacije, popusti i post-order ponude.
- Raniji newsletter endpoint, korisničke rute i informativne stranice.

Javni prikaz koristi iste izvorne modele i handlere. Dugmad artikala šalju izvorne slug/ID/variation vrijednosti u postojeći tok korpe; nije korišten nepovezan novi model narudžbi. Novi header vodi u postojeće naloge, magacin i veleprodaju.

## Važne granice

**Monri kod je uključen, ali lokalna naplata nije spremna:** `MONRI_ENABLED=True` i okruženje je `test`, dok su `MONRI_MERCHANT_KEY` i `MONRI_AUTHENTICITY_TOKEN` prazni. Ključeve treba unijeti u serversku konfiguraciju/`.env`. Nisu izvršene stvarne kartične naplate.

X-Express korisničko ime i lozinka postoje u lokalnim postavkama. Nije izvršena prijava kod pružaoca niti kreirana stvarna pošiljka, pa time nisu potvrđeni važenje pristupnih podataka i vanjska dostupnost servisa.

Magacin i veleprodaja vraćeni su s postojećim ponašanjem. Širi testovi imaju ranija neslaganja očekivanja i implementacije, uključujući picking, manjkove, rezervacije i pojedine prikaze. Ovo povezivanje ne rješava te ranije probleme.

## Provjere

- `manage.py check` prolazi; provjerene su postojeće migracije bez izmjene baze.
- **63 ciljana testa prolaze:** novi testovi povezivanja, postojeći Monri testovi, partner stock API, dostupnost korpe, sekcije početne i četiri testa stranica magacina.
- U pregledniku potvrđeni su dodavanje izvornog artikla u korpu (brojač 0 → 1), lista želja, otvaranje popup prozora brendova i zatvaranje tipkom Escape.
- Mobilni prikaz: dvije kolone na 390 px, bez horizontalnog prelijevanja dokumenta.
- Napravljeni su snimci `exports/integrated-shop-desktop.png` i `exports/integrated-shop-mobile.png`.

Širi skup prije korekcije nedostajućeg CSS-a: 276 testova, 43 neuspješna očekivanja i 5 grešaka. Isti testovi magacina/veleprodaje na izvornom kodu i predlošcima iz backupa: 224 testa, identičnih 43 neuspješnih očekivanja i 5 grešaka. Poređenjem naziva potvrđeno je da nije bilo novih padova. Četiri greške uzrokovao je poziv nepostojećeg `css/staff-overview-reference.css`; ta referenca je uklonjena i sva četiri odgovarajuća testa potom prolaze. Ostaje ranija greška za `cancelled` rezultat pickinga i ranija neuspješna očekivanja; kompletan širi skup nakon te male korekcije nije ponovo pokretan.

## Pokretanje i povrat

```sh
venv/bin/python manage.py runserver
```

- Trgovina: http://127.0.0.1:8000/
- Magacin: http://127.0.0.1:8000/nalog/magacin/
- Veleprodaja: http://127.0.0.1:8000/veleprodaja

Koriste se originalni Ecommerce korisnici/pristupni podaci. Aktivacija vanjskih servisa i produkcijski deployment nisu izvršeni.

„Restore Backup“ i dalje vraća kod na originalnu tačku prije eksperimentalnih izmjena, prema `RESTORE_BACKUP.md`. Dodatna kopija prve prenesene verzije koda je `backups/online-shop-before-integration.tar.gz`.
