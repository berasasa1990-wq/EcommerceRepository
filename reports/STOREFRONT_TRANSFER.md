# Prenos izvornog webshopa

Izvor: `/Users/bera1990/BERA-webshop` → novi projekat `/Users/bera1990/Ecommerce`.

Prenesen je postojeći kod, bez izrade novog dizajna. Prenesena su **302 promijenjena ili nedostajuća fajla**, uz postojeće identične fajlove koji nisu prepisivani. Izvorni kod je provjeren SHA-256 zapisima poslije provjera i nije promijenjen. Nisu kopirani `.env`, lozinke/tokene okruženja, izvorna baza niti korisnički uploadi i podaci kupaca. Raniji kod odredišta sačuvan je prije prepisivanja u `/private/tmp/ecommerce-transfer-original-code`.

## Rute i zavisnosti

| Stranica | Stvarna ruta | View | Glavni predložak |
|---|---|---|---|
| Početna | `/` | `views.home` | `home.html` |
| Artikal | `/artikal/<slug>/` | `views.product_detail` | `product_detail.html` |
| Korpa | `/korpa/` | `views.cart_view` | `cart.html` |
| Checkout | `/narudzba/` | `views.checkout` | `checkout.html` |
| Panel | `/panel`, `/panel/` | `views.staff_panel` | `staff/panel.html` |
| Postavke i grupe panela | `/panel/podesavanja`, `/panel/b2b`, `/panel/loyalty`, `/panel/greb-greb`, `/panel/sekcija/<section>/` | `panel_settings` | `staff/settings_workspace.html` i uključeni editori |
| Editor modela panela | `/panel/podesavanja/sekcije/…` | `panel_admin_site` | postojeći Django admin predlošci i proširenja |
| Narudžbe | `/nalog/narudzbe/`, `/nalog/online-narudzbe/`, `/nalog/provjera-narudzbi/` | postojeći staff views | postojeći staff predlošci |
| Skladište | `/wms/`, `/wms/<section>/`, `/nalog/magacin/…` | `views_wms`, `views_magacin` | postojeći WMS i magacin predlošci |

`reports/storefront_dependency_inventory.json` sadrži 180 ruta i zavisnosti 131 korijenskog predloška: extends/include komponente i literalne static reference. `reports/storefront_transfer_manifest.json` sadrži svaki preneseni fajl, izvorni i konačni hash te oznaku prilagodbe.

Header, footer, navigacija, cijene, ponude, količinski popusti, kuponi, sažeci narudžbe i popup komponente koriste postojeće zajedničke include predloške. Checkout koristi modele novog projekta `Order`, `OrderItem`, kupce, postojeće obračune i rezervaciju zaliha; nisu dodavane paralelne tabele narudžbi.

## Nužne prilagodbe

- U postojećim postavkama dodani su branding context processor, inventory middleware i konfiguracija izvornog admina. Postojeće postavke baze, emailova, storagea i integracija su zadržane.
- Logo, naziv, kontakt i podaci firme učitavaju se iz `SiteSettings`; rezervni logo headera koristi `brand_logo_url`, umjesto fiksnog BERA loga.
- Sačuvan je raniji URL i reverse naziv `/nalog/admin/` / `staff_admin_panel`, koji otvara novi panel.
- Prenesene su nove migracije 0311–0351. Ranije migracije odredišta nisu prepisivane. Lokalna migracija 0352 usklađuje podrazumijevane vrijednosti i metapodatke polja.
- B2B admin media sada izričito učitava `jquery.init.js` prije `b2b-settings.js`, čime je otklonjena izvorna greška `django is not defined` bez promjene dizajna.
- Testovi konfiguracije Monri zadržavaju pravila postojećeg projekta; nisu promijenjena deployment pravila za čitanje merchant credentials. Nova konfiguracija plaćanja nije kopirana iz izvora.

## Provjere

