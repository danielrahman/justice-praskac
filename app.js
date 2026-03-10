/* ============================================================
   Justice Práskač — Frontend Application
   ============================================================ */

const API = "";
const HISTORY_PAGE_SIZE = 20;
const HISTORY_RECENT_LIMIT = 3;
const LOADING_STATUS_LIMIT = 3;
const LOADING_STATUS_MESSAGES = [
  "Stahuji výpis z justice.cz a zpracovávám finanční podklady.",
  "Prohledávám veřejné rejstříky a sbírky listin…",
  "Analyzuji dostupné účetní závěrky…",
  "Procházím obchodní rejstřík a insolvenční záznam…",
  "Shromažďuji data z veřejných zdrojů…",
];
function randomLoadingMessage() {
  return LOADING_STATUS_MESSAGES[Math.floor(Math.random() * LOADING_STATUS_MESSAGES.length)];
}

// ---- State ----
const state = {
  query: "",
  loading: false,
  profile: null,
  preview: null,
  history: [],
  historyRecent: [],
  historyTotal: 0,
  historyOffset: 0,
  historyLimit: HISTORY_PAGE_SIZE,
  statusLog: [],
  error: null,
  selectedMatch: null,
  autocompleteResults: [],
  autocompleteActiveIndex: -1,
  autocompleteOpen: false,
  autocompleteLoading: false,
  drawerOpen: false,
  pendingHeroAnimation: null,
  pendingHomeFocus: false,
  expandedAccordions: new Set(),
  expandedPanels: new Set(),
};

// ---- DOM refs (populated on DOMContentLoaded) ----
let $content, $statusDot, $statusText, $header, $railHistory, $drawerHistory, $drawer;

let _acTimer = null;
let _acController = null;
let _statusSeq = 0;
let _activeRequestToken = 0;
let _pendingSearchStageAnimation = null;
let _pendingPanelRevealKeys = null;
const _profileCache = new Map();

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
const _fmtInt = new Intl.NumberFormat("cs-CZ", { maximumFractionDigits: 0 });

const AI_PRICING = [
  { pattern: /^claude-opus-4-6(?:-|$)/i, input: 5.0, output: 25.0, cacheWrite: 6.25, cacheRead: 0.5 },
  { pattern: /^claude-opus-4(?:-|$)/i, input: 15.0, output: 75.0, cacheWrite: 18.75, cacheRead: 1.5 },
  { pattern: /^claude-sonnet-4(?:-|$)/i, input: 3.0, output: 15.0, cacheWrite: 3.75, cacheRead: 0.3 },
  { pattern: /^claude-haiku-(?:4|3-5)(?:-|$)/i, input: 1.0, output: 5.0, cacheWrite: 1.25, cacheRead: 0.1 },
];

const fmtM = (v) => {
  if (v == null || !isFinite(v)) return "—";
  return (Math.abs(v) >= 100 ? _fmtCZ0 : _fmtCZ2).format(v) + " mil.";
};

const fmtPct = (v) => {
  if (v == null || !isFinite(v)) return "—";
  return _fmtPct1.format(v) + " %";
};

const fmtDate = (v) => {
  if (!v) return "—";
  const d = new Date(v);
  if (Number.isNaN(d.getTime())) return v;
  return _fmtDateCZ.format(d);
};

const fmtInt = (v) => {
  if (v == null || Number.isNaN(v)) return "—";
  return _fmtInt.format(v);
};

