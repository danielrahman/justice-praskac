import re
from pathlib import Path

base = Path('/home/user/workspace/justice-praskac')
server_path = base / 'server.py'
app_path = base / 'app.js'
style_path = base / 'style.css'
index_path = base / 'index.html'

server = server_path.read_text(encoding='utf-8')
app = app_path.read_text(encoding='utf-8')
style = style_path.read_text(encoding='utf-8')
index_html = index_path.read_text(encoding='utf-8')

server = server.replace('PROFILE_CACHE_VERSION = "v5_stream_history_tablefix"', 'PROFILE_CACHE_VERSION = "v6_shared_history_mobile_status"')

old_db_block = '''def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS company_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            visitor_id TEXT NOT NULL,
            subject_id TEXT NOT NULL,
            ico TEXT,
            name TEXT,
            query TEXT,
            payload_json TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(visitor_id, subject_id)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_company_history_visitor_updated ON company_history(visitor_id, updated_at DESC)"
    )
    conn.commit()
    return conn


def save_history_entry(visitor_id: str | None, profile: dict[str, Any], query: str | None = None) -> None:
    if not visitor_id:
        return
    conn = get_db()
    try:
        conn.execute(
            """
            INSERT INTO company_history (visitor_id, subject_id, ico, name, query, payload_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            ON CONFLICT(visitor_id, subject_id)
            DO UPDATE SET
                ico = excluded.ico,
                name = excluded.name,
                query = COALESCE(excluded.query, company_history.query),
                payload_json = excluded.payload_json,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                visitor_id,
                str(profile.get("subject_id") or ""),
                str(profile.get("ico") or ""),
                str(profile.get("name") or ""),
                query,
                json.dumps(profile, ensure_ascii=False),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def get_history_entries(visitor_id: str | None, limit: int = 12) -> list[dict[str, Any]]:
    if not visitor_id:
        return []
    conn = get_db()
    try:
        rows = conn.execute(
            """
            SELECT subject_id, ico, name, query, payload_json, updated_at
            FROM company_history
            WHERE visitor_id = ?
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (visitor_id, limit),
        ).fetchall()
    finally:
        conn.close()
    items: list[dict[str, Any]] = []
    for row in rows:
        payload: dict[str, Any] = {}
        try:
            payload = json.loads(row["payload_json"])
        except Exception:
            payload = {}
        timeline = payload.get("financial_timeline") or []
        latest = timeline[-1] if timeline else None
        items.append(
            {
                "subject_id": row["subject_id"],
                "ico": row["ico"],
                "name": row["name"],
                "query": row["query"],
                "updated_at": row["updated_at"],
                "latest_year": latest.get("year") if latest else None,
                "latest_revenue": latest.get("revenue") if latest else None,
                "analysis_overview": payload.get("analysis_overview"),
                "praskac_count": len(payload.get("praskac") or []),
            }
        )
    return items
'''

new_db_block = '''def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS shared_company_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            subject_id TEXT NOT NULL UNIQUE,
            ico TEXT,
            name TEXT,
            query TEXT,
            payload_json TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            last_visitor_id TEXT
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_shared_company_history_updated ON shared_company_history(updated_at DESC)"
    )
    conn.commit()
    return conn


def save_history_entry(visitor_id: str | None, profile: dict[str, Any], query: str | None = None) -> None:
    conn = get_db()
    try:
        conn.execute(
            """
            INSERT INTO shared_company_history (subject_id, ico, name, query, payload_json, created_at, updated_at, last_visitor_id)
            VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, ?)
            ON CONFLICT(subject_id)
            DO UPDATE SET
                ico = excluded.ico,
                name = excluded.name,
                query = COALESCE(excluded.query, shared_company_history.query),
                payload_json = excluded.payload_json,
                updated_at = CURRENT_TIMESTAMP,
                last_visitor_id = excluded.last_visitor_id
            """,
            (
                str(profile.get("subject_id") or ""),
                str(profile.get("ico") or ""),
                str(profile.get("name") or ""),
                query,
                json.dumps(profile, ensure_ascii=False),
                visitor_id,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def get_history_entries(_visitor_id: str | None = None, limit: int = 40) -> list[dict[str, Any]]:
    conn = get_db()
    try:
        rows = conn.execute(
            """
            SELECT subject_id, ico, name, query, payload_json, updated_at
            FROM shared_company_history
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    finally:
        conn.close()
    items: list[dict[str, Any]] = []
    for row in rows:
        items.append(
            {
                "subject_id": row["subject_id"],
                "ico": row["ico"],
                "name": row["name"],
                "query": row["query"],
                "updated_at": row["updated_at"],
            }
        )
    return items
'''

if old_db_block not in server:
    raise SystemExit('Old DB block not found')
server = server.replace(old_db_block, new_db_block)

