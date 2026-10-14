# Čuvanje i oporavak backupa

Aplikacija ne briše stare kopije i ne koristi rok zadržavanja. Svako kreiranje
baze i media arhive dobija novo ime, uključujući više poziva u istoj sekundi.
Opcija `backup_db --force` / `backup_r2 --force` ostaje kompatibilna, ali ne
prepisuje postojeću kopiju.

## Baza

`python manage.py backup_db` ili Magacin → Backup na server pravi novu kopiju.
Baza se prvo zapisuje u `.partial` fajl, provjerava i sinhronizuje na disk.
Tek završena kopija pojavljuje se na listi za vraćanje. Pored nje je `.sha256`
kontrolni zbir; pri kopiranju na drugi disk sačuvati oba fajla. Prekinuti zapisi
ostaju kao `.partial` radi pregleda, ali se ne nude za restore. Stari backupi bez
kontrolnog zbira ostaju podržani uz provjeru strukture.

Prije restore-a provjerava se kopija i pravi nova sigurnosna kopija trenutne
baze. SQLite se vraća kroz transakcijsku backup funkciju. PostgreSQL restore
koristi jednu transakciju i prekida na grešci. `pg_dump` i `pg_restore` moraju
biti instalirani i kompatibilni s verzijom serverske baze.

## Trajni server

Na Renderu `RENDER_DISK_PATH` mora pokazivati na stvarno montiran trajni disk.
Sama varijabla ili običan folder nisu dokaz trajnosti. `MAGACIN_BACKUP_DIR`,
ako je postavljen, mora biti unutar tog diska. Bez toga aplikacija odbija
„Backup na server”; direktno preuzimanje na računar ostaje dostupno.

Nije uvedeno automatsko raspoređivanje backupa niti je mijenjana konfiguracija
aktivnog servera. Prije puštanja izmjena provjeriti mount, slobodan prostor,
serverske cron poslove i eventualna vanjska pravila brisanja. Na drugim
hosting okruženjima administrator mora potvrditi trajnost izabranog foldera.
Neograničeno zadržavanje zahtijeva povećavanje prostora; aplikacija ne briše
stare kopije da bi napravila mjesta za nove.

Za oporavak nakon gubitka cijelog servera potrebna je dodatna kopija na
nezavisnom, privatnom odredištu. Ovaj kod ne šalje baze u javni bucket sa slikama
niti podešava vanjsku replikaciju. Kopije sa samo jednog diska ne štite od
kvara ili brisanja tog diska.

## Slike i provjera oporavka

`backup_db --media` arhivira lokalni `MEDIA_ROOT`. Ako su slike u R2, koristiti
`backup_r2`; on pravi novu ZIP kopiju bez prepisivanja, a nepotpun download
prijavljuje kao grešku. ZIP sa već preuzetim fajlovima ostaje sačuvan.

Oporavak prvo isprobati u odvojenom okruženju. Za produkcijski restore zaustaviti
upise/obradu narudžbi, sačuvati tekuću bazu i slike, vratiti odabranu kopiju i
provjeriti narudžbe, kupce, zalihe i prikaz slika prije ponovnog otvaranja sajta.
Lokalni testovi provjeravaju SQLite oporavak i očuvanje prethodnog stanja;
PostgreSQL oporavak treba dodatno isprobati na odvojenoj serverskoj bazi.

## Trajna historija narudžbi i kupaca

Migracija `0313_protect_permanent_history` osvježava database triggere i dodaje
početnu snimku zapisa koji još nemaju historiju. Online narudžbe, ručne narudžbe,
stavke, registrovani korisnici i kupci magacina bilježe se pri upisu, izmjeni i
brisanju, uključujući masovne operacije i direktni SQL. Podaci kupca gosta ostaju
u snimci njegove narudžbe. Otkazivanje ne uklanja historiju.

Historiju `SystemDataRevision` baza odbija izmijeniti ili obrisati. PostgreSQL
odbija i `TRUNCATE` nad tabelama koje se prate, jer ta naredba zaobilazi redovne
triggere. Aplikacijski korisnik baze ne smije imati ovlaštenja za isključivanje
triggera, brisanje tabela ili promjenu njihove strukture; migracije treba izvoditi
odvojenim administratorskim korisnikom. Vlasnik baze i dalje može ukloniti zaštite.

Ove zaštite rade u istoj transakciji s promjenom: neuspješno arhiviranje prekida
promjenu umjesto da se izgubi trag. Rollback ne predstavlja završenu narudžbu i
ne ostavlja trajnu reviziju. Historija ne može rekonstruisati podatke obrisane
prije nego što je počelo njeno bilježenje.

Ovo ne zamjenjuje nezavisne sigurnosne kopije. Za server je potrebno podesiti
redovne privatne kopije na drugom odredištu, nadzor neuspjelih kopija i probni
oporavak. Ovaj zadatak ne postavlja vanjski raspored ili odredište bez pristupa
hostingu. Lokalni backup nije dokaz da je produkcijska baza zaštićena.
