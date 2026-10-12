# Učitavanje dodatnih artikala — 12.10.2026.

Potvrđeno: dugme „Prikaži više” preuzimalo je kompletan HTML stranice, a zatim
izdvajalo samo kartice i dugme. Novi zahtjev šalje X-Catalog-Fragment: 1;
Django za rezultate pretrage i kategorija vraća samo taj sadržaj. Ne učitavaju
se niti ponovo izvršavaju header, footer, hero i skripte. Redoslijed,
filteri, cijene, broj artikala i inicijalizacija korpe ostaju isti.

Obični zahtjevi i direktni URL-ovi paginacije i dalje vraćaju cijelu stranicu.
Vary: X-Catalog-Fragment razlikuje dvije reprezentacije u HTTP cacheu.
Nema keširanja korisničkog HTML-a, migracija, deploymenta ni promjene podataka.

Izolovani testovi sa 45 proizvoda, stranica 2, 20 kartica:

| Prikaz | Prije (HTML bajtovi) | Poslije | Smanjenje |
| --- | ---: | ---: | ---: |
| Pretraga | 128268 | 70861 | 44,8% |
| Kategorija | 125397 | 70867 | 43,5% |

Vrijednosti su nekomprimirani sintetički lokalni odgovori, ne produkcijski
transfer ili mjerenje ukupnog vremena sajta. Mrežna latencija, stvarni SQL
planovi, Render CPU i browser Long Tasks nisu potvrđeni ovim mjerenjem.

Prošlo je 10 testova: tests_catalog_load_more, tests_category_performance,
tests_noviteti_selection, uz izolovane scripts.bera_home_qa_settings.
JavaScript syntax i git diff --check prolaze.

Izmjene: views.py, catalog-load-more.js, base.html (verzija skripte),
novi partial catalog_results_fragment.html i tests_catalog_load_more.py.

Sljedeće mjerenje treba razdvojiti produkcijski TTFB, spor SQL i browser
Long Tasks na početnoj, pretrazi i kategorijama prije daljnjih zahvata.
