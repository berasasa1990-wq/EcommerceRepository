# Izgled /admin prema BERA-webshopu

Promijenjen je samo `EcommerceApp/template/admin/base_site.html`: koristi stvarni izvorni BERA predložak, uz statičke resurse kopirane u `bera_reference/`. Prethodni predložak sačuvan je u `EcommerceApp/template/legacy_admin/base_site.html`. Naziv i logo dolaze iz postojećih postavki sajta.

CSS i JavaScript korišten za BERA admin provjeren je bajt po bajt prema izvornom projektu. Registracije u `admin.py`, akcije, URL-ovi, modeli, forme, inline komponente, integracije i dozvole nisu mijenjani. Postojeći prilagođeni predlošci za artikle i postavke ostaju aktivni.

Na sintetičkoj bazi uspješno su provjerene 32 liste modela originalnog admina, pretraga artikala, autocomplete artikla u akcijama, kreiranje/otvaranje/brisanje testnog taga, zabrana pristupa staff korisniku bez dozvola i preusmjeravanje neprijavljenih korisnika. Sve testne izmjene vraćene su rollbackom. Prolaze Django system check i regresije panela (23 liste i forme, CRUD, postavke i dozvole).

Preglednik na 1440 i 390 px: početni admin, artikli, unos artikla, akcije, unos akcije i B2B postavke. Snimci i rezultati su u `reports/bera-admin-visual/`; skripta `scripts/bera_admin_qa.cjs`.

Nema novih migracija. Nisu mijenjani izvorni projekat ni stvarna baza, niti su slani emailovi ili aktivirane vanjske integracije. Provjere pokrivaju navedene putanje; sve produkcijske integracije i sve kombinacije akcija nisu izvršavane.

Otkrivena i ispravljena prethodna B2B JavaScript greška `django is not defined`: B2BSettingsAdmin.Media sada učitava Django jQuery i jquery.init prije postojeće B2B skripte. Poslovna logika B2B-a ostaje ista.
