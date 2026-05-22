# UI/UX Review — Job Hunt Assistant
**Date:** 2026-05-22
**Reviewer:** UI Design Audit (Claude Sonnet 4.6)
**Scope:** Full frontend source review — all panels, common components, CSS tokens, utils

---

## Executive Summary

The codebase reflects several rounds of incremental improvement (the prior refactor plan in `ui-ux-refactor-plan.md` got Phases 1–2 mostly done: Modal, StatusBadge, MetricGrid are solid, and dialogs are consolidated). What remains is a layer of organic growth: inconsistent spacing values scattered as magic numbers, duplicate style object patterns, a missing CSS token (`--shadow-lg`), a tab navigation that wraps awkwardly at common screen widths, and several interaction patterns that feel unfinished or confusing relative to the sophistication of the underlying features.

The design system foundation (color tokens, typography scale, badge system) is genuinely good. The work ahead is mostly about tightening execution rather than redesigning from scratch.

---

## 1. Current Design System Inventory

### What exists and works well

**Color tokens** (`index.css`): A coherent 14-token palette covering surfaces, accents, semantic colors, and header gradients. All components reference these tokens consistently. The indigo-to-purple gradient header (`--header-from: #2d3a9e` to `--header-to: #4a5cf7`) is distinctive and professional.

**Typography**: System font stack, 14px base, consistent use of 13px for secondary content and 12px for metadata. `--text` / `--text-muted` are used reliably.

**Badge system**: The `.badge` + `.badge-{tone}` CSS class system is clean and well-adopted across StatusBadge, EmailsPanel, TriagePanel, etc.

**Common components**: `Modal`, `StatusBadge`, `MetricGrid`, `UndoEditNotice`, `CompanyHistoryDialog`, `CareerScanModals` — these are well-structured and form a genuine component library foundation.

**Button classes**: `btn-primary`, `btn-secondary`, `btn-danger`, `btn-success` — complete, well-styled, with hover/active states.

### What is incomplete or missing

| Item | Gap |
|---|---|
| `--shadow-lg` | Referenced in `JobCard.jsx`, `TriagePanel.jsx`, `JobQueue.jsx` but **never defined** in `index.css`. Falls back to `undefined`, so those elements get no shadow. |
| Spacing scale | No `--space-*` tokens. Magic pixel values (6px, 7px, 8px, 10px, 11px, 12px, 14px, 16px, 18px, 20px, 22px, 24px, 28px) appear inconsistently across components. |
| `--radius-sm` | `--radius: 10px` and `--radius-lg: 14px` exist, but small elements (badges use `999px`, context menu borders use `4px` or `6px`) lack a small-radius token. |
| Focus ring standard | `index.css` defines a 3px accent focus ring for form controls. Buttons have no focus-visible style — keyboard users get the browser default (which varies). |
| Mobile/responsive | No responsive breakpoints anywhere in the CSS or JSX. The app appears designed for a fixed ~1200px wide container with no adaptation below that. |

---

## 2. Specific Inconsistencies and Rough Edges

### 2.1 Tab Navigation (HIGH)

**Problem:** The tab bar in `App.jsx` uses `whiteSpace: "pre-line"` and embedded newlines (`\n`) to force two-line tab labels ("Application\nTracking", "Emails Scanning\nHistory"). These tabs have `minWidth: 74px` and `padding: 6px 14px` with no overall width constraint. At 1200px, the seven tabs, logo, and auth status indicator almost certainly overflow or wrap into a second line. The two-line labels also look inconsistent next to the single-line labels.

**Before:** `{ key: "emails", label: "Emails Scanning\nHistory" }` rendered in a tab with `fontSize: 13px, minWidth: 74px`

**After:** Shorten labels to single-line equivalents. Suggested renames:
- "Application\nTracking" → "Applications"
- "Emails Scanning\nHistory" → "Email History"
- "Career Sites" keeps as-is

