# CarpologijaBH / Ecommerce — audit Render outbound bandwidtha

Datum: 31.10.2026. Produkcija: `https://carpologijabh.ba/`, ulazni www domen: `https://www.carpologijabh.ba/`. Render servis prema korisniku: EcommerceRepository.

**Uzrok mjesečnih 37,16 GB nije potvrđen.** Ovo je pregled aktivnog Django projekta, konfiguracije i ograničenih javnih HTTP uzoraka; nije analiza 24-satnih produkcijskih access logova. Render/Cloudflare dashboard, produkcijske environment vrijednosti, stvarni start command, historijski DNS i mrežni obračun nisu dostupni. Nije izvršen deploy, push, promjena DNS-a, SSL-a, R2-a, Render konfiguracije niti migracija produkcijske baze.

## A. Nalazi i dokazi

### 1. Gdje odlaze slike, static i ostali odgovori

- `EcommerceProject/settings.py:408–551`: static ostaje `/static/`, WhiteNoise koristi `CompressedManifestStaticFilesStorage`; static je na Renderu, nije automatski prebačen u R2. `STATIC_URL` se prisiljava na relativnu putanju. `WHITENOISE_MAX_AGE=31536000`; hashirani produkcijski uzorci imaju još duži immutable cache.
- `settings.py:442` i `EcommerceProject/urls.py:53`: R2 media storage zavisi od konfiguracije. Kada je R2 uključen, lokalni Django media endpoint nije dodan. Kada nije, `views_media.py` servira lokalne fajlove sa dugim cacheom. Lokalno stanje konfiguracije nije dokaz produkcijskog stanja.
- `EcommerceApp/storage_backends.py`: R2 URL bez potpisanih query parametara; `Cache-Control: public, max-age=31536000, immutable`. **Domen `media.opremazaribolov.ba` ostaje netaknut.** Javni URL ne otkriva pouzdano naziv bucketa; naziv treba provjeriti u Render/R2 postavkama bez iznošenja ključeva.
- Produkcijski uzorci slike i AVIF kategorije na ovom domenu vraćaju Cloudflare HIT, veliki Age, strong ETag i zatim 304 sa 0 body bajtova. Ovo ne podržava tvrdnju da sve R2 slike prolaze kroz Django.
- Lokalna inventura: `EcommerceApp/static` 262 fajla / 20.966.883 B; `staticfiles` 3.155 / 84.076.507 B; `media` 19.422 / 493.178.559 B. Ovo su **veličine na disku, ne potrošnja mreže**. `staticfiles` sadrži hashirane, originalne, kompresovane i druge kopije.
- Veliki izvorni static PNG fajlovi: `hero-hoodie-clean.png` 1.826.507 B, `app-fishing-hero-v1.png` 1.709.184 B, `newsletter-banner-final.png` 1.534.382 B, `home-fishing-chairs.png` 1.528.916 B, `newsletter-reference.png` 1.442.089 B. Nije dokazano da se svi trenutno učitavaju na produkcijskoj početnoj. Newsletter partial referencira veliki PNG; stylesheet referencira newsletter pozadinu. Njihovu stvarnu učestalost treba izmjeriti prije konverzije.

### 2. Mjerena produkcijska zaglavlja i veličine

Dokazi su u `bandwidth-public-probe-2026-10-31.json` i `bandwidth-public-followup-2026-10-31.json`. Ovo su mali vremenski uzorci bez autentifikacije, ne cjelodnevni promet. B označava bajtove tijela odgovora na klijentskoj strani; veličine nisu Render obračunski bajtovi.

| Odgovor | Mjereno tijelo | Dekodovano tijelo | Cache i ostalo |
| --- | ---: | ---: | --- |
| Početna apex | 41.220 B gzip | 413.226 B | 200, CF DYNAMIC, Vary Cookie/Accept-Encoding, bez javnog Cache-Control u uzorku |
| Facebook XML feed | 887.177 B gzip | **8.654.552 B** | 200, CF DYNAMIC, max-age=900, Vary Cookie; bez ETag u uzorku |
| Sitemap | 50.113 B gzip | 657.856 B | 200, CF DYNAMIC, Vary Accept-Encoding |
| Core public CSS | ~69.079 B gzip | 427.500 B | CF DYNAMIC; max-age=315360000/public/immutable; weak ETag |
| Desktop CSS | 9.497 B gzip | 59.060 B | isti static cache; CF DYNAMIC |
| Logo JPG | 86.659 B | 86.659 B | hashirani URL, immutable; CF DYNAMIC |
| R2 site PNG | 59.119 B | 59.119 B | CF HIT, godišnji immutable, strong ETag, ponovljeni uslovni zahtjev 304 |
| R2 AVIF ikona | 4.979 B | 4.979 B | CF HIT; uslovni zahtjev 304 |

