# Uklanjanje nepotrebnog rada u WMS-u

Izmjene su lokalne. Produkcijska baza, konfiguracija i resursi nisu korišteni;
nema migracija, commita, pusha ili deploya.

## Uzroci i izmjene

- `EcommerceApp/context_processors.py`: WMS je koristio puni storefront
  context (meni kategorija, korpa, akcije, popupovi i kontekst oporavka korpe).
  Sada koristi postojeći lagani context sa postavkama sajta.
- `EcommerceApp/template/bera_reference/base.html`: WMS više ne uključuje
  kupovne popupove, greb-greb, kreiranje seta, obavijesti o dostupnosti,
  marketinško praćenje posjetilaca, Google tag, Meta Pixel i skriptu poravnanja
  logotipa brendova. Prodavnica i panel zadržavaju svoj storefront context.
- `EcommerceApp/middleware/meta_page_view.py`: WMS i panel više ne šalju
  server-side Meta PageView za administrativni rad. Javni PageView ostaje.
- Dodani testovi u `EcommerceApp/tests_wms_render_performance.py`; prošireni
  `EcommerceApp/tests_meta_page_view.py`.

Zadržani su WMS skripte, CSRF, obavijesti o narudžbama, provjere dozvola,
zahtjevi za otključavanje modula, trajno čuvanje unosa i PWA. Zajednički glavni
JavaScript i CSS nisu uklonjeni zbog postojećih zajedničkih interakcija i stilova.
Nisu mijenjani obračuni, plaćanje, modeli, lager niti rezervacije.

## Provjere i mjerenja

Izolovani sintetički SQLite fixture, offline postavke, sve testne promjene
vraćene transakcijskim rollbackom. Nije testirana produkcijska baza.

| Stranica | HTML prije / poslije (bajtovi) | SQL prije / poslije | Medijan prije / poslije |
| --- | --- | --- | --- |
| `/wms/` | 54.729 / 35.409 | 8 / 8 | 9,89 / 8,63 ms |
| `/wms/zalihe/?q=QA` | 60.450 / 41.128 | 11 / 11 | 10,94 / 9,47 ms |
| `/wms/narudzbe/` | 67.502 / 48.182 | 11 / 11 | 11,31 / 10,28 ms |

Tri zahtjeva po stranici, topli lokalni cache; vrijeme značajno varira sa
opterećenjem računara. Ovo nije dokaz produkcijskog ubrzanja. Broj SQL upita
ostaje isti u ovom fixtureu jer nema aktivnih marketinških ponuda. Testovi
posebno potvrđuju da WMS ne pokreće učitavanje menija, korpe ili ponuda.
Sirovi rezultati: `workspace-overhead-before.json` i `workspace-overhead-after.json`.

Prošli: `manage.py check`, 8 izolovanih testova konteksta/Meta middlewarea,
canonical stock checks i canonical WMS regresija (prijenos, rezervacije,
ručne i webshop narudžbe, završetak odvajanja bez duplog skidanja lagera,
štampa, sekcije i dozvole), provjera HTML-a sa uključenim testnim ID-ovima
integracija te `git diff --check`.

Preostalo: produkcijski PostgreSQL planovi upita, obim stvarnih podataka,
Render CPU/memorija i latencije, mjerenje u pregledniku i vizuelna provjera.
Aktivne obavijesti i provjere dozvola i dalje periodično rade jer imaju
funkcionalnu svrhu. Izmjene nisu deployane; produkcijsko ubrzanje nije potvrđeno.