old_merge = re.compile(r'''def merge_financial_timeline\(docs: list\[dict\[str, Any\]\]\) -> tuple\[list\[dict\[str, Any\]\], list\[dict\[str, Any\]\]\]:\n(?:    .*\n)+?    return ordered, processed_docs\n''')
new_merge = '''def extract_financial_doc_data(doc: dict[str, Any]) -> tuple[dict[str, Any], dict[int, dict[str, float]]]:
    doc_copy = dict(doc)
    if not doc.get("pdf_url"):
        doc_copy["extraction_mode"] = "missing"
        doc_copy["page_count"] = doc.get("pages", 0)
        doc_copy["metrics_found"] = []
        doc_copy["download_links"] = doc.get("download_links") or []
        return doc_copy, {}

    primary_year = (doc.get("years") or [None])[0]
    if not primary_year:
        doc_copy["extraction_mode"] = "unknown"
        doc_copy["page_count"] = doc.get("pages", 0)
        doc_copy["metrics_found"] = []
        doc_copy["download_links"] = doc.get("download_links") or []
        return doc_copy, {}

    try:
        pdf_text = get_pdf_text(doc["pdf_url"])
        extracted = extract_financial_metrics_from_text(pdf_text["text"], primary_year)
    except Exception:
        extracted = {"year_map": {}, "multiplier": 1000, "found_metrics": {}}
        pdf_text = {"mode": "unknown", "page_count": doc.get("pages", 0)}

    doc_copy["extraction_mode"] = pdf_text.get("mode")
    doc_copy["page_count"] = pdf_text.get("page_count")
    doc_copy["metrics_found"] = sorted(list(extracted.get("found_metrics", {}).keys()))
    doc_copy["download_links"] = doc.get("download_links") or []
    return doc_copy, extracted.get("year_map", {})


def merge_doc_year_map(timeline: dict[int, dict[str, Any]], doc_copy: dict[str, Any], year_map: dict[int, dict[str, float]]) -> None:
    primary_year = (doc_copy.get("years") or [None])[0]
    for year, values in year_map.items():
        slot = timeline.setdefault(year, {"year": year, "sources": []})
        doc_score = int(doc_copy.get("doc_quality_score") or 0)
        value_score = doc_score + 20 if year == primary_year else doc_score - 20
        for key, value in values.items():
            if value is None:
                continue
            existing_value = slot.get(key)
            existing_score = slot.get(f"_{key}_score", -1)
            if existing_value is None or value_score > existing_score:
                slot[key] = value
                slot[f"_{key}_score"] = value_score
        slot["sources"].append(
            {
                "document_number": doc_copy.get("document_number"),
                "detail_url": doc_copy.get("detail_url"),
                "pdf_url": doc_copy.get("pdf_url"),
                "year": primary_year,
            }
        )


def finalize_financial_timeline(timeline: dict[int, dict[str, Any]]) -> list[dict[str, Any]]:
    ordered = [timeline[y] for y in sorted(timeline.keys()) if y >= 2000]
    for row in ordered:
        for key in list(row.keys()):
            if key.startswith("_") and key.endswith("_score"):
                row.pop(key, None)
    ordered = normalize_timeline_outliers(ordered)
    ordered = sanitize_financial_rows(ordered)
    ordered = recalculate_timeline_ratios(ordered)
    return ordered


def merge_financial_timeline(docs: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    timeline: dict[int, dict[str, Any]] = {}
    processed_docs: list[dict[str, Any]] = []
    for doc in docs:
        doc_copy, year_map = extract_financial_doc_data(doc)
        processed_docs.append(doc_copy)
        merge_doc_year_map(timeline, doc_copy, year_map)
    ordered = finalize_financial_timeline(timeline)
    return ordered, processed_docs
'''
server, count = old_merge.subn(new_merge, server, count=1)
if count != 1:
    raise SystemExit('merge_financial_timeline block not replaced')

old_history_endpoint = '''@app.get("/api/history")
def api_history(request: Request) -> dict[str, Any]:
    visitor_id = request.headers.get("X-Visitor-Id")
    return {"items": get_history_entries(visitor_id)}
'''
new_history_endpoint = '''@app.get("/api/history")
def api_history(request: Request) -> dict[str, Any]:
    visitor_id = request.headers.get("X-Visitor-Id")
    return {"items": get_history_entries(visitor_id)}
'''
server = server.replace(old_history_endpoint, new_history_endpoint)

