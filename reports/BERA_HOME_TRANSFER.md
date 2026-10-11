# Prenos BERA početne stranice

Početna `/` koristi stvarne kopirane BERA komponente. Izvor `/Users/bera1990/BERA-webshop` korišten je samo za čitanje. Postojeći templates, static resursi i ostali views nisu obrisani. Superuser može otvoriti prethodnu početnu preko `/?izgled=prethodni`.

## Prenesene datoteke

- `EcommerceApp/template/bera_reference/`: kopirani templates sa lokalnim extends/include putanjama.
- `EcommerceApp/static/bera_reference/`: kopirani CSS, JavaScript, slike i fontovi; originalne CSS klase i responsive pravila sačuvani.
- `EcommerceApp/templatetags/bera_warehouse_access.py`: kompatibilnost sa postojećim provjerama pristupa.
- `EcommerceApp/bera_home.py`: povezivanje sa postojećim modelima i postavkama.
- `EcommerceApp/views.py`: izbor prenesene početne i podaci za postojeće bannere.
- `config/bera_home_content.json`: podesivi naslovi sekcija i newsletter tekst.
- `EcommerceApp/static/bera_reference/js/newsletter-subscribe.js`: prijava preko postojećeg newsletter endpointa, bez promjene rasporeda forme.

Tačan spisak svih 514 kopiranih datoteka, izvornih putanja i SHA vrijednosti nalazi se u `bera_home_import_manifest.json`. Graf 50 templates od kojih zavisi početna nalazi se u `bera_home_dependencies.json`. Kopiran je širi skup templates kako bi uključene komponente radile samostalno; ostale stranice nisu prebačene na novi base.

## Podesivi podaci

Logo i kontakt čitaju se iz postojećeg `SiteSettings`, kao i naziv organizacije, Facebook, Instagram i Messenger. Naziv može koristiti i postojeći `SITE_NAME`; e-mail i telefon imaju postojeće fallback postavke. Artikli, banneri, kategorije, brendovi i SEO tekst koriste bazu novog projekta. Slike proizvoda i sadržaj baze nisu kopirani iz izvora.

Za izdvojenu i novu ponudu koriste se postojeći modeli. Ako odredišna baza već ima tabelu `EcommerceApp_homebestsellerproduct`, čita se njen aktivni izbor najprodavanijih artikala; bez te tabele ostaje postojeći izbor proizvoda. Nisu uvedene niti izvršene migracije.

## Provjere

Oba projekta pokrenuta su sa istom sintetičkom lokalnom bazom i testnim slikama, bez učitavanja .env. E-mail koristi memorijski backend; vanjski HTTP zahtjevi i plaćanja isključeni su u QA postavkama.

- Širine 1440 px i 390 px, visina prozora 900 px: snimci početnih stranica identični su piksel po piksel. Dimenzije snimaka su 1440×3079 i 390×4885.
- Svih 27 mjerenih elemenata ima isti raspored; nema JavaScript grešaka ni neuspjelih učitavanja resursa.
- Na obje širine prošli su dodavanje u korpu, prikaz artikla u korpi, lista želja, pretraga i newsletter prijava.
- Svih 50 zavisnih templates uspješno se kompajlira.
- Početna, korpa, prijava i testna stranica artikla vraćaju 200; prethodna početna dostupna je superuseru.
- `collectstatic` sa WhiteNoise manifest backendom završen je uspješno, bez brisanja postojećih static datoteka.
- SHA provjera kopiranih izvornih datoteka potvrđuje da izvor nije mijenjan.

Snimci i rezultati su u `reports/bera-home-visual/`. Vizuelno poređenje odnosi se na isti testni sadržaj; stvarne slike i izbor proizvoda zavise od sadržaja odredišne baze.

## Preostale zavisnosti i objava

Nema novih Python ili JavaScript paketa. Pri objavi treba uključiti kopirane datoteke i izvršiti uobičajeni `collectstatic`. Postavke prodavnice i medijski resursi moraju biti dostupni na novom sajtu. Nije izvršen push, deploy, produkcijska migracija, slanje stvarnih e-mailova ili naplata.

`scripts/import_bera_home.py` služi početnom prenosu, nije runtime zavisnost. Njegovo ponovno pokretanje prepisalo bi naknadne prilagodbe u kopiranom namespaceu. QA skripte koriste isključivo privremenu sintetičku bazu i nisu produkcijske postavke.
