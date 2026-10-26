# Prijava iz glavnog Django sajta

Ključ je generisan u `.secrets/admin_sso_key` ovog projekta. Kopirajte njegov sadržaj u serversku varijablu `SHOP_ADMIN_SSO_KEY` na glavnom sajtu. Ključ ne unosite u template, JavaScript ili Git. Isti ključ na ovom sajtu možete postaviti varijablom `ADMIN_SSO_KEY`.

Ovaj sajt otvara admin kao `Bera1990`. Za drugog postojećeg aktivnog superusera postavite `ADMIN_SSO_USERNAME` na ovom serveru.

Na glavnom sajtu dodajte u settings.py:

```python
import os
SHOP_ADMIN_SSO_KEY = os.environ['SHOP_ADMIN_SSO_KEY']
SHOP_ADMIN_SSO_URL = 'https://DOMENA-OVOG-SHOPA/admin/sso/'
```

Kopirajte view iz `main_site_admin_sso.py` u aplikaciju glavnog sajta i povežite URL:

```python
path('otvori-shop-admin/', open_shop_admin, name='open_shop_admin'),
```

U njegov administratorski panel dodajte:

```html
<form method="post" action="{% url 'open_shop_admin' %}">
  {% csrf_token %}
  <button type="submit">Otvori shop admin</button>
</form>
```

Samo aktivni superuser glavnog sajta može izdati token. Token vrijedi 60 sekundi, koristi se jednom i šalje POST zahtjevom. Odredišni sajt otvara isključivo svoj `/admin/`. Postojeća prijava lozinkom ostaje dostupna.

Na produkciji oba sajta koristite HTTPS, `DEBUG = False`, odgovarajući `ALLOWED_HOSTS`, `SESSION_COOKIE_SECURE = True` i `CSRF_COOKIE_SECURE = True`. Za isključivanje SSO-a uklonite lokalni ključ i serversku varijablu `ADMIN_SSO_KEY`; za rotaciju zamijenite ključ na oba servera.
