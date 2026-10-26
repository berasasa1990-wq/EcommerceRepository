# BERA CONTROL

Odvojen lokalni panel vlasnika platforme: `/bera-control/`. Ne koristi Django korisnika, staff/superuser prava niti licensing aktivaciju za autentikaciju. Ne registruje svoje modele u običnom adminu. Ne mijenja artikle, narudžbe, korisnike ili postojeće tabele. Migracije dodaju control tabele, singleton početno stanje i tabelu lokalnih aktivacijskih kodova.

## Instalacija i prvo postavljanje

Iz korijena projekta:

```sh
venv/bin/python manage.py migrate
venv/bin/python manage.py bera_control_bootstrap
venv/bin/python manage.py runserver 127.0.0.1:8000
```

Otvoriti `http://127.0.0.1:8000/bera-control/setup/`, unijeti token iz serverske komande, novu lozinku i potvrdu. Token vrijedi 10 minuta; nova komanda poništava prethodni token. Ne slati token u URL-u ili spremati u repozitorij. Lozinka mora imati najmanje 12 znakova i proći Django validatore. Spremanje je uslovni atomski UPDATE: od istovremenih inicijalizacija samo jedna može uspjeti. Hash lozinke i trajni datum inicijalizacije ostaju u bazi. Potrošeni token se briše. Nakon inicijalizacije setup GET/POST vraća 404, a bootstrap komanda odbija izdavanje novih tokena.

Naredne prijave: `http://127.0.0.1:8000/bera-control/`. Nema registracije niti javnog resetovanja. Neinicijalizovan korijenski URL također ne daje mogućnost preuzimanja panela; token se dobija isključivo na serveru.

## Oporavak

```sh
venv/bin/python manage.py bera_control_recover
```

Potvrditi upisom `BERA CONTROL`, zatim dvaput unijeti novu lozinku u skriveni prompt. Komanda radi samo nad već inicijalizovanim panelom. Ne otvara setup ponovo, opoziva sve control sesije promjenom generacije i zapisuje događaj oporavka. Lozinka se ne prosljeđuje argumentom komandne linije.

## Aktivacija modulskim kodom

1. Vlasnik u `/bera-control/` odobrava modul pomoću ON prekidača ili dugmeta „Odobri i generiši kod“.
2. Panel prikazuje nasumični `BERA-…` kod licence i dugme „Kopiraj“. Modul čeka aktivaciju.
3. Vlasnik šalje kod kupcu. Kupac se prijavljuje u svoj `/panel/`, otvara „Dodatni moduli“, klikne „Aktivacija“, unese kod u popup i potvrdi „Aktiviraj modul“.
4. Modul je odmah dostupan, bez Installation ID-a, udaljene aktivacije, dodatnih ključeva ili sinhronizacije.

Kod je vezan za modul ove webshop instalacije. Vlasnički panel upravlja ovom lokalnom instalacijom; kod ne aktivira zaseban webshop s drugom bazom. Nasumični kod sadrži 128 bita entropije. U bazi su digest za provjeru i šifrirana kopija za prikaz vlasniku. Šifriranje koristi postojeći Django SECRET_KEY, bez dodatne konfiguracije. Kod se ne prikazuje u kupčevom HTML-u niti zapisuje u audit. Pravo generisanja ima samo odvojeno prijavljeni vlasnik; unos koda zahtijeva staff pristup običnom panelu i CSRF.

Kod se koristi jednokratno. OFF blokira i unos koda i korištenje aktiviranog modula, bez brisanja podataka. Ponovni ON zadržava ranije iskorišteni kod i ponovo dopušta rad. „Generiši novi kod“ zahtijeva potvrdu, poništava prethodni kod i aktivaciju te zahtijeva da kupac unese novi kod. Nakon promjene serverskog SECRET_KEY postojeći digest i aktivacija ostaju važeći, ali prikaz šifriranog koda zahtijeva izdavanje novog koda.

## Trajanje i istek aktivacije

U kartici modula vlasnik unosi „Trajanje aktivacije (dana)“ pri generisanju koda. Dozvoljeno je 1–36500 dana; prazno znači neograničeno. Rok novog koda počinje kada kupac uspješno aktivira kod, a ne na datum izdavanja. Postojeći kodovi bez roka ostaju neograničeni.

Dugme „Sačuvaj trajanje / produži“ mijenja trajanje bez promjene koda. Za kod koji čeka aktivaciju pohranjuje trajanje za buduću aktivaciju. Za već aktivirani modul postavlja novi rok od trenutka spremanja; time se može obnoviti i istekla aktivacija. Lokalni OFF i dalje blokira modul čak i nakon produženja.

Kupčev `/panel/moduli/` prikazuje datum isteka i crveni bedž tokom posljednjih sedam dana: „7 dana do isteka licence“, zatim preostali broj dana zaokružen naviše (posljednja 24 sata: „1 dan do isteka licence“). Na tačan trenutak isteka zajednički backend servis odbija modul, bez potrebe za schedulerom ili restartom. Panel tada prikazuje „Licenca je istekla“. Podaci modula se ne brišu. Promjene trajanja bilježe se u auditu. Samo vlasnik s odvojenom control sesijom može mijenjati rok.

## Pravila prioriteta modula

Jedina zajednička efektivna provjera ostaje `bera_licensing.services.module_enabled()` i `require_module()`:

