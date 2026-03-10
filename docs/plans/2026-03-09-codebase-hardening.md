# Justice Práskač — Codebase Hardening Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Fix all Critical, Important, and Minor issues identified in the full codebase review — security, architecture, testing, observability, and cleanup.

**Architecture:** Work in phases — security-critical fixes first, then infrastructure (logging, deps, tests), then refactoring (module extraction, dedup), then polish (minor fixes). Each phase is independent after Phase 1.

**Tech Stack:** Python 3 / FastAPI / SQLite / Vanilla JS / ESLint

---

## Phase 1: Security & Data Hygiene (Critical)

### Task 1: Fix .gitignore and clean tracked files

> **Before starting, ask the user:**
> 1. Do you want to rewrite git history to remove the large cached files from all commits (using `git filter-repo`), or just stop tracking them going forward? History rewrite is destructive but shrinks the repo from ~217MB.
> 2. Should `app_state.db` be gitignored, or do you want to keep a seed/empty version committed?
> 3. Are there any files in `cache/` that should be preserved outside git (backed up somewhere)?
> 4. Should the patch/update scripts (`patch_robustness_redesign.py`, `update_batch2.py`, `update_batch3.py`) and test data files (`test_company_skate.json`, `test_stream_skate*.txt`, `sample_profile.json`) be removed from tracking too?

**Files:**
- Modify: `.gitignore`

**Step 1: Update .gitignore**

```gitignore
# macOS
.DS_Store

# Python
__pycache__/
*.pyc
.venv/

# Runtime data
cache/
app_state.db

# Logs
*.log

# Test/sample data
sample_profile.json
test_company_skate.json
test_stream_skate*.txt

# Environment
.env
```

**Step 2: Remove tracked files from index (without deleting from disk)**

```bash
git rm -r --cached cache/
git rm --cached app_state.db
git rm --cached *.log
git rm --cached sample_profile.json test_company_skate.json test_stream_skate.txt test_stream_skate_v2.txt
```

**Step 3: Commit**

```bash
git add .gitignore
git commit -m "chore: update .gitignore, stop tracking cache, logs, and runtime data"
```

---

### Task 2: Fix CORS — restrict allowed origins

> **Before starting, ask the user:**
> 1. What origin(s) will the frontend be served from? (e.g., `http://localhost:3000`, a production domain?)
> 2. Should CORS origins be configurable via environment variable (e.g., `JUSTICE_CORS_ORIGINS`)?
> 3. Do you need credentials support (cookies/auth headers) in CORS?

**Files:**
- Modify: `server.py:2333-2338`

**Step 1: Replace CORS middleware configuration**

Replace lines 2333-2338 in `server.py`:

```python
# Before:
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# After:
_cors_origins = os.environ.get("JUSTICE_CORS_ORIGINS", "http://localhost:3000").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_methods=["GET"],
    allow_headers=["*"],
)
```

**Step 2: Commit**

```bash
git add server.py
git commit -m "security: restrict CORS to configured origins"
```

---

### Task 3: Fix SSRF in document resolve endpoint

> **Before starting, ask the user:**
> 1. Should the validation only allow `https://or.justice.cz/` URLs, or also `https://justice.cz/` and other subdomains?
> 2. Should we also validate `subjektId` format in other endpoints (must be numeric)?

**Files:**
- Modify: `server.py:2376-2393`

**Step 1: Add URL validation to document resolve**

Add before line 2378:

```python
@app.get("/api/document/resolve")
def api_document_resolve(detail_url: str = Query(..., alias="detailUrl"), index: int = Query(0, ge=0), prefer_pdf: bool = Query(True)) -> FileResponse:
    allowed_prefixes = ("https://or.justice.cz/", "https://justice.cz/")
    if not any(detail_url.startswith(p) for p in allowed_prefixes):
        raise HTTPException(status_code=400, detail="Neplatná URL dokumentu.")
    detail = parse_document_detail(detail_url, force_refresh=True)
    # ... rest unchanged
```

**Step 2: Add subjektId validation to company endpoints**

