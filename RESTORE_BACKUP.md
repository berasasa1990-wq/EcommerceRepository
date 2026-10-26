# Restore Backup

Kada korisnik napiše „Restore Backup“, vrati projekt na stanje sačuvano 26.10.2026. prije eksperimentalnih izmjena.

Arhiva: `backups/restore-point-2026-10-26/project.tar.gz`
Manifest: `backups/restore-point-2026-10-26/manifest.json`

Arhiva uključuje trenutne datoteke projekta, postojeće necommitovane izmjene, konfiguraciju, SQLite bazu i media datoteke. Izuzeti su `.git`, `venv`, `.venv`, `backups`, `__pycache__` i `.pytest_cache`. Git historiju i virtualno okruženje ne vraćati iz ove arhive.

Prije vraćanja provjeriti arhivu i sačuvati zasebnu kopiju tadašnjeg stanja. Vratiti arhivirane datoteke i ukloniti naknadno dodane datoteke projektnog koda koje nisu u manifestu. Sačuvati backup arhive i ovu uputu. Ne mijenjati datoteke izvan ovog projekta. Vraćanje baze i media datoteka radi samo ako korisnik zatraži i povrat podataka; uobičajeni „Restore Backup“ odnosi se na kompletan kod i konfiguraciju projekta.
