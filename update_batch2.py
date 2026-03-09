from pathlib import Path
import re

root = Path('/home/user/workspace/justice-praskac')
server = root / 'server.py'
app = root / 'app.js'
html = root / 'index.html'
css = root / 'style.css'

server_text = server.read_text(encoding='utf-8')
app_text = app.read_text(encoding='utf-8')
html_text = html.read_text(encoding='utf-8')
css_text = css.read_text(encoding='utf-8')

server_text = server_text.replace(
'''def fetch_binary(url: str, path: Path) -> Path:
    if path.exists() and path.stat().st_size > 0:
        return path
    response = SESSION.get(url, timeout=120)
    response.raise_for_status()
    path.write_bytes(response.content)
    return path
''',
'''def response_is_pdf(response: requests.Response) -> bool:
    content_type = (response.headers.get("content-type") or "").lower()
    if "application/pdf" in content_type:
        return True
    return response.content[:4] == b"%PDF"


def resolve_live_download_url(url: str) -> str | None:
    try:
        response = SESSION.get(url, timeout=45)
        response.raise_for_status()
    except Exception:
        return None
    if response_is_pdf(response):
        return url
    return None


def fetch_binary(url: str, path: Path) -> Path:
    if path.exists() and path.stat().st_size > 0:
        return path
    response = SESSION.get(url, timeout=120)
    response.raise_for_status()
    if not response_is_pdf(response):
        raise ValueError(f"URL did not return a PDF: {url}")
    path.write_bytes(response.content)
    return path
''')

server_text = server_text.replace(
'''def fetch_extract(subjekt_id: str, typ: str) -> dict[str, Any]:
    cache_name = f"extract_{subjekt_id}_{typ.lower()}"
    cached = load_json_cache(cache_name, 60 * 60 * 24)
    if cached is not None:
        return cached
    url = f"{BASE_UI}rejstrik-firma.vysledky?subjektId={subjekt_id}&typ={typ}"
    parsed = parse_extract_rows(fetch_text(url))
    parsed["url"] = url
    save_json_cache(cache_name, parsed)
    return parsed
''',
'''def fetch_extract(subjekt_id: str, typ: str, force_refresh: bool = False) -> dict[str, Any]:
    cache_name = f"extract_{subjekt_id}_{typ.lower()}"
    if not force_refresh:
        cached = load_json_cache(cache_name, 60 * 60 * 24)
        if cached is not None:
            return cached
    url = f"{BASE_UI}rejstrik-firma.vysledky?subjektId={subjekt_id}&typ={typ}"
    parsed = parse_extract_rows(fetch_text(url))
    parsed["url"] = url
    save_json_cache(cache_name, parsed)
    return parsed
''')

server_text = server_text.replace(
'''def parse_document_detail(url: str) -> dict[str, Any]:
    cache_name = f"doc_detail_{slug_hash(url)}"
    cached = load_json_cache(cache_name, 60 * 60 * 24 * 7)
    if cached is not None:
        return cached
    html = fetch_text(url)
    soup = BeautifulSoup(html, "html.parser")
    pdf_link = soup.find("a", href=re.compile(r"/ias/content/download\?id="))
    pdf_url = urljoin(BASE_SITE, pdf_link["href"]) if pdf_link else None
    pdf_name = norm_text(pdf_link.get_text(" ", strip=True)) if pdf_link else None
    result = {
        "detail_url": url,
        "pdf_url": pdf_url,
        "pdf_name": pdf_name,
    }
    save_json_cache(cache_name, result)
    return result
''',
'''def parse_document_detail(url: str, force_refresh: bool = False) -> dict[str, Any]:
    cache_name = f"doc_detail_{slug_hash(url)}"
    if not force_refresh:
        cached = load_json_cache(cache_name, 60 * 60 * 24 * 7)
        if cached is not None:
            live_url = cached.get("pdf_url")
            if live_url and resolve_live_download_url(live_url):
                return cached
    html = fetch_text(url)
    soup = BeautifulSoup(html, "html.parser")
    downloads: list[dict[str, Any]] = []
    for link in soup.find_all("a", href=re.compile(r"/ias/content/download\?id=")):
        download_url = urljoin(BASE_SITE, link["href"])
        label = norm_text(link.get_text(" ", strip=True))
        is_pdf = ".pdf" in label.lower()
        downloads.append(
            {
                "label": label,
                "url": download_url,
                "is_pdf": is_pdf,
            }
        )
    pdf_candidates = [item for item in downloads if item.get("is_pdf")]
    pdf_url = None
    pdf_name = None
    for candidate in pdf_candidates:
        if resolve_live_download_url(candidate["url"]):
            pdf_url = candidate["url"]
            pdf_name = candidate["label"]
            break
    if pdf_url is None and pdf_candidates:
        pdf_url = pdf_candidates[0]["url"]
        pdf_name = pdf_candidates[0]["label"]
    result = {
        "detail_url": url,
        "pdf_url": pdf_url,
        "pdf_name": pdf_name,
        "download_links": downloads,
    }
    save_json_cache(cache_name, result)
    return result
''')