old_stream_chunk = '''            yield sse_event("status", {"label": "Čtu úplný výpis a historii změn"})
            full_extract = fetch_extract(subjekt_id, "UPLNY", force_refresh=refresh)
            people = extract_people_and_owners(current_extract)
            history = extract_history_events(full_extract)

            yield sse_event("status", {"label": "Stahuji Sbírku listin"})
            docs = parse_document_list(subjekt_id, force_refresh=refresh)
            relevant_docs = pick_recent_financial_docs(docs, max_years=5, force_refresh_details=refresh)
            yield sse_event("status", {"label": f"Čtu finanční listiny ({len(relevant_docs)}) a vytahuji čísla"})
            timeline, processed_docs = merge_financial_timeline(relevant_docs)
            overview, deep, praskac = build_highlights(timeline, processed_docs, history)

            yield sse_event("status", {"label": "Dopočítávám souhrn a kontroluji veřejná čísla"})
            if AI_ENABLED:
                try:
                    ai_analysis = generate_ai_analysis(
                        company_name=company_name,
                        ico=ico,
                        basic_info_items=basic_info_items,
                        executives=people["executives"],
                        owners=people["owners"],
                        history=history,
                        timeline=timeline,
                        docs=processed_docs,
                        overview_fallback=overview,
                        deep_fallback=deep,
                        praskac_fallback=praskac,
                    )
                except Exception:
                    ai_analysis = {
                        "analysis_engine": "fallback",
                        "analysis_model": None,
                        "analysis_usage": None,
                        "analysis_overview": "Shrnutí běží bez AI vrstvy. Níže je pravidlový výstup z veřejných podkladů justice.cz.",
                        "data_quality_note": "Kvalita dat závisí na čitelnosti veřejných PDF a úplnosti Sbírky listin.",
                        "insight_summary": overview,
                        "deep_insights": deep,
                        "praskac": praskac,
                    }
            else:
                ai_analysis = {
                    "analysis_engine": "disabled",
                    "analysis_model": None,
                    "analysis_usage": None,
                    "analysis_overview": "AI vrstva je vypnutá. Níže je pravidlový výstup z veřejných podkladů justice.cz.",
                    "data_quality_note": "Kvalita dat závisí na čitelnosti veřejných PDF a úplnosti Sbírky listin.",
                    "insight_summary": overview,
                    "deep_insights": deep,
                    "praskac": praskac,
                }

            external_checks = build_external_checks(timeline, company_name, ico)
            profile = {
'''
new_stream_chunk = '''            yield sse_event("status", {"label": "Čtu úplný výpis a historii změn"})
            full_extract = fetch_extract(subjekt_id, "UPLNY", force_refresh=refresh)
            people = extract_people_and_owners(current_extract)
            history = extract_history_events(full_extract)

            yield sse_event("status", {"label": "Stahuji seznam listin ze Sbírky listin"})
            docs = parse_document_list(subjekt_id, force_refresh=refresh)
            relevant_docs = pick_recent_financial_docs(docs, max_years=5, force_refresh_details=refresh)
            yield sse_event("status", {"label": f"Vybral jsem {len(relevant_docs)} relevantních finančních listin"})

            timeline_map: dict[int, dict[str, Any]] = {}
            processed_docs: list[dict[str, Any]] = []
            total_docs = len(relevant_docs)
            for idx, doc in enumerate(relevant_docs, start=1):
                year_hint = (doc.get("years") or [None])[0]
                doc_title = doc.get("document_number") or doc.get("type") or "listina"
                if year_hint:
                    label = f"Čtu listinu {idx}/{total_docs}: {doc_title} · rok {year_hint}"
                else:
                    label = f"Čtu listinu {idx}/{total_docs}: {doc_title}"
                yield sse_event("status", {"label": label})
                doc_copy, year_map = extract_financial_doc_data(doc)
                processed_docs.append(doc_copy)
                merge_doc_year_map(timeline_map, doc_copy, year_map)
                metric_count = len(doc_copy.get("metrics_found") or [])
                if metric_count:
                    yield sse_event("status", {"label": f"Z listiny {idx}/{total_docs} jsem vytáhl {metric_count} metrik"})
                else:
                    yield sse_event("status", {"label": f"Listina {idx}/{total_docs} má slabší čitelnost, zkouším další podklady"})

            timeline = finalize_financial_timeline(timeline_map)
            overview, deep, praskac = build_highlights(timeline, processed_docs, history)

            yield sse_event("status", {"label": "Kontroluji trendy, díry v letech a veřejné signály"})
            if AI_ENABLED:
                yield sse_event("status", {"label": "Píšu AI shrnutí a skládám body do profilu"})
                try:
                    ai_analysis = generate_ai_analysis(
                        company_name=company_name,
                        ico=ico,
                        basic_info_items=basic_info_items,
                        executives=people["executives"],
                        owners=people["owners"],
                        history=history,
                        timeline=timeline,
                        docs=processed_docs,
                        overview_fallback=overview,
                        deep_fallback=deep,
                        praskac_fallback=praskac,
                    )
                except Exception:
                    ai_analysis = {
                        "analysis_engine": "fallback",
                        "analysis_model": None,
                        "analysis_usage": None,
                        "analysis_overview": "Shrnutí běží bez AI vrstvy. Níže je pravidlový výstup z veřejných podkladů justice.cz.",
                        "data_quality_note": "Kvalita dat závisí na čitelnosti veřejných PDF a úplnosti Sbírky listin.",
                        "insight_summary": overview,
                        "deep_insights": deep,
                        "praskac": praskac,
                    }
            else:
                ai_analysis = {
                    "analysis_engine": "disabled",
                    "analysis_model": None,
                    "analysis_usage": None,
                    "analysis_overview": "AI vrstva je vypnutá. Níže je pravidlový výstup z veřejných podkladů justice.cz.",
                    "data_quality_note": "Kvalita dat závisí na čitelnosti veřejných PDF a úplnosti Sbírky listin.",
                    "insight_summary": overview,
                    "deep_insights": deep,
                    "praskac": praskac,
                }

            yield sse_event("status", {"label": "Porovnávám čísla s veřejnou kontrolou a ukládám sdílenou historii"})
            external_checks = build_external_checks(timeline, company_name, ico)
            profile = {
'''
if old_stream_chunk not in server:
    raise SystemExit('Old stream chunk not found')
