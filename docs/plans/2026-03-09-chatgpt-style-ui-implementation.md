# ChatGPT-style UI Redesign — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Restyle the Justice Práskač frontend to feel like ChatGPT — centered input on empty state, autocomplete dropdown, thinking bubble during loading, sidebar with "Nové prověření" button.

**Architecture:** Vanilla JS SPA with state-driven rendering. All changes are in 3 files: `style.css` (animations/transitions), `index.html` (shell restructure), `app.js` (views, autocomplete, state machine). No backend changes. The app has two visual modes: "empty" (centered input, no header) and "active" (header with input, content below).

**Tech Stack:** Vanilla JS, Tailwind CSS Play CDN, SSE streaming, Chart.js

---

### Task 1: Add CSS animations and utility classes

**Files:**
- Modify: `style.css`

**Step 1: Add fade transition utilities**

Append to `style.css`:

```css
/* View fade transitions */
.view-enter {
  animation: viewFadeIn 200ms ease both;
}

@keyframes viewFadeIn {
  from { opacity: 0; transform: translateY(6px); }
  to { opacity: 1; transform: translateY(0); }
}

/* Thinking bubble line appear */
@keyframes lineAppear {
  from { opacity: 0; transform: translateX(-4px); }
  to { opacity: 1; transform: translateX(0); }
}

.thinking-line {
  animation: lineAppear 150ms ease both;
}

/* Autocomplete dropdown */
.autocomplete-dropdown {
  animation: dropdownIn 120ms ease both;
}

@keyframes dropdownIn {
  from { opacity: 0; transform: translateY(-4px); }
  to { opacity: 1; transform: translateY(0); }
}

/* Header slide-down when first shown */
@keyframes headerSlideIn {
  from { opacity: 0; transform: translateY(-100%); }
  to { opacity: 1; transform: translateY(0); }
}

.header-enter {
  animation: headerSlideIn 200ms ease both;
}

/* Hero center layout — vertically center on empty state */
.hero-centered {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  min-height: calc(100vh - 2rem);
  min-height: calc(100dvh - 2rem);
}
```

**Step 2: Verify syntax**

Run: `cat style.css | head -5` — just confirm file was written.

**Step 3: Commit**

```bash
git add style.css
git commit -m "style: add fade, autocomplete, and thinking-bubble CSS animations"
```

---

### Task 2: Restructure the HTML shell

**Files:**
- Modify: `index.html`

The key structural changes:
1. **Desktop sidebar** (`#history-rail`): Add brand/logo at top + "Nové prověření" button. Remove standalone "Historie" label.
2. **Mobile drawer** (`#history-drawer`): Add "Nové prověření" button at top.
3. **Header** (`#app-header`): Starts hidden (CSS class `hidden`). Remove mobile brand from header (it lives in sidebar/drawer now). Remove the "Historie" toggle button from header. Keep search form + status pill.
4. **Main content** (`#content`): No structural change.

**Step 1: Update desktop sidebar**

Replace the `<aside id="history-rail" ...>` block. The new sidebar has:
- Brand/logo at top (moved from header)
- "Nové prověření" button below brand
- "Historie" label
- History list
- Footer disclaimer

```html
<aside id="history-rail" class="hidden lg:flex flex-col w-[260px] flex-shrink-0 border-r border-slate-200/80 bg-white">
  <div class="px-3 pt-4 pb-2">
    <div class="flex items-center gap-2.5 px-1">
      <div class="w-8 h-8 rounded-lg bg-teal-50 border border-teal-100 flex items-center justify-center text-teal-700">
        <svg class="w-4 h-4" viewBox="0 0 24 24" fill="none"><path d="M4 17.5V6.5L12 3l8 3.5v11L12 21l-8-3.5Z" stroke="currentColor" stroke-width="1.6"/><path d="M8.5 9.5h7M8.5 12h7M8.5 14.5h4.5" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>
      </div>
      <div>
        <div class="font-semibold text-sm text-slate-900 tracking-tight leading-tight">Justice Práskač</div>
        <div class="text-[11px] text-slate-400">Screening českých firem</div>
      </div>
    </div>
  </div>
  <div class="px-3 pb-2">
    <button id="new-check-btn" type="button" class="w-full flex items-center gap-2 px-3 py-2 rounded-lg text-sm font-medium text-slate-700 hover:bg-slate-50 ring-1 ring-slate-200 transition-colors">
      <svg class="w-4 h-4 text-slate-400" fill="none" viewBox="0 0 24 24" stroke-width="2" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" d="M12 4.5v15m7.5-7.5h-15"/></svg>
      Nové prověření
    </button>
  </div>
  <div class="px-3 pb-1">
    <div class="text-[11px] font-medium text-slate-400 uppercase tracking-wider px-1">Historie</div>
  </div>
  <div id="rail-history" class="flex-1 overflow-y-auto px-2 pb-4 space-y-0.5"></div>
  <div class="px-3 py-2.5 border-t border-slate-100 text-[11px] text-slate-400 leading-relaxed">
    Veřejná data z justice.cz.
  </div>
</aside>
```

