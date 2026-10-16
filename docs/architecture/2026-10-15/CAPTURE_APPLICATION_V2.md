# Sealed capture applications v2 — local implementation contract

Contract revision 2, October 16, 2026, 12:39 a.m. CDT (America/Chicago). Proposed/approved-for-local-build protocol; not a deployment, provider-authentication, model-adoption or D2-T3 completion claim. The R1 whiteboard/backlog remains unchanged.

This reference freezes the additive wire/storage boundary for the SQL and Python lanes. Existing v1 delivery functions/results and the offline capacity planner remain unchanged. The only permitted compatibility addition is the shared-lock BEFORE INSERT guard described below. Implementation and acceptance evidence belong in the handoff, not in these normative shapes.

## 1. Authority, canonical types and identities

- Public callers submit raw capture metadata/private chunk pointers, then server-issued step IDs. They never submit a receipt subset, group selection, normalized label, batch assignment, proof or revision.
- The packaged verifier/binder verifies raw bytes and frozen identities. SQL independently derives complete accepted membership from original generation payloads and durable ACKs and checks capture/component/proof closure. SQL does not parse provider CSV bytes or authenticate their origin.
- All canonical JSON follows existing `require_canonical_v1`/`canonical_json_v1`/`digest_json_v1` and Python fingerprint semantics. Preserve original JSON numeric scalars before conversion to jsonb: `true`, `1`, and `1.0` are not interchangeable identity inputs.
- `HASH` means `sha256:` plus exactly 64 lowercase hexadecimal digits. Sequences are canonical decimal strings, 0 through 9223372036854775807; ordinals/counts are JSON integers, never booleans/floats. Timestamps require explicit timezone and a valid instant. Database receipt clocks are not source issue/finish clocks.
- Shapes below specify exact keys; notation strings such as `HASH` denote types, not accepted literal digest values. All omitted/extra keys are refused. Arrays described as sorted/unique must be checked, not silently repaired.

Constants:

```text
execution_policy = sealed_whole_groups_v2
binding_policy   = nfl.capture_labels_v1
capture_intent_id = capture_request.delivery_id
application_id = fingerprint({version:2, kind:"nfl.capture_application.v2",
                             capture_intent_id, execution_policy})
request_id = fingerprint(the complete v2 request)
step_id = fingerprint({version:2, kind:"nfl.capture_application_step.v2",
                       application_id, ordinal})
```

The application ID excludes membership, processing time and an arbitrary nonce. A registration retry returns the original seal/ACK; it never admits a larger cohort. A changed request under the same application/common intent is a conflict. New execution-policy values are not a mechanism to reapply a prior capture.

## 2. Public request and source vector

The exact public registration request is:

```json
{
  "version": 2,
  "kind": "nfl.capture_application_request.v2",
  "execution_policy": "sealed_whole_groups_v2",
  "capture_request": {
    "version": 1,
    "kind": "nfl.capture_delivery_request.v1",
    "destination_id": "HASH",
    "producer_id": "nfl2026",
    "capture_id": "HASH",
    "binding_policy": "nfl.capture_labels_v1",
    "envelope": "EXACT_EXISTING_CAPTURE_ENVELOPE_OBJECT",
    "raw_pointers": ["EXACT_EXISTING_EVIDENCE_OBJECT_POINTER"],
    "delivery_id": "HASH"
  },
  "application_id": "HASH"
}
```

`envelope` and each pointer are objects, not the notation strings above. Their complete existing contract is normative without alteration: `nfl_capture_delivery_journal.make_capture_delivery_request`, `nfl_capture_evidence._preflight`, `evidence_chunks._pointer`, and the existing raw binder. Envelope keys are `version,kind,scope,manifest,observations,raw_objects`; pointer keys are `version,kind,scope,object_id,raw_bytes,chunks`, with chunk references `chunk_id,raw_bytes`. Version-1 IDs, raw descriptors, ordered source observations, profile URLs, clocks, audits, canonical 1 MiB chunk framing and 64 MiB per-reference accounting remain enforced. Unknown upstream update and exact finish times remain null. No caller URL is fetched.

The server derives `source_vector_id`; it is not a public input. Build one entry per manifest source:

```text
[profile, season, week, raw_sha256, raw_bytes, canonical_observed_utc]
```