This is a one-line fix in the `TABS` constant in `App.jsx`.

**Deeper issue:** The tab bar has no overflow behavior specified. At smaller widths it will break layout. Consider a `flex-wrap: wrap` or a horizontal scroll on the nav, or reducing the header to icon + label tabs.

---

### 2.2 Missing `--shadow-lg` Token (HIGH)

**Problem:** Three files reference `var(--shadow-lg)` for context menus and the import dialog — but this CSS variable is never defined in `index.css`. The context menus and `importDialogPanelStyle` in `JobQueue` therefore render with no shadow at all.

**Fix:** Add to `:root` in `index.css`:
```css
--shadow-lg: 0 8px 32px rgba(30,34,69,0.14), 0 2px 8px rgba(30,34,69,0.08);
```

This is a two-minute fix with a meaningful visual impact on the three affected surfaces.

---

### 2.3 Button Sizing Fragmentation (MEDIUM-HIGH)

The codebase has four distinct button-padding dialects and they are mixed within single components:

| Pattern | Example location |
|---|---|
| Default global (`7px 16px`, `font-size 13px`) | `index.css` base |
| Compact (`5px 11px`, `font-size 12px`) | DuplicateCard action row, TriagePanel toolbar |
| Micro (`3px 9px`, `font-size 11px`) | UndoEditNotice, reasoning section |
| Custom per-element overrides | `fontSize: "13px", padding: "7px 14px"` repeated verbatim across every chat Send button |

The Send button in the cover letter chat, pitch chat, fit review chat, and resume tailor chat all repeat the same 5 lines of inline style (`fontSize: "13px", padding: "7px 14px", alignSelf: "flex-end"` etc.) because there is no "compact primary" button variant in the global CSS.

**Recommendation:** Add two button size modifiers to `index.css`:
```css
.btn-sm {
  font-size: 12px;
  padding: 5px 12px;
}
.btn-xs {
  font-size: 11px;
  padding: 3px 9px;
}
```
Then replace the `style={{ fontSize: "12px", padding: "5px 11px" }}` overrides with `className="btn-secondary btn-sm"`.

---

### 2.4 JobCard State Explosion and Action Row Clarity (MEDIUM-HIGH)

`JobCard.jsx` manages 30+ state variables for five independent modals (cover letter, pitch, fit chat, resume tailor, description edit), each with their own `*Open`, `*Messages`, `*Input`, `*Sending`, `*PendingUpdate`, `*Copied` states. This is an architectural issue beyond the scope of a UI audit, but it creates a specific UX problem: **all five modals share a single `saveStatus` state and a single `folderEditOpen` state**, creating confusing behavior when switching between cover letter and resume tailoring views. The "Saved as Markdown" confirmation from a resume tailor save will flash incorrectly if the cover letter modal is then opened.

**Action row layout:** The primary action row at the bottom of JobCard has: "Add Job URL", "Review Fit with AI", "Re-evaluate Fit", "Applied", "Duplicate", "Skip ▾" — six items, some of which are secondary utilities mixed with primary decisions. The "Applied" and "Skip" buttons are the primary decision points but they share equal visual weight with "Re-evaluate Fit."

**Recommendation:** Establish a visual hierarchy in the action row:
- Primary tier (right-aligned, full weight): "Applied" (btn-success), "Skip ▾" (btn-danger)
- Secondary tier (left-aligned, muted): "Review Fit with AI", "Re-evaluate Fit", "Add Job URL"

---

### 2.5 DuplicateCard vs TriageCard Layout Inconsistency (MEDIUM)

`DuplicateCard` (in TriagePanel) and `JobCard` (used in both JobQueue and Triage) render the same data in noticeably different layouts:

- `DuplicateCard` puts company name prominently first (`fontSize: 15px, fontWeight: 600`) then title below in muted text — company-first hierarchy.
- `JobCard` puts "Title at Company" in a single line with the fit score badge to the left — title-first hierarchy.

