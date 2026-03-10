# Justice Práskač

Webová aplikace pro rychlý veřejný screening českých firem nad daty z `justice.cz`, Sbírky listin a veřejně dostupných PDF příloh.

Projekt po zadání názvu firmy nebo IČO:

- najde odpovídající subjekt ve veřejném rejstříku,
- stáhne aktuální i úplný výpis,
- projde Sbírku listin,
- vybere relevantní finanční dokumenty z posledních let,
- vytěží z PDF základní finanční metriky,
- dopočítá jednoduché trendy a poměry,
- složí z toho přehledný profil firmy,
- doplní stručné shrnutí a "práskač" signály nad veřejnými podklady.

## Co aplikace umí

- Vyhledávání podle názvu firmy nebo IČO.
- Výběr správného subjektu při více shodách.
- Načtení základního profilu firmy:
  - obchodní firma,
  - IČO,
  - právní forma,
  - datum vzniku,
  - spisová značka,
  - sídlo,
  - vybrané veřejné doplňující údaje.
- Vytěžení vedení, statutárních orgánů a vlastníků z rejstříkového výpisu.
- Analýza historických změn z úplného výpisu:
  - změny názvu,
  - změny sídla,
  - obměny vedení.
- Průchod Sbírky listin a výběr relevantních finančních dokumentů.
- Zpracování více příloh k jedné listině, ne jen prvního PDF.
- Extrakce textu z digitálních PDF přes `pdftotext`.
- OCR fallback přes `tesseract` pro skeny nebo slabě čitelné dokumenty.
- Skládání finanční timeline za poslední roky:
  - tržby,
  - čistý zisk / ztráta,
  - aktiva,
  - vlastní kapitál,
  - závazky,
  - dluh,
  - marže a poměrové ukazatele.
- Zobrazení trendů v grafu a tabulce let.
- AI shrnutí nad veřejnými daty s fallbackem na pravidlový výstup.
- "Práskač" sekce s upozorněními na veřejně viditelné anomálie nebo mezery v datech.
- Sdílená historie již prověřených firem uložená v Turso.
  Záměrná produktová funkce: historie je sdílená napříč uživateli, nejde o omyl ani leak mezi sessions.
- Relevantní zpracované PDF a vybraný extrahovaný text uložené v Cloudflare R2.
- Jednoduché externí porovnání s veřejným snapshotem z Chytrého rejstříku.
- Průběžné streamování stavu zpracování přes SSE, aby frontend ukazoval postup analýzy.

## Jak to funguje

1. Frontend odešle dotaz na `/api/search`.
2. Backend najde kandidáty na `justice.cz`.
3. Po výběru subjektu backend načte:
   - aktuální výpis (`PLATNY`),
   - úplný výpis (`UPLNY`),
   - seznam listin ve Sbírce listin.
4. Backend z listin vybere relevantní finanční dokumenty a jejich PDF přílohy.
5. Každé PDF zkusí přečíst nejdřív digitálně, případně přes OCR.
6. Z textu vytáhne finanční metriky a složí časovou řadu.
7. Nad časovou řadou a rejstříkovými daty vygeneruje shrnutí, deep insights a "práskač" sekci.
8. Profil uloží do Turso a relevantní dokumenty do R2.

## Použitý stack

### Backend

- Python 3
- FastAPI
- Uvicorn
- Requests
- BeautifulSoup (`bs4`)
- Turso / libSQL (`libsql`)
- Anthropic SDK
- Cloudflare R2 přes `boto3`
- `urllib3` retry adaptery pro robustnější HTTP volání

### Zpracování dokumentů

- `pdfinfo` pro počet stran a kontrolu PDF
- `pdftotext` pro textovou extrakci z digitálních PDF
- `tesseract` s jazykem `ces+eng` pro OCR skenů

### Frontend

- Vanilla JavaScript
- HTML
- CSS
- Chart.js pro finanční graf

### Kvalita kódu

- ESLint flat config pro frontendový JavaScript

## Architektura repozitáře

- `server.py` — vstupní bod (entry point), spouští uvicorn
- `justice/` — hlavní Python balíček
  - `app.py` — FastAPI aplikace, endpointy, CORS, statické soubory
  - `ai.py` — Anthropic AI integrace, analýza, shrnutí
  - `db.py` — DAL pro Turso / SQLite fallback, historie, profily, runs, dokumenty
  - `pipeline.py` — sdílený cache/refresh flow pro profily firem
  - `storage_r2.py` — Cloudflare R2 storage layer
  - `documents.py` — zpracování PDF, OCR, parsing listin
  - `extraction.py` — extrakce finančních metrik z textu
  - `scraping.py` — HTTP stahování, parsing HTML z justice.cz
  - `utils.py` — parsování dat, normalizace textu, cachování