Normalize `observed_utc` to its UTC instant with exactly six fractional digits and `Z` (Python UTC microsecond precision / PostgreSQL timestamptz precision). Sort entries by profile then season then week, treating null week as -1 for sorting only; retain JSON null in the entry. Reject duplicate `(profile,season,week)` entries. `source_vector_id = fingerprint(sorted_entries)`.

Do not include observation ID, HTTP metadata, audit decoration, fetch-start spelling, capture start/completion clocks or capture ID in this semantic vector. Those fields remain separately hash-verified through the original envelope. This prevents metadata-only restamping and equivalent timestamp spelling from bypassing vector uniqueness.

Enforce uniqueness of `(destination_id,producer_id,binding_policy,source_vector_id)` among v2 applications. An existing different v2 application with the same semantic vector is a conflict, not a fresh application. Following a membership-incomplete result, a replacement requires a different verified capture and vector; there is no arbitrary reapply nonce. This initial contract introduces **no implicit provider-time or DB-terminal-time freshness floor**. Different claimed clocks do not prove a genuine new provider fetch; provider authentication remains unperformed, and existing D1 chronology fences still apply. There is no retroactive semantic-vector index/deduplication over v1 deliveries: cross-version exclusion is only the exact common capture intent. Do not claim a differently restamped v1 capture is retrospectively blocked by v2 vector uniqueness.

## 3. Complete immutable seal

The server produces the seal after a complete pinned raw/frozen-member bind. Clients do not provide it to the public API.

```json
{
  "version": 2,
  "kind": "nfl.capture_application_seal.v2",
  "application_id": "HASH",
  "request_id": "HASH",
  "capture_intent_id": "HASH",
  "capture_id": "HASH",
  "execution_policy": "sealed_whole_groups_v2",
  "binding_policy": "nfl.capture_labels_v1",
  "season": 2026,
  "weeks": [6],
  "source_watermark": "123",
  "membership_id": "HASH",
  "source_vector_id": "HASH",
  "limits": {"members": 500, "observations": 500, "revisions": 500, "rpc_bytes": 8388608},
  "groups": [],
  "batches": [],
  "seal_id": "HASH"
}
```

`seal_id` hashes every other seal field. `source_watermark` equals registration's expected observed watermark. `weeks` is the sorted unique nonempty integer list from the capture. V2 global `membership_id` is the fingerprint of `{binding_policy,groups:[group.membership_id in group_id order]}`. Order sealed groups by `group_id`.

The illustrated limits are private preparation defaults/hard ceilings, not mandatory exact values. The public v2 receiver chooses **400 members, observations and revisions per new batch**. This is server-owned policy, not a public request field. Trusted server preparation may seal smaller limits for a bounded application: every limit is a strict JSON integer from 1 through its illustrated ceiling, never a boolean or float. Each whole group and batch must obey its sealed member/observation/revision limits, and the actual escaped per-batch RPC must obey its sealed `rpc_bytes` limit. The independent global registration RPC ceiling remains 8 MiB. Once admitted, no limit or assignment can be enlarged or repacked. An original 500-member seal is recovered and executed unchanged; the new registration default cannot reinterpret it.

This global order is explicitly **different from the current raw binder/v1 scope-tuple order**. Python must first validate the binder's original scope-ordered digest, then construct the v2 group-ID-ordered digest; never assert those two global digests are equal. Individual group membership hashes are unchanged. SQL derives and hashes v2 groups in group-ID order. The accepted offline planner's original digest/order is not rewritten or silently treated as this new wire contract.

Each sealed group has exactly:

```json
{
  "group_id": "HASH",
  "scope": [2026, "GAME_ID", "PLAYER_ID", "wr_rec_yds", "home"],
  "receipt_ids": ["HASH"],
  "source_receipt_ids": ["HASH"],
  "generation_object_ids": ["HASH"],
  "membership_id": "HASH",
  "status": "planned",
  "reason_codes": [],
  "batch_ordinal": 0,
  "fact": {},
  "fact_id": "HASH"
}
```

Scope is the existing underlying `(season,game_id,player_id,market,side)`, not line/model/vintage. Existing member-ID arrays are sorted/unique/nonempty; receipt/source counts match. Membership includes all accepted rungs/vintages and all durable original-generation references. `group_id` and `membership_id` use the existing v1 membership derivations.

A planned group has empty reasons, one integer batch ordinal and one common fact. A withheld group has nonempty sorted/unique machine reasons and null ordinal/fact/fact ID. Reasons use the existing `[a-z][a-z0-9_]{0,99}` grammar, at most 100 per group. Do not invent zero/VOID for an unknown measurement.

