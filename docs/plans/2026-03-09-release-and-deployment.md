# Release & Deployment Plan — praskac.xyz

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Complete remaining hardening tasks (15, 17), prepare for single-service deployment to Railway at `praskac.xyz`, and create deployment infrastructure (Dockerfile, static serving, env docs).

**Architecture:** Single FastAPI service serves both the API (`/api/*`) and static frontend files (`/`). Railway runs the Docker container, domain points to `praskac.xyz`. CORS becomes unnecessary since everything is same-origin.

**Tech Stack:** Python 3 / FastAPI / SQLite / Vanilla JS / Docker / Railway

**Decisions made:**
- **Task 16 (scroll-hide):** Already implemented — JS (app.js:1002-1031), CSS (style.css:768-775). No work needed.
- **Task 18 (async migration):** Skipped. FastAPI runs sync endpoints in a thread pool automatically. For friends-only traffic, this is fine. Converting `requests` → `httpx` + async subprocess would be a large refactor with high bug risk and marginal benefit.
- **Task 11 (pipeline dedup):** Out of scope for this plan. The sync `build_company_profile` (ai.py:560-655) and streaming `api_company_stream` (app.py:152-298) share logic but work correctly. Dedup is a future refactor.

---

## Task 1: Fix app.js API URL for same-origin deployment

The frontend currently assumes the API is on a separate port:
```javascript
const API = `http://${window.location.hostname || "localhost"}:8000`;
```

Since FastAPI will serve both static files and API, the frontend should use relative URLs.

**Files:**
- Modify: `app.js:1`

**Step 1: Change API base to empty string**

Replace line 1:
```javascript
const API = "";
```

This makes all `fetch(${API}/api/...)` calls use relative paths like `/api/search`, which hit the same origin.

**Step 2: Verify no other hardcoded port references**

Search `app.js` for `:8000` or `localhost` — there should be none after the change.

**Step 3: Commit**

```bash
git add app.js
git commit -m "fix: use relative API URLs for same-origin deployment"
```

---

## Task 2: Add static file serving to FastAPI

FastAPI needs to serve `index.html`, `app.js`, `style.css`, `base.css` from the project root.

**Files:**
- Modify: `justice/app.py`

**Step 1: Add StaticFiles mount and index.html fallback**

Add at the bottom of `justice/app.py`, after all API route definitions:

```python
from fastapi.staticfiles import StaticFiles
from pathlib import Path

_static_dir = Path(__file__).resolve().parent.parent

@app.get("/")
def serve_index():
    return FileResponse(_static_dir / "index.html")

app.mount("/", StaticFiles(directory=str(_static_dir)), name="static")
```

The explicit `GET /` route must come before the `StaticFiles` mount so the index page is served. `StaticFiles` handles `app.js`, `style.css`, `base.css`.

**Step 2: Remove CORS middleware (same-origin, no longer needed)**

Since frontend and API are on the same origin, CORS middleware is unnecessary. Remove the CORS import, `_cors_origins` config, and `app.add_middleware(CORSMiddleware, ...)` block. Keep `JUSTICE_CORS_ORIGINS` env var support in case someone runs frontend separately in development — but wrap it:

```python
_cors_origins_env = os.environ.get("JUSTICE_CORS_ORIGINS", "")
if _cors_origins_env:
    _cors_origins = [o.strip() for o in _cors_origins_env.split(",") if o.strip()]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins,
        allow_methods=["GET", "OPTIONS"],
        allow_headers=["Accept", "Content-Type"],
    )
```

**Step 3: Test locally**

```bash
cd /Users/danielrahman/Desktop/justice-praskac
.venv/bin/uvicorn justice.app:app --host 0.0.0.0 --port 8000
# Visit http://localhost:8000 — should serve the full app
# Visit http://localhost:8000/api/health — should return {"status": "ok"}
```

**Step 4: Commit**

```bash
git add justice/app.py
git commit -m "feat: serve static frontend from FastAPI, make CORS opt-in"
```

---

## Task 3: Cache eviction (Task 17 from hardening plan)

Without eviction, `cache/` grows unbounded. Add LRU eviction by modification time, triggered on every cache write.

**Files:**
- Modify: `justice/utils.py`
- Create: `tests/test_cache_eviction.py`

**Step 1: Write failing tests**

```python
# tests/test_cache_eviction.py
import json
import os
import time
from pathlib import Path
from unittest.mock import patch

