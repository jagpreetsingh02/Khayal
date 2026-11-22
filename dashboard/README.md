# Care Circle — family dashboard

React + Vite + TypeScript + Recharts. Mobile first (children mostly use phones). Talks to the
dashboard API in `backend/app/api/dashboard.py`; polls every 4 s so alerts and approvals show
up live; all times are shown in Asia/Kolkata.

```bash
npm install
npm run build    # type-check + production build -> dist/, served by FastAPI at /dashboard/
npm run dev      # http://localhost:5173/dashboard/ with hot reload; /api is proxied to :8000
npm run lint     # oxlint
```

Sign in with `DASHBOARD_DEMO_TOKEN` (default `change-me` in mock mode) and pick a family
member. Only the parent can change what is shared; only a family member can approve a refill.

Layout: `src/api.ts` (typed client), `src/hooks.ts` (polling), `src/time.ts` (IST formatting),
`src/components/` (one file per section; charts in `Trends.tsx`, loaded on demand).
