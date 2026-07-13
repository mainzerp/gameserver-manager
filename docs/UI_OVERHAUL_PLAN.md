# Web GUI Overhaul Plan

> Status: Planning phase — not yet implemented.
> Goal: Transform the current functional-but-generic GameServer Manager UI into a distinctive, modern "Command Center" interface that feels designed for server operators.

---

## 1. Design Direction: Tactical Command Center

The current UI is a dark admin panel with purple/indigo accents. The overhaul moves to a **tactical command center** aesthetic: deep, layered backgrounds, high-contrast status colors, sharp edges mixed with subtle glows, and a clear information hierarchy inspired by monitoring dashboards, HUDs and professional server tools.

Why this direction:
- A server management panel is used by technical admins who need clarity and speed.
- Status-critical information (running / stopped / crashed) must be immediately scannable.
- It avoids the generic "purple gradient SaaS" look.
- It scales well across light and dark modes.

Tone keywords: precise, technical, calm-under-pressure, high-contrast, utilitarian luxury.

---

## 2. Color Palette

### 2.1 Background Layers

| Token | Dark Mode | Light Mode | Usage |
|-------|-----------|------------|-------|
| `--clr-bg` | `#080a10` | `#f4f6f8` | Page background |
| `--clr-surface-1` | `#0e1220` | `#ffffff` | Cards, panels |
| `--clr-surface-2` | `#141a2b` | `#f8fafc` | Inputs, table headers, hover states |
| `--clr-surface-3` | `#1c233a` | `#eef2f7` | Active/highlighted rows, subtle elevation |
| `--clr-toolbar` | `#11162a` | `#eef2f7` | Toolbars, bulk-action bars |

### 2.2 Text

| Token | Dark Mode | Light Mode | Usage |
|-------|-----------|------------|-------|
| `--clr-text-primary` | `#e8eef7` | `#0f172a` | Headlines, primary text |
| `--clr-text-secondary` | `#9fb0cc` | `#475569` | Body, descriptions |
| `--clr-text-muted` | `#5f6f8e` | `#94a3b8` | Timestamps, meta data |
| `--clr-text-inverse` | `#080a10` | `#ffffff` | Text on accent buttons |

### 2.3 Accent & Interaction

| Token | Dark Mode | Light Mode | Usage |
|-------|-----------|------------|-------|
| `--clr-accent` | `#00d4ff` | `#0ea5e9` | Primary actions, links, active states |
| `--clr-accent-hover` | `#66e5ff` | `#0284c7` | Hover |
| `--clr-accent-subtle` | `rgba(0, 212, 255, 0.10)` | `rgba(14, 165, 233, 0.10)` | Soft backgrounds for accents |
| `--clr-accent-glow` | `rgba(0, 212, 255, 0.35)` | `rgba(14, 165, 233, 0.25)` | Glows, focus rings |

### 2.4 Status Colors (kept bold)

| Status | Dark | Light | Usage |
|--------|------|-------|-------|
| Success | `#22c55e` | `#16a34a` | Running, online, OK |
| Warning | `#f59e0b` | `#d97706` | Starting, warnings, update available |
| Danger | `#ef4444` | `#dc2626` | Crashed, stopped, errors |
| Info | `#38bdf8` | `#2563eb` | Info badges, tips |

### 2.5 Special Server-Type Colors

Optionally, distinguish server types by a subtle color code:
- Minecraft Java: amber/gold accent (`--clr-mc: #f59e0b`)
- Minecraft Bedrock: emerald (`--clr-bedrock: #34d399`)
- Steam: cyan (`--clr-steam: #00d4ff`)

These are used only as small indicators (icons, left borders, status dots) so the UI stays cohesive.

---

## 3. Typography

Current fonts: Inter + JetBrains Mono. To escape the generic look, switch to a more characterful pairing:

- **Display / headings**: `Space Grotesk` or `Satoshi` — geometric, modern, slightly technical.
- **Body / UI**: `Geist` or `Manrope` — clean, contemporary, good density.
- **Monospace**: keep `JetBrains Mono` for console, file paths, ports, logs.