The exact common fact projection is:

```json
{
  "week": 6,
  "actual": 80.0,
  "played": true,
  "status": "FINAL",
  "reason": null,
  "participation_basis": "stat_row",
  "game": {
    "season": 2026,
    "week": 6,
    "game_id": "GAME_ID",
    "kickoff_utc": "TIMEZONE_AWARE_INSTANT",
    "home": "SEA",
    "away": "LAR",
    "state": "FINAL"
  },
  "source_observations": {
    "espn.scoreboard": {"source": "espn.scoreboard", "observation_id": "HASH", "observed_utc": "TIMEZONE_AWARE_INSTANT"},
    "nflverse.stats_player": {"source": "nflverse.stats_player", "observation_id": "HASH", "observed_utc": "TIMEZONE_AWARE_INSTANT"}
  }
}
```

This is the existing observed-kernel measurement projection without version/canonical contract/hit, plus its exact source components. `fact_id=fingerprint(fact)`. FINAL/FINAL_OT scoreboard interpretation, normalized measurement game state, finite numeric actuals (including zero/negative), VOID nulls, R106 participation evidence, missing/NA behavior and required source components remain the existing verifier/kernel rules. A VOID uses null actual, `played:false`, `status:"VOID"`, `reason:"did_not_play"` and the existing DNP participation basis; snap components are included when required. Do not rewrite any source scalar's numeric identity through jsonb.

Each batch assignment has exactly:

```json
{"ordinal": 0, "step_id": "HASH", "group_ids": ["HASH"], "member_count": 496}
```

Ordinals are consecutive from zero. Group IDs within a batch are sorted. Every planned group occurs exactly once; withheld groups never occur. Whole-group group-ID-ordered next-fit packing is deterministic. This assignment is immutable; later revisions are not sealed payload hashes. Measure the **actual v2 RPC** outer escaped wire, not the offline planner's unimplemented capacity model. If a single group cannot fit, withhold it before sealing and preserve independent groups. Do not silently repack an admitted application.

## 4. Registration ACK

```json
{
  "version": 2, "kind": "nfl.capture_application_registration.v2",
  "application_id": "HASH", "request_id": "HASH", "capture_intent_id": "HASH", "capture_id": "HASH",
  "seal_id": "HASH", "membership_id": "HASH", "source_vector_id": "HASH",
  "status": "registered", "group_count": 185, "member_count": 1088, "batch_count": 3,
  "next_step_id": "HASH",
  "source_watermark": "123", "application_seq": "1", "recorded_utc": "DATABASE_UTC_INSTANT",
  "scope": "sealed_application_only", "durable": true,
  "learning_accepted": false, "source_authentication": "not_performed",
  "registration_id": "HASH"
}
```

`registration_id` hashes every other ACK field. Sequence/time are DB-owned. ACK bytes are immutable; its original `next_step_id` is not current progress. When there are no planned batches, registration atomically also creates the terminal result; next step is null. Retrieve terminal identity through the status/terminal read, not an ACK rewrite. Exact request replay recovers this original ACK before current CAS/membership/raw work. Changed request under the same ID returns PT409.

An existing v1 common intent is **not** a v2 registration ACK: return PT409 `capture_application_legacy_original_exists`; the API supplies only a static instruction to recover the existing v1 original. Do not create or reinterpret a v2 cohort.

## 5. Batch inputs, result and terminal records

The internal `p_batch_json` is the unchanged D1 object `{version:1,observations:[],revisions:[]}`. `p_group_results_json` is an array of existing v1 group results, ordered by group ID, for exactly the sealed assignment. Their exact keys remain:

```text
group_id, scope, receipt_ids, source_receipt_ids, generation_object_ids,
membership_id, status, reason_codes, observation_ids, revision_ids
```

Status is accepted/withheld. Accepted groups require one matching observation per receipt, even for unchanged measurements; revisions may be a subset when existing outcomes already match. Withheld groups have no writes. Unknown/duplicate/unassigned proofs fail globally. Every accepted **observation** measurement's common projection and source components must match the registered fact; all original D1 validation/chronology/atomicity checks still run. An exact already-durable revision replay may retain its older source components and must not be forced to use the new capture's component IDs. Only an exact immutable existing revision receives that exception; new revisions must satisfy the normal D1 observation/proof linkage.

Immutable batch result:

```json
{
  "version": 2, "kind": "nfl.capture_application_batch_result.v2",
  "application_id": "HASH", "step_id": "HASH", "ordinal": 0,
  "seal_id": "HASH", "membership_id": "HASH", "status": "committed",
  "groups": [], "added_observations": 496, "added_revisions": 496,
  "input_watermark": "123", "watermark": "1115",
  "application_seq": "2", "recorded_utc": "DATABASE_UTC_INSTANT",
  "durable": true, "scope": "observations_and_batch_results",
  "source_authentication": "not_performed",
  "batch_result_id": "HASH"
}
```

`batch_result_id` hashes every other field. `committed` means the group results and both evidence streams committed together; it does not mean every group was accepted. Added counts must equal actual new IDs, not merely fall within bounds. On membership invalidation the current step is `not_applied`, its assigned groups are withheld with `application_membership_changed`, and its added counts are zero. Its input/output evidence watermark is the current DB watermark. Retrieve the atomically created terminal result through status/terminal read. No subsequent new step is accepted. Existing original step results remain recoverable first. Neither immutable registration nor batch ACK embeds a terminal ID: this avoids circular ACK/terminal construction and permits AFTER INSERT terminalization within the same transaction without updating an immutable row.

Terminal result:

```json
{
  "version": 2, "kind": "nfl.capture_application_result.v2",
  "application_id": "HASH", "request_id": "HASH", "capture_intent_id": "HASH", "capture_id": "HASH",
  "seal_id": "HASH", "membership_id": "HASH", "source_vector_id": "HASH",
  "status": "accepted", "reason_codes": [], "current_membership_id": "HASH",
  "group_report_id": "HASH",
  "counts": {"groups":185,"members":1088,"accepted_groups":185,"accepted_members":1088,
    "withheld_groups":0,"withheld_members":0,"not_applied_groups":0,"not_applied_members":0,
    "batches":3,"committed_batches":3},
  "registered_watermark": "123", "watermark": "2299",
  "application_seq": "5", "recorded_utc": "DATABASE_UTC_INSTANT",
  "processing_complete": true, "complete_success": true, "learning_accepted": true,
  "durable": true, "scope": "application_group_results", "source_authentication": "not_performed",
  "terminal_result_id": "HASH"
}
```

`terminal_result_id` hashes every other field. Status is exactly one of:

- `accepted`: all groups accepted, nonempty membership.
- `partial`: some accepted, some withheld; all assigned steps processed.
- `withheld`: nonempty membership, no accepted groups; processing complete.
- `no_supported_membership`: zero groups/members/batches.
- `incomplete_membership_changed`: current complete membership differs from the seal.
- `incomplete_integrity_or_capacity`: a verified raw-integrity or immutable-assignment capacity failure prevents further processing.

Only the first four are processing complete. Only accepted is complete success. Learning accepted is true iff accepted groups exist; it is not model adoption. On normal completion current membership equals sealed membership. Membership invalidation records the independently derived new membership ID. A source-integrity/capacity stop records the current independently derived membership ID too. Terminal reasons are sorted/unique and static: `application_membership_changed`, `source_integrity_failed`, or `runtime_capacity_exceeded` as applicable. A transient outage, request deadline or execution budget does not terminally stop an application; leave it active for retry.

Counts partition every sealed group/member. Withheld counts include all nonaccepted groups/members; not-applied counts are an explicitly identified subset of withheld counts, not another denominator. Initial withheld groups have no batch. `group_report_id` is the fingerprint of the complete sorted terminal group-view records below. Terminalization after the final batch is in that batch's transaction; later membership changes never rewrite its original cohort/result.

## 6. Status and paged reads

The single registration-view record has exactly:

```json
{"request":{},"seal_header":{},"registration":{}}
```

`request` is the complete original public v2 request, including its capture envelope and raw pointers. `registration` is the immutable registration ACK from section 4. `seal_header` is the complete immutable seal with **only** `groups` and `batches` removed; retain `seal_id`, limits, all identity fields and the original source watermark. This record, all group pages' `sealed_group` objects in group-ID order and all batch pages' `assignment` objects in ordinal order reconstruct the exact seal. The port must rehash that reconstructed seal and request and verify registration identity before any resumed raw read/step. ACK alone is not sufficient restart state.

Before registration commits, preflight the **entire universal registration page**, with this one combined record and a conservative 19-digit page `ledger_watermark`, against the 3 MiB response limit using the actual SQL response encoding. A near-3-MiB public request may therefore be unsupported once restart metadata is included; refuse it with PT422 `capture_application_registration_record_exceeds_byte_limit` before creating any application/ACK. Do not truncate the request, omit pointers, split this single record silently or return an unreadable registration. No separate persisted parsed index is implied.

