# NFL public-pool evidence — local checkpoint

Revision 2, November 9, 2026, 7:44 p.m. CST (America/Chicago).

Owner: Codex in the current session. No assignments or handoffs to Claude or
Cursor. Henry/Grok may monitor deployment and intake evidence after its service
connections are verified. This checkpoint is local, default-disabled and
unpublished. It does not activate learned predictions.

## Problem and scope

M1 intake stores immutable `generated_unverified` forecasts. Previously the
corrections fitter expected `verified_publication`, but there was no supported
way to establish that fact without changing the original receipt identity.
M1 features also omit the selection field expected by that fitter. The original
NFL source receipt already carries the supported `over` direction.

This change joins a separate server-observed public-pool attestation to the
existing generation, source receipt and generic receipt. The fitter obtains
direction from that original source. It never edits the original receipt,
backdates an observation, reconstructs an old publication, or changes equations.

The observation proves that one exact pool was retrievable anonymously over
HTTPS from an explicitly configured origin. It does not prove that every rung
was displayed, selected or placed as a bet. Pool-level evaluation must remain
separate from as-offered card accuracy. Provider truth, browser display,
sportsbook settlement, candidate evaluation and adoption are separate evidence.

## Request and persistence

`POST /api/v1/nfl/publication-attestations` accepts only:

```json
{"version": 1, "object_id": "sha256:<finalized-generation-object-id>"}
```

The request uses the separate server-side `SELFLEARN_NFL_PUBLICATION_TOKEN`.
It cannot supply a URL, body, publication status, timestamp or forecast IDs.
This token cannot ingest forecasts, submit outcomes or operate the owner API.
Missing or colliding credentials fail closed. Never put credentials in a
browser, a source file, a task message or an evidence report.

The handler first recovers any original durable result. Otherwise, it fetches
`/data/leg_pool.json` from the configured origin without authentication, cookies,
proxy inheritance or redirects. It requires HTTP 200, JSON content, UTF-8 and
at most 3 MiB. Invalid, duplicated, deeply nested or non-finite JSON is refused.
The socket timeout is 12 seconds and the cooperative observation budget is 20
seconds; the hosting platform must enforce a hard request deadline as well.

The body must match the frozen source pool's canonical digest. Its exact raw
bytes receive a separate digest and are retained privately with the observation.
The finalized generation ACK determines which source receipts were accepted.
Each accepted generic receipt must still match the original source and kickoff.

The database stamps its own time after obtaining the writer lock. HTTP
completion and the database recording timestamp must both precede kickoff.
Late members remain explicit rejections. This timestamp is not a claim about
the eventual transaction commit instant. Evidence and the compact response are
stored atomically. Retries recover the original result even after kickoff;
they do not create a new observation or a second training sample.

Generic journals remain domain-agnostic. Applications explicitly compose:

- `adapters.nfl_publication_journal.NFLPublicationJournal` for local SQLite;
- `adapters.nfl_publication_journal.NFLSupabasePublicationJournal` for the hosted
  journal port.

The SQLite implementation is an explicit local test/operator choice, never a
production fallback. Supabase uses the existing journal writer lock and sequence.
The private publication table has no direct service-role insertion grant; a
service-only RPC enforces the write contract. Ordinary users have no access.

## Fitting and operator visibility

`read_publication_fit_snapshot()` pins receipts, generations, outcome revisions
and attestations to one database snapshot/fence. The fitter does not mix fresh
reads from different times. It joins only evidence known before the training
cutoff, resolves the latest eligible outcome revision and excludes unresolved
or void outcomes. An attested forecast remains ineligible until an outcome is
available. Several attestations for the same original receipt cannot increase
its sample count. Repeated forecast vintages for the same contract/event are
also reduced to the earliest eligible forecast, ordered by forecast creation,
first-seen time and receipt ID, before joining outcomes. An earlier unresolved
forecast cannot be replaced by a later winning vintage. Distinct thresholds
can still be correlated; sample floors are not a claim of independent trials.

The fit input hash includes receipt, source, generation, pool, outcome revision,
attestation and policy identities as well as numerical inputs. The trusted
artifact source is now `receipt_journal_publication_as_of_v2`. Legacy source
artifacts and a caller-provided `verified_publication` enum do not qualify.
Runtime artifact authorization replays the same binder. Existing sample floors,
caps, expiry behavior and baseline fallback remain in place.

The authenticated forecast trace can show `public_pool_availability=attested`
and compact proof IDs when publication recording is configured. Its existing
forecast-display and model-adoption fields remain unverified. Raw public-pool
bodies are not included in the trace or ACK.

## Configuration and release order

No settings or migrations were applied to a hosted service during this work.
Before a recording-only release:

1. Review this candidate and pass the dedicated fresh PostgreSQL 17 CI gate.
   Local scripted adapters or PGlite execution do not replace native concurrent
   transactions or hosted PostgREST verification.
2. Reconcile the deployed migration history. The new CLI-generated file is
   `supabase/migrations/20261110011758_publication_attestations_v1.sql` and depends
   on the journal and observed-outcome migrations. Do not reapply prior privilege
   changes merely because an older branch contains them.
3. Verify the actual NFL production origin. There is **no default domain**.
   The previously rejected custom domain must not be reused as an assumption.
4. In the approved server environment configure the distinct publication token,
   `SELFLEARN_NFL_PUBLICATION_ORIGIN=https://<verified-origin>` and
   `SELFLEARN_PUBLICATION_MODE=record`. The explicit Supabase journal backend and
   its existing credentials are still required. Keep corrections mode off.
5. Deploy the tested exact engine head only after release authorization. Check
   private database readiness, denied public access and an actual hosted round
   trip. A successful Vercel build alone is insufficient.
6. Schedule an idempotent request only after the forecast generation ACK and
   the matching public NFL deployment. If the public pool has already changed,
   retain the mismatch and capture the next genuine generation; do not publish
   an old file to manufacture a historical training sample.
7. Verify persisted original IDs, raw evidence, duplicate/restart behavior and
   the pre-kickoff cutoff. Later join authoritative outcomes and evaluate a
   frozen candidate on unseen graded periods before separate adoption.

The producer publication outbox, cloud schedule and actual hosted round trip
remain unfinished. Existing forecast/outcome exporters are reusable, but they
do not already perform this new request.

## Verification limits and rollback

See the [local evidence report](reviews/NFL_PUBLICATION_EVIDENCE_20261109.json)
for exact commands, checkout identities and counts. Source `c59c14d1` passes
1,600 core/hosted tests with no skips. The sequential SQL proof passed for 100
and 1,000 synthetic rungs; the latter publication RPC took 6.35 seconds in
PGlite, with 16.04 seconds for the full SQL sequence. These are local measurements,
not a hosted latency guarantee. Tests use explicit synthetic clocks and injected HTTP bodies; they
are not historical publication evidence. Native PostgreSQL concurrency and live
Supabase/Vercel behavior must not be marked passed from these local tests.

Recording can be disabled by removing `SELFLEARN_PUBLICATION_MODE=record` and
redeploying the server. Keep immutable records; do not delete journal history.
This is a deploy-based stop, not an immediate kill switch. No automatic model
promotion is added. Previously served NFL static files require their own rebuild
and deployment to change; disabling engine recording does not rewrite them.