Feed zahtjev bez kompresije namjerno je prekinut nakon 4.000.001 B; to nije puna izmjerena identity veličina. Puna XML veličina poznata je iz dekodovanog gzip odgovora. HTTP probe su proizvele mali dodatni promet tokom ovog audita.

Tri CSS uzorka vratila su 200 i na `If-None-Match` sa ETagom iz prethodnog odgovora. Dodatni core CSS test bez query stringa i sa `Accept-Encoding: identity` također je vratio weak ETag i 200. Ponovljeni zahtjevi imaju Render origin zaglavlja. Dug browser cache postoji; **browser cache i edge cache nisu isto**. Mogući uzroci su bypass pravilo, transformacije/weak ETag između proxyja i WhiteNoise-a ili druga produkcijska konfiguracija. Ne može se iz nekoliko zahtjeva zaključiti da su svi edge cacheovi neispravni. Cache pravila nisu mijenjana.

### 3. Velike rute i ponovljeni zahtjevi

- `views_feed.py:_feed_items` učitava sve aktivne, vidljive artikle sa varijacijama, opisima do 5.000 znakova i svim feed stavkama u jednom XML-u. Stari `@cache_page(900)` štedi generisanje, **ne sprečava slanje cijelog odgovora**. LocMem cache je lokalni po procesu; restart/max-requests ga briše.
- Prije ovog patcha `middleware/gzip_html.py` kompresuje samo `text/html`. XML/JSON/CSV nemaju aplikacijski gzip kroz ovaj middleware. Cloudflare u izmjerenom feed/sitemap odgovoru već kompresuje za klijenta; nije izmjereno da li je prethodno Render→Cloudflare slanje bilo kompresovano.
- `sitemaps.py`: proizvodi/kategorije/brendovi/vlog; ProductSitemap limit 50.000. XML može rasti s katalogom; trenutni dekodovani uzorak ~658 KB. Nema globalne zabrane crawlera.
- `views_catalog_api.py` i `views_partner_stock.py`: autentifikovani JSON proizvodi/stanje su paginirani, najviše 100 proizvoda po stranici. Pojedinačan proizvod i dalje može imati mnogo opisa/varijacija; kategorije/brendovi vraćaju kolekcije. Nema dokaza da ove rute dominiraju prometom.
- `views_magacin.py:9264–9285`: download postojećeg i trenutnog DB backupa preko FileResponse. Pristup zahtijeva login + warehouse ovlaštenje; trenutni download pravi novu kopiju. PostgreSQL `.dump` može biti velik; nema historijskih veličina ni broja preuzimanja.
- `data_history.py:76–85`: ovlašteni download sačuvanih media fajlova preko FileResponse/BytesIO. `media_retention.py` čuva originalne uploadove i prethodne verzije u bazi; može povećati DB i veličinu DB backupa. To nije dokaz čestih downloada.
- `views_ledger.py:118`: ovlašteni CSV izvoz. Loyalty kartice/QR/barcode rute vraćaju PNG/JPG; WMS/račun/picking su HTML za browser print, ne automatski veliki PDF download. Backup CLI može kreirati ZIP/tar arhive, ali to samo po sebi nije javni ZIP download endpoint. Nije pronađen opšti javni download proizvoljnih backup arhiva u aktivnom URLconfu.
- WMS HTML prikazuje sve narudžbe u odabranom statusu bez paginacije, a state JSON sve pending ID-ove (`views_magacin.py:388`). Veličina zavisi od broja narudžbi; postojeći `Cache-Control: no-store` ostaje. Drugi pregledani magacin spiskovi koriste paginaciju (npr. 50/60/300 stavki).
- `visitor-presence.js` šalje heartbeat svakih 25 s dok je stranica vidljiva; `public-online-visitors.js` poll svakih 20 s; `cart-stock.js` svakih 15 s; `staff-wms.js` svakih 15 s. Broj tabova/posjetilaca može povećati zbir malih odgovora. WMS nije javno keširan i funkcionalnost nije mijenjana. Ne tretirati svaki JS setInterval kao HTTP zahtjev: neki samo ažuriraju sat/animaciju.
- Servis-initiated potencijalni izvori: R2 upload i arhiviranje zamijenjenih objekata, Meta Conversions API, Mungos/kurirski servisi, e-mail i background provjera uplata. `render_sync.py` je u pregledanom kodu deaktiviran. `start_web.sh` pokreće payment watcher; deklarisani interval je 10 minuta. CLI backup R2 **download** prvenstveno je inbound, dok upload prema R2 iz Rendera predstavlja outbound. Nije pokretan nijedan sync/backup posao za audit.