server = server.replace(old_stream_chunk, new_stream_chunk)

server_path.write_text(server, encoding='utf-8')

app = app.replace('  mobileSidebar: false,\n};', '  mobileSidebar: false,\n  headerHidden: false,\n  lastScrollTop: 0,\n};')
app = app.replace('  content: document.querySelector("#content"),\n  status: document.querySelector("#status-pill"),', '  content: document.querySelector("#content"),\n  mainHeader: document.querySelector(".main-header"),\n  status: document.querySelector("#status-pill"),')

old_render_sidebar = '''function renderSidebar() {
  const isMobile = window.innerWidth <= 980;
  const open = isMobile ? state.mobileSidebar : state.sidebarOpen;
  if (els.sidebar) {
    els.sidebar.classList.toggle("is-mobile-open", isMobile && open);
  }
  if (els.sidebarBackdrop) {
    els.sidebarBackdrop.hidden = !(isMobile && open);
  }
  if (els.sidebarToggle) {
    els.sidebarToggle.setAttribute("aria-expanded", String(open));
  }
  if (!els.historyList) return;
  if (!state.history.length) {
    els.historyList.innerHTML = '<div class="sidebar-empty">Historie se začne ukládat po prvním prověření.</div>';
    bindHistoryButtons();
    return;
  }
  els.historyList.innerHTML = state.history.map((item) => `
    <button type="button" class="history-item" data-history-subjekt-id="${escapeHtml(item.subject_id)}" data-history-query="${escapeHtml(item.query || item.ico || item.name || "")}">
      <strong>${escapeHtml(item.name || "Firma")}</strong>
      <span>IČO ${escapeHtml(item.ico || "—")}${item.latest_year ? ` · ${escapeHtml(String(item.latest_year))}` : ""}</span>
      <span>${escapeHtml(item.analysis_overview || "Uložený profil firmy.")}</span>
    </button>
  `).join("");
  bindHistoryButtons();
}
'''
new_render_sidebar = '''function renderSidebar() {
  const isMobile = window.innerWidth <= 980;
  const open = isMobile ? state.mobileSidebar : state.sidebarOpen;
  if (els.sidebar) {
    els.sidebar.classList.toggle("is-mobile-open", isMobile && open);
  }
  if (els.sidebarBackdrop) {
    els.sidebarBackdrop.hidden = !(isMobile && open);
  }
  if (els.sidebarToggle) {
    els.sidebarToggle.setAttribute("aria-expanded", String(open));
  }
  if (!els.historyList) return;
  if (!state.history.length) {
    els.historyList.innerHTML = '<div class="sidebar-empty">Sdílená historie se začne plnit po prvním prověření.</div>';
    bindHistoryButtons();
    return;
  }
  els.historyList.innerHTML = state.history.map((item) => `
    <button type="button" class="history-item" data-history-subjekt-id="${escapeHtml(item.subject_id)}" data-history-query="${escapeHtml(item.query || item.ico || item.name || "")}">
      <strong>${escapeHtml(item.name || "Firma")}</strong>
    </button>
  `).join("");
  bindHistoryButtons();
}
'''
if old_render_sidebar not in app:
    raise SystemExit('renderSidebar not found')
app = app.replace(old_render_sidebar, new_render_sidebar)

insert_after_formatDateTime = '''function formatDateTime(value) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("cs-CZ", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}
'''
helpers = '''function formatDateTime(value) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("cs-CZ", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

function totalTokenCount(usage) {
  if (!usage) return null;
  const values = [
    usage.input_tokens,
    usage.output_tokens,
    usage.cache_creation_input_tokens,
    usage.cache_read_input_tokens,
  ].filter((value) => typeof value === "number" && Number.isFinite(value));
  if (!values.length) return null;
  return values.reduce((sum, value) => sum + value, 0);
}

function usageTags(profile) {
  const tags = [];
  if (profile.analysis_engine === "ai" && profile.analysis_model) {
    tags.push(`<span class="tag tag-ai">AI ${escapeHtml(profile.analysis_model)}</span>`);
  } else if (profile.analysis_engine === "fallback") {
    tags.push('<span class="tag tag-muted">Bez AI vrstvy</span>');
  }
  const tokens = totalTokenCount(profile.analysis_usage);
  if (tokens !== null) {
    tags.push(`<span class="tag tag-muted">${escapeHtml(new Intl.NumberFormat("cs-CZ").format(tokens))} tokenů</span>`);
  }
  tags.push(`<span class="tag tag-muted">${escapeHtml(profile.cache_status === "cached" ? "mezipaměť" : "čerstvá extrakce")}</span>`);
  return tags.join("");
}

function summaryListView(items) {
  if (!(items || []).length) {
    return '<div class="info-row"><strong>Shrnutí</strong><span>Z veřejných podkladů zatím nevyšlo dost spolehlivých bodů.</span></div>';
  }
  return (items || []).map((item) => insightRow(item, "insight-row")).join("");
}
'''
if insert_after_formatDateTime not in app:
    raise SystemExit('formatDateTime block not found')