const fmtUsd = (v) => {
  if (v == null || Number.isNaN(v)) return "—";
  if (v >= 1) return `$${v.toFixed(2)}`;
  if (v >= 0.1) return `$${v.toFixed(3)}`;
  if (v >= 0.01) return `$${v.toFixed(4)}`;
  return `$${v.toFixed(5)}`;
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

function getAiPricing(model) {
  const raw = String(model || "").trim();
  if (!raw) return null;
  return AI_PRICING.find((entry) => entry.pattern.test(raw)) || null;
}

function estimateAiCostFromUsage(model, usage) {
  const pricing = getAiPricing(model || usage?.model);
  if (!pricing || !usage) return null;

  const tokens = {
    input: Number(usage.input_tokens),
    output: Number(usage.output_tokens),
    cacheWrite: Number(usage.cache_creation_input_tokens),
    cacheRead: Number(usage.cache_read_input_tokens),
  };
  const component = (value, rate) => (Number.isFinite(value) && value > 0 ? (value / 1_000_000) * rate : 0);
  const total = component(tokens.input, pricing.input)
    + component(tokens.output, pricing.output)
    + component(tokens.cacheWrite, pricing.cacheWrite)
    + component(tokens.cacheRead, pricing.cacheRead);
  return total > 0 ? total : null;
}

function getAiUsageSummary(profile) {
  const usage = profile?.analysis_usage;
  if (!usage || typeof usage !== "object") return null;

  const inputTokens = Number.isFinite(Number(usage.input_tokens)) ? Number(usage.input_tokens) : null;
  const outputTokens = Number.isFinite(Number(usage.output_tokens)) ? Number(usage.output_tokens) : null;
  const cacheWriteTokens = Number.isFinite(Number(usage.cache_creation_input_tokens)) ? Number(usage.cache_creation_input_tokens) : null;
  const cacheReadTokens = Number.isFinite(Number(usage.cache_read_input_tokens)) ? Number(usage.cache_read_input_tokens) : null;
  const totalTokens = Number.isFinite(Number(usage.total_tokens))
    ? Number(usage.total_tokens)
    : [inputTokens, outputTokens, cacheWriteTokens, cacheReadTokens].reduce((sum, value) => sum + (value || 0), 0) || null;
  const costUsd = Number.isFinite(Number(usage.estimated_cost_usd))
    ? Number(usage.estimated_cost_usd)
    : estimateAiCostFromUsage(profile.analysis_model, usage);

  const parts = [];
  if (inputTokens != null) parts.push(`in ${fmtInt(inputTokens)}`);
  if (outputTokens != null) parts.push(`out ${fmtInt(outputTokens)}`);
  if (cacheReadTokens) parts.push(`cache read ${fmtInt(cacheReadTokens)}`);
  if (cacheWriteTokens) parts.push(`cache write ${fmtInt(cacheWriteTokens)}`);

  return {
    model: profile.analysis_model || usage.model || "",
    inputTokens,
    outputTokens,
    totalTokens,
    totalLabel: totalTokens != null ? `${fmtInt(totalTokens)} tok.` : "",
    breakdownLabel: parts.join(" · "),
    costUsd,
    costLabel: costUsd != null ? `~${fmtUsd(costUsd)}` : "",
  };
}

function getInfo(profile, key) {
  const item = (profile.basic_info || []).find(
    (i) => i.label.toLowerCase().includes(key.toLowerCase())
  );
  return item ? item.value : null;
}

function metricLabel(m) {
  return { revenue: "tržby", operating_profit: "provozní zisk", net_profit: "čistý zisk", assets: "aktiva", equity: "vlastní kapitál", liabilities: "cizí zdroje", debt: "dluh" }[m] || m;
}

function createStatusEntry(label) {
  return { id: `status-${++_statusSeq}`, label: String(label ?? "").trim() };
}

function resetStatusLog(labels = []) {
  state.statusLog = labels
    .filter(Boolean)
    .map(createStatusEntry)
    .slice(-LOADING_STATUS_LIMIT);
}

function pushStatusLog(label) {
  if (!label) return;
  state.statusLog = [...state.statusLog, createStatusEntry(label)].slice(-LOADING_STATUS_LIMIT);
}

function visibleStatusLog() {
  return state.statusLog.length
    ? state.statusLog
    : [{ id: "status-placeholder", label: randomLoadingMessage() }];
}

function getExpandKeys(key) {
  return key === "all-insights-bundle"
    ? ["all-insights", "all-praskac"]
    : [key];
}

function shouldAnimatePanelReveal(key, index) {
  return index >= 3 && !!_pendingPanelRevealKeys?.has(key);
}

function panelRevealDelay(index) {
  return Math.min((index - 3) * 36, 144);
}

function prefersReducedMotion() {
  return typeof window !== "undefined"
    && typeof window.matchMedia === "function"
    && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

function staleRequestError() {
  const error = new Error("Stale request");
  error.name = "StaleRequestError";
  return error;
}

function isStaleRequestError(error) {
  return error?.name === "StaleRequestError";
}

function isNotFoundError(error) {
  return Number(error?.status) === 404;
}

function cacheProfile(profile) {
  const subjectId = String(profile?.subject_id || "").trim();
  if (subjectId) _profileCache.set(subjectId, profile);
  return profile;
}

async function openLoadedProfile(id, profile, requestToken) {
  state.profile = cacheProfile(profile);
  state.loading = false;
  state.preview = null;
  state.statusLog = [];
  history.pushState({ subjektId: id }, "", `/firma/${id}`);
  render();
  const historyUpdated = await refreshHistoryPage(0, { updateRecent: true });
  ensureActiveRequest(requestToken);
  if (historyUpdated) renderHistory();
}

function nextRequestToken() {
  _activeRequestToken += 1;
  return _activeRequestToken;
}

function ensureActiveRequest(token, reader) {
  if (token === _activeRequestToken) return;
  if (reader) {
    try {
      const maybePromise = reader.cancel();
      if (maybePromise && typeof maybePromise.catch === "function") {
        maybePromise.catch(() => {});
      }
    } catch {}
  }
  throw staleRequestError();
}

function canAnimateViewTransition() {
  return !prefersReducedMotion()
    && typeof document !== "undefined"
    && typeof document.startViewTransition === "function";
}

function runViewTransition(type, update) {
  if (!type || !canAnimateViewTransition()) {
    update();
    return Promise.resolve();
  }

  const root = document.documentElement;
  root.dataset.viewTransition = type;
  const transition = document.startViewTransition(() => {
    update();
  });
  const cleanup = () => {
    if (root.dataset.viewTransition === type) delete root.dataset.viewTransition;
  };

  transition.finished.then(cleanup, cleanup);
  return transition.finished.catch(() => {});
}

function focusHeroInput({ selectionStart = null, selectionEnd = null } = {}) {
  const input = document.getElementById("hero-input");
  if (!input) return false;

  input.focus({ preventScroll: true });

  if (typeof input.setSelectionRange === "function") {
    const caret = input.value.length;
    input.setSelectionRange(
      selectionStart ?? caret,
      selectionEnd ?? selectionStart ?? caret
    );
  }

  return true;
}

function captureHeroInputState() {
  const active = document.activeElement;
  if (active?.id !== "hero-input") return null;

  return {
    selectionStart: active.selectionStart,
    selectionEnd: active.selectionEnd,
  };
}

function captureSearchStageTransitionFromHome() {
  if (prefersReducedMotion() || state.loading || state.profile || state.error) return null;

  const brand = document.querySelector("[data-search-brand-mark]");
  if (!brand) return null;

  const rect = brand.getBoundingClientRect();
  if (!rect.width || !rect.height) return null;

  return { brandRect: rect };
}

function queueSearchStageAnimation(snapshot) {
  _pendingSearchStageAnimation = snapshot;
}

function playPendingSearchStageAnimation() {
  const snapshot = _pendingSearchStageAnimation;
  _pendingSearchStageAnimation = null;

  if (!snapshot || prefersReducedMotion()) return;

  const brand = document.querySelector("[data-search-brand-mark]");
  if (!brand) return;

  const lastRect = brand.getBoundingClientRect();
  if (!lastRect.width || !lastRect.height) return;

  const firstCenterX = snapshot.brandRect.left + snapshot.brandRect.width / 2;
  const firstCenterY = snapshot.brandRect.top + snapshot.brandRect.height / 2;
  const lastCenterX = lastRect.left + lastRect.width / 2;
  const lastCenterY = lastRect.top + lastRect.height / 2;
  const deltaX = firstCenterX - lastCenterX;
  const deltaY = firstCenterY - lastCenterY;
  const scale = snapshot.brandRect.height / lastRect.height;

  brand.animate(
    [
      {
        transform: `translate(${deltaX}px, ${deltaY}px) scale(${scale})`,
        opacity: 0.96,
      },
      {
        transform: "translate(0, 0) scale(1)",
        opacity: 1,
      },
    ],
    {
      duration: 260,
      easing: "cubic-bezier(0.22, 1, 0.36, 1)",
      fill: "both",
    }
  );

  const body = document.querySelector("[data-search-stage-body]");
  if (!body) return;

  body.animate(
    [
      { opacity: 0, transform: "translateY(20px)" },
      { opacity: 1, transform: "translateY(0)" },
    ],
    {
      duration: 220,
      delay: 30,
      easing: "cubic-bezier(0.22, 1, 0.36, 1)",
      fill: "both",
    }
  );
}

function searchBrandMarkup({ compact = false, subtitle = "" } = {}) {
  const iconClass = compact
    ? "w-14 h-14 mb-3 mx-auto"
    : "w-20 h-20 mb-4 mx-auto";
  const titleClass = compact
    ? "text-xl sm:text-2xl font-bold tracking-tight text-ink-950"
    : "text-2xl sm:text-3xl font-bold tracking-tight text-ink-950";

  return `
    <div class="search-brand-mark ${compact ? "search-brand-mark-compact" : ""}" data-search-brand-mark>
      <img src="/praskac-icon-sm.png" alt="Justice Práskač" class="${iconClass}">
      <h1 class="${titleClass}">Justice Práskač</h1>
    </div>
    ${subtitle ? `<p class="mt-2 text-sm font-mono text-ink-400">${esc(subtitle)}</p>` : ""}`;
}

const FALLBACK_SENTENCE_ABBREVIATIONS = [
  "a.s.",
  "s.r.o.",
  "v.o.s.",
  "k.s.",
  "spol.",
  "např.",
  "tj.",
  "tzn.",
  "mil.",
  "mld.",
  "tis.",
];

function normalizeInlineText(text) {
  return String(text ?? "").replace(/\s+/g, " ").trim();
}

function splitSentences(text) {
  let normalized = normalizeInlineText(text);
  if (!normalized) return [];

  const replacements = new Map();
  FALLBACK_SENTENCE_ABBREVIATIONS.forEach((abbr, index) => {
    const token = `__abbr_${index}__`;
    normalized = normalized.split(abbr).join(token);
    replacements.set(token, abbr);
  });

  return normalized
    .split(/(?<=[.!?])\s+(?=[\p{Lu}\d])/u)
    .map((part) => {
      let restored = part.trim();
      replacements.forEach((abbr, token) => {
        restored = restored.split(token).join(abbr);
      });
      return restored;
    })
    .filter(Boolean);
}

function getExecutiveSummaryContent(profile) {
  const overview = normalizeInlineText(profile.analysis_overview || "");
  const items = profile.insight_summary || [];
  const note = normalizeInlineText(profile.data_quality_note || "");
  const sentences = splitSentences(overview);

  return {
    lead: sentences[0] || "",
    followUps: sentences.slice(1),
    items,
    note,
  };
}

// ---- Shared severity detection ----
function severityOf(item) {
  const t = ((item.title || "") + " " + (item.detail || "")).toLowerCase();
  if (t.includes("vysoký") || t.includes("ztráta") || t.includes("problém") || t.includes("rizik") || t.includes("velký") || t.includes("významn")) return "high";
  if (t.includes("střední") || t.includes("zpoždění") || t.includes("chybí") || t.includes("mezera") || t.includes("pokles")) return "medium";
  return "low";
}

const SEV_DOT = { high: "bg-red-500", medium: "bg-amber-400", low: "bg-ink-300" };

// ---- Shared Tailwind class strings ----
const CLS_CARD = "bg-ink-50 border border-ink-200";
const CLS_SECTION_HEADING = "text-lg font-bold text-ink-900 tracking-tight";
const CLS_LABEL_XS = "text-[10px] font-mono text-ink-400 uppercase tracking-widest";
const CLS_BADGE_SM = "inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-medium ring-1";
const CLS_SUBSECTION_HEADING = "text-sm font-mono font-semibold text-ink-500 uppercase tracking-widest";
const MODE_COLOR = { digital: "text-emerald-700 bg-emerald-50 ring-emerald-200/60", ocr: "text-amber-700 bg-amber-50 ring-amber-200/60", mixed: "text-ink-600 bg-ink-100 ring-ink-200/60" };

function valColor(v, { neutral = "text-ink-950", zero = "text-ink-950" } = {}) {
  if (v == null || !isFinite(v)) return neutral;
  if (v > 0) return "text-emerald-700";
  if (v < 0) return "text-red-600";
  return zero;
}

// ---- History rendering ----
function historyItemHtml(item, variant = "rail") {
  const isHero = variant === "hero";
  const buttonClass = isHero
    ? `w-full text-left px-3.5 py-3 ${CLS_CARD} hover:bg-ink-100 transition-colors group`
    : "w-full text-left px-3 py-2.5 text-sm hover:bg-ink-100 transition-colors group";
  return `<button type="button" data-pick-id="${esc(item.subject_id)}" data-pick-query="${esc(item.query || item.ico || item.name || "")}"
    class="${buttonClass}">
    <div class="font-medium text-ink-900 truncate group-hover:text-ink-950 leading-snug">${esc(item.name || "Firma")}</div>
    <div class="text-[11px] font-mono text-ink-400 truncate mt-0.5">${esc(item.ico || "")}${item.updated_at ? " · " + fmtRelative(item.updated_at) : ""}</div>
  </button>`;
}

function historyRangeText() {
  if (!state.historyTotal || !state.history.length) return "";
  const start = state.historyOffset + 1;
  const end = Math.min(state.historyOffset + state.history.length, state.historyTotal);
  return `${start}-${end} z ${state.historyTotal}`;
}

function historyHasPrevPage() {
  return state.historyOffset > 0;
}

function historyHasNextPage() {
  return state.historyOffset + state.history.length < state.historyTotal;
}

function historyPaginationHtml(variant = "rail") {
  if (!state.historyTotal) return "";
  const isHero = variant === "hero";
  const btnClass = isHero
    ? "inline-flex items-center justify-center min-w-[88px] px-3 py-2 bg-ink-50 text-xs font-mono font-medium text-ink-600 border border-ink-200 hover:bg-ink-100 hover:text-ink-800 transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
    : "inline-flex items-center justify-center min-w-[78px] px-2.5 py-1.5 bg-ink-50 text-[11px] font-mono font-medium text-ink-600 border border-ink-200 hover:bg-ink-100 hover:text-ink-800 transition-colors disabled:opacity-40 disabled:cursor-not-allowed";

  return `
    <div class="${isHero ? "mt-3 flex items-center justify-between gap-2" : "mt-2 px-1 flex items-center justify-between gap-2"}">
      ${!isHero ? `<div class="text-[11px] font-mono text-ink-400">${esc(historyRangeText())}</div>` : '<div class="text-[11px] font-mono text-ink-400">Stránkování historie</div>'}
      <div class="flex items-center gap-2">
        <button type="button" data-history-page="prev" class="${btnClass}" ${historyHasPrevPage() ? "" : "disabled"}>Novější</button>
        <button type="button" data-history-page="next" class="${btnClass}" ${historyHasNextPage() ? "" : "disabled"}>Starší</button>
      </div>
    </div>`;
}

function historySidebarHtml() {
  if (!state.history.length) {
    return '<div class="px-3 py-4 text-xs font-mono text-ink-400">Historie se začne plnit po prvním prověření.</div>';
  }

  return `
    <div class="space-y-0.5">
      ${state.history.map((item) => historyItemHtml(item, "rail")).join("")}
    </div>
    ${historyPaginationHtml("rail")}`;
}

function historyHeroHtml() {
  return `
    <div class="mt-8 pt-6 border-t border-ink-200 lg:hidden">
      <div class="flex items-center justify-between gap-3 mb-3">
        <div class="${CLS_LABEL_XS}">Historie prověření</div>
        ${state.historyTotal ? `<div class="text-[11px] font-mono text-ink-400">${esc(historyRangeText())}</div>` : ""}
      </div>
      ${state.history.length
        ? `<div class="space-y-2">${state.history.map((item) => historyItemHtml(item, "hero")).join("")}</div>`
        : `<div class="${CLS_CARD} px-4 py-4 text-sm font-mono text-ink-400">Historie se začne plnit po prvním prověření.</div>`}
      ${state.historyTotal ? historyPaginationHtml("hero") : ""}
    </div>`;
}

function renderHistory() {
  const html = historySidebarHtml();
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
function activeAutocompleteMatch() {
  if (!state.autocompleteOpen) return null;
  return state.autocompleteResults[state.autocompleteActiveIndex] || state.autocompleteResults[0] || null;
}

function renderAutocomplete(dropdownId = "hero-autocomplete") {
  const dropdown = document.getElementById(dropdownId);
  if (!dropdown) return;
  dropdown.innerHTML = autocompleteHtml(state.autocompleteResults, state.autocompleteActiveIndex);
}

function autocompleteHtml(results, activeIndex = -1) {
  if (!results.length) return "";
  return `
  <div class="autocomplete-dropdown bg-ink-50 border border-ink-900 overflow-hidden max-h-[320px] overflow-y-auto" role="listbox" aria-label="Nalezené firmy">
    ${results.map((m, index) => `
      <button type="button" data-pick-id="${esc(m.subject_id)}" data-pick-query="${esc(m.name || m.ico || "")}"
        data-autocomplete-index="${index}" role="option" aria-selected="${index === activeIndex ? "true" : "false"}"
        class="autocomplete-option ${index === activeIndex ? "is-active" : ""} w-full flex items-center justify-between px-4 py-3 hover:bg-ink-100 transition-colors text-left group border-b border-ink-200 last:border-0">
        <div class="min-w-0">
          <div class="text-sm font-medium text-ink-900 group-hover:text-ink-950 truncate">${esc(m.name)}</div>
          <div class="text-xs font-mono text-ink-400 mt-0.5 truncate">IČO ${esc(m.ico_display || m.ico)}${m.address ? " · " + esc(m.address) : ""}</div>
        </div>
        <svg class="w-4 h-4 text-ink-300 group-hover:text-ink-600 flex-shrink-0 ml-3" fill="none" viewBox="0 0 24 24" stroke-width="2" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" d="m8.25 4.5 7.5 7.5-7.5 7.5"/></svg>
      </button>`).join("")}
  </div>`;
}

function clearAutocomplete() {
  state.autocompleteResults = [];
  state.autocompleteActiveIndex = -1;
  state.autocompleteOpen = false;
  state.autocompleteLoading = false;
  if (_acTimer) { clearTimeout(_acTimer); _acTimer = null; }
  if (_acController) { _acController.abort(); _acController = null; }
  const heroAc = document.getElementById("hero-autocomplete");
  if (heroAc) heroAc.innerHTML = "";
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
      state.autocompleteActiveIndex = state.autocompleteOpen ? 0 : -1;
      renderAutocomplete(dropdownId);
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
      ready: "bg-ink-300",
      running: "bg-ink-500 status-running",
      done: "bg-emerald-500",
      error: "bg-red-400",
      waiting: "bg-amber-400",
    }[type] || "bg-ink-300");
  }
}

