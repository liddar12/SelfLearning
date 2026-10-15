# Receipt journal PostgreSQL checks

These tests execute the actual receipt-journal migration in an in-memory
PostgreSQL WASM engine, not a SQL mock or SQLite substitution. They do not start
a network service, download dependencies, access secrets, or contact Supabase.

Supply an already installed, reviewed `@electric-sql/pglite` module explicitly:

```sh
SELFLEARN_PGLITE_MODULE=/path/to/@electric-sql/pglite/dist/index.js \
  node --test tests/postgres/journal.test.mjs
```

The Node suite also runs the system `python3` as an independent standard-library
JSON/hash oracle. Set `SELFLEARN_TEST_PYTHON` to use another installed Python.
The oracle exercises seeded IEEE float cases, Python's fixed/scientific
thresholds, negative zero, large integers, Unicode and unsupported surrogate/NUL
quarantines. No package installation occurs.

Missing configuration or empty SQL is an error, not a skipped or passing test.
The suite creates test-only `anon`, `authenticated`, and BYPASSRLS
`service_role` roles, applies the migration, then exercises RPCs under their
actual PostgreSQL roles. It checks payload identity, idempotence, atomic
generation partitions, receipt-source conflicts, append-only correction/VOID
history, database-owned clocks, privilege denial and bounded watermark reads.

The real Python adapter can be exercised through the same SQL using an installed
test environment containing pytest:

```sh
SELFLEARN_PGLITE_MODULE=/path/to/@electric-sql/pglite/dist/index.js \
  PYTHONPATH=packages/selflearn-core \
  python -m pytest -q tests/postgres/test_adapter_integration.py
```

`rpc_bridge.mjs` is a test-only JSON-lines subprocess transport: each Python
adapter call runs the corresponding real SQL RPC as `service_role`. It is not a
fake success transport or an HTTP server.

To measure the unchanged real NFL producer population and its database batch,
run the opt-in read-only volume check:

```sh
SELFLEARN_PGLITE_MODULE=/path/to/@electric-sql/pglite/dist/index.js \
  NFL2026_CHECKOUT=/path/to/the/isolated/NFL2026-checkout \
  PYTHONPATH=packages/selflearn-core \
  python tests/postgres/benchmark_current_pool.py
```

The benchmark rebuilds the existing producer and selector in memory at the
frozen pool timestamp, captures all eligible receipts, persists source objects
and a generation, retries it, then reads a fenced evaluation snapshot. It checks
the actual offered-card cohort, consumes no outcomes, fits no model and verifies
source-file hashes are unchanged. JSON output reports individual RPC timings
and request/response sizes; exit status is nonzero if a local RPC reaches the
production adapter's 12-second timeout or its response exceeds 8 MiB. Passing is
only a local timing observation, not a hosted latency or capacity guarantee.

PGlite is single-user/single-connection. These checks cannot prove native
PostgreSQL concurrent transaction locking, PostgREST transport, deployed
Supabase grants, RLS event-trigger behavior, backups, or production durability.
Native PostgreSQL tests with separate connections and a disposable staging
Supabase round trip remain release requirements. Do not report these tests as
proof that the production learning pipeline is connected or model adoption is
enabled.

## Native transaction gate

`test_native_concurrency.py` uses only Python's standard library and an installed
`psql` client. CI provisions a disposable PostgreSQL 17 service; the test never
installs software, starts a database or contacts the cloud itself. For an
already-running **fresh disposable** local test database:

```sh
SELFLEARN_NATIVE_PG_DSN=postgresql://postgres:TEST_ONLY_PASSWORD@127.0.0.1:5432/selflearn_journal_test \
  SELFLEARN_REQUIRE_NATIVE_PG=1 \
  python -m unittest discover -s tests/postgres -p test_native_concurrency.py -v
```

Only `localhost`, `127.0.0.1` or `::1`, the `postgres` fixture administrator and
the exact database name `selflearn_journal_test` are accepted. URI connection
overrides are rejected and ambient PostgreSQL connection settings cannot
redirect a test. The URI/password are never printed or placed in process
arguments. The gate refuses an existing `selflearn_private` schema and does not
alter existing roles to make a test pass. Use a fresh disposable database for
each complete run; production migration history is not changed.

The loopback requirement applies to the **client destination** (`PGHOSTADDR`).
A Docker service published on the runner's `127.0.0.1` can report a private
`172.x` address from PostgreSQL's `inet_server_addr()` inside its container;
that server-side address is not incorrectly treated as the client target.
Offline guard tests cover this CI topology while retaining the database-name,
PostgreSQL-17 and fresh-schema checks. Those guard tests are not native database
execution evidence.

The native tests apply only the new receipt-journal migration. Separate live
connections verify competing source-receipt writes, exact retries, competing
correction heads, generation rollback and read-watermark stability while an
earlier insertion is still uncommitted. JSON result barriers prove the first
write has executed; PostgreSQL's actual blocker metadata proves the competitor
is waiting before the first writer is released. There is no blind timing-based
claim of concurrency. Statements, lock waits and idle transactions are bounded.

Without an explicit DSN, native tests report a skip and produce **no native
concurrency evidence**. With `SELFLEARN_REQUIRE_NATIVE_PG=1` (set in CI), absent
configuration, missing clients or unavailable databases fail the gate. This
does not replace a hosted Supabase/PostgREST staging round trip, security-advisor
review, backup/recovery checks or owner release approval.

The fixture serializer intentionally uses a restricted number vocabulary that
matches Python canonical JSON bytes. It must not be reused as a general
cross-language canonicalization library; production persistence preserves the
producer's original canonical JSON text.