server_text = server_text.replace(
'''def parse_document_list(subjekt_id: str) -> list[dict[str, Any]]:
    cache_name = f"docs_{subjekt_id}"
    cached = load_json_cache(cache_name, 60 * 60 * 24)
    if cached is not None:
        return cached
    url = f"{BASE_UI}vypis-sl-firma?subjektId={subjekt_id}"
''',
'''def parse_document_list(subjekt_id: str, force_refresh: bool = False) -> list[dict[str, Any]]:
    cache_name = f"docs_{subjekt_id}"
    if not force_refresh:
        cached = load_json_cache(cache_name, 60 * 60 * 24)
        if cached is not None:
            return cached
    url = f"{BASE_UI}vypis-sl-firma?subjektId={subjekt_id}"
''')

server_text = server_text.replace(
'''def pick_recent_financial_docs(docs: list[dict[str, Any]], max_years: int = 5) -> list[dict[str, Any]]:
''',
'''def pick_recent_financial_docs(docs: list[dict[str, Any]], max_years: int = 5, force_refresh_details: bool = False) -> list[dict[str, Any]]:
''')
server_text = server_text.replace(
'''        enriched = dict(doc)
        enriched.update(parse_document_detail(doc["detail_url"]))
''',
'''        enriched = dict(doc)
        enriched.update(parse_document_detail(doc["detail_url"], force_refresh=force_refresh_details))
''')

server_text = server_text.replace(
'''        doc_copy["metrics_found"] = sorted(list(extracted.get("found_metrics", {}).keys()))
        processed_docs.append(doc_copy)
''',
'''        doc_copy["metrics_found"] = sorted(list(extracted.get("found_metrics", {}).keys()))
        doc_copy["download_links"] = doc.get("download_links") or []
        processed_docs.append(doc_copy)
''')

server_text = server_text.replace(
'''    response = client.messages.create(
        model=AI_MODEL,
        max_tokens=1800,
        temperature=0.2,
        messages=[{"role": "user", "content": prompt}],
    )
''',
'''    response = client.messages.create(
        model=AI_MODEL,
        max_tokens=1800,
        temperature=0.2,
        messages=[{"role": "user", "content": prompt}],
    )
    usage = getattr(response, "usage", None)
    usage_payload = {
        "provider": "anthropic",
        "input_tokens": getattr(usage, "input_tokens", None),
        "output_tokens": getattr(usage, "output_tokens", None),
        "cache_creation_input_tokens": getattr(usage, "cache_creation_input_tokens", None),
        "cache_read_input_tokens": getattr(usage, "cache_read_input_tokens", None),
        "credits": None,
        "credits_note": "Přesné kredity nejsou z API dostupné.",
    }
''')
server_text = server_text.replace(
'''    return {
        "analysis_engine": "ai",
        "analysis_model": AI_MODEL,
''',
'''    return {
        "analysis_engine": "ai",
        "analysis_model": AI_MODEL,
        "analysis_usage": usage_payload,
''')