### 4. Domeni, redirecti i Cloudflare

- Javna DNS rezolucija oba `carpologijabh.ba` i `www.carpologijabh.ba` dala je Cloudflare IP adrese; oba HTTP odgovora imaju Cloudflare zaglavlja. Dakle, trenutno postoji dokaz proxy putanje za oba, a ne dokaz da je apex DNS-only. Historijsko stanje i dashboard proxy status treba ručno potvrditi.
- `https://www.../` → 301 `https://carpologijabh.ba/` → 200. `http://www.../` ima dodatni HTTPS korak: 301 → 301 → 200. `http://apex/` → 301 → 200. U uzorku nema redirect petlje. Redirect tijela su mala (60 B i 534 B), stoga sama nisu dokaz 37 GB.
- **`https://ecommercerepository.onrender.com/` vraća 200 početnu**, `/healthz` vraća 200/2 B. Direktni Render hostname je dostupan. To je mogućnost prometa izvan korisničke Cloudflare zone, ali broj takvih zahtjeva nije poznat.
- `settings.py:146–151` dopušta Render hostname i na Renderu `.onrender.com`; canonical SEO URL je apex (`settings.py:595`). SEO canonical nije HTTP host redirect. Nema dodanog automatskog redirecta: prethodno treba testirati healthcheck, callbackove, webhookove, POST zahtjeve, SEO, autentifikaciju i pouzdanost forwarded zaglavlja.
- Cloudflare zaglavlja na `onrender.com` ne znače da promet prolazi kroz **vašu** Cloudflare zonu: Render također ima proxy infrastrukturu.
- Razlika između navedenih 667 zahtjeva/~800 KB i izmjerenih apex 200 odgovora može biti pogrešna zona/hostname/status filter, drugi vremenski prozor, historijsko proxy stanje ili promet direktno prema Renderu. Nije potvrđeno koje je objašnjenje tačno. `media.opremazaribolov.ba` pripada drugoj DNS zoni od `carpologijabh.ba`; ni njihove analitike ne treba izjednačavati.

### 5. Logovanje i granice postojećih dokaza

`start_web.sh:16` pokreće Gunicorn sa 1 workerom, 4 threada, timeoutom 60 s i recikliranjem nakon ~400 zahtjeva. Nema `--access-logfile`/`--access-logformat`. Projektni LOGGING ranije je definisao namjenski Monri logger. Nisu pronađeni lokalni produkcijski access `.log` fajlovi. Render start command / `GUNICORN_CMD_ARGS` mogu imati dodatnu konfiguraciju; nije poznata. Repo `render.yaml` koristi drugo ime servisa, pa blueprint nije dokaz da produkcija tačno prati sve njegove vrijednosti.

Korisnikovi satni brojevi daju oko **97,6% HTTP Responses** i **2,4% Service-Initiated** od te dvije kategorije. Ako je taj sat reprezentativan, prvo tražiti izlazne HTTP odgovore. Nije opravdano jedan sat pretvoriti u mjesečni uzrok niti spojiti različite periode, servise, GB/GiB i zone.

## B. Potvrđeno i hipoteze

Potvrđeno: javni feed je velik; prethodni aplikacijski gzip preskače XML; static dolazi iz Render/WhiteNoise putanje; uzorkovani static zahtjevi su DYNAMIC; R2 uzorci su HIT; Render hostname javno radi; www/apex redirect lanac nema petlju u provjeri; historijski mjerni logovi nisu dostupni ovom auditu.