app = app.replace(insert_after_formatDateTime, helpers)

old_usage_rows = re.compile(r'''function usageRows\(profile\) \{.*?\n\}\n\nfunction documentLinks''', re.S)
new_usage_rows = '''function documentLinks(doc) {
  const links = [];
  if (doc.detail_url) links.push(`<a class="source-link" href="${escapeHtml(doc.detail_url)}" target="_blank" rel="noopener noreferrer">detail listiny</a>`);
  const downloadLinks = (doc.download_links || []).filter((item) => item && item.url);
  const pdfLinks = downloadLinks.filter((item) => item.is_pdf);
  if (pdfLinks.length) {
    pdfLinks.forEach((item, index) => {
      const proxyUrl = `${API}/api/document/resolve?detailUrl=${encodeURIComponent(doc.detail_url || "")}&index=${index}&prefer_pdf=true`;
      links.push(`<a class="source-link" href="${escapeHtml(proxyUrl)}" target="_blank" rel="noopener noreferrer">${escapeHtml(item.label || `PDF ${index + 1}`)}</a>`);
    });
  } else if (doc.pdf_url) {
    const fallbackUrl = `${API}/api/document/resolve?detailUrl=${encodeURIComponent(doc.detail_url || "")}&index=0&prefer_pdf=true`;
    links.push(`<a class="source-link" href="${escapeHtml(fallbackUrl)}" target="_blank" rel="noopener noreferrer">PDF</a>`);
  }
  return links.join("");
}

function documentLinks'''
app, count = old_usage_rows.subn(new_usage_rows, app, count=1)
if count != 1:
    raise SystemExit('usageRows block not replaced cleanly')
app = app.replace('function documentLinks(doc) {\n  const links = [];\n  if (doc.detail_url) links.push(`<a class="source-link" href="${escapeHtml(doc.detail_url)}" target="_blank" rel="noopener noreferrer">detail listiny</a>`);\n  const downloadLinks = (doc.download_links || []).filter((item) => item && item.url);\n  const pdfLinks = downloadLinks.filter((item) => item.is_pdf);\n  if (pdfLinks.length) {\n    pdfLinks.forEach((item, index) => {\n      const proxyUrl = `${API}/api/document/resolve?detailUrl=${encodeURIComponent(doc.detail_url || "")}&index=${index}&prefer_pdf=true`;\n      links.push(`<a class="source-link" href="${escapeHtml(proxyUrl)}" target="_blank" rel="noopener noreferrer">${escapeHtml(item.label || `PDF ${index + 1}`)}</a>`);\n    });\n  } else if (doc.pdf_url) {\n    const fallbackUrl = `${API}/api/document/resolve?detailUrl=${encodeURIComponent(doc.detail_url || "")}&index=0&prefer_pdf=true`;\n    links.push(`<a class="source-link" href="${escapeHtml(fallbackUrl)}" target="_blank" rel="noopener noreferrer">PDF</a>`);\n  }\n  return links.join("");\n}\n\nfunction documentLinks', 'function documentLinks')

