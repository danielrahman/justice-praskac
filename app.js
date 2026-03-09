const API = "port/8000";

const state = {
  query: "",
  loading: false,
  searching: false,
  matches: [],
  profile: null,
  preview: null,
  history: [],
  statusLog: [],
  error: null,
  selectedMatch: null,
  sidebarOpen: window.innerWidth > 980,
  mobileSidebar: false,
  headerHidden: false,
  lastScrollTop: 0,
};

const els = {
  appShell: document.querySelector("#app-shell"),
  searchForm: document.querySelector("#search-form"),
  queryInput: document.querySelector("#query-input"),
  submitBtn: document.querySelector("#submit-btn"),
  content: document.querySelector("#content"),
  mainHeader: document.querySelector(".main-header"),
  status: document.querySelector("#status-pill"),
  sidebar: document.querySelector("#sidebar"),
  sidebarToggle: document.querySelector("#sidebar-toggle"),
  historyList: document.querySelector("#history-list"),
  sidebarBackdrop: document.querySelector("#sidebar-backdrop"),
};

const formatMillion = (value) => {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return new Intl.NumberFormat("cs-CZ", {
    minimumFractionDigits: Math.abs(value) >= 100 ? 0 : 2,
    maximumFractionDigits: Math.abs(value) >= 100 ? 0 : 2,
  }).format(value) + " mil. Kč";
};

const formatPct = (value) => {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return new Intl.NumberFormat("cs-CZ", { maximumFractionDigits: 1 }).format(value) + " %";
};

const escapeHtml = (value) =>
  String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");

function setStatus(text) {
  els.status.textContent = text;
}