**Step 2: Update mobile drawer**

Add "Nové prověření" button in the drawer header area, below the close button row. Replace the drawer header block:

```html
<aside class="fixed inset-y-0 left-0 w-[300px] max-w-[85vw] bg-white shadow-2xl flex flex-col">
  <div class="px-4 py-3 border-b border-slate-100">
    <div class="flex items-center justify-between mb-3">
      <div class="flex items-center gap-2">
        <div class="w-7 h-7 rounded-md bg-teal-50 border border-teal-100 flex items-center justify-center text-teal-700">
          <svg class="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none"><path d="M4 17.5V6.5L12 3l8 3.5v11L12 21l-8-3.5Z" stroke="currentColor" stroke-width="1.8"/><path d="M8.5 9.5h7M8.5 12h7M8.5 14.5h4.5" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg>
        </div>
        <span class="font-semibold text-sm tracking-tight">Práskač</span>
      </div>
      <button id="drawer-close" type="button" class="p-1.5 rounded-lg text-slate-400 hover:text-slate-600 hover:bg-slate-100">
        <svg class="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke-width="2" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12"/></svg>
      </button>
    </div>
    <button id="new-check-btn-mobile" type="button" class="w-full flex items-center gap-2 px-3 py-2 rounded-lg text-sm font-medium text-slate-700 hover:bg-slate-50 ring-1 ring-slate-200 transition-colors">
      <svg class="w-4 h-4 text-slate-400" fill="none" viewBox="0 0 24 24" stroke-width="2" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" d="M12 4.5v15m7.5-7.5h-15"/></svg>
      Nové prověření
    </button>
  </div>
  <div id="drawer-history" class="flex-1 overflow-y-auto p-2 space-y-0.5"></div>
  <div class="px-4 py-3 border-t border-slate-100 text-[11px] text-slate-400 leading-relaxed">
    Veřejná data z justice.cz. Není to právní doporučení.
  </div>
</aside>
```

**Step 3: Update header**

The header starts hidden and only appears after the first search. Remove mobile brand, remove rail-toggle button. Keep: mobile hamburger, search form, status pill.

```html
<header id="app-header" class="sticky top-0 z-40 bg-white/95 backdrop-blur-md border-b border-slate-200/80 transition-all duration-200 hidden">
  <div class="px-4 sm:px-6 py-2.5">
    <div class="flex items-center gap-3">
      <!-- Mobile menu button -->
      <button id="mobile-menu-btn" type="button" class="lg:hidden p-1.5 -ml-1 rounded-lg text-slate-400 hover:text-slate-600 hover:bg-slate-100">
        <svg class="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke-width="2" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" d="M3.75 6.75h16.5M3.75 12h16.5m-16.5 5.25h16.5"/></svg>
      </button>
      <!-- Search -->
      <form id="search-form" class="flex-1 flex items-center gap-2 min-w-0">
        <div class="relative flex-1 max-w-xl">
          <div class="pointer-events-none absolute inset-y-0 left-0 flex items-center pl-3">
            <svg class="h-4 w-4 text-slate-400" fill="none" viewBox="0 0 24 24" stroke-width="2" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" d="m21 21-5.197-5.197m0 0A7.5 7.5 0 1 0 5.196 5.196a7.5 7.5 0 0 0 10.607 10.607Z"/></svg>
          </div>
          <input id="query-input" name="query" type="text" placeholder="Firma nebo IČO..." autocomplete="off"
            class="block w-full rounded-lg border-0 bg-slate-50 py-2 pl-9 pr-3 text-sm text-slate-900 ring-1 ring-inset ring-slate-200 placeholder:text-slate-400 focus:bg-white focus:ring-2 focus:ring-teal-500 transition-colors">
        </div>
        <button id="submit-btn" type="submit"
          class="flex-shrink-0 rounded-lg bg-teal-600 px-4 py-2 text-sm font-semibold text-white shadow-sm hover:bg-teal-700 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-teal-600 disabled:opacity-50 disabled:cursor-wait transition-colors">
          Prověřit
        </button>
      </form>
      <!-- Status pill -->
      <div id="status-pill" class="hidden sm:flex items-center gap-1.5 px-2.5 py-1.5 rounded-full text-xs font-medium bg-slate-50 text-slate-500 ring-1 ring-inset ring-slate-200/80 whitespace-nowrap select-none">
        <span id="status-dot" class="w-1.5 h-1.5 rounded-full bg-slate-300 flex-shrink-0"></span>
        <span id="status-text">Připraveno</span>
      </div>
    </div>
  </div>
</header>
```