server_text = server_text.replace(
'''                "analysis_model": None,
                "analysis_overview": "Shrnutí běží bez AI vrstvy. Níže je pravidlový výstup z veřejných podkladů justice.cz.",
''',
'''                "analysis_model": None,
                "analysis_usage": None,
                "analysis_overview": "Shrnutí běží bez AI vrstvy. Níže je pravidlový výstup z veřejných podkladů justice.cz.",
''', 1)
server_text = server_text.replace(
'''            "analysis_model": None,
            "analysis_overview": "AI vrstva je vypnutá. Níže je pravidlový výstup z veřejných podkladů justice.cz.",
''',
'''            "analysis_model": None,
            "analysis_usage": None,
            "analysis_overview": "AI vrstva je vypnutá. Níže je pravidlový výstup z veřejných podkladů justice.cz.",
''', 1)
server_text = server_text.replace(
'''        "analysis_model": ai_analysis.get("analysis_model"),
        "analysis_overview": ai_analysis["analysis_overview"],
''',
'''        "analysis_model": ai_analysis.get("analysis_model"),
        "analysis_usage": ai_analysis.get("analysis_usage"),
        "analysis_overview": ai_analysis["analysis_overview"],
''', 1)

server_text = server_text.replace(
'''def build_company_profile(subjekt_id: str, visitor_id: str | None = None, query: str | None = None) -> dict[str, Any]:
    cache_name = f"company_profile_{PROFILE_CACHE_VERSION}_{subjekt_id}"
    cached = load_json_cache(cache_name, PROFILE_CACHE_TTL_SECONDS)
    if cached is not None:
        save_history_entry(visitor_id, cached, query=query)
        return cached

    current_extract = fetch_extract(subjekt_id, "PLATNY")
    full_extract = fetch_extract(subjekt_id, "UPLNY")
    docs = parse_document_list(subjekt_id)
    relevant_docs = pick_recent_financial_docs(docs, max_years=5)
''',
'''def build_company_profile(subjekt_id: str, visitor_id: str | None = None, query: str | None = None, force_refresh: bool = False) -> dict[str, Any]:
    cache_name = f"company_profile_{PROFILE_CACHE_VERSION}_{subjekt_id}"
    if not force_refresh:
        cached = load_json_cache(cache_name, PROFILE_CACHE_TTL_SECONDS)
        if cached is not None:
            cached["cache_status"] = "cached"
            save_history_entry(visitor_id, cached, query=query)
            return cached

    current_extract = fetch_extract(subjekt_id, "PLATNY", force_refresh=force_refresh)
    full_extract = fetch_extract(subjekt_id, "UPLNY", force_refresh=force_refresh)
    docs = parse_document_list(subjekt_id, force_refresh=force_refresh)
    relevant_docs = pick_recent_financial_docs(docs, max_years=5, force_refresh_details=force_refresh)
''')
server_text = server_text.replace(
'''        "generated_at": datetime.now().astimezone().isoformat(),
    }
''',
'''        "generated_at": datetime.now().astimezone().isoformat(),
        "cache_status": "fresh" if force_refresh else "fresh",
    }
''', 1)

server_text = server_text.replace(
'''def api_company(request: Request, subjekt_id: str = Query(..., alias="subjektId"), q: str | None = Query(None)) -> dict[str, Any]:
    visitor_id = request.headers.get("X-Visitor-Id")
    profile = build_company_profile(subjekt_id, visitor_id=visitor_id, query=q)
    return profile
''',
'''def api_company(request: Request, subjekt_id: str = Query(..., alias="subjektId"), q: str | None = Query(None), refresh: bool = Query(False)) -> dict[str, Any]:
    visitor_id = request.headers.get("X-Visitor-Id")
    profile = build_company_profile(subjekt_id, visitor_id=visitor_id, query=q, force_refresh=refresh)
    return profile
''')