old_profile_view = re.compile(r'''function profileView\(profile\) \{.*?\n\}\n\nfunction insightRow''', re.S)
new_profile_view = '''function profileView(profile) {
  const latest = (profile.financial_timeline || []).slice(-1)[0];
  const previous = (profile.financial_timeline || []).slice(-2)[0];
  const yoy = latest && previous ? ((latest.revenue && previous.revenue) ? (((latest.revenue - previous.revenue) / Math.abs(previous.revenue)) * 100) : null) : null;

  return `
    <section class="profile">
      <div class="profile-hero">
        <article class="card profile-main-card">
          <div class="company-headline">
            <div>
              <div class="eyebrow-line">
                <span class="eyebrow-badge">Profil firmy</span>
                <span class="eyebrow-subtle">IČO ${escapeHtml(profile.ico || "—")}</span>
              </div>
              <h2>${escapeHtml(profile.name)}</h2>
            </div>
            <div class="tag-stack">
              <span class="tag">justice.cz</span>
              <span class="tag">Veřejný rejstřík</span>
              <span class="tag">Sbírka listin</span>
              ${usageTags(profile)}
            </div>
          </div>
          <div class="kpi-grid">
            <div class="kpi">
              <span class="kpi-label">Tržby naposled</span>
              <div class="kpi-value">${formatMillion(latest?.revenue)}</div>
              <div class="kpi-sub">${latest ? `rok ${latest.year}` : "bez dat"}</div>
            </div>
            <div class="kpi">
              <span class="kpi-label">Aktiva</span>
              <div class="kpi-value">${formatMillion(latest?.assets)}</div>
              <div class="kpi-sub">${latest ? `rok ${latest.year}` : "bez dat"}</div>
            </div>
            <div class="kpi">
              <span class="kpi-label">Čistý výsledek</span>
              <div class="kpi-value">${formatMillion(latest?.net_profit)}</div>
              <div class="kpi-sub">${latest ? `marže ${formatPct(latest.net_margin_pct)}` : "bez dat"}</div>
            </div>
            <div class="kpi">
              <span class="kpi-label">Růst tržeb</span>
              <div class="kpi-value">${formatPct(yoy)}</div>
              <div class="kpi-sub">${latest && previous ? `${previous.year} → ${latest.year}` : "bez srovnání"}</div>
            </div>
          </div>
          <div class="kpi-grid kpi-grid-secondary">
            <div class="kpi">
              <span class="kpi-label">Vlastní kapitál / aktiva</span>
              <div class="kpi-value">${formatPct(latest?.equity_ratio_pct)}</div>
              <div class="kpi-sub">${latest ? `rok ${latest.year}` : "bez dat"}</div>
            </div>
            <div class="kpi">
              <span class="kpi-label">Dluh</span>
              <div class="kpi-value">${formatMillion(latest?.debt)}</div>
              <div class="kpi-sub">${latest ? `rok ${latest.year}` : "bez dat"}</div>
            </div>
          </div>
          <div class="analysis-lead" style="margin-top:12px;">
            <div class="analysis-header-row">
              <div>
                <h3>AI shrnutí</h3>
              </div>
              <div class="action-cluster">
                <button class="retry-btn rerun-btn" type="button" data-rerun-subjekt-id="${escapeHtml(profile.subject_id)}">Spustit znovu</button>
              </div>
            </div>
            <div class="list-grid summary-bullets">
              ${summaryListView(profile.insight_summary || [])}
            </div>
            <div class="data-note summary-note" style="margin-top:10px;">${escapeHtml(profile.analysis_overview || "Shrnutí zatím není k dispozici.")}</div>
            <div class="data-note" style="margin-top:10px;">${escapeHtml(profile.data_quality_note || "Kvalita dat závisí na veřejných PDF a jejich čitelnosti.")}</div>
          </div>
        </article>
        <article class="card praskac-card">
          <div class="praskac-title">
            <h3>Práskač</h3>
            <span class="praskac-badge">jen veřejné signály</span>
          </div>
          <div class="list-grid">
            ${(profile.praskac || []).map((item) => insightRow(item, "praskac-row")).join("")}
          </div>
        </article>
      </div>

      <article class="card chart-card">
        <h3>Finanční vývoj</h3>
        <div class="small-note">Jen roky, kde šlo z veřejných PDF vytáhnout čísla dostatečně spolehlivě.</div>
        <div style="margin-top: 14px;"><canvas id="finance-chart" aria-label="Graf finančního vývoje"></canvas></div>
      </article>

      <article class="card">
        <h3>Tabulka let</h3>
        <div class="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Rok</th>
                <th>Tržby</th>
                <th>Čistý výsledek</th>
                <th>Aktiva</th>
                <th>Vlastní kapitál</th>
                <th>Dluh</th>
                <th>Marže</th>
              </tr>
            </thead>
            <tbody>
              ${(profile.financial_timeline || []).length
                ? profile.financial_timeline.map((row) => `
                  <tr>
                    <td>${escapeHtml(row.year)}</td>
                    <td>${formatMillion(row.revenue)}</td>
                    <td>${formatMillion(row.net_profit)}</td>
                    <td>${formatMillion(row.assets)}</td>
                    <td>${formatMillion(row.equity)}</td>
                    <td>${formatMillion(row.debt)}</td>
                    <td>${formatPct(row.net_margin_pct)}</td>
                  </tr>`).join("")
                : `<tr><td colspan="7">Z veřejných PDF se nepodařilo vytáhnout spolehlivou časovou řadu.</td></tr>`}
            </tbody>
          </table>
        </div>
      </article>

      <article class="card">
        <h3>Deep insights</h3>
        <div class="list-grid">
          ${(profile.deep_insights || []).map((item) => insightRow(item, "insight-row")).join("")}
        </div>
      </article>

      <div class="section-grid">
        <article class="card">
          <h3>Vedení</h3>
          <div class="list-grid">
            ${profile.executives?.length
              ? profile.executives.map((person) => `
                <div class="person-row">
                  <strong>${escapeHtml(person.name || "Neznámé jméno")}</strong>
                  <span>${escapeHtml(person.role || "Statutární role")}</span>
                  <span>${escapeHtml(person.raw || "")}</span>
                </div>`).join("")
              : `<div class="empty-state" style="padding: 16px;"><p>Ve veřejném výpisu jsem nenašel jasně čitelné osoby ve vedení.</p></div>`}
          </div>
        </article>

        <article class="card">
          <h3>Základní info</h3>
          <div class="list-grid">
            ${(profile.basic_info || []).map((item) => `
              <div class="info-row">
                <strong>${escapeHtml(item.label)}</strong>
                <span>${escapeHtml(item.value)}</span>
              </div>`).join("")}
          </div>
        </article>
      </div>

      <div class="section-grid">
        <article class="card">
          <h3>Vlastníci a orgány</h3>
          <div class="list-grid">
            ${profile.owners?.length
              ? profile.owners.map((owner) => `
                <div class="person-row">
                  <strong>${escapeHtml(owner.role || "Vlastnická položka")}</strong>
                  <span>${escapeHtml(owner.raw || "")}</span>
                </div>`).join("")
              : (profile.statutory_bodies || []).slice(0, 4).map((body) => `
                <div class="person-row">
                  <strong>${escapeHtml(body.title)}</strong>
                  <span>${escapeHtml((body.items || []).length)} položek ve veřejném výpisu</span>
                </div>`).join("") || `<div class="empty-state" style="padding: 16px;"><p>Vlastnické údaje nejsou v tomto výpisu jasně rozepsané.</p></div>`}
          </div>
        </article>

        <article class="card">
          <h3>Relevantní listiny</h3>
          <div class="list-grid">
            ${(profile.financial_documents || []).map((doc) => `
              <div class="doc-row">
                <strong>${escapeHtml(doc.document_number || "Listina")}</strong>
                <span>${escapeHtml(doc.type || "")}</span>
                <span>Rok ${escapeHtml((doc.years || ["?"])[0])} · ${escapeHtml(doc.extraction_mode || "?")} · ${escapeHtml(String(doc.page_count || doc.pages || "?"))} stran</span>
                <span>
                  ${documentLinks(doc)}
                </span>
              </div>`).join("")}
          </div>
        </article>
      </div>

      <div class="section-grid section-grid-compact-3">
        <article class="card">
          <h3>Prověřit</h3>
          <div class="list-grid">
            ${externalChecksView(profile)}
          </div>
        </article>
        <article class="card">
          <h3>Historické signály</h3>
          <div class="list-grid">
            ${historySignalRows(profile.history_signals || {})}
          </div>
        </article>

        <article class="card">
          <h3>Pokrytí dat</h3>
          <div class="list-grid">
            ${coverageRows(profile)}
          </div>
        </article>
      </div>

      <article class="card">
        <h3>Zdroje</h3>
        <div class="sources">
          ${Object.entries(profile.source_links || {})
            .filter(([, url]) => !!url)
            .map(([label, url]) => `<a class="source-link" href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(sourceLabel(label))}</a>`)
            .join("")}
        </div>
        <div class="footer-note">
          <span>Screening jen z veřejných podkladů justice.cz. Není to právní ani investiční doporučení.</span>
          <a href="https://www.perplexity.ai/computer" target="_blank" rel="noopener noreferrer">Created with Perplexity Computer</a>
        </div>
      </article>
    </section>`;
}

function insightRow'''
app, count = old_profile_view.subn(new_profile_view, app, count=1)
if count != 1:
    raise SystemExit('profileView block not replaced')

