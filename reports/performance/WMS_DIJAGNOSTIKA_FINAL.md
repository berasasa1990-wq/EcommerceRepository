# BERA WMS: dijagnostika, mjerenja i sigurne optimizacije

## Dijagnostika prije izmjena

Glavni dokazani N+1: `Product.prikazna_slika` prolazi `varijacije.all()`
kada nema glavne slike. WMS liste nisu prefetchovale varijacije. Učitavanje
slike nekoliko puta u templateu dodatno ponavlja upite. Prethodni fixture
sa glavnim slikama sakrivao je problem.

`views_wms.location_detail` učitavao je sve pozitivne zalihe lokacije.
Za svaki red renderuje obrazac sa svim odredišnim lokacijama: veličina DOM-a
raste sa brojem redova puta brojem lokacija. Sada je broj redova ograničen
na 30 po stranici; obrasci i raspored ostaju isti.

`workspace` shortages popup čitao je `order.source_order` za broj narudžbe
bez select_related, kao i fallback slike. Dodani su odgovarajući join/prefetch.
Prefetch slika dodan je i pregledu narudžbe i odvajanja.

Preostala mjesta sa rastom opterećenja:
- `order_customers`: sve odgovarajuće narudžbe i kupci učitavaju se prije
  Python deduplikacije/sortiranja/paginacije. `/wms/kupci/` može kreirati
  nedostajuće kupce na GET-u. Nije promijenjeno jer treba sačuvati prioritete
  izvora, kontakte i postojeći izbor kupca.
- `wms_orders.sync_panel_orders`: narudžbe se provjeravaju pri prikazu
  narudžbi/pakovanja. Ne preračunava lager; osigurava WMS dokumente.
- `order_products`: Sum rezervacija koristi GROUP BY uz sve Product kolone;
  pretraga zaliha koristi OR icontains i CASE za dostupnost. To zahtijeva
  provjeru stvarnih PostgreSQL planova, posebno kod velikog kataloga.
- Lista lokacija i shortage popup trenutno učitavaju sve zapise. Obrazac
  transfera učitava sva odredišta. Dalje ograničavanje traži odvojenu paginaciju
  i pretragu koja neće sakriti dostupne izbore.

Zalihe koriste originalne WarehouseStock/WarehouseLocation i Product;
GET ne sinhronizuje mirror lager niti ponovo obračunava kompletan lager.
Rezervacije u API katalogu se agregiraju iz originalnih zaliha. Nema
cacheiranja količina ili privatnog HTML-a.

## Mjerenja

Izolovana sintetička SQLite baza, 8 artikala, 8 redova na testnoj lokaciji;
glavne slike privremeno prazne da aktiviraju fallback. Sve izmjene rollback.
Po jedan profilisani zahtjev nakon zagrijavanja po stranici, isti podaci
prije/poslije. Vremena uključuju overhead cProfile i nisu benchmark Rendera.
SQL vrijeme mjeri execute_wrapper visoke preciznosti; ne uključuje čitavo
fetchovanje/materializaciju redova. View inclusive vremena su u JSON-u.
HTML start tag count je statička procjena, nije broj živih DOM elemenata.

| URL | SQL prije → poslije | Server ms prije → poslije | SQL execute ms prije → poslije | Bajtovi poslije | HTML tagovi poslije |
| --- | --- | --- | --- | --- | --- |
| `/wms/` | 8 → 8 | 21.523 → 21.189 | 0.548 → 0.532 | 35409 | 339 |
| `/wms/zalihe/?q=` | 20 → 13 | 38.685 → 32.637 | 0.905 → 0.643 | 51668 | 609 |
| `/wms/narudzbe/artikli/?q=` | 14 → 7 | 17.476 → 10.8 | 0.838 → 0.525 | 852 | None |
| `/wms/lokacije/` | 9 → 9 | 30.444 → 28.837 | 0.644 → 0.622 | 42378 | 468 |
| `/wms/lokacije/6/` | 18 → 12 | 34.907 → 28.929 | 0.83 → 0.57 | 49255 | 617 |
| `/wms/narudzbe/` | 11 → 11 | 35.614 → 28.411 | 1.118 → 0.641 | 48182 | 527 |
| `/wms/istorija/` | 8 → 8 | 23.81 → 22.187 | 0.424 → 0.367 | 34571 | 364 |

Sirovi rezultati: `wms-profiler/before.json`, `after.json`. Bez tvrdnje o
pouzdanom procentu produkcijskog ubrzanja. Na 8 artikala uklonjeno je 7
pojedinačnih upita po listi; broj fallback upita više ne raste po artiklu.

## SQL planovi i indeksi

