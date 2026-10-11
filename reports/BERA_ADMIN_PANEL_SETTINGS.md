# Admin i podešavanja panela

`/panel/podesavanja/` prikazuje sve postojeće modele iz admina EcommerceApp i auth, uz postojeći editor postavki sajta. Modeli koji su ranije bili skriveni u panelu (narudžbe, magacinski lager, dobavljači, kretanja, itd.) sada su dostupni prema njihovim postojećim dozvolama. Registracije i postojeće admin adrese nisu uklonjene. Oba editora koriste iste modele i istu konfiguriranu bazu.

BERA „Moduli / Dozvole” prenesen je iz izvornog modela, admina i predloška. Pojavljuje se prvi u admin navigaciji i dostupan je samo superkorisniku. Nije dostupan kroz panel editore. Zadane vrijednosti svih prekidača su uključene. WMS i akcije koriste postojeće provjere zaključavanja. Čuvanje prekidača ne mijenja količine artikala ni skladišne podatke. Zadržane su postojeće admin dozvole korisnika kako se ne bi prekinuo dosadašnji ovlašteni pristup; novi modul zahtijeva superkorisnika.

Dodata migracija `0313_bera_module_permissions` koristi zasebnu tabelu `EcommerceApp_bera_modulepermissions` radi izbjegavanja sudara sa ranije postojećim tabelama. Nije izvršena na stvarnoj bazi. Prije nje moraju biti primijenjene 0311 i 0312. Dok migracija nije primijenjena, ostale stranice rade, novi modul je skriven, a direktna lista modula daje objašnjenje 503.

Provjere na sintetičkoj bazi uz rollback: dostupnost svih podešavanja, odvojeni admin modul, unos i prikaz prekidača, zaključavanje WMS-a i akcija, ponovno uključivanje i povrat pristupa. WMS regresije (prenos robe, ručne/web narudžbe, rezervacije, stari magacin i dozvole) prolaze. Panel regresije sada obuhvataju 32 liste i forme; CRUD i provjere dozvola prolaze i prije nove migracije. Django check i makemigrations --check --dry-run prolaze.

Fajlovi: `bera_panel_settings.py`, `bera_panel_admin.py`, `bera_panel_modules.py`, `models.py`, `admin.py`, novi `admin_site.py`, `EcommerceProject/admin_apps.py`, `EcommerceProject/settings.py`, `template/admin/EcommerceApp/modulepermissions/change_form.html`, migracija 0313. Ispravljeno je i dupliciranje jQuery zavisnosti u panelu. Izvorni projekat nije mijenjan; nisu izvršene produkcijske integracije ni automatski deploy.
