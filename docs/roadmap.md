# Build & Deploy Roadmap

Sequenced plan reconciling the 7 gates (`power2026-build.md` §7) with the E1–E7
epics (`delivery-plan.md`) against **actual code state**. Two tracks — **Build**
(what to write) and **Deploy** (what goes live) — run in parallel. Legend:
✅ done · 🟡 partial · ⬜ not started · 🔒 blocked on Jimmy.

Repos: **liddar12/SelfLearning** (Python spine + research + execution-core) ·
**liddar12/liddar-terminal** (Vite/React + Vercel app).

> **Sports adapters:** the plan for putting sports prediction markets (NFL2026 first,
> WC2026 second) on this spine lives in [`sports-roadmap.md`](./sports-roadmap.md).

---

## 0. Where we are (verified)

**Done and deployed**
- `selflearn-core` spine ✅ — `Prediction`/`Outcome`/`Score` types, `SqliteStore`
  (CRUD + schema), horizons, `recommend.py`, walk-forward backtest (`signals`,
  `walkforward`, `score_resolved` with Wilson CI), feature layer (taxonomy +
  no-lookahead validator + assembler + `PriceActionPack` + `macro_policy` +
  `commodities` + in-memory point-in-time source), `Level0` updater.
- **Data contract** ✅ — `contracts.py` + `schemas/prediction_record.schema.json`
  (the breadth regime stamp seam between terminal and scorer).
- `execution-core` foundation ✅ — `Broker` port, `SimBroker`, pure `risk.check`,
  idempotent `OMS`, broker adapters (per `delivery-plan.md` E3).
- **Terminal** 🟡 — deployed on Vercel (Vite + Recharts), **Breadth feature live**,
  AI Calls/Puts scaffold, CI + Dependabot on both repos, weekly build Routine.

**Stubbed, gate-labeled (no silent stubs)**
- General scorer `scoring/metrics.py` + `scoring/calibration.py` → **Gate 3**.
- `registry/registry.py` bodies → **Gate 3**.
- Updater L1–L4 (`updater/policy.py`) → **Gate 3+**.
- `ScannerAdapter` → **Gate 4**. `SchwabBarSource` / broker → **Gate 5**.
- `geopolitics` / `ai_infra` feature packs, FRED/EIA point-in-time sources → **Gate 3+**.

---

## 1. Immediate next — buildable now (no external keys)

These have **no dependency on Jimmy** and unlock the most value. Recommended order:

### N1. General scorer + calibration (Gate 3, epic E1/E2 finish)
Implement `scoring/metrics.py` (per-task hit-rate, Brier, calibration curve,
MAE/MAPE/sign/R² for backtests, rolling + by cohort) and `scoring/calibration.py`
(Platt/isotonic, the L1 updater). Pure Python + the `scoring` extra
(numpy/scikit-learn) — already declared in `pyproject`. Small-sample honesty
(CIs) already exists in the backtest scorer; generalize it.
**Done =** scorer returns `Score` objects with `n`+CI over resolved predictions;
`registry` can promote/roll back a config by live score; tests green.

### N2. Terminal read-API (epic E5 Story 5.1)
Add Vercel serverless functions to `liddar-terminal` — `/api/scores`,
`/api/configs`, `/api/predictions` — that read the store and feed terminal views.
The terminal never imports Python; it reads JSON. **Done =** a Scores tab renders
live hit-rate + CI per scanner from real logged data.

### N3. Scanner logging (Gate 4 write-side)
Wire the terminal's AI Calls/Puts to **emit prediction records** (using the data
contract, incl. the breadth stamp) at idea time, and implement `ScannerAdapter`
to resolve each against the realized move over its horizon. This is the moment
"self-learning" actually turns on for the live product.
**Requires the shared store (M1 below)** — a Vercel writer + a Python reader can't
share SQLite. → triggers the Supabase deploy.

---

## 2. Blocked on Jimmy (critical-path unblocks) 🔒

| Need | Unblocks | Default rec |
|---|---|---|
| Confirm decisions A–D | Everything (naming, options-first, sim→approve→auto, build order) | Proceed as written |
| **EIA API key** + **gridstatus.io** tier + priority ISO | Gate 2 (H1 live), Gate 3 (H2–H4) | H1 in **ERCOT or CAISO** |
| Network **egress** for market-data hosts | Power research live data | — |
| Hosting decision: **Supabase** for shared store | Gate 4 live logging, read-API on real data | Supabase Postgres |
| **Schwab developer app** (OAuth id/secret, redirect), account type, options approval | Gate 5–7 execution | — |
| TLH tax context (taxable? cost-basis method? state?) | Gate 6 TLH engine | spec-ID lots |
| Min resolved-sample threshold L0→L1 | Updater autonomy | 30 |

---

## 3. Gate sequence (build track)

- **Gate 2 — data + first signal** 🟡🔒 · epic E2. EIA gas + one ISO DA LMP flowing;
  H1 walk-forward on **real** data (logic ✅, data blocked); predictions logging at L0.
- **Gate 3 — full backtest set + scoring** ⬜ · E1/E2. Scorer + calibration live (N1);
  H2–H4 with acceptance checks + write-ups.
- **Gate 4 — scanner integration** ⬜ · E5. `ScannerAdapter` piping real Liddar picks
  in (N3); terminal reads live hit-rates (N2).
- **Gate 5 — execution read-only** ⬜🔒 · E4. Schwab OAuth + positions/balances/orders
  read + reconciliation loop. No orders.
- **Gate 6 — execution simulated** ⬜ · E4/E3. Signals through the OMS in sim
  (`SimBroker` ✅); TLH engine proposing harvests; full risk gate enforced.
- **Gate 7 — execution human-approved live** ⬜🔒 · E4. Real orders, one-by-one
  approval, hard limits, kill switch. Fully-auto is a separate later gate.

---

## 4. Deploy track (milestones)

- **M0 — now** ✅ — terminal on Vercel; SelfLearning as a CI-tested library
  (pytest + walk-forward smoke on every push/PR).
- **M1 — shared store** 🔒 — provision **Supabase Postgres** as the prediction/outcome
  store. Prereq for Gate 4 live logging and the read-API on real data (Vercel
  writer + Python reader can't share a file). Add a `SupabaseStore` behind the
  existing `StorageBackend` port (store is already swappable).
- **M2 — terminal read-API** — deploy `/api/scores|configs|predictions` (Vercel
  functions reading Supabase). Terminal shows live scores.
- **M3 — always-on execution engine** 🔒 · epic E6 — containerize the engine
  (`Dockerfile` scaffolded) and deploy to a **persistent host (Fly/Railway/Render)**,
  not Vercel serverless — it must hold the Schwab streaming socket and run the
  reconciliation loop. Secrets via env; tokens refresh; restarts clean.
- **M4 — packaging** · E7 — publish `selflearn-core` / `signals` as standalone,
  zero-dep packages once the API stabilizes.

**Deploy rule:** nothing trades real money before the risk gate + TLH (E4.3) and
the always-on engine (M3) are green **and** a signal has a verified, net-of-cost
edge.

---

## 5. Critical path (one line)

N1 scorer → **Supabase (M1)** → N3 scanner logging (Gate 4) + N2 read-API (M2) →
[unblock EIA/ISO] Gate 2–3 live → [unblock Schwab] Gate 5 → 6 → 7 + M3 engine.

The weekly build Routine advances the **unblocked** items (N1/N2 and small
increments) via draft PRs; the 🔒 items wait on the table in §2.