In `api_company` (line 2358) and `api_company_stream` (line 2396), add:

```python
if not subjekt_id or not subjekt_id.strip().isdigit():
    raise HTTPException(status_code=400, detail="Neplatné ID subjektu.")
```

**Step 3: Commit**

```bash
git add server.py
git commit -m "security: validate detail_url and subjektId to prevent SSRF"
```

---

### Task 4: Add rate limiting

> **Before starting, ask the user:**
> 1. What rate limits make sense? Suggested: 10 requests/minute per IP for `/api/company` and `/api/company/stream`, 30/min for `/api/search`.
> 2. Should rate limiting be per-IP or global?
> 3. Do you want to add `slowapi` as a dependency, or implement a simpler in-memory rate limiter?
> 4. Should there be any authentication, or is rate limiting sufficient?

**Files:**
- Modify: `server.py` (imports + endpoint decorators)
- Potentially add: `requirements.txt` (if adding `slowapi`)

**Step 1: Install slowapi**

```bash
pip install slowapi
```

**Step 2: Add rate limiter setup after FastAPI app creation (line ~2332)**

```python
from slowapi import Limiter
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
```

**Step 3: Decorate expensive endpoints**

```python
@app.get("/api/company")
@limiter.limit("10/minute")
def api_company(request: Request, ...):
    ...

@app.get("/api/company/stream")
@limiter.limit("10/minute")
def api_company_stream(request: Request, ...):
    ...

@app.get("/api/search")
@limiter.limit("30/minute")
def api_search(request: Request, ...):
    ...
```

**Step 4: Commit**

```bash
git add server.py
git commit -m "security: add rate limiting to API endpoints"
```

---

### Task 5: Fix SQLite connection handling

> **Before starting, ask the user:**
> 1. Expected concurrency level? (How many simultaneous users?)
> 2. Should we use WAL mode for better concurrent read performance?
> 3. Should the DB schema init be a separate function called once at startup?

**Files:**
- Modify: `server.py:182-234`

**Step 1: Extract schema init into startup function**

```python
def init_db():
    """Create tables once at startup."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""CREATE TABLE IF NOT EXISTS ...""")
    conn.execute("""CREATE INDEX IF NOT EXISTS ...""")
    conn.commit()
    conn.close()
```

**Step 2: Simplify get_db() to just open a connection**

```python
def get_db():
    conn = sqlite3.connect(str(DB_PATH), timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row
    return conn
```

**Step 3: Add startup event to FastAPI**

```python
@app.on_event("startup")
def on_startup():
    init_db()
```

**Step 4: Ensure save_history_entry uses context manager for safe writes**

```python
def save_history_entry(visitor_id, profile, query=""):
    conn = get_db()
    try:
        with conn:
            conn.execute("""INSERT OR REPLACE INTO ...""", (...))
    finally:
        conn.close()
```

**Step 5: Commit**

```bash
git add server.py
git commit -m "fix: init DB schema once at startup, add WAL mode and timeouts"
```

---

## Phase 2: Infrastructure (Important)

### Task 6: Create dependency management file

> **Before starting, ask the user:**
> 1. Prefer `requirements.txt` or `pyproject.toml`?
> 2. Should we pin exact versions or use compatible-release specifiers (`~=`)?
> 3. Should we separate dev dependencies (eslint, any future test deps)?

**Files:**
- Create: `requirements.txt` (or `pyproject.toml`)

**Step 1: Generate from current .venv**

```bash
pip freeze > requirements.txt
```

**Step 2: Trim to only project dependencies**

Keep only: `fastapi`, `uvicorn`, `requests`, `beautifulsoup4`, `anthropic`, `urllib3`, `slowapi` (if added in Task 4), and their required sub-dependencies.

**Step 3: Commit**

```bash
git add requirements.txt
git commit -m "chore: add requirements.txt with pinned dependencies"
```

---

### Task 7: Add structured logging

