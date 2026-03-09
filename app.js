/* ============================================================
   Justice Práskač — Frontend Application
   ============================================================ */

const API = "";

// ---- State ----
const state = {
  query: "",
  loading: false,
  profile: null,
  preview: null,
  history: [],
  statusLog: [],
  error: null,
  selectedMatch: null,
  autocompleteResults: [],
  autocompleteOpen: false,
  autocompleteLoading: false,
  drawerOpen: false,
  headerHidden: false,
  lastScrollY: 0,
  expandedAccordions: new Set(),
  expandedPanels: new Set(),
};

// ---- DOM refs (populated on DOMContentLoaded) ----
let $content, $form, $input, $submit, $statusDot, $statusText, $header,
    $railHistory, $drawerHistory, $drawer;

let _acTimer = null;
let _acController = null;

// ---- Utilities ----
const esc = (v) =>
  String(v ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");

const _fmtCZ0 = new Intl.NumberFormat("cs-CZ", { minimumFractionDigits: 0, maximumFractionDigits: 0 });
const _fmtCZ2 = new Intl.NumberFormat("cs-CZ", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const _fmtPct1 = new Intl.NumberFormat("cs-CZ", { maximumFractionDigits: 1 });
const _fmtDateCZ = new Intl.DateTimeFormat("cs-CZ", { day: "2-digit", month: "2-digit", year: "numeric" });

const fmtM = (v) => {
  if (v == null || Number.isNaN(v)) return "—";
  return (Math.abs(v) >= 100 ? _fmtCZ0 : _fmtCZ2).format(v) + " mil.";
};

const fmtPct = (v) => {
  if (v == null || Number.isNaN(v)) return "—";
  return _fmtPct1.format(v) + " %";
};

const fmtDate = (v) => {
  if (!v) return "—";
  const d = new Date(v);
  if (Number.isNaN(d.getTime())) return v;
  return _fmtDateCZ.format(d);
};

const fmtRelative = (v) => {
  if (!v) return "";
  const d = new Date(v);
  if (Number.isNaN(d.getTime())) return "";
  const diff = Date.now() - d.getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 60) return `před ${mins} min`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `před ${hrs} h`;
  const days = Math.floor(hrs / 24);
  return `před ${days} d`;
};

function getInfo(profile, key) {
  const item = (profile.basic_info || []).find(
    (i) => i.label.toLowerCase().includes(key.toLowerCase())
  );
  return item ? item.value : null;
}

function metricLabel(m) {
  return { revenue: "tržby", operating_profit: "provozní zisk", net_profit: "čistý zisk", assets: "aktiva", equity: "vlastní kapitál", liabilities: "cizí zdroje", debt: "dluh" }[m] || m;
}

// ---- Shared severity detection ----
function severityOf(item) {
  const t = ((item.title || "") + " " + (item.detail || "")).toLowerCase();
  if (t.includes("vysoký") || t.includes("ztráta") || t.includes("problém") || t.includes("rizik") || t.includes("velký") || t.includes("významn")) return "high";
  if (t.includes("střední") || t.includes("zpoždění") || t.includes("chybí") || t.includes("mezera") || t.includes("pokles")) return "medium";
  return "low";
}

const SEV_DOT = { high: "bg-red-500", medium: "bg-amber-400", low: "bg-slate-300" };

// ---- Shared Tailwind class strings ----
const CLS_CARD = "bg-white rounded-xl ring-1 ring-slate-200/60 shadow-sm";
const CLS_SECTION_HEADING = "text-sm font-semibold text-slate-900";
const CLS_LABEL_XS = "text-[10px] text-slate-400 uppercase tracking-wider";
const CLS_BADGE_SM = "inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-medium ring-1";
const SEV_TONE = { high: "bg-red-400", medium: "bg-amber-500", low: "bg-neutral-500" };
const MODE_COLOR = { digital: "text-emerald-700 bg-emerald-50 ring-emerald-200/60", ocr: "text-amber-700 bg-amber-50 ring-amber-200/60", mixed: "text-slate-600 bg-slate-50 ring-slate-200/60" };

// ---- History rendering ----
function historyItemHtml(item) {
  return `<button type="button" data-pick-id="${esc(item.subject_id)}" data-pick-query="${esc(item.query || item.ico || item.name || "")}"
    class="w-full text-left px-3 py-2 rounded-lg text-sm hover:bg-slate-50 transition-colors group">
    <div class="font-medium text-slate-800 truncate group-hover:text-neutral-700 leading-snug">${esc(item.name || "Firma")}</div>
    <div class="text-[11px] text-slate-400 truncate mt-0.5">${esc(item.ico || "")}${item.updated_at ? " · " + fmtRelative(item.updated_at) : ""}</div>
  </button>`;
}

function renderHistory() {
  const html = state.history.length
    ? state.history.map(historyItemHtml).join("")
    : '<div class="px-3 py-4 text-xs text-slate-400">Historie se začne plnit po prvním prověření.</div>';
  if ($railHistory) $railHistory.innerHTML = html;
  if ($drawerHistory) $drawerHistory.innerHTML = html;
}

// ---- Drawer ----
function openDrawer() {
  state.drawerOpen = true;
  $drawer.classList.remove("hidden");
  requestAnimationFrame(() => $drawer.classList.remove("is-closed"));
}

function closeDrawer() {
  state.drawerOpen = false;
  $drawer.classList.add("is-closed");
  setTimeout(() => { if (!state.drawerOpen) $drawer.classList.add("hidden"); }, 200);
}

// ---- Autocomplete ----
function autocompleteHtml(results) {
  if (!results.length) return "";
  return `
  <div class="autocomplete-dropdown bg-white rounded-xl ring-1 ring-slate-200 shadow-lg overflow-hidden max-h-[320px] overflow-y-auto">
    ${results.map((m) => `
      <button type="button" data-pick-id="${esc(m.subject_id)}" data-pick-query="${esc(m.name || m.ico || "")}"
        class="w-full flex items-center justify-between px-4 py-3 hover:bg-slate-50 transition-colors text-left group border-b border-slate-50 last:border-0">
        <div class="min-w-0">
          <div class="text-sm font-medium text-slate-900 group-hover:text-neutral-700 truncate">${esc(m.name)}</div>
          <div class="text-xs text-slate-400 mt-0.5 truncate">IČO ${esc(m.ico_display || m.ico)}${m.address ? " · " + esc(m.address) : ""}</div>
        </div>
        <svg class="w-4 h-4 text-slate-300 group-hover:text-neutral-500 flex-shrink-0 ml-3" fill="none" viewBox="0 0 24 24" stroke-width="2" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" d="m8.25 4.5 7.5 7.5-7.5 7.5"/></svg>
      </button>`).join("")}
  </div>`;
}

function clearAutocomplete() {
  state.autocompleteResults = [];
  state.autocompleteOpen = false;
  state.autocompleteLoading = false;
  if (_acTimer) { clearTimeout(_acTimer); _acTimer = null; }
  if (_acController) { _acController.abort(); _acController = null; }
  const heroAc = document.getElementById("hero-autocomplete");
  if (heroAc) heroAc.innerHTML = "";
  const headerAc = document.getElementById("header-autocomplete");
  if (headerAc) headerAc.innerHTML = "";
}

function handleAutocompleteInput(value, dropdownId) {
  const q = value.trim();
  if (q.length < 2) { clearAutocomplete(); return; }

  if (_acTimer) clearTimeout(_acTimer);
  _acTimer = setTimeout(async () => {
    if (_acController) _acController.abort();
    _acController = new AbortController();
    state.autocompleteLoading = true;
    try {
      const res = await fetch(`${API}/api/search?q=${encodeURIComponent(q)}`, { signal: _acController.signal });
      if (!res.ok) return;
      const data = await res.json();
      state.autocompleteResults = data.results || [];
      state.autocompleteOpen = state.autocompleteResults.length > 0;
      const dropdown = document.getElementById(dropdownId);
      if (dropdown) dropdown.innerHTML = autocompleteHtml(state.autocompleteResults);
    } catch (e) {
      if (e.name !== "AbortError") { clearAutocomplete(); }
    } finally {
      state.autocompleteLoading = false;
      _acController = null;
    }
  }, 300);
}

// ---- Status ----
function setStatus(text, type) {
  if ($statusText) $statusText.textContent = text;
  if ($statusDot) {
    $statusDot.className = "w-1.5 h-1.5 rounded-full flex-shrink-0 " + ({
      ready: "bg-slate-300",
      running: "bg-neutral-500 status-running",
      done: "bg-emerald-500",
      error: "bg-red-400",
      waiting: "bg-amber-400",
    }[type] || "bg-slate-300");
  }
}

// ---- Header hide/show on scroll ----
function handleScroll() {
  const y = window.scrollY || 0;
  const delta = y - state.lastScrollY;
  if (y < 40) {
    state.headerHidden = false;
  } else if (delta > 10) {
    state.headerHidden = true;
  } else if (delta < -8) {
    state.headerHidden = false;
  }
  state.lastScrollY = y;
  if ($header) $header.classList.toggle("header-hidden", state.headerHidden);
}

// ============================================================
// VIEWS
// ============================================================

function heroView() {
  const recent = state.history.slice(0, 3);

  return `
  <div class="hero-centered px-4 sm:px-6">
    <div class="w-full max-w-xl mx-auto">
      <div class="text-center mb-8">
        <img src="/praskac-icon.png" alt="Justice Práskač" class="w-20 h-20 rounded-2xl mb-4 mx-auto">
        <h1 class="text-2xl sm:text-3xl font-bold tracking-tight text-slate-900">Justice Práskač</h1>
        <p class="mt-2 text-sm text-slate-500">Prověř firmu z veřejných rejstříků</p>
      </div>
      <!-- Hero search input -->
      <form id="hero-search-form" class="relative">
        <div class="relative">
          <div class="pointer-events-none absolute inset-y-0 left-0 flex items-center pl-4">
            <svg class="h-5 w-5 text-slate-400" fill="none" viewBox="0 0 24 24" stroke-width="2" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" d="m21 21-5.197-5.197m0 0A7.5 7.5 0 1 0 5.196 5.196a7.5 7.5 0 0 0 10.607 10.607Z"/></svg>
          </div>
          <input id="hero-input" type="text" placeholder="Název firmy nebo IČO..." autocomplete="off"
            class="block w-full rounded-xl border-0 bg-white py-3.5 pl-12 pr-24 text-base text-slate-900 ring-1 ring-inset ring-slate-200 shadow-sm placeholder:text-slate-400 focus:ring-2 focus:ring-neutral-500 transition-colors">
          <div class="absolute inset-y-0 right-0 flex items-center pr-2">
            <button type="submit" class="rounded-lg bg-neutral-900 px-4 py-2 text-sm font-semibold text-white shadow-sm hover:bg-neutral-800 transition-colors">
              Prověřit
            </button>
          </div>
        </div>
        <!-- Autocomplete dropdown renders here -->
        <div id="hero-autocomplete" class="absolute left-0 right-0 top-full mt-1 z-50"></div>
      </form>
      ${recent.length ? `
      <div class="mt-8 pt-6 border-t border-slate-100">
        <div class="text-[11px] font-medium text-slate-400 uppercase tracking-wider mb-3 text-center">Poslední prověření</div>
        <div class="grid grid-cols-1 sm:grid-cols-3 gap-2">
          ${recent.map((item) => `
            <button type="button" data-pick-id="${esc(item.subject_id)}" data-pick-query="${esc(item.query || item.ico || item.name || "")}"
              class="text-left px-3.5 py-3 rounded-xl bg-white ring-1 ring-slate-200/60 shadow-sm hover:ring-slate-300 hover:shadow transition-all group">
              <div class="text-sm font-medium text-slate-800 truncate group-hover:text-neutral-700">${esc(item.name || "Firma")}</div>
              <div class="text-[11px] text-slate-400 mt-0.5">${esc(item.ico || "")}${item.updated_at ? " · " + fmtRelative(item.updated_at) : ""}</div>
            </button>`).join("")}
        </div>
      </div>` : ""}
    </div>
  </div>`;
}

function loadingView(previewOrText, log) {
  const text = typeof previewOrText === "string" ? previewOrText : "Čtu veřejné podklady z justice.cz.";
  const items = (log || []).length ? log : [text];
  const preview = typeof previewOrText === "object" ? previewOrText : null;

  return `
  <div class="max-w-2xl mx-auto px-4 sm:px-6 py-12 view-enter">
    <div class="${CLS_CARD} overflow-hidden">
      ${preview ? `
      <div class="px-5 py-4 border-b border-slate-100">
        <div class="flex items-center gap-3">
          <img src="/praskac-icon.png" alt="" class="w-9 h-9 rounded-xl flex-shrink-0">
          <div>
            <div class="text-sm font-semibold text-slate-900">${esc(preview.name || "Načítaná firma")}</div>
            <div class="text-xs text-slate-400">IČO ${esc(preview.ico || "—")}</div>
          </div>
        </div>
      </div>` : `
      <div class="px-5 py-4 border-b border-slate-100 flex items-center gap-3">
        <img src="/praskac-icon.png" alt="" class="w-9 h-9 rounded-xl flex-shrink-0">
        <div>
          <div class="text-sm font-semibold text-slate-900">Analyzuji firmu</div>
          <div class="text-xs text-slate-400">Veřejné rejstříky</div>
        </div>
      </div>`}
      <div class="px-5 py-4 space-y-2.5">
        ${items.map((item, i) => {
          const isLast = i === items.length - 1;
          return `
          <div class="flex items-start gap-2.5 thinking-line" style="animation-delay: ${i * 30}ms">
            ${isLast
              ? '<div class="w-4 h-4 mt-0.5 flex-shrink-0 border-2 border-neutral-400 border-t-transparent rounded-full animate-spin"></div>'
              : '<svg class="w-4 h-4 text-neutral-500 mt-0.5 flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke-width="2.5" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" d="m4.5 12.75 6 6 9-13.5"/></svg>'}
            <span class="text-sm ${isLast ? "text-slate-700" : "text-slate-400"}">${esc(item)}</span>
          </div>`;
        }).join("")}
      </div>
      ${preview && (preview.basic_info || []).length ? `
      <div class="border-t border-slate-100 px-5 py-4">
        <div class="grid grid-cols-2 gap-2">
          ${(preview.basic_info || []).slice(0, 4).map((i) => `
            <div class="text-xs"><span class="text-slate-400">${esc(i.label)}:</span> <span class="text-slate-600">${esc(i.value)}</span></div>
          `).join("")}
        </div>
      </div>` : ""}
    </div>
  </div>`;
}

function errorView(text) {
  return `
  <div class="max-w-lg mx-auto px-4 sm:px-6 py-12 text-center view-enter">
    <div class="${CLS_CARD} p-6">
      <div class="w-10 h-10 rounded-full bg-red-50 text-red-500 flex items-center justify-center mx-auto mb-3">
        <svg class="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke-width="2" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" d="M12 9v3.75m9-.75a9 9 0 1 1-18 0 9 9 0 0 1 18 0Zm-9 3.75h.008v.008H12v-.008Z"/></svg>
      </div>
      <h2 class="text-lg font-semibold text-slate-900 mb-1">Něco se nepovedlo</h2>
      <p class="text-sm text-slate-500 mb-4">${esc(text)}</p>
      <button data-retry class="inline-flex items-center px-4 py-2 rounded-lg bg-neutral-900 text-white text-sm font-semibold hover:bg-neutral-800 transition-colors">Zkusit znovu</button>
    </div>
  </div>`;
}

// ============================================================
// PROFILE VIEW
// ============================================================

function sectionNav() {
  const tabs = [
    ["overview", "Přehled"],
    ["finance", "Finance"],
    ["insights", "Signály"],
    ["people", "Osoby"],
    ["documents", "Listiny"],
    ["sources", "Zdroje"],
  ];
  return `
  <nav id="section-nav" class="sticky top-[52px] z-30 -mx-4 sm:-mx-6 px-4 sm:px-6 bg-white/95 backdrop-blur-md border-b border-slate-100 mb-6">
    <div class="flex gap-1 overflow-x-auto scrollbar-hide py-2">
      ${tabs.map(([id, label]) => `
        <button data-nav="${id}" class="px-3 py-1.5 text-sm font-medium rounded-lg whitespace-nowrap transition-colors
          ${id === "overview" ? "bg-neutral-100 text-neutral-700" : "text-slate-500 hover:text-slate-700 hover:bg-slate-50"}">${esc(label)}</button>
      `).join("")}
    </div>
  </nav>`;
}

function profileHero(p) {
  const tl = p.financial_timeline || [];
  const latest = tl[tl.length - 1];
  const prev = tl.length >= 2 ? tl[tl.length - 2] : null;
  const yoy = latest && prev && latest.revenue && prev.revenue
    ? ((latest.revenue - prev.revenue) / Math.abs(prev.revenue)) * 100 : null;

  const legalForm = getInfo(p, "právní forma") || "";
  const city = getInfo(p, "sídlo") || "";
  const yearsCovered = tl.length ? `${tl[0].year}–${tl[tl.length - 1].year}` : "—";

  const metrics = [
    { label: "Aktiva", value: fmtM(latest?.assets), sub: latest ? `${latest.year}` : "—" },
    { label: "Čistý zisk", value: fmtM(latest?.net_profit), sub: latest ? `marže ${fmtPct(latest.net_margin_pct)}` : "—" },
    { label: "Kapitál", value: fmtPct(latest?.equity_ratio_pct), sub: latest ? `${latest.year}` : "—" },
  ];
  if (latest?.revenue != null) metrics.unshift({ label: "Tržby", value: fmtM(latest.revenue), sub: latest ? `${latest.year}` : "—" });
  if (yoy != null && isFinite(yoy)) metrics.push({ label: "Růst tržeb", value: fmtPct(yoy), sub: prev ? `${prev.year} → ${latest.year}` : "—" });
  if (latest?.debt != null) metrics.push({ label: "Dluh", value: fmtM(latest.debt), sub: latest ? `${latest.year}` : "—" });
  metrics.push({ label: "Pokrytí", value: `${tl.length} let`, sub: yearsCovered });

  const isAi = p.analysis_engine === "ai";
  const isCached = p.cache_status === "cached";

  return `
  <section id="section-overview" data-section="overview" class="scroll-mt-24">
    <div class="flex flex-col sm:flex-row sm:items-start sm:justify-between gap-3 mb-4">
      <div class="min-w-0">
        <h1 class="text-2xl sm:text-3xl font-bold tracking-tight text-slate-900 leading-tight">${esc(p.name)}</h1>
        <div class="mt-1.5 flex flex-wrap items-center gap-x-2.5 gap-y-1 text-sm text-slate-500">
          <span>IČO ${esc(p.ico || "—")}</span>
          ${legalForm ? `<span class="text-slate-300">·</span><span>${esc(legalForm)}</span>` : ""}
          ${city ? `<span class="text-slate-300">·</span><span class="truncate max-w-[200px]">${esc(city.split(",")[0])}</span>` : ""}
        </div>
      </div>
      ${isAi ? `
      <button data-rerun="${esc(p.subject_id)}" class="flex-shrink-0 inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium text-slate-500 ring-1 ring-slate-200 hover:bg-slate-50 hover:text-slate-700 transition-colors">
        <svg class="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke-width="2" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" d="M16.023 9.348h4.992v-.001M2.985 19.644v-4.992m0 0h4.992m-4.993 0 3.181 3.183a8.25 8.25 0 0 0 13.803-3.7M4.031 9.865a8.25 8.25 0 0 1 13.803-3.7l3.181 3.182"/></svg>
        Aktualizovat
      </button>` : `
      <button data-rerun="${esc(p.subject_id)}" class="flex-shrink-0 inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium text-white bg-neutral-900 hover:bg-neutral-800 shadow-sm transition-colors">
        <svg class="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke-width="2" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" d="M9.813 15.904 9 18.75l-.813-2.846a4.5 4.5 0 0 0-3.09-3.09L2.25 12l2.846-.813a4.5 4.5 0 0 0 3.09-3.09L9 5.25l.813 2.846a4.5 4.5 0 0 0 3.09 3.09L15.75 12l-2.846.813a4.5 4.5 0 0 0-3.09 3.09ZM18.259 8.715 18 9.75l-.259-1.035a3.375 3.375 0 0 0-2.455-2.456L14.25 6l1.036-.259a3.375 3.375 0 0 0 2.455-2.456L18 2.25l.259 1.035a3.375 3.375 0 0 0 2.455 2.456L21.75 6l-1.036.259a3.375 3.375 0 0 0-2.455 2.456Z"/></svg>
        Spustit AI analýzu
      </button>`}
    </div>
    <!-- Status chips -->
    <div class="flex flex-wrap gap-1.5 mb-5">
      <span class="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-medium bg-emerald-50 text-emerald-700 ring-1 ring-emerald-200/60">
        <span class="w-1 h-1 rounded-full bg-emerald-500"></span>Aktivní
      </span>
      <span class="inline-flex items-center px-2 py-0.5 rounded-full text-[11px] font-medium bg-slate-50 text-slate-500 ring-1 ring-slate-200/60">Veřejná data</span>
      ${isAi ? `<span class="inline-flex items-center px-2 py-0.5 rounded-full text-[11px] font-medium bg-neutral-100 text-neutral-700 ring-1 ring-neutral-300/60">AI ${esc(p.analysis_model || "")}</span>` : ""}
      <span class="inline-flex items-center px-2 py-0.5 rounded-full text-[11px] font-medium bg-slate-50 text-slate-500 ring-1 ring-slate-200/60">${isCached ? "Z mezipaměti" : "Čerstvé"}</span>
      ${p.generated_at ? `<span class="inline-flex items-center px-2 py-0.5 rounded-full text-[11px] font-medium bg-slate-50 text-slate-400 ring-1 ring-slate-200/60">${fmtRelative(p.generated_at) || fmtDate(p.generated_at)}</span>` : ""}
    </div>
    <!-- Metric cards -->
    <div class="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
      ${metrics.map((m) => `
        <div class="${CLS_CARD} px-4 py-3.5">
          <div class="text-[11px] font-medium text-slate-400 uppercase tracking-wider mb-1">${esc(m.label)}</div>
          <div class="text-lg font-bold tracking-tight text-slate-900 tabular-nums leading-tight">${esc(m.value)}</div>
          <div class="text-[11px] text-slate-400 mt-0.5">${esc(m.sub)}</div>
        </div>`).join("")}
    </div>
  </section>`;
}

function executiveSummary(p) {
  const overview = p.analysis_overview || "";
  const items = p.insight_summary || [];
  const deep = p.deep_insights || [];
  const note = p.data_quality_note || "";

  const toneColor = (i) => SEV_TONE[severityOf(items[i] || {})];

  return `
  <section class="${CLS_CARD} overflow-hidden">
    <div class="px-5 py-3.5 border-b border-slate-100 flex items-center justify-between">
      <h2 class="${CLS_SECTION_HEADING}">Shrnutí</h2>
      ${note ? `<span class="text-[11px] text-slate-400 max-w-xs truncate hidden sm:inline" title="${esc(note)}">${esc(note.slice(0, 60))}${note.length > 60 ? "..." : ""}</span>` : ""}
    </div>
    <div class="p-5">
      ${overview ? `<p class="text-sm text-slate-700 leading-relaxed">${esc(overview)}</p>` : '<p class="text-sm text-slate-400">Shrnutí zatím není k dispozici.</p>'}
      ${items.length ? `
      <div class="mt-4 grid grid-cols-1 sm:grid-cols-2 gap-2.5">
        ${items.slice(0, 4).map((item, i) => `
          <div class="flex gap-3 p-3 rounded-lg bg-slate-50/80">
            <div class="w-1.5 h-1.5 rounded-full ${toneColor(i)} mt-1.5 flex-shrink-0"></div>
            <div class="min-w-0">
              <div class="text-sm font-medium text-slate-800 leading-snug">${esc(item.title)}</div>
              <div class="text-xs text-slate-500 mt-0.5 leading-relaxed">${esc(item.detail)}</div>
            </div>
          </div>`).join("")}
      </div>` : ""}
      ${deep.length ? `
      <div class="mt-4">
        <button data-expand="ai-detail" class="text-xs font-medium text-neutral-600 hover:text-neutral-700 transition-colors">
          Podrobný rozbor (${deep.length} bodů) →
        </button>
        <div id="ai-detail" class="${state.expandedPanels.has("ai-detail") ? "" : "hidden"} mt-3 space-y-2">
          ${deep.map((item) => `
            <div class="flex gap-2.5 p-2.5 rounded-lg bg-slate-50 text-sm">
              <svg class="w-3.5 h-3.5 text-slate-400 mt-0.5 flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke-width="2" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" d="m8.25 4.5 7.5 7.5-7.5 7.5"/></svg>
              <div class="min-w-0"><span class="font-medium text-slate-800">${esc(item.title)}.</span> <span class="text-slate-500">${esc(item.detail)}</span></div>
            </div>`).join("")}
        </div>
      </div>` : ""}
    </div>
  </section>`;
}

function financialOverview(p) {
  const tl = p.financial_timeline || [];
  if (!tl.length) {
    return `
    <section id="section-finance" data-section="finance" class="scroll-mt-24">
      <div class="${CLS_CARD} p-5 text-center">
        <p class="text-sm text-slate-400">Z veřejných PDF se nepodařilo vytáhnout spolehlivou časovou řadu.</p>
      </div>
    </section>`;
  }

  const sorted = [...tl].sort((a, b) => a.year - b.year);
  const hasRevenue = sorted.some((r) => r.revenue != null);

  return `
  <section id="section-finance" data-section="finance" class="scroll-mt-24 space-y-4">
    <div class="flex items-center justify-between">
      <h2 class="${CLS_SECTION_HEADING}">Finanční vývoj</h2>
      <span class="text-[11px] text-slate-400">${sorted[0].year}–${sorted[sorted.length - 1].year} · ${sorted.length} let</span>
    </div>
    <!-- Chart -->
    <div class="${CLS_CARD} p-4">
      <div class="chart-wrap"><canvas id="finance-chart" aria-label="Graf finančního vývoje"></canvas></div>
    </div>
    <!-- Stats strip -->
    <div class="grid grid-cols-2 sm:grid-cols-4 gap-2">
      ${(() => {
        const last = sorted[sorted.length - 1];
        const first = sorted[0];
        const yrs = sorted.length;
        const strips = [];
        if (hasRevenue && last.revenue != null && first.revenue != null && yrs > 1) {
          const cagr = (Math.pow(last.revenue / first.revenue, 1 / (yrs - 1)) - 1) * 100;
          strips.push(["CAGR tržeb", isFinite(cagr) ? fmtPct(cagr) : "—"]);
        }
        if (last.equity_ratio_pct != null) strips.push(["VK/aktiva", fmtPct(last.equity_ratio_pct)]);
        if (last.net_margin_pct != null) strips.push(["Marže", fmtPct(last.net_margin_pct)]);
        strips.push(["Roky", `${yrs}`]);
        return strips.map(([l, v]) => `
          <div class="bg-white rounded-lg px-3 py-2 ring-1 ring-slate-200/60 shadow-sm">
            <div class="${CLS_LABEL_XS}">${esc(l)}</div>
            <div class="text-sm font-semibold text-slate-900 tabular-nums mt-0.5">${esc(v)}</div>
          </div>`).join("");
      })()}
    </div>
    <!-- Table -->
    <div class="${CLS_CARD} overflow-hidden">
      <div class="overflow-x-auto">
        <table class="min-w-full text-sm fin-table">
          <thead>
            <tr class="border-b border-slate-200 text-[11px] font-medium text-slate-400 uppercase tracking-wider">
              <th class="py-2.5 px-3 text-left bg-slate-50">Rok</th>
              ${hasRevenue ? '<th class="py-2.5 px-3 text-right bg-slate-50">Tržby</th>' : ""}
              <th class="py-2.5 px-3 text-right bg-slate-50">Zisk</th>
              <th class="py-2.5 px-3 text-right bg-slate-50">Aktiva</th>
              <th class="py-2.5 px-3 text-right bg-slate-50">VK</th>
              <th class="py-2.5 px-3 text-right bg-slate-50">Dluh</th>
              <th class="py-2.5 px-3 text-right bg-slate-50">Marže</th>
            </tr>
          </thead>
          <tbody class="divide-y divide-slate-50">
            ${sorted.map((r) => `
              <tr class="hover:bg-slate-50/50 transition-colors">
                <td class="py-2 px-3 font-medium text-slate-900 tabular-nums">${esc(r.year)}</td>
                ${hasRevenue ? `<td class="py-2 px-3 text-right tabular-nums text-slate-700">${fmtM(r.revenue)}</td>` : ""}
                <td class="py-2 px-3 text-right tabular-nums ${(r.net_profit ?? 0) < 0 ? "text-red-600" : "text-slate-700"}">${fmtM(r.net_profit)}</td>
                <td class="py-2 px-3 text-right tabular-nums text-slate-700">${fmtM(r.assets)}</td>
                <td class="py-2 px-3 text-right tabular-nums text-slate-700">${fmtM(r.equity)}</td>
                <td class="py-2 px-3 text-right tabular-nums text-slate-700">${fmtM(r.debt)}</td>
                <td class="py-2 px-3 text-right tabular-nums text-slate-500">${fmtPct(r.net_margin_pct)}</td>
              </tr>`).join("")}
          </tbody>
        </table>
      </div>
    </div>
  </section>`;
}

function aiInsightsSection(p) {
  const praskac = p.praskac || [];
  const deep = p.deep_insights || [];
  if (!praskac.length && !deep.length) return "";

  return `
  <section id="section-insights" data-section="insights" class="scroll-mt-24">
    <div class="flex items-center justify-between mb-4">
      <h2 class="${CLS_SECTION_HEADING}">Signály a postřehy</h2>
    </div>
    <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
      ${deep.length ? `
      <div class="${CLS_CARD} overflow-hidden">
        <div class="px-4 py-3 border-b border-slate-100">
          <h3 class="text-xs font-semibold text-slate-500 uppercase tracking-wider">Postřehy</h3>
        </div>
        <div class="divide-y divide-slate-50">
          ${deep.map((item) => `
            <div class="px-4 py-3 flex gap-3">
              <svg class="w-4 h-4 text-neutral-500 mt-0.5 flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke-width="2" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" d="M12 18v-5.25m0 0a6.01 6.01 0 0 0 1.5-.189m-1.5.189a6.01 6.01 0 0 1-1.5-.189m3.75 7.478a12.06 12.06 0 0 1-4.5 0m3.75 2.383a14.406 14.406 0 0 1-3 0M14.25 18v-.192c0-.983.658-1.823 1.508-2.316a7.5 7.5 0 1 0-7.517 0c.85.493 1.509 1.333 1.509 2.316V18"/></svg>
              <div class="min-w-0">
                <div class="text-sm font-medium text-slate-800 leading-snug">${esc(item.title)}</div>
                <div class="text-xs text-slate-500 mt-0.5 leading-relaxed">${esc(item.detail)}</div>
              </div>
            </div>`).join("")}
        </div>
      </div>` : ""}
      ${praskac.length ? `
      <div class="bg-white rounded-xl ring-1 ring-red-100 shadow-sm overflow-hidden">
        <div class="px-4 py-3 border-b border-red-50 flex items-center justify-between">
          <h3 class="text-xs font-semibold text-red-700 uppercase tracking-wider">Práskač</h3>
          <span class="text-[10px] text-red-400 font-medium">jen veřejné signály</span>
        </div>
        <div class="divide-y divide-red-50/50">
          ${praskac.slice(0, state.expandedPanels.has("all-praskac") ? 999 : 3).map((item) => {
            return `
            <div class="px-4 py-3 flex gap-3">
              <div class="w-2 h-2 rounded-full ${SEV_DOT[severityOf(item)]} mt-1.5 flex-shrink-0"></div>
              <div class="min-w-0">
                <div class="text-sm font-medium text-slate-800 leading-snug">${esc(item.title)}</div>
                <div class="text-xs text-slate-500 mt-0.5 leading-relaxed">${esc(item.detail)}</div>
              </div>
            </div>`;
          }).join("")}
        </div>
        ${praskac.length > 3 && !state.expandedPanels.has("all-praskac") ? `
          <div class="px-4 py-2.5 border-t border-red-50">
            <button data-expand="all-praskac" class="text-xs font-medium text-red-600 hover:text-red-700">Zobrazit všech ${praskac.length} signálů →</button>
          </div>` : ""}
      </div>` : ""}
    </div>
  </section>`;
}

function peopleSection(p) {
  const execs = p.executives || [];
  const owners = p.owners || [];
  const bodies = p.statutory_bodies || [];
  const signals = p.history_signals || {};

  return `
  <section id="section-people" data-section="people" class="scroll-mt-24">
    <div class="flex items-center justify-between mb-4">
      <h2 class="${CLS_SECTION_HEADING}">Osoby a struktura</h2>
    </div>
    <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
      <!-- Management -->
      <div class="${CLS_CARD} overflow-hidden">
        <div class="px-4 py-3 border-b border-slate-100 flex items-center justify-between">
          <h3 class="text-xs font-semibold text-slate-500 uppercase tracking-wider">Vedení</h3>
          ${execs.length ? `<span class="text-[10px] text-slate-400">${execs.length} osob</span>` : ""}
        </div>
        ${execs.length ? `
        <div class="divide-y divide-slate-50">
          ${execs.slice(0, state.expandedPanels.has("all-execs") ? 999 : 5).map((e) => `
            <div class="px-4 py-2.5">
              <div class="text-sm font-medium text-slate-800">${esc(e.name || "—")}</div>
              <div class="text-xs text-slate-500 mt-0.5">${esc(e.role || "Statutární role")}${e.birth_date ? ` · nar. ${esc(e.birth_date)}` : ""}</div>
            </div>`).join("")}
        </div>
        ${execs.length > 5 && !state.expandedPanels.has("all-execs") ? `
          <div class="px-4 py-2 border-t border-slate-50">
            <button data-expand="all-execs" class="text-xs font-medium text-neutral-600 hover:text-neutral-700">Zobrazit všech ${execs.length} →</button>
          </div>` : ""}
        ` : '<div class="p-4 text-xs text-slate-400">Ve výpisu nebyly nalezeny osoby ve vedení.</div>'}
      </div>
      <!-- Ownership -->
      <div class="${CLS_CARD} overflow-hidden">
        <div class="px-4 py-3 border-b border-slate-100 flex items-center justify-between">
          <h3 class="text-xs font-semibold text-slate-500 uppercase tracking-wider">Vlastníci a orgány</h3>
        </div>
        ${owners.length ? `
        <div class="divide-y divide-slate-50">
          ${owners.slice(0, 6).map((o) => `
            <div class="px-4 py-2.5">
              <div class="text-sm font-medium text-slate-800">${esc(o.role || "Vlastnická položka")}</div>
              <div class="text-xs text-slate-500 mt-0.5 line-clamp-2">${esc(o.raw || "")}</div>
            </div>`).join("")}
        </div>
        ` : bodies.length ? `
        <div class="divide-y divide-slate-50">
          ${bodies.slice(0, 4).map((b) => `
            <div class="px-4 py-2.5">
              <div class="text-sm font-medium text-slate-800">${esc(b.title)}</div>
              <div class="text-xs text-slate-500 mt-0.5">${(b.items || []).length} položek ve výpisu</div>
            </div>`).join("")}
        </div>
        ` : '<div class="p-4 text-xs text-slate-400">Vlastnické údaje nejsou ve výpisu rozepsané.</div>'}
        ${(signals.name_changes != null || signals.address_changes != null || signals.management_turnover != null) ? `
        <div class="px-4 py-3 border-t border-slate-100 bg-slate-50/50">
          <div class="text-[10px] font-medium text-slate-400 uppercase tracking-wider mb-2">Historické změny</div>
          <div class="flex flex-wrap gap-3 text-xs text-slate-600">
            ${signals.name_changes != null ? `<span>Název: <strong>${esc(signals.name_changes)}</strong></span>` : ""}
            ${signals.address_changes != null ? `<span>Sídlo: <strong>${esc(signals.address_changes)}</strong></span>` : ""}
            ${signals.management_turnover != null ? `<span>Vedení: <strong>${esc(signals.management_turnover)}</strong></span>` : ""}
          </div>
        </div>` : ""}
      </div>
    </div>
  </section>`;
}

function documentsSection(p) {
  const docs = p.financial_documents || [];
  if (!docs.length) {
    return `
    <section id="section-documents" data-section="documents" class="scroll-mt-24">
      <div class="flex items-center justify-between mb-4">
        <h2 class="${CLS_SECTION_HEADING}">Listiny a dokumenty</h2>
      </div>
      <div class="${CLS_CARD} p-5 text-center">
        <p class="text-sm text-slate-400">Nebyly nalezeny relevantní finanční listiny.</p>
      </div>
    </section>`;
  }

  return `
  <section id="section-documents" data-section="documents" class="scroll-mt-24">
    <div class="flex items-center justify-between mb-4">
      <h2 class="${CLS_SECTION_HEADING}">Listiny a dokumenty</h2>
      <span class="text-[11px] text-slate-400">${docs.length} listin</span>
    </div>
    <div class="${CLS_CARD} overflow-hidden divide-y divide-slate-100">
      ${docs.map((doc) => {
        const files = doc.candidate_files || [];
        const metrics = doc.metrics_found || [];
        const yr = (doc.years || ["?"])[0];
        const mode = doc.extraction_mode || "?";
        const modeClass = MODE_COLOR[mode] || MODE_COLOR.mixed;
        const key = `doc-${doc.document_number || doc.document_id || yr}`;
        const isOpen = state.expandedAccordions.has(key);

        return `
        <div>
          <button data-accordion="${esc(key)}" class="w-full flex items-center justify-between px-4 py-3 hover:bg-slate-50/50 transition-colors text-left ${isOpen ? "accordion-open bg-slate-50/30" : ""}">
            <div class="flex items-center gap-3 min-w-0">
              <span class="text-sm font-semibold text-slate-900 tabular-nums w-10 flex-shrink-0">${esc(yr)}</span>
              <span class="text-sm text-slate-600 truncate">${esc(doc.type || "Listina")}</span>
            </div>
            <div class="flex items-center gap-2 flex-shrink-0 ml-3">
              <span class="${CLS_BADGE_SM} ${modeClass}">${esc(mode)}</span>
              <span class="text-[11px] text-slate-400 tabular-nums">${files.length} PDF</span>
              <span class="text-[11px] text-slate-400 tabular-nums">${metrics.length} metrik</span>
              <svg class="w-4 h-4 text-slate-400 transition-transform duration-200 accordion-chevron ${isOpen ? "rotate-180" : ""}" fill="none" viewBox="0 0 24 24" stroke-width="2" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" d="m19.5 8.25-7.5 7.5-7.5-7.5"/></svg>
            </div>
          </button>
          <div id="${esc(key)}" class="${isOpen ? "" : "hidden"} border-t border-slate-100 bg-slate-50/30">
            <div class="px-4 py-3 space-y-2">
              ${metrics.length ? `
              <div class="flex flex-wrap gap-1">
                ${metrics.map((m) => `<span class="${CLS_BADGE_SM} bg-neutral-100 text-neutral-700 ring-neutral-300/60">${esc(metricLabel(m))}</span>`).join("")}
              </div>` : ""}
              ${files.length ? `
              <div class="space-y-1.5">
                ${files.map((f) => {
                  const openUrl = `${API}/api/document/resolve?detailUrl=${encodeURIComponent(doc.detail_url || "")}&index=${encodeURIComponent(f.pdf_index ?? 0)}&prefer_pdf=true`;
                  const fMode = f.extraction_mode || "?";
                  const fMetrics = f.metrics_found || [];
                  return `
                  <div class="flex items-center justify-between gap-3 px-3 py-2 rounded-lg bg-white ring-1 ring-slate-200/60">
                    <div class="min-w-0">
                      <div class="text-xs font-medium text-slate-700 truncate">${esc(f.label || "PDF příloha")}</div>
                      <div class="text-[11px] text-slate-400 mt-0.5">${esc(fMode)} · ${esc(String(f.page_count || "?"))} stran${fMetrics.length ? ` · ${fMetrics.length} metrik` : ""}</div>
                    </div>
                    <a href="${esc(openUrl)}" target="_blank" rel="noopener noreferrer" class="flex-shrink-0 text-xs font-medium text-neutral-600 hover:text-neutral-700 whitespace-nowrap">PDF →</a>
                  </div>`;
                }).join("")}
              </div>` : '<div class="text-xs text-slate-400">Žádná PDF příloha.</div>'}
              ${doc.detail_url ? `<a href="${esc(doc.detail_url)}" target="_blank" rel="noopener noreferrer" class="inline-flex text-xs font-medium text-slate-500 hover:text-slate-700 mt-1">Detail listiny →</a>` : ""}
            </div>
          </div>
        </div>`;
      }).join("")}
    </div>
  </section>`;
}

function coverageSection(p) {
  const docs = p.financial_documents || [];
  const tl = p.financial_timeline || [];
  const links = p.source_links || {};
  const withOcr = docs.filter((d) => d.extraction_mode === "ocr").length;
  const withDigital = docs.filter((d) => d.extraction_mode === "digital").length;
  const firstYear = tl.length ? tl[0].year : "—";
  const lastYear = tl.length ? tl[tl.length - 1].year : "—";

  const extChecks = p.external_checks;

  const sourceLabels = {
    current_extract: "Aktuální výpis",
    full_extract: "Úplný výpis",
    documents: "Sbírka listin",
    current_extract_pdf: "PDF aktuálního výpisu",
    full_extract_pdf: "PDF úplného výpisu",
    chytryrejstrik: "Chytrý rejstřík",
  };

  return `
  <section id="section-sources" data-section="sources" class="scroll-mt-24 space-y-4">
    <div class="flex items-center justify-between">
      <h2 class="${CLS_SECTION_HEADING}">Pokrytí dat a zdroje</h2>
    </div>
    <div class="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-3">
      ${[
        ["Listiny", docs.length],
        ["Roky", `${esc(firstYear)} – ${esc(lastYear)}`],
        ["Digitální PDF", withDigital],
        ["OCR listiny", withOcr],
      ].map(([label, value]) => `
        <div class="${CLS_CARD} px-4 py-3">
          <div class="${CLS_LABEL_XS}">${label}</div>
          <div class="text-lg font-bold text-slate-900 tabular-nums">${value}</div>
        </div>`).join("")}
    </div>
    ${extChecks && (extChecks.checks || []).length ? `
    <div class="${CLS_CARD} overflow-hidden">
      <div class="px-4 py-3 border-b border-slate-100 flex items-center justify-between">
        <h3 class="text-xs font-semibold text-slate-500 uppercase tracking-wider">Externí kontrola</h3>
        ${extChecks.source_name ? `<a href="${esc(extChecks.source_url || "#")}" target="_blank" rel="noopener noreferrer" class="text-[10px] text-neutral-600 hover:text-neutral-700">${esc(extChecks.source_name)} →</a>` : ""}
      </div>
      <div class="divide-y divide-slate-50">
        ${(extChecks.checks || []).map((c) => `
          <div class="px-4 py-2.5 flex items-center justify-between">
            <div class="text-sm text-slate-700">${esc(c.label)}</div>
            <div class="flex items-center gap-2 text-xs">
              <span class="text-slate-500">${fmtM(c.app_value)}</span>
              <span class="text-slate-300">vs</span>
              <span class="${c.status === "warning" ? "text-amber-600 font-medium" : "text-slate-500"}">${fmtM(c.external_value)}</span>
              ${c.status === "warning" ? '<svg class="w-3.5 h-3.5 text-amber-500" fill="none" viewBox="0 0 24 24" stroke-width="2" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" d="M12 9v3.75m-9.303 3.376c-.866 1.5.217 3.374 1.948 3.374h14.71c1.73 0 2.813-1.874 1.948-3.374L13.949 3.378c-.866-1.5-3.032-1.5-3.898 0L2.697 16.126ZM12 15.75h.007v.008H12v-.008Z"/></svg>' : ""}
            </div>
          </div>`).join("")}
      </div>
    </div>` : ""}
    <!-- Source links -->
    <div class="${CLS_CARD} px-4 py-3">
      <div class="text-[10px] font-medium text-slate-400 uppercase tracking-wider mb-2">Zdroje</div>
      <div class="flex flex-wrap gap-2">
        ${Object.entries(links)
          .filter(([, url]) => !!url)
          .map(([key, url]) => `<a href="${esc(url)}" target="_blank" rel="noopener noreferrer" class="inline-flex items-center px-2.5 py-1 rounded-lg text-xs font-medium text-slate-600 ring-1 ring-slate-200 hover:bg-slate-50 hover:text-neutral-700 transition-colors">${esc(sourceLabels[key] || key)} →</a>`).join("")}
      </div>
    </div>
    <!-- Footer -->
    <div class="text-center text-[11px] text-slate-400 py-4">
      Screening jen z veřejných podkladů justice.cz. Není to právní ani investiční doporučení.
    </div>
  </section>`;
}

function contextRailCards(p) {
  const praskac = p.praskac || [];
  const signals = p.history_signals || {};
  const tl = p.financial_timeline || [];
  const latest = tl[tl.length - 1];
  const docs = p.financial_documents || [];

  const legalForm = getInfo(p, "právní forma") || "—";
  const city = getInfo(p, "sídlo") || "—";
  const created = getInfo(p, "datum") || "—";
  const fileNo = getInfo(p, "spisová") || "—";

  return `
    <!-- Risk summary -->
    ${praskac.length ? `
    <div class="bg-white rounded-xl ring-1 ring-red-100 shadow-sm p-4">
      <div class="text-[10px] font-semibold text-red-600 uppercase tracking-wider mb-2.5">Rizikové signály</div>
      <div class="space-y-2">
        ${praskac.slice(0, 3).map((item) => {
          return `
          <div class="flex gap-2">
            <div class="w-1.5 h-1.5 rounded-full ${SEV_DOT[severityOf(item)]} mt-1.5 flex-shrink-0"></div>
            <div class="text-xs text-slate-700 leading-relaxed">${esc(item.title)}</div>
          </div>`;
        }).join("")}
      </div>
      ${praskac.length > 3 ? `<div class="text-[10px] text-red-500 mt-2">+ ${praskac.length - 3} dalších</div>` : ""}
    </div>` : ""}
    <!-- Quick facts -->
    <div class="${CLS_CARD} p-4">
      <div class="text-[10px] font-semibold text-slate-400 uppercase tracking-wider mb-2.5">Základní údaje</div>
      <dl class="space-y-2 text-xs">
        <div class="flex justify-between gap-2"><dt class="text-slate-400">Právní forma</dt><dd class="text-slate-700 text-right truncate max-w-[140px]">${esc(legalForm)}</dd></div>
        <div class="flex justify-between gap-2"><dt class="text-slate-400">Sídlo</dt><dd class="text-slate-700 text-right truncate max-w-[140px]">${esc(city.split(",")[0])}</dd></div>
        <div class="flex justify-between gap-2"><dt class="text-slate-400">Zapsáno</dt><dd class="text-slate-700">${esc(created)}</dd></div>
        <div class="flex justify-between gap-2"><dt class="text-slate-400">Spisová zn.</dt><dd class="text-slate-700 truncate max-w-[140px]">${esc(fileNo)}</dd></div>
      </dl>
    </div>
    <!-- Data quality -->
    <div class="${CLS_CARD} p-4">
      <div class="text-[10px] font-semibold text-slate-400 uppercase tracking-wider mb-2.5">Kvalita dat</div>
      <dl class="space-y-2 text-xs">
        <div class="flex justify-between"><dt class="text-slate-400">Listiny</dt><dd class="text-slate-700 font-medium">${docs.length}</dd></div>
        <div class="flex justify-between"><dt class="text-slate-400">Roky</dt><dd class="text-slate-700 font-medium">${tl.length}</dd></div>
        <div class="flex justify-between"><dt class="text-slate-400">Analýza</dt><dd class="text-slate-700 font-medium">${p.analysis_engine === "ai" ? "AI" : "Pravidlová"}</dd></div>
        ${signals.management_turnover != null ? `<div class="flex justify-between"><dt class="text-slate-400">Obměny vedení</dt><dd class="text-slate-700 font-medium">${esc(signals.management_turnover)}</dd></div>` : ""}
      </dl>
    </div>`;
}

function profileView(p) {
  const railHtml = contextRailCards(p);
  return `
  <div class="max-w-6xl mx-auto px-4 sm:px-6 py-5 view-enter">
    ${sectionNav()}
    <div class="xl:grid xl:grid-cols-[1fr_260px] xl:gap-6 xl:items-start">
      <!-- Main column -->
      <div class="space-y-6 min-w-0">
        ${profileHero(p)}
        <!-- Context cards inline for <xl -->
        <div class="xl:hidden grid grid-cols-1 sm:grid-cols-3 gap-3">
          ${railHtml}
        </div>
        ${executiveSummary(p)}
        ${financialOverview(p)}
        ${aiInsightsSection(p)}
        ${peopleSection(p)}
        ${documentsSection(p)}
        ${coverageSection(p)}
      </div>
      <!-- Context rail for xl+ -->
      <div class="hidden xl:block">
        <div class="sticky top-24 space-y-3">
          ${railHtml}
        </div>
      </div>
    </div>
  </div>`;
}

// ============================================================
// MAIN RENDER
// ============================================================

function render() {
  if (!$content) return;
  $submit.disabled = state.loading;

  // Header visibility: hidden on empty state, visible otherwise
  const showHeader = state.loading || state.profile || state.error;
  if ($header) {
    if (showHeader) {
      $header.classList.remove("hidden");
      if (!$header.dataset.shown) {
        $header.classList.add("header-enter");
        $header.dataset.shown = "1";
      }
    } else {
      $header.classList.add("hidden");
      delete $header.dataset.shown;
      $header.classList.remove("header-enter");
    }
  }

  // Status
  if (state.loading) {
    setStatus(state.statusLog.at(-1) || "Analyzuji...", "running");
  } else if (state.profile) {
    setStatus(state.profile.name, "done");
  } else if (state.error) {
    setStatus("Chyba", "error");
  } else {
    setStatus("Připraveno", "ready");
  }

  if (!state.loading) renderHistory();

  // Content
  if (state.loading) {
    $content.innerHTML = loadingView(
      state.preview,
      state.statusLog.length ? state.statusLog : ["Čtu veřejný výpis, Sbírku listin a finanční podklady."]
    );
  } else if (state.error) {
    $content.innerHTML = errorView(state.error);
  } else if (state.profile) {
    $content.innerHTML = profileView(state.profile);
    drawFinanceChart(state.profile.financial_timeline || []);
    initScrollSpy();
  } else {
    $content.innerHTML = heroView();
  }
}

// ============================================================
// API
// ============================================================

async function fetchJson(url, fallback) {
  let res;
  try { res = await fetch(url); } catch { throw new Error("Síťové spojení se nepovedlo."); }
  let data = null;
  try { data = await res.json(); } catch { data = null; }
  if (!res.ok) throw new Error(data?.detail || fallback);
  return data;
}

async function searchCompanies(q) {
  return fetchJson(`${API}/api/search?q=${encodeURIComponent(q)}`, "Hledání se nepovedlo.");
}

async function loadHistoryData() {
  try {
    const data = await fetchJson(`${API}/api/history`, "");
    return data.items || [];
  } catch { return []; }
}

async function loadCompanySnapshot(id, refresh) {
  return fetchJson(
    `${API}/api/company?subjektId=${encodeURIComponent(id)}&q=${encodeURIComponent(state.query || "")}${refresh ? "&refresh=true" : ""}`,
    "Nepodařilo se načíst detail firmy."
  );
}

async function loadCompanyStream(id, refresh) {
  const url = `${API}/api/company/stream?subjektId=${encodeURIComponent(id)}&q=${encodeURIComponent(state.query || "")}${refresh ? "&refresh=true" : ""}`;
  let res;
  try { res = await fetch(url, { headers: { Accept: "text/event-stream" } }); } catch { throw new Error("Síťové spojení se nepovedlo."); }
  if (!res.ok || !res.body) {
    let detail = "Nepodařilo se načíst detail firmy.";
    try { const p = await res.json(); detail = p?.detail || detail; } catch {}
    throw new Error(detail);
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";

  const parse = (chunk) => {
    const lines = chunk.split("\n");
    let event = "message";
    const datas = [];
    for (const line of lines) {
      if (line.startsWith("event:")) event = line.slice(6).trim();
      if (line.startsWith("data:")) datas.push(line.slice(5).trim());
    }
    if (!datas.length) return null;
    try { return { event, payload: JSON.parse(datas.join("\n")) }; } catch { return null; }
  };

  while (true) {
    const { value, done } = await reader.read();
    buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
    const parts = buffer.split("\n\n");
    buffer = parts.pop() || "";
    for (const part of parts) {
      const msg = parse(part);
      if (!msg) continue;
      if (msg.event === "status") {
        state.statusLog = [...state.statusLog, msg.payload.label].slice(-8);
        render();
      }
      if (msg.event === "preview") { state.preview = msg.payload; render(); }
      if (msg.event === "error") throw new Error(msg.payload?.detail || "Načítání se nepodařilo.");
      if (msg.event === "result") return msg.payload;
    }
    if (done) break;
  }
  throw new Error("Načítání skončilo předčasně.");
}

// ============================================================
// HANDLERS
// ============================================================

async function handleSearch(query) {
  state.query = query;
  state.loading = true;
  state.error = null;
  state.profile = null;
  state.preview = null;
  state.statusLog = ["Hledám firmu podle názvu nebo IČO"];
  state.expandedAccordions.clear();
  state.expandedPanels.clear();
  render();
  try {
    const data = await searchCompanies(query);
    const results = data.results || [];
    if (!results.length) {
      state.loading = false;
      state.error = "Nic jsem nenašel. Zkus přesnější název nebo osmimístné IČO.";
      render(); return;
    }
    if (results.length === 1) {
      await handlePick(results[0].subject_id);
      return;
    }
    const digits = query.replace(/\D/g, "");
    const exact = digits.length === 8 ? results.find((r) => r.ico === digits) : null;
    if (exact) { await handlePick(exact.subject_id); return; }
    // Multiple results — pick the first one
    await handlePick(results[0].subject_id);
  } catch (e) {
    state.loading = false;
    state.error = e.message || "Hledání se nepovedlo.";
    render();
  }
}

async function handlePick(id, opts = {}) {
  const refresh = !!opts.forceRefresh;
  state.selectedMatch = id;
  state.loading = true;
  state.error = null;
  state.profile = null;
  state.preview = null;
  state.statusLog = [refresh ? "Spouštím novou extrakci" : "Otevírám detail firmy"];
  state.expandedAccordions.clear();
  state.expandedPanels.clear();
  render();
  try {
    try {
      state.profile = await loadCompanyStream(id, refresh);
    } catch {
      state.statusLog = [...state.statusLog, "Stream vypadl, zkouším záložní načtení"].slice(-8);
      render();
      state.profile = await loadCompanySnapshot(id, refresh);
    }
    state.loading = false;
    state.preview = null;
    state.statusLog = [];
    state.history = await loadHistoryData();
    // Update URL to shareable link
    history.pushState({ subjektId: id }, "", `/firma/${id}`);
    render();
  } catch (e) {
    state.loading = false;
    state.preview = null;
    state.statusLog = [];
    state.error = e.message || "Nepodařilo se načíst detail firmy.";
    render();
  }
}

// ============================================================
// CHART
// ============================================================

let chartInstance = null;
function drawFinanceChart(rows) {
  const canvas = document.querySelector("#finance-chart");
  if (!canvas || !window.Chart) return;
  if (chartInstance) { chartInstance.destroy(); chartInstance = null; }
  const sorted = [...rows].sort((a, b) => a.year - b.year);
  const labels = sorted.map((r) => String(r.year));
  const hasRevenue = sorted.some((r) => r.revenue != null);
  const datasets = [];
  if (hasRevenue) {
    datasets.push({
      label: "Tržby",
      data: sorted.map((r) => r.revenue ?? null),
      borderColor: "#171717",
      backgroundColor: "rgba(23,23,23,0.08)",
      borderWidth: 2,
      tension: 0.3,
      pointRadius: 3,
      pointBackgroundColor: "#171717",
    });
  }
  datasets.push({
    label: "Čistý zisk",
    data: sorted.map((r) => r.net_profit ?? null),
    borderColor: "#ef4444",
    backgroundColor: "rgba(239,68,68,0.08)",
    borderWidth: 2,
    tension: 0.3,
    pointRadius: 3,
    pointBackgroundColor: "#ef4444",
  });
  datasets.push({
    label: "Aktiva",
    data: sorted.map((r) => r.assets ?? null),
    borderColor: "#6366f1",
    backgroundColor: "rgba(99,102,241,0.05)",
    borderWidth: 1.5,
    borderDash: [4, 3],
    tension: 0.3,
    pointRadius: 2,
    pointBackgroundColor: "#6366f1",
  });

  chartInstance = new Chart(canvas, {
    type: "line",
    data: { labels, datasets },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { intersect: false, mode: "index" },
      scales: {
        x: { ticks: { color: "#94a3b8", font: { size: 11 } }, grid: { color: "rgba(148,163,184,0.1)" } },
        y: { ticks: { color: "#94a3b8", font: { size: 11 }, callback: (v) => `${v}` }, grid: { color: "rgba(148,163,184,0.1)" } },
      },
      plugins: {
        legend: { labels: { color: "#475569", font: { size: 11 }, boxWidth: 12, padding: 16 } },
        tooltip: {
          backgroundColor: "#1e293b",
          titleFont: { size: 12 },
          bodyFont: { size: 11 },
          padding: 10,
          cornerRadius: 8,
          callbacks: { label: (ctx) => `${ctx.dataset.label}: ${fmtM(ctx.raw)}` },
        },
      },
    },
  });
}

// ============================================================
// SCROLL SPY
// ============================================================

let scrollSpyObserver = null;
function initScrollSpy() {
  if (scrollSpyObserver) scrollSpyObserver.disconnect();
  const sections = document.querySelectorAll("[data-section]");
  const navBtns = document.querySelectorAll("[data-nav]");
  if (!sections.length || !navBtns.length) return;

  scrollSpyObserver = new IntersectionObserver(
    (entries) => {
      for (const entry of entries) {
        if (entry.isIntersecting) {
          const id = entry.target.dataset.section;
          navBtns.forEach((btn) => {
            const isActive = btn.dataset.nav === id;
            btn.className = `px-3 py-1.5 text-sm font-medium rounded-lg whitespace-nowrap transition-colors ${
              isActive ? "bg-neutral-100 text-neutral-700" : "text-slate-500 hover:text-slate-700 hover:bg-slate-50"
            }`;
          });
        }
      }
    },
    { rootMargin: "-80px 0px -60% 0px" }
  );
  sections.forEach((s) => scrollSpyObserver.observe(s));
}

function handleNewCheck() {
  state.query = "";
  state.loading = false;
  state.error = null;
  state.profile = null;
  state.preview = null;
  state.statusLog = [];
  state.selectedMatch = null;
  state.expandedAccordions.clear();
  state.expandedPanels.clear();
  clearAutocomplete();
  $input.value = "";
  // Reset URL to root
  history.pushState(null, "", "/");
  render();
  const heroInput = document.getElementById("hero-input");
  if (heroInput) heroInput.focus();
}

// ============================================================
// EVENT DELEGATION
// ============================================================

function initEvents() {
  // Global click delegation
  document.addEventListener("click", (e) => {
    // Pick company (match or history)
    const pick = e.target.closest("[data-pick-id]");
    if (pick) {
      e.preventDefault();
      clearAutocomplete();
      const q = pick.dataset.pickQuery || "";
      if (q) { state.query = q; $input.value = q; }
      handlePick(pick.dataset.pickId);
      if (state.drawerOpen) closeDrawer();
      return;
    }

    // Rerun
    const rerun = e.target.closest("[data-rerun]");
    if (rerun) { handlePick(rerun.dataset.rerun, { forceRefresh: true }); return; }

    // Retry
    const retry = e.target.closest("[data-retry]");
    if (retry) {
      if (state.selectedMatch) handlePick(state.selectedMatch, { forceRefresh: true });
      else if (state.query) handleSearch(state.query);
      return;
    }

    // Accordion
    const acc = e.target.closest("[data-accordion]");
    if (acc) {
      const key = acc.dataset.accordion;
      const target = document.getElementById(key);
      if (!target) return;
      const isOpen = state.expandedAccordions.has(key);
      if (isOpen) state.expandedAccordions.delete(key);
      else state.expandedAccordions.add(key);
      target.classList.toggle("hidden", isOpen);
      acc.classList.toggle("accordion-open", !isOpen);
      acc.classList.toggle("bg-slate-50/30", !isOpen);
      const chev = acc.querySelector(".accordion-chevron");
      if (chev) chev.classList.toggle("rotate-180", !isOpen);
      return;
    }

    // Expand panel
    const expand = e.target.closest("[data-expand]");
    if (expand) {
      const key = expand.dataset.expand;
      state.expandedPanels.add(key);
      // Try local DOM toggle first (for panels that exist but are hidden)
      const target = document.getElementById(key);
      if (target) {
        target.classList.remove("hidden");
        const wrapper = expand.parentElement;
        if (wrapper && wrapper !== $content) wrapper.remove();
        else expand.remove();
      } else {
        // Slice-based expands (all-praskac, all-execs) need re-render
        render();
      }
      return;
    }

    // Section nav
    const nav = e.target.closest("[data-nav]");
    if (nav) {
      const sectionId = nav.dataset.nav;
      const target = document.getElementById(`section-${sectionId}`) || document.querySelector(`[data-section="${sectionId}"]`);
      if (target) target.scrollIntoView({ behavior: "smooth", block: "start" });
      return;
    }
  });

  // Search form
  $form.addEventListener("submit", (e) => {
    e.preventDefault();
    clearAutocomplete();
    const q = $input.value.trim();
    if (q) handleSearch(q);
  });

  // Mobile history drawer
  document.getElementById("mobile-menu-btn")?.addEventListener("click", openDrawer);
  document.getElementById("drawer-close")?.addEventListener("click", closeDrawer);
  document.getElementById("drawer-backdrop")?.addEventListener("click", closeDrawer);

  // Keyboard
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      if (state.autocompleteOpen) { clearAutocomplete(); return; }
      if (state.drawerOpen) closeDrawer();
    }
  });

  // Autocomplete: hero input
  document.addEventListener("input", (e) => {
    if (e.target.id === "hero-input") {
      handleAutocompleteInput(e.target.value, "hero-autocomplete");
    }
  });

  // Autocomplete: header input
  $input.addEventListener("input", () => {
    handleAutocompleteInput($input.value, "header-autocomplete");
  });

  // Hero form submit
  document.addEventListener("submit", (e) => {
    if (e.target.id === "hero-search-form") {
      e.preventDefault();
      const heroInput = document.getElementById("hero-input");
      const q = heroInput ? heroInput.value.trim() : "";
      if (q) {
        clearAutocomplete();
        $input.value = q;
        handleSearch(q);
      }
    }
  });

  // Click outside to close autocomplete
  document.addEventListener("mousedown", (e) => {
    if (!e.target.closest("#hero-search-form") && !e.target.closest("#search-form")) {
      clearAutocomplete();
    }
  });

  // New check buttons
  document.getElementById("new-check-btn")?.addEventListener("click", handleNewCheck);
  document.getElementById("new-check-btn-mobile")?.addEventListener("click", () => {
    handleNewCheck();
    closeDrawer();
  });

  // Scroll handler
  window.addEventListener("scroll", handleScroll, { passive: true });

  // Resize handler
  window.addEventListener("resize", () => {
    if (window.innerWidth >= 1024 && state.drawerOpen) closeDrawer();
  });

  // Browser back/forward
  window.addEventListener("popstate", () => {
    const match = window.location.pathname.match(/^\/firma\/(\d+)$/);
    const id = match ? match[1] : null;
    if (id) {
      handlePick(id);
    } else {
      // Back to home
      state.query = "";
      state.loading = false;
      state.error = null;
      state.profile = null;
      state.preview = null;
      state.statusLog = [];
      state.selectedMatch = null;
      state.expandedAccordions.clear();
      state.expandedPanels.clear();
      clearAutocomplete();
      $input.value = "";
      render();
    }
  });
}

// ============================================================
// INIT
// ============================================================

function init() {
  $content = document.getElementById("content");
  $form = document.getElementById("search-form");
  $input = document.getElementById("query-input");
  $submit = document.getElementById("submit-btn");
  $statusDot = document.getElementById("status-dot");
  $statusText = document.getElementById("status-text");
  $header = document.getElementById("app-header");
  $railHistory = document.getElementById("rail-history");
  $drawerHistory = document.getElementById("drawer-history");
  $drawer = document.getElementById("history-drawer");

  initEvents();

  // Check URL for direct company link (/firma/123)
  const urlMatch = window.location.pathname.match(/^\/firma\/(\d+)$/);
  const urlSubjektId = urlMatch ? urlMatch[1] : null;
  if (urlSubjektId) {
    handlePick(urlSubjektId);
  } else {
    render();
  }

  loadHistoryData().then((items) => {
    state.history = items;
    renderHistory();
    // Re-render hero if on empty state so recent history shows
    if (!state.loading && !state.profile && !state.error) {
      render();
    }
  });
}

init();