from justice.utils import evict_cache_dir, save_json_cache, JSON_DIR


def test_evict_cache_dir_removes_oldest(tmp_path):
    """When total size exceeds max, oldest files are removed."""
    for i in range(5):
        f = tmp_path / f"file{i}.json"
        f.write_text("x" * 1000)
        os.utime(f, (time.time() - (5 - i) * 100, time.time() - (5 - i) * 100))
    # 5 files × 1000 bytes = 5000 bytes. Evict to max 3000.
    evict_cache_dir(tmp_path, max_bytes=3000)
    remaining = sorted(f.name for f in tmp_path.iterdir())
    # The 2 oldest should be gone
    assert "file0.json" not in remaining
    assert "file1.json" not in remaining
    assert len(remaining) == 3


def test_evict_cache_dir_noop_under_limit(tmp_path):
    """No files removed when under the limit."""
    f = tmp_path / "small.json"
    f.write_text("x" * 100)
    evict_cache_dir(tmp_path, max_bytes=10000)
    assert f.exists()
```

**Step 2: Run tests to verify they fail**

```bash
.venv/bin/pytest tests/test_cache_eviction.py -v
```

Expected: ImportError — `evict_cache_dir` doesn't exist yet.

**Step 3: Implement evict_cache_dir**

Add to `justice/utils.py`:

```python
MAX_CACHE_BYTES = int(os.getenv("JUSTICE_MAX_CACHE_BYTES", str(2 * 1024 * 1024 * 1024)))  # 2 GB default

def evict_cache_dir(directory: Path, max_bytes: int = MAX_CACHE_BYTES) -> None:
    """Remove oldest files in directory until total size is under max_bytes."""
    try:
        files = [(f, f.stat()) for f in directory.iterdir() if f.is_file()]
    except OSError:
        return
    total = sum(s.st_size for _, s in files)
    if total <= max_bytes:
        return
    # Sort by modification time, oldest first
    files.sort(key=lambda x: x[1].st_mtime)
    for f, s in files:
        if total <= max_bytes:
            break
        try:
            f.unlink()
            total -= s.st_size
            logger.info(f"cache evict path={f}")
        except OSError:
            pass
```

**Step 4: Call eviction after cache writes**

In `save_json_cache`, add at the end:

```python
def save_json_cache(name: str, data: Any) -> None:
    path = JSON_DIR / f"{name}.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(f"cache write name={name}")
    evict_cache_dir(JSON_DIR)
```

**Step 5: Run tests**

```bash
.venv/bin/pytest tests/test_cache_eviction.py -v
```

Expected: PASS.

**Step 6: Run full test suite**

```bash
.venv/bin/pytest tests/ -v
```

**Step 7: Commit**

```bash
git add justice/utils.py tests/test_cache_eviction.py
git commit -m "feat: add LRU cache eviction with configurable size limit"
```

---

## Task 4: Create .env.example

Document all environment variables with descriptions and defaults.

**Files:**
- Create: `.env.example`

**Step 1: Create the file**

```env
# Required
ANTHROPIC_API_KEY=sk-ant-...your-key-here...

# Optional — AI configuration
JUSTICE_ENABLE_AI=1                        # Set to 0 to disable AI analysis
JUSTICE_AI_MODEL=claude_sonnet_4_5         # Anthropic model for analysis
JUSTICE_AI_TIMEOUT_SECONDS=90              # Timeout for AI API calls

# Optional — caching
JUSTICE_PROFILE_CACHE_TTL_SECONDS=259200   # Profile cache TTL (default: 3 days)
JUSTICE_MAX_CACHE_BYTES=2147483648         # Max cache dir size (default: 2 GB)
JUSTICE_CACHE_DIR=./cache                  # Cache directory path
JUSTICE_DB_PATH=./app_state.db             # SQLite database path

# Optional — server
JUSTICE_LOG_LEVEL=INFO                     # Log level: DEBUG, INFO, WARNING, ERROR
JUSTICE_CORS_ORIGINS=                      # Comma-separated origins (empty = disabled, same-origin)
PORT=8000                                  # Server port (Railway sets this automatically)
```

**Step 2: Add to .gitignore (already has `.env`, verify)**

Verify `.env` is in `.gitignore`. It is.

**Step 3: Commit**

```bash
git add .env.example
git commit -m "docs: add .env.example with all configuration options"
```

---

## Task 5: Create Dockerfile

Railway builds from Dockerfile. Need Python 3, system deps (poppler-utils, tesseract, language packs), pip install, and uvicorn entrypoint.

**Files:**
- Create: `Dockerfile`

**Step 1: Create Dockerfile**

```dockerfile
FROM python:3.13-slim

