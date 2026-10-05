# Monri kartično plaćanje

Checkout podržava pouzeće i Monri hosted form (Visa/Mastercard). Kartična opcija je uvijek vidljiva u checkoutu; naplata je dostupna tek uz
potpunu konfiguraciju. Bez konfiguracije checkout prikazuje poruku i odbija
kartično slanje prije kreiranja narudžbe, dok pouzeće ostaje dostupno. Default okruženje je TEST; produkcija se eksplicitno uključuje sa MONRI_ENVIRONMENT=production.
Broj kartice/CVV unose se kod Monrija; aplikacija ih ne prikuplja niti čuva.

## Konfiguracija servera

Primijeniti migration `0305_card_payment` putem standardnog deploymenta i
postaviti sljedeće environment varijable:

```
MONRI_ENABLED=True
MONRI_ENVIRONMENT=test
MONRI_MERCHANT_KEY=<tajni ključ iz Monri merchant profila>
MONRI_AUTHENTICITY_TOKEN=<token iz Monri merchant profila>
MONRI_PUBLIC_BASE_URL=https://<javni HTTPS domen testnog webshopa>
```

Credentials se čitaju isključivo iz process environmenta (Render), bez .env fallbacka.
Tajne se ne upisuju u git niti šalju u chat. Public base URL mora odgovarati
javnom domenu servera i nema path/query. U Monri testnom merchant profilu podesiti
callback URL `https://<domen>/placanje/monri/potvrda/` i uključiti redirect to
success URL. Form šalje success/cancel URL za konkretnu narudžbu.
Testni endpoint: `https://ipgtest.monri.com/v2/form`.
Produkcijski endpoint: `https://ipg.monri.com/v2/form`, potvrđen službenom
Redirect Form dokumentacijom, odjeljak 2.1. `MONRI_ENVIRONMENT` ima default
`test`; `production` bira produkcijski endpoint, nepoznate vrijednosti blokiraju
plaćanje. Credentials moraju odgovarati izabranom okruženju. Payment zapis mora
imati isto okruženje kao aktivna konfiguracija i za formu i za callback.
Produkcija zahtijeva javni base URL `https://carpologijabh.ba`.
`MONRI_ENABLED` ima default True, ali za rad su oba ključa i validan HTTPS URL
obavezni. Eksplicitni `MONRI_ENABLED=False` i dalje isključuje naplatu.
Monri flags mogu koristiti .env fallback; credentials nemaju fallback.
Ostala webshop konfiguracija nije mijenjana.
Za produkciju na Renderu postaviti `MONRI_ENVIRONMENT=production`; postojeće
produkcijske credentials zadržati. Ako postoje overrideovi, postaviti
`MONRI_ENABLED=True` i `MONRI_PUBLIC_BASE_URL=https://carpologijabh.ba`.
U produkcijskom merchant profilu omogućiti success/cancel i callback.
Stvarne payment requestove razvojni testovi ne šalju.
Ako su na Renderu samo oba testna ključa, default ostaje test.
Ako postoje stariji overrideovi, postaviti `MONRI_ENABLED=True`,
`MONRI_ENVIRONMENT=test`, `MONRI_PUBLIC_BASE_URL=https://carpologijabh.ba`.
Callback u Monri merchant profilu: `https://carpologijabh.ba/placanje/monri/potvrda/`.
Monri mora imati uključen redirect to success URL. Odbijene transakcije ostaju na
Monri formi; samo potpisani approved purchase callback označava uplatu.

Poruka „Kartično plaćanje trenutno nije dostupno. Odaberite plaćanje pouzećem.”
nastaje u `CheckoutForm.clean_payment_method` kada `configured()` nije prošao:
flag isključen, nedostaje ključ/token, mode nije test/production ili javni HTTPS URL nije
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

## Redirect Form audit (2026-10-05)

