# Oprema za Ribolov - Django E-commerce

Sajt za prodaju ribolovne opreme (opremazaribolov.ba).

## Lokalni razvoj

```bash
# 1. Kreiraj .env iz primjera
cp .env.example .env

# 2. Instaliraj zavisnosti
pip install -r requirements.txt

# 3. Pokreni migracije
python manage.py migrate

# 4. Pokreni server
python manage.py runserver
```

## Deploy na Render.com (Production)

### Koraci:

1. **Push kod na GitHub / GitLab**

2. **Na Renderu kreiraj:**
   - **PostgreSQL** bazu (besplatna ili starter)
   - **Web Service** (Python)

3. **Web Service podešavanja:**
   - **Build Command:**
     ```bash
     pip install -r requirements.txt && \
     python scripts/minify_assets.py && \
     python manage.py collectstatic --noinput && \
     python manage.py migrate --noinput
     ```
     (Ne stavljaj `createsuperuser` u build — ako nalog već postoji, dobiješ
     `CommandError: That username is already taken.` Superuser napravi jednom
     ručno u Shell: `python manage.py createsuperuser`.)
   - **Start Command:**
     ```bash
     gunicorn EcommerceProject.wsgi:application
     ```

4. **Dodaj Disk za Media** (obavezno za slike proizvoda):
   - Id i u Web Service → **Disks** → Add Disk
   - **Name:** media-disk
   - **Mount Path:** `/var/data`
   - **Size:** 5GB ili više

5. **Environment Variables** – OVO SE POSTAVLJA U RENDER DASHBOARD (ne iz .env iz GitHuba):
   - Idi na Web Service → **Environment** tab
   - Dodaj/uredi varijable tamo
   - Nakon izmjena → **Manual Deploy** da se primijeni
   - Ključne:
     - `SECRET_KEY` (generiši)
     - `DEBUG=False`
     - `ALLOWED_HOSTS=tvoja-app.onrender.com,*.onrender.com`
     - `SITE_URL=https://tvoja-app.onrender.com`
     - `RENDER_DISK_PATH=/var/data`
     - `DJANGO_SUPERUSER_*` (za automatsko kreiranje admina)
     - Sve ostale iz tvog .env (EMAIL, ODOO, SYNC, TURNSTILE...)

   Render automatski postavlja `RENDER_EXTERNAL_HOSTNAME` i `DATABASE_URL` (ako je povezan Postgres).

6. Poveži **PostgreSQL** service sa Web Service-om (Render će dodati `DATABASE_URL` automatski).

7. Deploy.

### Korisne komande u Render Shell-u:

```bash
python manage.py migrate
python manage.py collectstatic --noinput
# Superuser se sada automatski kreira preko build komande
```

### Važno za Media

Sve slike (proizvodi, banneri, logo) se čuvaju na disku koji si attach-ovao na `/var/data/media`.

Ako kasnije promijeniš mount path, obavezno promijeni i `RENDER_DISK_PATH`.

## Git

Nemoj commit-ovati:
- `.env`
- `db.sqlite3`
- `media/`
- `venv/`

Sve je pokriveno u `.gitignore`.

## Struktura

- `EcommerceApp/` — glavna aplikacija
- `EcommerceProject/settings.py` — podešavanja (podržava Render + Postgre + Disk)
- `render.yaml` — Render blueprint (opcionalno)

## B2B veleprodaja

- URL: `/veleprodaja` (posebna B2B prijava, bez javne registracije).
- **Zatraži pristup** pored prijave otvara formu za naziv firme, adresu, grad, JIB, telefon i kontakt ime.
  Zahtjev se šalje na **narudzbe@opremazaribolov.ba** preko konfigurisanog e-mail servisa.
  Ne kreira nalog automatski; neuspjelo slanje prikazuje grešku uz zadržavanje unosa.