> **Before starting, ask the user:**
> 1. JSON structured logging or human-readable format?
> 2. Log level from environment variable (e.g., `JUSTICE_LOG_LEVEL=INFO`)?
> 3. Should logs go to stdout only (for containerized deployment) or also to file?
> 4. What events are most important to log? Suggested: scrape attempts, PDF downloads, OCR invocations, AI calls, cache hits/misses, errors.

**Files:**
- Modify: `server.py` (add import, configure logger, add log statements at key points)

**Step 1: Add logging configuration near top of server.py (after imports)**

```python
import logging

log_level = os.environ.get("JUSTICE_LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, log_level, logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("justice")
```

**Step 2: Add log statements at key points**

- `fetch_text()`: log URL + status code
- `fetch_binary()`: log URL + file size
- `get_pdf_text()` / `ocr_selected_pages()`: log page count, OCR duration
- `generate_ai_analysis()`: log model, token usage, duration
- `save_json_cache()` / `load_json_cache()`: log cache hit/miss
- `save_history_entry()`: log subject ID
- Exception handlers: `logger.exception(...)` before returning user-facing error
- API endpoints: log request params

**Step 3: Remove or gitignore old manual log files**

**Step 4: Commit**

```bash
git add server.py
git commit -m "feat: add structured logging throughout backend"
```

---

### Task 8: Add unit tests for financial extraction

> **Before starting, ask the user:**
> 1. Use `pytest` or `unittest`?
> 2. Should we extract sample text from existing `cache/text/` files for test fixtures, or write synthetic fixtures?
> 3. Which functions are highest priority to test? Suggested priority:
>    - `parse_czech_date`
>    - `parse_number_candidates`
>    - `extract_metric_pair`
>    - `extract_financial_metrics_from_text`
>    - `normalize_timeline_outliers`
>    - `sanitize_financial_rows`
>    - `build_highlights`
>    - `clean_ico`
>    - `is_financial_document`
> 4. Should we refactor server.py into modules first (Task 10) to make imports cleaner, or test against the monolith?

**Files:**
- Create: `tests/` directory
- Create: `tests/conftest.py`
- Create: `tests/test_parsing.py`
- Create: `tests/test_financial_extraction.py`
- Create: `tests/test_highlights.py`
- Create: `tests/fixtures/` (sample text files)

**Step 1: Install pytest**

```bash
pip install pytest
```

**Step 2: Create test directory structure**

```bash
mkdir -p tests/fixtures
```

**Step 3: Write tests for pure parsing functions**

```python
# tests/test_parsing.py
from server import parse_czech_date, clean_ico, parse_number_candidates, is_financial_document

def test_parse_czech_date_standard():
    assert parse_czech_date("15. ledna 2023") == "2023-01-15"

def test_parse_czech_date_short():
    assert parse_czech_date("1. 3. 2022") is not None

def test_clean_ico_strips_whitespace():
    assert clean_ico(" 123 456 78 ") == "12345678"

def test_parse_number_candidates():
    results = parse_number_candidates("Tržby: 1 234 567 Kč")
    assert len(results) > 0

def test_is_financial_document():
    assert is_financial_document({"description": "Účetní závěrka 2023"})
    assert not is_financial_document({"description": "Plná moc"})
```

**Step 4: Write tests for financial extraction (with fixture data)**

```python
# tests/test_financial_extraction.py
from server import extract_financial_metrics_from_text, normalize_timeline_outliers, sanitize_financial_rows

def test_extract_basic_revenue():
    text = "Tržby za prodej výrobků a služeb    15 234 000"
    metrics = extract_financial_metrics_from_text(text, 2023)
    # Assert revenue was extracted
    ...

def test_normalize_timeline_outliers_removes_spikes():
    timeline = {...}  # timeline with an obvious outlier
    normalized = normalize_timeline_outliers(timeline)
    # Assert outlier was clamped/removed
    ...
```

**Step 5: Run tests**

```bash
pytest tests/ -v
```

**Step 6: Commit**

```bash
git add tests/ requirements.txt
git commit -m "test: add unit tests for parsing and financial extraction"
```

---

