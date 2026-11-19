# NFL collection handoff

The collection handoff is disabled by default. It retains immutable producer
attempts in the same private Supabase project as the forecast journal. It has
no dependency on the baseline publisher and never changes serving state. No
hosted deployment, migration or schedule is part of this change.

The scoped routes require `SELFLEARN_NFL_COLLECTION_MODE=record`, a server-owned
lowercase `SELFLEARN_NFL_COLLECTION_REPOSITORY`, and a distinct server-only
`SELFLEARN_NFL_COLLECTION_TOKEN`. Uploading the original object bodies still
uses the existing forecast intake credential. The app validates its existing
Supabase journal connection before opening the collection port. SQLite is an
explicit test injection.

## Producer and recovery

1. `POST /api/v1/nfl/collection-intents` seals the original `generation_object_id`,
   `generation_id`, producer `recorded_utc`, `latest_kickoff_utc`, and three
   `source_snapshots` for a `workflow`, `run_id`, and `run_attempt`. The workflow
   is `daily.yml` or `gameday.yml`. Run numbers are decimal strings. Submit JSON
   `version: 1`. The server sets the repository and the original database
   `stored_utc` and returns the complete attempt view.
2. Use `/api/v1/nfl/objects` to write the exact three frozen source objects and
   generation envelope. Missing original bytes hold the attempt. If no copy
   of the original bytes reached durable storage, status cannot supply or
   recreate them. Staging an intent alone does not finalize the generation.
3. Read `GET /api/v1/nfl/collection-intents/{workflow}/{run_id}/{run_attempt}`
   on a fresh runner. `POST` to that URL plus `/recover` with `version: 1`
   and the original `intent_id` requests readback or finalization. The server
   joins the immutable graph and checks the exact capture clock, generation ID,
   sources and latest original receipt kickoff. It finalizes using the existing
   journal transaction, and preserves any existing original ACK on replay.
4. A trusted Actions publisher can `POST` to the same URL plus `/publisher`
   using `version: 1`, the original `intent_id`, `published_sha`,
   `published_pool_id`, and `previous_pool_id`. It must already have verified
   the Git publisher receipt for this run and attempt. A no-op uses null for
   all three publisher fields. The SHA and canonical pool digest must be present
   together for a published commit. A null previous pool is reserved for the
   first publication. The server derives match, mismatch, unchanged or no-op
   from the original intent and these fields. The independent public observer
   remains responsible for verifying public HTTP bytes before kickoff.

`GET /api/v1/nfl/collection-intents/pending` scans at most `limit + 1` scoped
rows, where `limit` is between 1 and 50 and defaults to 16. It reports unexpired
attempt locators with all expected object kinds present. A page can be empty
with a non-null `next_after`. Continue with `after=next_after` until the cursor
is null. Scans include already-finalized attempts so a restart can find their
original ACK and publisher receipt. When the publication route is configured,
the scan reads the pinned job's latest durable event and omits completed or
failed jobs. An unregistered stage falls out at kickoff. Registered jobs fall
out at their original job deadline. The list is a recovery hint. The individual
routes independently validate their exact records.

Status shows pending or held states explicitly. `publication_ready` requires
an exhaustive successful intake ACK and a matching fresh publisher receipt
before the latest staged receipt kickoff. It means eligible for the existing
publication job enqueue. The response always reports `publication_verified`
and `training_approved` false. If receipt delivery fails before the server
writes, the attempt stays awaiting publisher. A durable receipt whose reply
was lost can be read back by run and attempt. Reuse of a recorded attempt or
publisher receipt with different content returns 409.

## Publication job handoff

Use `GET` or `POST` on the attempt URL plus `/publication` with the collection
credential and the existing verified publication configuration. `GET` reads
only. `POST` takes `version: 1` and the original `intent_id`. For a new eligible
attempt the server first commits the matching publisher receipt identity,
original v2 declaration, and full job definition. It then enqueues that job
through the existing pinned registration. The database derives the cutoff
from the immutable intent. If the database stamps a fresh registration at or
after kickoff, the registration rolls back and the prepared definition remains.

If preparation or registration replies are lost, repeat using the same run
and attempt. An unregistered original declaration is held when code, policy
or authority changes. A previously registered job remains readable under its
original authority using the stored association. No new job identity replaces
it. A read can return awaiting publication, prepared, registered, a terminal
job, or a hold. Only the existing worker can observe the actual public pool.
The publication handoff reports verification only for successful jobs with
validated pre-kickoff proof. Collection and recording carry no authorization
to fit, adopt, or serve a correction. No schedule is installed.

Adding these routes changes the deployed publication worker's declared code
identity because that identity includes the web service sources. Release work
must pair the reviewed engine and NFL client and check previously queued jobs
against their original declarations. Pending discovery in this API does not
change job discovery, reassign old jobs, or authorize a new declaration.

## Local checks

The ASGI tests open the same SQLite database through independent application
instances. The native PostgreSQL test uses a fresh disposable loopback PG17
`selflearn_collection_test` database and the optional Psycopg driver. It runs the
production Supabase adapter through a local RPC transport to real SQL, including
rollback of a new registration whose actual DB clock crossed kickoff. Hosted
PostgREST routing, hosted timings and deployed credentials remain unverified.