**Step 4: Verify HTML is valid**

Open in browser, confirm no layout breaks.

**Step 5: Commit**

```bash
git add index.html
git commit -m "feat: restructure HTML shell — sidebar brand, new-check button, conditional header"
```

---

### Task 3: Rewrite `heroView()` — ChatGPT-style centered input

**Files:**
- Modify: `app.js` (lines 176–200: `heroView()`)

Replace `heroView()` with a centered layout containing:
1. Logo mark + "Justice Práskač" brand (centered)
2. One-liner: "Prověř firmu z veřejných rejstříků"
3. Search input with icon (centered, max-w-xl ~540px) + submit button
4. Suggestion chips below input (3-4 example companies)
5. Bottom: last 2-3 history items as subtle cards (hidden if no history)

The hero has its OWN search form (id `hero-search-form`) with its own input (id `hero-input`). This is separate from the header search form. When the user submits or picks from autocomplete, the hero input value gets synced to the header input.

```javascript
function heroView() {
  const chips = [
    { label: "Škoda Auto", query: "Škoda Auto" },
    { label: "ČEZ", query: "ČEZ" },
    { label: "Agrofert", query: "Agrofert" },
    { label: "IČO 25788001", query: "25788001" },
  ];

  const recent = state.history.slice(0, 3);

  return `
  <div class="hero-centered px-4 sm:px-6">
    <div class="w-full max-w-xl mx-auto">
      <div class="text-center mb-8">
        <div class="inline-flex items-center justify-center w-12 h-12 rounded-2xl bg-teal-50 border border-teal-100 text-teal-700 mb-4">
          <svg class="w-6 h-6" viewBox="0 0 24 24" fill="none"><path d="M4 17.5V6.5L12 3l8 3.5v11L12 21l-8-3.5Z" stroke="currentColor" stroke-width="1.6"/><path d="M8.5 9.5h7M8.5 12h7M8.5 14.5h4.5" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>
        </div>
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
            class="block w-full rounded-xl border-0 bg-white py-3.5 pl-12 pr-24 text-base text-slate-900 ring-1 ring-inset ring-slate-200 shadow-sm placeholder:text-slate-400 focus:ring-2 focus:ring-teal-500 transition-colors">
          <div class="absolute inset-y-0 right-0 flex items-center pr-2">
            <button type="submit" class="rounded-lg bg-teal-600 px-4 py-2 text-sm font-semibold text-white shadow-sm hover:bg-teal-700 transition-colors">
              Prověřit
            </button>
          </div>
        </div>
        <!-- Autocomplete dropdown renders here -->
        <div id="hero-autocomplete" class="absolute left-0 right-0 top-full mt-1 z-50"></div>
      </form>
      <!-- Suggestion chips -->
      <div class="flex flex-wrap justify-center gap-2 mt-4">
        ${chips.map((c) => `
          <button type="button" data-chip-query="${esc(c.query)}"
            class="px-3 py-1.5 rounded-full text-xs font-medium text-slate-600 bg-white ring-1 ring-slate-200 hover:bg-slate-50 hover:text-teal-700 transition-colors">
            ${esc(c.label)}
          </button>`).join("")}
      </div>
      <!-- Recent history -->
      ${recent.length ? `
      <div class="mt-10 pt-6 border-t border-slate-100">
        <div class="text-[11px] font-medium text-slate-400 uppercase tracking-wider mb-3 text-center">Poslední prověření</div>
        <div class="grid grid-cols-1 sm:grid-cols-3 gap-2">
          ${recent.map((item) => `
            <button type="button" data-pick-id="${esc(item.subject_id)}" data-pick-query="${esc(item.query || item.ico || item.name || "")}"
              class="text-left px-3.5 py-3 rounded-xl bg-white ring-1 ring-slate-200/60 shadow-sm hover:ring-slate-300 hover:shadow transition-all group">
              <div class="text-sm font-medium text-slate-800 truncate group-hover:text-teal-700">${esc(item.name || "Firma")}</div>
              <div class="text-[11px] text-slate-400 mt-0.5">${esc(item.ico || "")}${item.updated_at ? " · " + fmtRelative(item.updated_at) : ""}</div>
            </button>`).join("")}
        </div>
      </div>` : ""}
    </div>
  </div>`;
}
```

