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
     python manage.py collectstatic --noinput && \
     python manage.py migrate && \
     python manage.py createsuperuser --noinput || true
     ```
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
