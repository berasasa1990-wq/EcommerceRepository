# Uklanjanje zastarjelih modula naloga

Datum: 12. 10. 2026. Lokalna izmjena; bez deploymenta, git pusha i produkcijskih migracija.

## Rezultat

- Uklonjene su sve 94 registrirane rute pod `/nalog/magacin/` i `/nalog/admin/`. Stare adrese vraćaju 404; nema preusmjeravanja na početnu stranicu.
- Svih 36 postojećih ostalih ruta pod `/nalog/` sačuvano je s istom putanjom i imenom. Prijava, odjava i prikaz vlastitih narudžbi provjereni su lokalno.
- 81 zajednička operacija registrirana je jednom u `wms_service_urls.py`. Četiri operacije štampanja cijena koriste postojeće WMS rute umjesto dodatnih definicija.
- Interna imena `staff_magacin_*` za sačuvane operacije ostaju zbog poziva iz admina, panela, nacrta i zajedničkih templates. Takva imena više ne generiraju stare adrese.
- Django `/admin/`, modeli, migracije i podaci nisu mijenjani ovim čišćenjem. Prethodne lokalne izmjene za lager, plaćanje i WMS ostaju sačuvane.

## Uklonjeni kod

- `views.staff_admin_panel` i njegov `staff/admin_panel.html`.
- Stari ulazni warehouse view, pregled s tri statistička helpera, izvještaji, podešavanja, sync ekran i tri backup HTTP handlera s njihovim UI helperima.
- `pregled.html`, `izvjestaji.html`, `podesavanja.html`, `sync_istorija.html`, `backup.html` i `_backup_panel.html` u oba warehouse template direktorija.
- Obje datoteke `staff-magacin-menu.css`: bile su namijenjene uklonjenom sidebaru. Sačuvani zajednički warehouse CSS i JavaScript ostaju.
- Stari JS za otvaranje sidebar-a i presretanje mobilnog dugmeta Nazad. Kompatibilni `mgNavToggle` sada otvara postojeći WMS meni.
- Testovi starih dashboard/izvještaj/backup ekrana zamijenjeni su provjerom nedostupnosti starih ruta; ostali servisni testovi ostaju.

## Sačuvani zajednički kod

`views_magacin.py`, `magacin.py`, `manual_order_drafts.py`, `data_history.py`, `views_product_sets.py`, `views_ledger.py`, warehouse modeli i većina warehouse templates nisu zasebni neaktivni moduli: njih koriste WMS operacije, editor izvornih narudžbi, javna ponuda ili Django admin. Njihova poslovna obrada ostaje. Biblioteka `db_backup.py` također ostaje; uklonjen je samo stari HTTP interfejs.

Stari warehouse `base.html` u oba direktorija sada je mali adapter prema WMS shellu. Putanje sačuvanih templates nisu masovno preimenovane kako bi postojeći render i include pozivi nastavili raditi.

## Izmijenjene datoteke

- `EcommerceApp/urls.py`: uklonjene stare definicije i uključene specifične WMS servisne rute prije generičke WMS rute.
- `EcommerceApp/wms_service_urls.py`: jedinstvene kanonske adrese zajedničkih operacija.
- `EcommerceApp/wms_tools.py`: jedan wrapper po servisnoj ruti; postojeći role/method dekoratori sačuvani, WMS lock provjere ostaju za deset preuzetih sekcija.
- `EcommerceApp/views.py`, `views_magacin.py`, `warehouse_access.py`, `context_processors.py`, `wms_legacy.py`, `wms_price_print.py`: uklanjanje isključivo starog UI koda, WMS kontekst i aktivne navigacijske adrese.
- `EcommerceApp/template/staff/magacin/base.html`, `template/bera_reference/staff/magacin/base.html`, `template/bera_reference/staff/wms/legacy_tool_base.html`: zajednički WMS shell.
- `EcommerceApp/template/account/index.html`, `template/base.html` i sačuvani staff/warehouse templates: linkovi na `/panel/` i `/wms/`, te kanonske rute za cijene.
- Obje datoteke `static/*/staff-magacin.js`: WMS navigacija; obje `permanent-form-inputs.js` i social-proof partials: sačuvano isključenje polling-a na novoj adresi liste artikala.
- `tests_legacy_route_retirement.py`, `tests_wms_tools.py`, `tests_magacin.py`, `tests_subscriptions.py`, `tests_odoo_detach.py`, `tests_b2b.py`, `tests_retired_features.py`, `tests_warehouse_performance.py`: testovi novih adresa i ažurirana očekivanja navigacije.
- JSON inventari i ovaj izvještaj pod `reports/cleanup/`.

Izmjene u modelima, formi kreiranja narudžbi i drugim WMS datotekama koje su već bile u radnom direktoriju nisu nove izmjene ovog čišćenja.

