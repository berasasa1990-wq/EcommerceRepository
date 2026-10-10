# Prenos lokacija iz magacina u WMS

U lokalnom novom projektu dodijeljene su stare lokacije i količine: **2.434 artikla, 513 lokacija i 3.368 zapisa zaliha**. Postojeća lokacija Garaza ostaje sačuvana i koristi se pri prenosu; WMS ukupno ima 513 lokacija.

Izvor su postojeće tabele `WarehouseLocation` i `WarehouseStock` u lokalnom `db.sqlite3`; nije čitana ni mijenjana baza projekta BERA-webshop. Šifre starih lokacija su nazivi WMS lokacija, a dodatni nazivi/opisi preneseni su u opis. Glavna lokacija artikla bira se prema starom redoslijedu, s prednošću lokacije s pozitivnom količinom. Sve ostale lokacije i nulte količine također su sačuvane.

Stare fizičke količine, tri rezervisana komada, kataloške količine i zastavice dostupnosti nisu mijenjani. Njihovi otisci su provjereni prije i poslije upisa. Svaki WMS zapis odgovara starom paru artikal/lokacija i njegovoj količini.

Korištena je atomska management komanda `import_magacin_locations`. Podrazumijevano radi samo pregled; upis zahtijeva `--apply --backup <nova-putanja>`. Ponovljeni prenos ne dodaje duplikate. Postojeće različite WMS zalihe, negativne količine ili varijante koje novi model ne može sačuvati zaustavljaju prenos bez upisa.

Prošlo je pet testova: pregled bez izmjena, tačan prenos sa čuvanjem rezervacija, ponovljivost, zaštita konflikata i odbijanje negativnih zaliha. Stvarna baza pregledana je nakon upisa i sve količine su tačno podudarne.

Backup ranijih WMS veza: `reports/magacin_wms_before_import.json`. Rezultati provjere: `reports/magacin_wms_import_validation.json`. Novi pregled je dostupan pod `/wms/lokacije/` i pretragom `/wms/zalihe/?q=...`.

Postavke `scripts/warehouse_local_settings.py` ciljaju isključivo lokalnu bazu novog projekta i isključuju vanjske servise i dotenv. Nisu poslani emailovi niti pozvane integracije.

## Prenos na glavnom sajtu iz WMS podešavanja

Nakon deploya otvorite `/wms/podesavanje/` i koristite **Prenesi lokacije i količine iz magacina**. Stranica prikazuje broj artikala, lokacija i fizičkih komada u postojećem magacinu tog sajta. POST `/wms/podesavanje/magacin-import/` koristi istu atomsku funkciju kao management komanda; ne učitava lokalni snapshot ili lokalnu bazu.

Pristup zahtijeva aktivnog superusera, otključan modul WMS Zalihe i CSRF token. GET ne izvršava prenos. Postojeće iste veze se ponovo koriste; različite WMS zalihe ili glavne lokacije zaustavljaju cijeli prenos bez upisa. Magacin, rezervacije i kataloške količine ostaju nepromijenjeni. Varijante i negativne količine se odbijaju jer ih novi WMS model ne može vjerno prenijeti.

Fajlovi ove opcije: `EcommerceApp/wms_legacy_import.py`, `EcommerceApp/management/commands/import_magacin_locations.py`, `EcommerceApp/views_wms.py`, `EcommerceApp/urls.py`, `EcommerceApp/template/staff/wms/podesavanje.html`, `EcommerceApp/tests_magacin_wms_import.py`. Nema novih paketa ili migracija za ovu opciju. Produkcijski prenos se pokreće ručno dugmetom nakon deploya.

Provjera nove opcije: svih **13 testova prolazi** u izolovanoj testnoj bazi (5 testova CLI prenosa i 8 web testova: pregled, prenos i ponavljanje, GET, anonimni/staff pristup, CSRF, zaključan modul, konflikt i postojeće čuvanje podešavanja). Testovi ne koriste stvarne emailove, plaćanja ili produkcijsku bazu.
