# SEO podešavanje webshopa

Otvorite `/panel/podesavanje/` → **SEO sajta i društvene mreže**. Pristup ima samo osoblje s pristupom panelu. Postavke se primjenjuju odmah nakon spremanja i ne mijenjaju dostavu, izdvojene artikle ili licence modula.

- Naziv sajta, naslov početne i osnovni meta opis.
- Glavna adresa domene, npr. `https://www.shop.ba`, bez putanje. Koristi se za canonical URL-ove, sitemap, Open Graph i strukturirane podatke. Prazno koristi trenutni validirani request host; ovo ne preusmjerava automatski posjetioce između domena.
- Uključivanje indeksiranja, sitemap.xml i strukturiranih podataka.
- Naslov/opis početne za dijeljenje, slika 1200 × 630 px (JPG/PNG), favicon i X/Twitter korisničko ime.
- Google Search Console i Bing Webmaster kodovi: unijeti samo vrijednost `content`, bez HTML-a. Slanje sitemap-a i verifikacija naloga obavljaju se kod tih servisa.
- Kontakt podaci organizacije za strukturirane podatke.

U obrascima **Artikli** i **Kategorije** dostupni su zaseban SEO naslov i opis. Prazna polja koriste automatski naslov; opis artikla koristi opis proizvoda, zatim globalni opis kao rezervu. Brandovi i javne liste imaju automatske naslove.

Svaka javna stranica dobija meta opis, robots oznaku, canonical, Open Graph i Twitter metapodatke. Proizvodi imaju Product/Offer i breadcrumb JSON-LD, početna Organization i WebSite JSON-LD. Schema ne izmišlja ocjene, recenzije ili podatke o dostavi. Cijena i stanje uzimaju se iz artikla; uslovne promocije ne predstavljaju se kao bezuslovna cijena proizvoda.

`/sitemap.xml` je indeks mapa javnih stranica, aktivnih proizvoda te kategorija/brendova s aktivnim artiklima. Mape se dijele na najviše 1000 URL-ova. Privatne stranice, pretrage i neaktivni artikli nisu uključeni. Kada je sitemap ili indeksiranje isključeno, sitemap vraća 404 i robots.txt ga ne oglašava.

`/robots.txt` se generiše automatski. Isključivanje indeksiranja šalje `noindex` u HTML-u i `X-Robots-Tag` zaglavlju, uz dopušten crawl kako pretraživači mogu pročitati zabranu. Administrativne putanje su dodatno disallow u robots.txt. Korpa, checkout, nalog, lista želja, paneli i rezultati pretrage imaju noindex neovisno o globalnom prekidaču. To nije zamjena za autentikaciju privatnih stranica.

Broj znakova i primjer rezultata u panelu su pomoć pri uređivanju. Pretraživači mogu prikazati drugačiji naslov/opis i sami određuju vrijeme ponovnog indeksiranja. [Google dokumentacija za robots/noindex](https://developers.google.com/search/docs/crawling-indexing/robots-meta-tag), [sitemap dokumentacija](https://developers.google.com/search/docs/crawling-indexing/sitemaps/build-sitemap).

## Instalacija na drugoj instanci

```sh
venv/bin/python manage.py migrate
venv/bin/python manage.py check
venv/bin/python manage.py test shop.test_seo --noinput
```