## Provjere

- `venv/bin/python manage.py check --settings=scripts.bera_home_qa_settings`: prolazi.
- Ciljana grupa: **52 testa prolaze** (20,105 s). Obuhvaća svih 94 starih ruta za anonimnog korisnika i superusera, 36 sačuvanih account ruta, sve WMS servisne reverse definicije, GET odgovore servisnih ruta, literalne template linkove, dozvole, račun, editor narudžbe, desktop/mobilni shell, korpu/checkout i admin/panel.
- Template audit nije našao nevažeće obavezne literalne URL pozive. Dva opcionalna Django admin poziva (`as` varijanta za admindocs i reset lozinke) namjerno nemaju obaveznu URL definiciju.
- `node --check` za obje izmijenjene warehouse JS datoteke i `git diff --check`: prolaze.
- Sva mjerenja i testne migracije korišteni su samo u izoliranoj lokalnoj SQLite testnoj bazi. PostgreSQL/produkcija nisu kontaktirani.

### Šira postojeća testna grupa

Pokrenute su i `tests_magacin`, `tests_subscriptions`, `tests_odoo_detach` i `tests_retired_features`: **193 testa, 40 neuspješnih provjera i 2 greške**. Ne predstavljam ovu grupu kao potpuno zelenu.

Kontrolna dijagnostika sa starim warehouse shellom i istim lokalnim poslovnim servisima također ima 44 neuspjela testa. To nije čist prethodni checkout niti produkcijsko mjerenje. Preostali problemi obuhvaćaju zastarjela očekivanja oko trenutka skidanja lagera, nultog lagera/MP-a, partial pickinga i već ugašenih live funkcija. Ti poslovni dijelovi nisu mijenjani ovim čišćenjem.

Jedina dodatna razlika u završnom širem pokušaju bila je provjera teksta „Otkaži”: test ga je nalazio u starom storefront chrome-u, dok stvarni picking kontrolni element već glasi „Poništi”. Test sada provjerava stvarni cancel form i „Poništi”. **Naknadna grupa svih šest pogođenih postojećih testova prolazi**, uključujući taj slučaj, štampu cijena/deklaracija, brzi unos i Fali u MP. Nakon te promjene nije ponovljena cijela grupa od 193 testa; preostali neuspjesi navedeni su u `broader-test-results.json`.

Ukupno su zelene dvije ciljane grupe: **52 + 6 = 58 testova**. Za opću tvrdnju da je cijeli projekt bez grešaka potrebno je zasebno uskladiti staru poslovnu testnu grupu s trenutnim, ranije izmijenjenim pravilima.

## Ograničenja i rizici

- Ovo je provjera lokalnog koda. Produkcijske stare adrese će ostati dostupne do objave izmjena; produkcija nije mijenjana.
- Sačuvane podatkovne strukture mogu sadržavati historijske URL-ove. Nema masovnog prepisivanja baze; wrapper normalizira poznate URL vrijednosti u odgovorima sačuvanih servisa.
- Stari bookmark-i i vanjski linkovi više ne otvaraju stare module. To je traženo ponašanje (404).
- Lokalni SQLite test ne provjerava ponašanje PostgreSQL row-lockova pod konkurentnim opterećenjem. Poslovna obrada lagera i narudžbi nije mijenjana ovim zadatkom.
- Dokumentacija i arhivski snapshot-i nisu aktivna URL konfiguracija. Ne brišu se historijski materijali niti zajednički servisi samo zbog riječi „magacin” u nazivu.

## Potpun inventar uklonjenih URL-ova

Kanonska zamjena označava sačuvanu operaciju na novoj adresi; stare adrese se ne preusmjeravaju.