Neither layout is wrong, but they appear on the same triage page mixed together, creating visual inconsistency. The `DuplicateCard` specifically renders a border using `var(--warning, #b45309)` — a CSS variable that doesn't exist in the token system, falling back to the hardcoded amber `#b45309`. This is functional but bypasses the token system.

**Fix:** Replace `var(--warning, #b45309)` with `var(--orange)` which is already in the system at `#b85500` (nearly identical hue).

---

### 2.6 Settings Panel Sidebar Nav Pattern (MEDIUM)

The `SettingsPanel` uses a left-sidebar nav with `width: 160px` and button-style nav items. This pattern is solid, but the active state styling (`background: var(--surface2), border: 1px solid var(--border)`) looks like a card, not an active nav indicator. Standard convention for a sidebar nav active state is a left accent bar or filled background, not a full outline — the current implementation makes active and inactive items look similar until examined closely.

**Recommendation:**
```css
/* Active sidebar nav item */
background: var(--accent-light);
color: var(--accent);
border-left: 3px solid var(--accent);
padding-left: 9px; /* compensate for the border width */
```

---

### 2.7 Toolbar Sort Row vs Search Row Inconsistency (MEDIUM)

In `TriagePanel`, the toolbar has two visual rows: a main row with search input, checkbox, count text, and a kebab menu; and a second row below with "Sort:" label and a select. In `ApplicationSheetPanel`, the toolbar is a single row with title, search, Refresh button, and kebab. In `JobQueue`, the toolbar has a mode-toggle button group, then a separate search.

All three panels have different toolbar architectures. When switching between tabs, the page header area visually "resets" with a different density and layout each time.

**Recommendation:** Define a standard panel-toolbar pattern: `[Title block] [flex spacer] [search input, max-width 320px] [action buttons, right-aligned]`. Sort controls can live below this line if needed. Keeping the title + summary on the left and actions on the right is already done in `ApplicationSheetPanel` — that panel's toolbar style should be the reference implementation.

---

### 2.8 Empty State Inconsistency (LOW-MEDIUM)

The Triage empty state uses a `✅` emoji with `fontSize: 32px` as an icon. The Application Sheet empty state uses a plain text paragraph inside `.empty-state`. The Job Queue empty state (when no jobs match search) is a styled `<p>` inside `.empty-state`. None of these are wrong, but they have different structure and feel.

The `.empty-state` CSS class (`padding: 72px 20px`, `text-align: center`) is defined globally but only some panels use it.

---

### 2.9 Chat Input Duplicated Inline Across Four Modals (LOW-MEDIUM)

The cover letter, pitch, resume tailor, and fit review chat panels all implement the same chat message list + textarea + Send button pattern with essentially identical inline styles — the chat bubble style objects are literally copy-pasted four times in `JobCard.jsx`. The fit review chat has been extracted to a `FitReviewPanel` sub-component with named style constants (`fitChatBubbleStyle`, etc.), but the cover letter, pitch, and resume tailor chats have not been refactored to use it.

**Recommendation:** Create a reusable `ChatPanel` component (or at minimum a shared `chatBubbleStyle` constant in a style module) and use it across all four chat surfaces. This also makes future changes (like adding user avatars, timestamps, or markdown rendering) a single-point change.

---

### 2.10 Progress/Status Feedback Fragmentation (LOW-MEDIUM)

The app has several different patterns for showing in-progress feedback:
1. `inline-spinner` CSS class (used in App.jsx, CompanyHistoryDialog, email panels)
2. Loading text changes ("Applying…", "Re-evaluating...", "Saving...") on buttons
3. The `loading-bar` CSS class progress bar (App.jsx ListenerImportDialog, CareerScanProgressPopup)
4. `inlineImportProgressStyle` text box in JobQueue

The spinner is the most reusable but isn't applied to buttons — loading state is communicated only through text label changes. This means there's no visual "something is happening" feedback beyond the text swap, which is easy to miss for operations that take 5+ seconds (cover letter generation, AI scoring).