Nije potvrđeno: koja ruta potroši najviše mjesečno, broj bot zahtjeva, veliki downloadi u obračunskom mjesecu, stvarno kompresovani bajtovi na Render izlaznoj mreži, razlog razlike Cloudflare/Render, ni da je određeni crawler ili bucket uzrok. UA oznake nisu dokaz identiteta bota.

## C. Top 5 kandidata, prema dokazima (nije rang stvarne potrošnje)

| Rang | Kandidat | Dokaz / ograničenje |
| --- | --- | --- |
| 1 | Veliki javni Facebook XML feed | 8,65 MB dekodovano; nedostajala aplikacijska XML kompresija/ETag; broj i origin encoding nepoznati |
| 2 | Static preko Rendera uz nedostajući edge HIT u uzorcima | core CSS ~69 KB gzip, logo ~87 KB, postoje višemegabajtni PNG; browser cache je ispravan, dnevni miss/bypass broj nepoznat |
| 3 | Ponavljani dinamički HTML/XML i bot crawling | početna ~41 KB gzip/~413 KB raw, sitemap ~658 KB raw; nema produkcijskog prometa po ruti/UA |
| 4 | Direktni onrender/alternativni hostname promet | 200 potvrđen, može zaobići korisnički CDN; ovo je pristupni put do odgovora iz prethodnih stavki, ne zaseban sadržaj koji treba dvostruko sabirati |
| 5 | Ovlašteni backup/media downloadi i izvozi | kod potvrđuje velike moguće odgovore, DB čuva originale; nema dokaza o učestalosti/veličini u produkciji |

R2 uploadi/eksterni pozivi su dodatna hipoteza za Service-Initiated, niže prioriteta prema dostavljenom satnom odnosu. Polling treba izmjeriti, ali nema dokaza da sitni JSON odgovori dominiraju.

## D. Izvršene promjene i testovi

1. Samo javni `facebook_feed`: `gzip_page` unutar postojećeg 15-minutnog cachea; `ConditionalGetMiddleware` kao view decorator izvan cachea. Odvojene gzip/identity varijante preko Vary Accept-Encoding. ETag/If-None-Match omogućuju 304. XML podaci, URL, stavke i interval cachea ostaju isti; ostali endpointovi nemaju novu javnu cache politiku. Kompresovana reprezentacija se kešira, pa se ne kompresuje ponovo na svakom cache hitu.
2. Nova opcionalna `HttpDiagnosticsMiddleware`, najspoljnija da vidi body poslije HTML gzipa i WhiteNoise odgovore. **Isključena po defaultu**, uklanja se iz middleware lanca kada je disabled. Konfiguracija `HTTP_DIAGNOSTICS_ENABLED` i `HTTP_DIAGNOSTICS_SAMPLE_RATE` (0–1, default 1). Namjenski JSON logger.
3. Evidentira UTC vrijeme, validiran hostname, route obrazac bez vrijednosti parametara, status, HTTP metod, broj body bajtova/procjenu, trajanje, UA kategoriju i sampling rate. Nema raw putanje, query stringa, request/response sadržaja, IP-a, cookieja, tokena, kredencijala ili punog user-agenta. Neprepoznate/ranije presretnute rute su `(unresolved)`, static/media putanje wildcard. Nema blokiranja korisnika ni crawlera.
4. Sync/async streamovi broje yieldovane komade lijeno, uz oznaku nepotpunog streama pri prekidu. FileResponse se **ne omotava** i zadržava WSGI file wrapper/sendfile; na zatvaranju se prijavljuje Content-Length kao neprovjerena procjena. Ako veličina nije poznata, bilježi null. Greška u loggeru ne zaustavlja odgovor.
5. `manage.py http_bandwidth_report`: agregira top 20 ruta po ukupnim procijenjenim bajtovima, broju zahtjeva i prosjeku, grupisano po hostu+ruti. Prikazuje statuse, UA kategorije, nepoznate veličine, djelimične streamove, file procjene i sampling. Ne ekstrapolira uzorak na puni promet.