insert_load_snapshot_after = '''async function loadHistory() {
  try {
    const data = await fetchJson(`${API}/api/history`, "Nepodařilo se načíst historii.");
    return data.items || [];
  } catch {
    return [];
  }
}
'''
load_snapshot_block = '''async function loadHistory() {
  try {
    const data = await fetchJson(`${API}/api/history`, "Nepodařilo se načíst historii.");
    return data.items || [];
  } catch {
    return [];
  }
}

async function loadCompanySnapshot(subjektId, forceRefresh = false) {
  const url = `${API}/api/company?subjektId=${encodeURIComponent(subjektId)}&q=${encodeURIComponent(state.query || "")}${forceRefresh ? "&refresh=true" : ""}`;
  return fetchJson(url, "Nepodařilo se načíst detail firmy.");
}
'''
if insert_load_snapshot_after not in app:
    raise SystemExit('loadHistory block not found')
app = app.replace(insert_load_snapshot_after, load_snapshot_block)

old_handle_pick = '''async function handlePick(subjektId, options = {}) {
  const forceRefresh = !!options.forceRefresh;
  state.selectedMatch = subjektId;
  state.loading = true;
  state.searching = false;
  state.error = null;
  state.profile = null;
  state.preview = null;
  state.statusLog = [forceRefresh ? "Spouštím novou extrakci" : "Otevírám detail firmy"];
  render();
  try {
    state.profile = await loadCompanyStream(subjektId, forceRefresh);
    state.loading = false;
    state.matches = [];
    state.preview = null;
    state.statusLog = [];
    state.history = await loadHistory();
    render();
  } catch (error) {
    state.loading = false;
    state.preview = null;
    state.statusLog = [];
    state.error = error.message || "Nepodařilo se načíst detail firmy.";
    render();
  }
}
'''
new_handle_pick = '''async function handlePick(subjektId, options = {}) {
  const forceRefresh = !!options.forceRefresh;
  state.selectedMatch = subjektId;
  state.loading = true;
  state.searching = false;
  state.error = null;
  state.profile = null;
  state.preview = null;
  state.statusLog = [forceRefresh ? "Spouštím novou extrakci" : "Otevírám detail firmy"];
  render();
  try {
    try {
      state.profile = await loadCompanyStream(subjektId, forceRefresh);
    } catch (streamError) {
      state.statusLog = [...state.statusLog, "Stream vypadl, zkouším záložní načtení profilu"].slice(-10);
      render();
      state.profile = await loadCompanySnapshot(subjektId, forceRefresh);
    }
    state.loading = false;
    state.matches = [];
    state.preview = null;
    state.statusLog = [];
    state.history = await loadHistory();
    render();
  } catch (error) {
    state.loading = false;
    state.preview = null;
    state.statusLog = [];
    state.error = error.message || "Nepodařilo se načíst detail firmy.";
    render();
  }
}
'''
if old_handle_pick not in app:
    raise SystemExit('handlePick block not found')