server_text = server_text.replace(
'''def api_company_stream(request: Request, subjekt_id: str = Query(..., alias="subjektId"), q: str | None = Query(None)) -> StreamingResponse:
''',
'''def api_company_stream(request: Request, subjekt_id: str = Query(..., alias="subjektId"), q: str | None = Query(None), refresh: bool = Query(False)) -> StreamingResponse:
''')
server_text = server_text.replace(
'''        cached = load_json_cache(cache_name, PROFILE_CACHE_TTL_SECONDS)
        if cached is not None:
            save_history_entry(visitor_id, cached, query=q)
            yield sse_event("status", {"label": "Načítám uložený profil z mezipaměti"})
            yield sse_event("result", cached)
            return

        yield sse_event("status", {"label": "Otevírám aktuální výpis firmy"})
        current_extract = fetch_extract(subjekt_id, "PLATNY")
''',
'''        if not refresh:
            cached = load_json_cache(cache_name, PROFILE_CACHE_TTL_SECONDS)
            if cached is not None:
                cached["cache_status"] = "cached"
                save_history_entry(visitor_id, cached, query=q)
                yield sse_event("status", {"label": "Načítám uložený profil z mezipaměti"})
                yield sse_event("result", cached)
                return

        yield sse_event("status", {"label": "Spouštím novou extrakci z veřejných podkladů" if refresh else "Otevírám aktuální výpis firmy"})
        current_extract = fetch_extract(subjekt_id, "PLATNY", force_refresh=refresh)
''')
server_text = server_text.replace(
'''        full_extract = fetch_extract(subjekt_id, "UPLNY")
''',
'''        full_extract = fetch_extract(subjekt_id, "UPLNY", force_refresh=refresh)
''', 1)
server_text = server_text.replace(
'''        docs = parse_document_list(subjekt_id)
        relevant_docs = pick_recent_financial_docs(docs, max_years=5)
''',
'''        docs = parse_document_list(subjekt_id, force_refresh=refresh)
        relevant_docs = pick_recent_financial_docs(docs, max_years=5, force_refresh_details=refresh)
''', 1)
server_text = server_text.replace(
'''                    "analysis_model": None,
                    "analysis_overview": "Shrnutí běží bez AI vrstvy. Níže je pravidlový výstup z veřejných podkladů justice.cz.",
''',
'''                    "analysis_model": None,
                    "analysis_usage": None,
                    "analysis_overview": "Shrnutí běží bez AI vrstvy. Níže je pravidlový výstup z veřejných podkladů justice.cz.",
''', 1)
server_text = server_text.replace(
'''                "analysis_model": None,
                "analysis_overview": "AI vrstva je vypnutá. Níže je pravidlový výstup z veřejných podkladů justice.cz.",
''',
'''                "analysis_model": None,
                "analysis_usage": None,
                "analysis_overview": "AI vrstva je vypnutá. Níže je pravidlový výstup z veřejných podkladů justice.cz.",
''', 1)
server_text = server_text.replace(
'''            "analysis_model": ai_analysis.get("analysis_model"),
            "analysis_overview": ai_analysis["analysis_overview"],
''',
'''            "analysis_model": ai_analysis.get("analysis_model"),
            "analysis_usage": ai_analysis.get("analysis_usage"),
            "analysis_overview": ai_analysis["analysis_overview"],
''', 1)
server_text = server_text.replace(
'''            "generated_at": datetime.now().astimezone().isoformat(),
        }
''',
'''            "generated_at": datetime.now().astimezone().isoformat(),
            "cache_status": "fresh",
        }
''', 1)

app_text = app_text.replace(
'''  selectedMatch: null,
  sidebarOpen: true,
};
''',
'''  selectedMatch: null,
  sidebarOpen: window.innerWidth > 980,
  mobileSidebar: window.innerWidth > 980 ? false : false,
};
''')

app_text = app_text.replace(
'''  sidebar: document.querySelector("#sidebar"),
  sidebarToggle: document.querySelector("#sidebar-toggle"),
  historyList: document.querySelector("#history-list"),
};
''',
'''  sidebar: document.querySelector("#sidebar"),
  sidebarToggle: document.querySelector("#sidebar-toggle"),
  historyList: document.querySelector("#history-list"),
  sidebarBackdrop: document.querySelector("#sidebar-backdrop"),
};
''')