# System dependencies for PDF processing and OCR
RUN apt-get update && apt-get install -y --no-install-recommends \
    poppler-utils \
    tesseract-ocr \
    tesseract-ocr-ces \
    tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Railway sets PORT env var
CMD uvicorn justice.app:app --host 0.0.0.0 --port ${PORT:-8000}
```

**Step 2: Create .dockerignore**

```
.venv/
.git/
__pycache__/
*.pyc
.env
.DS_Store
docs/
tests/
.pytest_cache/
cache/
app_state.db
*.log
sample_profile.json
test_company_skate.json
test_stream_skate*.txt
```

**Step 3: Test Docker build locally (optional)**

```bash
docker build -t justice-praskac .
docker run -p 8000:8000 -e ANTHROPIC_API_KEY=$ANTHROPIC_API_KEY justice-praskac
```

**Step 4: Commit**

```bash
git add Dockerfile .dockerignore
git commit -m "feat: add Dockerfile for Railway deployment"
```

---

## Task 6: Fix README (Task 15 from hardening plan)

Rewrite README to reflect current architecture (modular `justice/` package), remove hardcoded paths, add deployment instructions, and document system dependencies with install commands.

**Files:**
- Modify: `README.md`

**Step 1: Update the following sections:**

1. **Architecture section** — update to reflect `justice/` package modules instead of monolithic `server.py`
2. **Remove hardcoded paths** (lines 181-186) — paths are now relative via `ROOT_DIR` in `justice/utils.py`, configurable via env vars
3. **Add system dependencies install commands** — macOS (Homebrew) and Linux (apt)
4. **Add deployment section** — Railway deployment steps
5. **Add environment variables section** — reference `.env.example`
6. **Update "Spuštění lokálně" section** — single command `uvicorn justice.app:app`

**Step 2: Commit**

```bash
git add README.md
git commit -m "docs: update README with deployment guide, system deps, and current architecture"
```

---

## Task 7: Update server.py entrypoint for PORT env var

Railway sets `PORT` dynamically. The entrypoint should respect it.

**Files:**
- Modify: `server.py`

**Step 1: Update server.py**

```python
#!/usr/bin/env python3
"""Entry point for Justice Praskac API."""
import os
from justice.app import app

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
```

**Step 2: Commit**

```bash
git add server.py
git commit -m "fix: respect PORT env var in server entrypoint"
```

---

## Task 8: Remove helper text mentioning scroll-hide (Task 16 cleanup)

The helper text at `index.html:97` says "Při scrollu se horní lišta schová, při návratu zase vyjede." This is implementation detail exposed to users — not useful as a helper hint.

**Files:**
- Modify: `index.html:97`

**Step 1: Remove the scroll-hide mention from helper text**

Replace the helper text with something useful, or remove it:
```html
<p class="helper-text">
  Když bude více shod, nabídnu výběr správné firmy.
</p>
```

**Step 2: Commit**

```bash
git add index.html
git commit -m "fix: remove scroll-hide implementation detail from user-facing helper text"
```

---

## Execution Order

```
Task 1 → Fix app.js API URL (prerequisite for Task 2)
Task 2 → Static file serving in FastAPI
Task 8 → Clean up helper text (quick, independent)
Task 3 → Cache eviction (independent)
Task 4 → .env.example (independent)
Task 5 → Dockerfile + .dockerignore (depends on Tasks 1-2 for testing)
Task 6 → README rewrite (last — reflects final state)
Task 7 → server.py PORT fix (independent, small)
```

Tasks 3, 4, 7, 8 are independent and can run in parallel.

---

## Post-deploy checklist

After Railway deployment:
1. Set `ANTHROPIC_API_KEY` in Railway Variables
2. Set custom domain `praskac.xyz` in Railway Settings → Networking
3. Verify `https://praskac.xyz` loads the frontend
4. Verify `https://praskac.xyz/api/health` returns `{"status": "ok"}`
5. Test a company search end-to-end
