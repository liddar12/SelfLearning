"""Real file/process persistence, not authenticated owner or model adoption proof."""
import copy
import json
import os
import subprocess
import sys
from unittest.mock import patch

import pytest

from selflearn_core import owner_intents as dto
from selflearn_core.probability_receipts import fingerprint
from selflearn_core.store.file_owner_intents import FileOwnerIntentJournal
from selflearn_core.store.receipt_journal import JournalConflict

AUTHORITY=fingerprint('configured-file-owner-authority')
NS={'domain':'example','task':'binary.forecast','producer_id':'file-pilot'}
PAYLOAD={'coefficients':{'a':0.0,'b':1.0},'scope_kind':'future_period_v2_not_exact_generation'}
OID=fingerprint(PAYLOAD)
EVIDENCE=json.dumps({'object_id':OID,'kind':'source_snapshot','payload':PAYLOAD,'received_utc':'2026-10-15T00:00:00Z'}).encode()
NOW='2026-10-16T18:00:00Z'


def command(head=None,action='approve',artifact='a',namespace=None,**changes):
    pins={key:fingerprint(key)for key in dto.PIN_KEYS-{'evidence_object_ids'}}
    pins.update(artifact_id=fingerprint(artifact),evidence_object_ids=[OID])
    raw={'version':1,'kind':'selflearn.owner_intent_command.v1','namespace':namespace or NS,
        'action':action,'expected_head_event_id':head,'pins':pins,'effective_from_utc':None,
        'expires_utc':None if action=='revoke'else'2026-10-19T23:59:59Z','reason':'Explicit local test intent only'}
    raw.update(changes)
    return dto.seal_command(raw,authority_id=AUTHORITY)


def journal(root,**options):
    return FileOwnerIntentJournal(root,authority_id=AUTHORITY,actor_ref='configured-local-operator',
        evidence_reader=lambda oid:EVIDENCE,clock=lambda:NOW,**options)


def test_constructor_and_empty_read_do_not_create_a_repository_or_load_domain_code(tmp_path):
    root=tmp_path/'missing'
    value=FileOwnerIntentJournal(root,authority_id=AUTHORITY,clock=lambda:NOW)
    snapshot=value.read_owner_intents(NS)
    assert not root.exists() and snapshot['events']==[] and snapshot['watermark']=='0'
    assert snapshot['activation']is False and snapshot['domain_pin_binding_verified']is False
    with pytest.raises(ValueError,match='read_only'):value.append_owner_intent(command())
    for kwargs in ({'authority_id':'bad'},{'authority_id':AUTHORITY,'actor_ref':'bad\n'},
        {'authority_id':AUTHORITY,'evidence_reader':True},{'authority_id':AUTHORITY,'clock':False}):
        with pytest.raises(ValueError):FileOwnerIntentJournal(root,**kwargs)


def test_actual_restart_preserves_intents_revoke_rollback_and_original_historical_ack(tmp_path):
    first=journal(tmp_path/'store');cmd=command();untouched=copy.deepcopy(cmd)
    a=first.append_owner_intent(cmd)
    second=journal(tmp_path/'store')
    b=second.append_owner_intent(command(a['event_id'],artifact='b'))
    rollback=second.append_owner_intent(command(b['event_id'],action='rollback'))
    revoked=second.append_owner_intent(command(rollback['event_id'],action='revoke'))
    snapshot=first.read_owner_intents(NS)
    assert snapshot['head_event_id']==revoked['event_id'] and snapshot['head_sequence']==4
    assert [e['command']['action']for e in snapshot['events']]==['approve','approve','rollback','revoke']
    assert all(snapshot[k]==v for k,v in dto.FLAGS.items())
    with (patch.object(first,'clock',side_effect=AssertionError('retry clock')),
          patch.object(first,'evidence_reader',side_effect=AssertionError('retry evidence'))):
        assert first.append_owner_intent(cmd)==a
    assert cmd==untouched and first.read_owner_intents(NS)['head_sequence']==4
    assert list((tmp_path/'store').rglob('.owner-*'))==[]


def test_stale_head_missing_reference_and_clock_failure_never_advance_head(tmp_path):
    j=journal(tmp_path/'store');a=j.append_owner_intent(command())
    with pytest.raises(JournalConflict):j.append_owner_intent(command(artifact='other'))
    for raw in (b'{}',b'{"a":1,"a":2}',b'{"x":NaN}',b'{broken',None):
        with patch.object(j,'evidence_reader',return_value=raw),pytest.raises((ValueError,TypeError)):
            j.append_owner_intent(command(a['event_id'],artifact='other'))
        assert j.read_owner_intents(NS)['head_event_id']==a['event_id']
    for change in ({'object_id':fingerprint('wrong')},{'kind':'generation'},{'payload':{'changed':True}},
        {'received_utc':'2026-10-20T00:00:00Z'}):
        bad=json.loads(EVIDENCE);bad.update(change)
        with patch.object(j,'evidence_reader',return_value=json.dumps(bad).encode()),pytest.raises(ValueError):
            j.append_owner_intent(command(a['event_id'],artifact='other'))
    with patch.object(j,'clock',return_value='2026-10-14T00:00:00Z'),pytest.raises(ValueError):
        j.append_owner_intent(command(a['event_id'],artifact='other'))
    assert j.read_owner_intents(NS)['head_sequence']==1