app_text = app_text.replace(
'''function renderSidebar() {
  if (els.sidebar) {
    els.sidebar.classList.toggle("is-collapsed", !state.sidebarOpen);
  }
  if (els.appShell) {
    els.appShell.classList.toggle("sidebar-collapsed", !state.sidebarOpen);
  }
  if (els.sidebarToggle) {
    els.sidebarToggle.setAttribute("aria-expanded", String(state.sidebarOpen));
    els.sidebarToggle.textContent = state.sidebarOpen ? "Skrýt panel" : "Zobrazit panel";
  }
''',
'''function renderSidebar() {
  const isMobile = window.innerWidth <= 980;
  const open = isMobile ? state.mobileSidebar : state.sidebarOpen;
  if (els.sidebar) {
    els.sidebar.classList.toggle("is-collapsed", !open);
    els.sidebar.classList.toggle("is-mobile-open", isMobile && open);
  }
  if (els.appShell) {
    els.appShell.classList.toggle("sidebar-collapsed", !open && !isMobile);
    els.appShell.classList.toggle("mobile-sidebar-open", isMobile && open);
  }
  if (els.sidebarBackdrop) {
    els.sidebarBackdrop.hidden = !(isMobile && open);
  }
  if (els.sidebarToggle) {
    els.sidebarToggle.setAttribute("aria-expanded", String(open));
    els.sidebarToggle.textContent = open ? "Skrýt panel" : "Zobrazit panel";
  }
''')

app_text = app_text.replace(
'''function profileView(profile) {
''',
'''function formatDateTime(value) {
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

function usageRows(profile) {
  const usage = profile.analysis_usage;
  const rows = [];
  rows.push(["Model", profile.analysis_model || (profile.analysis_engine === "ai" ? "AI" : "bez AI")]);
  rows.push(["Kredity", usage?.credits ?? "nedostupné"]);
  if (usage?.input_tokens !== undefined && usage?.input_tokens !== null) rows.push(["Vstupní tokeny", new Intl.NumberFormat("cs-CZ").format(usage.input_tokens)]);
  if (usage?.output_tokens !== undefined && usage?.output_tokens !== null) rows.push(["Výstupní tokeny", new Intl.NumberFormat("cs-CZ").format(usage.output_tokens)]);
  rows.push(["Režim", profile.cache_status === "cached" ? "mezipaměť" : "čerstvá extrakce"]);
  rows.push(["Vygenerováno", formatDateTime(profile.generated_at)]);
  return rows.map(([label, value]) => `
      <div class="info-row compact-row">
        <strong>${escapeHtml(label)}</strong>
        <span>${escapeHtml(String(value))}</span>
      </div>`).join("");
}

function documentLinks(doc) {
  const links = [];
  if (doc.detail_url) links.push(`<a class="source-link" href="${escapeHtml(doc.detail_url)}" target="_blank" rel="noopener noreferrer">detail listiny</a>`);
  const downloadLinks = (doc.download_links || []).filter((item) => item && item.url);
  const pdfLink = downloadLinks.find((item) => item.is_pdf) || (doc.pdf_url ? { url: doc.pdf_url, label: "PDF", is_pdf: true } : null);
  if (pdfLink) links.push(`<a class="source-link" href="${escapeHtml(pdfLink.url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(pdfLink.label || "PDF")}</a>`);
  return links.join("");
}

function externalChecksView(profile) {
  const checks = profile.external_checks?.checks || [];
  if (!checks.length) return `<div class="empty-state compact-empty"><p>Externí kontrola zatím není k dispozici.</p></div>`;
  return checks.map((item) => `
    <div class="${item.status === 'warning' ? 'praskac-row' : 'info-row'} compact-row">
      <strong>${escapeHtml(item.label)}</strong>
      <span>Aplikace: ${escapeHtml(formatMillion(item.app_value))} · Kontrola: ${escapeHtml(formatMillion(item.external_value))}</span>
      <span>${escapeHtml(item.detail || '')}</span>
    </div>`).join('');
}

function profileView(profile) {
''')