- Django `check`: bez grešaka; `makemigrations --check --dry-run`: bez promjena; `git diff --check`: prolazi.
- 103 ciljana Django testa prolaze: korpa, checkout, kupci, rezervacija zaliha, modeli narudžbi, galerija 360, kuponi, setovi, prava panela i zaključani moduli.
- Još 19 izvršenih testova ponuda/izbora artikla/popusta prolazi, od kojih je 12 dodatnih u odnosu na prethodnu grupu. Četiri testa postojeće Monri konfiguracije prolaze. Ukupno **119 različitih ciljanih testova** prolazi.
- 40 provjera u pregledniku prolazi: izvor i odredište × desktop/mobile × 10 provjera (anonimni pristup panelu, otvaranje/zatvaranje uvećanja, izbor količine, dodavanje, promjena količine, reload, uklanjanje, odbijanje praznog checkouta i uspješna pouzećna testna narudžba).
- Izgled provjeren na istim širinama oba sajta: **1440 px i 390 px**, visina viewporta 900 px. Glavne stranice i editori: 38 parova; sve otkrivene poveznice panela: 116 parova. Sačuvano ukupno **308 snimaka**.
- `reports/transfer-visual/` i `reports/transfer-panel/` sadrže snimke, browser rezultate i poređenje. Mjereni su dimenzije dokumenta, rasporedi header/main/footer/stranica, fontovi i boje, te razlike PNG piksela. Sitne razlike u rasterizaciji prikazane su odvojeno od razlika većih od 10 nivoa po RGB kanalu.
- QA je koristio istu zasebnu sintetičku bazu i iste sintetičke slike na oba projekta. Email backend je locmem, Monri/Mungos/R2/OLX/XExpress credentials i tracking su isključeni; preglednik blokira sve vanjske zahtjeve. Stvarni emailovi nisu poslani i stvarna plaćanja nisu izvršena.
- Lokalna postojeća baza odredišta pregledana je samo za čitanje radi provjere šeme: nema nedostajućih kolona modela. Njeni kupci i narudžbe nisu korišteni za vizuelni QA.
- Production static build uspješno je izgrađen u postojećem `staticfiles`: 99 kopiranih/promijenjenih, 270 nepromijenjenih fajlova, 820 post-processing izlaza. `requirements.txt` je isti kao u izvoru; nisu potrebni novi runtime paketi.

## Preostala ograničenja i zavisnosti

1. Poređenje identičnog izgleda odnosi se na isti sadržaj i postavke, uz sintetičke podatke. Izvorni uploadi i sadržaj baze nisu kopirani; novi sajt koristi svoje slike artikala, bannere, logo i identitet. Za podudaranje stvarnog sadržaja potrebno je podesiti odobrene resurse u novom projektu.
2. Izvor ima mobilno preklapanje dugmeta „Opcije” s dugmetom za uvećanje na artiklima sa sličnim opcijama. Raspored je zadržan radi tražene vjernosti; uvećanje tada radi putem fokusa i Enter tipke. Desktop pointer provjera prolazi. Ovo je postojeća greška izvornog izgleda, a ne nova razlika.
3. Šire postojeće suite provjere imaju 14 neuspješnih testova (10 inventory/settings/WMS, 4 B2B administracije). Svih 14 je reproducirano i na neizmijenjenom izvoru u istim izolovanim uslovima. To nisu novouvedene razlike prenosa; nisu prepravljani testovi da sakriju te rezultate.
4. Historija podataka generiše zapise tokom QA posjeta, pa sadržaj i vremenski zapisi mogu biti različiti između uzastopnih snimaka. Dimenzije i stil su poređeni odvojeno.
5. Na drugom deploymentu potrebno je primijeniti migracije i napraviti `collectstatic` uz njegova postojeća okruženja. Stvarni email, dostava, kartični gateway i storage zahtijevaju postavke novog sajta; u QA su namjerno isključeni.
6. QA skripte zahtijevaju Playwright i Chromium preglednik. Koriste lokalni dostupni Playwright paket i Brave, uz override varijable `TRANSFER_PLAYWRIGHT` i `TRANSFER_BROWSER_EXECUTABLE` za drugo okruženje. Ove zavisnosti su samo za QA, ne za rad aplikacije.

