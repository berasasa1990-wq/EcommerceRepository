Pregled konfiguracije za `carpologija-test.onrender.com` na grani `novi-dizajn`

Implementacija se aktivira samo preko `DJANGO_SETTINGS_MODULE=EcommerceProject.settings_render_test`. Produkcijski settings modul i Render blueprint zadržavaju postojeću konfiguraciju. Nisu izvršene migracije niti deploy, nije uspostavljena veza s Render ili produkcijskom bazom i nisu učitane niti prikazane `.env` tajne.

Postavite sljedeće vrijednosti na **posebnom testnom web servisu**:

| KEY | VALUE |
| --- | --- |
| DJANGO_SETTINGS_MODULE | EcommerceProject.settings_render_test |
| DATABASE_URL | Internal Database URL iz Connect ekrana baze carpologija_test_db; nikako URL produkcijske baze. |
| TEST_DATABASE_HOST | Tačan HOST iz tog istog URL-a testne baze, bez porta ili https://. |
| TEST_DATABASE_NAME | Stvarni Database naziv iz testnog URL-a. Obično carpologija_test_db, ali naziv Render resursa može se razlikovati od stvarnog PostgreSQL database naziva. |
| SECRET_KEY | Nova nasumična tajna samo za testni servis, najmanje 50 znakova. Ne kopirati produkcijsku. |
| DEBUG | False |
| ALLOWED_HOSTS | carpologija-test.onrender.com |
| CSRF_TRUSTED_ORIGINS | https://carpologija-test.onrender.com |
| SITE_URL | https://carpologija-test.onrender.com |
| SITE_PREP_ENABLED | true |
| SITE_PREP_PASSWORD | Nova testna lozinka, najmanje 16 znakova, zbog kopiranih podataka kupaca. |
| SITE_NAME | Carpologija — TEST (opcionalno; naziv sačuvan u SiteSettings ima prednost). |

Hosts, CSRF i SITE_URL se u testnom modulu dodatno fiksiraju na navedeni domen. DEBUG=True je greška konfiguracije. Cookies su ograničeni na testni host; ne nasljeđuju produkcijski SESSION_COOKIE_DOMAIN. HTTPS i secure cookies ostaju uključeni. Host i naziv baze se provjeravaju prije korištenja; pogrešno podudaranje zaustavlja aplikaciju. Koristite zasebnog PostgreSQL korisnika ograničenog na testnu bazu, bez prava na produkcijsku bazu. Nije moguće iz koda dokazati da host zaista pripada testnoj bazi ako sami pogrešno označite oba ENV polja: vrijednosti uzmite iz Connect ekrana TESTNE baze.

**Render postavke servisa** (nisu environment ključevi): Branch `novi-dizajn`; Auto-Deploy `Off`; Pre-deploy Command prazan; bez cron servisa, background workera ili povezanih produkcijskih environment grupa. Postavke mijenjati samo na testnom servisu.

Build Command bez migracija i automatskih zadataka:

```bash
pip install -r requirements.txt && python scripts/minify_assets.py && python manage.py collectstatic --noinput
```

Start Command:

```bash
bash scripts/render_test_start.sh
```

Nemojte koristiti postojeći `scripts/render_start.sh` ni postojeći blueprint Build Command za testni servis: oni sadrže `migrate`. Nova testna start skripta ne izvršava migracije. Render omogućava zasebno podešavanje build/start/pre-deploy komandi i isključivanje Auto-Deploy: https://render.com/docs/deploys . URL baze uzmite iz testnog PostgreSQL resursa: https://render.com/docs/postgresql-creating-connecting .

Izolacija integracija:

- Email backend je Django dummy: nijedan SMTP email ne odlazi kupcu ili prodavnici. `EMAIL_BACKEND` se ranije nije čitao iz ENV, pa samo dodavanje tog ključa bez testnog settings modula nije dovoljno.
- Monri je potpuno isključen, a sve `/placanje/monri/` rute su blokirane; ne radi ni testna ni stvarna kartična naplata. Mungos, X-Express, OLX, xAI, Meta CAPI i udaljeni sync nemaju produkcijske akreditive. Requests, urllib i botocore HTTP transporti su blokirani u testnom procesu, uključujući čitanje/pisanje R2 preko SDK-a.
- R2 i AWS varijable se odbacuju; upload i brisanje fajlova rade samo na lokalnom testnom media storageu. Kopiranje baze ne kopira slike. Za slike koristite zaseban testni disk/media kopiju. Bez diska, novi uploadi na Renderu nisu trajni preko redeploya. `RENDER_DISK_PATH` može biti putanja isključivo diska TESTNOG servisa; ne dijeliti produkcijski disk.
- Odlazni email threadovi i poznate integracijske/backup management komande su ugašeni. Ne kreirajte Render cron servise ili workere za ovu testnu aplikaciju. U projektu nije pronađen Celery/beat scheduler ili startup scheduler koji zahtijeva poseban ENV prekidač.
- Produkcijski sync webhookovi su blokirani, API ključevi su prazni, Google/Meta praćenje je isključeno. Odgovori imaju `X-Robots-Tag: noindex, nofollow, noarchive`; robots.txt zabranjuje indeksiranje. SITE_PREP lozinka štiti kopirane korisničke podatke; noindex sam po sebi nije zaštita pristupa.

Provjere migracija:

Modeli odgovaraju postojećim migracijama; nema nove migracije potrebne za ove promjene konfiguracije. Posljednja migracija aplikacije u repozitoriju je `0352_alter_sitesettings_chat_pozdrav_poruka_and_more`. Ne znamo koji dio grafa je primijenjen u kopiranoj Render bazi. Tabela Banner se kreira u `0001_initial`; ranija greška da tabela ne postoji zahtijeva provjeru i stvarnih tabela i migration history, ne automatski `--fake`.

Tek nakon što ručno postavite testne ENV vrijednosti, možete pokrenuti provjeru koja ne mijenja podatke:

```bash
python scripts/inspect_render_test_database.py
```

Skripta koristi PostgreSQL READ ONLY transakciju i prikazuje samo nedostajuće tabele i neprimijenjene migracije. Nije pokrenuta tokom ove revizije.

Database-free provjere koje su pokrenute: sigurnosne postavke, zabrana odlaznog HTTP-a, dummy mail, neaktivni email threadovi, blokada Monri webhooka i poređenje migration state s modelima. Svi prolaze; bez DB veze i bez izvršavanja migration operacija.

Kompletan inventar KEY naziva (bez vrijednosti tajni):

Obavezne za testni servis:

- `ALLOWED_HOSTS` — EcommerceProject/settings.py:116
- `CSRF_TRUSTED_ORIGINS` — EcommerceProject/settings.py:163
- `DATABASE_URL` — EcommerceApp/db_backup.py:234, EcommerceProject/settings.py:326, EcommerceProject/settings_render_test.py:9
- `DEBUG` — EcommerceProject/settings.py:88, EcommerceProject/settings_render_test.py:14
- `DJANGO_SETTINGS_MODULE` — EcommerceProject/asgi.py:14, EcommerceProject/settings.py:30, EcommerceProject/wsgi.py:14, manage.py:9
- `SECRET_KEY` — EcommerceProject/settings.py:85, EcommerceProject/settings_render_test.py:11
- `SITE_URL` — EcommerceProject/settings.py:138, EcommerceProject/settings.py:588
- `TEST_DATABASE_HOST` — EcommerceProject/settings_render_test.py:7
- `TEST_DATABASE_NAME` — EcommerceProject/settings_render_test.py:8

Opcionalne:

- `DEFAULT_FROM_EMAIL` — EcommerceProject/settings.py:581
- `GIT_COMMIT` — EcommerceApp/site_version.py:26
- `LIVE_VISITOR_EXCLUDE_IPS` — EcommerceProject/settings.py:646
- `LOYALTY_VIBER_FROM_PHONE` — EcommerceProject/settings.py:593
- `MEDIA_CACHE_MAX_AGE` — EcommerceProject/settings.py:420
- `MEDIA_URL` — EcommerceProject/settings.py:426
- `MESSENGER_PAGE` — EcommerceProject/settings.py:594
- `ORDER_NOTIFICATION_EMAIL` — EcommerceProject/settings.py:583
- `RENDER` — EcommerceApp/db_backup.py:70, EcommerceApp/management/commands/backup_r2.py:44, EcommerceApp/management/commands/backup_r2.py:250, EcommerceProject/settings.py:174, EcommerceProject/settings.py:213
- `RENDER_DISK_PATH` — EcommerceApp/db_backup.py:59, EcommerceApp/db_backup.py:76, EcommerceApp/db_backup.py:113, EcommerceApp/db_backup.py:31, EcommerceApp/db_backup.py:413, EcommerceApp/management/commands/backup_r2.py:46, EcommerceApp/management/commands/backup_r2.py:52, EcommerceApp/management/commands/backup_r2.py:252, EcommerceProject/settings.py:78, EcommerceProject/settings.py:362
- `RENDER_EXTERNAL_HOSTNAME` — EcommerceApp/db_backup.py:69, EcommerceApp/management/commands/backup_r2.py:45, EcommerceApp/management/commands/backup_r2.py:251, EcommerceProject/settings.py:77, EcommerceProject/settings.py:174, EcommerceProject/settings.py:362
- `RENDER_GIT_COMMIT` — EcommerceApp/site_version.py:24
- `SESSION_COOKIE_DOMAIN` — EcommerceProject/settings.py:240
- `SITE_NAME` — EcommerceProject/settings.py:689
- `SITE_PREP_ENABLED` — EcommerceProject/settings.py:653, EcommerceProject/settings_render_test.py:17
- `SITE_PREP_PASSWORD` — EcommerceProject/settings.py:652, EcommerceProject/settings_render_test.py:17
- `SITE_VERSION` — EcommerceApp/site_version.py:46
- `SOURCE_VERSION` — EcommerceApp/site_version.py:25
- `STATIC_URL` — EcommerceProject/settings.py:409
- `STORE_EMAIL` — EcommerceProject/settings.py:595
- `STORE_PHONE` — EcommerceProject/settings.py:591

Produkcijske integracije:

- `CATALOG_SYNC_API_KEY` — EcommerceProject/settings.py:630
- `EMAIL_APP_PASSWORD` — EcommerceProject/settings.py:579
- `EMAIL_HOST` — EcommerceProject/settings.py:573
- `EMAIL_HOST_PASSWORD` — EcommerceProject/settings.py:579
- `EMAIL_HOST_USER` — EcommerceProject/settings.py:577
- `EMAIL_PORT` — EcommerceProject/settings.py:574
- `EMAIL_TIMEOUT` — EcommerceProject/settings.py:587
- `EMAIL_USE_SSL` — EcommerceProject/settings.py:576
- `EMAIL_USE_TLS` — EcommerceProject/settings.py:575
- `FACEBOOK_DOMAIN_VERIFICATION` — EcommerceProject/settings.py:692
- `GOOGLE_ADS_ID` — EcommerceProject/settings.py:691
- `GOOGLE_ANALYTICS_ID` — EcommerceProject/settings.py:690
- `META_ACCESS_TOKEN` — EcommerceProject/settings.py:599
- `META_PIXEL_ID` — EcommerceProject/settings.py:598
- `META_TEST_EVENT_CODE` — EcommerceProject/settings.py:600
- `MONRI_AUTHENTICITY_TOKEN` — EcommerceProject/monri_config.py:14
- `MONRI_ENABLED` — EcommerceProject/monri_config.py:8
- `MONRI_ENVIRONMENT` — EcommerceProject/monri_config.py:9
- `MONRI_MERCHANT_KEY` — EcommerceProject/monri_config.py:13
- `MONRI_PUBLIC_BASE_URL` — EcommerceProject/monri_config.py:15
- `MUNGOS_API_KEY` — EcommerceProject/settings.py:73
- `MUNGOS_BASE_URL` — EcommerceProject/settings.py:69
- `MUNGOS_ECOMMERCE_ACCESS_CODE` — EcommerceProject/settings.py:74
- `MUNGOS_ENABLED` — EcommerceProject/settings.py:75
- `MUNGOS_ENVIRONMENT` — EcommerceProject/settings.py:67
- `OLX_API_TOKEN` — EcommerceProject/settings.py:633
- `OLX_CITY_ID` — EcommerceProject/settings.py:634
- `OLX_COUNTRY_ID` — EcommerceProject/settings.py:635
- `OLX_DEFAULT_CATEGORY_ID` — EcommerceProject/settings.py:636
- `PARTNER_STOCK_API_KEY` — EcommerceProject/settings.py:670
- `R2_ACCESS_KEY_ID` — EcommerceProject/settings.py:434
- `R2_ACCOUNT_ID` — EcommerceProject/settings.py:433
- `R2_BUCKET_NAME` — EcommerceProject/settings.py:436
- `R2_CUSTOM_DOMAIN` — EcommerceProject/settings.py:439
- `R2_ENDPOINT_URL` — EcommerceProject/settings.py:463
- `R2_JURISDICTION` — EcommerceProject/settings.py:462
- `R2_LOCATION` — EcommerceProject/settings.py:441
- `R2_PUBLIC_BASE_URL` — EcommerceProject/settings.py:440
- `R2_REGION` — EcommerceProject/settings.py:492
- `R2_SECRET_ACCESS_KEY` — EcommerceProject/settings.py:435
- `SYNC_API_KEY` — EcommerceProject/settings.py:608
- `SYNC_TIMEOUT` — EcommerceProject/settings.py:609
- `TURNSTILE_SECRET_KEY` — EcommerceProject/settings.py:641
- `TURNSTILE_SITE_KEY` — EcommerceProject/settings.py:640
- `XAI_API_KEY` — EcommerceApp/quick_activation.py:136
- `XAI_MODEL` — EcommerceApp/quick_activation.py:31, EcommerceApp/magacin.py:2991
- `XEXPRESS_API_URL` — EcommerceProject/settings.py:615
- `XEXPRESS_LOKACIJA` — EcommerceProject/settings.py:619
- `XEXPRESS_NACIN_PLACANJA` — EcommerceProject/settings.py:625
- `XEXPRESS_OBVEZNIK_PLACANJA` — EcommerceProject/settings.py:626
- `XEXPRESS_PASSWORD` — EcommerceProject/settings.py:614
- `XEXPRESS_REZERVACIJA` — EcommerceProject/settings.py:623
- `XEXPRESS_TIMEOUT` — EcommerceProject/settings.py:616
- `XEXPRESS_USERNAME` — EcommerceProject/settings.py:613

Za testni servis ostavite produkcijske integracijske ključeve **nepostavljene**. Iznimke poput `MONRI_ENABLED=false` i `MUNGOS_ENABLED=false` možete postaviti radi preglednosti, ali testni modul ih svakako forsira na false. SMTP/R2/API tajne nemojte unositi. Opcionalne identitetske ključeve postavite po potrebi; vremenske limite, medijske i verzijske ključeve možete ostaviti na zadanim vrijednostima. Render automatski postavlja svoje RENDER_* metapodatke.

Lokalni browser QA skriptovi imaju dodatne alatske environment ključeve; spisak je u `environment_tooling_inventory.json`. Oni se ne postavljaju na Renderu. `PYTHON_VERSION` i `PORT` su platformske varijable Rendera/dependencija; `EMAIL_BACKEND`, `SYNC_ENABLED` i `USE_R2_MEDIA` nisu generički ENV prekidači postojećeg projekta. Django settings se razlikuju od ENV ključeva. U testnom modulu njihove vrijednosti kontroliše kod.