// ============================================================
// VIEWS
// ============================================================

function heroView(animation = "") {
  const recent = state.historyRecent;
  const heroAnimationClass = animation === "home-reset" ? "home-return-enter" : "";

  return `
  <div class="hero-centered px-4 sm:px-6 ${heroAnimationClass}" data-search-stage="home">
    <div class="hero-shell w-full max-w-xl mx-auto">
      <div class="text-center mb-8">
        ${searchBrandMarkup({ subtitle: "Prověř firmu z veřejných rejstříků" })}
      </div>
      <!-- Hero search input -->
      <form id="hero-search-form" class="relative">
        <div class="relative">
          <div class="pointer-events-none absolute inset-y-0 left-0 flex items-center pl-4">
            <svg class="h-5 w-5 text-ink-400" fill="none" viewBox="0 0 24 24" stroke-width="2" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" d="m21 21-5.197-5.197m0 0A7.5 7.5 0 1 0 5.196 5.196a7.5 7.5 0 0 0 10.607 10.607Z"/></svg>
          </div>
          <input id="hero-input" type="text" placeholder="Název firmy nebo IČO..." autocomplete="off" aria-label="Název firmy nebo IČO"
            value="${esc(state.query)}"
            class="block w-full border border-ink-900 bg-ink-50 py-3.5 pl-11 pr-[5.5rem] sm:pl-12 sm:pr-24 text-base text-ink-950 placeholder:text-ink-400 focus:ring-2 focus:ring-ink-900 focus:ring-offset-0 transition-colors">
          <div class="absolute inset-y-0 right-0 flex items-center pr-2">
            <button type="submit" class="bg-ink-900 px-4 py-2 text-sm font-mono font-semibold text-ink-50 hover:bg-ink-950 transition-colors">
              Prověřit
            </button>
          </div>
        </div>
        <!-- Autocomplete dropdown renders here -->
        <div id="hero-autocomplete" class="absolute left-0 right-0 top-full mt-1 z-50"></div>
      </form>
      ${historyHeroHtml()}
      ${recent.length ? `
      <div class="hidden lg:block mt-8 pt-6 border-t border-ink-200">
        <div class="${CLS_LABEL_XS} mb-3 text-center">Poslední prověření</div>
        <div class="grid grid-cols-1 sm:grid-cols-3 gap-px bg-ink-200 border border-ink-200">
          ${recent.map((item) => `
            <button type="button" data-pick-id="${esc(item.subject_id)}" data-pick-query="${esc(item.query || item.ico || item.name || "")}"
              class="text-left px-3.5 py-3 bg-ink-50 hover:bg-ink-100 transition-colors group">
              <div class="text-sm font-medium text-ink-900 truncate group-hover:text-ink-950">${esc(item.name || "Firma")}</div>
              <div class="text-[11px] font-mono text-ink-400 mt-0.5 truncate">${esc(item.ico || "")}${item.updated_at ? " · " + fmtRelative(item.updated_at) : ""}</div>
            </button>`).join("")}
        </div>
      </div>` : ""}
    </div>
  </div>`;
}

function statusIconMarkup() {
  return `
    <span class="loading-status-icon-state loading-status-icon-complete" aria-hidden="true">
      <svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke-width="2.5" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" d="m4.5 12.75 6 6 9-13.5"/></svg>
    </span>
    <span class="loading-status-icon-state loading-status-icon-current" aria-hidden="true">
      <span class="loading-status-spinner"></span>
    </span>`;
}

function renderStatusLine(item, index, total) {
  const isCurrent = index === total - 1;
  return `
    <div class="loading-status-item ${isCurrent ? "is-current" : "is-complete"}" data-status-id="${esc(item.id)}">
      <div class="loading-status-item-icon">${statusIconMarkup()}</div>
      <div class="loading-status-item-label">${esc(item.label)}</div>
    </div>`;
}

function renderStatusLines(items) {
  return items.map((item, index) => renderStatusLine(item, index, items.length)).join("");
}

function createStatusLineElement(item, index, total) {
  const wrapper = document.createElement("div");
  wrapper.innerHTML = renderStatusLine(item, index, total).trim();
  return wrapper.firstElementChild;
}

function syncLoadingStatusLines(container, items) {
  const reduceMotion = prefersReducedMotion();
  const existing = Array.from(container.querySelectorAll(".loading-status-item"));
  const existingById = new Map(existing.map((node) => [node.dataset.statusId, node]));
  const firstRects = reduceMotion
    ? new Map()
    : new Map(existing.map((node) => [node.dataset.statusId, node.getBoundingClientRect()]));
  const nextIds = new Set(items.map((item) => item.id));

  existing.forEach((node) => {
    if (!nextIds.has(node.dataset.statusId)) node.remove();
  });

  items.forEach((item, index) => {
    let node = existingById.get(item.id);
    if (!node) {
      node = createStatusLineElement(item, index, items.length);
    } else {
      node.classList.toggle("is-current", index === items.length - 1);
      node.classList.toggle("is-complete", index !== items.length - 1);
      const label = node.querySelector(".loading-status-item-label");
      if (label) label.textContent = item.label;
    }

    const anchor = container.children[index] || null;
    if (anchor !== node) container.insertBefore(node, anchor);
  });

  if (reduceMotion) return;

  Array.from(container.querySelectorAll(".loading-status-item")).forEach((node) => {
    const first = firstRects.get(node.dataset.statusId);
    if (!first) {
      node.animate(
        [
          { opacity: 0, transform: "translateY(8px)" },
          { opacity: 1, transform: "translateY(0)" },
        ],
        {
          duration: 180,
          easing: "cubic-bezier(0.22, 1, 0.36, 1)",
        }
      );
      return;
    }
    const last = node.getBoundingClientRect();
    const deltaY = first.top - last.top;
    if (Math.abs(deltaY) < 1) return;
    node.animate(
      [
        { transform: `translateY(${deltaY}px)` },
        { transform: "translateY(0)" },
      ],
      {
        duration: 220,
        easing: "cubic-bezier(0.22, 1, 0.36, 1)",
      }
    );
  });
}

