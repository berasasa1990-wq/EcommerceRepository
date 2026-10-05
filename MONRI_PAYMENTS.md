# Monri kartično plaćanje

Checkout podržava pouzeće i Monri hosted form (Visa/Mastercard). Kartična opcija je uvijek vidljiva u checkoutu; naplata je dostupna tek uz
potpunu konfiguraciju. Bez konfiguracije checkout prikazuje poruku i odbija
kartično slanje prije kreiranja narudžbe, dok pouzeće ostaje dostupno. Integracija je isključivo TEST; produkcijska naplata je blokirana.
Broj kartice/CVV unose se kod Monrija; aplikacija ih ne prikuplja niti čuva.

## Konfiguracija testnog servera

Primijeniti migration `0305_card_payment` putem standardnog deploymenta i
postaviti sljedeće environment varijable:

```
MONRI_ENABLED=True
MONRI_ENVIRONMENT=test
MONRI_MERCHANT_KEY=<tajni ključ iz Monri merchant profila>
MONRI_AUTHENTICITY_TOKEN=<token iz Monri merchant profila>
MONRI_PUBLIC_BASE_URL=https://<javni HTTPS domen testnog webshopa>
```

Tajne se ne upisuju u git niti šalju u chat. Public base URL mora odgovarati
javnom domenu servera i nema path/query. U Monri testnom merchant profilu podesiti
callback URL `https://<domen>/placanje/monri/potvrda/` i uključiti redirect to
success URL. Form šalje success/cancel URL za konkretnu narudžbu.
Testni endpoint: `https://ipgtest.monri.com/v2/form`.
Produkcijski endpoint nije dostupan u ovoj implementaciji. `MONRI_ENVIRONMENT`
ima default `test`; vrijednost `production` ili druga nepoznata vrijednost blokira
kartično plaćanje. Koristiti isključivo ključeve testnog merchant profila.
`MONRI_ENABLED` ima default True, ali za rad su oba ključa i validan HTTPS URL
obavezni. Eksplicitni `MONRI_ENABLED=False` i dalje isključuje naplatu.
Render environment varijable imaju prednost nad lokalnim `.env` vrijednostima
isključivo za Monri konfiguraciju; ostala webshop konfiguracija nije mijenjana.
Ako su na Renderu samo oba testna ključa, dodatne varijable nisu obavezne.
Ako postoje stariji overrideovi, postaviti `MONRI_ENABLED=True`,
`MONRI_ENVIRONMENT=test`, `MONRI_PUBLIC_BASE_URL=https://carpologijabh.ba`.
Callback u Monri merchant profilu: `https://carpologijabh.ba/placanje/monri/potvrda/`.
Monri mora imati uključen redirect to success URL. Odbijene transakcije ostaju na
Monri formi; samo potpisani approved purchase callback označava uplatu.

Poruka „Kartično plaćanje trenutno nije dostupno. Odaberite plaćanje pouzećem.”
nastaje u `CheckoutForm.clean_payment_method` kada `configured()` nije prošao:
flag isključen, nedostaje ključ/token, mode nije test ili javni HTTPS URL nije
validan. Stari default `MONRI_ENABLED=False` blokirao je instalacije sa samo dva
ključa. Vrijednosti ključeva se ne loguju niti prikazuju pri dijagnostici.

## Ponašanje

Iznos se uzima iz serverom izračunatog Order.ukupno, u BAM feningama. Narudžba
čeka uplatu, lager je rezervisan postojeći checkout flowom. Callback se provjerava
SHA-512 potpisom nad originalnim bodyjem, uz provjeru order number, iznosa, BAM,
okruženja, purchase, approved/0000. Ponovljeni isti callback ne naplaćuje niti
finalizuje narudžbu ponovo. Drugi transaction ID za plaćenu narudžbu vraća 409.
Browser success/cancel URL nikad sam ne mijenja status uplate.

Kartični checkout šalje email, loyalty/Odoo sync tek nakon potvrđene uplate.
Validacija/pakovanje i slanje kuriru blokirani su dok uplata nije potvrđena ili
ako se iznos narudžbe razlikuje od uplate. Plaćena kartična narudžba nema COD
naplatu. Ručne kartične narudžbe zadržavaju dosadašnje ponašanje.

Odustajanje od Monri forme ne otkazuje narudžbu niti automatski pušta rezervacije
(jer browser redirect nije pouzdan dokaz otkazivanja). Kupac može nastaviti
plaćanje istog order numbera. Neplaćene narudžbe otkazuju se postojećim staff flowom.
Refund, djelimični refund i automatsko isticanje neplaćenih narudžbi nisu uključeni;
promjene plaćene narudžbe koje mijenjaju iznos zahtijevaju provjeru u Monri portalu.

Dokumentacija: https://docs.monri.com/docs/en/form-redirect
Razvojna provjera: izolovana test baza i mockovi; nema stvarnih transakcija.
