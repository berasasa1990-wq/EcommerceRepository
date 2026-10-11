# BERA WMS magacin

Panel ikonica WMS Magacin sada otvara stvarni BERA WMS preko `/wms/`. Dostupni su i `/wms`, `/panel/wms` i `/panel/wms/`. Prethodni pregled WMS modela ostaje na `/panel/wms/sekcije/`, a stari magacin na postojećim `/nalog/magacin/...` URL-ovima.

## Preneseni kod

- `EcommerceApp/views_wms.py`: stvarni BERA views za zalihe, narudžbe, odvajanje, prenosnice, lokacije, kupce, podešavanje, štampanje i Excel import.
- `EcommerceApp/wms_orders.py`, `EcommerceApp/wms_excel.py`: izvorni helperi, uz povezivanje s lokalnim modelima.
- `EcommerceApp/template/bera_reference/staff/wms/`: kopirani BERA templates, uključujući sidebar, tabele, pretrage, dijaloge, forme, picking, fakture, najave i barkod lokacije.
- `EcommerceApp/static/bera_reference/css/bera-wms-independent.css` i povezani resursi: izvorni stilovi kopirani tokom početnog prenosa.
- `EcommerceApp/models.py`: dodani WMS modeli i posebna metadata evidencija primarne lokacije. Postojeća Product tabela nije proširena kolonom koja bi pokvarila postojeće stranice prije migracije.
- `EcommerceApp/wms_legacy.py`: povezivanje WMS-a s postojećim magacinom, njegovim lokacijama, zalihama, rezervacijama i završavanjem webshop narudžbi.
- `EcommerceApp/urls.py`: novi WMS URL-ovi; postojeći magacinski URL-ovi nisu uklonjeni.
- `EcommerceApp/templatetags/bera_warehouse_access.py`: postojeći storefront radi i dok nove WMS tabele nisu kreirane.

Naziv, logo i kontakt i dalje čitaju postavke novog projekta. Izvorni BERA projekat nije mijenjan; baza, .env i kupci iz izvornog projekta nisu kopirani.

## Usklađivanje postojećeg lagera

Postojeći WarehouseStock ostaje autoritet za stanje. WMS pri otvaranju usklađuje svoj prikaz s postojećim zalihama i mapira lokacije, bez promjene fizičkih količina. WMS prenos robe koristi postojeći transfer servis; korekcije koriste postojeći magacinski servis i bilježe kretanja. Rezervisane količine ne mogu se premjestiti mimo obrade narudžbe.

Završavanje webshop narudžbe prenosi potvrđene stavke u postojeći picking i poziva postojeću validaciju lagera. Ponovno završavanje ne skida zalihu ponovo. Količine po varijacijama nisu spljoštene: direktna WMS korekcija takve lokacije odbija se porukom da se varijacije uređuju u postojećem magacinu. Postojeće varijacijske funkcije ostaju dostupne.

## Migracije — potrebna aktivacija

Dodane su **0311_bera_wms** (WMS evidencije) i **0312_wms_legacy_location** (veza s postojećim lokacijama). Nisu izvršene na korisnikovoj lokalnoj niti produkcijskoj bazi. Prije aktivacije WMS-a na tom okruženju treba primijeniti ove migracije na odgovarajuću bazu. Ako tabele nisu spremne, samo WMS prikazuje jasnu poruku i HTTP 503; stari magacin i storefront nastavljaju raditi.

Ako odredišna baza već ima WMS tabele iz druge grane ili izvora, prije primjene treba uskladiti stvarnu šemu i migration history. Migracije ne treba naslijepo označavati kao izvršene. Nema novih paketa; openpyxl je već u requirements.

## Provjere

Sve provjere izvršene su isključivo na zasebnoj sintetičkoj bazi `/private/tmp/bera-wms-qa.sqlite3`, s lokalnim medijima, memorijskim e-mail backendom i blokiranim vanjskim HTTP zahtjevima. Dodatne testne tabele/kolone kreirane su samo u toj privremenoj bazi; testne poslovne izmjene vraćene su transakcijom.

- Svih sedam sekcija: isti raspored kao izvor na širinama 1440 px i 390 px; bez JS grešaka i nedostajućih resursa.
- Trinaest snimaka identično piksel po piksel; mobilni Kupci ima samo razlike do 1 RGB vrijednosti, bez piksela s razlikom preko 10.
- Postojeće zalihe vidljive u WMS-u.
- Prenosnica mijenja postojeće lokacijske količine; ponovljena potvrda ne prenosi robu ponovo.
- Kreiranje VP narudžbe, odvajanje i završavanje ažuriraju postojeći lager.
- Checkout narudžba završena kroz WMS pravilno validira postojeći lager, bez dvostrukog skidanja.
- Prenos rezervisane robe odbijen, bez promjene količina ili rezervacija.
- Pregled narudžbe, račun, faktura, najava, lokacija, barkod lokacije i Excel template vraćaju 200.
- Neovlašteni korisnik nema pristup WMS-u.
- Početna, artikal i korpa rade i bez nove WMS metadata tabele; stari magacin ostaje dostupan.
- Django system check, provjera migration state i whitespace provjera prolaze.

Snimci i rezultati su u `reports/bera-wms-visual/`; provjere su u `scripts/bera_wms_regression.py`, `scripts/bera_wms_visual_qa.cjs` i `scripts/bera_wms_qa_settings.py`.

Nije izvršen deploy, produkcijska migracija, stvarno slanje e-mailova ili naplata. Sve kombinacije stvarnih artikala, varijacija, setova, plaćenih kartičnih narudžbi i Excel datoteka nisu pojedinačno provjerene.


## Ispravka sudara postojećih tabela

Migracija `0311_bera_wms` koristi zasebne nazive tabela `EcommerceApp_bera_*`, a njena imenovana ograničenja prefiks `bera_`. Modeli koriste iste eksplicitne nazive. Time postojeće `EcommerceApp_wms*` i `EcommerceApp_productwmsstock` tabele ostaju sačuvane. Nisu promijenjeni URL-ovi niti Python nazivi modela. Ovo je ispravka nove, neprimijenjene migracije; ne koristi `--fake`, brisanje tabela ili izmjenu ranije evidencije migracija.

Na lokalnoj bazi provjereni su isključivo nazivi tabela i evidencija migracija, bez čitanja podataka kupaca. `0311_bera_wms` nije evidentirana kao primijenjena; postoje drugačije imenovane stare migracije sa istim numeričkim prefiksima, što Django dozvoljava.

Migracije 0311 i 0312 uspješno su primijenjene samo na odvojenoj sintetičkoj QA bazi, uz već postojeće stare WMS tabele. Broj redova svih prethodno postojećih WMS tabela ostao je isti. Ponovljene funkcionalne provjere WMS-a i starog magacina prošle su, kao i Django system check i makemigrations --check --dry-run. Stvarna lokalna i produkcijska baza nisu migrirane.
