# Panel artikli

Lista u `/panel/sekcija/product/` učitava administrativni changelist
`/panel/podesavanja/sekcije/EcommerceApp/product/`.

`ProductAdmin.get_queryset` imao je globalni JOIN/COUNT DISTINCT/GROUP BY
za broj varijacija i dodatne upite za kategorije prikazane u svakom redu.
Sada koristi skalarnu agregaciju preko indeksiranog FK artikal_id, uz
select_related za brend, kategoriju i roditeljsku kategoriju. Sačuvani su
broj varijacija, sortiranje i filteri. Sličice koriste lazy loading/async
decoding. List editable, dozvole i stock +/- kontrole nisu mijenjani.

Izolovani SQLite fixture, isti podaci i zagrijan prikaz:

| Prikaz | Upiti prije / poslije | Lokalno vrijeme prije / poslije |
| --- | --- | --- |
| Lista | 31 / 15 | 50,58 / 48,50 ms |
| Pretraga QA | 31 / 15 | 61,11 / 47,09 ms |
| Bez varijacija | 31 / 15 | 105,00 / 55,15 ms |

Jedan zahtjev po varijanti; vremena variraju i nisu produkcijski benchmark.
Podaci: `panel-products/before.json`, `after.json`. Skripta reprodukcije:
`scripts/panel_products_profile.py`, dozvoljena samo na izolovanoj QA bazi.

Provjereni su stvarni brojevi varijacija prema ProductVariation tabeli,
filter sa varijacijama, sortiranje i zadržana list_editable polja; Django
check i git diff --check prolaze. Sve testne promjene rollback.

Promijenjeni su `EcommerceApp/admin.py`, profilerska skripta i izvještaji.
Nema migracija, konfiguracijskih promjena, commita, pusha ili deploya.
Stvarni PostgreSQL plan i performanse velikog kataloga još nisu izmjereni.
