-- Disposable pages only. Fixed sequences and clocks make native/WASM output
-- directly comparable; no RPCs treat these large synthetic bytes as evidence.
alter table selflearn_private.journal_receipts disable trigger user;
alter table selflearn_private.journal_objects disable trigger user;
alter table selflearn_private.journal_generation_commits disable trigger user;
insert into selflearn_private.journal_receipts(receipt_id,domain,task,source_receipt_id,payload_json,journal_seq,stored_utc)
values('sha256:'||repeat('1',64),'nfl','nfl.parlay_leg','sha256:'||repeat('2',64),
  '{"event_id":"nfl:2026:game:G1"}',1,'2026-11-01T00:00:00.012345Z');
insert into selflearn_private.journal_objects(object_id,kind,payload_json,journal_seq,stored_utc)
select 'sha256:'||lpad(i::text,64,'0'),'generation',
  '{"float":1.0,"escaped":' || pg_catalog.to_json(repeat(chr(92),i*71))::text ||
  ',"padding":"'||repeat('x',1420000+i*7100)||'"}',2+i,'2026-11-01T00:01:00.023456Z'
from generate_series(100,123) i;
insert into selflearn_private.journal_objects(object_id,kind,payload_json,journal_seq,stored_utc)
values('sha256:'||lpad('124',64,'0'),'generation',json_build_object('escapes',repeat(chr(92),1580000))::text,
  126,'2026-11-01T00:01:00.023456Z');
insert into selflearn_private.journal_generation_commits(object_id,receipts_json,rejections_json,ack_json,journal_seq,stored_utc)
select o.object_id,'[]','[]', '{"raw_ack_lexeme":1.0}',o.journal_seq+100,'2026-11-01T00:02:00.034567Z'
from selflearn_private.journal_objects o;
insert into selflearn_private.capture_game_generation_members_v1(object_id,receipt_id,season,week,game_id,journal_seq)
select c.object_id,'sha256:'||repeat('1',64),2026,11,case when c.journal_seq=226 then 'G2' else 'G1' end,c.journal_seq
from selflearn_private.journal_generation_commits c;
alter table selflearn_private.journal_generation_commits enable trigger user;
alter table selflearn_private.journal_objects enable trigger user;
alter table selflearn_private.journal_receipts enable trigger user;
