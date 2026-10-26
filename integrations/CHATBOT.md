# ChatBot

Podešavanje: `/panel/chatbot/` (samo osoblje koje ima pristup panelu).

Chatbot je na početnoj stranici u donjem desnom uglu. Po defaultu se animirano otvara nakon 5 sekundi. Nakon prvog otvaranja se u istom tabu ne otvara ponovo automatski; dugme za ručno otvaranje ostaje dostupno. Na mobitelu je iznad donje navigacije. Poštuje `prefers-reduced-motion`; automatsko otvaranje ne preuzima fokus tastature.

## Vođeni odabir artikala (podrazumijevani način)

Radi bez API ključa, potpuno preko baze webshopa. U `/panel/chatbot/` uključite „Vođeni odabir artikala“ i uredite:

- Pitanje za kategoriju (npr. „Koji uređaj želite kupiti?“).
- Pitanje za maksimalni budžet u KM.
- Ponuđene budžete, odvojene zarezom (npr. `300, 500, 1000, 2000`). Kupac može upisati i bilo koji drugi iznos.
- Poruku rezultata i poruku kada nema artikala; dostupne oznake su `{category}`, `{budget}`, `{count}`.

Kategorije se automatski preuzimaju iz sajta, bez ručnog unošenja liste u chatbot. Nakon izbora kategorije i budžeta prikazuju se samo aktivni artikli koji nisu označeni „Nije na stanju“, s cijenom manjom ili jednakom budžetu. Rezultati su sortirani od niže cijene, po šest artikala, sa slikom, nazivom, cijenom i linkom. Budžet je cijena jednog artikla bez dostave. Ako nema odgovarajućih artikala, bot ne izmišlja preporuke i ne prekoračuje budžet.

Kupac može promijeniti kategoriju, budžet, prikazati još artikala ili započeti novi razgovor. Odabir je u njegovoj Django sesiji i nije vidljiv drugim posjetiocima. Ovaj način nema vanjske AI pozive i ne troši AI kvotu. To je vodič kategorija → budžet, a ne proizvoljni vizuelni editor grananja.

Za slobodni AI razgovor isključite vođeni odabir; prethodno spremljeni API ključ i AI postavke ostaju sačuvani.

## AI povezivanje

U panelu unesite OpenAI API ključ, ostavite model `gpt-4.1-mini` ili unesite drugi podržani tekstualni model dostupan vašem projektu, i spremite. Ključ se šifrira postojećim Django SECRET_KEY i ne šalje u HTML. Prazno polje zadržava ključ; označeni checkbox ga uklanja. Alternativno postavite serverski `OPENAI_API_KEY`. Uklanjanje lokalnog ključa ne uklanja eventualni serverski ključ. Promjena SECRET_KEY zahtijeva ponovni unos spremljenog ključa.

Slobodni AI način koristi [Responses API](https://developers.openai.com/api/docs/guides/text), fiksni HTTPS endpoint i `store: false`. Ne koristi ChatGPT prijavu. API potrošnja pripada vlasniku unesenog ključa. Bez ključa se prikazuje pozdrav i widget, ali poruka korisniku po slanju jasno kaže da asistent nije dostupan; nema simuliranih AI odgovora. Stvarni API poziv nije potvrđen bez važećeg ključa.

Upute i „Informacije o trgovini“ popunite provjerenim podacima: plaćanje, radno vrijeme, dostava, povrati i kontakt. Upute asistenta traže da ne izmišlja podatke. Asistent dobija i javne odgovarajuće artikle i postavke dostave; nema pristup privatnim narudžbama niti funkcije za naručivanje, mijenjanje korpe ili drugih podataka. Prijedlozi proizvoda odvojeno prikazuju stvarne nazive, cijene i linkove iz baze; AI tekst se prikazuje kao običan tekst.

## Ograničenja i privatnost

- CSRF i POST zaštita poruka; do 1000 znakova po poruci.
- Posljednjih deset poruka drži se u Django sesiji. „Novi razgovor“ ih uklanja, a ne resetuje limit potrošnje. Drugi posjetioci ne dobijaju tu historiju.
- Server šalje tekst razgovora i javni kontekst OpenAI servisu, bez tajnog ključa u promptu. Poruke ne šaljite s lozinkama ili podacima kartice.
- Do 600 izlaznih tokena po odgovoru; timeout API poziva 25 sekundi. Nema automatskog ponavljanja naplativih zahtjeva.
- Dnevni limit cijelog sajta je podesiv (po defaultu 200), uz najviše 6 poruka u minuti po direktnom IP-u i 40 po sesiji/danu. Brojači su u bazi i važe preko više workera/restarta. Dan je UTC. Kod reverse proxyja direktni IP može biti zajednički; forwarding headeri se namjerno ne prihvataju bez zasebno podešenog povjerenja u proxy.
- Brojači nemaju tekst razgovora niti sirovu IP adresu; istekli se čiste pri narednom zahtjevu. Reset razgovora ne poništava brojače.

## Provjera i instalacija

```sh
venv/bin/python manage.py migrate
venv/bin/python manage.py test shop.test_chatbot --noinput
node --check shop/static/chatbot.js
```

Automatski testovi koriste lažni provider, ne troše stvarni API kredit. Pokrivaju HTML bez ključa, šifriranje i očuvanje ključa, izolaciju sesija, limite, CSRF, greške API-ja i strukturu Responses zahtjeva. Za provjeru pravog AI odgovora nakon unosa ključa otvorite početnu i pošaljite poruku.
