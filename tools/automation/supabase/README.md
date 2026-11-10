# Supabase readiness candidate — 2026-11-10

This folder prepares a dedicated trading prediction store. **Nothing here has been applied to a hosted project.** It is not a trading engine, broker connection, or proof that automation is ready.

## Verified access and decision

Access is now verified to **SelfLearningAI** (`omavgimpnvrpuxqhfxpf`, ACTIVE_HEALTHY), in the SelfLearningAI organization (`oukmfzwhkgiabisneuyr`), region `us-east-2`. Read-only catalogs show an existing shared learning/NFL database with four public learning tables and 15 private journal tables. See the [dated access and isolation record](../../../docs/supabase-access-and-isolation-2026-11-10.md). It supersedes earlier permission-denial observations. No prediction/journal contents, Auth users or secrets were read. No hosted project, schema or setting was changed. The separate AYSO project remains untouched.

Recommend a new **liddar-trading** project in the same organization and region, initially for SHADOW/paper work. The owner must choose the organization before the Supabase cost/creation tools can be invoked. After that choice, obtain the current quote and free-project eligibility; no $0 guarantee or specific-organization creation authorization is recorded. This candidate remains unapplied and must not be used as an upgrade for the existing shared database.

## What is prepared

- `supabase/migrations/20261110210410_trade_bot_prediction_store.sql`: generated with Supabase CLI 3.1.0 `migration new`; candidate for an empty dedicated project only.
- `readiness.sql`: read-only catalog inspection. It does not read journal contents.
- `check_readiness.py`: GET-only REST probes with an explicit expected project reference. It never prints credentials or server response bodies and rejects redirects.
- `test/schema.test.mjs`: disposable PostgreSQL tests using PGlite 0.5.16.

The migration matches the public base fields in SelfLearning main `46be142de18823ec86be2e33d8a421ce272616a8` and the terminal main `6f4af5112c4a43247428f1ad64cd9ebdb5fa9602` contract. It extends that baseline contract with database-owned `received_at` timestamps and `selflearn_status()` required by the release branch's `SupabaseStore`. The inspected existing learning database already has these fields/function; this candidate is still only for a new empty project. The hosted Python store and generic journals still need a separate code extraction/review; this folder does not merge that release branch.

## Schema boundaries

`predictions` and `outcomes` preserve their existing JSON payload fields and integer Unix-second event times. Their receipt timestamps are assigned by PostgreSQL and excluded from service-role insert grants. They permit SELECT and column-scoped INSERT for the server role, with no UPDATE/DELETE/TRUNCATE grants, plus immutable triggers. RLS is enabled and both browser roles have no table permissions or policies. The foreign key supports the terminal's PostgREST `outcomes!inner(...)` join.

`registry` and `registry_scores` are compatible read-only shells. No hosted model promotion writer exists in main, so this candidate does not grant registry mutation. The status RPC is a stable **security invoker** function, callable only by the server role. A project administrator can alter schema or disable protections; append-only behavior is enforced against normal application roles, not a hostile administrator.

The migration refuses any existing public tables/views/sequences, an existing private helper schema, or an existing status function. It is **not** an in-place upgrade for the learning/NFL database. There is no destructive rollback script. A failure rolls back the transaction; an existing project needs a separately reviewed migration.

Receipt time proves when a record reached this database. It does not prove when a model observed inputs or generated the forecast. The terminal auth patch labels client-submitted ideas as `meta._terminal.provenance = unverified_import`; downstream evaluation must exclude these from verified forward evidence unless independently validated. This store contains no order/fill/position ledger or broker authority.

## Local validation

```sh
npm ci
npm test
python -m unittest discover -s test -p 'test_*.py'
```

The previously completed seven SQL tests and three checker tests cover RLS and grants, service inserts and joins, receipt timestamp ownership, denied browser access, immutability, duplicate/invalid records, foreign keys, safe refusal of an existing schema and read-only probe behavior. The SQL tests execute disposable PostgreSQL via PGlite. They were not rerun for the documentation-only reconnection update. Hosted trading-project PostgREST/Auth, backups and billing remain unverified; the existing learning project's region is now known.

## Read-only hosted checks for the selected trading project

Load the values in `.env.example` through a secure environment mechanism, then run:

```sh
python check_readiness.py --expected-ref THE_REVIEWED_PROJECT_REFERENCE
```

The checker verifies zero-row field projections, the PostgREST foreign-key join, receipt ordering, status RPC availability and anonymous denial. The status RPC uses GET; it must already be the reviewed stable function. No inserts or migration writes occur. A passing result does not prove authenticated-user denial, write grants, owner sign-in, migration history, backup suitability, billing, hosted Python integration or trade readiness. Inspect `readiness.sql` and perform a disposable project integration test before production enablement.

## Enablement order

1. Access and existing-project catalog inspection are complete. Obtain the owner's organization choice for the proposed separate `liddar-trading`, then verify its current tier, eligibility and cost before creation.
2. Review/apply an appropriate migration in a disposable project and verify PostgREST joins plus least-privilege behavior. This candidate is only for a fresh dedicated database.
3. Deploy the accompanying terminal owner-auth patch to the intended terminal project using Node 22, with a correctly provisioned owner account and matching public/server Auth configuration.
4. Verify anonymous and wrong-owner calls cannot reach storage or paid research APIs.
5. Only then supply the server database secret and research provider key, redeploy and run the end-to-end checks. Keep live broker authority disabled.

No automation should be described as ready until these hosted checks and the separate broker/execution work pass.

## Primary documentation consulted

- [Supabase API keys](https://supabase.com/docs/guides/getting-started/api-keys): modern `sb_secret_` keys belong only on secured backends; they are not bearer JWTs.
- [Securing the Data API](https://supabase.com/docs/guides/api/securing-your-api): grants and RLS are separate controls.
- [Supabase CLI v3](https://supabase.com/changelog/supabase-cli-v3): target behavior changed; hosted actions require explicit remote configuration.
- [Supabase password sign-in](https://supabase.com/docs/reference/javascript/auth-signinwithpassword): owner login uses the official client API.