**Step 2: Verify syntax**

Run: `node -c app.js`
Expected: no syntax errors

**Step 3: Commit**

```bash
git add app.js
git commit -m "feat: rewrite heroView as ChatGPT-style centered input with chips and history"
```

---

### Task 4: Add autocomplete system

**Files:**
- Modify: `app.js`

This adds debounced autocomplete on both the hero input and the header input. Pattern from user's Demolice Reciklace project: 300ms debounce, min 2 chars, AbortController for cancellation.

**Step 1: Add autocomplete state and module-level variables**

Add to the `state` object (after `lastScrollY`):

```javascript
  autocompleteResults: [],
  autocompleteOpen: false,
  autocompleteLoading: false,
```

Add module-level variables (after DOM refs):

```javascript
let _acTimer = null;       // debounce timer
let _acController = null;  // AbortController for in-flight requests
```

**Step 2: Add autocomplete functions**

Add these after the `toggleRail()` function (around line 141), before the VIEWS section:

```javascript
// ---- Autocomplete ----
function autocompleteHtml(results) {
  if (!results.length) return "";
  return `
  <div class="autocomplete-dropdown bg-white rounded-xl ring-1 ring-slate-200 shadow-lg overflow-hidden max-h-[320px] overflow-y-auto">
    ${results.map((m) => `
      <button type="button" data-pick-id="${esc(m.subject_id)}" data-pick-query="${esc(m.name || m.ico || "")}"
        class="w-full flex items-center justify-between px-4 py-3 hover:bg-slate-50 transition-colors text-left group border-b border-slate-50 last:border-0">
        <div class="min-w-0">
          <div class="text-sm font-medium text-slate-900 group-hover:text-teal-700 truncate">${esc(m.name)}</div>
          <div class="text-xs text-slate-400 mt-0.5 truncate">IČO ${esc(m.ico_display || m.ico)}${m.address ? " · " + esc(m.address) : ""}</div>
        </div>
        <svg class="w-4 h-4 text-slate-300 group-hover:text-teal-500 flex-shrink-0 ml-3" fill="none" viewBox="0 0 24 24" stroke-width="2" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" d="m8.25 4.5 7.5 7.5-7.5 7.5"/></svg>
      </button>`).join("")}
  </div>`;
}

function clearAutocomplete() {
  state.autocompleteResults = [];
  state.autocompleteOpen = false;
  state.autocompleteLoading = false;
  if (_acTimer) { clearTimeout(_acTimer); _acTimer = null; }
  if (_acController) { _acController.abort(); _acController = null; }
  // Clear both dropdowns
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
```

**Step 3: Add autocomplete dropdown container to header search form**

In `index.html`, inside the `<form id="search-form">`, after the `</div>` that closes the `relative flex-1 max-w-xl` wrapper but before the submit button, add an autocomplete container. Actually, wrap the input div in a relative parent:

The header search form's input wrapper (`.relative.flex-1.max-w-xl`) needs an autocomplete dropdown below it. Add after the input `</div>` (closing the `.relative.flex-1.max-w-xl`):

Actually, make the max-w-xl wrapper itself relative and add the dropdown inside it:

```html
<div class="relative flex-1 max-w-xl">
  <div class="pointer-events-none absolute inset-y-0 left-0 flex items-center pl-3">
    <svg ...></svg>
  </div>
  <input id="query-input" ...>
  <!-- Header autocomplete dropdown -->
  <div id="header-autocomplete" class="absolute left-0 right-0 top-full mt-1 z-50"></div>
</div>
```

**Step 4: Wire autocomplete input events in `initEvents()`**

In `initEvents()`, add input event listeners:

```javascript
// Autocomplete on hero input (delegated — hero-input is dynamically rendered)
document.addEventListener("input", (e) => {
  if (e.target.id === "hero-input") {
    handleAutocompleteInput(e.target.value, "hero-autocomplete");
  }
});

// Autocomplete on header input
$input.addEventListener("input", () => {
  handleAutocompleteInput($input.value, "header-autocomplete");
});

// Close autocomplete on click outside
document.addEventListener("click", (e) => {
  if (!e.target.closest("#hero-search-form") && !e.target.closest("#search-form")) {
    clearAutocomplete();
  }
});

// Close autocomplete on Escape
// (modify existing keydown handler to also handle this)
```

**Step 5: Wire hero form submission**

Add delegated submit handler for the hero form. In `initEvents()`:

```javascript
// Hero search form submission (delegated — form is dynamically rendered)
document.addEventListener("submit", (e) => {
  if (e.target.id === "hero-search-form") {
    e.preventDefault();
    const heroInput = document.getElementById("hero-input");
    const q = heroInput ? heroInput.value.trim() : "";
    if (q) {
      clearAutocomplete();
      $input.value = q;   // sync to header input
      handleSearch(q);
    }
  }
});
```

**Step 6: Wire chip clicks**

Add to the click delegation in `initEvents()`:

```javascript
// Suggestion chip
const chip = e.target.closest("[data-chip-query]");
if (chip) {
  const q = chip.dataset.chipQuery;
  if (q) {
    clearAutocomplete();
    $input.value = q;
    handleSearch(q);
  }
  return;
}
```

**Step 7: Clear autocomplete when picking a company**

In the existing `[data-pick-id]` click handler, add `clearAutocomplete();` before calling `handlePick()`.

**Step 8: Verify syntax**

Run: `node -c app.js`

**Step 9: Commit**

```bash
git add app.js index.html
git commit -m "feat: add debounced autocomplete dropdown for company search"
```

---

### Task 5: Rewrite `loadingView()` — ChatGPT thinking bubble

**Files:**
- Modify: `app.js` (lines 202–245: `loadingView()`)

Replace with a chat-style thinking bubble. Each SSE status line appears as a new line with a subtle animation. The latest line has a spinning indicator; previous lines have a checkmark.