function loadingView(previewOrText, log, opts = {}) {
  const items = (log || []).length ? log : visibleStatusLog();
  const preview = typeof previewOrText === "object" ? previewOrText : null;
  const animateIn = opts.animateIn !== false;

  return `
  <div id="loading-view" data-has-preview="${preview ? '1' : '0'}" class="max-w-2xl mx-auto px-4 sm:px-6 py-12 ${animateIn ? "view-enter" : ""}">
    <div class="text-center mb-6">
      ${searchBrandMarkup({
        compact: true,
        subtitle: "Stahuji data z justice.cz a zpracovávám podklady.",
      })}
    </div>
    <div data-search-stage-body class="${CLS_CARD} overflow-hidden">
      ${preview ? `
      <div class="px-5 py-4 border-b border-ink-200">
        <div>
          <div class="text-sm font-semibold text-ink-900">${esc(preview.name || "Načítám firmu")}</div>
          <div class="text-xs font-mono text-ink-400">IČO ${esc(preview.ico || "—")}</div>
        </div>
      </div>` : `
      <div class="px-5 py-4 border-b border-ink-200">
        <div>
          <div class="text-sm font-semibold text-ink-900">Zpracovávám veřejné rejstříky</div>
          <div class="text-xs font-mono text-ink-400">Justice.cz, sbírka listin, finanční výkazy</div>
        </div>
      </div>`}
      <div class="px-5 py-4">
        <div id="loading-status-lines" class="loading-status-list" aria-live="polite" aria-relevant="additions text">
          ${renderStatusLines(items)}
        </div>
      </div>
      ${preview && (preview.basic_info || []).length ? `
      <div class="border-t border-ink-200 px-5 py-4">
        <div class="grid grid-cols-2 gap-2">
          ${(preview.basic_info || []).slice(0, 4).map((i) => `
            <div class="text-xs font-mono"><span class="text-ink-400">${esc(i.label)}:</span> <span class="text-ink-600">${esc(i.value)}</span></div>
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
      <div class="w-10 h-10 border border-red-300 bg-red-50 text-red-500 flex items-center justify-center mx-auto mb-3">
        <svg class="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke-width="2" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" d="M12 9v3.75m9-.75a9 9 0 1 1-18 0 9 9 0 0 1 18 0Zm-9 3.75h.008v.008H12v-.008Z"/></svg>
      </div>
      <h2 class="text-lg font-bold text-ink-900 mb-1">Načtení se nezdařilo</h2>
      <p class="text-sm text-ink-500 mb-4">${esc(text)}</p>
      <button data-retry class="inline-flex items-center px-4 py-2 bg-ink-900 text-ink-50 text-sm font-mono font-semibold hover:bg-ink-950 transition-colors">Zkusit znovu</button>
    </div>
  </div>`;
}

// ============================================================
// PROFILE VIEW
// ============================================================

function sectionNav() {
  const tabs = [
    ["overview", "Přehled"],
    ["analysis", "Analýza"],
    ["finance", "Finance"],
    ["people", "Osoby"],
    ["sources", "Zdroje"],
  ];
  return `
  <nav id="section-nav" class="sticky top-14 z-30 border-b border-ink-900 bg-ink-50 lg:top-0">
    <div class="px-4 py-2 sm:px-6">
      <div class="flex gap-0 overflow-x-auto scrollbar-hide">
      ${tabs.map(([id, label]) => `
        <button data-nav="${id}" class="px-3 py-2.5 sm:py-1.5 text-sm font-mono font-medium whitespace-nowrap transition-colors border-b-2
          ${id === "overview" ? "border-ink-900 text-ink-900" : "border-transparent text-ink-400 hover:text-ink-700 hover:border-ink-300"}">${esc(label)}</button>
      `).join("")}
      </div>
    </div>
  </nav>`;
}

function profileHero(p) {
  const tl = p.financial_timeline || [];
  const latest = tl[tl.length - 1];
  const prev = tl.length >= 2 ? tl[tl.length - 2] : null;
  const yoy = latest && prev && latest.revenue && prev.revenue
    ? ((latest.revenue - prev.revenue) / (Math.abs(prev.revenue) || 1)) * 100 : null;

  const legalForm = getInfo(p, "právní forma") || "";
  const city = getInfo(p, "sídlo") || "";
  const created = getInfo(p, "datum") || "";

  const { lead } = getExecutiveSummaryContent(p);

  // Key metrics only — the essentials
  const metrics = [];
  if (latest?.revenue != null) metrics.push({ label: "Tržby", value: fmtM(latest.revenue), sub: `${latest.year}`, color: "text-ink-950" });
  metrics.push({ label: "Čistý zisk", value: fmtM(latest?.net_profit), sub: latest ? `zisková marže ${fmtPct(latest.net_margin_pct)}` : "—", color: valColor(latest?.net_profit) });
  metrics.push({ label: "Vlastní kapitál", value: fmtPct(latest?.equity_ratio_pct), sub: latest ? `podíl vlastních zdrojů` : "—", color: latest?.equity_ratio_pct != null ? (latest.equity_ratio_pct >= 30 ? "text-emerald-700" : latest.equity_ratio_pct >= 10 ? "text-amber-600" : "text-red-600") : "text-ink-950" });
  if (yoy != null && isFinite(yoy)) metrics.push({ label: "Růst tržeb", value: fmtPct(yoy), sub: `${prev.year} → ${latest.year}`, color: valColor(yoy) });
  if (!metrics.some((m) => m.label === "Růst tržeb") && latest?.debt != null) {
    metrics.push({ label: "Dluh", value: fmtM(latest.debt), sub: `${latest.year}`, color: "text-ink-950" });
  }

  const isAiRetryOnly = p.analysis_engine === "fallback";
  const rerunClasses = "inline-flex items-center gap-1.5 px-3.5 py-2 sm:px-3 sm:py-1.5 text-xs font-mono font-medium text-ink-500 border border-ink-300 hover:bg-ink-100 hover:text-ink-900 transition-colors";
  const aiRunClasses = "inline-flex items-center gap-1.5 px-3.5 py-2 sm:px-3 sm:py-1.5 text-xs font-mono font-medium text-ink-50 bg-ink-900 hover:bg-ink-950 transition-colors";

  return `
  <section id="section-overview" data-section="overview" class="scroll-mt-32 lg:scroll-mt-24">
    <!-- Identity -->
    <div class="flex flex-col sm:flex-row sm:items-start sm:justify-between gap-3 mb-4">
      <div class="min-w-0">
        <h1 class="text-3xl sm:text-4xl font-bold tracking-tight text-ink-950 leading-none break-words">${esc(p.name)}</h1>
        <div class="mt-2 flex flex-wrap items-center gap-x-3 gap-y-0.5 text-sm text-ink-500">
          <span class="font-mono text-ink-400">IČO ${esc(p.ico || "—")}</span>
          ${legalForm ? `<span class="text-ink-300">·</span><span>${esc(legalForm)}</span>` : ""}
          ${city ? `<span class="text-ink-300">·</span><span class="truncate max-w-[220px]">${esc(city.split(",")[0])}</span>` : ""}
          ${created ? `<span class="text-ink-300">·</span><span class="font-mono text-ink-400">${esc(created)}</span>` : ""}
        </div>
      </div>
      <div class="flex flex-wrap items-center gap-2 flex-shrink-0">
        ${isAiRetryOnly ? `
          <button data-run-ai="${esc(p.subject_id)}" class="${aiRunClasses}">
            <svg class="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke-width="2" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" d="M9.813 15.904 9 18.75l-.813-2.846a4.5 4.5 0 0 0-3.09-3.09L2.25 12l2.846-.813a4.5 4.5 0 0 0 3.09-3.09L9 5.25l.813 2.846a4.5 4.5 0 0 0 3.09 3.09L15.75 12l-2.846.813a4.5 4.5 0 0 0-3.09 3.09ZM18.259 8.715 18 9.75l-.259-1.035a3.375 3.375 0 0 0-2.455-2.456L14.25 6l1.036-.259a3.375 3.375 0 0 0 2.455-2.456L18 2.25l.259 1.035a3.375 3.375 0 0 0 2.455 2.456L21.75 6l-1.036.259a3.375 3.375 0 0 0-2.455 2.456Z"/></svg>
            Spustit AI analýzu
          </button>` : ""}
        <button data-rerun="${esc(p.subject_id)}" class="${rerunClasses}">
          <svg class="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke-width="2" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" d="M16.023 9.348h4.992v-.001M2.985 19.644v-4.992m0 0h4.992m-4.993 0 3.181 3.183a8.25 8.25 0 0 0 13.803-3.7M4.031 9.865a8.25 8.25 0 0 1 13.803-3.7l3.181 3.182"/></svg>
          Načíst znovu
        </button>
      </div>
    </div>
    <!-- AI summary — the most important sentence -->
    ${lead ? `<p class="text-base text-ink-600 leading-relaxed mb-6 max-w-2xl">${esc(lead)}</p>` : ""}
    <!-- Key metrics -->
    ${metrics.length ? `
    <div class="grid grid-cols-2 sm:grid-cols-${Math.min(metrics.length, 4)} gap-px bg-ink-200 border border-ink-200">
      ${metrics.map((m) => `
        <div class="px-4 py-3.5 bg-ink-50">
          <div class="${CLS_LABEL_XS} mb-1">${esc(m.label)}</div>
          <div class="text-xl font-bold tracking-tight ${m.color || "text-ink-950"} font-mono tabular-nums leading-tight">${esc(m.value)}</div>
          <div class="text-[11px] font-mono text-ink-400 mt-0.5">${esc(m.sub)}</div>
        </div>`).join("")}
    </div>` : ""}
  </section>`;
}

function financialOverview(p) {
  const tl = p.financial_timeline || [];
  if (!tl.length) {
    return `
    <section id="section-finance" data-section="finance" class="scroll-mt-32 lg:scroll-mt-24">
      <div class="${CLS_CARD} p-5 text-center">
        <p class="text-sm font-mono text-ink-400">Finanční data nejsou k dispozici — veřejné výkazy neobsahují strojově čitelné údaje.</p>
      </div>
    </section>`;
  }

  const sorted = [...tl].sort((a, b) => a.year - b.year);
  const hasRevenue = sorted.some((r) => r.revenue != null);

  return `
  <section id="section-finance" data-section="finance" class="scroll-mt-32 lg:scroll-mt-24 space-y-4">
    <div class="flex items-center justify-between">
      <h2 class="${CLS_SECTION_HEADING}">Finance</h2>
      <span class="text-sm font-mono text-ink-400">${sorted[0].year}–${sorted[sorted.length - 1].year}</span>
    </div>
    <!-- Chart -->
    <div class="border border-ink-200 p-4">
      <div class="chart-wrap"><canvas id="finance-chart" aria-label="Graf finančního vývoje"></canvas></div>
    </div>
    <!-- Stats strip -->
    <div class="grid grid-cols-2 sm:grid-cols-4 gap-px bg-ink-200 border border-ink-200">
      ${(() => {
        const last = sorted[sorted.length - 1];
        const first = sorted[0];
        const yrs = sorted.length;
        const strips = [];
        if (hasRevenue && last.revenue != null && first.revenue != null && yrs > 1) {
          const cagr = (Math.pow(last.revenue / first.revenue, 1 / (yrs - 1)) - 1) * 100;
          strips.push(["Průměrný růst", isFinite(cagr) ? fmtPct(cagr) : "—", valColor(isFinite(cagr) ? cagr : null, { neutral: "text-ink-900" })]);
        }
        if (last.equity_ratio_pct != null) strips.push(["Vlastní kapitál", fmtPct(last.equity_ratio_pct), last.equity_ratio_pct >= 30 ? "text-emerald-700" : last.equity_ratio_pct >= 10 ? "text-amber-600" : "text-red-600"]);
        if (last.net_margin_pct != null) strips.push(["Marže", fmtPct(last.net_margin_pct), valColor(last.net_margin_pct, { neutral: "text-ink-900" })]);
        strips.push(["Období", `${yrs} let`, "text-ink-900"]);
        return strips.map(([l, v, c]) => `
          <div class="bg-ink-50 px-3 py-2">
            <div class="${CLS_LABEL_XS}">${esc(l)}</div>
            <div class="text-sm font-bold font-mono ${c || "text-ink-900"} tabular-nums mt-0.5">${esc(v)}</div>
          </div>`).join("");
      })()}
    </div>
    <!-- Table -->
    <div class="border border-ink-200 overflow-hidden">
      <div class="fin-table-wrap">
        <table class="min-w-full text-sm font-mono fin-table">
          <thead>
            <tr class="border-b border-ink-900 ${CLS_LABEL_XS} text-ink-500">
              <th class="py-2 px-3 text-left bg-ink-100">Rok</th>
              ${hasRevenue ? '<th class="py-2 px-3 text-right bg-ink-100">Tržby</th>' : ""}
              <th class="py-2 px-3 text-right bg-ink-100">Zisk</th>
              <th class="py-2 px-3 text-right bg-ink-100">Aktiva</th>
              <th class="py-2 px-3 text-right bg-ink-100">Vl. kapitál</th>
              <th class="py-2 px-3 text-right bg-ink-100">Dluh</th>
              <th class="py-2 px-3 text-right bg-ink-100">Marže</th>
            </tr>
          </thead>
          <tbody class="divide-y divide-ink-100">
            ${sorted.map((r) => `
              <tr class="hover:bg-ink-100 transition-colors">
                <td class="py-2 px-3 font-semibold text-ink-900 tabular-nums">${esc(r.year)}</td>
                ${hasRevenue ? `<td class="py-2 px-3 text-right tabular-nums text-ink-700">${fmtM(r.revenue)}</td>` : ""}
                <td class="py-2 px-3 text-right tabular-nums ${valColor(r.net_profit, { neutral: "text-ink-700", zero: "text-ink-700" })} ${(r.net_profit ?? 0) < 0 ? "font-semibold" : ""}">${fmtM(r.net_profit)}</td>
                <td class="py-2 px-3 text-right tabular-nums text-ink-700">${fmtM(r.assets)}</td>
                <td class="py-2 px-3 text-right tabular-nums text-ink-700">${fmtM(r.equity)}</td>
                <td class="py-2 px-3 text-right tabular-nums text-ink-700">${fmtM(r.debt)}</td>
                <td class="py-2 px-3 text-right tabular-nums ${valColor(r.net_margin_pct, { neutral: "text-ink-500", zero: "text-ink-500" })}">${fmtPct(r.net_margin_pct)}</td>
              </tr>`).join("")}
          </tbody>
        </table>
      </div>
    </div>
  </section>`;
}

function analysisSection(p) {
  const praskac = p.praskac || [];
  const deep = p.deep_insights || [];
  const { followUps, items, note } = getExecutiveSummaryContent(p);
  const hasContent = praskac.length || deep.length || followUps.length || items.length;
  if (!hasContent) return "";

  return `
  <section id="section-analysis" data-section="analysis" class="scroll-mt-32 lg:scroll-mt-24 space-y-6">
    <h2 class="${CLS_SECTION_HEADING}">Analýza</h2>
    ${followUps.length ? `
    <ul class="space-y-2 border-l-2 border-ink-200 pl-4">
      ${followUps.map((sentence) => `
        <li class="text-sm text-ink-700 leading-relaxed">${esc(sentence)}</li>`).join("")}
    </ul>` : ""}
    ${items.length ? `
    <div class="grid grid-cols-1 sm:grid-cols-2 gap-px bg-ink-200 border border-ink-200">
      ${items.slice(0, 4).map((item) => `
        <div class="bg-ink-50 px-4 py-3 min-w-0">
          <div class="text-sm font-semibold text-ink-900 truncate">${esc(item.title)}</div>
          <div class="mt-1 text-sm text-ink-600 leading-relaxed break-words">${esc(item.detail)}</div>
        </div>`).join("")}
    </div>` : ""}
    ${deep.length ? `
    <div>
      <h3 class="${CLS_SUBSECTION_HEADING} mb-3">Detailní zjištění</h3>
      <div class="space-y-0 divide-y divide-ink-200 border-y border-ink-200">
        ${deep.slice(0, state.expandedPanels.has("all-insights") ? 999 : 4).map((item) => `
          <div class="px-4 py-3">
            <div class="text-sm font-semibold text-ink-900">${esc(item.title)}</div>
            <div class="mt-1 text-sm text-ink-600 leading-relaxed break-words">${esc(item.detail)}</div>
          </div>`).join("")}
      </div>
      ${deep.length > 4 && !state.expandedPanels.has("all-insights") ? `
        <button type="button" data-expand="all-insights-bundle" class="mt-2 px-3 py-2 text-sm font-mono font-medium text-ink-500 hover:text-ink-900 hover:bg-ink-100 transition-colors">+ ${deep.length - 4} dalších</button>` : ""}
    </div>` : ""}
    ${praskac.length ? `
    <div>
      <h3 class="${CLS_SUBSECTION_HEADING} mb-3">Upozornění z veřejných zdrojů</h3>
      <div class="space-y-0 divide-y divide-ink-200 border-y border-ink-200">
        ${praskac.slice(0, state.expandedPanels.has("all-praskac") ? 999 : 4).map((item) => {
          const sev = severityOf(item);
          const sevBorder = sev === "high" ? "border-l-3 border-l-red-500"
            : sev === "medium" ? "border-l-3 border-l-amber-500"
            : "";
          return `
          <div class="px-4 py-3 ${sevBorder}">
            <div class="text-sm font-semibold text-ink-900">${esc(item.title)}</div>
            <div class="mt-1 text-sm text-ink-600 leading-relaxed break-words">${esc(item.detail)}</div>
          </div>`;
        }).join("")}
      </div>
      ${praskac.length > 4 && !state.expandedPanels.has("all-praskac") ? `
        <button type="button" data-expand="all-insights-bundle" class="mt-2 px-3 py-2 text-sm font-mono font-medium text-ink-500 hover:text-ink-900 hover:bg-ink-100 transition-colors">+ ${praskac.length - 4} dalších</button>` : ""}
    </div>` : ""}
    ${note ? `<p class="text-xs font-mono text-ink-400 leading-relaxed">${esc(note)}</p>` : ""}
  </section>`;
}

function peopleSection(p) {
  const execs = p.executives || [];
  const owners = p.owners || [];
  const bodies = p.statutory_bodies || [];
  const signals = p.history_signals || {};

  return `
  <section id="section-people" data-section="people" class="scroll-mt-32 lg:scroll-mt-24">
    <div class="flex items-center justify-between mb-4">
      <h2 class="${CLS_SECTION_HEADING}">Osoby a struktura</h2>
    </div>
    <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
      <!-- Management -->
      <div class="${CLS_CARD} overflow-hidden">
        <div class="px-4 py-3 border-b border-ink-200 flex items-center justify-between">
          <h3 class="${CLS_LABEL_XS}">Vedení</h3>
          ${execs.length ? `<span class="text-[10px] font-mono text-ink-400">${execs.length} osob</span>` : ""}
        </div>
        ${execs.length ? `
        <div class="divide-y divide-ink-100">
          ${execs.slice(0, state.expandedPanels.has("all-execs") ? 999 : 5).map((e) => `
            <div class="px-4 py-2.5">
              <div class="text-sm font-medium text-ink-900 truncate">${esc(e.name || "—")}</div>
              <div class="text-xs font-mono text-ink-500 mt-0.5 truncate">${esc(e.role || "Člen statutárního orgánu")}${e.birth_date ? ` · nar. ${esc(e.birth_date)}` : ""}</div>
            </div>`).join("")}
        </div>
        ${execs.length > 5 && !state.expandedPanels.has("all-execs") ? `
          <div class="border-t border-ink-200">
            <button data-expand="all-execs" class="w-full text-left px-4 py-3 text-xs font-mono font-medium text-ink-500 hover:text-ink-900 hover:bg-ink-100 transition-colors">+ ${execs.length - 5} dalších</button>
          </div>` : ""}
        ` : '<div class="p-4 text-xs font-mono text-ink-400">Ve veřejném výpisu nejsou uvedeny osoby ve vedení.</div>'}
      </div>
      <!-- Ownership -->
      <div class="${CLS_CARD} overflow-hidden">
        <div class="px-4 py-3 border-b border-ink-200 flex items-center justify-between">
          <h3 class="${CLS_LABEL_XS}">Vlastníci a orgány</h3>
        </div>
        ${owners.length ? `
        <div class="divide-y divide-ink-100">
          ${owners.slice(0, 6).map((o) => `
            <div class="px-4 py-2.5">
              <div class="text-sm font-medium text-ink-900 truncate">${esc(o.role || "Vlastník / společník")}</div>
              <div class="text-xs font-mono text-ink-500 mt-0.5 line-clamp-2">${esc(o.raw || "")}</div>
            </div>`).join("")}
        </div>
        ` : bodies.length ? `
        <div class="divide-y divide-ink-100">
          ${bodies.slice(0, 4).map((b) => `
            <div class="px-4 py-2.5">
              <div class="text-sm font-medium text-ink-900 truncate">${esc(b.title)}</div>
              <div class="text-xs font-mono text-ink-500 mt-0.5">${(b.items || []).length} položek ve výpisu</div>
            </div>`).join("")}
        </div>
        ` : '<div class="p-4 text-xs font-mono text-ink-400">Vlastnická struktura není ve výpisu detailně rozepsána.</div>'}
        ${(signals.name_changes != null || signals.address_changes != null || signals.management_turnover != null) ? `
        <div class="px-4 py-3 border-t border-ink-200 bg-ink-100/50">
          <div class="${CLS_LABEL_XS} mb-2">Historické změny</div>
          <div class="flex flex-wrap gap-3 text-xs font-mono text-ink-600">
            ${signals.name_changes != null ? `<span>Název: <strong>${esc(signals.name_changes)}</strong></span>` : ""}
            ${signals.address_changes != null ? `<span>Sídlo: <strong>${esc(signals.address_changes)}</strong></span>` : ""}
            ${signals.management_turnover != null ? `<span>Vedení: <strong>${esc(signals.management_turnover)}</strong></span>` : ""}
          </div>
        </div>` : ""}
      </div>
    </div>
  </section>`;
}

function documentsAndSourcesSection(p) {
  const docs = p.financial_documents || [];
  const tl = p.financial_timeline || [];
  const links = p.source_links || {};
  const aiUsage = getAiUsageSummary(p);
  const extChecks = p.external_checks;

  const sourceLabels = {
    current_extract: "Aktuální výpis",
    full_extract: "Úplný výpis",
    documents: "Sbírka listin",
    current_extract_pdf: "PDF aktuálního výpisu",
    full_extract_pdf: "PDF úplného výpisu",
    chytryrejstrik: "Chytrý rejstřík",
  };

  const sourceLinks = Object.entries(links).filter(([, url]) => !!url);

  if (!docs.length && !sourceLinks.length) {
    return `
    <section id="section-sources" data-section="sources" class="scroll-mt-32 lg:scroll-mt-24">
      <h2 class="${CLS_SECTION_HEADING} mb-4">Zdroje</h2>
      <p class="text-sm font-mono text-ink-400">Ve sbírce listin nebyly nalezeny finanční dokumenty.</p>
    </section>`;
  }

  return `
  <section id="section-sources" data-section="sources" class="scroll-mt-32 lg:scroll-mt-24 space-y-5">
    <h2 class="${CLS_SECTION_HEADING}">Zdroje</h2>
    ${docs.length ? `
    <!-- Documents — simplified -->
    <div>
      <h3 class="${CLS_SUBSECTION_HEADING} mb-2">Listiny <span class="font-normal text-ink-400">(${docs.length})</span></h3>
      <div class="divide-y divide-ink-200 border border-ink-200 overflow-hidden">
        ${docs.map((doc) => {
          const files = doc.candidate_files || [];
          const yr = (doc.years || ["?"])[0];
          const key = `doc-${doc.document_number || doc.document_id || yr}`;
          const isOpen = state.expandedAccordions.has(key);
          const firstFile = files[0];
          const pdfUrl = firstFile ? `${API}/api/document/resolve?detailUrl=${encodeURIComponent(doc.detail_url || "")}&index=${encodeURIComponent(firstFile.pdf_index ?? 0)}&prefer_pdf=true` : "";

          return `
          <div class="${isOpen ? "bg-ink-100/30" : ""}">
            <button data-accordion="${esc(key)}" class="w-full flex items-center justify-between px-4 py-3 sm:py-2.5 hover:bg-ink-100/50 transition-colors text-left ${isOpen ? "accordion-open" : ""}">
              <div class="flex items-center gap-3 min-w-0">
                <span class="text-sm font-mono font-medium text-ink-900 tabular-nums w-10 flex-shrink-0">${esc(yr)}</span>
                <span class="text-sm text-ink-600 truncate">${esc(doc.type || "Listina")}</span>
              </div>
              <div class="flex items-center gap-2 flex-shrink-0 ml-3">
                ${pdfUrl ? `<a href="${esc(pdfUrl)}" target="_blank" rel="noopener noreferrer" class="text-xs font-mono font-medium text-ink-500 hover:text-ink-900" onclick="event.stopPropagation()">PDF</a>` : ""}
                <svg class="w-4 h-4 text-ink-400 transition-transform duration-200 accordion-chevron ${isOpen ? "rotate-180" : ""}" fill="none" viewBox="0 0 24 24" stroke-width="2" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" d="m19.5 8.25-7.5 7.5-7.5-7.5"/></svg>
              </div>
            </button>
            <div id="${esc(key)}" class="accordion-body ${isOpen ? "accordion-body-open" : ""}">
              <div class="accordion-body-inner border-t border-ink-200 divide-y divide-ink-100">
              ${files.map((f) => {
                const openUrl = `${API}/api/document/resolve?detailUrl=${encodeURIComponent(doc.detail_url || "")}&index=${encodeURIComponent(f.pdf_index ?? 0)}&prefer_pdf=true`;
                return `
                <a href="${esc(openUrl)}" target="_blank" rel="noopener noreferrer" class="flex items-center justify-between gap-3 px-4 py-2.5 hover:bg-ink-100/50 transition-colors no-underline">
                  <span class="text-xs font-mono text-ink-600 truncate">${esc(f.label || "PDF příloha")} · ${esc(String(f.page_count || "?"))} stran</span>
                  <span class="text-xs font-mono font-medium text-ink-500 flex-shrink-0">Otevřít &rarr;</span>
                </a>`;
              }).join("")}
              ${doc.detail_url ? `<a href="${esc(doc.detail_url)}" target="_blank" rel="noopener noreferrer" class="block px-4 py-2.5 text-xs font-mono text-ink-400 hover:text-ink-700 hover:bg-ink-100/50 transition-colors no-underline">Detail na justice.cz &rarr;</a>` : ""}
              </div>
            </div>
          </div>`;
        }).join("")}
      </div>
    </div>` : ""}
    ${extChecks && (extChecks.checks || []).length ? `
    <!-- External cross-check -->
    <div>
      <h3 class="${CLS_SUBSECTION_HEADING} mb-2">
        Externí kontrola
        ${extChecks.source_name ? `<a href="${esc(extChecks.source_url || "#")}" target="_blank" rel="noopener noreferrer" class="font-normal text-xs font-mono text-ink-400 hover:text-ink-700 ml-1">${esc(extChecks.source_name)} &rarr;</a>` : ""}
      </h3>
      <div class="divide-y divide-ink-200 border border-ink-200 overflow-hidden">
        ${(extChecks.checks || []).map((c) => `
          <div class="px-4 py-2.5 flex items-center justify-between gap-3 text-sm">
            <span class="text-ink-700 truncate min-w-0">${esc(c.label)}</span>
            <span class="font-mono tabular-nums flex-shrink-0 ${c.status === "warning" ? "text-amber-600 font-medium" : "text-ink-500"}">${fmtM(c.app_value)} vs ${fmtM(c.external_value)}</span>
          </div>`).join("")}
      </div>
    </div>` : ""}
    <!-- Source links -->
    ${sourceLinks.length ? `
    <div>
      <h3 class="${CLS_SUBSECTION_HEADING} mb-2">Odkazy</h3>
      <div class="flex flex-wrap gap-2">
        ${sourceLinks.map(([key, url]) => `<a href="${esc(url)}" target="_blank" rel="noopener noreferrer" class="inline-flex items-center px-3 py-2 sm:px-2.5 sm:py-1.5 text-xs font-mono font-medium text-ink-600 border border-ink-200 hover:bg-ink-100 hover:text-ink-900 transition-colors">${esc(sourceLabels[key] || key)} &rarr;</a>`).join("")}
      </div>
    </div>` : ""}
    <!-- AI transparency + footer -->
    <div class="pt-4 border-t border-ink-200 space-y-2">
      ${aiUsage ? `
      <div class="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs font-mono text-ink-400">
        ${p.analysis_engine === "ai" ? '<span>AI analýza</span>' : ""}
        ${aiUsage.model ? `<span>${esc(aiUsage.model)}</span>` : ""}
        ${aiUsage.totalLabel ? `<span>${esc(aiUsage.totalLabel)}</span>` : ""}
        ${aiUsage.costLabel ? `<span>${esc(aiUsage.costLabel)}</span>` : ""}
        ${p.generated_at ? `<span>${esc(fmtRelative(p.generated_at) || fmtDate(p.generated_at))}</span>` : ""}
      </div>` : ""}
      <div class="text-[11px] font-mono text-ink-400">
        Pouze z veřejných zdrojů justice.cz. Nenahrazuje právní ani finanční poradenství.
      </div>
    </div>
  </section>`;
}

function profileView(p) {
  return `
  <div class="view-enter">
    ${sectionNav()}
    <div class="max-w-4xl mx-auto px-4 sm:px-6 pt-5 pb-28 sm:pb-32">
      <div class="space-y-8 min-w-0">
        ${profileHero(p)}
        ${analysisSection(p)}
        ${financialOverview(p)}
        ${peopleSection(p)}
        ${documentsAndSourcesSection(p)}
      </div>
    </div>
  </div>`;
}

// ============================================================
// MAIN RENDER
// ============================================================

function render() {
  if (!$content) return;
  const heroInputState = captureHeroInputState();
  const shouldRestoreHeroFocus = state.pendingHomeFocus || !!heroInputState;

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
    setStatus(state.statusLog.at(-1)?.label || "Analyzuji...", "running");
  } else if (state.profile) {
    setStatus(state.profile.name, "done");
  } else if (state.error) {
    setStatus("Chyba", "error");
  } else {
    setStatus("Připraveno", "ready");
  }

  if (!state.loading) renderHistory();

  // Determine current view type and scroll to top on view change
  const nextView = state.loading ? "loading" : state.error ? "error" : state.profile ? "profile" : "home";
  if (render._lastView && render._lastView !== nextView) {
    window.scrollTo(0, 0);
  }
  render._lastView = nextView;

  // Content
  if (state.loading) {
    const statusEl = $content.querySelector("#loading-status-lines");
    const loadingEl = $content.querySelector("#loading-view");
    const hadPreview = loadingEl?.dataset.hasPreview === "1";
    const hasPreview = !!state.preview;
    const items = visibleStatusLog();
    if (statusEl && hasPreview === hadPreview) {
      syncLoadingStatusLines(statusEl, items);
    } else {
      // Full render: first time or preview just arrived
      $content.innerHTML = loadingView(state.preview, items, {
        animateIn: !loadingEl && !_pendingSearchStageAnimation,
      });
    }
  } else if (state.error) {
    $content.innerHTML = errorView(state.error);
  } else if (state.profile) {
    $content.innerHTML = profileView(state.profile);
    drawFinanceChart(state.profile.financial_timeline || []);
    initScrollSpy();
    initTableScrollHint();
  } else {
    const pendingHeroAnimation = state.pendingHeroAnimation;
    state.pendingHeroAnimation = null;
    $content.innerHTML = heroView(pendingHeroAnimation);
    if (shouldRestoreHeroFocus) {
      state.pendingHomeFocus = false;
      requestAnimationFrame(() => {
        focusHeroInput(heroInputState || {});
      });
    }
  }

  _pendingPanelRevealKeys = null;
  playPendingSearchStageAnimation();
}

// ============================================================
// API
// ============================================================

async function fetchJson(url, fallback, init = {}) {
  let res;
  try { res = await fetch(url, init); } catch { throw new Error("Připojení k serveru selhalo. Zkontrolujte internetové připojení."); }
  let data = null;
  try { data = await res.json(); } catch { data = null; }
  if (!res.ok) {
    const error = new Error(data?.detail || fallback);
    error.status = res.status;
    throw error;
  }
  return data;
}

async function searchCompanies(q) {
  return fetchJson(`${API}/api/search?q=${encodeURIComponent(q)}`, "Hledání v rejstříku se nezdařilo. Zkuste to znovu.");
}

async function loadHistoryData(offset = state.historyOffset, limit = state.historyLimit) {
  try {
    return await fetchJson(
      `${API}/api/history?limit=${encodeURIComponent(limit)}&offset=${encodeURIComponent(offset)}`,
      ""
    );
  } catch { return null; }
}

async function refreshHistoryPage(offset = state.historyOffset, opts = {}) {
  const { updateRecent = false } = opts;
  const nextOffset = Math.max(0, offset);
  const data = await loadHistoryData(nextOffset, state.historyLimit);
  if (!data) return false;
  state.history = data.items || [];
  state.historyTotal = data.total || 0;
  state.historyLimit = data.limit || state.historyLimit;
  state.historyOffset = data.offset || 0;
  if (updateRecent || state.historyOffset === 0) {
    state.historyRecent = state.history.slice(0, HISTORY_RECENT_LIMIT);
  }
  return true;
}

async function loadCompanySnapshot(id, refresh) {
  return fetchJson(
    `${API}/api/company?subjektId=${encodeURIComponent(id)}&q=${encodeURIComponent(state.query || "")}${refresh ? "&refresh=true" : ""}`,
    "Načtení detailu firmy se nezdařilo. Zkuste to znovu."
  );
}

async function loadStoredCompanyProfile(id) {
  return fetchJson(
    `${API}/api/company/stored?subjektId=${encodeURIComponent(id)}&q=${encodeURIComponent(state.query || "")}`,
    "Profil firmy zatím není k dispozici."
  );
}

async function loadCompanyAi(id) {
  return fetchJson(
    `${API}/api/company/ai?subjektId=${encodeURIComponent(id)}&q=${encodeURIComponent(state.query || "")}`,
    "AI analýza se nezdařila. Zkuste to znovu.",
    { method: "POST" }
  );
}

async function loadCompanyStream(id, refresh, requestToken) {
  const url = `${API}/api/company/stream?subjektId=${encodeURIComponent(id)}&q=${encodeURIComponent(state.query || "")}${refresh ? "&refresh=true" : ""}`;
  let res;
  try { res = await fetch(url, { headers: { Accept: "text/event-stream" } }); } catch { throw new Error("Připojení k serveru selhalo. Zkontrolujte internetové připojení."); }
  if (!res.ok || !res.body) {
    let detail = "Načtení detailu firmy se nezdařilo.";
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
    ensureActiveRequest(requestToken, reader);
    buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
    const parts = buffer.split("\n\n");
    buffer = parts.pop() || "";
    for (const part of parts) {
      ensureActiveRequest(requestToken, reader);
      const msg = parse(part);
      if (!msg) continue;
      if (msg.event === "status") {
        pushStatusLog(msg.payload.label);
        render();
      }
      if (msg.event === "preview") { state.preview = msg.payload; render(); }
      if (msg.event === "error") throw new Error(msg.payload?.detail || "Zpracování dat se nezdařilo.");
      if (msg.event === "result") return msg.payload;
    }
    if (done) break;
  }
  throw new Error("Spojení se serverem bylo přerušeno. Zkuste to znovu.");
}

// ============================================================
// HANDLERS
// ============================================================

async function handleSearch(query) {
  const requestToken = nextRequestToken();
  queueSearchStageAnimation(captureSearchStageTransitionFromHome());
  state.query = query;
  state.loading = true;
  state.error = null;
  state.profile = null;
  state.preview = null;
  state.pendingHeroAnimation = null;
  resetStatusLog(["Hledám firmu v obchodním rejstříku"]);
  state.expandedAccordions.clear();
  state.expandedPanels.clear();
  render();
  try {
    const data = await searchCompanies(query);
    ensureActiveRequest(requestToken);
    const results = data.results || [];
    if (!results.length) {
      state.loading = false;
      state.error = "Žádná firma nenalezena. Zkuste přesnější název nebo celé osmimístné IČO.";
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
    if (isStaleRequestError(e)) return;
    state.loading = false;
    state.error = e.message || "Hledání v rejstříku se nezdařilo. Zkuste to znovu.";
    render();
  }
}

async function handlePick(id, opts = {}) {
  const requestToken = nextRequestToken();
  const refresh = !!opts.forceRefresh;
  const cachedProfile = !refresh ? _profileCache.get(String(id)) || null : null;
  queueSearchStageAnimation(captureSearchStageTransitionFromHome());
  state.selectedMatch = id;
  state.error = null;
  state.preview = null;
  state.pendingHeroAnimation = null;
  state.expandedAccordions.clear();
  state.expandedPanels.clear();

  if (cachedProfile) {
    await openLoadedProfile(id, cachedProfile, requestToken);
    return;
  }

  try {
    if (!refresh) {
      try {
        const storedProfile = await loadStoredCompanyProfile(id);
        ensureActiveRequest(requestToken);
        await openLoadedProfile(id, storedProfile, requestToken);
        return;
      } catch (storedErr) {
        if (isStaleRequestError(storedErr)) return;
        if (!isNotFoundError(storedErr)) {
          console.warn("Stored profile fetch failed:", storedErr);
        }
      }
    }

    state.loading = true;
    state.profile = null;
    resetStatusLog([refresh ? "Stahuji aktuální data z justice.cz" : "Načítám profil firmy"]);
    render();

    let profile;
    try {
      profile = await loadCompanyStream(id, refresh, requestToken);
      ensureActiveRequest(requestToken);
    } catch (streamErr) {
      if (isStaleRequestError(streamErr)) return;
      console.warn("Stream failed:", streamErr);
      pushStatusLog("Zkouším alternativní způsob načtení");
      render();
      profile = await loadCompanySnapshot(id, refresh);
      ensureActiveRequest(requestToken);
    }
    await openLoadedProfile(id, profile, requestToken);
  } catch (e) {
    if (isStaleRequestError(e)) return;
    state.loading = false;
    state.preview = null;
    state.statusLog = [];
    state.error = e.message || "Načtení detailu firmy se nezdařilo. Zkuste to znovu.";
    render();
  }
}

async function handleAiEnhance(id) {
  const requestToken = nextRequestToken();
  const existingProfile = state.profile;
  state.selectedMatch = id;
  state.loading = true;
  state.error = null;
  state.profile = null;
  state.preview = existingProfile
    ? {
        subject_id: existingProfile.subject_id,
        name: existingProfile.name,
        ico: existingProfile.ico,
        basic_info: existingProfile.basic_info || [],
      }
    : null;
  state.pendingHeroAnimation = null;
  resetStatusLog(["Spouštím AI analýzu profilu"]);
  render();
  try {
    state.profile = cacheProfile(await loadCompanyAi(id));
    ensureActiveRequest(requestToken);
    state.loading = false;
    state.preview = null;
    state.statusLog = [];
    await refreshHistoryPage(0, { updateRecent: true });
    render();
  } catch (e) {
    if (isStaleRequestError(e)) return;
    state.loading = false;
    state.preview = null;
    state.statusLog = [];
    state.error = e.message || "AI analýza se nezdařila. Zkuste to znovu.";
    if (existingProfile) state.profile = existingProfile;
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
      borderColor: "#1c1917",
      backgroundColor: "rgba(28,25,23,0.06)",
      borderWidth: 2,
      tension: 0.3,
      pointRadius: 3,
      pointBackgroundColor: "#1c1917",
    });
  }
  const profitData = sorted.map((r) => r.net_profit ?? null);
  const profitColors = profitData.map((v) => v != null && v < 0 ? "#dc2626" : "#047857");
  datasets.push({
    label: "Čistý zisk",
    data: profitData,
    borderColor: "#047857",
    backgroundColor: "rgba(4,120,87,0.04)",
    borderWidth: 2,
    tension: 0.3,
    pointRadius: 3,
    pointBackgroundColor: profitColors,
    segment: {
      borderColor: (ctx) => {
        const prev = ctx.p0.parsed.y;
        const next = ctx.p1.parsed.y;
        if (prev < 0 && next < 0) return "#dc2626";
        if (prev < 0 || next < 0) return "#a8a29e";
        return "#047857";
      },
    },
  });
  datasets.push({
    label: "Aktiva",
    data: sorted.map((r) => r.assets ?? null),
    borderColor: "#78716c",
    backgroundColor: "rgba(120,113,108,0.04)",
    borderWidth: 1.5,
    borderDash: [4, 3],
    tension: 0.3,
    pointRadius: 2,
    pointBackgroundColor: "#78716c",
  });

  chartInstance = new Chart(canvas, {
    type: "line",
    data: { labels, datasets },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { intersect: false, mode: "index" },
      scales: {
        x: { ticks: { color: "#a8a29e", font: { size: 11, family: "JetBrains Mono" } }, grid: { color: "rgba(168,162,158,0.12)" } },
        y: { ticks: { color: "#a8a29e", font: { size: 11, family: "JetBrains Mono" }, callback: (v) => `${v}` }, grid: { color: "rgba(168,162,158,0.12)" } },
      },
      plugins: {
        legend: { labels: { color: "#57534e", font: { size: 11, family: "JetBrains Mono" }, boxWidth: 12, padding: 16 } },
        tooltip: {
          backgroundColor: "#1c1917",
          titleFont: { size: 12, family: "JetBrains Mono" },
          bodyFont: { size: 11, family: "JetBrains Mono" },
          padding: 10,
          cornerRadius: 0,
          callbacks: { label: (ctx) => `${ctx.dataset.label}: ${fmtM(ctx.raw)}` },
        },
      },
    },
  });
}