app_text = app_text.replace(
'''      <div class="profile-hero">
        <article class="card">
''',
'''      <div class="profile-hero compact-profile-hero">
        <article class="card hero-card-compact">
''')

app_text = app_text.replace(
'''          <div class="company-headline">
            <div>
              <h2>${escapeHtml(profile.name)}</h2>
              <p>IČO ${escapeHtml(profile.ico || "—")}</p>
            </div>
            <div class="tag-stack">
''',
'''          <div class="company-headline company-headline-compact">
            <div>
              <div class="eyebrow-line">
                <span class="eyebrow-badge">Profil firmy</span>
                <span class="eyebrow-subtle">IČO ${escapeHtml(profile.ico || "—")}</span>
              </div>
              <h2>${escapeHtml(profile.name)}</h2>
            </div>
            <div class="tag-stack">
''')

app_text = app_text.replace(
'''          <div class="analysis-lead">
            <h3>AI shrnutí</h3>
            <p>${escapeHtml(profile.analysis_overview || "Shrnutí zatím není k dispozici.")}</p>
            <div class="data-note">${escapeHtml(profile.data_quality_note || "Kvalita dat závisí na veřejných PDF a jejich čitelnosti.")}</div>
          </div>
''',
'''          <div class="analysis-lead compact-analysis-lead">
            <div class="analysis-header-row">
              <div>
                <h3>AI shrnutí</h3>
                <p>${escapeHtml(profile.analysis_overview || "Shrnutí zatím není k dispozici.")}</p>
              </div>
              <div class="action-cluster">
                <button class="retry-btn rerun-btn" type="button" data-rerun-subjekt-id="${escapeHtml(profile.subject_id)}">Spustit znovu</button>
              </div>
            </div>
            <div class="data-note">${escapeHtml(profile.data_quality_note || "Kvalita dat závisí na veřejných PDF a jejich čitelnosti.")}</div>
          </div>
''')

app_text = app_text.replace(
'''        <article class="card praskac-card">
''',
'''        <article class="card side-rail-card">
          <h3>AI a stav</h3>
          <div class="list-grid compact-grid">
            ${usageRows(profile)}
          </div>
          ${profile.analysis_usage?.credits_note ? `<div class="small-note usage-note">${escapeHtml(profile.analysis_usage.credits_note)}</div>` : ""}
        </article>
        <article class="card praskac-card">
''')

app_text = app_text.replace(
'''      <div class="section-grid">
        <article class="card">
          <h3>Historické signály</h3>
''',
'''      <div class="section-grid section-grid-compact-3">
        <article class="card">
          <h3>Prověřit</h3>
          <div class="list-grid compact-grid">
            ${externalChecksView(profile)}
          </div>
        </article>
        <article class="card">
          <h3>Historické signály</h3>
''')

app_text = app_text.replace(
'''                  <a class="source-link" href="${escapeHtml(doc.detail_url)}" target="_blank" rel="noopener noreferrer">detail listiny</a>
                  ${doc.pdf_url ? `<a class="source-link" href="${escapeHtml(doc.pdf_url)}" target="_blank" rel="noopener noreferrer">PDF</a>` : ""}
                </span>
''',
'''                  ${documentLinks(doc)}
                </span>
''')

app_text = app_text.replace(
'''async function loadCompanyStream(subjektId) {
  const url = `${API}/api/company/stream?subjektId=${encodeURIComponent(subjektId)}&q=${encodeURIComponent(state.query || "")}`;
''',
'''async function loadCompanyStream(subjektId, forceRefresh = false) {
  const url = `${API}/api/company/stream?subjektId=${encodeURIComponent(subjektId)}&q=${encodeURIComponent(state.query || "")}${forceRefresh ? "&refresh=true" : ""}`;
''')

