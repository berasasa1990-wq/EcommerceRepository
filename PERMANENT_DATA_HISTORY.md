# Trajno čuvanje podataka

Uključivanje: `python manage.py migrate`, ponovno pokretanje aplikacije i objava
statičkih fajlova uobičajenim postupkom projekta. Migracija 0283 pravi početni
snimak postojećih poslovnih tabela. Promjene prije uključivanja mogu se vratiti
samo ako već postoje u starim evidencijama ili backupima.

Čuvaju se:

- INSERT/UPDATE/DELETE svih poslovnih `EcommerceApp_*` tabela, korisničkih `auth_*` tabela i admin loga,
  uključujući grupne promjene i direktni SQL. Audit je dio iste transakcije.
  Same tabele historije ne bilježe se rekurzivno.
- Sve verzije ručnih narudžbi, uključujući završene, rezervisane, otkazane i
  verzije iz zastarjelih prozora. Nema roka automatskog brisanja.
- Tekstualni, brojčani i izborni unosi vlasnika/superusera na sajtu, u magacinu,
  B2B-u i adminu. Lozinke, tokeni i podaci kartica ne kopiraju se u snimke formi.
- Originalni fajlovi poslani prijavljenim korisnikom te datoteke koje aplikacija
  sprema, mijenja ili uklanja kroz konfigurisani storage; sadržaj ostaje u bazi.

Pregled: `/nalog/magacin/historija-podataka/` (samo superuser).
Historija narudžbi: `/nalog/magacin/nacrti/historija/`; vraćanje pravi novi nacrt.
Historija drugih obrazaca omogućuje pregled/kopiranje prethodnih vrijednosti,
bez automatskog slanja starog obrasca.

„Sačuvano u bazi” prikazuje se tek nakon potvrde servera. Nepotvrđeni unosi
ostaju u lokalnom redu za ponovno slanje. Brisanje browser storagea ili kvar
uređaja prije potvrde može izgubiti još neposlane podatke. Fajl samo izabran u
pregledniku, ali još neposlan, nije stigao u bazu.

Ovo nije zamjena za backup na nezavisnom trajnom prostoru. Kvar/brisanje cijele
baze može uništiti i njenu historiju. `backup_db` zadržava stare kopije;
produkcijski raspored i vanjsku pohranu treba osigurati u infrastrukturi.
Pratiti kapacitet baze, posebno zbog slika i posjeta. Administrativni TRUNCATE,
isključivanje triggera i vraćanje starog backupa nisu standardne promjene reda.

Nakon novih migracija post_migrate osvježava triggere, uključujući nove tabele.
Podržani su SQLite i PostgreSQL. Lokalna automatizovana provjera koristi SQLite;
PostgreSQL granu treba provjeriti na staging bazi prije produkcijske migracije.
