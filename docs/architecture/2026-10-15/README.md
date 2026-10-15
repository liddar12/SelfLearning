# SelfLearning architecture and delivery package

Revision 1, October 15, 2026, America/Chicago. The export timestamp appears on every visual page. Prepared for owner review and Claude Code implementation handoff.

Implementation status updated October 15, 2026, 5:44 p.m. CDT: use the
implementation handoff for later code/test evidence. The synchronized R1 PDF
and its editable visual source remain the design snapshot; they are not a
claim that proposed components are deployed.

Use the existing Vercel Python backend and Supabase project, preserve NFL2026's Netlify PWA, and build a durable shadow learning loop before activating any candidate. The Production operator token is already configured per the owner; no replacement token or new database setup is needed.

## Open these first

- [Architecture whiteboard PDF](SelfLearning_Architecture_R1.pdf): seven rendered pages covering boundaries, components, persistence, detailed pipelines, learning states, operator journeys, team, roadmap and release gates.
- [Technical plan](TECHNICAL_PLAN.md): current-versus-proposed state, credential boundaries, source/data authority, API contracts, schema model, risks and rollout.
- [Delivery backlog](BACKLOG.md): 12 epics, 31 features, 31 user stories, 93 tasks and 92 acceptance criteria, with effort/risk, owners and dependency mapping.
- [Spreadsheet-friendly backlog](BACKLOG.csv): one row per task, including parent story and acceptance/test references.
- [Implementation handoff](IMPLEMENTATION_HANDOFF.md): current branches, local changes, exact verification and next file-owned workstreams.

Editable visual source: [architecture.html](architecture.html), [render.mjs](render.mjs), [portable PDF exporter](export-vector-pdf.mjs). Editable backlog source: [backlog-source.mjs](backlog-source.mjs). These are repository documentation tools, not an NFL app build step.

## What the owner can do

No additional `SELFLEARN_OPERATOR_TOKEN` setup is needed. If convenient, confirm that Vercel's backend has `SUPABASE_URL` and either `SUPABASE_SECRET_KEY` or supported legacy `SUPABASE_SERVICE_ROLE_KEY`; they may already be configured. Do not copy secret values into chat or into the PWA.

The implementation team should perform the authenticated API/database test, reconcile migration history, create narrow pipeline credentials and prepare the reviewed release. A separate `SELFLEARN_NFL_INGEST_TOKEN` is needed for the Vercel verifier and NFL GitHub Actions when delivery is enabled; never reuse the operator token. The new journal selector is an explicit rollout switch, not a setting to enable before migration/release checks. The owner remains the authority for production migrations/deployment, changing the portfolio objective and model activation. The plan does not require buying another provider or enabling automatic promotion.

## Evidence and remaining implementation

Supabase project `omavgimpnvrpuxqhfxpf`, four RLS-enabled foundation tables, applied migration history and security-advisor findings were verified through project-scoped management MCP. The schema history mismatch and callable privileged helper need review; management connectivity is not working application ingestion.

Local isolated branches now include NFL generation receipt capture independent of MY display IDs, retry-safe export, hosted foundation reconciliation, scoped API intake, prospective adaptation, SQLite contracts and a Supabase journal/migration, immutable outcome revisions, typed offline shadow evaluation, and R130 reporting. Latest root checks: 511 core/hosted tests passed, all nine critical Python targets above 90% executable-line coverage; 16 PostgreSQL-WASM SQL tests and 8 actual adapter/API/adversarial SQL tests passed. Native concurrency and hosted Supabase remain unverified; see the handoff for exact scope. The NFL gate passed with 2,165 feature tests but skipped browser step 8. No production migration, export, two-cycle hosted learner, candidate activation or measured accuracy improvement is claimed.

The pilot is single-owner, not multi-tenant. Preview requires a separate staging database/project and separate credentials. Authoritative outcome capture/transport, publication observations, scheduled evaluation, persisted candidate return and independent NFL approval/consumption remain implementation work. The old settled and prospective adapters have different envelope identities; the implemented pure bridge joins the original source receipt to the exact persisted envelope.

The full historical R130 reference series remains unverified here because the shared NFL Git repository is shallow. Frozen Week 5 settlement is verified separately; the UI must show unavailable history rather than invented prospective counts.

## Rebuild and verify the planning artifacts

From the SelfLearning repository root:

```sh
node docs/architecture/2026-10-15/render.mjs
node docs/architecture/2026-10-15/export-vector-pdf.mjs
git diff --check
```

The first command validates unique backlog IDs, feature references, acceptance-to-test mapping and both dependency DAGs; it regenerates the Markdown, CSV, editable HTML and count manifest. The second creates a seven-page vector PDF from that HTML and, on macOS, renders every page through `sips` for inspection. It checks estimated text/page bounds and writes [render verification](render-verification.json). Preview PNGs are in [previews](previews/).

The optional Playwright PDF route was attempted but browser launch is blocked in this sandbox. No browser-layout acceptance is claimed. The delivered PDF uses the portable vector exporter and all seven actual page previews were inspected for clipping, legibility and arrow semantics. A reviewer identified an authentication-boundary arrow error; the source and export were corrected to route operator access through the API.

Acceptance test IDs in the backlog describe required future tests. They are not proof those tests already exist. The plan uses current Supabase authorization guidance and Vercel's existing FastAPI support; it does not introduce another framework or hosting service.