Izvori: [službeni endpoint manual](https://ipgtest.monri.com/en/documentation/v2_form)
i [Redirect Form dokumentacija](https://docs.monri.com/docs/en/form-redirect),
posebno 2.4 (digest), 2.6 (callback/return) i 3.6 (URL overrides).
Quickstart primjer koristi `cancel_url`/`callback_url`, ali detaljna tabela 3.6
i endpoint manual zahtijevaju `cancel_url_override`/`callback_url_override`.
Adapter je ispravljen prema tim tabelama. To je greška u povratnim URL poljima;
nije dokaz da je ona uzrok Monrijevog odbijanja authenticity tokena.

Potpis je obični SHA-512, ne HMAC: UTF-8 bajtovi konkatenacije
`MONRI_MERCHANT_KEY + order_number + amount + currency`, bez separatora,
bez newlinea i bez authenticity tokena. Rezultat je lowercase hex (128 znakova).
Manualova tabela navodi dužinu digest-a 40, ali njegov algoritam i puni testni
vektor daju 128; koristi se algoritam i testni vektor. Ključ se obrađuje na
serveru i nikada se ne šalje formom. Token se šalje kao `authenticity_token`,
ne koristi se kao ključ. Vrijednosti credentials se ne mijenjaju niti trimuju.

| Polje | Vrijednost / izvor |
| --- | --- |
| authenticity_token | MONRI_AUTHENTICITY_TOKEN iz environmenta |
| order_number | Order.broj, uključujući vodeće nule |
| amount | CardPayment.amount kao integer string u feningama; 13.00 BAM = 1300 |
| currency | CardPayment.currency, checkout postavlja BAM |
| digest | SHA-512 nad tačnim stringovima iz istog fields objekta |
| ch_full_name / ch_email | Order.ime_prezime / email |
| ch_address / ch_city / ch_zip | Order.adresa / grad / postanski_broj |
| ch_country / ch_phone | BA / Order.telefon |
| order_info | Carpologija narudžba + Order.broj |
| transaction_type / language | purchase / hr |
| success_url_override | HTTPS javni domen + monri_return za payment UUID |
| cancel_url_override | HTTPS javni domen + monri_cancel za payment UUID |
| callback_url_override | HTTPS javni domen + /placanje/monri/potvrda/ |

Iznos nastaje iz postojećeg serverom izračunatog Order.ukupno * 100; nema
float pretvaranja ili decimalnog separatora u requestu. HTML autoescaping
ne mijenja DOM vrijednost; test provjerava renderovani HTML i URL-encoded POST
roundtrip, token, vodeće nule i UTF-8. Nema browser JavaScript izmjena potpisanih
polja. Test službenog digest vektora prolazi. Token nije dio digest inputa.

Buyer podaci se prosljeđuju bez promjena webshop podataka. Manual navodi limite
name/city/phone 3–30, address/email 3–100, zip 3–9; checkout dozvoljava i prazan
poštanski broj. Zato nije garantovano da svaki postojeći profil zadovoljava sve
Monri buyer limite. Ti podaci nisu dio digesta i ne objašnjavaju prijavljenu
token grešku. Za kontrolni test koristiti kupca sa popunjenim poštanskim brojem
i podacima unutar tih limita.

Success/cancel samo prikazuju status; ne koriste neprovjereni browser redirect
za potvrdu uplate. Jedino potpisani JSON callback (`WP3-callback` + SHA-512 nad
ključem i originalnim bodyjem), uz provjeru iznosa/valute/order numbera i
approved purchase/0000, označava plaćanje. Odbijena transakcija ostaje kod Monrija.
U merchant profilu moraju biti omogućeni i podešeni success/cancel endpointi
da bi overrides radili, prema dokumentaciji 3.6.

`MONRI_FORM_REQUEST` INFO log za test i production koristi zaseban console logger.
Loguje samo endpoint, order_number, amount, currency, nazive polja,
dužine credentials, naziv algoritma i encoding. Nema credentials vrijednosti,
digesta, buyer podataka, kartičnih podataka ili payment UUID/return URLova.
Blokirani requesti ne emituju taj log; environment je dio sigurnog loga. Credentials na Renderu
nisu dostupni lokalnom auditu; nije utvrđen uzrok njihove identifikacije kao
invalid token na Monri strani. Nakon deploymenta uporediti sigurni log za
konkretnu testnu narudžbu sa browser requestom lokalno, bez dijeljenja tokena,
ključa ili punog payload-a. Ako Monri i dalje odbija token uz identične vrijednosti,
Monri podršci dostaviti order_number, vrijeme, endpoint i redigovanu grešku.
Credentials ne mijenjati na osnovu ovog audita.