### Task 9: Remove dead code and scripts

> **Before starting, ask the user:**
> 1. Should `patch_robustness_redesign.py`, `update_batch2.py`, `update_batch3.py` be deleted entirely or moved to `scripts/archive/`?
> 2. Should the `notes_plan.txt` file be kept?
> 3. Any of the log files (`server.log`, `server2.log`, etc.) needed for reference?

**Files:**
- Delete or move: `patch_robustness_redesign.py`, `update_batch2.py`, `update_batch3.py`
- Optionally delete: `notes_plan.txt`, `server*.log`, `server_batch2.log`

**Step 1: Remove files**

```bash
git rm patch_robustness_redesign.py update_batch2.py update_batch3.py
git rm notes_plan.txt server.log server2.log server3.log server_batch2.log server_current.log
```

**Step 2: Commit**

```bash
git commit -m "chore: remove dead scripts and old log files"
```

---

## Phase 3: Architecture Refactoring

### Task 10: Extract server.py into modules

> **Before starting, ask the user:**
> 1. Preferred module structure? Suggested:
>    ```
>    justice/
>      __init__.py
>      app.py          # FastAPI app, endpoints, CORS
>      db.py           # SQLite helpers
>      scraping.py     # HTTP fetching, HTML parsing
>      extraction.py   # Financial metric extraction
>      documents.py    # PDF processing, OCR
>      ai.py           # Anthropic integration
>      models.py       # Data structures / TypedDicts
>      utils.py        # Date parsing, text normalization, caching
>    ```
>    Or keep it as a single `server.py` but better organized with clear section comments?
> 2. Should we keep `server.py` as the entry point that imports from the package, or switch to `python -m justice`?
> 3. How much refactoring risk is acceptable? Extract-only (move code, fix imports) or also clean up interfaces?

**Files:**
- Create: module files per chosen structure
- Modify: `server.py` (reduce to imports + app setup)

This is the largest task. Each module extraction should be a separate commit:

**Step 1:** Extract `utils.py` (pure functions: date parsing, text normalization, slug_hash, caching)
**Step 2:** Extract `db.py` (get_db, init_db, save_history_entry, get_history_entries)
**Step 3:** Extract `scraping.py` (fetch_text, fetch_binary, parse_search_results, search_companies, parse_extract_rows, fetch_extract)
**Step 4:** Extract `documents.py` (PDF processing, OCR, document list parsing, document detail parsing)
**Step 5:** Extract `extraction.py` (all financial metric extraction, timeline management)
**Step 6:** Extract `ai.py` (generate_ai_analysis, compact_* helpers, build_highlights)
**Step 7:** Extract `app.py` (FastAPI setup, endpoints, CORS, rate limiting)
**Step 8:** Update imports everywhere, run tests
**Step 9:** Commit after each extraction

---

### Task 11: Deduplicate build_company_profile and api_company_stream

> **Before starting, ask the user:**
> 1. Should the streaming endpoint use a callback/event pattern (yield progress from shared logic), or should we extract a pipeline that returns intermediate results?
> 2. Is the synchronous `/api/company` endpoint still needed, or can everything use streaming?

**Files:**
- Modify: `server.py` (or `app.py` after Task 10) — lines 2234-2329 and 2396-2535

**Step 1: Extract shared pipeline function**

```python
def run_company_pipeline(subjekt_id, force_refresh=False, visitor_id="", query="", on_progress=None):
    """Core pipeline. on_progress(stage, data) called at each stage if provided."""
    # ... shared logic ...
    if on_progress:
        on_progress("extract_done", {"basic_info": basic_info_items})
    # ... continue ...
    return profile
```

**Step 2: Simplify build_company_profile to call shared pipeline**

```python
def build_company_profile(subjekt_id, force_refresh=False, visitor_id="", query=""):
    return run_company_pipeline(subjekt_id, force_refresh, visitor_id, query)
```

**Step 3: Simplify api_company_stream to call shared pipeline with SSE callbacks**

