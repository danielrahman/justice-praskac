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
- Sdílená historie již prověřených firem uložená v SQLite.
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
8. Profil uloží do JSON cache a do SQLite historie.

## Použitý stack

### Backend

- Python 3
- FastAPI
- Uvicorn
- Requests
- BeautifulSoup (`bs4`)
- SQLite (`sqlite3`)
- Anthropic SDK
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

- `server.py`  
  Hlavní FastAPI backend, scraping, parsing, OCR, finanční extrakce, AI vrstva i API endpointy.
- `app.js`  
  Frontend stav, volání API, SSE stream, render celé aplikace a grafů.
- `index.html`  
  Základní shell aplikace.
- `base.css`  
  Design tokeny a základní globální styly.
- `style.css`  
  Layout a komponentové styly UI.
- `app_state.db`  
  SQLite databáze pro sdílenou historii prověřených firem.
- `cache/`  
  Lokální cache JSON, PDF a extrahovaných textů.

## API endpointy

- `GET /api/health`  
  Jednoduchý healthcheck.
- `GET /api/search?q=...`  
  Vyhledání firem podle názvu nebo IČO.
- `GET /api/history`  
  Sdílená historie již prověřených subjektů.
- `GET /api/company?subjektId=...`  
  Hotový synchronní profil firmy.
- `GET /api/company/stream?subjektId=...`  
  Streamovaný profil přes `text/event-stream`, vhodný pro průběžný progress v UI.
- `GET /api/document/resolve?detailUrl=...&index=...`  
  Stažení nebo otevření konkrétní PDF přílohy z detailu listiny.

## Spuštění lokálně

### 1. Python závislosti

Projekt nemá zamčený `requirements.txt`, ale podle importů potřebuje minimálně:

```bash
pip install fastapi uvicorn requests beautifulsoup4 anthropic urllib3
```

### 2. Systémové utility

Na stroji musí být dostupné:

```bash
pdfinfo
pdftotext
tesseract
```

Pro OCR je potřeba mít nainstalované jazykové balíčky pro češtinu a angličtinu.

### 3. Spuštění backendu

```bash
cd /Users/danielrahman/Desktop/justice-praskac
python server.py
```

Nebo:

```bash
uvicorn server:app --host 0.0.0.0 --port 8000 --reload
```

### 4. Spuštění frontendu

Frontend je statický. Může běžet z jednoduchého static serveru.

Například:

```bash
cd /Users/danielrahman/Desktop/justice-praskac
python -m http.server 3000
```

Pak otevři:

- frontend: `http://localhost:3000`
- backend API: `http://localhost:8000`

Poznámka: v aktuální verzi je base URL API zadané přímo v `app.js`, takže pro lokální běh může být potřeba upravit `const API` na správnou adresu backendu.

Poznámka: backend má v `server.py` aktuálně natvrdo zadané cesty:

- `CACHE_DIR = /home/user/workspace/justice-praskac/cache`
- `DB_PATH = /home/user/workspace/justice-praskac/app_state.db`

Pokud projekt spouštíš jinde, uprav tyto cesty nebo si připrav odpovídající adresářovou strukturu.

## Environment proměnné

Backend používá tyto proměnné:

- `JUSTICE_PROFILE_CACHE_TTL_SECONDS`  
  TTL JSON cache profilů. Výchozí hodnota jsou 3 dny.
- `JUSTICE_AI_MODEL`  
  Název modelu pro AI shrnutí. Výchozí: `claude_sonnet_4_5`.
- `JUSTICE_ENABLE_AI`  
  `1` nebo `0` pro zapnutí / vypnutí AI vrstvy.
- `JUSTICE_AI_TIMEOUT_SECONDS`  
  Timeout pro volání AI.

Pro Anthropic SDK je v praxi potřeba také standardní autentizační proměnná:

- `ANTHROPIC_API_KEY`

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

Repozitář aktuálně obsahuje i pracovní cache, SQLite databázi a logy, takže slouží nejen jako zdroják, ale i jako snapshot vývojového a datového stavu projektu.
