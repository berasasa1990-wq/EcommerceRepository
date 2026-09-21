# Inventar referenci nakon SEO izmjena

Pronađeno 240 pojedinačnih pojavljivanja u izvornom radnom stablu. Tajne vrijednosti iz `.env` nisu kopirane.

A: 17; B: 6; C: 36; D: 181.

A = preostali javni tekst/default; B = media host; C = migracija/kompatibilnost; D = nije SEO URL.

| Datoteka:linija | Grupa | Objašnjenje |
|---|---|---|
| `.env.example:9` | D | Operativna konfiguracija; vrijednosti nisu prikazane zbog tajnih podataka. |
| `.env.example:11` | D | Operativna konfiguracija; vrijednosti nisu prikazane zbog tajnih podataka. |
| `.env.example:12` | D | Operativna konfiguracija; vrijednosti nisu prikazane zbog tajnih podataka. |
| `.env.example:13` | D | Operativna konfiguracija; vrijednosti nisu prikazane zbog tajnih podataka. |
| `EcommerceApp/admin.py:987` | D | Opis proizvoda / ključna fraza, nije naziv starog domena. |
| `EcommerceApp/admin.py:1069` | A | Stari javni naziv u izvornom tekstu/defaultu; runtime SEO izlaz je sanitizovan ili je potrebna ručna provjera. |
| `EcommerceApp/admin.py:1069` | D | Opis proizvoda / ključna fraza, nije naziv starog domena. |
| `EcommerceApp/admin.py:2305` | A | Stari javni naziv u izvornom tekstu/defaultu; runtime SEO izlaz je sanitizovan ili je potrebna ručna provjera. |
| `EcommerceApp/apps.py:55` | A | Stari javni naziv u izvornom tekstu/defaultu; runtime SEO izlaz je sanitizovan ili je potrebna ručna provjera. |
| `EcommerceApp/apps.py:56` | A | Stari javni naziv u izvornom tekstu/defaultu; runtime SEO izlaz je sanitizovan ili je potrebna ručna provjera. |
| `EcommerceApp/context_processors.py:17` | A | Stari javni naziv u izvornom tekstu/defaultu; runtime SEO izlaz je sanitizovan ili je potrebna ručna provjera. |
| `EcommerceApp/context_processors.py:209` | A | Stari javni naziv u izvornom tekstu/defaultu; runtime SEO izlaz je sanitizovan ili je potrebna ručna provjera. |
| `EcommerceApp/emails.py:38` | D | Transakcijska poruka ili loyalty tok; izvan SEO promjene. |
| `EcommerceApp/emails.py:64` | D | Transakcijska poruka ili loyalty tok; izvan SEO promjene. |
| `EcommerceApp/emails.py:156` | D | Transakcijska poruka ili loyalty tok; izvan SEO promjene. |
| `EcommerceApp/emails.py:179` | D | Transakcijska poruka ili loyalty tok; izvan SEO promjene. |
| `EcommerceApp/emails.py:205` | D | Transakcijska poruka ili loyalty tok; izvan SEO promjene. |
| `EcommerceApp/emails.py:231` | D | Transakcijska poruka ili loyalty tok; izvan SEO promjene. |
| `EcommerceApp/emails.py:332` | D | Transakcijska poruka ili loyalty tok; izvan SEO promjene. |
| `EcommerceApp/emails.py:361` | D | Transakcijska poruka ili loyalty tok; izvan SEO promjene. |
| `EcommerceApp/emails.py:430` | D | Transakcijska poruka ili loyalty tok; izvan SEO promjene. |
| `EcommerceApp/emails.py:442` | D | Transakcijska poruka ili loyalty tok; izvan SEO promjene. |
| `EcommerceApp/emails.py:449` | D | Transakcijska poruka ili loyalty tok; izvan SEO promjene. |
| `EcommerceApp/emails.py:456` | D | Transakcijska poruka ili loyalty tok; izvan SEO promjene. |
| `EcommerceApp/emails.py:460` | D | Transakcijska poruka ili loyalty tok; izvan SEO promjene. |
| `EcommerceApp/emails.py:479` | D | Transakcijska poruka ili loyalty tok; izvan SEO promjene. |
| `EcommerceApp/emails.py:583` | D | Transakcijska poruka ili loyalty tok; izvan SEO promjene. |
| `EcommerceApp/emails.py:593` | D | Transakcijska poruka ili loyalty tok; izvan SEO promjene. |
| `EcommerceApp/loyalty.py:714` | D | Transakcijska poruka ili loyalty tok; izvan SEO promjene. |
| `EcommerceApp/loyalty.py:1040` | D | Transakcijska poruka ili loyalty tok; izvan SEO promjene. |
| `EcommerceApp/loyalty.py:1695` | D | Transakcijska poruka ili loyalty tok; izvan SEO promjene. |
| `EcommerceApp/loyalty.py:1877` | D | Transakcijska poruka ili loyalty tok; izvan SEO promjene. |
| `EcommerceApp/loyalty.py:1884` | D | Transakcijska poruka ili loyalty tok; izvan SEO promjene. |
| `EcommerceApp/loyalty.py:2002` | D | Transakcijska poruka ili loyalty tok; izvan SEO promjene. |
| `EcommerceApp/magacin.py:5336` | D | Stvarna email adresa ili Facebook profil; potrebna poslovna odluka. |
| `EcommerceApp/migrations/0062_sitesettings_kontakt_messenger.py:16` | C | Historijska migracija; ne prepisivati. |
| `EcommerceApp/migrations/0169_chat_settings_and_product_offer.py:42` | C | Historijska migracija; ne prepisivati. |
| `EcommerceApp/migrations/0180_seo_best_practice.py:22` | C | Historijska migracija; ne prepisivati. |
| `EcommerceApp/migrations/0180_seo_best_practice.py:22` | D | Opis proizvoda / ključna fraza, nije naziv starog domena. |
| `EcommerceApp/migrations/0180_seo_best_practice.py:25` | C | Historijska migracija; ne prepisivati. |
| `EcommerceApp/migrations/0180_seo_best_practice.py:27` | D | Opis proizvoda / ključna fraza, nije naziv starog domena. |
| `EcommerceApp/migrations/0180_seo_best_practice.py:29` | C | Historijska migracija; ne prepisivati. |
| `EcommerceApp/migrations/0180_seo_best_practice.py:35` | C | Historijska migracija; ne prepisivati. |
| `EcommerceApp/migrations/0180_seo_best_practice.py:38` | C | Historijska migracija; ne prepisivati. |
| `EcommerceApp/migrations/0180_seo_best_practice.py:40` | D | Opis proizvoda / ključna fraza, nije naziv starog domena. |
| `EcommerceApp/migrations/0180_seo_best_practice.py:46` | C | Historijska migracija; ne prepisivati. |
| `EcommerceApp/migrations/0180_seo_best_practice.py:48` | D | Opis proizvoda / ključna fraza, nije naziv starog domena. |
| `EcommerceApp/migrations/0180_seo_best_practice.py:51` | D | Opis proizvoda / ključna fraza, nije naziv starog domena. |
| `EcommerceApp/migrations/0180_seo_best_practice.py:57` | C | Historijska migracija; ne prepisivati. |
| `EcommerceApp/migrations/0180_seo_best_practice.py:57` | D | Opis proizvoda / ključna fraza, nije naziv starog domena. |
| `EcommerceApp/migrations/0180_seo_best_practice.py:65` | C | Historijska migracija; ne prepisivati. |
| `EcommerceApp/migrations/0180_seo_best_practice.py:68` | C | Historijska migracija; ne prepisivati. |
| `EcommerceApp/migrations/0180_seo_best_practice.py:73` | C | Historijska migracija; ne prepisivati. |
| `EcommerceApp/migrations/0180_seo_best_practice.py:76` | C | Historijska migracija; ne prepisivati. |
| `EcommerceApp/migrations/0180_seo_best_practice.py:82` | C | Historijska migracija; ne prepisivati. |
| `EcommerceApp/migrations/0180_seo_best_practice.py:87` | C | Historijska migracija; ne prepisivati. |
| `EcommerceApp/migrations/0180_seo_best_practice.py:92` | C | Historijska migracija; ne prepisivati. |
| `EcommerceApp/migrations/0180_seo_best_practice.py:123` | C | Historijska migracija; ne prepisivati. |
| `EcommerceApp/migrations/0180_seo_best_practice.py:123` | D | Opis proizvoda / ključna fraza, nije naziv starog domena. |
| `EcommerceApp/migrations/0180_seo_best_practice.py:129` | C | Historijska migracija; ne prepisivati. |
| `EcommerceApp/migrations/0180_seo_best_practice.py:153` | C | Historijska migracija; ne prepisivati. |
| `EcommerceApp/migrations/0180_seo_best_practice.py:164` | C | Historijska migracija; ne prepisivati. |
| `EcommerceApp/migrations/0180_seo_best_practice.py:197` | C | Historijska migracija; ne prepisivati. |
| `EcommerceApp/migrations/0180_seo_best_practice.py:232` | C | Historijska migracija; ne prepisivati. |
| `EcommerceApp/migrations/0180_seo_best_practice.py:235` | C | Historijska migracija; ne prepisivati. |
| `EcommerceApp/migrations/0180_seo_best_practice.py:249` | D | Opis proizvoda / ključna fraza, nije naziv starog domena. |
| `EcommerceApp/models.py:463` | D | Admin pomoćni tekst ili chat poruka; nije generisani SEO URL. |
| `EcommerceApp/models.py:526` | D | Admin pomoćni tekst ili chat poruka; nije generisani SEO URL. |
| `EcommerceApp/models.py:544` | A | Stari javni naziv u izvornom tekstu/defaultu; runtime SEO izlaz je sanitizovan ili je potrebna ručna provjera. |
| `EcommerceApp/models.py:549` | D | Stvarna email adresa ili Facebook profil; potrebna poslovna odluka. |
| `EcommerceApp/models.py:564` | D | Stvarna email adresa ili Facebook profil; potrebna poslovna odluka. |
| `EcommerceApp/models.py:583` | A | Stari javni naziv u izvornom tekstu/defaultu; runtime SEO izlaz je sanitizovan ili je potrebna ručna provjera. |
| `EcommerceApp/models.py:586` | A | Stari javni naziv u izvornom tekstu/defaultu; runtime SEO izlaz je sanitizovan ili je potrebna ručna provjera. |
| `EcommerceApp/models.py:735` | D | Admin pomoćni tekst ili chat poruka; nije generisani SEO URL. |
| `EcommerceApp/models.py:1584` | C | Provjera internog linka za stari host tokom migracije. |
| `EcommerceApp/models.py:1584` | C | Provjera internog linka za stari host tokom migracije. |
| `EcommerceApp/odoo_sales.py:435` | D | Operativna integracija; izvan SEO prikaza. |
| `EcommerceApp/olx_api.py:50` | D | Operativna integracija; izvan SEO prikaza. |
| `EcommerceApp/quick_activation.py:186` | D | Operativna integracija; izvan SEO prikaza. |
| `EcommerceApp/static/admin/css/ozr_admin.css:1` | D | Stil/skripta ili natpis za osoblje; nije SEO URL. |
| `EcommerceApp/static/admin/css/ozr_admin.v20260830.css:1` | D | Stil/skripta ili natpis za osoblje; nije SEO URL. |
| `EcommerceApp/static/js/staff-alerts.js:355` | D | Opis proizvoda / ključna fraza, nije naziv starog domena. |
| `EcommerceApp/static/js/staff-alerts.js:376` | D | Stil/skripta ili natpis za osoblje; nije SEO URL. |
| `EcommerceApp/static/js/staff-product-objava.js:488` | D | Stil/skripta ili natpis za osoblje; nije SEO URL. |
| `EcommerceApp/static/js/staff-product-objava.js:874` | D | Stil/skripta ili natpis za osoblje; nije SEO URL. |
| `EcommerceApp/static/maintenance.html:8` | A | Stari javni naziv u izvornom tekstu/defaultu; runtime SEO izlaz je sanitizovan ili je potrebna ručna provjera. |
| `EcommerceApp/static/maintenance.html:32` | A | Stari javni naziv u izvornom tekstu/defaultu; runtime SEO izlaz je sanitizovan ili je potrebna ručna provjera. |
| `EcommerceApp/template/admin/EcommerceApp/product/brzi_unos_aktivacija.html:1524` | D | Interna staff/admin stranica; nije indeksabilna. |
| `EcommerceApp/template/admin/EcommerceApp/product/brzi_unos_aktivacija.html:1606` | D | Opis proizvoda / ključna fraza, nije naziv starog domena. |
| `EcommerceApp/template/admin/base_site.html:4` | D | Interna staff/admin stranica; nije indeksabilna. |
| `EcommerceApp/template/admin/base_site.html:15` | D | Interna staff/admin stranica; nije indeksabilna. |
| `EcommerceApp/template/admin/base_site.html:17` | D | Interna staff/admin stranica; nije indeksabilna. |
| `EcommerceApp/template/base.html:7` | B | Media/CDN URL; ostaje na starom media hostu. |
| `EcommerceApp/template/order_success.html:12` | D | Opis proizvoda / ključna fraza, nije naziv starog domena. |
| `EcommerceApp/template/ponuda_pdf.html:6` | A | Stari javni naziv u izvornom tekstu/defaultu; runtime SEO izlaz je sanitizovan ili je potrebna ručna provjera. |
| `EcommerceApp/template/ponuda_pdf.html:251` | A | Stari javni naziv u izvornom tekstu/defaultu; runtime SEO izlaz je sanitizovan ili je potrebna ručna provjera. |
| `EcommerceApp/template/ponuda_pdf.html:253` | D | Opis proizvoda / ključna fraza, nije naziv starog domena. |
| `EcommerceApp/template/ponuda_pdf.html:268` | A | Stari javni naziv u izvornom tekstu/defaultu; runtime SEO izlaz je sanitizovan ili je potrebna ručna provjera. |
| `EcommerceApp/template/ponuda_pdf.html:327` | A | Stari javni naziv u izvornom tekstu/defaultu; runtime SEO izlaz je sanitizovan ili je potrebna ručna provjera. |
| `EcommerceApp/template/site_prep.html:9` | A | Stari javni naziv u izvornom tekstu/defaultu; runtime SEO izlaz je sanitizovan ili je potrebna ručna provjera. |
| `EcommerceApp/template/site_prep.html:120` | A | Stari javni naziv u izvornom tekstu/defaultu; runtime SEO izlaz je sanitizovan ili je potrebna ručna provjera. |
| `EcommerceApp/template/staff/active_carts.html:7` | D | Interna staff/admin stranica; nije indeksabilna. |
| `EcommerceApp/template/staff/admin_panel.html:7` | D | Interna staff/admin stranica; nije indeksabilna. |
| `EcommerceApp/template/staff/b2b_live.html:7` | D | Interna staff/admin stranica; nije indeksabilna. |
| `EcommerceApp/template/staff/gift_voucher_print.html:190` | D | Interna staff/admin stranica; nije indeksabilna. |
| `EcommerceApp/template/staff/live_analytics.html:6` | D | Interna staff/admin stranica; nije indeksabilna. |
| `EcommerceApp/template/staff/loyalty_system.html:7` | D | Interna staff/admin stranica; nije indeksabilna. |
| `EcommerceApp/template/staff/magacin/base.html:7` | D | Interna staff/admin stranica; nije indeksabilna. |
| `EcommerceApp/template/staff/magacin/brzi_unos_aktivacija.html:1525` | D | Interna staff/admin stranica; nije indeksabilna. |
| `EcommerceApp/template/staff/magacin/brzi_unos_aktivacija.html:1607` | D | Opis proizvoda / ključna fraza, nije naziv starog domena. |
| `EcommerceApp/template/staff/online_orders.html:6` | D | Interna staff/admin stranica; nije indeksabilna. |
| `EcommerceApp/template/staff/order_lookup.html:6` | D | Interna staff/admin stranica; nije indeksabilna. |
| `EcommerceApp/template/staff/uvoz.html:6` | D | Interna staff/admin stranica; nije indeksabilna. |
| `EcommerceApp/templates/b2b/base.html:13` | D | Opis proizvoda / ključna fraza, nije naziv starog domena. |
| `EcommerceApp/templates/b2b/base.html:25` | D | Opis proizvoda / ključna fraza, nije naziv starog domena. |
| `EcommerceApp/tests.py:750` | D | Testni podaci ili tvrdnja; pregledati pri čišćenju testova. |
| `EcommerceApp/tests_b2b.py:423` | D | Opis proizvoda / ključna fraza, nije naziv starog domena. |
| `EcommerceApp/tests_b2b.py:495` | D | Testni podaci ili tvrdnja; pregledati pri čišćenju testova. |
| `EcommerceApp/tests_home_banner_preload.py:83` | B | Media/CDN URL; ostaje na starom media hostu. |
| `EcommerceApp/tests_magacin.py:2269` | D | Opis proizvoda / ključna fraza, nije naziv starog domena. |
| `EcommerceApp/tests_magacin.py:2277` | D | Opis proizvoda / ključna fraza, nije naziv starog domena. |
| `EcommerceApp/tests_magacin.py:2287` | D | Opis proizvoda / ključna fraza, nije naziv starog domena. |
| `EcommerceApp/tests_magacin.py:2298` | D | Opis proizvoda / ključna fraza, nije naziv starog domena. |
| `EcommerceApp/tests_magacin.py:2321` | D | Opis proizvoda / ključna fraza, nije naziv starog domena. |
| `EcommerceApp/tests_magacin.py:2345` | D | Opis proizvoda / ključna fraza, nije naziv starog domena. |
| `EcommerceApp/tests_magacin.py:2352` | D | Opis proizvoda / ključna fraza, nije naziv starog domena. |
| `EcommerceApp/tests_seo_domain.py:15` | D | Testni podaci ili tvrdnja; pregledati pri čišćenju testova. |
| `EcommerceApp/tests_seo_domain.py:15` | D | Testni podaci ili tvrdnja; pregledati pri čišćenju testova. |
| `EcommerceApp/tests_seo_domain.py:56` | D | Testni podaci ili tvrdnja; pregledati pri čišćenju testova. |
| `EcommerceApp/tests_seo_domain.py:59` | D | Testni podaci ili tvrdnja; pregledati pri čišćenju testova. |
| `EcommerceApp/tests_seo_domain.py:65` | B | Media/CDN URL; ostaje na starom media hostu. |
| `EcommerceApp/tests_seo_domain.py:67` | D | Testni podaci ili tvrdnja; pregledati pri čišćenju testova. |
| `EcommerceApp/tests_seo_domain.py:68` | D | Testni podaci ili tvrdnja; pregledati pri čišćenju testova. |
| `EcommerceApp/utils/seo.py:29` | D | Opis proizvoda / ključna fraza, nije naziv starog domena. |
| `EcommerceApp/utils/seo.py:35` | C | Pravilo zamjene ili podrška za stari host tokom migracije. |
| `EcommerceApp/utils/seo.py:228` | B | Media/CDN URL; ostaje na starom media hostu. |
| `EcommerceApp/utils/seo.py:488` | D | Opis proizvoda / ključna fraza, nije naziv starog domena. |
| `EcommerceApp/utils/seo.py:493` | D | Opis proizvoda / ključna fraza, nije naziv starog domena. |
| `EcommerceApp/utils/seo.py:507` | D | Opis proizvoda / ključna fraza, nije naziv starog domena. |
| `EcommerceApp/utils/seo.py:516` | D | Opis proizvoda / ključna fraza, nije naziv starog domena. |
| `EcommerceApp/utils/seo.py:519` | D | Opis proizvoda / ključna fraza, nije naziv starog domena. |
| `EcommerceApp/utils/seo.py:524` | D | Opis proizvoda / ključna fraza, nije naziv starog domena. |
| `EcommerceApp/views.py:2085` | C | Provjera internog linka za stari host tokom migracije. |
| `EcommerceApp/views.py:2085` | C | Provjera internog linka za stari host tokom migracije. |
| `EcommerceApp/views.py:2822` | D | Opis proizvoda / ključna fraza, nije naziv starog domena. |
| `EcommerceApp/views.py:2828` | D | Opis proizvoda / ključna fraza, nije naziv starog domena. |
| `EcommerceApp/views.py:2834` | D | Opis proizvoda / ključna fraza, nije naziv starog domena. |
| `EcommerceApp/views.py:2845` | D | Opis proizvoda / ključna fraza, nije naziv starog domena. |
| `EcommerceApp/views.py:2847` | D | Opis proizvoda / ključna fraza, nije naziv starog domena. |
| `EcommerceApp/views.py:2854` | D | Opis proizvoda / ključna fraza, nije naziv starog domena. |
| `EcommerceApp/views.py:2863` | D | Opis proizvoda / ključna fraza, nije naziv starog domena. |
| `EcommerceApp/views.py:2865` | D | Opis proizvoda / ključna fraza, nije naziv starog domena. |
| `EcommerceApp/views.py:7248` | D | Opis proizvoda / ključna fraza, nije naziv starog domena. |
| `EcommerceApp/views.py:7261` | D | Opis proizvoda / ključna fraza, nije naziv starog domena. |
| `EcommerceApp/views_b2b.py:524` | D | Stvarna email adresa ili Facebook profil; potrebna poslovna odluka. |
| `EcommerceProject/settings.py:104` | C | Podrška za stari host, CSRF ili kolačiće tokom 301 migracije. |
| `EcommerceProject/settings.py:105` | C | Pravilo zamjene ili podrška za stari host tokom migracije. |
| `EcommerceProject/settings.py:106` | B | Media/CDN URL; ostaje na starom media hostu. |
| `EcommerceProject/settings.py:156` | C | Podrška za stari host, CSRF ili kolačiće tokom 301 migracije. |
| `EcommerceProject/settings.py:157` | C | Podrška za stari host, CSRF ili kolačiće tokom 301 migracije. |
| `EcommerceProject/settings.py:213` | C | Pravilo zamjene ili podrška za stari host tokom migracije. |
| `EcommerceProject/settings.py:213` | C | Pravilo zamjene ili podrška za stari host tokom migracije. |
| `EcommerceProject/settings.py:215` | C | Podrška za stari host, CSRF ili kolačiće tokom 301 migracije. |
| `EcommerceProject/settings.py:216` | C | Podrška za stari host, CSRF ili kolačiće tokom 301 migracije. |
| `EcommerceProject/settings.py:408` | B | Media/CDN URL; ostaje na starom media hostu. |
| `EcommerceProject/settings.py:548` | D | Stvarna email adresa ili Facebook profil; potrebna poslovna odluka. |
| `EcommerceProject/settings.py:552` | D | Stvarna email adresa ili Facebook profil; potrebna poslovna odluka. |
| `EcommerceProject/settings.py:556` | D | Stvarna email adresa ili Facebook profil; potrebna poslovna odluka. |
| `EcommerceProject/settings.py:565` | D | Messenger ili operativna postavka; nije SEO URL. |
| `EcommerceProject/settings.py:566` | D | Stvarna email adresa ili Facebook profil; potrebna poslovna odluka. |
| `EcommerceProject/settings.py:628` | D | Messenger ili operativna postavka; nije SEO URL. |
| `README.md:1` | D | Opis proizvoda / ključna fraza, nije naziv starog domena. |
| `README.md:3` | D | Dokumentacija ili operativni zapis; nije aktivni HTML. |
| `README.md:106` | D | Stvarna email adresa ili Facebook profil; potrebna poslovna odluka. |
| `render.yaml:52` | D | Operativna konfiguracija; vrijednosti nisu prikazane zbog tajnih podataka. |
| `render.yaml:54` | D | Operativna konfiguracija; vrijednosti nisu prikazane zbog tajnih podataka. |
| `render.yaml:56` | D | Operativna konfiguracija; vrijednosti nisu prikazane zbog tajnih podataka. |
| `render.yaml:58` | D | Operativna konfiguracija; vrijednosti nisu prikazane zbog tajnih podataka. |
| `render.yaml:90` | D | Operativna konfiguracija; vrijednosti nisu prikazane zbog tajnih podataka. |
| `scripts/settings_snapshot_0840.json:93` | D | Historijski snapshot; nije aktivan kod. |
| `scripts/settings_snapshot_0840.json:93` | D | Historijski snapshot; nije aktivan kod. |
| `scripts/settings_snapshot_0840.json:93` | D | Historijski snapshot; nije aktivan kod. |
| `scripts/settings_snapshot_0840.json:95` | D | Historijski snapshot; nije aktivan kod. |
| `scripts/settings_snapshot_1906.json:34` | D | Historijski snapshot; nije aktivan kod. |
| `scripts/settings_snapshot_1906.json:34` | D | Historijski snapshot; nije aktivan kod. |
| `scripts/settings_snapshot_1906.json:34` | D | Historijski snapshot; nije aktivan kod. |
| `scripts/settings_snapshot_1906.json:36` | D | Historijski snapshot; nije aktivan kod. |
| `scripts/settings_snapshot_2005.json:45` | D | Historijski snapshot; nije aktivan kod. |
| `scripts/settings_snapshot_2005.json:45` | D | Historijski snapshot; nije aktivan kod. |
| `scripts/settings_snapshot_2005.json:45` | D | Historijski snapshot; nije aktivan kod. |
| `scripts/settings_snapshot_2005.json:47` | D | Historijski snapshot; nije aktivan kod. |
| `scripts/snapshots/0840/EcommerceApp/admin.py:167` | D | Historijski snapshot; nije aktivan kod. |
| `scripts/snapshots/0840/EcommerceApp/template/product_detail.html:11` | D | Historijski snapshot; nije aktivan kod. |
| `scripts/snapshots/pocetno/EcommerceApp/static/js/staff-product-objava.js:488` | D | Historijski snapshot; nije aktivan kod. |
| `scripts/snapshots/pocetno/EcommerceApp/static/js/staff-product-objava.js:874` | D | Historijski snapshot; nije aktivan kod. |
| `scripts/snapshots/pocetno/EcommerceApp/template/account/index.html:7` | D | Historijski snapshot; nije aktivan kod. |
| `scripts/snapshots/pocetno/EcommerceApp/template/account/order_detail.html:7` | D | Historijski snapshot; nije aktivan kod. |
| `scripts/snapshots/pocetno/EcommerceApp/template/admin/EcommerceApp/product/brzi_unos_aktivacija.html:1524` | D | Historijski snapshot; nije aktivan kod. |
| `scripts/snapshots/pocetno/EcommerceApp/template/admin/EcommerceApp/product/brzi_unos_aktivacija.html:1606` | D | Opis proizvoda / ključna fraza, nije naziv starog domena. |
| `scripts/snapshots/pocetno/EcommerceApp/template/admin/base_site.html:4` | D | Historijski snapshot; nije aktivan kod. |
| `scripts/snapshots/pocetno/EcommerceApp/template/admin/base_site.html:14` | D | Historijski snapshot; nije aktivan kod. |
| `scripts/snapshots/pocetno/EcommerceApp/template/auth/login.html:5` | D | Historijski snapshot; nije aktivan kod. |
| `scripts/snapshots/pocetno/EcommerceApp/template/auth/register.html:5` | D | Historijski snapshot; nije aktivan kod. |
| `scripts/snapshots/pocetno/EcommerceApp/template/base.html:89` | D | Historijski snapshot; nije aktivan kod. |
| `scripts/snapshots/pocetno/EcommerceApp/template/base.html:272` | D | Historijski snapshot; nije aktivan kod. |
| `scripts/snapshots/pocetno/EcommerceApp/template/base.html:274` | D | Historijski snapshot; nije aktivan kod. |
| `scripts/snapshots/pocetno/EcommerceApp/template/cart.html:6` | D | Historijski snapshot; nije aktivan kod. |
| `scripts/snapshots/pocetno/EcommerceApp/template/checkout.html:5` | D | Historijski snapshot; nije aktivan kod. |
| `scripts/snapshots/pocetno/EcommerceApp/template/emails/live_offer.html:11` | D | Historijski snapshot; nije aktivan kod. |
| `scripts/snapshots/pocetno/EcommerceApp/template/feeds/facebook_feed.xml:4` | D | Historijski snapshot; nije aktivan kod. |
| `scripts/snapshots/pocetno/EcommerceApp/template/order_success.html:5` | D | Historijski snapshot; nije aktivan kod. |
| `scripts/snapshots/pocetno/EcommerceApp/template/pages/about.html:28` | D | Historijski snapshot; nije aktivan kod. |
| `scripts/snapshots/pocetno/EcommerceApp/template/partials/footer.html:44` | D | Historijski snapshot; nije aktivan kod. |
| `scripts/snapshots/pocetno/EcommerceApp/template/partials/footer.html:104` | D | Historijski snapshot; nije aktivan kod. |
| `scripts/snapshots/pocetno/EcommerceApp/template/partials/loyalty_card_print.html:11` | D | Historijski snapshot; nije aktivan kod. |
| `scripts/snapshots/pocetno/EcommerceApp/template/partials/loyalty_card_print.html:15` | D | Historijski snapshot; nije aktivan kod. |
| `scripts/snapshots/pocetno/EcommerceApp/template/partials/order_invoice_document.html:43` | D | Historijski snapshot; nije aktivan kod. |
| `scripts/snapshots/pocetno/EcommerceApp/template/partials/order_invoice_document.html:138` | D | Historijski snapshot; nije aktivan kod. |
| `scripts/snapshots/pocetno/EcommerceApp/template/ponuda_pdf.html:6` | D | Historijski snapshot; nije aktivan kod. |
| `scripts/snapshots/pocetno/EcommerceApp/template/ponuda_pdf.html:201` | D | Historijski snapshot; nije aktivan kod. |
| `scripts/snapshots/pocetno/EcommerceApp/template/ponuda_pdf.html:203` | D | Opis proizvoda / ključna fraza, nije naziv starog domena. |
| `scripts/snapshots/pocetno/EcommerceApp/template/ponuda_pdf.html:219` | D | Historijski snapshot; nije aktivan kod. |
| `scripts/snapshots/pocetno/EcommerceApp/template/ponuda_pdf.html:277` | D | Historijski snapshot; nije aktivan kod. |
| `scripts/snapshots/pocetno/EcommerceApp/template/robots.txt:1` | D | Historijski snapshot; nije aktivan kod. |
| `scripts/snapshots/pocetno/EcommerceApp/template/robots.txt:8` | D | Historijski snapshot; nije aktivan kod. |
| `scripts/snapshots/pocetno/EcommerceApp/template/site_prep.html:9` | D | Historijski snapshot; nije aktivan kod. |
| `scripts/snapshots/pocetno/EcommerceApp/template/site_prep.html:120` | D | Historijski snapshot; nije aktivan kod. |
| `scripts/snapshots/pocetno/EcommerceApp/template/staff/active_carts.html:7` | D | Historijski snapshot; nije aktivan kod. |
| `scripts/snapshots/pocetno/EcommerceApp/template/staff/admin_panel.html:6` | D | Historijski snapshot; nije aktivan kod. |
| `scripts/snapshots/pocetno/EcommerceApp/template/staff/gift_voucher_print.html:190` | D | Historijski snapshot; nije aktivan kod. |
| `scripts/snapshots/pocetno/EcommerceApp/template/staff/live_analytics.html:6` | D | Historijski snapshot; nije aktivan kod. |
| `scripts/snapshots/pocetno/EcommerceApp/template/staff/loyalty_system.html:7` | D | Historijski snapshot; nije aktivan kod. |
| `scripts/snapshots/pocetno/EcommerceApp/template/staff/magacin/base.html:7` | D | Historijski snapshot; nije aktivan kod. |
| `scripts/snapshots/pocetno/EcommerceApp/template/staff/magacin/brzi_unos_aktivacija.html:1525` | D | Historijski snapshot; nije aktivan kod. |
| `scripts/snapshots/pocetno/EcommerceApp/template/staff/magacin/brzi_unos_aktivacija.html:1607` | D | Opis proizvoda / ključna fraza, nije naziv starog domena. |
| `scripts/snapshots/pocetno/EcommerceApp/template/staff/olx_messages.html:6` | D | Historijski snapshot; nije aktivan kod. |
| `scripts/snapshots/pocetno/EcommerceApp/template/staff/online_orders.html:6` | D | Historijski snapshot; nije aktivan kod. |
| `scripts/snapshots/pocetno/EcommerceApp/template/staff/order_lookup.html:6` | D | Historijski snapshot; nije aktivan kod. |
| `scripts/snapshots/pocetno/EcommerceApp/template/staff/site_overview.html:6` | D | Historijski snapshot; nije aktivan kod. |
| `scripts/snapshots/pocetno/EcommerceApp/template/staff/uvoz.html:6` | D | Historijski snapshot; nije aktivan kod. |