## Ponovljiva izolovana provjera

Primjeri se izvršavaju iz korijena novog projekta. Koristiti `env -i PATH="$PATH" PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=scripts:. DJANGO_SETTINGS_MODULE=transfer_qa_settings` prije Python komandi, da se ne naslijede credentials okruženja. QA settings nikad ne učitava `.env`.

- `venv/bin/python manage.py check`
- `venv/bin/python manage.py migrate --noinput` (isključivo privremena QA baza)
- `venv/bin/python scripts/transfer_qa_seed.py`
- `venv/bin/python manage.py runserver 127.0.0.1:8011 --noreload`
- Referenca koristi isti QA settings preko `PYTHONPATH=/Users/bera1990/Ecommerce/scripts`, `TRANSFER_QA_ROOT=/Users/bera1990/BERA-webshop`, `TRANSFER_QA_DB=/private/tmp/ecommerce-transfer-qa.sqlite3` i port 8012; `PYTHONDONTWRITEBYTECODE=1` čuva izvor bez pycache zapisa.
- `node scripts/transfer_interaction_qa.cjs`
- `node scripts/transfer_visual_qa.cjs`
- `TRANSFER_QA_PANEL_LINKS=1 TRANSFER_VISUAL_OUTPUT=reports/transfer-panel node scripts/transfer_visual_qa.cjs`
- `venv/bin/python scripts/transfer_compare_visuals.py` i isto s argumentom `reports/transfer-panel`.

## Dodatni lokalni fajlovi

`EcommerceProject/settings.py`, `EcommerceApp/migrations/0352_alter_sitesettings_chat_pozdrav_poruka_and_more.py`, izvještaji i QA/inventory/compare skripte pod `scripts/transfer_*`.

## Svi preneseni fajlovi

### Python moduli i provjere

