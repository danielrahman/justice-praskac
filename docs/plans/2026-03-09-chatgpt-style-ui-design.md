# ChatGPT-style UI Redesign

## Goal

Restyle the Justice Práskač frontend to feel like a ChatGPT-style interface: centered input on empty state, thinking bubble during loading, sidebar for history, autocomplete for company search.

## Design

### Empty State (centered)

- Logo mark + "Justice Práskač" brand centered vertically
- One-liner: "Prověř firmu z veřejných rejstříků"
- Single input field with search icon, centered, max-width ~540px
- Below input: 3-4 suggestion chips (example companies/IČOs), clickable
- Bottom area: last 2-3 history items as subtle cards (name + IČO + relative time). Hidden if no history.

### Autocomplete Dropdown

- As user types (debounced ~300ms, min 2 chars), hit `/api/search`
- Show dropdown below input with matching companies (name, IČO, address)
- Clicking a result triggers `handlePick()` directly — no separate match screen
- `matchView()` is removed entirely
- Cancel in-flight requests when new input arrives
- Pattern borrowed from Demolice Reciklace project (ARES lookup)

### Sidebar

- Desktop: 260px rail
- Header: brand/logo + "Nové prověření" button (resets to empty state)
- Body: history list (company name, IČO, relative time)
- Clicking history item loads that company
- Mobile: drawer (same as current), triggered by hamburger
- Brand/logo lives in sidebar, not main header

### Loading = "Thinking" Bubble

- After company is picked, centered input transitions to sticky header bar
- Hero content fades out
- Main area shows a single chat-style card with streaming text
- Each SSE `status` event appends a new line (like AI reasoning)
- `preview` event shows company name/IČO at top of bubble
- Subtle spinner/pulse on the latest line
- Replaces current `loadingView()` entirely

### Transition: Loading → Profile

- Thinking bubble fades out (~200ms)
- Structured profile dashboard fades in
- Profile layout unchanged: section nav, metrics, charts, tables, accordions, context rail on xl+

### Header (after first search)

- Sticky, compact: search input (left/center), status pill (right)
- Search input in header allows new searches without returning to empty state
- Mobile: hamburger + compact brand + input
- Same hide-on-scroll-down behavior

### What Changes

- `heroView()` → minimal centered state with chips + recent history
- `loadingView()` → ChatGPT thinking bubble with streaming lines
- `matchView()` → removed, replaced by autocomplete dropdown
- Search input: centered hero → collapses into header after first search
- Sidebar: gets "Nové prověření" button, brand moves here
- Fade transitions between states

### What Stays

- Profile view (all sections, context rail, charts, scroll spy)
- Accordion documents, expandable panels
- Event delegation, state management architecture
- All backend APIs
- CSS animations (shimmer, drawer, header hide)
- Tailwind CSS via CDN
