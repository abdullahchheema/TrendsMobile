# Trends Mobile ERP — Design System (source of truth)

Generated with the UI/UX Pro Max skill (`--design-system --persist`, dials: variance 3 · motion 4 · density 7), then **adapted to the owner's brief**:
the auto-picked palette (light, gold, serif) was replaced by a near-black charcoal theme with a single Covison-violet accent and Inter type.
Page-specific overrides, if any, live in `pages/<page>.md` and win over this file.

Code: `offline/static/css/` — `tokens.css` → `base.css` → `layout.css` → `components.css` → `pages.css`. Behaviour: `offline/static/ui.js`.
Contrast is verified by `python scripts/check_contrast.py` (WCAG AA: text ≥ 4.5:1, focus ring ≥ 3:1).

## Principles
1. Dark, quiet, precise: charcoal surfaces, hairline borders, restrained glass only on shell chrome, cards and overlays.
2. One accent (violet) for primary action, active navigation and focus. Status colours (green/amber/red/blue) always come with text or an icon.
3. Density 7: compact tables and forms for shop staff who work all day; large touch targets (≥ 40 px) on POS and mobile.
4. Motion 4: 150–300 ms, purposeful (state changes, entry of content, dialogs). Everything honours `prefers-reduced-motion`.
5. Real data only. Empty states explain what to do next.

## Colour tokens (`tokens.css`)
| Role | Token | Value |
|---|---|---|
| Page | `--bg` / `--bg-2` | `#0b0c0f` / `#101217` |
| Surfaces | `--surface` / `-2` / `-3` | `#14171d` / `#1a1e26` / `#232835` |
| Borders | `--line` / `-2` / `-3` | white at 8% / 14% / 22% |
| Text | `--ink` / `-2` / `-3` | `#f2f4f8` / `#b9c0cd` / `#8f97a8` |
| Accent | `--accent` / `--accent-hover` / `--accent-text` | `#6a5df0` / `#5b4fe3` / `#a99fff` |
| Status | `--ok` `--warn` `--bad` `--info` | `#4ade80` `#fbbf24` `#f87171` `#7cb3ff` |

## Type, space, shape
Inter (body) + Inter Tight 500 (headings, big figures), self-hosted. Base 14 px/1.5; h1 26, h2 18, h3 15. Space scale 4·8·12·16·24·32·48. Radii 8 / 12 / 16 / pill.

## Components
Shell (header bar, glass sidebar with active pip, footer credit bar) · buttons (primary / default / danger / loading) · inputs with visible labels and inline `role=alert` errors · tables (sticky header, hover row, `.num` right-aligned, pagination) · badges · panels / stat cards / KPI cards · SVG chart with data-table fallback · toast (auto-hide; errors stay) · confirm dialog (`data-confirm`) · global search palette (`Ctrl/⌘ K` or `/`) · skeleton + spinner loading states · skip link · shortcuts `N` new sale, `B` buy stock.

## Rules to keep
- No raw hex in templates; use tokens. No emoji as icons (inline SVG only).
- Every interactive element: visible focus, `cursor:pointer`, hover feedback, ≥ 40 px hit area on touch layouts.
- Layouts verified at 375 / 768 / 1024 / 1440 with no horizontal page scroll (tables scroll inside `.table-scroll`).
- Receipts and printed reports stay black-on-white (`@media print` and the standalone receipt template).