// ============================================================
// SCROLL SPY
// ============================================================

let scrollSpyScrollHandler = null;
let scrollSpyResizeHandler = null;
let scrollSpyRaf = 0;

function navButtonClasses(isActive) {
  return `px-3 py-2.5 sm:py-1.5 text-sm font-mono font-medium whitespace-nowrap transition-colors border-b-2 ${
    isActive ? "border-ink-900 text-ink-900" : "border-transparent text-ink-400 hover:text-ink-700 hover:border-ink-300"
  }`;
}

function setActiveSectionNav(sectionId) {
  document.querySelectorAll("[data-nav]").forEach((btn) => {
    btn.className = navButtonClasses(btn.dataset.nav === sectionId);
  });
}

let _cachedNavOffset = 0;

function computeSectionNavOffset() {
  const nav = document.getElementById("section-nav");
  if (!nav) return window.innerWidth >= 1024 ? 96 : 132;
  const navTop = Number.parseFloat(window.getComputedStyle(nav).top || "0") || 0;
  const navHeight = nav.getBoundingClientRect().height || 0;
  return navTop + navHeight + 12;
}

function getSectionNavOffset() {
  return _cachedNavOffset;
}

function updateActiveSectionNavFromScroll() {
  const sections = Array.from(document.querySelectorAll("[data-section]"));
  if (!sections.length) return;

  const threshold = getSectionNavOffset();
  const maxScrollTop = Math.max(0, document.documentElement.scrollHeight - window.innerHeight);

  if (Math.abs(window.scrollY - maxScrollTop) < 4) {
    setActiveSectionNav(sections[sections.length - 1].dataset.section);
    return;
  }

  let activeSectionId = sections[0].dataset.section;
  for (const section of sections) {
    if (section.getBoundingClientRect().top <= threshold) activeSectionId = section.dataset.section;
    else break;
  }

  setActiveSectionNav(activeSectionId);
}