app_text = app_text.replace(
'''async function handlePick(subjektId) {
''',
'''async function handlePick(subjektId, options = {}) {
  const forceRefresh = !!options.forceRefresh;
''')
app_text = app_text.replace(
'''  state.statusLog = ["Otevírám detail firmy"]; 
''',
'''  state.statusLog = [forceRefresh ? "Spouštím novou extrakci" : "Otevírám detail firmy"];
''')
app_text = app_text.replace(
'''    state.profile = await loadCompanyStream(subjektId);
''',
'''    state.profile = await loadCompanyStream(subjektId, forceRefresh);
''')

app_text = app_text.replace(
'''function bindRetry() {
  document.querySelector(".retry-btn")?.addEventListener("click", () => {
    if (state.query) handleSearch(state.query);
  });
}
''',
'''function bindRetry() {
  document.querySelector(".retry-btn:not(.rerun-btn)")?.addEventListener("click", () => {
    if (state.query) handleSearch(state.query);
  });
}

function bindRerunButtons() {
  document.querySelectorAll("[data-rerun-subjekt-id]").forEach((button) => {
    button.addEventListener("click", () => handlePick(button.dataset.rerunSubjektId, { forceRefresh: true }));
  });
}
''')
app_text = app_text.replace(
'''    els.content.innerHTML = profileView(state.profile);
    drawFinanceChart(state.profile.financial_timeline || []);
    return;
''',
'''    els.content.innerHTML = profileView(state.profile);
    bindRerunButtons();
    drawFinanceChart(state.profile.financial_timeline || []);
    return;
''')
app_text = app_text.replace(
'''els.sidebarToggle?.addEventListener("click", () => {
  state.sidebarOpen = !state.sidebarOpen;
  renderSidebar();
});
''',
'''els.sidebarToggle?.addEventListener("click", () => {
  if (window.innerWidth <= 980) state.mobileSidebar = !state.mobileSidebar;
  else state.sidebarOpen = !state.sidebarOpen;
  renderSidebar();
});

els.sidebarBackdrop?.addEventListener("click", () => {
  state.mobileSidebar = false;
  renderSidebar();
});

window.addEventListener("resize", () => {
  if (window.innerWidth > 980) {
    state.mobileSidebar = false;
  }
  renderSidebar();
});
''')
app_text = app_text.replace(
'''      handlePick(button.dataset.historySubjektId);
''',
'''      handlePick(button.dataset.historySubjektId);
      if (window.innerWidth <= 980) {
        state.mobileSidebar = false;
        renderSidebar();
      }
''')

html_text = html_text.replace(
'''    <div class="app-shell" id="app-shell">
''',
'''    <div class="sidebar-backdrop" id="sidebar-backdrop" hidden></div>
    <div class="app-shell" id="app-shell">
''')