Testovi u `EcommerceApp/tests_http_bandwidth.py`: disabled/sampling, privatnost i normalizacija, brojanje kompresovanog bodyja, HEAD/304, lazy kompletan/prekinut/neuspješan/async stream, očuvanje FileResponse isporuke/zatvaranja, logger failure, feed cache varijante/304 i agregacija vremenskog prozora. Integracioni test provjerava privatni no-store odgovor, cookie, gzip i normalizovanu rutu kroz Django middleware chain.

Rezultati: zasebno **14/14 novih testova prolazi**. Širi izbor sa prvih 11 novih testova i postojećim suiteovima **80/80 prolazi**: permissions policy, WMS, Monri, checkout notifications, backup durability i permanent history. Testovi koriste izolovanu test bazu, ne produkcijsku. Završni izbor novih + SEO domain + Meta PageView testova: **24/25 prolazi**, uz jedan postojeći pad. Dodatna SEO provjera otkrila je postojeći pad `SeoDomainTests.test_home_product_and_category_use_apex_canonical`: početna nema očekivani `<h1>` (0 umjesto 1). Isti test pada i sa originalnim `views_feed.py` i `settings.py` iz HEAD-a, bez ovog patcha. Privremeno korištene originalne datoteke su vraćene na audit izmjene u finally bloku; nema izmjena početne stranice u ovom patchu. `git diff --check` uredan pri provjeri.

Nisu mijenjane funkcije korpe, checkouta, plaćanja, prijave, WMS-a, domenske rute, R2 storage ni postojeće private cache politike. Nema automatskog canonical redirecta ili promjene Cloudflare pravila.

## E. Kako utvrditi uzrok za 24 sata

### Aplikacijsko mjerenje — nakon zasebnog pregleda/deploya od strane vlasnika

Audit nije deployan. Kada vlasnik odluči objaviti kod, ručno uključiti na **EcommerceRepository**:

```text
HTTP_DIAGNOSTICS_ENABLED=true
HTTP_DIAGNOSTICS_SAMPLE_RATE=1
```

Zabilježiti početak/kraj u UTC, pratiti log volumen/opterećenje i sačuvati JSON poruke `event=http_bandwidth` za neprekinuta 24 sata iz svih instanci/restartova. Nakon mjerenja isključiti flag. Ako je rate manji od 1, report pokazuje samo uzorak; ne zvati ga kompletnim brojem zahtjeva. Ne koristiti raw Gunicorn request-line format koji može sadržavati query tokene.

Lokalno nad izvezenim JSONL porukama (jedan JSON događaj po redu, dozvoljen tekstualni timestamp prefiks):

```bash
venv/bin/python manage.py http_bandwidth_report /tmp/render-http-24h.jsonl \
  --hours 24 --top 20 --until 2026-11-02T00:00:00+00:00 \
  > /tmp/render-http-top20.json
```

Datum zamijeniti krajem stvarnog prozora. Ako Render export omotava poruke u JSON objekte ili JSON array, prvo izvući samo polje poruke u JSONL; parser očekuje JSON događaj, ne proizvoljan dashboard export. Ne unositi pristupne tokene u ovu komandu.

**Granice:** ovo su application body estimates, ne stvarno poslani socket bajtovi. Ne uključuju HTTP zaglavlja, TLS/HTTP protokol, retransmisije ili Render/proxy transformacije. Obični body je pripremljen, ne potvrđeno isporučen; stream je yieldovan serveru, ne potvrđen klijentu; FileResponse Content-Length može precijeniti prekinut transfer. Trajanje normalnog odgovora je do pripreme, stream/file do zatvaranja. Streaming događaj može doći kasnije od početka zahtjeva. Zahtjevi koje Render odbije prije aplikacije, kao i service-initiated egress, nisu obuhvaćeni. Pri restartu se događaji nedovršenih streamova mogu izgubiti. Stvarni mrežni zbir potvrditi Render metrikom u istom prozoru.

### Render dashboard — ručno potvrditi