| Uklonjena adresa | Sačuvana operacija / zamjena |
| --- | --- |
| `/nalog/magacin/setovi/` | `/wms/artikli-u-setu/` |
| `/nalog/magacin/historija-podataka/fajl/<int:pk>/` | `/wms/operacije/historija-podataka/fajl/<int:pk>/` |
| `/nalog/magacin/nacrti/uvoz/` | `/wms/operacije/nacrti/uvoz/` |
| `/nalog/magacin/nacrti/sacuvaj/` | `/wms/operacije/nacrti/sacuvaj/` |
| `/nalog/magacin/nacrti/historija/` | `/wms/operacije/nacrti/historija/` |
| `/nalog/magacin/nacrti/<int:pk>/vrati/` | `/wms/operacije/nacrti/<int:pk>/vrati/` |
| `/nalog/magacin/historija-podataka/` | `/wms/operacije/historija-podataka/` |
| `/nalog/magacin/historija-podataka/unos/` | `/wms/operacije/historija-podataka/unos/` |
| `/nalog/magacin/duguje/` | `/wms/operacije/duguje/` |
| `/nalog/admin/` | Uklonjen stari UI |
| `/nalog/magacin/` | Uklonjen stari UI |
| `/nalog/magacin/pregled/` | Uklonjen stari UI |
| `/nalog/magacin/barkod-provjera/` | `/wms/operacije/barkod-provjera/` |
| `/nalog/magacin/dupli-barkodovi/<int:pk>/obrisi/` | `/wms/dupli-barkodovi/<int:pk>/obrisi/` |
| `/nalog/magacin/dupli-barkodovi/` | `/wms/dupli-barkodovi/` |
| `/nalog/magacin/artikli/` | `/wms/operacije/artikli/` |
| `/nalog/magacin/artikli/brzi-unos/` | `/wms/operacije/artikli/brzi-unos/` |
| `/nalog/magacin/artikli/brzi-unos/novi/` | `/wms/operacije/artikli/brzi-unos/novi/` |
| `/nalog/magacin/artikli/brzi-unos/<int:product_id>/` | `/wms/operacije/artikli/brzi-unos/<int:product_id>/` |
| `/nalog/magacin/artikli/lookup/` | `/wms/operacije/artikli/lookup/` |
| `/nalog/magacin/stampa-cijena/` | `/wms/stampa-cijena/` |
| `/nalog/magacin/stampa-cijena/ista/` | `/wms/stampa-cijena/ista/` |
| `/nalog/magacin/stampa-cijena/razlicite/` | `/wms/stampa-cijena/razlicite/` |
| `/nalog/magacin/stampa-cijena/print/` | `/wms/stampa-cijena/print/` |
| `/nalog/magacin/stampa-deklaracije/` | `/wms/stampaj-deklaracije/` |
| `/nalog/magacin/stampa-deklaracije/<int:pk>/stampa/` | `/wms/stampaj-deklaracije/<int:pk>/stampa/` |
| `/nalog/magacin/artikli/<int:pk>/` | `/wms/operacije/artikli/<int:pk>/` |
| `/nalog/magacin/artikli/<int:pk>/stampa/` | `/wms/operacije/artikli/<int:pk>/stampa/` |
| `/nalog/magacin/artikli/<int:pk>/stampa-barkod/` | `/wms/operacije/artikli/<int:pk>/stampa-barkod/` |
| `/nalog/magacin/artikli/<int:pk>/izmjena/` | `/wms/operacije/artikli/<int:pk>/izmjena/` |
| `/nalog/magacin/artikli/<int:pk>/istorija/` | `/wms/operacije/artikli/<int:pk>/istorija/` |
| `/nalog/magacin/lokacije/lookup/` | `/wms/operacije/lokacije/lookup/` |
| `/nalog/magacin/lokacije/<int:pk>/stampa/` | `/wms/operacije/lokacije/<int:pk>/stampa/` |
| `/nalog/magacin/lokacije/` | `/wms/operacije/lokacije/` |
| `/nalog/magacin/zalihe/` | `/wms/operacije/zalihe/` |
| `/nalog/magacin/rezervni-dijelovi/` | `/wms/rezervni-dijelovi/` |
| `/nalog/magacin/dnevno-skidanje-mp/` | `/wms/dnevno-skidanje/` |
| `/nalog/magacin/transferi/` | `/wms/operacije/transferi/` |
| `/nalog/magacin/popis/` | `/wms/popis-robe/` |
| `/nalog/magacin/popis/<int:pk>/` | `/wms/popis-robe/<int:pk>/` |
| `/nalog/magacin/popis/stampa/` | `/wms/popis-robe/stampa/` |
| `/nalog/magacin/popis-test/` | `/wms/popis-robe/test/` |
| `/nalog/magacin/provjera-lagera/` | `/wms/operacije/provjera-lagera/` |
| `/nalog/magacin/provjera-lagera/stampa/` | `/wms/operacije/provjera-lagera/stampa/` |
| `/nalog/magacin/fali-na-sajtu/` | `/wms/fali-u-mp/` |
| `/nalog/magacin/narudzbe/` | `/wms/operacije/narudzbe/` |
| `/nalog/magacin/narudzbe/nova/` | `/wms/operacije/narudzbe/nova/` |
| `/nalog/magacin/narudzbe/nova/bulk/` | `/wms/operacije/narudzbe/nova/bulk/` |
| `/nalog/magacin/narudzbe/vp/` | `/wms/operacije/narudzbe/vp/` |
| `/nalog/magacin/narudzbe/brza-posta/` | `/wms/operacije/narudzbe/brza-posta/` |
| `/nalog/magacin/narudzbe/brza-posta/<str:broj>/` | `/wms/operacije/narudzbe/brza-posta/<str:broj>/` |
| `/nalog/magacin/narudzbe/x-express/` | `/wms/operacije/narudzbe/x-express/` |
| `/nalog/magacin/kupci/` | `/wms/operacije/kupci/` |
| `/nalog/magacin/kupci/lookup/` | `/wms/operacije/kupci/lookup/` |
| `/nalog/magacin/kupci/sacuvaj/` | `/wms/operacije/kupci/sacuvaj/` |
| `/nalog/magacin/kupci/loyalty/` | `/wms/operacije/kupci/loyalty/` |
| `/nalog/magacin/narudzbe/stampa/` | `/wms/operacije/narudzbe/stampa/` |
| `/nalog/magacin/narudzbe/stampa-kolicine/` | `/wms/operacije/narudzbe/stampa-kolicine/` |
| `/nalog/magacin/narudzbe/packing/` | `/wms/operacije/narudzbe/packing/` |
| `/nalog/magacin/narudzbe/packing/izbor/` | `/wms/operacije/narudzbe/packing/izbor/` |
| `/nalog/magacin/narudzbe/validiraj/` | `/wms/operacije/narudzbe/validiraj/` |
| `/nalog/magacin/narudzbe/odstampano/` | `/wms/operacije/narudzbe/odstampano/` |
| `/nalog/magacin/pakovanje/` | `/wms/operacije/pakovanje/` |
| `/nalog/magacin/pakuj/` | `/wms/operacije/pakuj/` |
| `/nalog/magacin/pakuj/provjera/` | `/wms/operacije/pakuj/provjera/` |
| `/nalog/magacin/pakuj/sken/` | `/wms/operacije/pakuj/sken/` |
| `/nalog/magacin/pakuj/<str:broj>/oslobodi/` | `/wms/operacije/pakuj/<str:broj>/oslobodi/` |
| `/nalog/magacin/pakuj/<str:broj>/stampaj-zapakovano/` | `/wms/operacije/pakuj/<str:broj>/stampaj-zapakovano/` |
| `/nalog/magacin/narudzbe/<str:broj>/barkod.png` | `/wms/operacije/narudzbe/<str:broj>/barkod.png` |
| `/nalog/magacin/pakuj/<str:broj>/` | `/wms/operacije/pakuj/<str:broj>/` |
| `/nalog/magacin/uvoz/` | `/wms/uvoz/` |
| `/nalog/magacin/uvoz/novi/` | `/wms/uvoz/novi/` |
| `/nalog/magacin/uvoz/<int:pk>/cijene/` | `/wms/uvoz/<int:pk>/cijene/` |
| `/nalog/magacin/uvoz/<int:pk>/stampa/` | `/wms/uvoz/<int:pk>/stampa/` |
| `/nalog/magacin/uvoz/<int:pk>/popis/stampa/` | `/wms/uvoz/<int:pk>/popis/stampa/` |
| `/nalog/magacin/uvoz/<int:pk>/popis/` | `/wms/uvoz/<int:pk>/popis/` |
| `/nalog/magacin/uvoz/<int:pk>/` | `/wms/uvoz/<int:pk>/` |
| `/nalog/magacin/nivelacije/akcija/nova/` | `/wms/nivelacije/akcija/nova/` |
| `/nalog/magacin/nivelacije/akcija/<int:pk>/stampa/` | `/wms/nivelacije/akcija/<int:pk>/stampa/` |
| `/nalog/magacin/nivelacije/akcija/<int:pk>/etikete/` | `/wms/nivelacije/akcija/<int:pk>/etikete/` |
| `/nalog/magacin/nivelacije/akcija/<int:pk>/` | `/wms/nivelacije/akcija/<int:pk>/` |
| `/nalog/magacin/nivelacije/stampa/` | `/wms/nivelacije/stampa/` |
| `/nalog/magacin/nivelacije/izmjenjene/<int:pk>/` | `/wms/nivelacije/izmjenjene/<int:pk>/` |
| `/nalog/magacin/nivelacije/` | `/wms/nivelacije/` |
| `/nalog/magacin/dobavljaci/` | `/wms/operacije/dobavljaci/` |
| `/nalog/magacin/ponude/` | `/wms/kreiraj-ponudu/` |
| `/nalog/magacin/ponude/<int:pk>/` | `/wms/kreiraj-ponudu/<int:pk>/` |
| `/nalog/magacin/ponude/<int:pk>/prihvati/` | `/wms/kreiraj-ponudu/<int:pk>/prihvati/` |
| `/nalog/magacin/izvjestaji/` | Uklonjen stari UI |
| `/nalog/magacin/podesavanja/` | Uklonjen stari UI |
| `/nalog/magacin/sync/istorija/` | Uklonjen stari UI |
| `/nalog/magacin/backup/` | Uklonjen stari UI |
| `/nalog/magacin/backup/preuzmi/<str:name>/` | Uklonjen stari UI |
| `/nalog/magacin/backup/preuzmi-sada/` | Uklonjen stari UI |
