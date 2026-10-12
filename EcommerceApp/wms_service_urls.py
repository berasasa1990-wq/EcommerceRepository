"""Shared warehouse operations used by WMS, panel and Django admin.

Historical reverse names are internal compatibility identifiers only.
No routes under the retired /nalog/magacin/ prefix are registered.
"""
from django.urls import path
from . import views_magacin, views_product_sets, views_ledger
from . import manual_order_drafts, data_history
from .wms_tools import service_patterns

urlpatterns = service_patterns([
    path("wms/artikli-u-setu/", views_product_sets.product_sets, name="staff_magacin_setovi"),
    path('wms/operacije/historija-podataka/fajl/<int:pk>/', data_history.media_download, name='staff_magacin_media_history_download'),
    path('wms/operacije/nacrti/uvoz/', manual_order_drafts.import_local_draft, name='staff_magacin_draft_import'),
    path('wms/operacije/nacrti/sacuvaj/', manual_order_drafts.save_draft, name='staff_magacin_draft_save'),
    path('wms/operacije/nacrti/historija/', manual_order_drafts.history, name='staff_magacin_draft_history'),
    path('wms/operacije/nacrti/<int:pk>/vrati/', manual_order_drafts.restore_revision, name='staff_magacin_draft_restore'),
    path('wms/operacije/historija-podataka/', data_history.history, name='staff_magacin_data_history'),
    path('wms/operacije/historija-podataka/unos/', data_history.save_input, name='staff_magacin_save_input'),
    path('wms/operacije/duguje/', views_ledger.ledger, name='staff_magacin_duguje'),
    path('wms/operacije/barkod-provjera/', views_magacin.magacin_barkod_provjera, name='staff_magacin_barkod_provjera'),
    path('wms/dupli-barkodovi/<int:pk>/obrisi/', views_magacin.magacin_dupli_barkod_obrisi, name='staff_magacin_dupli_barkod_obrisi'),
    path('wms/dupli-barkodovi/', views_magacin.magacin_dupli_barkodovi, name='staff_magacin_dupli_barkodovi'),
    path('wms/operacije/artikli/', views_magacin.magacin_artikli, name='staff_magacin_artikli'),
    path('wms/operacije/artikli/brzi-unos/', views_magacin.magacin_brzi_unos, name='staff_magacin_brzi_unos'),
    path(
            'wms/operacije/artikli/brzi-unos/novi/',
            views_magacin.magacin_brzi_unos_novi,
            name='staff_magacin_brzi_unos_novi',
        ),
    path(
            'wms/operacije/artikli/brzi-unos/<int:product_id>/',
            views_magacin.magacin_brzi_unos_aktivacija,
            name='staff_magacin_brzi_unos_aktivacija',
        ),
    path('wms/operacije/artikli/lookup/', views_magacin.magacin_artikli_lookup, name='staff_magacin_artikli_lookup'),
    path(
            'wms/stampaj-deklaracije/',
            views_magacin.magacin_stampa_deklaracije,
            name='staff_magacin_stampa_deklaracije',
        ),
    path(
            'wms/stampaj-deklaracije/<int:pk>/stampa/',
            views_magacin.magacin_stampa_deklaracije_print,
            name='staff_magacin_stampa_deklaracije_print',
        ),
    path('wms/operacije/artikli/<int:pk>/', views_magacin.magacin_artikal, name='staff_magacin_artikal'),
    path(
            'wms/operacije/artikli/<int:pk>/stampa/',
            views_magacin.magacin_artikal_stampa,
            name='staff_magacin_artikal_stampa',
        ),
    path(
            'wms/operacije/artikli/<int:pk>/stampa-barkod/',
            views_magacin.magacin_artikal_stampa_barkod,
            name='staff_magacin_artikal_stampa_barkod',
        ),
    path('wms/operacije/artikli/<int:pk>/izmjena/', views_magacin.magacin_artikal_izmjena, name='staff_magacin_artikal_izmjena'),
    path('wms/operacije/artikli/<int:pk>/istorija/', views_magacin.magacin_istorija, name='staff_magacin_istorija'),
    path('wms/operacije/lokacije/lookup/', views_magacin.magacin_lokacije_lookup, name='staff_magacin_lokacije_lookup'),
    path(
            'wms/operacije/lokacije/<int:pk>/stampa/',
            views_magacin.magacin_lokacija_stampa,
            name='staff_magacin_lokacija_stampa',
        ),
    path('wms/operacije/lokacije/', views_magacin.magacin_lokacije, name='staff_magacin_lokacije'),
    path('wms/operacije/zalihe/', views_magacin.magacin_zalihe, name='staff_magacin_zalihe'),
    path(
            'wms/rezervni-dijelovi/',
            views_magacin.magacin_rezervni_dijelovi,
            name='staff_magacin_rezervni_dijelovi',
        ),
    path(
            'wms/dnevno-skidanje/',
            views_magacin.magacin_mp_dnevno_skidanje,
            name='staff_magacin_mp_dnevno',
        ),
    path('wms/operacije/transferi/', views_magacin.magacin_transferi, name='staff_magacin_transferi'),
    path('wms/popis-robe/', views_magacin.magacin_popis, name='staff_magacin_popis'),
    path('wms/popis-robe/<int:pk>/', views_magacin.magacin_popis, name='staff_magacin_popis_detail'),
    path('wms/popis-robe/stampa/', views_magacin.magacin_popis_stampa, name='staff_magacin_popis_stampa'),
    path('wms/popis-robe/test/', views_magacin.magacin_popis_test, name='staff_magacin_popis_test'),
    path('wms/operacije/provjera-lagera/', views_magacin.magacin_provjera_lagera, name='staff_magacin_provjera_lagera'),
    path(
            'wms/operacije/provjera-lagera/stampa/',
            views_magacin.magacin_provjera_lagera_stampa,
            name='staff_magacin_provjera_lagera_stampa',
        ),
    path('wms/fali-u-mp/', views_magacin.magacin_fali_na_sajtu, name='staff_magacin_fali_na_sajtu'),
    path('wms/operacije/narudzbe/', views_magacin.magacin_narudzbe, name='staff_magacin_narudzbe'),
    path('wms/operacije/narudzbe/nova/', views_magacin.magacin_narudzba_nova, name='staff_magacin_narudzba_nova'),
    path(
            'wms/operacije/narudzbe/nova/bulk/',
            views_magacin.magacin_narudzba_bulk,
            name='staff_magacin_narudzba_bulk',
        ),
    path('wms/operacije/narudzbe/vp/', views_magacin.magacin_vp_narudzba, name='staff_magacin_vp_narudzba'),
    path(
            'wms/operacije/narudzbe/brza-posta/',
            views_magacin.magacin_brza_posta,
            name='staff_magacin_brza_posta',
        ),
    path(
            'wms/operacije/narudzbe/brza-posta/<str:broj>/',
            views_magacin.magacin_brza_posta_detail,
            name='staff_magacin_brza_posta_detail',
        ),
    path(
            'wms/operacije/narudzbe/x-express/',
            views_magacin.magacin_xexpress_bulk,
            name='staff_magacin_xexpress_bulk',
        ),
    path('wms/operacije/kupci/', views_magacin.magacin_kupci, name='staff_magacin_kupci'),
    path('wms/operacije/kupci/lookup/', views_magacin.magacin_kupci_lookup, name='staff_magacin_kupci_lookup'),
    path('wms/operacije/kupci/sacuvaj/', views_magacin.magacin_kupci_save, name='staff_magacin_kupci_save'),
    path(
            'wms/operacije/kupci/loyalty/',
            views_magacin.magacin_loyalty_telefon,
            name='staff_magacin_loyalty_telefon',
        ),
    path('wms/operacije/narudzbe/stampa/', views_magacin.magacin_narudzbe_stampa, name='staff_magacin_narudzbe_stampa'),
    path(
            'wms/operacije/narudzbe/stampa-kolicine/',
            views_magacin.magacin_narudzbe_stampa_kolicine,
            name='staff_magacin_narudzbe_stampa_kolicine',
        ),
    path('wms/operacije/narudzbe/packing/', views_magacin.magacin_narudzbe_packing, name='staff_magacin_narudzbe_packing'),
    path(
            'wms/operacije/narudzbe/packing/izbor/',
            views_magacin.magacin_narudzbe_packing_izbor,
            name='staff_magacin_narudzbe_packing_izbor',
        ),
    path('wms/operacije/narudzbe/validiraj/', views_magacin.magacin_narudzbe_validiraj, name='staff_magacin_narudzbe_validiraj'),
    path(
            'wms/operacije/narudzbe/odstampano/',
            views_magacin.magacin_narudzbe_mark_printed,
            name='staff_magacin_narudzbe_mark_printed',
        ),
    path('wms/operacije/pakovanje/', views_magacin.magacin_pakovanje, name='staff_magacin_pakovanje'),
    path('wms/operacije/pakuj/', views_magacin.magacin_pakuj, name='staff_magacin_pakuj'),
    path('wms/operacije/pakuj/provjera/', views_magacin.magacin_pakuj_provjera, name='staff_magacin_pakuj_provjera'),
    path('wms/operacije/pakuj/sken/', views_magacin.magacin_pakuj_sken, name='staff_magacin_pakuj_sken'),
    path(
            'wms/operacije/pakuj/<str:broj>/oslobodi/',
            views_magacin.magacin_pakuj_oslobodi,
            name='staff_magacin_pakuj_oslobodi',
        ),
    path(
            'wms/operacije/pakuj/<str:broj>/stampaj-zapakovano/',
            views_magacin.magacin_pakuj_stampaj_zapakovano,
            name='staff_magacin_pakuj_stampaj_zapakovano',
        ),
    path(
            'wms/operacije/narudzbe/<str:broj>/barkod.png',
            views_magacin.magacin_narudzba_barkod,
            name='staff_magacin_narudzba_barkod',
        ),
    path('wms/operacije/pakuj/<str:broj>/', views_magacin.magacin_pakuj_detail, name='staff_magacin_pakuj_detail'),
    path('wms/uvoz/', views_magacin.magacin_uvoz, name='staff_magacin_uvoz'),
    path('wms/uvoz/novi/', views_magacin.magacin_uvoz_novi, name='staff_magacin_uvoz_novi'),
    path('wms/uvoz/<int:pk>/cijene/', views_magacin.magacin_uvoz_cijene, name='staff_magacin_uvoz_cijene'),
    path('wms/uvoz/<int:pk>/stampa/', views_magacin.magacin_uvoz_stampa, name='staff_magacin_uvoz_stampa'),
    path(
            'wms/uvoz/<int:pk>/popis/stampa/',
            views_magacin.magacin_uvoz_popis_stampa,
            name='staff_magacin_uvoz_popis_stampa',
        ),
    path(
            'wms/uvoz/<int:pk>/popis/',
            views_magacin.magacin_uvoz_popis,
            name='staff_magacin_uvoz_popis',
        ),
    path('wms/uvoz/<int:pk>/', views_magacin.magacin_uvoz_detail, name='staff_magacin_uvoz_detail'),
    path(
            'wms/nivelacije/akcija/nova/',
            views_magacin.magacin_nivelacije_akcija_nova,
            name='staff_magacin_nivelacije_akcija_nova',
        ),
    path(
            'wms/nivelacije/akcija/<int:pk>/stampa/',
            views_magacin.magacin_nivelacije_akcija_stampa,
            name='staff_magacin_nivelacije_akcija_stampa',
        ),
    path(
            'wms/nivelacije/akcija/<int:pk>/etikete/',
            views_magacin.magacin_nivelacije_akcija_etikete,
            name='staff_magacin_nivelacije_akcija_etikete',
        ),
    path(
            'wms/nivelacije/akcija/<int:pk>/',
            views_magacin.magacin_nivelacije_akcija_detail,
            name='staff_magacin_nivelacije_akcija_detail',
        ),
    path(
            'wms/nivelacije/stampa/',
            views_magacin.magacin_nivelacije_stampa,
            name='staff_magacin_nivelacije_stampa',
        ),
    path(
            'wms/nivelacije/izmjenjene/<int:pk>/',
            views_magacin.magacin_nivelacije_uvoz,
            name='staff_magacin_nivelacije_uvoz',
        ),
    path('wms/nivelacije/', views_magacin.magacin_nivelacije, name='staff_magacin_nivelacije'),
    path('wms/operacije/dobavljaci/', views_magacin.magacin_dobavljaci, name='staff_magacin_dobavljaci'),
    path('wms/kreiraj-ponudu/', views_magacin.magacin_ponude, name='staff_magacin_ponude'),
    path('wms/kreiraj-ponudu/<int:pk>/', views_magacin.magacin_ponuda_detail, name='staff_magacin_ponuda_detail'),
    path(
            'wms/kreiraj-ponudu/<int:pk>/prihvati/',
            views_magacin.magacin_ponuda_prihvati,
            name='staff_magacin_ponuda_prihvati',
        ),
])