| Stanje | Efektivni status |
| --- | --- |
| Lokalni prekidač OFF | NEAKTIVAN, bez obzira na kod ili staru licencu |
| Izdat lokalni kod, kupac ga još nije iskoristio | NEAKTIVAN |
| Lokalni ON, kupac je iskoristio ispravan kod i lokalni rok nije istekao | AKTIVAN, nezavisno od udaljene licence |
| Lokalni rok je istekao | NEAKTIVAN, bez povratka na staru licencu |
| Nije izdat lokalni kod | Postojeća licensing/legacy provjera ostaje važeća |

**Promjena u odnosu na prvobitnu implementaciju:** vlasničko izdavanje lokalnog koda eksplicitno prebacuje taj modul na lokalnu aktivaciju. Nakon toga se za taj modul ne zahtijevaju potpisani udaljeni dokument, Installation ID, plaćeni legacy rok ili provjera License Managera. Vlasnikov OFF ostaje najviši prioritet. Nema automatskog prelaska svih modula i nema otključavanja samim ON prekidačem prije kupčevog unosa koda. Stare licensing tabele, migracije i kompatibilni endpointi nisu obrisani; stari tok više nije ponuđen u kupčevom interfejsu.

Implementirani moduli su Loyalty, Akcija – Ponuda + (`article_plus`), Količinski popust (`quantity`) i Bundle set (`bundle`). Regularna akcija je FREE i ne zahtijeva licencu. Tri plaćena modula akcija su po defaultu zaključana: za njih je obavezno izdati i aktivirati lokalni kod, čak i ako ne postoji zapis prekidača ili stara udaljena licenca. Greb-greb, Poklon vaučeri, Newsletter, B2B veleprodaja i Napredna analitika prikazuju da su u pripremi i nemaju dostupne akcije generisanja/aktivacije. Nove integracije moraju koristiti zajednički servis u svim poslovnim operacijama prije dodavanja u `IMPLEMENTED`.

Loyalty backend provjere pokrivaju stranicu, panel, korpu, checkout, dodjelu/iskorištavanje bodova i admin poslovne akcije. Nijedan control prekidač ne briše kartice, bodove ili druge podatke. Svaki zahtjev čita stanje iz baze, bez procesnog cachea i bez restarta.

## Sigurnost i operativni uslovi

- CSRF na svim control POST akcijama; promjena modula i odjava prihvataju samo POST.
- Sesija ima zasebnu control oznaku i generaciju; ključ se rotira pri prijavi/odjavi. Neaktivnost od 30 minuta zahtijeva novu control prijavu, bez odjavljivanja webshop korisnika.
- Pet pogrešnih prijava zaključava panel na 15 minuta. Zaključavanje je globalno i spremljeno u bazu, pa drugi IP/worker ne zaobilazi limit. To znači da napadač može privremeno zaključati vlasnika; serverski oporavak ostaje dostupan. Preporučeno dodatno infrastrukturno ograničenje broja zahtjeva na ovoj putanji.
- Audit bilježi izdavanje/odbijanje tokena, inicijalizaciju, prijave, zaključane pokušaje, odjavu, oporavak i prethodno/novo lokalno stanje modula. Ne zapisuje lozinke ni tokene. IP je direktni REMOTE_ADDR, bez povjerenja u proizvoljne forwarding headere.
- Control odgovori imaju `Cache-Control: no-store` i ne učitavaju vanjske resurse. Panel koristi `same-origin` referrer politiku: interne forme zadržavaju Origin/Referer za CSRF provjeru, a vanjski linkovi ne šalju Referer. Ne koristiti `no-referrer` na stranicama s POST formama.
- Za produkciju obavezno podesiti `DEBUG=False`, zaseban siguran `SECRET_KEY`, ispravan `ALLOWED_HOSTS` i HTTPS. Settings automatski uključuje Secure session i CSRF cookies kada je DEBUG isključen; HttpOnly session i SameSite=Lax su eksplicitni. Postojeći razvojni settings nije kompletna produkcijska konfiguracija.
- Naziv webshopa opcionalno: `BERA_CONTROL_SHOP_NAME`; domen je validirani Django request host. Panel predstavlja webshop ove instalacije, nije višeklijentski centralni portal.
- Backup baze čuva konfiguraciju i audit. Zaštititi serverski pristup i backup jer serverski operater može izvršiti oporavak.

## Provjere

```sh
venv/bin/python manage.py check
venv/bin/python manage.py makemigrations --check --dry-run
venv/bin/python manage.py test bera_control bera_licensing shop --noinput
```

Za nove instalacije primijeniti migracije navedenom komandom. Nema Git pusha ili deploya.

## Plaćeni moduli akcija

Vlasnik izdaje zaseban kod i određuje trajanje za Ponudu +, Količinski popust i Bundle na isti način kao za Loyalty. Plaćanje se dogovara s vlasnikom platforme; ovaj tok ne implementira naplatu karticom. Kupac aktivira kupljeni modul u `/panel/moduli/`. Kod za jedan modul ne aktivira ostale.

Panel akcija prikazuje zaključane vrste kao onemogućene opcije i upućuje na aktivaciju. Backend odbija direktne pokušaje spremanja takve akcije. Korpa i checkout ponovo provjeravaju aktivaciju i rok pri svakom obračunu. Isključeni/istekli moduli ne daju popuste, ne nude promocije na stranici artikla i ne ulaze u akcijsku ponudu po toj osnovi. Bundle POST endpoint također zahtijeva važeću aktivaciju.

Postojeći artikli i postavke promocija nisu obrisani niti mijenjani. Nakon ponovne aktivacije njihove se postavke ponovo koriste. Regularno sniženje cijene dostupno je bez koda, uključujući i kada su svi plaćeni moduli isključeni.