- `EcommerceApp/account_verification.py`
- `EcommerceApp/admin.py`
- `EcommerceApp/admin_forms.py`
- `EcommerceApp/admin_site.py`
- `EcommerceApp/apps.py`
- `EcommerceApp/branding.py`
- `EcommerceApp/cart.py`
- `EcommerceApp/context_processors.py`
- `EcommerceApp/customer_profiles.py`
- `EcommerceApp/emails.py`
- `EcommerceApp/forms.py`
- `EcommerceApp/loyalty.py`
- `EcommerceApp/magacin.py`
- `EcommerceApp/middleware/inventory_module.py`
- `EcommerceApp/models.py`
- `EcommerceApp/module_settings.py`
- `EcommerceApp/monri.py`
- `EcommerceApp/olx_api.py`
- `EcommerceApp/online_gift.py`
- `EcommerceApp/panel_admin.py`
- `EcommerceApp/panel_modules.py`
- `EcommerceApp/panel_settings.py`
- `EcommerceApp/pricing.py`
- `EcommerceApp/product360.py`
- `EcommerceApp/product_features.py`
- `EcommerceApp/product_identifiers.py`
- `EcommerceApp/quick_activation.py`
- `EcommerceApp/sitemaps.py`
- `EcommerceApp/sync_handlers.py`
- `EcommerceApp/templatetags/warehouse_access.py`
- `EcommerceApp/tests.py`
- `EcommerceApp/tests_admin_theme.py`
- `EcommerceApp/tests_akcija_admin.py`
- `EcommerceApp/tests_b2b.py`
- `EcommerceApp/tests_barcodes.py`
- `EcommerceApp/tests_category_logo.py`
- `EcommerceApp/tests_checkout_notifications.py`
- `EcommerceApp/tests_coupon_sale_exclusion.py`
- `EcommerceApp/tests_customer_profiles.py`
- `EcommerceApp/tests_home_banner_preload.py`
- `EcommerceApp/tests_inventory_module.py`
- `EcommerceApp/tests_magacin.py`
- `EcommerceApp/tests_menu_color.py`
- `EcommerceApp/tests_monri.py`
- `EcommerceApp/tests_mungos_bulk.py`
- `EcommerceApp/tests_mungos_product.py`
- `EcommerceApp/tests_order_admin_email.py`
- `EcommerceApp/tests_panel_access.py`
- `EcommerceApp/tests_panel_settings.py`
- `EcommerceApp/tests_product360.py`
- `EcommerceApp/tests_retired_features.py`
- `EcommerceApp/tests_seo_domain.py`
- `EcommerceApp/tests_settings_workspace.py`
- `EcommerceApp/tests_stock_notify.py`
- `EcommerceApp/tests_stock_tracking.py`
- `EcommerceApp/tests_warehouse_new_product.py`
- `EcommerceApp/tests_wms_independent.py`
- `EcommerceApp/upsell.py`
- `EcommerceApp/urls.py`
- `EcommerceApp/utils/images.py`
- `EcommerceApp/utils/seo.py`
- `EcommerceApp/views.py`
- `EcommerceApp/views_b2b.py`
- `EcommerceApp/views_magacin.py`
- `EcommerceApp/views_wms.py`
- `EcommerceApp/warehouse_access.py`
- `EcommerceApp/warehouse_product_form.py`
- `EcommerceApp/wms_excel.py`
- `EcommerceApp/wms_orders.py`
- `EcommerceApp/xexpress_service.py`
- `EcommerceProject/admin_apps.py`
- `EcommerceProject/urls.py`

### Nove migracije

- `EcommerceApp/migrations/0311_white_label_identity.py`
- `EcommerceApp/migrations/0312_configurable_newsletter_banner.py`
- `EcommerceApp/migrations/0313_forced_search_priority.py`
- `EcommerceApp/migrations/0314_product_stock_tracking.py`
- `EcommerceApp/migrations/0315_home_bestseller_products.py`
- `EcommerceApp/migrations/0316_inventory_module.py`
- `EcommerceApp/migrations/0317_inventory_module_label.py`
- `EcommerceApp/migrations/0318_product_sets_module.py`
- `EcommerceApp/migrations/0319_warehouse_tool_modules.py`
- `EcommerceApp/migrations/0320_spare_parts_module.py`
- `EcommerceApp/migrations/0321_stock_check_module.py`
- `EcommerceApp/migrations/0322_stock_check_tools.py`
- `EcommerceApp/migrations/0323_import_module.py`
- `EcommerceApp/migrations/0324_panel_tool_modules.py`
- `EcommerceApp/migrations/0325_banner_destination_module.py`
- `EcommerceApp/migrations/0326_panel_actions_module.py`
- `EcommerceApp/migrations/0327_independent_wms.py`
- `EcommerceApp/migrations/0328_product_wms_location.py`
- `EcommerceApp/migrations/0329_product_multiple_wms_locations.py`
- `EcommerceApp/migrations/0330_product_360.py`
- `EcommerceApp/migrations/0331_wms_transfer_settings.py`
- `EcommerceApp/migrations/0332_wms_section_permissions.py`
- `EcommerceApp/migrations/0333_wms_storefront_availability.py`
- `EcommerceApp/migrations/0334_merge_wms_stock_locations.py`
- `EcommerceApp/migrations/0335_wms_inventory_mode.py`
- `EcommerceApp/migrations/0336_visible_out_of_stock.py`
- `EcommerceApp/migrations/0337_wms_transfer_documents.py`
- `EcommerceApp/migrations/0338_wms_order_cancel.py`
- `EcommerceApp/migrations/0339_wms_cancellation_reason.py`
- `EcommerceApp/migrations/0340_wms_panel_orders.py`
- `EcommerceApp/migrations/0341_wms_odvajanje_robe.py`
- `EcommerceApp/migrations/0342_wms_picking_confirmation.py`
- `EcommerceApp/migrations/0343_wms_optional_picking_photo.py`
- `EcommerceApp/migrations/0344_wms_shortage_resolution.py`
- `EcommerceApp/migrations/0345_wms_completion_date.py`
- `EcommerceApp/migrations/0346_wms_order_items.py`
- `EcommerceApp/migrations/0347_wms_customers.py`
- `EcommerceApp/migrations/0348_wms_item_discount.py`
- `EcommerceApp/migrations/0349_wms_customer_delete.py`
- `EcommerceApp/migrations/0350_wms_picking_last_location.py`
- `EcommerceApp/migrations/0351_wms_vat_regular_price.py`

