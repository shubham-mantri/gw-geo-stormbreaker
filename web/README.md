# web — gw-geo dashboard

The end-user dashboard for GEO / AI-search visibility, attribution & execution
(ui-spec §1/§2). Standalone Next.js app; talks to the backend REST API over HTTP
only (ui-spec §6 / TRD §11).

## Stack

Next.js (App Router) · TypeScript · Tailwind + shadcn/ui · TanStack Query ·
Recharts · Vitest + Testing Library (unit) · Playwright (e2e).

## Commands

```bash
npm install       # install deps (creates node_modules/, gitignored)
npm run dev       # dev server on :3000
npm run build     # production build
npm run test      # Vitest unit tests
npm run e2e       # Playwright e2e (boots its own dev server on :4488)
npm run lint      # eslint (next lint)
npm run typecheck # tsc --noEmit
```

Playwright needs a browser once: `npx playwright install chromium`.

## Conventions for screen work (M2-T18…T20)

Everything shared is already here — **screens only add
`app/(app)/<screen>/page.tsx`** and should not edit shared files:

- **API** — `lib/api.ts` `apiClient(getToken?)` covers every ui-spec §6 endpoint
  (brands, overview, visibility, sources, pipeline, alerts, prompts, createBrand,
  savePrompts, connectIntegration, leadCaptureSnippet). It sends
  `Authorization: Bearer <token>` and redirects to `/login` on 401. Use it via
  TanStack Query inside a screen.
- **Types** — `lib/types.ts` mirrors the contract shapes (snake_case, as on the
  wire). Import these; don't redeclare.
- **Auth / tenancy** — `lib/auth.ts`. Tenant comes from the token and is
  **read-only** on the client (ui-spec §5); there is no tenant selector.
- **UI kit** — shadcn/ui primitives in `components/ui/*` (button, input, label,
  card, badge, select, table, skeleton) plus `ConfidenceBadge` (always show CI +
  `n` — ui-spec §4). Add more primitives with `npx shadcn add <name>`.
- **Shell** — `app/(app)/layout.tsx` provides the auth guard + `Sidebar`
  (Overview/Visibility/Sources/Pipeline/Alerts/Settings) + `TopBar` (brand
  switcher, date range, engine filter). Import alias `@/*` maps to the web root.

## Backend URL

`NEXT_PUBLIC_API_URL` sets the API base. **Empty (default) = same-origin**, which
is what the e2e relies on (it mocks `/auth/login` on the app's own origin). Point
it at the backend (e.g. `http://localhost:8000`) — or configure a same-origin
proxy/rewrite — for live data.

## CI

`.github/workflows/web-ci.yml` runs on changes under `web/`:
**install → lint → typecheck → vitest → playwright**.
