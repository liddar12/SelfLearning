# Trading isolation inside the shared Supabase project

This additive setup implements the owner's decision to share the existing
SelfLearningAI project with NFL. It creates trading-only storage and RPCs;
it does not copy, alter or grant new access to existing NFL records. The
older `../supabase` candidate remains a fresh-project-only option and must
not be applied to the shared database.

## Authentication and permissions

- `trading` holds predictions, outcomes and read-only registry shells.
- `trading_private.principals` is an operator-managed allowlist of verified
  Supabase Auth user UUIDs. It starts empty, and enrollment defaults disabled.
- Requests use the project's publishable/legacy anon API key plus the caller's
  actual Supabase Auth bearer token. Database policies evaluate `auth.uid()`.
- Enabled `owner` principals can read trading evidence and import predictions.
  Every owner import receives database-enforced `unverified_import` provenance.
- Enabled `worker` principals can read and append predictions and outcomes.
  Their records are labeled `worker_submitted`, which is not proof of verified
  forward generation or accurate outcome resolution.
- Neither principal kind can edit/delete/truncate evidence or promote registry
  versions. The application cannot enroll principals or change its own kind.
- `anon`, `PUBLIC` and `service_role` receive no access to the new tables/RPCs.
  The elevated project service key is deliberately not used by these clients.
- All functions are security invokers with an explicit `pg_catalog` search
  path. Existing Auth settings and existing NFL table/function grants are
  unchanged. The migration checks unsafe role inheritance and effective
  privileges and aborts atomically if those prevent isolation.

The private principal UUID has no foreign key to `auth.users`. Operators must
verify the actual Auth identity before enabling it. This avoids modifying Auth
objects and permits rollback-only role tests without creating user accounts.
Disable the principal first when revoking access; deleting an Auth account
alone does not necessarily invalidate a previously issued token immediately.
No password, refresh token, signing secret or API secret belongs in this folder.

## RPC contract: `shared-trading-v1`

The RPCs live in the already exposed `public` schema, with explicit execute
grants. Their data tables stay in separate, unexposed schemas. No global Data
API schema setting needs to be changed. Each RPC checks enrollment, including
empty-list reads, so an unlisted user receives an authorization error.

| RPC | Input | Result |
| --- | --- | --- |
| `trade_bot_status` | None | `{schema_version, server_time, principal_kind}` |
| `trade_bot_append_prediction` | `p_record`: existing Prediction JSON fields | Inserted prediction ID string |
| `trade_bot_attach_outcome` | `p_record`: existing Outcome JSON fields; worker only | Prediction ID string |
| `trade_bot_list_predictions` | Shared list parameters below | JSON array of prediction rows |
| `trade_bot_list_resolved` | Shared list parameters below | JSON array of predictions with exactly one joined `outcomes` item |
| `trade_bot_get_prediction` | `p_id` | Prediction row or null |
| `trade_bot_list_unresolved` | Required `p_task`, `p_now_ts`, plus remaining list parameters | JSON array whose horizon elapsed and no outcome existed by the receipt cutoff |

Shared list parameters:

- `p_task`: optional task filter, except unresolved scans require a task.
- `p_limit`: integer from 1 to 500, default 100.
- `p_before_received_at` and `p_before_id`: both absent or both present.
- `p_as_of`: finite database receipt cutoff, default statement time. A client
  captures `trade_bot_status.server_time` once and supplies it to every page.

Rows are ordered by `received_at DESC, id COLLATE "C" DESC`. Reuse the exact
last-row receipt string and ID as the next cursor; preserve timestamp
microseconds. JavaScript ID comparisons must use UTF-8 byte order if checking
ordering. Resolved scans apply the cutoff to both prediction and outcome
receipts. These separate HTTP requests share a receipt cutoff, **not an
exported PostgreSQL MVCC snapshot**. A transaction that stamps a record before
the cutoff but commits later may affect a later scan. Freeze an evaluated
dataset explicitly when reproducibility across concurrent writers is needed.

Allowed tasks are exactly `scanner`, `trade_decision`, `price_action_trend`,
or `trading.` followed by a lowercase letter and zero or more lowercase
letters, digits, underscores, periods or hyphens. Total length is at most 200;
whitespace and NFL/other task namespaces are rejected.

Prediction/outcome `received_at` and `actor_user_id` are database-owned.
Unknown fields, forged receipt fields, malformed JSON shapes, out-of-range
values, oversized records and invalid cursors fail explicitly. Outcome IDs
reference only `trading.predictions`; a matching NFL ID cannot satisfy the FK.
The migration has no order, position, broker, scheduling or autonomy API.

## Validation and migration

The migration filename was generated with the pinned Supabase CLI:

`supabase/migrations/20261110232506_shared_trading_isolation.sql`

It refuses pre-existing `trading`/`trading_private` schemas or public
`trade_bot_*` functions. Do not remove that guard to retry a deployment. First
inspect migration history and the actual catalog. There is no destructive
rollback script; rolling back a release must preserve recorded evidence.

Run the disposable PostgreSQL tests with:

```sh
npm ci --ignore-scripts --no-audit --no-fund
npm test
```

PGlite is pinned at 0.5.16. The suite seeds an NFL-shaped existing database and
checks schema/data/grant preservation, all principal boundaries, SQL-enforced
provenance, receipt/actor ownership, append-only behavior, cross-domain denial,
input validation, keyset pagination, receipt cutoffs, disable/reapply behavior
and atomic refusal of unsafe role inheritance/default grants. It does not
claim to verify hosted Auth sign-in, PostgREST transport or a running worker.

`readiness.sql` inspects only the new catalog, effective grants and enrollment
counts. It does not read prediction contents, private user details or secrets.
`verify-rollback.sql` is the initial hosted permission check, to run as one
administrator SQL request after migration and before actual enrollment. It
refuses nonempty trading tables, uses random synthetic principal UUIDs without
creating Auth users, checks real database roles and discovers the existing
NFL catalog, then rolls back all fixtures and confirms the five tables remain
empty. It outputs only summary booleans. If a check fails, issue `ROLLBACK`
before reusing that connection. Its same-request receipt cutoff intentionally
excludes newly stamped fixtures; positive list pagination is exercised by
the disposable suite using separate requests.
Root deployment verification should compare existing NFL catalog fingerprints
before/after and run rollback-only permission checks, then inspect Supabase
security advisors. Actual owner and worker sign-in remain separate checks.

## References

- [Supabase roles](https://supabase.com/docs/guides/database/postgres/roles)
- [Row-level security](https://supabase.com/docs/guides/database/postgres/row-level-security)
- [Database functions and security invoker](https://supabase.com/docs/guides/database/functions)
- [API key roles](https://supabase.com/docs/guides/api/api-keys)