app = app.replace(old_handle_pick, new_handle_pick)

append_js = '''

function applyHeaderVisibility() {
  if (!els.appShell) return;
  els.appShell.classList.toggle("header-hidden", state.headerHidden);
}

function handleScrollVisibility(scrollTop) {
  const nextTop = Math.max(0, scrollTop || 0);
  const delta = nextTop - state.lastScrollTop;
  if (nextTop < 24) {
    state.headerHidden = false;
  } else if (delta > 10) {
    state.headerHidden = true;
  } else if (delta < -8) {
    state.headerHidden = false;
  }
  state.lastScrollTop = nextTop;
  applyHeaderVisibility();
}

els.content?.addEventListener("scroll", () => {
  if (window.innerWidth > 980) {
    handleScrollVisibility(els.content.scrollTop);
  }
});

window.addEventListener("scroll", () => {
  if (window.innerWidth <= 980) {
    handleScrollVisibility(window.scrollY || document.documentElement.scrollTop || 0);
  }
}, { passive: true });

window.visualViewport?.addEventListener("resize", () => {
  document.documentElement.style.setProperty("--vvh", `${window.visualViewport.height}px`);
});

if (window.visualViewport) {
  document.documentElement.style.setProperty("--vvh", `${window.visualViewport.height}px`);
}
'''
if 'render();\n' not in app[-40:]:
    raise SystemExit('Unexpected app.js ending')
app = app + append_js

index_html = index_html.replace('content="width=device-width, initial-scale=1.0"', 'content="width=device-width, initial-scale=1, viewport-fit=cover, interactive-widget=resizes-content"')
index_html = index_html.replace('Historie prověření</h3>\n            <span class="section-note">rychlý návrat</span>', 'Historie prověření</h3>\n            <span class="section-note">sdílené pro všechny</span>')
index_html = index_html.replace('Historie se ukládá pro rychlý návrat k už prověřeným firmám.', 'Sdílená historie všech prověřených firem pro rychlý návrat a porovnání.')
index_html = index_html.replace('Když bude více shod, nabídnu výběr správné firmy. Vyhledávání zůstává vždy nahoře i na mobilu.', 'Když bude více shod, nabídnu výběr správné firmy. Při scrollu se horní lišta schová, při návratu zase vyjede.')

style += '''

.history-item {
  gap: 0;
  padding: 10px 12px;
  min-height: auto;
  box-shadow: none;
}

.history-item span {
  display: none;
}

.history-item strong {
  font-size: 0.95rem;
  line-height: 1.35;
}

.main-header {
  max-height: 240px;
  overflow: clip;
  transition: max-height 220ms ease, padding 220ms ease, opacity 220ms ease, transform 220ms ease, border-color 220ms ease;
}

.app-shell.header-hidden .main-header {
  max-height: 0;
  padding-top: 0;
  padding-bottom: 0;
  border-bottom-color: transparent;
  opacity: 0;
  transform: translateY(-12px);
}

.company-headline,
.tag-stack,
.sources,
.action-cluster,
.footer-note,
.profile,
.profile-hero,
.card,
.kpi,
.main,
.main-header,
.sidebar,
.header-copy,
.analysis-header-row {
  min-width: 0;
}

.tag,
.eyebrow-subtle,
.source-link,
.praskac-badge {
  white-space: normal;
  overflow-wrap: anywhere;
  text-align: left;
}

.summary-bullets .insight-row {
  background: #f7fafc;
}

.summary-note {
  color: var(--text);
}

.profile-main-card {
  display: grid;
  gap: 12px;
}

input,
button,
select,
textarea {
  font-size: 16px;
}

html,
body {
  width: 100%;
  overflow-x: hidden;
}

@media (max-width: 980px) {
  .header-actions {
    flex-direction: row;
    align-items: center;
    justify-content: space-between;
  }

  .header-actions > * {
    width: auto;
  }

  .kpi-grid,
  .kpi-grid-secondary {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .main-header {
    position: sticky;
    top: 0;
  }
}

@media (max-width: 520px) {
  .top-search-grid {
    grid-template-columns: 1fr;
  }

  .kpi-value {
    font-size: clamp(0.98rem, 0.9rem + 0.7vw, 1.22rem);
  }
}
'''

server_path.write_text(server, encoding='utf-8')
app_path.write_text(app, encoding='utf-8')
style_path.write_text(style, encoding='utf-8')
index_path.write_text(index_html, encoding='utf-8')
print('update_batch3 applied')
