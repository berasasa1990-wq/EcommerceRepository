# BERA izgled artikla, korpe i checkouta

Preneseni izgled aktivan je na postojećim URL-ovima:

- `/artikal/<slug>/` — postojeći `product_detail` view.
- `/korpa/` — postojeći `cart_view`, uz iste POST akcije za količine, uklanjanje i kupone.
- `/narudzba/` — postojeći `checkout`, postojeća forma i kreiranje `Order`/`OrderItem`.

Izvorni kod nalazi se u BERA `product_detail.html`, `cart.html`, `checkout.html`, njihovom `base.html` i uključivanim partials. Kopije koriste `EcommerceApp/template/bera_reference/` i `EcommerceApp/static/bera_reference/`, prethodno prenesene za početnu. Potpun spisak kopiranih resursa je `bera_home_import_manifest.json`.

## Izmjene

`EcommerceApp/views.py` mijenja završno renderovanje ovih stranica i dodaje podatke potrebne BERA prikazu. Obračun cijena, kuponi, lager, validacija, rezervacija artikala, narudžbe i integracije nisu zamijenjeni izvornim BERA backendom. URL konfiguracija i modeli nisu mijenjani.

`EcommerceApp/bera_home.py` sada izdvaja zajedničke postavke prodavnice u `storefront_context` i koristi ih na svim prenesenim stranicama. `EcommerceApp/bera_product_features.py` je kopirani BERA helper za prikaz karakteristika iz postojećeg opisa.

`bera_reference/partials/product_bundle_set.html` zadržava BERA izgled, ali šalje postojeći Carpologija `akcija_id` za kupovinu seta. To sprečava zamjenu dosadašnje bundle akcije novijom BERA gratis akcijom.

`bera_reference/js/product-mobile-zoom.js` dodaje uvećanje dodirom slike na mobilnom uređaju, bez promjene CSS-a. Izvorni mobilni raspored može preklopiti dugmad zoom i Druge opcije; dodir slike osigurava dostupno uvećanje. Prevlačenje kroz galeriju ostaje odvojeno od kratkog dodira.

Stari templates i statičke datoteke nisu obrisani. Superuser može dodati `?izgled=prethodni` na svaki navedeni URL radi pregleda prethodnog izgleda.

## Provjere

Testovi koriste isključivo privremenu sintetičku bazu, testne slike i lokalni e-mail backend. Vanjski HTTP zahtjevi su blokirani; checkout test dodatno blokira obavijesti i sync. Kreiranje testne narudžbe izvršeno je u transakciji koja je u cijelosti vraćena.

- Poređenje izvora i odredišta na 1440 px i 390 px, prozor visine 900 px.
- Isti raspored mjerenih elemenata na sve tri stranice i obje širine.
- Završni snimci sve tri stranice na obje širine identični su piksel po piksel uz isti testni sadržaj.
- Bez JavaScript grešaka ili nedostajućih resursa u vizuelnom poređenju.
- Backend provjere: dodavanje, promjena količine, uklanjanje, prazna korpa, checkout validacija, isti ukupni iznos korpe i checkouta, kreiranje narudžbe i njenih stavki, čišćenje korpe nakon narudžbe, autentifikovane stranice i prethodni templates.
- Browser provjere galerije, tabova, količina, dodavanja, checkout forme i uklanjanja evidentirane su u `bera-storefront-visual/interactions.json`.
- Django system check i `collectstatic` sa WhiteNoise manifest backendom uspješni.
- Svih 514 izvornih datoteka iz početnog manifesta ostalo je nepromijenjeno.

Snimci i rezultati su u `reports/bera-storefront-visual/`. Poređenje se odnosi na isti sintetički sadržaj. Sve kombinacije stvarnih akcija i svih ostalih modula projekta nisu pojedinačno testirane.

## Zavisnosti

Nema novih paketa niti migracija. Stranice koriste postojeće podatke, medijske datoteke i postavke novog sajta. Modeli ove verzije nemaju BERA 360° galeriju; obična galerija i uvećanje rade sa postojećim slikama. Pri objavi izvršiti uobičajeni `collectstatic`. Nije izvršen push, deploy, produkcijska migracija, stvarno slanje e-mailova ili naplata.