Font sizes stay practical (14–16px body), but headings get slightly more weight and tighter letter-spacing to feel editorial.

---

## 4. Component System

### 4.1 Cards

- Background: `--clr-surface-1`.
- Border: `1px solid --clr-border` (darker, more subtle than today).
- Border radius: `12px` (slightly larger, more modern).
- Shadow: subtle `0 4px 20px rgba(0,0,0,0.15)` in dark mode, `0 2px 12px rgba(0,0,0,0.06)` in light mode.
- Hover: border brightens slightly, surface lifts by 1px.
- Optional: left accent border for server status (green/amber/red).

### 4.2 Buttons

- Primary: solid cyan accent, high contrast, small inner glow on hover.
- Secondary: transparent with tinted border, subtle hover fill.
- Danger: red with subtle glow.
- Sizes: standard `40px` height, `32px` compact, `48px` prominent.
- Border radius: `8px` (consistent with inputs).
- Focus: `2px` accent outline with `2px` offset.

### 4.3 Inputs & Forms

- Background: `--clr-surface-2`.
- Border: `1px solid --clr-border`.
- Focus: accent border + soft glow.
- Labels: smaller, uppercase, letter-spaced, muted color for a technical look.
- Selects: custom chevron, matching input style.

### 4.4 Tables

- Row separators only, no vertical borders.
- Header: `--clr-surface-2` with muted uppercase labels.
- Hover: `--clr-surface-3`.
- Selected row: left accent border + subtle background tint.

### 4.5 Tabs

- Replace current underline tabs with pill-style or segment-control tabs.
- Active tab: filled surface, accent text, subtle shadow.
- Inactive: transparent, muted text.
- Transition: `150ms` ease on background and color.

### 4.6 Badges & Status

- Badges: small pills with left dot.
- Running status: pulsing green dot (CSS animation).
- Crashed: solid red with subtle red glow.
- Starting: pulsing amber dot.

### 4.7 Console

- Background: deep `#05060a` with a faint grid pattern or scanline texture.
- Text: bright green for info (`#4ade80`), amber warnings, red errors.
- Line highlight: left border color-coded by level.
- Input: fixed bottom, command prompt with `>` prefix.

### 4.8 Progress Bars

- Lower track: `--clr-surface-3`.
- Fill: gradient from accent to accent-hover.
- Add a subtle shimmer animation for indeterminate states.

---

## 5. Page-Level Redesign Plan

### 5.1 Login Page

- Centered card with a dark, atmospheric background.
- Large logo/title with the cyan accent.
- Minimal form, floating labels or clean top labels.
- Subtle background effect: faint radial gradient or animated noise.

### 5.2 Dashboard

- Hero section: system health summary (CPU, RAM, Disk) as large, glanceable stat cards.
- Server grid: more compact cards with:
  - Left status strip (color by status).
  - Server icon/type badge.
  - Name, status badge, address.
  - Mini resource sparklines or bars.
  - Quick action buttons (start/stop/restart) always visible, disabled when not applicable.
- Filter bar: integrated with the grid, maybe floating above.
- Empty state: illustration-friendly message with a clear CTA.

### 5.3 Server Detail

- Sticky header: server name, status badge, address copy, quick actions.
- Tabs: modern segment control, sticky below header on scroll.
- Console tab: full-height terminal feel, better command input, autocomplete popup styling.
- Overview tab: large resource charts, player count, recent events as a timeline.
- Settings: form in a clean grid, section dividers.
- Minecraft Config: two-column layout (property name + value) with raw editor toggle.
- Mods/Workshop: cards with preview images, clearer install/remove actions.
- Backups: timeline/list with size and restore action.
- Files: keep file manager but modernize icons, breadcrumbs, drag-and-drop zone.

### 5.4 Server Create

- Already moved to tabs in this iteration.
- Enhance with step-by-step feel for the create tab.
- Visual server-type cards (Minecraft Java, Bedrock, Steam) instead of a plain dropdown.
- Inline validation with clearer error presentation.
- Upload & Import tabs can be kept as-is but styled to match the new card system.