### Predlošci i zajedničke komponente

- `EcommerceApp/template/account/index.html`
- `EcommerceApp/template/account/order_detail.html`
- `EcommerceApp/template/admin/EcommerceApp/banner/change_list.html`
- `EcommerceApp/template/admin/EcommerceApp/modulepermissions/change_form.html`
- `EcommerceApp/template/admin/EcommerceApp/product/brzi_unos_aktivacija.html`
- `EcommerceApp/template/admin/base_site.html`
- `EcommerceApp/template/admin/customer_profile_overview.html`
- `EcommerceApp/template/admin/profile_reset_password.html`
- `EcommerceApp/template/auth/admin_password_reset_email.txt`
- `EcommerceApp/template/auth/admin_password_reset_subject.txt`
- `EcommerceApp/template/auth/login.html`
- `EcommerceApp/template/auth/password_reset_complete.html`
- `EcommerceApp/template/auth/password_reset_confirm.html`
- `EcommerceApp/template/auth/password_reset_done.html`
- `EcommerceApp/template/auth/password_reset_email.txt`
- `EcommerceApp/template/auth/password_reset_form.html`
- `EcommerceApp/template/auth/password_reset_subject.txt`
- `EcommerceApp/template/auth/register.html`
- `EcommerceApp/template/base.html`
- `EcommerceApp/template/cart.html`
- `EcommerceApp/template/category.html`
- `EcommerceApp/template/checkout.html`
- `EcommerceApp/template/emails/coupon_reward.html`
- `EcommerceApp/template/emails/coupon_reward.txt`
- `EcommerceApp/template/emails/live_offer.html`
- `EcommerceApp/template/emails/order_admin.html`
- `EcommerceApp/template/emails/stock_back.html`
- `EcommerceApp/template/emails/stock_unsubscribed.html`
- `EcommerceApp/template/feeds/facebook_feed.xml`
- `EcommerceApp/template/home.html`
- `EcommerceApp/template/monri_form.html`
- `EcommerceApp/template/monri_status.html`
- `EcommerceApp/template/order_success.html`
- `EcommerceApp/template/pages/about.html`
- `EcommerceApp/template/pages/payment.html`
- `EcommerceApp/template/pages/payment_security.html`
- `EcommerceApp/template/pages/purchase_terms.html`
- `EcommerceApp/template/partials/bera_cart_recommendations.html`
- `EcommerceApp/template/partials/bera_category_branch.html`
- `EcommerceApp/template/partials/bera_default_hero.html`
- `EcommerceApp/template/partials/bera_footer.html`
- `EcommerceApp/template/partials/bera_home_bottom.html`
- `EcommerceApp/template/partials/bera_home_nav.html`
- `EcommerceApp/template/partials/bera_offer_banners.html`
- `EcommerceApp/template/partials/bera_product_details.html`
- `EcommerceApp/template/partials/bera_product_related.html`
- `EcommerceApp/template/partials/coupon_form.html`
- `EcommerceApp/template/partials/footer.html`
- `EcommerceApp/template/partials/home_loyalty_banner.html`
- `EcommerceApp/template/partials/home_mobile_benefits.html`
- `EcommerceApp/template/partials/home_mobile_navigation.html`
- `EcommerceApp/template/partials/home_product_card.html`
- `EcommerceApp/template/partials/home_vlog.html`
- `EcommerceApp/template/partials/loyalty_card_print.html`
- `EcommerceApp/template/partials/order_invoice_document.html`
- `EcommerceApp/template/partials/product_brand_logo.html`
- `EcommerceApp/template/partials/product_bundle_set.html`
- `EcommerceApp/template/partials/product_card_price.html`
- `EcommerceApp/template/partials/product_catalog.html`
- `EcommerceApp/template/partials/product_catalog_card.html`
- `EcommerceApp/template/partials/product_qty_deal.html`
- `EcommerceApp/template/partials/product_stock_label.html`
- `EcommerceApp/template/partials/stock_notify_button.html`
- `EcommerceApp/template/ponuda_pdf.html`
- `EcommerceApp/template/product_detail.html`
- `EcommerceApp/template/robots.txt`
- `EcommerceApp/template/site_prep.html`
- `EcommerceApp/template/staff/active_carts.html`
- `EcommerceApp/template/staff/b2b_live.html`
- `EcommerceApp/template/staff/gift_voucher.html`
- `EcommerceApp/template/staff/gift_voucher_print.html`
- `EcommerceApp/template/staff/live_analytics.html`
- `EcommerceApp/template/staff/loyalty_system.html`
- `EcommerceApp/template/staff/loyalty_workspace.html`
- `EcommerceApp/template/staff/magacin/artikal.html`
- `EcommerceApp/template/staff/magacin/artikli.html`
- `EcommerceApp/template/staff/magacin/base.html`
- `EcommerceApp/template/staff/magacin/brzi_unos_aktivacija.html`
- `EcommerceApp/template/staff/magacin/brzi_unos_novi.html`
- `EcommerceApp/template/staff/magacin/dupli_barkodovi.html`
- `EcommerceApp/template/staff/magacin/module_locked.html`
- `EcommerceApp/template/staff/magacin/narudzbe.html`
- `EcommerceApp/template/staff/magacin/tool_lock.html`
- `EcommerceApp/template/staff/online_orders.html`
- `EcommerceApp/template/staff/order_lookup.html`
- `EcommerceApp/template/staff/orders_validation.html`
- `EcommerceApp/template/staff/orders_validation_content.html`
- `EcommerceApp/template/staff/orders_validation_scripts.html`
- `EcommerceApp/template/staff/panel.html`
- `EcommerceApp/template/staff/scratch_analytics.html`
- `EcommerceApp/template/staff/scratch_workspace.html`
- `EcommerceApp/template/staff/settings.html`
- `EcommerceApp/template/staff/settings_editor_base.html`
- `EcommerceApp/template/staff/settings_workspace.html`
- `EcommerceApp/template/staff/uvoz.html`
- `EcommerceApp/template/staff/wms/announcement_print.html`
- `EcommerceApp/template/staff/wms/base.html`
- `EcommerceApp/template/staff/wms/customer_form.html`
- `EcommerceApp/template/staff/wms/customers.html`
- `EcommerceApp/template/staff/wms/empty.html`
- `EcommerceApp/template/staff/wms/icon.html`
- `EcommerceApp/template/staff/wms/invoice_selection.html`
- `EcommerceApp/template/staff/wms/location_barcode_print.html`
- `EcommerceApp/template/staff/wms/location_detail.html`
- `EcommerceApp/template/staff/wms/location_transfer_row.html`
- `EcommerceApp/template/staff/wms/online_order_form.html`
- `EcommerceApp/template/staff/wms/order_detail.html`
- `EcommerceApp/template/staff/wms/order_form.html`
- `EcommerceApp/template/staff/wms/orders_print.html`
- `EcommerceApp/template/staff/wms/picking.html`
- `EcommerceApp/template/staff/wms/podesavanje.html`
- `EcommerceApp/template/staff/wms/prenosnica.html`
- `EcommerceApp/template/staff/wms/stock_search.html`
- `EcommerceApp/template/staff/wms/workspace.html`
- `EcommerceApp/template/staff/wms_workspace.html`
- `EcommerceApp/template/wishlist.html`
- `EcommerceApp/templates/b2b/base.html`
- `EcommerceApp/templates/b2b/category_tree.html`