Offline SQLite EXPLAIN i introspekcija indeksa spremljeni su u
`wms-profiler/query-plans.json`. Nije rađen PostgreSQL EXPLAIN ANALYZE.
WarehouseStock već ima FK indekse, unique(product, variation_key, location)
i indeks(product, location). Dodatni indeks(location, product) ili parcijalni
location za kolicina>0 razmotriti samo ako stvarni plan dokaže korist.
Za substring pretragu potencijalno koristiti pg_trgm GIN indekse nad izrazom
koji stvarni Django PostgreSQL SQL koristi (npr. UPPER kolone); običan B-tree
nije dovoljan za proizvoljan %tekst%. Za status/datum narudžbi razmotriti
kompozitni indeks po potvrdi plana i selektivnosti.
Nema novih migracija ili indeksa; potrebno je ručno odobrenje prije njihove izrade.

## JavaScript, slike i server

Pretrage koriste debounce i zaštitu rednim brojem odgovora; korisnički dijalog
renderuje paginirane rezultate. Slike WMS zaliha/lokacije već koriste lazy
loading. Pozivi provjere dozvola ostaju radi zaključavanja modula. Ranije
optimizovani staff badge polling koristi summary odgovor i zaštitu od
preklopljenih poziva; puni endpoint ostaje za druge klijente.

Nije izvršeno mjerenje JavaScript vremena, živog DOM-a, LCP/INP/CLS niti
vizuelno poređenje desktop/mobilnog preglednika. CSS i postojeći raspored nisu
mijenjani. Ograničenje redova smanjuje DOM na velikoj lokaciji; fixture od 8
redova ne pokazuje tu razliku.

Repo Gunicorn: 1 worker / 4 threada / timeout 60 / max requests 400+jitter;
Render konfiguracija nije mijenjana niti je potvrđeno da je stvarna ista.
Priloženi CPU graf ranije nije pokazao zasićenje, što ne isključuje čekanje
baze/API-ja. Nema mjerenja Render latencija, memorije ili restartova.

## Testovi

- `scripts/wms_performance_regression.py`: pretraga po jedinstvenom barkodu,
  isti artikal na dvije lokacije, 32 reda lokacije raspoređena 30+2, pristup
  drugoj stranici, ograničen broj fallback upita, nepromijenjene količine i
  rezervacije poslije prikaza, anonimni pristup odbijen.
- `scripts/carp_canonical_stock_checks.py`: direktni originalni lager,
  varijacije, transferi, bez mirror sync/read/write.
- `scripts/carp_canonical_wms_regression.py`: narudžbe, odvajanje, rezervisani
  transfer, ručno/webshop skidanje samo jednom, štampa, dozvole i stari magacin.
- 8 kontekst/Meta middleware testova i `manage.py check`: prolaze.
- `git diff --check`: prolazi.

Stvarna istovremena promjena zaliha NIJE validirana: SQLite ne implementira
PostgreSQL select_for_update row locks. Sekvencijalni test ponovnog završetka
narudžbe prolazi, ali nije zamjena za konkurentni PostgreSQL test. Za to je
potrebna izolovana PostgreSQL baza; produkcijska nije korištena.

## Izmijenjeni fajlovi ove faze

- `EcommerceApp/views_wms.py`
- `EcommerceApp/template/bera_reference/staff/wms/location_detail.html`
- `scripts/wms_profile_local.py`
- `scripts/wms_performance_regression.py`
- ovaj izvještaj i tri JSON rezultata u `wms-profiler/`.

Poslovna logika, lager, dozvole i konfiguracije nisu izmijenjeni. Kod lokalno
prolazi navedene provjere, ali produkcijska spremnost za veliko opterećenje
još zahtijeva PostgreSQL konkurentnost i desktop/mobilnu vizuelnu provjeru.
Nema commita, pusha, deploya ili migracije produkcijske baze.

## Inventar URL-ova