**Recommendation:** Add an inline spinner to the `btn-primary` and `btn-success` loading states for long operations (cover letter, pitch, re-evaluate). The spinner is already defined in CSS — it's just not used inside buttons.

---

### 2.11 ApplicationSheetPanel Sort Indicator Glyphs (LOW)

The sort indicator uses raw `^` and `v` characters (`sortConfig.direction === "asc" ? "^" : "v"`). These look thin and typographically inconsistent with the rest of the UI. Use `▲` / `▼` (U+25B2/25BC) or `↑` / `↓` for a cleaner look. The sort active state also shows nothing when unsorted rather than a subtle indicator, making it hard to discover which columns are sortable.

---

### 2.12 "Applied" Button Wording (LOW)

In `JobCard.jsx`, the primary approval action is labeled **"Applied"** — past tense. This reads as a status label rather than an action verb. The button should use an imperative: **"Mark Applied"** or **"Move to Queue"** to clearly communicate that clicking it performs an action. The label only changes to "Applying…" during the load state, which is the only hint that it's a verb.

---

## 3. Proposed Design System Additions

### 3.1 CSS Token Additions

Add to `:root` in `index.css`:

```css
/* Missing shadow token (currently undefined — breaks context menus) */
--shadow-lg: 0 8px 32px rgba(30,34,69,0.14), 0 2px 8px rgba(30,34,69,0.08);

/* Small radius for inline code, toggle pills */
--radius-sm: 6px;

/* Spacing scale — use these instead of arbitrary px values */
--space-1: 4px;
--space-2: 8px;
--space-3: 12px;
--space-4: 16px;
--space-5: 20px;
--space-6: 24px;
--space-8: 32px;
```

The spacing scale isn't something to retroactively apply everywhere at once — but starting to use these tokens in new components and in fixes prevents the magic-number fragmentation from growing.

### 3.2 Button Size Modifiers

```css
/* Add to index.css alongside existing button classes */
.btn-sm {
  font-size: 12px;
  padding: 5px 12px;
}
.btn-xs {
  font-size: 11px;
  padding: 3px 9px;
}
```

### 3.3 Focus Styles for Buttons

```css
button:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 2px;
}
```

### 3.4 Panel Toolbar Pattern

Define a shared CSS class:

```css
.panel-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 14px;
}

.panel-toolbar-title {
  font-size: 18px;
  font-weight: 700;
  line-height: 1.2;
}

.panel-toolbar-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-left: auto;
}
```

`ApplicationSheetPanel` and `TriagePanel` already implement this pattern via inline `const toolbarStyle` and `triageToolbarStyle` objects. Extract those to shared CSS.

---

## 4. Prioritized Action List

### Priority 1 — Fix Before Any New Features (Bugs or Visual Breaks)

1. **Define `--shadow-lg` in `index.css`** — currently broken for context menus and the import dialog in three files. 30-second fix.

2. **Shorten tab labels to single-line strings** — "Emails Scanning\nHistory" and "Application\nTracking" cause layout instability at common window widths and look unpolished. 2-minute fix in `App.jsx`.

### Priority 2 — High-Value, Low-Effort Consistency Wins

3. **Add `.btn-sm` and `.btn-xs` button size classes to `index.css`** — eliminates the most pervasive form of inline style repetition (24+ button elements use `style={{ fontSize: "12px", padding: ... }}`).

4. **Fix DuplicateCard amber border token** — replace `var(--warning, #b45309)` with `var(--orange)` in `TriagePanel.jsx`. Brings the card into the token system.

5. **Update sort indicator glyphs** — replace `^`/`v` with `▲`/`▼` in `ApplicationSheetPanel.jsx`.

6. **Rename "Applied" button to "Mark Applied"** in `JobCard.jsx` action row — removes label/action ambiguity.

### Priority 3 — Interaction Clarity and Hierarchy