function scrollToSection(sectionId) {
  const target = document.getElementById(`section-${sectionId}`) || document.querySelector(`[data-section="${sectionId}"]`);
  if (!target) return;

  const targetTop = window.scrollY + target.getBoundingClientRect().top - getSectionNavOffset();
  const maxScrollTop = Math.max(0, document.documentElement.scrollHeight - window.innerHeight);
  const top = Math.min(Math.max(0, targetTop), maxScrollTop);

  setActiveSectionNav(sectionId);
  window.scrollTo({ top, behavior: "smooth" });
}

function initScrollSpy() {
  if (scrollSpyScrollHandler) window.removeEventListener("scroll", scrollSpyScrollHandler);
  if (scrollSpyResizeHandler) window.removeEventListener("resize", scrollSpyResizeHandler);
  if (scrollSpyRaf) {
    cancelAnimationFrame(scrollSpyRaf);
    scrollSpyRaf = 0;
  }

  const sections = document.querySelectorAll("[data-section]");
  const navBtns = document.querySelectorAll("[data-nav]");
  if (!sections.length || !navBtns.length) return;

  _cachedNavOffset = computeSectionNavOffset();
  updateActiveSectionNavFromScroll();

  scrollSpyScrollHandler = () => {
    if (scrollSpyRaf) return;
    scrollSpyRaf = window.requestAnimationFrame(() => {
      scrollSpyRaf = 0;
      updateActiveSectionNavFromScroll();
    });
  };

  scrollSpyResizeHandler = () => {
    _cachedNavOffset = computeSectionNavOffset();
    updateActiveSectionNavFromScroll();
  };

  window.addEventListener("scroll", scrollSpyScrollHandler, { passive: true });
  window.addEventListener("resize", scrollSpyResizeHandler);
}

