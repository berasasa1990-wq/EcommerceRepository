# Mungos category audit — lokalna SQLite baza

Ovo nije produkcijski audit. Traženi Product ID-jevi nisu prisutni. Webshop podaci nisu mijenjani.

READY_BY_CATEGORY označava samo kategorijsku pokrivenost; READY_PAYLOAD uključuje ostale preview validacije i nije potvrda bulk remote-state guardova.

| ID | Naziv | Parent ID / naziv | Proizvodi (direktno) | Prethodni mapping | Predloženi / trenutni categoryCode | Izvor mappinga ID |
|---|---|---|---:|---|---|---|
| 7 | Udice za ribolov | None / — | 0 | — | SportRecreation_Equipment_FishingEquipment_Hooks | 7 |
| 8 | Udice za more | 7 / Udice za ribolov | 4 | — | SportRecreation_Equipment_FishingEquipment_Hooks | 7 |
| 9 | Garderoba | None / — | 22 | — | SportRecreation_Equipment_FishingEquipment_FishingWear | 9 |
| 10 | Kačketi | None / — | 9 | — | SportRecreation_Equipment_FishingEquipment_FishingWear | 10 |
| 11 | Trokuke | None / — | 7 | — | SportRecreation_Equipment_FishingEquipment_Hooks | 11 |
| 12 | Vezane udice | 7 / Udice za ribolov | 8 | — | SportRecreation_Equipment_FishingEquipment_Hooks | 7 |
| 13 | Udice za bjelu ribu | 7 / Udice za ribolov | 41 | — | SportRecreation_Equipment_FishingEquipment_Hooks | 7 |
| 14 | Feeder udice | 7 / Udice za ribolov | 57 | — | SportRecreation_Equipment_FishingEquipment_Hooks | 7 |
| 15 | Udice za travu | 7 / Udice za ribolov | 6 | — | SportRecreation_Equipment_FishingEquipment_Hooks | 7 |
| 16 | Udice za soma | 7 / Udice za ribolov | 0 | — | SportRecreation_Equipment_FishingEquipment_Hooks | 7 |
| 17 | Udice dugi vrat | 7 / Udice za ribolov | 1 | — | SportRecreation_Equipment_FishingEquipment_Hooks | 7 |
| 18 | Virble i kopče | None / — | 15 | — | SportRecreation_Equipment_FishingEquipment_Accessories | 18 |
| 19 | Šaranske udice | 7 / Udice za ribolov | 1 | — | SportRecreation_Equipment_FishingEquipment_Hooks | 7 |
| 20 | Žute trokuke | 11 / Trokuke | 0 | — | SportRecreation_Equipment_FishingEquipment_Hooks | 11 |
| 21 | Crvene trokuke | 11 / Trokuke | 11 | — | SportRecreation_Equipment_FishingEquipment_Hooks | 11 |
| 22 | Crne trokuke | 11 / Trokuke | 17 | — | SportRecreation_Equipment_FishingEquipment_Hooks | 11 |
| 23 | Bronzane trokuke | 11 / Trokuke | 5 | — | SportRecreation_Equipment_FishingEquipment_Hooks | 11 |
| 24 | Feeder stapovi | None / — | 4 | — | SportRecreation_Equipment_FishingEquipment_FishingRods | 24 |
| 25 | saranski stapovi | None / — | 2 | — | SportRecreation_Equipment_FishingEquipment_FishingRods | 25 |
| 26 | Kape i kacketi TEST | 24 / Feeder stapovi | 0 | — | SportRecreation_Equipment_FishingEquipment_FishingWear | 26 |
| 27 | Stalci za spod TEST | 24 / Feeder stapovi | 0 | — | SportRecreation_Equipment_FishingEquipment_Accessories | 27 |
| 28 | Stapovi za spod TEST | 24 / Feeder stapovi | 0 | — | SportRecreation_Equipment_FishingEquipment_FishingRods | 28 |
| 29 | Stalci za spod PROD | 24 / Feeder stapovi | 3 | — | SportRecreation_Equipment_FishingEquipment_Accessories | 29 |
| 30 | Stapovi spod PROD | 24 / Feeder stapovi | 2 | — | SportRecreation_Equipment_FishingEquipment_FishingRods | 30 |
| 31 | Varalice more TEST | 24 / Feeder stapovi | 3 | — | SportRecreation_Equipment_FishingEquipment_Lures | 31 |
| 32 | Varalice single TEST | 24 / Feeder stapovi | 2 | — | SportRecreation_Equipment_FishingEquipment_Lures | 32 |