css_text += '''

.sidebar-backdrop {
  position: fixed;
  inset: 0;
  background: rgba(4, 8, 14, 0.66);
  backdrop-filter: blur(2px);
  z-index: 25;
}

.compact-profile-hero {
  grid-template-columns: minmax(0, 1.35fr) minmax(240px, 0.65fr);
  align-items: start;
}

.hero-card-compact {
  padding: 20px;
}

.company-headline-compact {
  margin-bottom: 18px;
}

.eyebrow-line {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
}

.eyebrow-badge,
.eyebrow-subtle {
  display: inline-flex;
  align-items: center;
  border-radius: 999px;
  padding: 6px 10px;
  font-size: var(--text-xs);
  border: 1px solid var(--line);
}

.eyebrow-badge {
  background: rgba(91, 192, 168, 0.12);
  color: var(--accent-2);
}

.eyebrow-subtle {
  color: var(--muted);
  background: rgba(255,255,255,0.03);
}

.compact-analysis-lead {
  margin-top: 4px;
}

.analysis-header-row {
  display: flex;
  justify-content: space-between;
  gap: 16px;
  align-items: flex-start;
}

.analysis-header-row h3 {
  margin-bottom: 10px;
}

.analysis-header-row p {
  margin: 0;
}

.action-cluster {
  display: flex;
  gap: 10px;
  flex-wrap: wrap;
}

.side-rail-card {
  padding: 18px;
}

.side-rail-card h3,
.praskac-card h3 {
  margin-bottom: 14px;
}

.section-grid-compact-3 {
  grid-template-columns: repeat(3, minmax(0, 1fr));
}

.compact-empty {
  padding: 16px;
}

.usage-note {
  margin-top: 12px;
}

.info-row.compact-row,
.praskac-row.compact-row {
  padding: 12px 14px;
}

.card {
  padding: 20px;
}

.profile {
  gap: 18px;
}

.section-grid {
  gap: 18px;
}

.kpi-grid {
  gap: 12px;
}

.kpi {
  padding: 14px;
  border-radius: 16px;
}

.kpi-value {
  font-size: clamp(1.04rem, 0.96rem + 0.55vw, 1.55rem);
}

.main-header {
  padding: 18px 24px;
}

.main {
  padding: 20px 24px 28px;
}

.hero-state,
.empty-state,
.error-state,
.loading-state,
.match-state {
  padding: 24px;
}

.hero-state h2,
.empty-state h2,
.error-state h2,
.loading-state h2,
.match-state h2 {
  max-width: none;
  font-size: clamp(1.5rem, 1.2rem + 0.7vw, 2.3rem);
}

.search-panel {
  padding: 18px;
}

.search-row input {
  padding: 12px 14px;
}

.tag-stack {
  gap: 8px;
}

.tag {
  padding: 7px 10px;
  font-size: var(--text-xs);
}

.result-picker {
  padding: 16px;
}

@media (max-width: 1180px) {
  .section-grid-compact-3 {
    grid-template-columns: 1fr 1fr;
  }
}

@media (max-width: 980px) {
  body {
    overflow: auto;
  }

  .app-shell,
  .app-shell.sidebar-collapsed {
    grid-template-columns: 1fr;
    grid-template-rows: auto 1fr;
    height: auto;
    min-height: 100dvh;
  }

  .sidebar {
    position: fixed;
    top: 0;
    left: 0;
    bottom: 0;
    width: min(88vw, 360px);
    z-index: 30;
    border-right: 1px solid var(--line);
    border-bottom: 0;
    transform: translateX(-102%);
    opacity: 0;
    pointer-events: none;
    padding: 18px;
  }

  .sidebar.is-mobile-open {
    transform: translateX(0);
    opacity: 1;
    pointer-events: auto;
  }

  .sidebar.is-collapsed {
    transform: translateX(-102%);
    opacity: 0;
    pointer-events: none;
    padding: 18px;
    border-color: var(--line);
  }

  .main-header,
  .main {
    grid-column: 1;
  }

  .main-header {
    position: sticky;
    top: 0;
  }

  .main {
    overflow: visible;
    padding: 16px;
  }

  .compact-profile-hero,
  .profile-hero,
  .section-grid,
  .section-grid-compact-3,
  .hero-grid,
  .kpi-grid,
  .preview-grid {
    grid-template-columns: 1fr;
  }

  .header-row,
  .analysis-header-row,
  .company-headline,
  .preview-head,
  .footer-note {
    flex-direction: column;
    align-items: stretch;
  }

  .header-actions {
    width: 100%;
    justify-content: stretch;
  }

  .header-actions > * {
    width: 100%;
  }

  .sidebar-toggle,
  .status-pill,
  .search-actions button,
  .rerun-btn {
    min-height: 44px;
  }

  .chart-card canvas {
    height: 240px;
  }
}

@media (max-width: 760px) {
  .main-header,
  .main {
    padding-left: 12px;
    padding-right: 12px;
  }

  .hero-state,
  .empty-state,
  .error-state,
  .loading-state,
  .match-state,
  .card,
  .search-panel {
    padding: 16px;
  }

  .table-wrap th,
  .table-wrap td {
    padding: 10px 12px;
  }

  .company-headline h2 {
    font-size: clamp(1.45rem, 1.2rem + 1.2vw, 1.95rem);
  }
}
'''

server.write_text(server_text, encoding='utf-8')
app.write_text(app_text, encoding='utf-8')
html.write_text(html_text, encoding='utf-8')
css.write_text(css_text, encoding='utf-8')
print('updated')