7. **Establish visual hierarchy in JobCard action row** — separate the "decision" buttons (Mark Applied, Skip) from "utility" buttons (Review Fit with AI, Re-evaluate Fit) using layout and visual weight. Utility buttons should be smaller or positioned left; decision buttons should be right-aligned and full-weight.

8. **Update Settings sidebar nav active state** — change from the full-outline style to a left-accent-bar pattern to follow standard sidebar conventions.

9. **Add spinner to long-running button operations** — cover letter generation, pitch generation, and re-evaluate all take 5–15s. The button text swap alone is not enough feedback. Use the existing `.inline-spinner` class inside the button.

### Priority 4 — Architecture Cleanup (Schedule for a Focused Refactor Session)

10. **Extract shared `ChatPanel` component** — the chat message list + input + send button pattern is duplicated four times in `JobCard.jsx`. A single `ChatPanel` component with props for messages, input, send handler, and sending state would reduce the file by ~150 lines and make future chat UI changes a single edit.

11. **Define `--shadow-lg` and shared toolbar CSS classes** — establish the full spacing/shadow token set, then migrate `triageToolbarStyle` and `ApplicationSheetPanel.toolbarStyle` to shared `.panel-toolbar` CSS.

12. **Consolidate empty state pattern** — all panels should use the `.empty-state` class. Add an optional `icon` prop pattern (or just emoji + standard sizing) so empty states feel cohesive.

### Priority 5 — Deferred (Polish After Stability)

13. **Add keyboard focus styles to buttons** — `button:focus-visible` rule in `index.css`. Small accessibility win.

14. **Consider tab overflow behavior** — as the tab count grows, the header may need horizontal scroll or a dropdown for tabs that don't fit. Not urgent if the tab count stays at seven.

15. **Responsive layout foundation** — the 1200px max-width container with no breakpoints is acceptable for a personal desktop tool, but a single `@media (max-width: 900px)` breakpoint that collapses the header into a simpler layout would prevent horizontal scrolling on smaller monitors.

---

## 5. Screenshot Observations

### Screenshot 1 — Triage Panel (Mixed Card Types)

**What was confirmed by the screenshot:**

The triage cards are more information-dense than the source review alone indicated. Each card contains, in order: fit score circle, job title + company chip + source badge + date + tracked count + Re-evaluate button (all on one header row), then a parser confidence + fit % line, a URL, an email excerpt inset box, five action buttons in a row, and finally a three-item or two-item decision row. That is roughly eight distinct visual regions on a single card before any expansion.

The **information hierarchy problem** is concrete: the most important question ("what is this job?") is answered in the first row, but it competes visually with the Re-evaluate button and the tracked count on the same line. The second row ("what do I do with this job?") is buried several lines below. A user's eye has no clear landing point.

**Possible duplicate cards** are visually distinct thanks to the amber/orange tinted border and background — this works. However:
- The orange is quite saturated and creates significant visual weight. A softer amber wash (e.g. `rgba(180, 83, 9, 0.08)` background with `rgba(180, 83, 9, 0.4)` border) would communicate "caution/different" without making the card feel alarming.
- The three decision buttons ("Yes, same job — skip it", "Mark as Unique", "No, it's a different role") span the full card width at near-primary button size. They dominate the card. Since these are low-frequency one-time decisions per duplicate, they should be secondary-weight or compact-sized. Consider `btn-sm` sizing for all three.
- The label text in the duplicate card ("Possible duplicate") is a chip near the top — this is well-placed and clear.

**Email excerpt inset boxes** (light grey background): The label "From:" renders in low contrast against the grey background. "New match:" and "From:" are the most important metadata in those boxes but visually they look like placeholder text. The label text needs to be `var(--text)` weight, not `var(--text-muted)`.