## Rezultati

- TOTAL_CATEGORIES: 26
- MAPPED_CATEGORIES: 26
- UNMAPPED_CATEGORIES: 0
- TOTAL_PRODUCTS: 5844
- PRODUCTS_READY_BY_CATEGORY: 220
- PRODUCTS_BLOCKED_BY_CATEGORY: 5624
- PRODUCTS_READY_PAYLOAD: 180
- PRODUCTS_OTHER_PAYLOAD_REVIEW: 5664
- PRODUCTS_WITHOUT_CATEGORY: 5624

Nejasne lokalne kategorije: nema. Kape, stalci i varalice ispod štapova koriste direktan mapping prema vrsti opreme; roditelji se ne mijenjaju.

Proizvodi 14, 20, 22, 23, 34, 35, 36, 37, 38: nisu prisutni u lokalnoj bazi. Njihove stvarne kategorije i uzrok na produkciji ostaju nepotvrđeni.

Pokretanje nad produkcijskim podacima: `python manage.py mungos_category_audit`. Command radi samo SELECT i nema Mungos HTTP klijenta.

## Produkcijska mapping pravila — 2026-10-02

Dodano je šest pozitivnih ID mappinga: 143 → Hooks, 157 → Feeders,
162 → Accessories, 167 → Accessories, 173 → GroundbaitsBaits,
181 → FishingWear. ID 191 (Kamp) ima eksplicitnu NEEDS_REVIEW barijeru.
ID pravila zahtijevaju i potvrđeni naziv; podkategorije nasljeđuju roditelja
osim kada imaju vlastiti explicit override.
Svi kodovi koriste prefiks `SportRecreation_Equipment_FishingEquipment_`.

Novi explicit override mappingi:
- Plovci za ribolov → FloatsBobbers.
- Vrtilice i Kopče, Igle i Alati, Ribolovni sistemi, Krimp i Split Ring,
  Olovo za ribolov, Sajlice za Grabljivice, Stoperi, Rakete i kobre → Accessories.

NEEDS_REVIEW barijere: Naočare, Kamp, Šatori, Stolice, Upaljaci, Lampe,
Noževi, Vreće za spavanje, Kuhinja i kamp program, Baterije, Suncobrani,
Kreveti, Stolići. Proizvodi bez kategorije ostaju NEEDS_REVIEW bez pogađanja iz naziva.
Raniji potvrđeni mappingi ostaju u `EXPLICIT_CATEGORY_MAPPING`.

Lokalni audit ponovljen uz blokiran HTTP, SELECT-only guard i SQLite mode=ro.
Rezultati su isti: 26/26 lokalnih kategorija mapirano; 220 proizvoda pokriveno
kategorijom, 5624 bez kategorije ostaju blokirana; 180 READY_FOR_REVIEW payloadova.
Produkcijski parent ID-jevi nisu prisutni u lokalnoj bazi; produkcijska pravila
provjerena su testovima bez izmjena webshop podataka.

Dry-run artefakti:
- `mungos_product_dry_run.local.json`: Accessories, READY_FOR_REVIEW.
- `mungos_product_dry_run.no_category.local.json`: null categoryCode, NEEDS_REVIEW.

Svih 125 testova iz svih `tests_mungos*.py` modula je prošlo.
Nije izvršen HTTP niti push; Category/Product baza nije mijenjana.