let _tableScrollHandler = null;
function initTableScrollHint() {
  const wrap = document.querySelector(".fin-table-wrap");
  if (_tableScrollHandler && _tableScrollHandler.el) {
    _tableScrollHandler.el.removeEventListener("scroll", _tableScrollHandler.fn);
    _tableScrollHandler = null;
  }
  if (!wrap) return;
  let raf = 0;
  const check = () => {
    const atEnd = wrap.scrollLeft + wrap.clientWidth >= wrap.scrollWidth - 2;
    wrap.classList.toggle("scrolled-end", atEnd);
  };
  check();
  const handler = () => {
    if (raf) return;
    raf = requestAnimationFrame(() => { raf = 0; check(); });
  };
  wrap.addEventListener("scroll", handler, { passive: true });
  _tableScrollHandler = { el: wrap, fn: handler };
}

function resetHomeState({ animate = false } = {}) {
  nextRequestToken();
  state.query = "";
  state.loading = false;
  state.error = null;
  state.profile = null;
  state.preview = null;
  state.statusLog = [];
  state.selectedMatch = null;
  state.pendingHeroAnimation = animate ? "home-reset" : null;
  state.pendingHomeFocus = true;
  state.expandedAccordions.clear();
  state.expandedPanels.clear();
  clearAutocomplete();
}

