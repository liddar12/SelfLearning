-- Disposable scoped-history benchmark with fixed IDs and journal sequences.
-- Raw rows are synthetic and never enter grading or production data.
alter table selflearn_private.journal_receipts disable trigger user;
alter table selflearn_private.journal_observations disable trigger user;
alter table selflearn_private.journal_revisions disable trigger user;
insert into selflearn_private.journal_receipts(receipt_id,domain,task,source_receipt_id,payload_json,journal_seq,stored_utc)
select 'sha256:'||lpad(i::text,64,'0'),'nfl','nfl.parlay_leg','sha256:'||lpad((50000+i)::text,64,'0'),
  '{"event_id":"nfl:2026:game:'||case when i<=2996 then 'G1' else 'OTHER' end||'","raw_float":80.0,"padding":"'||repeat('x',1200)||'"}',
  i,'2026-11-01T00:00:00.012345Z' from generate_series(1,28736) i;
insert into selflearn_private.journal_observations(observation_id,receipt_id,evidence_observation_id,payload_json,journal_seq,stored_utc)
select 'sha256:'||lpad((120000+i)::text,64,'0'),'sha256:'||lpad(i::text,64,'0'),'sha256:'||lpad((130000+i)::text,64,'0'),
  '{"raw_float":80.0,"padding":"'||repeat('x',1600)||'"}',60000+i,'2026-11-01T00:01:00.023456Z'
from generate_series(1,15000) i where i<=1727 or i>2996;
insert into selflearn_private.journal_revisions(revision_id,receipt_id,supersedes,payload_json,journal_seq,stored_utc)
select 'revision-'||i,'sha256:'||lpad(i::text,64,'0'),null,
  '{"raw_float":80.0,"padding":"'||repeat('x',2000)||'"}',80000+i,'2026-11-01T00:02:00.034567Z'
from generate_series(1,15000) i where i<=1727 or i>2996;
insert into selflearn_private.journal_observations(observation_id,receipt_id,evidence_observation_id,payload_json,journal_seq,stored_utc)
select 'sha256:'||lpad((220000+i)::text,64,'0'),'sha256:'||lpad(i::text,64,'0'),'sha256:'||lpad((230000+i)::text,64,'0'),
  '{"raw_float":81.0}',100000+i,'2026-11-01T00:03:00.045678Z' from generate_series(1,20) i;
insert into selflearn_private.journal_revisions(revision_id,receipt_id,supersedes,payload_json,journal_seq,stored_utc)
select 'correction-'||i,'sha256:'||lpad(i::text,64,'0'),'revision-'||i,
  '{"raw_float":81.0}',110000+i,'2026-11-01T00:04:00.056789Z' from generate_series(1,20) i;
alter table selflearn_private.journal_receipts enable trigger user;
alter table selflearn_private.journal_revisions enable trigger user;
alter table selflearn_private.journal_observations enable trigger user;
analyze selflearn_private.journal_receipts;
analyze selflearn_private.journal_revisions;
analyze selflearn_private.journal_observations;