**The "View Job" button** with a down-chevron appears in the triage card action row. It looks like a dropdown/split button trigger, but the chevron may just be decorative. If it opens a dropdown, that affordance needs a visible separator between the label and the chevron (the standard split-button pattern: `[View Job | ▾]` with a vertical divider). If the chevron is just decorative, remove it — chevrons on buttons universally signal a menu to users.

**Revised priority for 2.5:** The title-first vs. company-first hierarchy difference between JobCard and DuplicateCard is noticeable in a mixed list. The DuplicateCard's company-first layout (company at 15px/600 weight, title below in muted text) reads as a contact card, while JobCard reads as a job listing. In a mixed triage list this is jarring. The fix remains the same as originally proposed — align DuplicateCard to title-first — but the screenshot confirms it is worth doing.

---

### Screenshot 2 — Header Bar and Job Queue Panel

**Tab bar:** The active pill style (solid dark blue filled pill for "Job Queue") is clear and effective — this is the right pattern. The tab bar at the observed window width fits comfortably on one line. However, "Emails Scanning History" is confirmed to be the longest tab label; on any window narrower than the current viewport it will be the first label to cause wrapping or overflow. The recommendation to shorten it to "Email History" stands.

**Job Queue toolbar:** The action toolbar row contains: "Scan Emails", "Search Job Boards", "Re-evaluate", "Import Jobs" (solid blue, primary weight), "History", a search box, the "31 jobs ready to review" count, and a vertical-ellipsis menu. This is confirmed as visually cluttered. "Import Jobs" correctly uses primary button style. The problem is that "History" and the ellipsis menu sit at the same visual elevation as "Scan Emails" and "Search Job Boards" without any grouping separator. Users cannot tell at a glance which buttons are primary actions vs. access to secondary functions.

**Recommendation for toolbar (sharpens item 2.7):** Group the toolbar into three explicit clusters separated by a `1px var(--border)` vertical divider:
- Left cluster (primary actions): "Scan Emails", "Search Job Boards", "Import Jobs"
- Middle cluster (queue operations): "Re-evaluate", "History", ellipsis menu
- Right cluster: search input, count label

"Import Jobs" using blue primary weight is correct but it should be the only primary button in the row. "Scan Emails" and "Search Job Boards" are also high-frequency primary actions — they should be `btn-primary` too, not secondary weight.

**Fit score circles:** All visible job cards show "75" as the fit score. If this is because fit scoring is not running or returning a constant, users have no way to differentiate cards by quality. This is a data/pipeline issue rather than a design issue, but the design should account for it: if a score is unavailable or stale, the score circle should render in a muted grey with a "?" or "--" instead of a number that looks authoritative. Currently, a stuck score looks identical to a real score.

**"Applied" button visual weight (sharpens item 2.12):** In the job card action row, "Applied" (green) and "Duplicate" (presumably grey or secondary) appear at equal visual weight. "Applied" is the primary happy-path action for a job the user wants to pursue — it should be the most visually prominent button in the card's action row. This confirms the priority-3 recommendation to give decision buttons (Mark Applied, Skip) more visual weight than utility buttons.

**"Skip ·" split button:** The dot after "Skip" is confirmed as a very subtle dropdown affordance. The standard pattern for a split button is a visible divider line inside the button between the label and the caret, and using `▾` instead of a dot. Replacing the dot with `▾` and adding `border-left: 1px solid rgba(255,255,255,0.3)` before the caret would make the split-button nature immediately recognizable.

---

## 6. What the Previous Refactor Plan Got Right

The `ui-ux-refactor-plan.md` Phases 1 and 2 are substantially complete:
- `Modal` is fully adopted; all dialogs use it consistently (Esc, click-outside, standardized header/footer)
- `StatusBadge`, `MetricGrid`, `UndoEditNotice` are solid and well-used
- The global CSS token system covers colors, radius, and shadow (minus `--shadow-lg`) well
- The `.badge-*` color system is consistent across all panels

The remaining work is the precision layer: token completeness, button size standardization, interaction hierarchy, and the inline-style debt in `JobCard.jsx`.