```javascript
function loadingView(previewOrText, log) {
  const text = typeof previewOrText === "string" ? previewOrText : "Čtu veřejné podklady z justice.cz.";
  const items = (log || []).length ? log : [text];
  const preview = typeof previewOrText === "object" ? previewOrText : null;

  return `
  <div class="max-w-2xl mx-auto px-4 sm:px-6 py-12 view-enter">
    <!-- Thinking bubble -->
    <div class="${CLS_CARD} overflow-hidden">
      ${preview ? `
      <div class="px-5 py-4 border-b border-slate-100">
        <div class="flex items-center gap-3">
          <div class="w-9 h-9 rounded-xl bg-teal-50 border border-teal-100 flex items-center justify-center text-teal-700 flex-shrink-0">
            <svg class="w-4.5 h-4.5" viewBox="0 0 24 24" fill="none"><path d="M4 17.5V6.5L12 3l8 3.5v11L12 21l-8-3.5Z" stroke="currentColor" stroke-width="1.6"/><path d="M8.5 9.5h7M8.5 12h7M8.5 14.5h4.5" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>
          </div>
          <div>
            <div class="text-sm font-semibold text-slate-900">${esc(preview.name || "Načítaná firma")}</div>
            <div class="text-xs text-slate-400">IČO ${esc(preview.ico || "—")}</div>
          </div>
        </div>
      </div>` : `
      <div class="px-5 py-4 border-b border-slate-100 flex items-center gap-3">
        <div class="w-9 h-9 rounded-xl bg-teal-50 border border-teal-100 flex items-center justify-center text-teal-700 flex-shrink-0">
          <svg class="w-4.5 h-4.5" viewBox="0 0 24 24" fill="none"><path d="M4 17.5V6.5L12 3l8 3.5v11L12 21l-8-3.5Z" stroke="currentColor" stroke-width="1.6"/><path d="M8.5 9.5h7M8.5 12h7M8.5 14.5h4.5" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>
        </div>
        <div>
          <div class="text-sm font-semibold text-slate-900">Analyzuji firmu</div>
          <div class="text-xs text-slate-400">Veřejné rejstříky</div>
        </div>
      </div>`}
      <!-- Streaming status lines -->
      <div class="px-5 py-4 space-y-2.5">
        ${items.map((item, i) => {
          const isLast = i === items.length - 1;
          return `
          <div class="flex items-start gap-2.5 thinking-line" style="animation-delay: ${i * 30}ms">
            ${isLast
              ? '<div class="w-4 h-4 mt-0.5 flex-shrink-0 border-2 border-teal-400 border-t-transparent rounded-full animate-spin"></div>'
              : '<svg class="w-4 h-4 text-teal-500 mt-0.5 flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke-width="2.5" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" d="m4.5 12.75 6 6 9-13.5"/></svg>'}
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
```

**Step 2: Verify syntax**

Run: `node -c app.js`

**Step 3: Commit**

```bash
git add app.js
git commit -m "feat: rewrite loadingView as ChatGPT-style thinking bubble"
```

---

### Task 6: Update `render()` and state machine

**Files:**
- Modify: `app.js`

Key changes:
1. **Remove `matchView()`** entirely (the function and its call in render)
2. **Header visibility**: hidden on empty state, visible when loading/profile/error
3. **Remove `state.matches` logic** from render — autocomplete handles disambiguation
4. **`handleSearch()`**: When multiple results come back, show them in autocomplete dropdown instead of calling matchView
5. **Show/hide header** based on state

**Step 1: Update render()**

```javascript
function render() {
  if (!$content) return;
  $submit.disabled = state.loading || state.searching;

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
```

**Step 2: Update `handleSearch()` to use autocomplete instead of matchView**

The new `handleSearch()` skips the "searching" loading state. Instead, when user submits the form with Enter (not from autocomplete), it searches and:
- If 1 result → pick it directly
- If exact IČO match → pick it directly
- If multiple → show in autocomplete dropdown (or just pick the first if from form submit)

Actually, the form submit should work the same as before, but without the matchView. When multiple results come back from a form submit (not autocomplete), auto-pick the best match or show them in the autocomplete dropdown.

```javascript
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
    // Multiple results — pick the first one (autocomplete handles disambiguation)
    await handlePick(results[0].subject_id);
  } catch (e) {
    state.loading = false;
    state.error = e.message || "Hledání se nepovedlo.";
    render();
  }
}
```

**Step 3: Remove `matchView()` function** (lines 261–279)

Delete the entire `matchView()` function.

**Step 4: Remove `state.searching` and `state.matches`**

Remove `searching: false` and `matches: []` from the initial state object. Remove `state.selectedMatch` too — replace its usage with just tracking the ID locally.

Actually, keep `state.matches` and `state.selectedMatch` but just don't render matchView. The selectedMatch is still used in retry logic. Simplify: remove `state.searching` (no longer a separate visual state), keep `state.matches` for internal tracking.

Clean up `state`:
- Remove `searching: false` from initial state
- Keep `matches: []` (used internally)
- Keep `selectedMatch` (used in retry)
- Remove the `state.searching` condition from render()
- Remove the `state.matches` condition from render()

**Step 5: Add `handleNewCheck()` function**

```javascript
function handleNewCheck() {
  state.query = "";
  state.loading = false;
  state.error = null;
  state.profile = null;
  state.preview = null;
  state.statusLog = [];
  state.matches = [];
  state.selectedMatch = null;
  state.expandedAccordions.clear();
  state.expandedPanels.clear();
  clearAutocomplete();
  $input.value = "";
  render();
  // Focus the hero input after render
  const heroInput = document.getElementById("hero-input");
  if (heroInput) heroInput.focus();
}
```

**Step 6: Wire "Nové prověření" buttons**

In `initEvents()`:

```javascript
document.getElementById("new-check-btn")?.addEventListener("click", () => {
  handleNewCheck();
});
document.getElementById("new-check-btn-mobile")?.addEventListener("click", () => {
  handleNewCheck();
  closeDrawer();
});
```

**Step 7: Verify syntax**

Run: `node -c app.js`

**Step 8: Commit**

```bash
git add app.js
git commit -m "feat: update state machine — remove matchView, add handleNewCheck, conditional header"
```

---

### Task 7: Polish transitions and edge cases

**Files:**
- Modify: `app.js`, `style.css`

**Step 1: Add view-enter class to profile and error views**

In `profileView()`, wrap the outer div content:
```html
<div class="max-w-6xl mx-auto px-4 sm:px-6 py-5 view-enter">
```

In `errorView()`, add `view-enter` to the outer div:
```html
<div class="max-w-lg mx-auto px-4 sm:px-6 py-12 text-center view-enter">
```

**Step 2: Handle scroll behavior for empty state**

On empty state, disable header scroll hide (no header to hide). The `handleScroll` function should be a no-op when header is hidden. Already works because `$header.classList.toggle` won't matter when header is hidden.

**Step 3: Sync header input with hero input on pick**

When a company is picked from autocomplete in the hero view, sync the query to the header input. This already happens in the click handler where we do `$input.value = q`. Verify this works.

**Step 4: Handle keyboard navigation in autocomplete**

Add basic Escape-to-close in the existing keydown handler:

```javascript
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") {
    if (state.autocompleteOpen) { clearAutocomplete(); return; }
    if (state.drawerOpen) closeDrawer();
  }
});
```

**Step 5: Close autocomplete when form is submitted**

In the form submit handler, add `clearAutocomplete()`:

```javascript
$form.addEventListener("submit", (e) => {
  e.preventDefault();
  clearAutocomplete();
  const q = $input.value.trim();
  if (q) handleSearch(q);
});
```

**Step 6: Remove the rail-toggle button handler**

Since we removed the "Historie" toggle from the header, remove:
```javascript
document.getElementById("rail-toggle")?.addEventListener("click", toggleRail);
```

The `toggleRail()` function can stay (it's still useful for potential future use) or be removed. Remove it to keep things clean.

**Step 7: Verify everything works**

Run: `node -c app.js`

Test manually in browser:
- Empty state: centered input visible, header hidden, sidebar visible
- Type in hero input: autocomplete dropdown appears
- Click autocomplete result: thinking bubble appears, header slides in
- Profile loads: profile replaces thinking bubble
- Click "Nové prověření": returns to empty state
- Mobile: drawer has brand + new check button

**Step 8: Commit**

```bash
git add app.js style.css
git commit -m "feat: polish transitions, keyboard handling, and edge cases"
```

---

### Task 8: Final cleanup

**Files:**
- Modify: `app.js`

**Step 1: Remove dead code**

- Remove `matchView()` if not already removed
- Remove `state.searching` references everywhere (including `render()`, `handleSearch()`, `renderHistory()`)
- Remove the `toggleRail()` function and `rail-toggle` handler
- Clean up any leftover comments referencing old behavior

**Step 2: Remove the submit button from the hero form**

Wait — we do want a submit button in the hero. Keep it.

**Step 3: Update the `handlePick` data-pick-id handler**

When picking from autocomplete in the hero, sync query to header input AND clear autocomplete:

In the click delegation, the `[data-pick-id]` handler already has `$input.value = q` and we added `clearAutocomplete()`. Verify this is correct.

**Step 4: Test the full flow end-to-end**

1. Load page → see centered hero with chips
2. Click "ČEZ" chip → searching, thinking bubble, profile loads
3. Click "Nové prověření" in sidebar → back to hero
4. Type "Škoda" in hero input → autocomplete shows results
5. Click a result → thinking bubble → profile
6. Type in header input → autocomplete in header
7. Submit header form → new search
8. Error state → retry button works
9. Mobile drawer → brand, new check button, history
10. Browser back/forward → not applicable (no routing, but state should be consistent)

**Step 5: Final commit**

```bash
git add app.js style.css index.html
git commit -m "feat: complete ChatGPT-style UI redesign"
```