- Kreiranje pristupa: `/admin/` → **B2B korisnici** → **Dodaj B2B korisnik**.
  Unesite korisničko ime, firmu i novu šifru. Obični kupci i admin nalozi sami po sebi nemaju B2B pristup.
- Isključivanje polja **Aktivan** ili promjena šifre ukida postojeće B2B sesije.
- Katalog koristi aktivne kategorije i vidljive artikle, uključujući varijacije.
  VPC netto računa se iz redovne MPC: `MPC / 1.38 / 1.17`, uz zaokruživanje tek konačnog iznosa na dvije decimale.
- Količine dolaze iz Magacina, po aktivnim lokacijama (bez internih lokacija prenosa koje Magacin ignoriše).
  U katalogu se prikazuje samo zbir dostupnih količina nakon rezervacija.
- Podešavanja izgleda: `/admin/` → **B2B** → dodajte/otvorite podešavanja.
  **Glavni banner (prvi slajd)** prikazuje se preko cijele širine iznad brendova, umjesto naslova i opisa.
  U **Dodatni banneri za slider** dodajte više slika, redoslijed i aktivnost (preporuka 1600 × 400 px).
  Polje **Link bannera** na glavnom i dodatnim bannerima otvara unesenu lokalnu ili HTTP(S) adresu klikom; prazno znači da banner nije link.
  Slider se smjenjuje automatski svake 3 sekunde (i kada je miš preko bannera), uz strelice, tačkice i pauzu; jedan banner je statičan.
  U meniju se ikonica prikazuje samo uz **Sve kategorije**. Glavne kategorije klikom otvaraju/zatvaraju podkategorije.
- **Admin → B2B → Noviteti / Akcijska ponuda**: dodajte više artikala preko pretrage (bez ograničenja broja).
  Zeleno **NOVITETI** i crveno **AKCIJSKA PONUDA** iznad fiksne korpe otvaraju ove grupe; isti artikal može biti u obje.
- B2B prikaz: crno-narandžasti raspored, kategorije lijevo, logotipi brendova i tabela artikala.
  Pretraga uključuje nazive, šifre i brendove; dostupni su filter brenda, samo na stanju, sortiranje i 25/50/100 artikala po stranici.
  Dostupni artikli i varijacije uvijek su prvi, prije paginacije i bez obzira na sortiranje; za nultu dostupnost piše **Nije na stanju**.
- Dugme **Dodaj u korpu** prvo otvara pitanje **Koliko komada?**, a nakon potvrde dodaje odabranu količinu u posebnu B2B korpu na `/veleprodaja/korpa/`.
  Dodavanje radi bez osvježavanja ili pomjeranja stranice, uz ažuriranje broja i iznosa u korpi.
  Korpa podržava izmjenu količina i uklanjanje, provjerava dostupnost i računa trenutne VPC netto cijene na serveru.
  Odjava briše B2B korpu; korpa ne rezerviše zalihe.
- **Završi narudžbu** → opcionalna napomena → **Pošalji narudžbu u magacin** → izbor **Gotovinski / Žiralno** odmah šalje narudžbu.
  Telefon, e-mail, adresa i grad se ne traže; kupac se identifikuje preko svog B2B naloga.
  Narudžba odmah ulazi u picking i rezerviše tačne varijacije na lokacijama.
  Prije slanja prikazuju se netto, PDV i ukupno; postojeći magacin dobija cijene sa PDV-om.
  Završetak B2B pickinga skida samo potvrđene količine sa potvrđenih lokacija, oslobađa ostatak rezervacija
  i ažurira ukupan iznos prema stvarno pokupljenim količinama. Ponovljeno slanje/završavanje ne duplira narudžbu ili skidanje.
  Evidencija je dostupna kroz **Admin → B2B narudžbe** i postojeće narudžbe/picking u Magacinu.
- Pri objavi primijeniti migraciju: `python manage.py migrate` i prikupiti statiku: `python manage.py collectstatic --noinput`.