Status record:

```json
{
  "version":2,"kind":"nfl.capture_application_status.v2","application_id":"HASH",
  "registration_id":"HASH","seal_id":"HASH","membership_id":"HASH",
  "status":"active","batch_count":3,"committed_batches":1,"next_step_id":"HASH",
  "terminal_result_id":null,"ledger_watermark":"2",
  "scope":"pinned_application_progress","durable_ack":false,"execution_ready":true,
  "source_authentication":"not_performed"
}
```

Status is active/terminal. At terminal, next step is null and terminal ID is nonnull. `execution_ready` means a registered local protocol can accept bounded step commands, not that hosted intake is enabled. It is false for terminal status. This is a pinned view, not an immutable original ACK or current membership reverification.

Group-view record:

```json
{"sealed_group":{},"execution_state":"pending","result":null}
```

`sealed_group` is the complete seal group. Execution state is `pending`, `initial_withheld`, `committed`, or `not_applied`. Result is null only for pending; otherwise it is an existing v1-shaped group result. Initial withheld and terminal not-applied groups have empty proof IDs and explicit reasons. Group records are sorted/keyset-paged by sealed `group_id`.

The `batches` list-view record:

```json
{"assignment":{"ordinal":0,"step_id":"HASH","group_ids":["HASH"],"member_count":496},"result":null}
```

Result is null or the immutable batch result. Order/keyset cursor is canonical ordinal text. The single `batch` view instead uses `p_key=step_id` and returns `records=[<immutable batch result>]` after commitment or `records=[]` before commitment; it does not wrap that result with an assignment. A step must belong to that seal even if not yet committed. The `batches` list always retains its full immutable assignments for restart reconstruction.

Universal page:

```json
{
  "version":2,"kind":"nfl.capture_application_page.v2","found":true,
  "application_id":"HASH","view":"groups","ledger_watermark":"2",
  "registration_id":"HASH","seal_id":"HASH",
  "records":[],"total_records":185,"next_after":"HASH"
}
```

Views: registration/status/groups/batches/batch/terminal. Single-record views require null after and limit 1; p_key is null except batch. Group/batch pages use limit 1..100 and actual serialized-size bounds. First read chooses a committed application-ledger watermark; all following pages must use that exact fence. Never mix current batch results into a prior page fence. Unknown application returns found false, empty records, zero total, null next/registration/seal/watermark. Known application with uncommitted batch/terminal returns found true with zero records. API ownership checks precede returning records.

Cursors are group hashes or canonical ordinal strings, depending on view; nonnull cursors must identify a preceding record in the selected immutable assignment. Reject cursor-after-end, invalid types, future fences and fences before registration. A complete-reader port starts at null, checks invariant metadata/total, ordering, duplicates and termination, and verifies the terminal report hash after collecting every group. No subset is a complete application report.

## 7. SQL contracts, atomicity and error mapping

Use these exact public SQL function signatures (all text parameters except limit):

```text
selflearn_journal_register_capture_application_v2(p_request_json, p_seal_json, p_watermark)
selflearn_journal_commit_capture_application_batch_v2(p_application_id, p_step_id,
    p_group_results_json, p_batch_json, p_watermark)
selflearn_journal_stop_capture_application_v2(p_application_id, p_reason, p_watermark)
selflearn_journal_read_capture_application_v2(p_application_id, p_view, p_key,
    p_after, p_limit integer, p_ledger_watermark)
```

Register returns the original registration ACK. Commit returns the original batch result. Stop returns the original terminal result; allowed stop reasons are `membership_changed`, `source_integrity_failed`, `runtime_capacity_exceeded`, `complete`. SQL must independently prove membership_changed and complete; the trusted raw verifier supplies conservative source/capacity stops. Read returns the universal page.

All new writer/direct-insert paths require READ COMMITTED and acquire advisory transaction lock `(619044,1)`. Registration validates expected observed CAS and complete current membership. A commit first recovers an existing exact application/step result, then checks terminal/order, independently re-derives current full membership, then checks global observed CAS and validates proofs. Membership mismatch persists the not-applied step and terminal result **without raising an exception that rolls them back**. Unrelated evidence writes cause PT409 and a full snapshot/re-resolve. Both streams and their immutable result commit atomically; call existing `selflearn_journal_commit_observed_v1`, never separate per-row network writes.