def test_separate_namespaces_and_authorities_cannot_borrow_a_valid_head(tmp_path):
    j=journal(tmp_path/'store');a=j.append_owner_intent(command())
    other={**NS,'producer_id':'other'}
    assert j.read_owner_intents(other)['events']==[]
    j.append_owner_intent(command(namespace=other))
    assert j.read_owner_intents(NS)['head_event_id']==a['event_id']
    alternate=FileOwnerIntentJournal(tmp_path/'store',authority_id=fingerprint('other'),clock=lambda:NOW)
    assert alternate.read_owner_intents(NS)['events']==[]
    with pytest.raises(ValueError):alternate._path('..','events')


def test_pinned_read_retains_original_complete_chain_during_a_later_cooperating_append(tmp_path):
    j=journal(tmp_path/'store');a=j.append_owner_intent(command())
    real=j._events
    def later(namespace,head):
        result=real(namespace,head)
        j._events=real
        j.append_owner_intent(command(a['event_id'],action='revoke'))
        return result
    with patch.object(j,'_events',side_effect=later):snapshot=j.read_owner_intents(NS)
    assert snapshot['head_event_id']==a['event_id'] and snapshot['head_sequence']==1
    assert j.read_owner_intents(NS)['head_sequence']==2


def test_partial_or_tampered_history_is_never_returned_as_complete(tmp_path):
    j=journal(tmp_path/'store');a=j.append_owner_intent(command());j.append_owner_intent(command(a['event_id'],action='revoke'))
    path=j._path('events',a['event_id'][7:]+'.json');original=path.read_bytes();path.unlink()
    with pytest.raises(FileNotFoundError):j.read_owner_intents(NS)
    path.write_bytes(original);doc=json.loads(original);doc['actor_ref']='forged';path.write_text(dto.canonical(doc))
    with pytest.raises(ValueError,match='hash'):j.read_owner_intents(NS)
    path.write_bytes(original)
    headpath=j._head_path(NS);head=json.loads(headpath.read_text());head['head_sequence']=True
    headpath.write_text(dto.canonical(head))
    with pytest.raises(ValueError):j.read_owner_intents(NS)


def test_symlinks_nonregular_files_wrong_sizes_and_noncanonical_bytes_refuse(tmp_path):
    outside=tmp_path/'outside';outside.mkdir();root=tmp_path/'link';root.symlink_to(outside,target_is_directory=True)
    with pytest.raises(ValueError,match='symlink'):journal(root)
    j=journal(tmp_path/'store');j.append_owner_intent(command());head=j._head_path(NS);original=head.read_bytes()
    head.unlink();head.symlink_to(outside/'absent')
    with pytest.raises(ValueError,match='symlink'):j.read_owner_intents(NS)
    head.unlink();os.mkfifo(head)
    with pytest.raises(ValueError,match='type'):j.read_owner_intents(NS)
    head.unlink();head.write_bytes(b' '*4097)
    with pytest.raises(ValueError,match='size'):j.read_owner_intents(NS)
    head.write_bytes(original+b'\n')
    with pytest.raises(ValueError,match='noncanonical'):j.read_owner_intents(NS)
    assert list(outside.iterdir())==[]


def test_interrupted_head_publication_leaves_only_unreferenced_immutable_event_and_retry_recovers(tmp_path):
    j=journal(tmp_path/'store');real=j._write;cmd=command()
    def fail(path,value,*,immutable):
        if not immutable:raise OSError('synthetic interrupted pointer')
        return real(path,value,immutable=immutable)
    with patch.object(j,'_write',side_effect=fail),pytest.raises(OSError):j.append_owner_intent(cmd)
    assert j.read_owner_intents(NS)['events']==[]
    assert len(list((j.root/'events').glob('*.json')))==1
    ack=j.append_owner_intent(cmd)
    assert j.read_owner_intents(NS)['head_event_id']==ack['event_id']
    # Same event bytes reuse the orphan without changing it.
    assert len(list((j.root/'events').glob('*.json')))==1
    event=j.read_owner_intents(NS)['events'][0];path=j._path('events',event['event_id'][7:]+'.json')
    j._write(path,event,immutable=True)
    with pytest.raises(ValueError,match='conflict'):j._write(path,{**event,'actor_ref':'changed'},immutable=True)