```python
path('wms/stampa-cijena/', wms_price_print.price_print, name='staff_wms_stampa_cijena'),
path('wms/stampa-cijena/ista/', wms_price_print.price_print, {'mode': 'ista'}, name='staff_wms_stampa_cijena_ista'),
path('wms/stampa-cijena/razlicite/', wms_price_print.price_print, {'mode': 'razlicite'}, name='staff_wms_stampa_cijena_razlicite'),
path('wms/stampa-cijena/print/', wms_price_print.price_print, {'mode': 'print'}, name='staff_wms_stampa_cijena_print'),
path('wms/narudzbe/kreiraj/<str:tip>/', views_wms.create_order, name='staff_wms_create_order'),
path('wms/narudzbe/pregled/<int:pk>/', views_wms.order_detail, name='staff_wms_order'),
path('wms/narudzbe/<int:pk>/izmjena/', views_wms.edit_order, name='staff_wms_edit_order'),
path('wms/narudzbe/<int:pk>/pakovanje/', views_wms.pack_order, name='staff_wms_pack_order'),
path('wms/narudzbe/<int:pk>/odvajanje/', views_wms.pick_order, name='staff_wms_pick_order'),
path('wms/odvajanje/istorija/', views_wms.picking_history, name='staff_wms_picking_history'),
path('wms/narudzbe/<int:pk>/vrati/', views_wms.restore_order, name='staff_wms_restore_order'),
path('wms/narudzbe/<int:pk>/otkazi/', views_wms.cancel_order, name='staff_wms_cancel_order'),
path('wms/lokacije/nedostaje/<int:pk>/', views_wms.resolve_shortage, name='staff_wms_resolve_shortage'),
path('wms/narudzbe/stampa/<str:kind>/', views_wms.print_orders, name='staff_wms_print_orders'),
path('wms/narudzbe/artikli/', views_wms.order_products, name='staff_wms_order_products'),
path('wms/narudzbe/kupci/', views_wms.order_customers, name='staff_wms_order_customers'),
path('wms/kupci/<int:pk>/<str:action>/', views_wms.manage_customer, name='staff_wms_manage_customer'),
path('wms/lokacije/<int:pk>/barkod/', views_wms.location_barcode_print, name='staff_wms_location_barcode'),
path('wms/lokacije/<int:pk>/', views_wms.location_detail, name='staff_wms_location'),
path('wms/podesavanje/excel-import/', views_wms.excel_import, name='staff_wms_excel_import'),
path('wms/', views_wms.workspace, name='staff_wms'),
path('wms/<str:section>/', views_wms.workspace, name='staff_wms_section'),
path('panel/wms', views_wms.workspace, name='staff_wms_workspace'),
path('panel/wms/', views_wms.workspace),
path('panel/wms/sekcije/', bera_panel_settings.staff_wms_workspace, name='staff_wms_model_workspace'),
```

Glavni views: `views_wms.py`, `wms_price_print.py`, `bera_panel_stock.py`.
Zavisnosti: `wms_legacy.py`, `wms_orders.py`, `wms_excel.py`, `magacin.py`,
`wms_customer_forms.py`, `warehouse_access.py`, `bera_panel_modules.py`,
`bera_home.py`, `request_performance.py`, context_processors i middleware.
Modeli: Product/ProductVariation, WarehouseLocation/Stock/Movement,
Order/OrderItem, WMSOrder/OrderItem/Customer/PickLine/Transfer/Settings i
ModulePermissions. Zadržani stari mirror modeli nisu autoritet lagera.

### WMS templates

- `EcommerceApp/template/bera_reference/staff/wms/announcement_print.html`
- `EcommerceApp/template/bera_reference/staff/wms/base.html`
- `EcommerceApp/template/bera_reference/staff/wms/customer_form.html`
- `EcommerceApp/template/bera_reference/staff/wms/customers.html`
- `EcommerceApp/template/bera_reference/staff/wms/empty.html`
- `EcommerceApp/template/bera_reference/staff/wms/history.html`
- `EcommerceApp/template/bera_reference/staff/wms/icon.html`
- `EcommerceApp/template/bera_reference/staff/wms/invoice_selection.html`
- `EcommerceApp/template/bera_reference/staff/wms/location_barcode_print.html`
- `EcommerceApp/template/bera_reference/staff/wms/location_detail.html`
- `EcommerceApp/template/bera_reference/staff/wms/location_transfer_row.html`
- `EcommerceApp/template/bera_reference/staff/wms/online_order_form.html`
- `EcommerceApp/template/bera_reference/staff/wms/order_detail.html`
- `EcommerceApp/template/bera_reference/staff/wms/order_form.html`
- `EcommerceApp/template/bera_reference/staff/wms/orders_print.html`
- `EcommerceApp/template/bera_reference/staff/wms/pagination.html`
- `EcommerceApp/template/bera_reference/staff/wms/picking.html`
- `EcommerceApp/template/bera_reference/staff/wms/picking_history.html`
- `EcommerceApp/template/bera_reference/staff/wms/podesavanje.html`
- `EcommerceApp/template/bera_reference/staff/wms/prenosnica.html`
- `EcommerceApp/template/bera_reference/staff/wms/stampa_cijena.html`
- `EcommerceApp/template/bera_reference/staff/wms/stock_search.html`
- `EcommerceApp/template/bera_reference/staff/wms/workspace.html`

### WMS JavaScript

- `EcommerceApp/static/bera_reference/js/wms-online-order.js`
- `EcommerceApp/static/bera_reference/js/wms-order-customers.js`
- `EcommerceApp/static/bera_reference/js/wms-order-leave-guard.js`
- `EcommerceApp/static/bera_reference/js/wms-picking.js`

Zajednički base: `template/bera_reference/base.html`, CSS
`static/bera_reference/css/bera-wms-independent.css`, shared JS main,
csrf-session, staff-alerts, module unlock dialog i permanent-form-inputs.