- Da li je 37,16 GB za jedan servis ili workspace; obračunski datumi, UTC, GB/GiB, svi servisi i background/cron jobovi.
- EcommerceRepository Metrics: HTTP Responses vs Service-Initiated po satu/danu i vrijeme skokova. Uporediti top20 sa **istim** vremenskim prozorom i svim instancama.
- Stvarni Start Command i GUNICORN_CMD_ARGS, broj workera/instanci i restarti; postoje li access logs/request logs u planu. Ne izvoziti osjetljive raw query podatke u izvještaj.
- Custom Domains (www/apex), javni Render hostname i opcija isključivanja default hostnamea, healthcheck/callback potrebe. Ništa od toga audit nije mijenjao.
- Backupi, manuelna preuzimanja, import/sync/image obrada i scheduled jobs u satima skokova; produkcijski USE_R2_MEDIA i bucket naziv provjeriti bez prikaza ključeva.

### Cloudflare dashboard — ručno potvrditi

- Tačna zona **carpologijabh.ba**, zona **opremazaribolov.ba** za media odvojeno, period/timezone i ukloniti hostname/status/path/country filtere. Posebno porediti apex i www; 301-only prikaz možda posmatra samo redirect host.
- DNS apex i www: stvarni A/AAAA/CNAME target, orange-cloud Proxy status, eventualni dodatni AAAA/duplikati, odgovarajući Render custom domains. Trenutni DNS uzorak podržava proxy za oba, ali nije zamjena za dashboard.
- Cache Rules / Page Rules / Workers: bypass, cookie pravila, origin Cache-Control, Cache eligibility, cache key sa query stringom, Edge TTL. Provjeriti zašto baš hashirani `/static/` primjeri vraćaju DYNAMIC i weak ETag/200 na uslovni zahtjev.
- Prije uskog static pravila ručno potvrditi da `/static/` sadrži samo javne fajlove. Potencijalno poštovati origin immutable cache samo na `/static/`; testirati MISS→HIT, Age, 304, gzip/br/identity i različite POP-ove. Ne koristiti blanket Cache Everything za sajt.
- **Ne javno keširati korpu, checkout, login, admin, nalog, WMS, personalizovani HTML, autentifikovane API-je ili Set-Cookie odgovore.**
- SSL/TLS i redirect rules/Bulk Redirects/Always Use HTTPS: eventualno spojiti http+www u jedan canonical redirect tek uz testove; ne mijenjati automatski mod SSL ili Django trusted proxy.
- Analytics/Logs: top host/path/status, cache status, bot/UA kategorije, broj XML fetchova, promet izvan BiH. Ne blokirati zemlje ili crawlere na osnovu ovog audita; Google/Bing/Meta i BiH kupci ostaju dopušteni. UA je lako lažirati; potvrđeni bot identitet utvrđivati Cloudflare signalima ili zvaničnom verifikacijom.

## F. Uštede i izvori

**Nema pouzdane procjene mjesečne uštede.** Nedostaju broj fetchova, origin encoding i mrežno mjerenje prije/poslije.

Jedina trenutno mjerljiva usporedba reprezentacija: feed 8.654.552 B raw naspram 887.177 B gzip na edge-klijent putanji, razlika 7.767.375 B (~89,75%) po punom odgovoru. Klijent ovu kompresiju već dobija preko Cloudflarea, pa to **nije nova potvrđena Render ušteda**. Nova aplikacijska kompresija može smanjiti origin izlaz kada proxy/klijent traži gzip; treba potvrditi stvarni origin Accept-Encoding/Render metričke promjene. Klijent bez gzip podrške i dalje dobija puni XML. 304 uklanja body samo kada potrošač šalje važeći validator; feed se može promijeniti nakon obnove cachea. Gzip ETag je stabilan unutar keširane reprezentacije, ne garantovano preko novih cache generisanja.

Dokumentacija korištena za tumačenje:

- [Render service metrics](https://render.com/docs/service-metrics) i [Outbound bandwidth](https://render.com/docs/outbound-bandwidth): HTTP response i service-initiated kategorije; obračun nije veličina lokalnog foldera.
- [WhiteNoise Django](https://whitenoise.readthedocs.io/en/stable/django.html): compressed manifest static i hashirani immutable resursi.
- [Gunicorn settings](https://gunicorn.org/reference/settings/): access logging i response-length format; stvarni config provjeriti u servisu.
- [Cloudflare proxy status](https://developers.cloudflare.com/dns/proxy-status/) i [Analytics FAQ](https://developers.cloudflare.com/analytics/faq/about-analytics/): proxy putanja, zone i interpretacija analitike.