def test_independent_process_writer_lock_and_restart_are_real_not_a_mocked_database(tmp_path):
    root=tmp_path/'store';j=journal(root)
    code="""import json,sys
from selflearn_core.store.file_owner_intents import FileOwnerIntentJournal
j=FileOwnerIntentJournal(sys.argv[1],authority_id=sys.argv[2],actor_ref='configured-child',
 evidence_reader=lambda oid:sys.argv[4].encode(),clock=lambda:'2026-10-16T18:00:00Z')
try:
 print(json.dumps(j.append_owner_intent(json.loads(sys.argv[3]))))
except BlockingIOError:
 print('busy');sys.exit(3)
"""
    args=[sys.executable,'-c',code,str(root),AUTHORITY,json.dumps(command()),EVIDENCE.decode()]
    with j.lock(NS):
        busy=subprocess.run(args,text=True,capture_output=True,timeout=15)
        assert busy.returncode==3 and busy.stdout.strip()=='busy',busy.stderr
    saved=subprocess.run(args,text=True,capture_output=True,timeout=15)
    assert saved.returncode==0,saved.stderr
    ack=json.loads(saved.stdout);assert j.read_owner_intents(NS)['head_event_id']==ack['event_id']
    with pytest.raises(JournalConflict):j.append_owner_intent(command(artifact='competing'))


def test_complete_maximum_chain_and_original_retry_at_capacity(tmp_path):
    # Seed 256 genuine DTO events through the immutable file writer; this is not
    # a claim that append was executed 256 times or an OS power-loss drill.
    j=journal(tmp_path/'store');events=[];first=command()
    for index in range(dto.MAX_EVENTS):
        cmd=first if index==0 else command(events[-1]['event_id'],artifact='a'+str(index))
        event=dto.make_event(cmd,events,authority_id=AUTHORITY,actor_ref=j.actor_ref,recorded_utc=NOW)
        j._write(j._path('events',event['event_id'][7:]+'.json'),event,immutable=True);events.append(event)
    head={'version':1,'kind':'selflearn.file_owner_head.v1','authority_id':AUTHORITY,'namespace':NS,
        'head_sequence':len(events),'head_event_id':events[-1]['event_id']};head['pointer_id']=fingerprint(head)
    j._write(j._head_path(NS),head,immutable=False)
    assert j.read_owner_intents(NS)['head_sequence']==256
    assert j.append_owner_intent(first)==dto.acknowledgement(events[0])
    with pytest.raises(ValueError,match='capacity'):j.append_owner_intent(command(events[-1]['event_id'],artifact='new'))
    assert j.read_owner_intents(NS)['head_sequence']==256


def test_evidence_limits_and_unavailable_reader_refuse_before_persisting_any_event(tmp_path):
    j=journal(tmp_path/'store')
    with patch.object(j,'evidence_reader',None),pytest.raises(ValueError,match='reader'):j.append_owner_intent(command())
    with patch('selflearn_core.store.file_owner_intents.MAX_EVIDENCE_BYTES',4),pytest.raises(ValueError,match='bytes'):
        j.append_owner_intent(command())
    with patch('selflearn_core.store.file_owner_intents.MAX_TOTAL_EVIDENCE_BYTES',4),pytest.raises(ValueError,match='limit'):
        j.append_owner_intent(command())
    assert not j.root.exists()


def test_failed_file_sync_or_head_rename_never_returns_a_success_and_cleans_owned_temporary_files(tmp_path):
    for operation in ('sync','rename'):
        j=journal(tmp_path/operation)
        target='selflearn_core.store.file_owner_intents.os.'+('fsync'if operation=='sync'else'replace')
        with patch(target,side_effect=OSError('synthetic write fault')),pytest.raises(OSError):
            j.append_owner_intent(command())
        assert j.read_owner_intents(NS)['head_sequence']==0
        assert list(j.root.rglob('.owner-*'))==[]
        ack=j.append_owner_intent(command())
        assert j.read_owner_intents(NS)['head_event_id']==ack['event_id']


def test_noncooperating_head_change_after_event_write_is_not_acknowledged(tmp_path):
    j=journal(tmp_path/'store');real=j._head;calls=0
    def changed(namespace):
        nonlocal calls
        calls+=1
        return real(namespace)if calls==1 else{'changed_by_noncooperating_writer':True}
    with patch.object(j,'_head',side_effect=changed),pytest.raises(JournalConflict):
        j.append_owner_intent(command())
    assert j.read_owner_intents(NS)['events']==[]


def test_lost_reply_after_head_rename_sync_failure_recovers_the_original_not_another_intent(tmp_path):
    j=journal(tmp_path/'store');real=j._sync
    def fault(path):
        if path.name=='heads':raise OSError('synthetic head directory sync failure')
        return real(path)
    with patch.object(j,'_sync',side_effect=fault),pytest.raises(OSError):j.append_owner_intent(command())
    saved=j.read_owner_intents(NS);assert saved['head_sequence']==1
    with patch.object(j,'clock',side_effect=AssertionError('retry clock')):
        ack=j.append_owner_intent(command())
    assert ack['event_id']==saved['head_event_id']
    assert j.read_owner_intents(NS)['head_sequence']==1
    assert list(j.root.rglob('.owner-*'))==[]