### CSS, JavaScript i statički resursi

- `EcommerceApp/static/admin/css/bera-admin.css`
- `EcommerceApp/static/admin/css/ozr_admin.css`
- `EcommerceApp/static/admin/css/ozr_admin.v20260830.css`
- `EcommerceApp/static/css/admin-module-switches.css`
- `EcommerceApp/static/css/b2b.css`
- `EcommerceApp/static/css/bera-account.css`
- `EcommerceApp/static/css/bera-cart.css`
- `EcommerceApp/static/css/bera-catalog.css`
- `EcommerceApp/static/css/bera-home.css`
- `EcommerceApp/static/css/bera-offer-popup.css`
- `EcommerceApp/static/css/bera-orders.css`
- `EcommerceApp/static/css/bera-product.css`
- `EcommerceApp/static/css/bera-wms-independent.css`
- `EcommerceApp/static/css/desktop-reference.css`
- `EcommerceApp/static/css/home-mobile-reference.css`
- `EcommerceApp/static/css/newsletter-gray.css`
- `EcommerceApp/static/css/panel-articles.css`
- `EcommerceApp/static/css/panel-blue.css`
- `EcommerceApp/static/css/panel-model-editor.css`
- `EcommerceApp/static/css/panel-settings-workspace.css`
- `EcommerceApp/static/css/panel-settings.css`
- `EcommerceApp/static/css/panel-shell.css`
- `EcommerceApp/static/css/product-reference.css`
- `EcommerceApp/static/css/product-wms-location.css`
- `EcommerceApp/static/css/product360.css`
- `EcommerceApp/static/css/staff-magacin-menu.css`
- `EcommerceApp/static/css/staff-new-article-reference.css`
- `EcommerceApp/static/css/staff-orders-validation.css`
- `EcommerceApp/static/css/storefront-extras.min.css`
- `EcommerceApp/static/css/style.css`
- `EcommerceApp/static/img/bera-hero.webp`
- `EcommerceApp/static/img/bera-logo.svg`
- `EcommerceApp/static/img/email/box.png`
- `EcommerceApp/static/img/email/calendar.png`
- `EcommerceApp/static/img/email/card.png`
- `EcommerceApp/static/img/email/cart.png`
- `EcommerceApp/static/img/email/check.png`
- `EcommerceApp/static/img/email/document.png`
- `EcommerceApp/static/img/email/headset-white.png`
- `EcommerceApp/static/img/email/headset.png`
- `EcommerceApp/static/img/email/pin.png`
- `EcommerceApp/static/img/email/shield-white.png`
- `EcommerceApp/static/img/email/shield.png`
- `EcommerceApp/static/img/email/truck-white.png`
- `EcommerceApp/static/img/email/truck.png`
- `EcommerceApp/static/img/email/user.png`
- `EcommerceApp/static/img/maintenance-generic.svg`
- `EcommerceApp/static/img/newsletter-generic.svg`
- `EcommerceApp/static/img/pwa-icon.svg`
- `EcommerceApp/static/img/webshop-logo.svg`
- `EcommerceApp/static/js/b2b.js`
- `EcommerceApp/static/js/bera-brand-logo-align.js`
- `EcommerceApp/static/js/bera-brands.js`
- `EcommerceApp/static/js/bera-cart-layout.js`
- `EcommerceApp/static/js/bera-product.js`
- `EcommerceApp/static/js/cart-qty.min.js`
- `EcommerceApp/static/js/panel-articles-fit.js`
- `EcommerceApp/static/js/panel-settings-workspace.js`
- `EcommerceApp/static/js/panel-settings.js`
- `EcommerceApp/static/js/product-wms-list-add.js`
- `EcommerceApp/static/js/product-wms-location.js`
- `EcommerceApp/static/js/product360-admin.js`
- `EcommerceApp/static/js/product360-gallery.js`
- `EcommerceApp/static/js/product360.js`
- `EcommerceApp/static/js/staff-alerts.js`
- `EcommerceApp/static/js/staff-magacin.js`
- `EcommerceApp/static/js/staff-product-objava.js`
- `EcommerceApp/static/js/wms-online-order.js`
- `EcommerceApp/static/js/wms-order-customers.js`
- `EcommerceApp/static/js/wms-picking.js`
- `EcommerceApp/static/maintenance.html`

