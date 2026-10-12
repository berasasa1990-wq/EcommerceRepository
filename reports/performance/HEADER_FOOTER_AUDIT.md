# Header/footer i stari GTmetrix izgled — 12.10.2026.

## Zaključak

Na pregledanom javnom HTML-u nisu pronađeni dupli glavni headeri, footeri, ID-ovi ili potpuno ponovljeni vanjski CSS/JS URL-ovi. Lokalni HTTP testovi potvrđuju isto za javne stranice, prijavljeni nalog, checkout s proizvodom, admin i panel. Ovo nije vizuelni/interaktivni test svih uređaja ili svih pojedinačnih admin modela.

GTmetrix screenshot je iz izvještaja generisanog **12:35 po vremenu Sarajeva** (03:35 -0700). Javna datoteka `bera-home.4c727c9900c0.css` trenutno ima `Last-Modified: 12:50:53` po vremenu Sarajeva (10:50:53 GMT). Lokalni git ima commit `0cd38f0b` iz 12:50:02. Tajming potvrđuje da screenshot prethodi trenutnom statičkom artefaktu; najvjerovatnije je testirao raniji build. Sam screenshot ne omogućava dokazivanje tadašnjeg HTML-a, neuspjelog CSS zahtjeva ili cache sadržaja.

Današnji javni HTML sadrži `body.bera-storefront-header`, novi `bera-home-nav`, jedan `header#header` i jedan `footer.bera-footer`. Produkcijski `bera-home.css` je bajt-identičan lokalnoj datoteci (SHA1 `d584c55f14485d3f728ef9bce9ad8b34c196c8bd`). Taj CSS skriva promo traku i staru desktop navigaciju, prikazuje novu navigaciju i mijenja izgled pretrage. Nije potrebno naslijepo brisati cache ili mijenjati template za ovaj stari screenshot; novi test treba pokrenuti na direktnom `https://carpologijabh.ba/` i provjeriti screenshot i neuspjele zahtjeve.

## Putanje template-a

- Početna, korpa, checkout, proizvod, kategorija i brendovi koriste `bera_reference/base.html`. Datoteke kao `template/home.html` su kratki `extends` adapteri, ne drugi paralelno renderovani headeri.
- Prijavljeni nalog koristi `bera_reference/account/index.html` i isti novi base.
- Footer lanac je `base.html → partials/footer.html → partials/bera_footer.html`, koji generiše jedan footer.
- `template/base.html` i stari `template/partials/footer.html` i dalje postoje za druge/legacy putanje. Njihovo postojanje ne znači da se učitavaju uz novi base. `render_storefront` ima ograničeni superuser preview `?izgled=prethodni` za neke druge stranice; glavne navedene stranice izuzete su iz tog preview-a.
- `/admin/` koristi Django `admin/base.html` sa prilagođenim `admin/base_site.html`, a ne webshop base. Lokalno: jedan admin header, bez webshop footera.
- Prijavljeni `/panel/` koristi `bera_reference/staff/panel.html`; base preskače webshop header na `/panel`, prikazuje povratnu navigaciju. Lokalno: bez webshop headera i jedan generisani webshop footer. Zadržane globalne webshop skripte/stilovi/popups predstavljaju kandidat za buduću izolaciju staff layouta, uz provjeru svih panel interakcija.
- Više `<header>` elemenata za naslove sekcija i više navigacija za desktop/mobilni meni nisu sami po sebi dupli glavni header. Stare navigacijske strukture i CSS pravila još postoje, ali ih novi CSS nadjačava. Potencijalna ušteda traži DOM/CSS coverage svih breakpointa i menija prije uklanjanja.

## Javni read-only uzorak

Detalji: `header-footer-public-audit.json`. Svaki red označava jedan GET; mrežna mjerenja su nekontrolisan uzorak s ovog računara, uz do tri paralelna zahtjeva. Vrijeme do zaglavlja uključuje DNS/TLS, mrežu i eventualna preusmjeravanja, **nije izolovano vrijeme Django obrade**.