### 5.5 Settings & Administration Pages

- Consistent form layout, cards per section.
- Toggle switches instead of raw checkboxes.
- Better grouping with section headers and descriptions.
- Save-bar that appears at the bottom when changes are made.

---

## 6. Micro-interactions & Motion

### 6.1 Global

- Smooth theme transition (dark/light toggle) with `transition` on color/background variables.
- Subtle page fade-in on navigation.
- Staggered card entrance on dashboard load.

### 6.2 Status

- Pulsing status dot for running/starting servers.
- Glow transition when a server changes state (e.g., stopped -> running).

### 6.3 Hover / Focus

- Buttons lift slightly and glow on hover.
- Cards brighten their border on hover.
- Inputs glow on focus.
- Focus rings are visible and accented.

### 6.4 Feedback

- Toast notifications for actions (success, error, info) instead of full-page reloads where possible.
- Loading skeletons for async data.
- Button loading state with spinner.

### 6.5 Console

- New log lines slide in from the top.
- Auto-scroll keeps the latest line visible.
- Filter toggles dim inactive levels.

---

## 7. Accessibility

- Maintain WCAG 2.1 AA contrast ratios for all text.
- Visible focus states on every interactive element.
- `prefers-reduced-motion` media query disables animations for users who need it.
- Proper heading hierarchy on every page.
- ARIA labels for icon-only buttons.

---

## 8. Technical Implementation Strategy

### 8.1 CSS Architecture

1. Keep Tailwind CSS as the build system.
2. Replace/extend the CSS variables in `base.html` with the new palette.
3. Add utility classes for the new components (e.g., `.card`, `.btn`, `.badge`, `.input-field`) in `base.html` or a separate `components.css`.
4. Update `tailwind.css` build if needed (re-run `npm run css:build`).
5. Add a small `animations.css` for global motion and reduced-motion support.

### 8.2 Template Refactoring

1. `base.html` — new variables, fonts, global components.
2. `dashboard.html` — restructure server grid, add stat cards.
3. `server_detail.html` — sticky header, new tabs, console redesign, timeline/events.
4. `server_create.html` — visual server-type selector, inline validation.
5. `servers.py` / other routers — may need to pass extra context (e.g., `server_type_color`).
6. `login.html`, `settings.html`, `users.html`, etc. — consistent cards and forms.

### 8.3 JavaScript Enhancements

- Toast notification system (add to base template).
- Improved console autocomplete and command history.
- Dashboard live resource updates with smoother transitions.
- Theme toggle stored in localStorage (already exists, polish it).

### 8.4 Font & Asset Updates

- Add new self-hosted fonts (e.g., `Satoshi`, `Geist`) to `app/static/fonts/` and `fonts.css`.
- Update `Dockerfile` / build to include fonts if they are downloaded.

---

## 9. Phased Roadmap

### Phase 1: Foundation
- Update CSS variables and global component classes in `base.html`.
- Add new fonts and animation utilities.
- Re-run Tailwind build and verify both themes.

### Phase 2: Core Pages
- Redesign login page.
- Redesign dashboard (server cards, stats, filters).
- Redesign server detail header and tab navigation.

### Phase 3: Detail Views
- Console redesign.
- Overview / charts / events timeline.
- Settings, config editor, mods/workshop pages.

### Phase 4: Polish
- Toast notifications.
- Loading states and skeletons.
- Accessibility audit and reduced-motion support.
- Cross-browser testing.

---

## 10. Deliverables for Review

- Updated `base.html` with new CSS variables and global styles.
- Component style guide (this document + live examples in templates).
- Redesigned dashboard and server detail pages.
- Optional: Figma/wireframe export or screenshots if needed.

---

## 11. Open Questions

1. Should the overhaul also introduce a **light-mode-first** or remain **dark-first**?
2. Should the upload form be inlined into the create page, or remain a separate page?
3. Should the console get a true full-screen terminal mode?
4. Are there any brand colors or logo constraints we must respect?

Once these questions are answered, the first phase can begin.