## Reproducirani neuspješni testovi izvora

- `ERROR: test_panel_orders_shared_with_wms_and_removed_when_not_active (EcommerceApp.tests_wms_independent.IndependentWMSTests.test_panel_orders_shared_with_wms_and_removed_when_not_active)`
- `FAIL: test_disabled_module_blocks_warehouse_tools_including_posts (EcommerceApp.tests_inventory_module.InventoryModuleTests.test_disabled_module_blocks_warehouse_tools_including_posts)`
- `FAIL: test_locked_tools_remain_visible_in_warehouse_menu (EcommerceApp.tests_inventory_module.InventoryModuleTests.test_locked_tools_remain_visible_in_warehouse_menu)`
- `FAIL: test_quantity_and_manual_modes (EcommerceApp.tests_inventory_module.InventoryModuleTests.test_quantity_and_manual_modes)`
- `FAIL: test_sets_module_is_independent_and_blocks_direct_access (EcommerceApp.tests_inventory_module.InventoryModuleTests.test_sets_module_is_independent_and_blocks_direct_access)`
- `FAIL: test_django_admin_contains_no_moved_shop_models (EcommerceApp.tests_settings_workspace.SettingsWorkspaceTests.test_django_admin_contains_no_moved_shop_models)`
- `FAIL: test_wms_group_contains_warehouse_and_four_model_sections (EcommerceApp.tests_settings_workspace.SettingsWorkspaceTests.test_wms_group_contains_warehouse_and_four_model_sections)`
- `FAIL: test_order_status_filters (EcommerceApp.tests_wms_independent.IndependentWMSTests.test_order_status_filters)`
- `FAIL: test_settings_and_partial_transfer (EcommerceApp.tests_wms_independent.IndependentWMSTests.test_settings_and_partial_transfer)`
- `FAIL: test_storefront_availability_setting_and_stock_module (EcommerceApp.tests_wms_independent.IndependentWMSTests.test_storefront_availability_setting_and_stock_module)`
- `FAIL: test_admin_autocomplete_excludes_selected_and_discount_validation (EcommerceApp.tests_b2b.B2BTests.test_admin_autocomplete_excludes_selected_and_discount_validation)`
- `FAIL: test_admin_b2b_live_shows_logged_in_cart (EcommerceApp.tests_b2b.B2BTests.test_admin_b2b_live_shows_logged_in_cart)`
- `FAIL: test_admin_banner_upload_and_custom_category_icon (EcommerceApp.tests_b2b.B2BTests.test_admin_banner_upload_and_custom_category_icon)`
- `FAIL: test_b2b_admin_collections_use_search_and_save_multiple_products (EcommerceApp.tests_b2b.B2BTests.test_b2b_admin_collections_use_search_and_save_multiple_products)`
