# Panel i komande ikonica

`/panel` i `/panel/` koriste kopirani BERA početni panel. Ikonice sada otvaraju njegove radne prostore sa bočnim menijem i ugrađenim editorima:

- Artikli: `/panel/sekcija/product/` — artikli, izdvojena ponuda, novo u ponudi, tagovi, kategorije i brendovi dostupni u postojećim modelima.
- Akcije: `/panel/sekcija/akcija/` — postojeće akcije i upsell ponude.
- Podešavanja: `/panel/podesavanja` — postavke sajta, banneri, profili i SEO. Posebni endpointi za logo i pretragu artikala/kategorija/brendova.
- B2B: `/panel/b2b` — postavke, korisnici, narudžbe i B2B Live.
- Loyalty: `/panel/loyalty` — postojeći loyalty sistem i editor kartica.
- Greb-Greb: `/panel/greb-greb` — postojeći editor nagrada i analitika.
- Ostale model kartice: `/panel/sekcija/<section>/`.
- WMS Magacin: `/wms`, `/wms/` i `/panel/wms` sada koriste preneseni BERA WMS; detalji, povezivanje lagera i potrebne migracije opisani su u `BERA_WMS_TRANSFER.md`. Pregled WMS modela ostaje na `/panel/wms/sekcije/`.
- Poklon vaučer, Live centar i Narudžbe koriste postojeće komande i njihove provjere pristupa.

## Implementacija

`EcommerceApp/bera_panel_settings.py` je kopirani BERA kod radnih prostora i postavki, prilagođen lokalnim templates i postojećim dozvolama. `bera_panel_admin.py` dodatno registruje postojeće editore na `/panel/podesavanja/sekcije/`, bez uklanjanja registracija iz starog admina. Pretrage, filteri, forme, autocomplete, masovne akcije i postojeći custom admin handleri nasljeđuju se iz trenutnog Carpologija koda. Redirecti i linkovi iz contexta koji vode na stari admin prilagođeni su dodatnom panel namespaceu. Pop-up editori i iframe koriste SAMEORIGIN, uz nepromijenjene autentifikaciju, model dozvole i CSRF zaštitu.

`EcommerceApp/template/bera_panel_admin/` sadrži 55 lokalnih templates: postojeće Django admin templates i trenutne Carpologija custom templates, sa kopiranim BERA plavim base izgledom. Redoslijed jquery.init prije custom B2B JavaScripta ispravljen je samo za dodatni panel editor.

Dodani su URL-ovi u `EcommerceApp/urls.py` i `EcommerceProject/urls.py`; postojeći URL-ovi nisu uklonjeni. Kopirani BERA radni prostori i CSS/JS koriste lokalni `bera_reference` namespace. Izvorni BERA projekat nije mijenjan.

Staff pristup ne daje automatski pravo uređivanja svih modela. Zadržane su postojeće admin dozvole; izmjena postavki i loga zahtijeva change_sitesettings. Module switches koriste postojeći ModulePermissions model ako ga instalacija ima; trenutna verzija ga nema pa nema novih prekidača ni migracija. Radni prostori i dodatni admin provjeravaju dostupne prekidače i direktan pristup.

## Provjere

Na sintetičkoj QA bazi:

- 23 model liste i dostupne forme za dodavanje vraćaju 200; editori imaju SAMEORIGIN.
- Dodavanje, izmjena i brisanje testnog taga prošli su; redirect ostaje u panelu.
- Pretraga postavki, validacija forme i logo endpoint provjereni.
- Obični korisnik nema pristup panelu; staff bez dozvola ne može uređivati proizvode, postavke ili logo, niti otvoriti WMS magacin.
- Stari `/admin/EcommerceApp/product/` i `/nalog/admin/` ostali su funkcionalni i registracije sačuvane.
- Browser klikovi na Artikli, Akcije, Podešavanja, B2B i Loyalty i otvorena Add forma na 1440 px i 390 px: bez JS grešaka ili neuspjelih resursa.
- Django system check, collectstatic sa WhiteNoise manifestom i git diff whitespace provjera prolaze.

Rezultati i snimci: `reports/bera-panel-visual/`. Reproduktivna backend provjera: `scripts/bera_panel_regression.py` uz izolovane QA postavke. Testne izmjene vraćene su transakcijom; ne koriste se produkcijska baza, lični podaci, stvarni e-mailovi ili plaćanja.

Nema novih paketa, migracija, pusha ili deploya. Modeli kojih nema u trenutnom projektu (npr. HomeBestsellerProduct) nisu izmišljeni niti dodani; dostupne sekcije određuju postojeće registracije i dozvole.