Exact original recovery precedes today's CAS/membership/raw work. Identity/request conflicts never recover a different operation. Do not retry unknown transport errors automatically or ACK before verifying complete paginated results and exact added-ID counts against the pinned snapshot.

Insertion-count authority is explicit: a fresh commit response is checked against the exact absent observation/revision ID sets at that response's **original input watermark**, before any matching-stored-result fallback. Complete historical GETs verify the immutable DB-computed result's hashes, full cohort/assignment/report partition, bounds, sequence and pinned-ledger consistency; they do not independently reconstruct every prior insertion count by repeatedly loading all historical observed snapshots. This trusts the narrowly privileged, tested atomic SQL count computation, not arbitrary client labels or a self-consistent hash alone. It is neither Byzantine-proof verification nor provider authentication.

Tables in `selflearn_private`:

| Table | Service INSERT input columns | Computed immutable authority |
| --- | --- | --- |
| `capture_applications_v2` | request_json, seal_json, expected_watermark | IDs, owner/scope, source vector, registration_json, application_seq, registered_utc |
| `capture_application_batches_v2` | application_id, step_id, group_results_json, batch_json, expected_watermark | ordinal, result_json, batch_result_id, application_seq, recorded_utc |
| `capture_application_results_v2` | application_id, stop_reason, expected_watermark | complete terminal result/report, terminal_result_id, application_seq, recorded_utc |
| `capture_membership_cache_v2` | season, weeks_json | DB-derived authority fence/policy/cache IDs, complete members/groups/original-source index, payload hash, byte accounting and stored clock; optimization only |

Primary/unique constraints: application ID; common capture intent; semantic source-vector ownership tuple; `(application_id,ordinal)`; step ID; one terminal per application. FK without cascading mutation. Use a separate shared application-ledger bigint sequence; these records alone do not advance observed evidence watermark. FORCE RLS, revoke public/anon/authenticated, service SELECT plus only listed INSERT columns, invoker/empty-search-path functions, and UPDATE/DELETE/TRUNCATE refusal triggers apply. No SECURITY DEFINER fallback. Guards compute result columns for direct INSERT exactly as RPCs do.

`capture_members_v2` is an explicit mechanical lift of complete v1 generation/ACK/original-source/contract validation, changing only function identity and output ceiling to 10,000. Retain all existing global receipt/generation byte/count work caps. Do not weaken checks or reinterpret jsonb equality as canonical-text equality. Require actual <=500 positive and fault-case parity against unchanged v1. All v2 writers use this one complete membership primitive.

The additive old-table trigger must sort before `capture_delivery_insert`, acquire the same lock, derive/validate the common intent from original fields (not trust a supplied digest), and reject first-time v1 insertion if v2 owns it. V2 registration checks existing v1 under that same lock. Existing v1 exact recovery happens before INSERT and remains available. Assert all existing function definitions remain byte-identical.

### Derived membership prewarm boundary

The additive optimization RPC is `selflearn_journal_prepare_capture_membership_v2(p_season integer,p_weeks_json text)`. Weeks use exact canonical JSON, sorted unique strict integers 1..18; season is 2000..2100. Its exact 13-key metadata response is:

```json
{
  "version":2,"kind":"nfl.capture_membership_cache.v2",
  "scope":"derived_membership_cache_only","durable_ack":false,"learning_accepted":false,
  "season":2026,"weeks":[6],"authority_watermark":"123","policy_id":"HASH",
  "cache_id":"HASH","membership_id":"HASH","member_count":1088,"group_count":185
}
```

This is a performance hint, not a caller-supplied membership certificate or learning/publication/provider-authentication ACK. Public HTTP callers cannot submit cached members, labels, source indexes, policy overrides, GUCs or a receipt subset. The service role can insert only season/weeks into the private cache table; an invoker BEFORE INSERT trigger computes every authority/payload field from complete immutable source records. Anonymous/authenticated roles have no access, and UPDATE/DELETE/TRUNCATE remain refused. Creation and lookup share READ COMMITTED and advisory lock `(619044,1)` with source writers and capture commits; no external I/O runs inside that SQL transaction.