function renderSidebar() {
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

function render() {
  els.submitBtn.disabled = state.loading || state.searching;
  if (state.searching) setStatus("Hledám firmu v rejstříku");
  else if (state.loading) setStatus(state.statusLog.at(-1) || "Čtu výpisy a listiny");
  else if (state.profile) setStatus(`Hotovo · ${state.profile.name}`);
  else if (state.matches.length > 1) setStatus("Vyber správnou firmu");
  else if (state.error) setStatus("Potřeba opakovat načtení");
  else setStatus("Připraveno");

  renderSidebar();

  if (state.searching) {
    els.content.innerHTML = loadingView("Hledám kandidáty podle názvu nebo IČO.");
    return;
  }
  if (state.loading) {
    els.content.innerHTML = loadingView(
      state.preview,
      state.statusLog.length ? state.statusLog : ["Čtu veřejný výpis, Sbírku listin a finanční podklady z posledních let."],
    );
    return;
  }
  if (state.error) {
    els.content.innerHTML = errorView(state.error);
    bindRetry();
    return;
  }
  if (state.matches.length > 1 && !state.profile) {
    els.content.innerHTML = matchView(state.matches);
    bindMatchButtons();
    return;
  }
  if (state.profile) {
    els.content.innerHTML = profileView(state.profile);
    bindRerunButtons();
    drawFinanceChart(state.profile.financial_timeline || []);
    return;
  }
  els.content.innerHTML = heroView();
}

function heroView() {
  return `
    <section class="hero-state">
      <div class="hero-grid">
        <div>
          <h2>Rychlý profil firmy z justice.cz</h2>
          <p>
            Zadej název firmy nebo IČO. Aplikace dohledá odpovídající subjekt ve veřejném rejstříku,
            projde detail firmy, Sbírku listin a vytáhne základní profil, finance a veřejné signály.
          </p>
        </div>
        <div class="hero-points">
          ${[
            ["Základní profil", "Firma, IČO, právní forma, sídlo a hlavní veřejně dostupné údaje."],
            ["Vedení a vlastníci", "Jednatelé, orgány a vlastnické informace, pokud jsou veřejně ve výpisu."],
            ["Finanční přehled", "Poslední roky ze Sbírky listin, trendy tržeb, zisku, kapitálu a dluhu."],
            ["Práskač", "Přímé shrnutí veřejně viditelných anomálií, mezer nebo podezřelých změn."],
          ]
            .map(
              ([title, text]) => `
                <div class="hero-point">
                  <span class="dot" aria-hidden="true"></span>
                  <div>
                    <strong>${escapeHtml(title)}</strong>
                    <span>${escapeHtml(text)}</span>
                  </div>
                </div>`
            )
            .join("")}
        </div>
      </div>
    </section>`;
}

function previewCard(preview) {
  if (!preview) return "";
  return `
    <article class="preview-card">
      <div class="preview-head">
        <div>
          <h3>${escapeHtml(preview.name || "Načítaná firma")}</h3>
          <p>IČO ${escapeHtml(preview.ico || "—")}</p>
        </div>
        <span class="tag">základní info hned</span>
      </div>
      <div class="preview-grid">
        ${(preview.basic_info || []).slice(0, 6).map((item) => `
          <div class="info-row compact-row">
            <strong>${escapeHtml(item.label)}</strong>
            <span>${escapeHtml(item.value)}</span>
          </div>
        `).join("")}
      </div>
    </article>`;
}

function loadingView(previewOrText, progressItems = []) {
  const fallbackText = typeof previewOrText === "string" ? previewOrText : "Čtu veřejné podklady z justice.cz.";
  const progress = progressItems.length ? progressItems : [fallbackText];
  const preview = typeof previewOrText === "object" ? previewOrText : null;
  return `
    <section class="loading-state">
      <h2>Chvilka. Tahám veřejné podklady.</h2>
      <p>${escapeHtml(fallbackText)}</p>
      ${previewCard(preview)}
      <div class="progress-list" aria-live="polite">
        ${progress.map((item, index) => `
          <div class="progress-item ${index === progress.length - 1 ? "is-current" : ""}">
            <span class="progress-dot" aria-hidden="true"></span>
            <span>${escapeHtml(item)}</span>
          </div>
        `).join("")}
      </div>
      <div class="loading-lines">
        <div class="loading-line"></div>
        <div class="loading-line"></div>
        <div class="loading-line"></div>
        <div class="loading-line"></div>
      </div>
    </section>`;
}

function errorView(text) {
  return `
    <section class="error-state">
      <h2>Něco se nepovedlo</h2>
      <p>${escapeHtml(text)}</p>
      <div style="margin-top: 18px;">
        <button class="retry-btn" type="button">Zkusit znovu</button>
      </div>
    </section>`;
}

function matchView(matches) {
  return `
    <section class="match-state">
      <h2>Našel jsem víc firem</h2>
      <p>Vyber správný subjekt. Pak už otevřu detail a Sbírku listin jen pro něj.</p>
      <div class="match-list">
        ${matches
          .map(
            (item) => `
              <article class="result-picker">
                <div>
                  <strong>${escapeHtml(item.name)}</strong>
                  <span>IČO ${escapeHtml(item.ico_display || item.ico)} · ${escapeHtml(item.file_number || "")}</span>
                  <span>${escapeHtml(item.address || "")}</span>
                </div>
                <button type="button" data-subjekt-id="${escapeHtml(item.subject_id)}">Vybrat</button>
              </article>`
          )
          .join("")}
      </div>
    </section>`;
}

function formatDateTime(value) {
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

function metricLabel(metric) {
  return {
    revenue: "tržby",
    operating_profit: "provozní výsledek",
    net_profit: "čistý výsledek",
    assets: "aktiva",
    equity: "vlastní kapitál",
    liabilities: "cizí zdroje",
    debt: "dluh",
  }[metric] || metric;
}

function metricPills(metrics) {
  if (!(metrics || []).length) {
    return '<span class="mini-pill mini-pill-muted">bez jistých metrik</span>';
  }
  return metrics.map((metric) => `<span class="mini-pill">${escapeHtml(metricLabel(metric))}</span>`).join("");
}

function renderDocumentCard(doc) {
  const files = doc.candidate_files || [];
  const primaryYear = (doc.years || ["?"])[0];
  return `
    <article class="doc-card">
      <div class="doc-card-head">
        <div>
          <strong>${escapeHtml(doc.document_number || "Listina")}</strong>
          <div class="doc-subtitle">${escapeHtml(doc.type || "")}</div>
        </div>
        <div class="doc-head-tags">
          <span class="tag tag-muted">rok ${escapeHtml(primaryYear)}</span>
          <span class="tag tag-muted">${escapeHtml(doc.extraction_mode || "?")}</span>
          <span class="tag tag-muted">${escapeHtml(String(doc.candidate_file_count || files.length || 0))} PDF</span>
        </div>
      </div>
      <div class="doc-summary-grid">
        <div class="info-row compact-row">
          <strong>Pokrytí</strong>
          <span>Procházím všechny kandidátní PDF přílohy k této listině, ne jen první soubor.</span>
        </div>
        <div class="info-row compact-row">
          <strong>Nalezené metriky</strong>
          <span class="mini-pill-row">${metricPills(doc.metrics_found || [])}</span>
        </div>
      </div>
      <div class="attachment-list">
        ${files.length
          ? files.map((file) => {
              const openUrl = `${API}/api/document/resolve?detailUrl=${encodeURIComponent(doc.detail_url || "")}&index=${encodeURIComponent(file.pdf_index ?? 0)}&prefer_pdf=true`;
              return `
                <div class="attachment-row">
                  <div>
                    <strong>${escapeHtml(file.label || "PDF příloha")}</strong>
                    <div class="attachment-meta">${escapeHtml(file.extraction_mode || "?")} · ${escapeHtml(String(file.page_count || file.page_hint || "?"))} stran</div>
                  </div>
                  <div class="attachment-actions">
                    <span class="mini-pill-row">${metricPills(file.metrics_found || [])}</span>
                    <a class="source-link" href="${escapeHtml(openUrl)}" target="_blank" rel="noopener noreferrer">otevřít PDF</a>
                  </div>
                </div>`;
            }).join("")
          : `<div class="attachment-row empty-attachment"><span>Nebyla nalezena žádná PDF příloha.</span></div>`}
      </div>
      <div class="doc-footer-links">
        <a class="source-link" href="${escapeHtml(doc.detail_url || "#")}" target="_blank" rel="noopener noreferrer">detail listiny</a>
      </div>
    </article>`;
}

function documentLinks(doc) {
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

      <div class="section-grid section-grid-docs">
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

        <article class="card docs-section-card">
          <div class="docs-section-head">
            <div>
              <h3>Relevantní listiny</h3>
              <div class="small-note">U každé listiny zobrazím všechny kandidátní PDF přílohy a co se z nich podařilo vytáhnout.</div>
            </div>
            <div class="tag-stack">
              <span class="tag tag-muted">${escapeHtml(String((profile.financial_documents || []).length))} listin</span>
              <span class="tag tag-muted">všechny PDF přílohy</span>
            </div>
          </div>
          <div class="documents-grid">
            ${(profile.financial_documents || []).map((doc) => renderDocumentCard(doc)).join("")}
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

function insightRow(item, className) {
  return `
    <div class="${className}">
      <strong>${escapeHtml(item.title)}</strong>
      <span>${escapeHtml(item.detail)}</span>
    </div>`;
}

function historySignalRows(history) {
  const rows = [
    ["Změny názvu", history.name_changes],
    ["Změny sídla", history.address_changes],
    ["Obměny vedení", history.management_turnover],
  ].filter(([, value]) => value !== undefined && value !== null);
  if (!rows.length) {
    return `<div class="info-row"><strong>Historie</strong><span>Historické změny se nepodařilo spolehlivě dopočítat.</span></div>`;
  }
  return rows
    .map(([label, value]) => `
      <div class="info-row compact-row">
        <strong>${escapeHtml(label)}</strong>
        <span>${escapeHtml(String(value))}</span>
      </div>`)
    .join("");
}

function coverageRows(profile) {
  const docs = profile.financial_documents || [];
  const timeline = profile.financial_timeline || [];
  const firstYear = timeline.length ? timeline[0].year : "—";
  const lastYear = timeline.length ? timeline[timeline.length - 1].year : "—";
  const withOcr = docs.filter((doc) => doc.extraction_mode === "ocr").length;
  const withDigital = docs.filter((doc) => doc.extraction_mode === "digital").length;
  return [
    ["Relevantní listiny", `${docs.length}`],
    ["Pokryté roky", `${firstYear} → ${lastYear}`],
    ["Digitální PDF", `${withDigital}`],
    ["OCR listiny", `${withOcr}`],
  ]
    .map(([label, value]) => `
      <div class="info-row compact-row">
        <strong>${escapeHtml(label)}</strong>
        <span>${escapeHtml(String(value))}</span>
      </div>`)
    .join("");
}

function sourceLabel(key) {
  return {
    current_extract: "Aktuální výpis",
    full_extract: "Úplný výpis",
    documents: "Sbírka listin",
    current_extract_pdf: "PDF aktuálního výpisu",
    full_extract_pdf: "PDF úplného výpisu",
    chytryrejstrik: "Chytrý rejstřík",
  }[key] || key;
}

async function fetchJson(url, fallbackMessage) {
  let res;
  try {
    res = await fetch(url);
  } catch {
    throw new Error("Síťové spojení se nepovedlo. Zkus to prosím znovu.");
  }
  let payload = null;
  try {
    payload = await res.json();
  } catch {
    payload = null;
  }
  if (!res.ok) {
    throw new Error(payload?.detail || fallbackMessage);
  }
  return payload;
}

async function searchCompanies(query) {
  const url = `${API}/api/search?q=${encodeURIComponent(query)}`;
  return fetchJson(url, "Nepodařilo se načíst výsledky hledání.");
}

async function loadHistory() {
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

async function loadCompanyStream(subjektId, forceRefresh = false) {
  const url = `${API}/api/company/stream?subjektId=${encodeURIComponent(subjektId)}&q=${encodeURIComponent(state.query || "")}${forceRefresh ? "&refresh=true" : ""}`;
  let res;
  try {
    res = await fetch(url, { headers: { Accept: "text/event-stream" } });
  } catch {
    throw new Error("Síťové spojení se nepovedlo. Zkus to prosím znovu.");
  }
  if (!res.ok || !res.body) {
    let detail = "Nepodařilo se načíst detail firmy.";
    try {
      const payload = await res.json();
      detail = payload?.detail || detail;
    } catch (parseError) {
      void parseError;
    }
    throw new Error(detail);
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";

  const flushEvent = (chunk) => {
    const lines = chunk.split("\n");
    let eventName = "message";
    const dataLines = [];
    for (const line of lines) {
      if (line.startsWith("event:")) eventName = line.slice(6).trim();
      if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
    }
    if (!dataLines.length) return null;
    try {
      return { event: eventName, payload: JSON.parse(dataLines.join("\n")) };
    } catch {
      return null;
    }
  };

  while (true) {
    const { value, done } = await reader.read();
    buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
    const parts = buffer.split("\n\n");
    buffer = parts.pop() || "";
    for (const part of parts) {
      const parsed = flushEvent(part);
      if (!parsed) continue;
      if (parsed.event === "status") {
        state.statusLog = [...state.statusLog, parsed.payload.label].slice(-8);
        render();
      }
      if (parsed.event === "preview") {
        state.preview = parsed.payload;
        render();
      }
      if (parsed.event === "error") {
        throw new Error(parsed.payload?.detail || "Načítání firmy se nepodařilo dokončit.");
      }
      if (parsed.event === "result") {
        return parsed.payload;
      }
    }
    if (done) break;
  }
  throw new Error("Načítání skončilo předčasně bez výsledku. Zkus to prosím znovu.");
}

async function handleSearch(query) {
  state.query = query;
  state.searching = true;
  state.loading = false;
  state.error = null;
  state.profile = null;
  state.preview = null;
  state.statusLog = [];
  state.matches = [];
  state.selectedMatch = null;
  render();
  try {
    const data = await searchCompanies(query);
    const results = data.results || [];
    state.searching = false;
    if (!results.length) {
      state.error = "Nic jsem nenašel. Zkus přesnější název firmy nebo čisté osmimístné IČO.";
      render();
      return;
    }
    if (results.length === 1) {
      await handlePick(results[0].subject_id);
      return;
    }
    const exactIco = query.replace(/\D/g, "");
    const exact = exactIco.length === 8 ? results.find((item) => item.ico === exactIco) : null;
    if (exact) {
      await handlePick(exact.subject_id);
      return;
    }
    state.matches = results;
    render();
  } catch (error) {
    state.searching = false;
    state.error = error.message || "Hledání se nepovedlo.";
    render();
  }
}

async function handlePick(subjektId, options = {}) {
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

function bindMatchButtons() {
  document.querySelectorAll("[data-subjekt-id]").forEach((button) => {
    button.addEventListener("click", () => handlePick(button.dataset.subjektId));
  });
}

function bindRetry() {
  document.querySelector(".retry-btn:not(.rerun-btn)")?.addEventListener("click", () => {
    if (state.selectedMatch) {
      handlePick(state.selectedMatch, { forceRefresh: true });
      return;
    }
    if (state.query) handleSearch(state.query);
  });
}

function bindRerunButtons() {
  document.querySelectorAll("[data-rerun-subjekt-id]").forEach((button) => {
    button.addEventListener("click", () => handlePick(button.dataset.rerunSubjektId, { forceRefresh: true }));
  });
}

function bindHistoryButtons() {
  document.querySelectorAll("[data-history-subjekt-id]").forEach((button) => {
    button.addEventListener("click", () => {
      const query = button.dataset.historyQuery || "";
      if (query) {
        state.query = query;
        els.queryInput.value = query;
      }
      handlePick(button.dataset.historySubjektId);
      if (window.innerWidth <= 980) {
        state.mobileSidebar = false;
        renderSidebar();
      }
    });
  });
}

let chartInstance = null;
function drawFinanceChart(rows) {
  const canvas = document.querySelector("#finance-chart");
  if (!canvas || !window.Chart) return;
  if (chartInstance) {
    chartInstance.destroy();
    chartInstance = null;
  }
  const labels = rows.map((row) => String(row.year));
  chartInstance = new Chart(canvas, {
    type: "line",
    data: {
      labels,
      datasets: [
        {
          label: "Tržby",
          data: rows.map((row) => row.revenue ?? null),
          borderColor: "#0f8c73",
          backgroundColor: "rgba(15, 140, 115, 0.10)",
          borderWidth: 2.5,
          tension: 0.28,
          pointRadius: 2.5,
        },
        {
          label: "Čistý výsledek",
          data: rows.map((row) => row.net_profit ?? null),
          borderColor: "#c65d45",
          backgroundColor: "rgba(198, 93, 69, 0.10)",
          borderWidth: 2.5,
          tension: 0.28,
          pointRadius: 2.5,
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { intersect: false, mode: "index" },
      scales: {
        x: {
          ticks: { color: "#5f6f82" },
          grid: { color: "rgba(18,32,51,0.07)" },
        },
        y: {
          ticks: {
            color: "#5f6f82",
            callback: (value) => `${value} mil. Kč`,
          },
          grid: { color: "rgba(18,32,51,0.07)" },
        },
      },
      plugins: {
        legend: {
          labels: { color: "#122033" },
        },
        tooltip: {
          callbacks: {
            label: (ctx) => `${ctx.dataset.label}: ${formatMillion(ctx.raw)}`,
          },
        },
      },
    },
  });
}

els.searchForm.addEventListener("submit", (event) => {
  event.preventDefault();
  const query = els.queryInput.value.trim();
  if (!query) return;
  handleSearch(query);
});

els.sidebarToggle?.addEventListener("click", () => {
  if (window.innerWidth <= 980) state.mobileSidebar = !state.mobileSidebar;
  else state.sidebarOpen = !state.sidebarOpen;
  renderSidebar();
  if (window.innerWidth > 980) {
    els.sidebar.style.display = state.sidebarOpen ? "block" : "none";
    els.appShell.style.gridTemplateColumns = state.sidebarOpen ? "300px minmax(0, 1fr)" : "0 minmax(0, 1fr)";
  }
});

els.sidebarBackdrop?.addEventListener("click", () => {
  state.mobileSidebar = false;
  renderSidebar();
});

window.addEventListener("resize", () => {
  if (window.innerWidth > 980) {
    state.mobileSidebar = false;
    els.sidebar.style.display = state.sidebarOpen ? "block" : "none";
    els.appShell.style.gridTemplateColumns = state.sidebarOpen ? "300px minmax(0, 1fr)" : "0 minmax(0, 1fr)";
  } else {
    els.sidebar.style.display = "block";
    els.appShell.style.gridTemplateColumns = "1fr";
  }
  renderSidebar();
});

if (window.innerWidth > 980 && !state.sidebarOpen) {
  els.sidebar.style.display = "none";
  els.appShell.style.gridTemplateColumns = "0 minmax(0, 1fr)";
}

loadHistory().then((items) => {
  state.history = items;
  render();
});

render();


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