- `app.js` — frontend: stav, volání API, SSE stream, rendering
- `index.html` — HTML shell aplikace
- `base.css` — design tokeny a globální styly
- `style.css` — layout a komponentové styly
- `Dockerfile` — Docker image pro deployment na Railway
- `.env.example` — dokumentace všech environment proměnných
- `tests/` — pytest unit testy

## API endpointy

- `GET /api/health`  
  Jednoduchý healthcheck.
- `GET /api/search?q=...`  
  Vyhledání firem podle názvu nebo IČO.
- `GET /api/history`  
  Sdílená historie již prověřených subjektů. Záměrná produktová funkce, ne izolovaná per-user historie.
- `GET /api/company?subjektId=...`  
  Hotový synchronní profil firmy.
- `POST /api/company/ai?subjektId=...`  
  AI-only vylepšení nad už uloženým profilem firmy bez nového načítání Sbírky listin a PDF.
- `GET /api/company/stream?subjektId=...`  
  Streamovaný profil přes `text/event-stream`, vhodný pro průběžný progress v UI.
- `GET /api/document/resolve?detailUrl=...&index=...`  
  Stažení nebo otevření konkrétní PDF přílohy z detailu listiny.

## Spuštění lokálně

### 1. Python závislosti

```bash
pip install -r requirements.txt
```

### 2. Systémové utility

Na stroji musí být dostupné:

```bash
pdfinfo
pdftotext
tesseract
```

Pro OCR je potřeba mít nainstalované jazykové balíčky pro češtinu a angličtinu.

#### macOS (Homebrew)

```bash
brew install poppler tesseract tesseract-lang
```

#### Ubuntu / Debian

```bash
sudo apt-get install poppler-utils tesseract-ocr tesseract-ocr-ces tesseract-ocr-eng
```

### 3. Spuštění

```bash
cd justice-praskac
.venv/bin/uvicorn justice.app:app --host 0.0.0.0 --port 8000 --reload
```

Aplikace běží na `http://localhost:8000` — frontend i API.

Při lokálním spuštění se automaticky načítá kořenový `.env`, pokud proměnné ještě nejsou nastavené v procesu.

## Environment proměnné

Kompletní seznam s výchozími hodnotami viz `.env.example`.

Pro Railway/Turso/R2 jsou prakticky potřeba:

- `DATABASE_URL`
- `TURSO_AUTH_TOKEN`
- `S3_ENDPOINT`
- `S3_BUCKET`
- `S3_ACCESS_KEY_ID`
- `S3_SECRET_ACCESS_KEY`
- `ANTHROPIC_API_KEY`

## Deployment na Railway

1. Repozitář pushni na GitHub.
2. V [Railway](https://railway.com) vytvoř nový projekt a propoj s GitHub repem.
3. Railway automaticky detekuje `Dockerfile` a buildne image.
4. V Railway dashboard nastav environment proměnné z `.env.example`, hlavně Turso a R2 klíče.
5. V Settings → Networking přidej custom doménu (např. `praskac.xyz`).
6. Nastav DNS: CNAME záznam pro doménu směřující na Railway.

Railway automaticky nastaví `PORT` a HTTPS.

## Co uživatel po prověření dostane

- základní firemní profil,
- osoby ve vedení,
- vlastníky a orgány,
- tabulku a graf finančního vývoje,
- přehled relevantních listin a příloh,
- AI shrnutí,
- deep insights,
- "práskač" sekci,
- historické signály změn,
- přímé odkazy na zdroje.

## Omezení

- Jde pouze o screening nad veřejnými daty, ne o due diligence.
- Kvalita výsledku závisí na tom, co je skutečně zveřejněné v `justice.cz`.
- Pokud je listina jen sken, kvalita OCR může být slabší.
- Finanční metriky se odvozují z různě kvalitních PDF a nemusí být vždy úplné.
- Externí porovnání s Chytrým rejstříkem je jen pomocná kontrola, ne autoritativní zdroj.

## Stav projektu

Repozitář má být čistě zdroják. Zdroj pravdy je Turso + R2; lokální disk už nemá sloužit jako persistent storage pro historii ani dokumenty.

## Krátké To-Do

- Pokud se aplikace začne používat ve větším provozu, přidat autentifikaci / autorizaci na API endpointy.