| Stranica | Odgovor / provjerena stranica | Glavni header/footer | CSS / JS | Vrijeme do zaglavlja |
|---|---|---:|---:|---:|
| Početna | 200 | 1 / 1 | 9 / 13 | 2333 ms |
| Korpa | 200, prazna korpa | 1 / 1 | 15 / 14 | 2719 ms |
| Brendovi | 200 | 1 / 1 | 12 / 10 | 1801 ms |
| Proizvod CL8007 | 200 | 1 / 1 | 20 / 17 | 1593 ms |
| Kategorija Igle i Alati | 200 | 1 / 1 | 13 / 10 | 1406 ms |
| Checkout | Prazna korpa preusmjerava na početnu | 1 / 1 na odredištu | 9 / 13 | 4292 ms uključujući redirect |
| Nalog | Preusmjerava na prijavu | 1 / 1 na prijavi | 15 / 11 | 3082 ms uključujući redirect |
| Panel | Preusmjerava na prijavu | 1 / 1 na prijavi | 15 / 11 | 2954 ms uključujući redirect |
| Admin | Preusmjerava na admin prijavu | 1 / 1 u javnom odgovoru | 9 / 5 | 2752 ms uključujući redirect |

Javni admin/login odgovor nije dokaz layouta prijavljenog admina; prijavljeni admin/panel/nalog i stvarni checkout provjereni su **lokalno sa sintetičkim podacima**. Produkcijske sesije i korisnički podaci nisu korišteni. Prvi pokušaj javnog audita imao je mrežni timeout; ponovljeni završeni audit je zapisani uzorak, ne reprezentativan performance benchmark.

HTML početne u uzorku ima oko 298 KB prije transportne kompresije, kategorija oko 389 KB. Ovo nisu transfer veličine iz GTmetrixa. Na početnoj bundle datoteke zamjenjuju svoje izvorne CSS-ove; ne učitavaju se oba kompleta istovremeno. Ostale stranice imaju više odvojenih CSS datoteka. Nisu dupli URL-ovi, ali broj zahtjeva i slojevita cascade mogu povećati trošak početnog prikaza.

## Cache i stvarni kandidati za usporavanje

- HTML i provjereni hashed CSS imaju `cf-cache-status: DYNAMIC`; za taj uzorak nije poslužen Cloudflare edge HIT. CSS ima `Cache-Control: max-age=315360000, public, immutable`, što omogućava browser cache. `DYNAMIC` nije sam po sebi greška za personalizovani HTML.
- HTML postavlja CSRF cookie i `Vary: Cookie, Accept-Encoding`. Ne uključivati nekontrolisani full-page public cache: korpa/nalog/CSRF moraju ostati izolovani.
- Service worker je samo instalacioni worker, nema fetch handlera ili cache HTML-a. Nije potvrđen kao uzrok starog headera.
- Raniji CPU profil u `optimization-2/browser-*.json` pokazuje trošak geometrijske funkcije `measure` za sticky header, alignment logotipa i Facebook skripte. To je ranije mjerenje, ne novi profil današnje produkcije. `measure` ima jednu inicijalizaciju u novom base-u i više legitimnih okidača (load, resize, ResizeObserver, fonts). Daljnje mijenjanje traži novi trace i provjeru preklapanja/CLS-a.
- Korisnikov novi GTmetrix: LCP 3,7 s i TBT 308 ms, CLS 0. Nema dokaza da su uzrok dupli headeri. Za izolaciju tražiti GTmetrix HAR/trace istog builda, Render vremena obrade i SQL profil stvarnog kataloga. Sam screenshot ne daje vrijeme pojedinačnih resursa ili novi LCP element.

## Testovi i izmjene

Dodati su `EcommerceApp/tests_header_footer_audit.py`, ovaj izvještaj i javni JSON audit. Runtime template, CSS, JS, poslovna logika i migracije nisu mijenjani. Početni `git status` bio je čist; ostale prethodne izmjene nisu vraćane.

Tri regresiona testa prolaze i obuhvataju javne stranice, nalog, checkout s proizvodom, panel i admin. Provjeravaju broj glavnih headera/footera i odsustvo potpuno ponovljenih CSS/JS URL-ova. Ne tvrde da nema preklapajućih CSS pravila ili svih duplih JavaScript event listenera. Browser plugin nije mogao biti inicijalizovan zbog runtime zabrane importa `node:process`; u ovoj provjeri nisu napravljena nova vizuelna browser mjerenja.

```sh
venv/bin/python manage.py test EcommerceApp.tests_header_footer_audit --settings=scripts.bera_home_qa_settings --noinput
```

Nema produkcijskih migracija, deploymenta ili git pusha. Preporučeni redoslijed: novi GTmetrix istog builda i HTTPS URL-a, Render/SQL profil za mrežno čekanje, CPU trace header geometrije i third-party skripti, zatim coverage i ciljano smanjivanje CSS/DOM slojeva bez mijenjanja dizajna.
