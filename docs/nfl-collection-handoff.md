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
is null. Scans intentionally include already-finalized attempts so a restart
can find their original ACK and publisher receipt. A later queue integration
can add publication-terminal linkage. The deadline and presence checks in
this list are hints. Recovery independently verifies the actual graph.

Status shows pending or held states explicitly. `publication_ready` requires
an exhaustive successful intake ACK and a matching fresh publisher receipt
before the latest staged receipt kickoff. It means eligible for the existing
publication job enqueue. The response always reports `publication_verified`
and `training_approved` false. The handoff does not enqueue, invoke a worker,
fetch public data, fit, or adopt anything. If receipt delivery fails before the
server writes, the attempt stays awaiting publisher. A durable receipt whose
reply was lost can be read back by run and attempt. Reuse of a recorded attempt
or publisher receipt with different content returns 409.

Adding these routes changes the deployed publication worker's declared code
identity because that identity includes the web service sources. Release work
must pair the reviewed engine and NFL client and check previously queued jobs
against their original declarations. Pending discovery in this API does not
change job discovery, reassign old jobs, or authorize a new declaration.

## Local checks

The ASGI tests open the same SQLite database through independent application
instances. The native PostgreSQL test uses a fresh disposable loopback PG17
`selflearn_collection_test` database and the optional Psycopg driver. It runs the
production Supabase adapter through a local RPC transport to real SQL. Hosted
PostgREST routing, hosted timings and deployed credentials remain unverified.
