# Prikaz kategorija prema BERA-webshopu

`category_detail` u `EcommerceApp/views.py` koristi postojeći `render_storefront` za `category.html` i `category_subcategories.html`. Originalni predlošci ostaju sačuvani. Kategorije, filteri, sortiranje, paginacija, SEO i URL-ovi koriste postojeću poslovnu logiku.

Preneseni predlošci `bera_reference/category.html`, `category_subcategories.html`, `partials/product_catalog.html` i `partials/category_subnav.html` identični su izvornim predlošcima nakon uklanjanja lokalnog prefiksa. Koriste već prenesene zajedničke komponente i resurse.

Provjera sa sintetičkim podacima na širinama 1440 i 390 px: podkategorije i direktna lista artikala imaju identične piksele; prikaz svih artikala ima isti raspored, uz razliku samo u nazivu prodavnice u SEO tekstu koji novi sajt učitava iz vlastitih postavki (Carpologija BH umjesto naziva testne izvorne prodavnice). Svih šest prikaza nema JavaScript grešaka ni neuspjelih resursa. Snimci i rezultati su u `reports/bera-category-visual/`; ponovljiva provjera u `scripts/bera_category_qa.cjs`.

HTTP provjere podkategorija, direktne liste, svih artikala i sortiranja prolaze sa statusom 200. Django system check prolazi. Nisu potrebne nove migracije. Izvorni projekat i stvarna baza nisu izmijenjeni.
