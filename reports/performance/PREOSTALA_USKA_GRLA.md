# Dijagnostika preostalog usporavanja

## Potvrđeni uzroci u kodu

1. `static/bera_reference/js/staff-alerts.js` i `static/js/staff-alerts.js`
   svakih 5 sekundi pozivaju `views.staff_site_events_poll`. Trenutni klijent
   koristi samo `new_orders_count` i `latest_id`; backend je ipak pripremao
   online sesije, korpe, ponude i detalje događaja preko
   `staff_alerts.get_staff_events_since` i `build_visitor_states`.
   Broj otvorenih tabova dodatno množi ove pozive. Nije bilo zaštite od
   paralelnih pollova dok prethodni još traje.
2. `views_wms.order_customers` učitava sve odgovarajuće webshop narudžbe,
   ručne narudžbe i kupce, gradi listu, uklanja duplikate i sortira u Pythonu,
   pa tek tada izdvaja 30 rezultata. Paginacija ne ograničava rad baze ili
   potrošnju memorije. `/wms/kupci/` dodatno materijalizuje nedostajuće kupce
   preko `get_or_create` prilikom GET-a. Ovaj tok nije izmijenjen zbog
   očuvanja postojećeg izbora i prioriteta podataka kupaca.
3. `views_wms.workspace` za narudžbe i pakovanje poziva
   `wms_orders.sync_panel_orders` pri svakom prikazu. To pretražuje aktivne
   webshop narudžbe i pravi nedostajuće WMS dokumente. Ovo nije sinhronizacija
   lagera i nije uklonjeno jer osigurava da sve webshop narudžbe uđu u WMS.
4. Pretraga zaliha koristi OR `icontains` nad nazivom/šifrom/barkodom i
   sortiranje po izračunatoj dostupnosti. PostgreSQL planovi i obim stvarnih
   podataka nisu dostupni lokalno; potrebu za indeksima nije moguće potvrditi
   bez EXPLAIN/produkcijskih metrika. Nisu dodani indeksi ili migracije.

## Implementirana sigurna ispravka

Badge klijent šalje `summary=1`; autorizovani endpoint vraća samo broj novih
narudžbi i zadnji ID, uz postojeću strukturu odgovora. Puni odgovor ostaje
dostupan drugim klijentima. Nema cachea privatnih podataka. Spriječeni su
preklopljeni pozivi. Interval i obavijest o novim narudžbama ostaju isti.
Verzija skripte povećana je u oba base templatea.

Izolovano sintetičko SQLite mjerenje za `since=1`: puni zahtjev **16 SQL
upita / 5.511 bajtova**, novi summary zahtjev **4 upita / 110 bajtova**.
Broj novih narudžbi jednak je u oba odgovora; priprema stanja posjetilaca
se ne poziva za summary. Prvi raniji prolaz imao je 15 upita, što pokazuje
razliku između zagrijanog i nezagrijanog middleware/cache stanja.

Prošli su offline integraciona provjera endpointa i zaštite od anonimnog
pristupa, canonical stock checks i WMS regresija. Sve promjene testnih
podataka vraćene su rollbackom; produkcijska baza nije korištena.

## Granice zaključka

Ovo potvrđuje nepotreban rad u kodu, ali ne dokazuje dominantni uzrok
produkcijskog usporavanja. Render CPU/memorija/restarti, stvarni Gunicorn
start command, PostgreSQL latencije i Cloudflare/browser vremena nisu mjereni.
Repo `render.yaml` ima 1 worker / 4 threada i periodične worker restarte;
stvarna konfiguracija servisa nije potvrđena. Kod nije automatski deployan,
pa lokalne izmjene same po sebi ne ubrzavaju objavljeni sajt.

Promijenjeni fajlovi: `views.py`, obje `staff-alerts.js` skripte, oba base
templatea i ovaj izvještaj. Nema promjene lagera, cijena, plaćanja ili dozvola.