function navigateHome(opts = {}) {
  const animate = opts.animate !== false;
  const useViewTransition = animate && canAnimateViewTransition();

  const update = () => {
    resetHomeState({ animate: animate && !useViewTransition });
    const historyMethod = window.location.pathname === "/" ? "replaceState" : "pushState";
    history[historyMethod](null, "", "/");
    render();
  };

  if (useViewTransition) runViewTransition("home-reset", update);
  else update();

  refreshHistoryPage(0, { updateRecent: true }).then((ok) => {
    if (ok && !state.loading && !state.profile && !state.error) render();
  });
}

function handleNewCheck() {
  navigateHome({ animate: true });
}

// ============================================================
// EVENT DELEGATION
// ============================================================

function initEvents() {
  // Global click delegation
  document.addEventListener("click", (e) => {
    const homeTrigger = e.target.closest("[data-home-trigger]");
    if (homeTrigger) {
      e.preventDefault();
      if (homeTrigger.closest("#history-drawer") && state.drawerOpen) {
        closeDrawer();
      }
      handleNewCheck();
      return;
    }

    // Pick company (match or history)
    const pick = e.target.closest("[data-pick-id]");
    if (pick) {
      e.preventDefault();
      clearAutocomplete();
      const q = pick.dataset.pickQuery || "";
      if (q) state.query = q;
      handlePick(pick.dataset.pickId);
      if (state.drawerOpen) closeDrawer();
      return;
    }

    const historyPage = e.target.closest("[data-history-page]");
    if (historyPage) {
      if (state.loading || state._historyPaging) return;
      const direction = historyPage.dataset.historyPage;
      const nextOffset = direction === "prev"
        ? Math.max(0, state.historyOffset - state.historyLimit)
        : state.historyOffset + state.historyLimit;
      if ((direction === "prev" && !historyHasPrevPage()) || (direction === "next" && !historyHasNextPage())) return;
      state._historyPaging = true;
      refreshHistoryPage(nextOffset).then((ok) => {
        state._historyPaging = false;
        if (!ok) return;
        if (!state.loading && !state.profile && !state.error) render();
        else renderHistory();
      });
      return;
    }

    const runAi = e.target.closest("[data-run-ai]");
    if (runAi) { handleAiEnhance(runAi.dataset.runAi); return; }

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
      target.classList.toggle("accordion-body-open", !isOpen);
      acc.classList.toggle("accordion-open", !isOpen);
      const wrapper = acc.parentElement;
      if (wrapper) wrapper.classList.toggle("bg-ink-100/30", !isOpen);
      const chev = acc.querySelector(".accordion-chevron");
      if (chev) chev.classList.toggle("rotate-180", !isOpen);
      return;
    }

    // Expand panel
    const expand = e.target.closest("[data-expand]");
    if (expand) {
      const key = expand.dataset.expand;
      const keys = getExpandKeys(key);
      const nextKeys = keys.filter((item) => !state.expandedPanels.has(item));
      const sectionId = expand.dataset.expandSection;

      if (!nextKeys.length) {
        if (sectionId) scrollToSection(sectionId);
        return;
      }

      nextKeys.forEach((item) => state.expandedPanels.add(item));
      _pendingPanelRevealKeys = new Set(nextKeys);

      // Try local DOM toggle first (for panels that exist but are hidden)
      const target = document.getElementById(key);
      if (target) {
        _pendingPanelRevealKeys = null;
        target.classList.remove("hidden");
        const wrapper = expand.parentElement;
        if (wrapper && wrapper !== $content) wrapper.remove();
        else expand.remove();
        if (sectionId) scrollToSection(sectionId);
      } else {
        // Slice-based expands (all-praskac, all-execs) need re-render
        render();
        if (sectionId) {
          requestAnimationFrame(() => {
            scrollToSection(sectionId);
          });
        }
      }
      return;
    }

    // Section nav
    const nav = e.target.closest("[data-nav]");
    if (nav) {
      scrollToSection(nav.dataset.nav);
      return;
    }
  });

  // Mobile history drawer
  document.getElementById("mobile-menu-btn")?.addEventListener("click", openDrawer);
  document.getElementById("drawer-close")?.addEventListener("click", closeDrawer);
  document.getElementById("drawer-backdrop")?.addEventListener("click", closeDrawer);

  // Keyboard
  document.addEventListener("keydown", (e) => {
    if (e.target.id === "hero-input" && state.autocompleteOpen && state.autocompleteResults.length) {
      if (e.key === "ArrowDown") {
        e.preventDefault();
        state.autocompleteActiveIndex = (state.autocompleteActiveIndex + 1) % state.autocompleteResults.length;
        renderAutocomplete();
        return;
      }

      if (e.key === "ArrowUp") {
        e.preventDefault();
        state.autocompleteActiveIndex = state.autocompleteActiveIndex <= 0
          ? state.autocompleteResults.length - 1
          : state.autocompleteActiveIndex - 1;
        renderAutocomplete();
        return;
      }

      if (e.key === "Enter" && state.autocompleteActiveIndex >= 0) {
        e.preventDefault();
        const match = state.autocompleteResults[state.autocompleteActiveIndex];
        if (match) {
          state.query = match.name || match.ico || "";
          clearAutocomplete();
          handlePick(match.subject_id);
        }
        return;
      }
    }

    if (e.key === "Escape") {
      if (state.autocompleteOpen) { clearAutocomplete(); return; }
      if (state.drawerOpen) closeDrawer();
    }

    // Cmd+K / Ctrl+K — quick-focus search
    if ((e.metaKey || e.ctrlKey) && e.key === "k") {
      e.preventDefault();
      if (state.profile || state.error) {
        navigateHome();
        requestAnimationFrame(() => {
          const inp = document.getElementById("hero-input");
          if (inp) { inp.focus(); inp.select(); }
        });
      } else {
        const inp = document.getElementById("hero-input");
        if (inp) { inp.focus(); inp.select(); }
      }
    }
  });

  // Autocomplete: hero input
  document.addEventListener("input", (e) => {
    if (e.target.id === "hero-input") {
      state.query = e.target.value;
      handleAutocompleteInput(e.target.value, "hero-autocomplete");
    }
  });

  // Hero form submit
  document.addEventListener("submit", (e) => {
    if (e.target.id === "hero-search-form") {
      e.preventDefault();
      if (state.loading) return;
      const heroInput = document.getElementById("hero-input");
      const q = heroInput ? heroInput.value.trim() : "";
      const match = activeAutocompleteMatch();
      if (match) {
        state.query = match.name || match.ico || q;
        clearAutocomplete();
        handlePick(match.subject_id);
        return;
      }
      if (q) {
        state.query = q;
        clearAutocomplete();
        handleSearch(q);
      }
    }
  });

  // Click outside to close autocomplete
  document.addEventListener("mousedown", (e) => {
    if (!e.target.closest("#hero-search-form")) {
      clearAutocomplete();
    }
  });

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
      resetHomeState();
      render();
    }
  });
}

// ============================================================
// INIT
// ============================================================

function init() {
  $content = document.getElementById("content");
  $statusDot = document.getElementById("status-dot");
  $statusText = document.getElementById("status-text");
  $header = document.getElementById("app-header");
  $railHistory = document.getElementById("rail-history");
  $drawerHistory = document.getElementById("drawer-history");
  $drawer = document.getElementById("history-drawer");

  console.log(
    "%cJustice Práskač%c\nScreening českých firem z veřejných zdrojů.\nhttps://praskac.xyz",
    "font-weight:bold;font-size:14px;font-family:monospace;color:#1c1917",
    "font-size:11px;font-family:monospace;color:#78716c"
  );

  initEvents();

  // Check URL for direct company link (/firma/123)
  const urlMatch = window.location.pathname.match(/^\/firma\/(\d+)$/);
  const urlSubjektId = urlMatch ? urlMatch[1] : null;
  if (urlSubjektId) {
    handlePick(urlSubjektId);
  } else {
    render();
  }

  refreshHistoryPage(0, { updateRecent: true }).then((ok) => {
    if (ok) renderHistory();
    if (ok && !state.loading && !state.profile && !state.error) {
      render();
    }
  });
}

init();
