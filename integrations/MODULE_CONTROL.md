> Naslijeđeni režim. Za novi potpisani sistem i postepeni prelazak pogledati
> [BERA_LICENSING.md](BERA_LICENSING.md). Kada se uključi enforcement za Loyalty,
> ovaj API vraća 410 i SiteModule više ne određuje prava.

# Uključivanje i isključivanje Loyalty modula

Na ovom sajtu: `/admin/shop/sitemodule/` prikazuje prekidač Loyalty i datum isteka.
Modul je trenutno uključen. Prazan datum znači da nema automatskog isteka.
Kada istekne ili se isključi, nestaju kartica, dugme u korpi i Loyalty u panelu;
server prestaje obračunavati popust i pri direktnom slanju checkouta.
Kartice, rasponi i ranije narudžbe se čuvaju za ponovno uključivanje.

Ključ za kontrolu modula je u `.secrets/module_control_key`. Odvojen je od ključa
za administratorsku prijavu. Kopirajte ga samo u serversku konfiguraciju glavnog sajta.

Glavni Django sajt, settings.py:

```python
import os
SHOP_MODULE_CONTROL_KEY = os.environ['SHOP_MODULE_CONTROL_KEY']
SHOP_MODULE_CONTROL_URL = 'https://DOMENA-SHOPA/api/modules/loyalty/'
```

Kopirajte funkciju iz `module_control.py` u glavni sajt. Nakon provjerene uplate:

```python
set_loyalty_module(enabled=True, revision=payment_version, paid_until=subscription.ends_at)
```

Kada pretplata prestane ili treba ugasiti pristup:

```python
set_loyalty_module(enabled=False, revision=payment_version)
```

`payment_version` je rastući cijeli broj za ovaj shop, sačuvan u bazi glavnog sajta
uz promjenu stanja pretplate. Pri ponavljanju istog događaja koristite istu verziju i
iste podatke. Starije verzije dobijaju HTTP 409 i ne mijenjaju modul. Ponovite zahtjev
u pozadinskom poslu ako mreža zakaže. Datum isteka automatski gasi Loyalty i bez
novog zahtjeva glavnog sajta. Novi događaj ponovne uplate mora imati višu verziju.

API prihvata samo potpisani POST token koji vrijedi 60 sekundi. Uključivanje se
ne poziva iz javnog JavaScripta. U produkciji koristite HTTPS.

Ovdje je pripremljen prijem i primjer poziva. Povezivanje s konkretnim naplatnim
sistemom glavnog sajta treba napraviti u njegovom provjerenom payment webhooku.