Cache identity binds **season, canonical weeks, current global source-authority watermark and policy ID**. The source watermark is the maximum committed journal sequence across **all domains'** `journal_receipts`, `journal_objects` and `journal_generation_commits`, not merely NFL rows or accepted generations. Any new row in those tables invalidates a hit, even when the NFL member set is unchanged. Observation/revision-only writes intentionally do not invalidate membership; every new step still rereads the complete indexed outcome history and performs normal kernel/source fences and observed CAS. A miss runs the unchanged complete `capture_members_v2` validation before deriving groups and the original-source index. No negative-result shortcut may hide new malformed or orphan source rows.

The policy ID hashes exact qualified function definitions for membership enumeration, grouping, membership identity, original-source indexing, source-authority selection, cache guard/lookup/policy/locking, canonical-input validation, canonical JSON, digest, JSON number/string and NFL line-token helpers. Changing a dependency must create a different policy key, not reuse an older immutable derivation. Cache hits recheck the payload hash, full group membership hash and cache identity. Original learning-receipt JSON remains **text** inside source-index wrappers, preserving integer/float and other authenticated source lexemes instead of normalizing those values through jsonb.

The global cache is bounded to **256 immutable rows and 128 MiB of accounted serialized rows**, including escaped source text, metadata and conservative counter overhead. This is not a promise about exact PostgreSQL disk/TOAST/index allocation. Row/aggregate limits are checked under the same lock before admission; no automatic eviction, refund, reset or GC is implemented. Existing exact hits remain usable at capacity; a required new derivation refuses with PT503 `capture_membership_cache_capacity_exceeded`, leaving operation IDs resumable rather than claiming success or silently bypassing validation. Long-running production operation therefore still needs an owner-approved capacity/retention procedure; this finite local pilot is not an unbounded autonomous cache.

Original application/step recovery remains before cache warming, current raw reads and clocks. Fresh registration performs its complete Python raw bind/preparation, then prewarms before registration SQL. A fresh step recovers the original/pinned application first, then prewarms before its new full snapshot/membership/raw work. The cache never replaces raw re-verification, complete group/vintage/rung closure, frozen identity checks, the current outcome resolve or atomic CAS. Prewarm and later SQL calls must each fit the configured transport timeout; the complete HTTP operation must also fit the unchanged 60-second platform duration and server-owned operation budget with margin. Local PostgreSQL-WASM timings alone are not hosted performance or native-concurrency acceptance.

SQLSTATEs:

| Code | Meaning / HTTP behavior |
| --- | --- |
| PT422 | Typed/canonical/source/seal/proof/assignment/work/page/isolation violation; static sanitized 422 |
| PT409 | Changed immutable request, semantic-vector reuse, legacy-original ownership, stale observed CAS, terminal/out-of-order new step; sanitized 409 |
| PT503 | Explicitly missing/disabled configured capability; sanitized 503 |
| 42501 | Unauthorized DB role/column/function; never expose SQL diagnostics to API caller |

Stable special conflict messages: `capture_application_request_conflict`, `capture_application_source_vector_reused`, `capture_application_legacy_original_exists`, `capture_application_v2_owns_intent`, `stale_capture_application_watermark`, `capture_application_terminal`, `capture_application_step_out_of_order`. Other invalid data may use the static `invalid_capture_application`/`invalid_capture_application_page`; do not persist untrusted exception text. A newly detected membership invalidation is a returned durable incomplete result, not PT409.

## 8. Limits and API/operator behavior

| Boundary | Ceiling |
| --- | --- |
| Actual public JSON body, including v2 wrapper/whitespace | 3 MiB |
| Existing raw chunk / aggregate capture | 1 MiB / 64 MiB, with existing per-profile and per-reference limits |
| Canonical seal | 3 MiB |
| Complete groups / members | 2,000 / 10,000 |
| Per batch members / observations / revisions | 500 / 500 / 500 |
| New public v2 registration's server-owned member / observation / revision defaults | 400 / 400 / 400; existing seals retain their original limits/assignments |
| Actual escaped internal RPC outer body | 8 MiB |
| Paged response | 100 records and 3 MiB; reduce returned count when needed; registration preflights its full one-record page with maximum fence width before admission; a single unrepresentable record fails closed |
| Complete generic NFL authority scan | Existing 10,000 receipts +64 MiB; 256 generation commits +64 MiB |
| Per group reasons | 100 existing-format codes |

Resource-limit refusal never silently drops members. An individually oversized group is withheld before sealing. Unexpected post-seal wire growth stops the application with an incomplete capacity result rather than splitting/repacking it.

Locally implemented application routes, separate from v1; not hosted-activation or performance acceptance:

```text
POST /api/v2/nfl/capture-applications
GET  /api/v2/nfl/capture-applications/{application_id}
POST /api/v2/nfl/capture-applications/{application_id}/steps/{step_id}
GET  /api/v2/nfl/capture-applications/{application_id}/steps/{step_id}
```

Registration POST accepts only the public v2 request. Step POST accepts only `{}` and a server-issued step ID; no client assignment/labels. The application GET validates allowlisted view/cursor/fence fields. Step GET returns the original batch result or a bounded pending/not-found state. Existing separate outcome credential, configured destination/producer, private storage and durable staging quota remain required. Owner/forecast tokens cannot access these routes. Missing configuration/default-off capability must not activate from a token alone.

The exact registration POST wrapper is `{version:2,registration:<original immutable registration ACK>,scope:"registration_only",learning_accepted:false}`. The exact successful step POST wrapper is `{version:2,result:<original immutable batch ACK>,scope:"batch_only"}`. Both GET routes return the universal page, including a step's empty records while pending. A stopped step returns static HTTP 409 guidance to GET its original terminal report; it does not return a fabricated success wrapper.

Runtime selection requires explicit `SELFLEARN_CAPTURE_BACKEND=supabase` and `SELFLEARN_CAPTURE_PROTOCOL=v2` plus the independently valid server configuration. Protocol defaults to v1; preserve existing v1 response/status bytes. The static configuration status adds `capture_protocol` only for selected v2. No environment, token, bucket or migration is provisioned by this document or by pure selection, and neither selector nor token proves hosted readiness.

Immutable sender outbox stores original registration request/ACK, each server-issued step command/ACK, and final original result in separate paths. GET original before restaging/recomputing; bounded retries retain exact operation IDs. A lost batch reply recovers that step, not a newly selected next batch. A replacement after an incomplete terminal result needs a new verified capture, not a new nonce or overwritten ACK. Do not reuse an incomplete application's applied subset as a full evaluation cohort.

The v2 sender caps each urllib operation at **50 seconds**, further bounded by its command's remaining monotonic deadline. It checks the remaining budget before network work, between bounded response reads and before accepting a response. This socket/inactivity timeout is not a guaranteed hard wall-clock limit on DNS resolution or a continuously trickling response. Expiry leaves the original operation resumable; neither expiry nor a late response is learning acceptance. This v2 sender bound does not widen the server's 45-second operation budget, SQL transport timeout, v1 sender timeout or platform duration.

Preferred minimal executor is stateless bounded step calls: one complete verified bind/cache per process, full raw verification again on restart, complete indexed snapshot/current membership and current-revision resolve for every new step. No durable parsed index or worker lease is introduced. Request-budget exhaustion leaves active work resumable. Lease expiry is therefore not an implemented capability or acceptance claim; any future lease must be separate coordination, never a replacement for CAS/immutable results. Scheduling, retention/GC, quotas already consumed by orphan staging, native concurrency and hosted activation remain separate gates.

The existing Vercel `maxDuration` is **60 seconds**, unchanged. Measure complete per-call snapshot enumeration, resumed raw verification/binding, kernel work, SQL membership enumeration/commit, paging and serialization, not just the pure planner. Set a server-owned monotonic wall-clock budget that stops before starting another step; do not leave a DB transaction open during raw/network work. An in-flight commit may outlive a disconnected client and is recovered by its immutable step ID. Do not claim hosted readiness if measured registration/step calls cannot fit the configured duration with safety margin. A separately authorized runtime/workflow/configuration change is required if this bound cannot be met; do not assume an 800-second platform allowance.

## 9. Required proof before D2-T3 completion

Actual 1,088-member/185-group raw capture through registration, three whole-group batches and terminal report; complete rung/vintage closure and independent oversized-group survival; registration/batch/final lost-reply recovery; duplicate workers and kill/restart at every committed prefix; injected failure between evidence and result yields neither; unrelated-write CAS recomputation; membership expansion terminalizes without further evidence; no late change after finalization rewrites the original; direct INSERT/RPC/v1-v2 race protection; semantic restamp and clock-spelling rejection; tamper/type/size/role/page/fence failures; full report count/hash verification and unchanged original forecasts; measured full registration/resume/step duration and bounded-deadline recovery against the unchanged 60-second runtime. Author/run native multi-session tests separately. PostgreSQL-WASM/in-process HTTP/fake private Storage evidence must never be relabeled native, network HTTP, hosted Storage, provider authentication or production readiness.
