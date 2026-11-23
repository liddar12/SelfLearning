# Scoped generation proof warming

The additive `20261123192721_capture_generation_authority_proofs_v1.sql`
migration follows scoped membership and batched-object reads. It does not
backfill certificates inside the migration transaction or change forecast
admission, outcome writes, existing v2 behavior, or journal watermarks.

Each certificate is derived from an immutable original generation and its full
ACK partition. The verifier checks canonical bytes, the envelope hash, unique
and exhaustive source membership, and accepted receipt mappings. It records
the original object/commit sequences and raw payload/ACK hashes under a
validator-definition policy. Only the private verifier can insert a
certificate; the service role cannot insert or modify certificate rows.

The service-only RPC
`selflearn_journal_prepare_capture_generation_proofs_v1(p_game_scope_json)`
derives all generation IDs from the persisted game index and verifies at most
one missing generation per call. It accepts the canonical game scope, never a
caller-selected member subset. Its response reports total, verified, remaining,
and prepared-object metadata, with `durable_ack=false` and
`learning_accepted=false`.

Scoped registration now warms before full snapshot/raw binding, after request
validation and original/legacy recovery. The Python port loops bounded calls
using the operation deadline. Completed proof transactions survive a deadline
or failed later call; the next scheduled attempt resumes. The same path
revalidates existing active scopes once and picks up new appended generations.
Proofs are shared across games using the same immutable original. A verifier
policy change requires fresh certificates and invalidates derived membership
caches. Missing certificates fail closed; a metadata response is never source
or membership authority.

Membership construction still verifies every selected original source hash,
contract, period, frozen forecast parity, and completeness. It now carries
exact original source JSON and kickoff from that same pass into the cache,
retaining the duplicate-original raw-byte conflict check. This removes a
second source-document scan. Registration/batch/terminal CAS and global
watermark semantics are unchanged.

Local PostgreSQL-WASM profile: 28,736 receipts, 43 mixed-game generations,
3,567 selected receipts, 23,925,699 original generation bytes. Cold prepare
was 9.10 seconds before and 3.01 seconds after warming; 43 one-time proof calls
took 13.22 seconds total, at most 0.461 seconds each. A separate 2,914,539-byte
generation with 3,500 receipts / 85 selected took 1.49 seconds to verify and
0.105 seconds to prepare. Membership, groups and exact source-index values
matched. These are local synthetic measurements, not hosted latency results.

Run `tests/postgres/generation_authority_proofs.test.mjs` for SQL refusal,
resumption, integrity and policy checks, and
`tests/postgres/profile_generation_authority_proofs.mjs` for the reproducible
43-generation comparison. Python port tests cover deadline interruption and
recovery ordering; the real Python-to-SQL adapter test uses the reviewed NFL
producer fixture checkout.
