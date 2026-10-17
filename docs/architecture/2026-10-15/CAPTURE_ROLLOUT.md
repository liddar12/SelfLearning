# Capture rollout: owner decisions and automated work

Local checkpoint `a5c4707`, October 15, 2026, 11:43 p.m. CDT
(America/Chicago). **Procedure only: nothing below has been applied to cloud
resources.** The current receiver handles complete captures up to 500 members;
full-slate multi-batch execution is still being built. Do not enable this
checkpoint as a complete automatic learning service.

## What needs the owner

1. Approve an isolated staging resource if there is no suitable existing one.
   Never point a Preview deployment at the production journal. A new resource
   or paid plan is a separate owner choice, not an automatic purchase.
2. Approve the reconciled exact-SHA production release and database migration
   after staging/native/browser gates pass. Existing Supabase history needs
   reconciliation; do not blindly run every local migration on production.
3. Approve later portfolio-policy changes or model activation separately.
   A working pipeline or stored candidate is not permission to adopt a model.

Everything else below is implementation-team work that can be automated when
authorized access is available. No recurring weekly manual SQL is intended.
An MCP permission block must be resolved by the owner, not worked around with
another transport. Do not paste credentials into chat.

## Release sequence for the implementation team

1. Refresh both GitHub main branches read-only and record the exact source,
   data/history, contract versions and intended deployment SHAs. Preserve
   concurrent Claude/pipeline changes. The latest local runtime is not
   automatically current main or production.
2. In staging, inspect applied migration history, helper permissions and
   backup/restore capability. Apply only the reviewed reconciled additive
   migration set. Run actual native multi-session isolation/concurrency and
   authenticated PostgREST grant tests; PostgreSQL-WASM does not satisfy these.
3. Provision a private evidence bucket with an explicit retention/restore
   policy. Private readback checks and content hashes are not WORM storage or
   provider authentication. Never use a public bucket or signed public URL.
4. Compute the code-owned namespace from `capture_configuration.py` for the
   approved destination/producer. Administrator-provision only its bounded
   `capture_staging_limits` row; application service credentials cannot create
   or change limits. Choose byte/chunk budgets explicitly. Reservations are
   all-time and failed/orphaned uploads remain charged. There is currently no
   automatic quota reset, refund or garbage collector.
5. While provisioning/changing the exact namespace, quiesce its producer and
   acquire transaction advisory lock `(619044, 3)` in the administrator's
   transaction. Limits updates do not automatically acquire that lock, so do
   not claim instant concurrent revocation without this procedure.
6. Configure the backend environment privately using the matrix below. Keep
   selectors disabled until the schema, quota, bucket and credentials are
   verified. The owner token is already configured per the owner; do not
   regenerate it merely because the new code exists.
7. Deploy the reviewed candidate to isolated staging. Configuration selection
   itself must perform no HTTP. Authorized capture operations check journal
   and original-result read RPCs; quota reservation remains mandatory before
   every Storage write. An owner-only status read reports configuration, not
   successful outcome intake or model adoption.
8. With separately authorized synthetic staging writes, verify the complete
   producer → private chunks → raw verifier → complete frozen membership →
   atomic observation/revision → immutable original result flow. Include
   exact retries, lost responses, old correction retries, VOID/null, disabled
   quota, private-bucket failure, unauthorized tokens and cross-scope GETs.
   Full-slate v2 requires its own tested sealed-batch release, not this v1 cap.
9. Release only after the exact candidate passes the required browser,
   native/hosted, security and runtime-duration checks. Enable selectors in
   the intended environment and deploy code that consumes those values;
   settings presence alone is not runtime evidence.
10. Wire scheduled producer/evaluator jobs only after the end-to-end delivery
    contract is accepted. Observe backlog age, quota usage, withheld reasons,
    revision coverage and duration. An upload, registration or partial result
    must never be displayed as whole-capture learning success.

## Configuration matrix

| Name | Where | Meaning |
| --- | --- | --- |
| `SELFLEARN_OPERATOR_TOKEN` | Backend only; owner client supplies it in memory | Owner read/control-plane boundary; not a pipeline credential. |
| `SUPABASE_URL` | Backend only | Approved project HTTPS origin, not a caller-supplied URL. |
| `SUPABASE_SECRET_KEY` **or** `SUPABASE_SERVICE_ROLE_KEY` | Backend secret only | New opaque backend secret or supported legacy service-role JWT. Shape checks do not prove grants/signature. In the new capture selector, a present invalid preferred secret fails closed; remove it explicitly to select legacy. |
| `SELFLEARN_JOURNAL_BACKEND` | Backend | Exact `supabase` opts into forecast-journal ingestion; default `disabled`. |
| `SELFLEARN_NFL_INGEST_TOKEN` | Backend verifier + NFL Actions secret | Distinct forecast-generation credential, never reused as owner/outcome token. |
| `SELFLEARN_CAPTURE_BACKEND` | Backend | Exact `supabase` opts into capture intake; default `disabled`. A token alone does not enable it. |
| `SELFLEARN_NFL_OUTCOME_INGEST_TOKEN` | Backend verifier + NFL Actions secret | Third, distinct outcome-capture credential. Bound to code-owned destination/producer. |
| `SELFLEARN_CAPTURE_EVIDENCE_BUCKET` | Backend | Reviewed existing private bucket; selection does not create or test it. |

No Supabase secret belongs in NFL's static Netlify bundle, browser local
storage, a public MCP tool response, a repository or an exception log. Production
variables do not automatically configure Preview or GitHub Actions.

## Verification and rollback boundaries

`nfl_outcome_intake.readiness=configured_unverified` is truthful static
configuration status. `production_connection_verified` and
`working_learning_loop` remain false; `model_adoption` remains unverified.
Only an explicitly recorded, authorized hosted end-to-end result can establish
a connection, and even that does not establish improved forecasts.

If intake must stop, disable its selector, stop the producer and deploy that
configuration. This prevents future operations; it does not cancel an
in-flight transaction or erase existing immutable receipts/results. Keep old
outbox IDs and original ACKs. Roll back code only to a schema-compatible
revision; never drop append-only evidence as a rollback shortcut. Bucket/key
or quota changes need the same controlled procedure as initial provisioning.

## Owner links

- [Existing Supabase project](https://supabase.com/dashboard/project/omavgimpnvrpuxqhfxpf): use its Database/migrations and Storage navigation.
- [Security Advisor](https://supabase.com/dashboard/project/omavgimpnvrpuxqhfxpf/advisors)
- [Vercel environment settings](https://vercel.com/liddar-terminal/self-learning/settings/environment-variables)
- [NFL Actions](https://github.com/liddar12/NFL2026/actions)
- [SelfLearning Actions](https://github.com/liddar12/SelfLearning/actions)

Dashboard route availability and deployed project state are not verified by
this local document. Use the existing project navigation if a route changes.
