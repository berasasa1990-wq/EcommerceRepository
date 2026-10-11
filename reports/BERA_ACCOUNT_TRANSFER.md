# Moj nalog — BERA izgled

Postojeći views `account` i `account_order_detail` koriste `render_storefront` i ranije prenesene predloške `bera_reference/account/index.html`, `account/order_detail.html`, pripadajuće komponente, CSS i JavaScript. Originalni predlošci ostaju sačuvani.

Nisu mijenjani modeli, povezivanje baze, korisnici, šifre, sesije, narudžbe, loyalty, reklamacije, prijava ili odjava. Podaci se i dalje čitaju i čuvaju u istoj konfiguriranoj bazi. Nema novih migracija.

Na odvojenoj sintetičkoj bazi, uz rollback svih testnih izmjena, provjereni su: preusmjeravanje neprijavljenog korisnika, nalog prijavljenog korisnika, njegova narudžba, zabrana pristupa tuđoj narudžbi (404), čuvanje imena i adrese u postojećim User/UserProfile modelima i validacija neispravnog emaila. Django system check prolazi. Nisu slani stvarni emailovi niti pristupano stvarnoj bazi.

Desktop/mobilno poređenje i provjera navigacije po sekcijama: `scripts/bera_account_qa.cjs`, rezultati i snimci u `reports/bera-account-visual/`.