```python
def api_company_stream(...):
    def iterator():
        def on_progress(stage, data):
            # yield SSE event
            ...
        profile = run_company_pipeline(subjekt_id, ..., on_progress=on_progress)
        yield sse_event("result", profile)
    return StreamingResponse(iterator(), ...)
```

**Step 4: Run tests, verify both endpoints produce identical output**

**Step 5: Commit**

```bash
git commit -m "refactor: deduplicate company profile pipeline"
```

---

## Phase 4: Minor Fixes & Polish

### Task 12: Fix escapeHtml single quote

> No questions needed — straightforward fix.

**Files:**
- Modify: `app.js:47-52`

**Step 1: Add single quote escaping**

```javascript
const escapeHtml = (value) =>
  String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
```

**Step 2: Commit**

```bash
git add app.js
git commit -m "fix: escape single quotes in escapeHtml"
```

---

### Task 13: Fix is_section_label logic

> No questions needed.

**Files:**
- Modify: `server.py:837-856`

**Step 1: Clarify the condition**

Review the function and make the intent explicit with a comment or clearer logic.

**Step 2: Commit**

---

### Task 14: Clean up ESLint globals

> No questions needed.

**Files:**
- Modify: `eslint.config.mjs`

**Step 1: Remove unused globals, keep only `Chart`**

```javascript
globals: {
  ...globals.browser,
  Chart: "readonly",
}
```

**Step 2: Run ESLint to verify no new errors**

```bash
npx eslint app.js
```

**Step 3: Commit**

```bash
git add eslint.config.mjs
git commit -m "chore: remove unused ESLint globals"
```

---

### Task 15: Fix README hardcoded paths

> **Before starting, ask the user:**
> 1. What should the README say for local setup paths? Should it just say "paths are relative to the project root by default"?

**Files:**
- Modify: `README.md:181-186`

**Step 1: Update path references to reflect actual code behavior**

**Step 2: Commit**

---

### Task 16: Implement or remove scroll-hide-header

> **Before starting, ask the user:**
> 1. Do you want the scroll-hide behavior implemented, or should the helper text just be removed?

**Files:**
- If implementing: Modify `app.js` (add scroll listener that toggles `header-hidden` class)
- If removing: Modify `index.html:97` (remove the helper text)

**Step 1: Implement or remove per user choice**

**Step 2: Commit**

---

### Task 17: Add cache eviction (optional)

> **Before starting, ask the user:**
> 1. What max cache size is reasonable? (e.g., 1GB, 5GB?)
> 2. Evict by LRU (oldest access) or oldest modification time?
> 3. Should eviction run on a schedule or on every cache write?

**Files:**
- Modify: `server.py` (or `utils.py` after Task 10) — add eviction function

**Step 1: Add eviction function**

**Step 2: Wire into cache write or startup**

**Step 3: Commit**

---

## Phase 5: Async Migration (Optional / Future)

### Task 18: Migrate blocking endpoints to async (optional)

> **Before starting, ask the user:**
> 1. Is this needed now, or is rate limiting (Task 4) sufficient for current load?
> 2. Willing to add `httpx` as a dependency to replace `requests`?
> 3. Should the SSE streaming endpoint be the priority, or all endpoints?

This is a larger effort and may not be needed if the app has low concurrency. Defer unless load requires it.

---

## Execution Order

```
Phase 1 (Critical Security):
  Task 1  → .gitignore + untrack files
  Task 2  → CORS restriction
  Task 3  → SSRF + input validation
  Task 4  → Rate limiting
  Task 5  → SQLite connection fix

Phase 2 (Infrastructure):
  Task 6  → requirements.txt
  Task 7  → Logging
  Task 8  → Unit tests
  Task 9  → Remove dead code

Phase 3 (Architecture):
  Task 10 → Module extraction
  Task 11 → Pipeline deduplication

Phase 4 (Polish):
  Tasks 12-17 → Minor fixes

Phase 5 (Optional):
  Task 18 → Async migration
```

Tasks within each phase can be done in order listed. Phases 2-4 can be parallelized after Phase 1.
