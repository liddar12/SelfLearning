"""Actual pinned v2 queue routes; no network or model changes."""
import copy
from datetime import timedelta
from unittest.mock import patch

import pytest

from services.web import nfl_publication_jobs as W
from selflearn_core import learning_jobs as dto
from selflearn_core.adapters import nfl_publication_delivery as D
from selflearn_core.adapters.nfl import canonical_key
from selflearn_core.adapters.nfl_generation import MANIFEST_KEYS
from services.web.nfl_ingestion import finalize_generation
from selflearn_core.probability_receipts import fingerprint
from selflearn_core.store.learning_job_journal import LearningJobJournal
from selflearn_core.adapters.nfl_publication_journal import NFLPublicationJournal
from .publication_fixture import graph
from .test_nfl_publication import upload, post, transport_for, PUBLICATION, INGEST, OUTCOME, CORRECTIONS
from .test_publication_worker_routes import client_for, AUTHORITY, RUN, WORKER, ENV
from .fakes import TEST_KEY
from .test_nfl_publication_jobs import ports, CODE as WORKER_CODE

JOBS = '/api/v2/nfl/publication-jobs'
POLICY = '/api/v2/nfl/publication-job-policy'
CODE = fingerprint('explicit pinned test server code')


def setup(client, doc, sources):
    upload(client,doc,sources)
    declared=client.get(POLICY,headers={'Authorization':'Bearer '+PUBLICATION})
    assert declared.status_code==200, declared.text
    declaration=declared.json()
    D.validate_declaration(declaration,declaration['policy'],AUTHORITY)
    # graph fixture has exactly one accepted forecast; real producer supplies
    # its original finalized ACK accepted membership, not a fabricated forecast.
    spec,inputs,settings=D.definition(doc['envelope_id'],doc['generations'][0]['generation_id'],
        doc['generations'][0]['recorded_utc'],[r['kickoff_utc'] for r in doc['receipts']],
        declaration['policy'],declaration['code_id'],AUTHORITY)
    request={'version':2,'object_id':doc['envelope_id'],'expected_job_id':spec['job_id'],
             'expected_definition_id':spec['definition_id']}
    return spec,inputs,settings,request


def test_v2_original_completion_read_and_replay_ignore_current_policy_and_code(tmp_path):
    path=tmp_path/'v2.sqlite';doc,sources=graph();calls=[]
    fetch,_=transport_for(sources['leg_pool'],calls)
    with patch.object(W,'code_identity',return_value=CODE),client_for(path,fetch) as client:
        spec,inputs,settings,request=setup(client,doc,sources)
        absent=client.get(JOBS+'/'+spec['job_id'],headers={'Authorization':'Bearer '+PUBLICATION})
        assert absent.status_code==404
        queued=post(client,JOBS,request)
        assert queued.status_code==200,queued.text
        assert queued.json()['result'] is None and queued.json()['attestation'] is None
        assert calls==[]
        assert post(client,RUN,{'version':1},WORKER).json()['recording_complete'] is True
        done=client.get(JOBS+'/'+spec['job_id'],headers={'Authorization':'Bearer '+PUBLICATION})
        assert done.status_code==200,done.text
        view=done.json();assert view['snapshot']['effective_status']=='succeeded'
        assert view['result']['payload']['attestation_id']==view['attestation']['attestation_id']
        D.validate_progress(queued.json()['snapshot'],view['snapshot'],spec,AUTHORITY)
        generation=doc['generations'][0]
        members=view['attestation']['receipts']
        D.validate_delivery(view,spec,inputs,settings,members,generation,AUTHORITY)
        for target in ('result','proof_pool','proof_direction','proof_after_completion','input','snapshot'):
            altered=copy.deepcopy(view)
            if target=='result':
                altered['result']['payload']['model_adoption']=True
                altered['result']['object_id']=fingerprint(altered['result']['payload'])
            elif target=='input':
                altered['input']['payload']['object_id']=fingerprint('other generation')
                altered['input']['object_id']=fingerprint(altered['input']['payload'])
            elif target=='snapshot':
                altered['snapshot']['events'][-1]['command']['worker_id']='forged-worker'
            else:
                proof=altered['attestation']
                if target=='proof_pool':proof['source_pool_id']=fingerprint('other public pool')
                elif target=='proof_direction':proof['receipts'][0]['direction']='under'
                else:
                    # Otherwise valid original pool/member/proof clocks, still
                    # well before kickoff; hash resealing cannot move evidence
                    # later than the generic job's authoritative completion.
                    completed=dto.clock(view['snapshot']['events'][-1]['recorded_utc'])
                    proof['recorded_utc']=dto.stamp(completed+timedelta(seconds=1))
                proof['attestation_id']=fingerprint({k:v for k,v in proof.items() if k!='attestation_id'})
            reason='publication_job_result_before_proof' if target=='proof_after_completion' else None
            with pytest.raises(ValueError,match=reason):
                D.validate_delivery(altered,spec,inputs,settings,members,generation,AUTHORITY)
    changed={**ENV,'SELFLEARN_NFL_PUBLICATION_ORIGIN':'https://changed-policy.example.com'}
    with patch.object(W,'code_identity',side_effect=AssertionError('recovery must not scan current code')), \
         client_for(path,lambda *_:pytest.fail('status/recovery must not observe'),env=changed) as client:
        recovered=post(client,JOBS,request)
        assert recovered.status_code==200,recovered.text
        read=client.get(JOBS+'/'+spec['job_id'],headers={'Authorization':'Bearer '+PUBLICATION})
        assert read.status_code==200,read.text
        assert read.json()['attestation']==view['attestation']
        assert read.json()['result']==view['result']
    assert len(calls)==1


def test_v2_missing_pinned_definition_mismatch_never_writes_queue_objects(tmp_path):
    path=tmp_path/'mismatch.sqlite';doc,sources=graph()
    fetch,_=transport_for(sources['leg_pool'],[])
    with patch.object(W,'code_identity',return_value=CODE),client_for(path,fetch) as client:
        spec,_,_,request=setup(client,doc,sources)
        with NFLPublicationJournal(path) as journal:
            before=journal._db.execute('SELECT count(*) FROM receipt_journal_objects').fetchone()[0]
        for key in ('expected_job_id','expected_definition_id'):
            changed={**request,key:fingerprint('wrong '+key)}
            response=post(client,JOBS,changed)
            assert response.status_code==409,response.text
        with LearningJobJournal(path,authority_id=AUTHORITY) as jobs:
            assert jobs._db.execute('SELECT count(*) FROM learning_jobs_v1').fetchone()[0]==0
        with NFLPublicationJournal(path) as journal:
            assert journal._db.execute('SELECT count(*) FROM receipt_journal_objects').fetchone()[0]==before


def test_v2_proof_after_crash_is_read_without_claiming_completed_recording(tmp_path):
    path=tmp_path/'intermediate.sqlite';doc,sources=graph();calls=[]
    fetch,_=transport_for(sources['leg_pool'],calls)
    with patch.object(W,'code_identity',return_value=CODE),client_for(path,fetch) as client:
        spec,_,_,request=setup(client,doc,sources)
        assert post(client,JOBS,request).status_code==200
        # Real evidence route commits independently from job completion.
        proof=post(client,'/api/v1/nfl/publication-attestations',{'version':1,'object_id':doc['envelope_id']})
        assert proof.status_code==200,proof.text
        current=client.get(JOBS+'/'+spec['job_id'],headers={'Authorization':'Bearer '+PUBLICATION})
        assert current.status_code==200,current.text
        assert current.json()['snapshot']['effective_status']=='queued'
        assert current.json()['result'] is None
        assert current.json()['attestation']==proof.json()
    assert len(calls)==1


def test_v2_auth_exact_fields_disabled_and_no_side_effect_declaration(tmp_path):
    path=tmp_path/'scoped.sqlite';calls=[]
    with patch.object(W,'code_identity',return_value=CODE),client_for(path,lambda *_:calls.append('HTTP')) as client:
        for token in (TEST_KEY,INGEST,OUTCOME,CORRECTIONS,WORKER,''):
            assert client.get(POLICY,headers={'Authorization':'Bearer '+token}).status_code==401
            assert post(client,JOBS,{'version':2},token).status_code==401
        declaration=client.get(POLICY,headers={'Authorization':'Bearer '+PUBLICATION})
        assert declaration.status_code==200 and declaration.headers['cache-control']=='no-store'
        for bad in ({'version':True},{'version':2,'origin':'https://override.example.com'}):
            assert post(client,JOBS,bad).status_code==422
        assert client.get(POLICY+'?code_id=x',headers={'Authorization':'Bearer '+PUBLICATION}).status_code==422
    with LearningJobJournal(path,authority_id=AUTHORITY) as jobs:
        assert jobs._db.execute('SELECT count(*) FROM learning_jobs_v1').fetchone()[0]==0
    assert calls==[]
    with client_for(tmp_path/'disabled.sqlite',lambda *_:pytest.fail('HTTP'),
            env={**ENV,'SELFLEARN_PUBLICATION_WORKER_MODE':'off'}) as client:
        assert client.get(POLICY,headers={'Authorization':'Bearer '+PUBLICATION}).status_code==503


def test_v2_provider_read_failure_is_not_absence_or_registration(tmp_path):
    from contextlib import contextmanager
    from selflearn_core.store.supabase_store import StoreError
    class Broken:
        authority_id=AUTHORITY
        def find_job(self, _): raise StoreError(422)
        def register_job(self, _): pytest.fail('read error cannot register')
        def append_object(self, *_): pytest.fail('read error cannot write references')
    @contextmanager
    def pair(): yield Broken(),object()
    with client_for(tmp_path/'provider-error.sqlite',lambda *_:pytest.fail('HTTP'),pair=pair) as client:
        job_id=fingerprint('pinned missing-or-provider-failed')
        assert client.get(JOBS+'/'+job_id,headers={'Authorization':'Bearer '+PUBLICATION}).status_code==503
        response=post(client,JOBS,{'version':2,'object_id':fingerprint('original'),
            'expected_job_id':job_id,'expected_definition_id':fingerprint('definition')})
        assert response.status_code==503


def test_v2_partial_original_proof_keeps_accepted_and_late_partition(ports):
    jobs,journal,verifier,clock,calls,_,_=ports
    doc,sources=graph(kickoff='2030-01-01T13:00:00Z')
    second=copy.deepcopy(sources['leg_pool']['players'][0])
    second.update(game_id='G2',gsis_id='second-player')
    source=second['rungs'][0]['learning_receipt']
    source.update(game_id='G2',player_id='second-player')
    source['features']['correlation_tags']=['game:G2']
    source['contract_id']=fingerprint(list(canonical_key(source,2026)))
    source['receipt_id']=fingerprint({k:v for k,v in source.items() if k!='receipt_id'})
    sources['leg_pool']['players'].append(second)
    game={**sources['schedule']['games'][0],'game_id':'G2','kickoff_utc':'2030-01-01T14:00:00Z'}
    sources['schedule']['games'].append(game)
    row=copy.deepcopy(doc['receipts'][0])
    row.update(receipt_id=source['receipt_id'],learning_receipt=copy.deepcopy(source),kickoff_utc=game['kickoff_utc'])
    row['leg'].update(game_id='G2',gsis_id='second-player')
    doc['receipts'].append(row);gen=doc['generations'][0]
    gen['source_snapshots']={k:fingerprint(v) for k,v in sources.items()}
    gen['receipt_ids']=sorted(r['receipt_id'] for r in doc['receipts'])
    gen['generation_id']=fingerprint({k:gen[k] for k in MANIFEST_KEYS})
    for item in doc['receipts']:item['generation_id']=gen['generation_id']
    doc['envelope_id']=fingerprint({k:v for k,v in doc.items() if k!='envelope_id'})
    for value in sources.values():journal.append_object('source_snapshot',fingerprint(value),value)
    journal.append_object('generation',doc['envelope_id'],doc)
    ack=finalize_generation(journal,doc['envelope_id'])
    assert len(ack['receipts'])==2 and ack['rejections']==[]
    generation,members=W._generation(jobs,journal,doc['envelope_id'])
    spec,inputs,settings=D.definition(doc['envelope_id'],gen['generation_id'],gen['recorded_utc'],
        [r['event_start_utc'] for r in members.values()],verifier.policy,WORKER_CODE,jobs.authority_id)
    queued=W.enqueue_pinned(jobs,journal,doc['envelope_id'],spec['job_id'],spec['definition_id'],verifier.policy)
    assert queued['result'] is None and queued['attestation'] is None
    frozen=copy.deepcopy((journal.receipts(),journal.generations()))
    import json
    def fetch(url):
        calls.append(url)
        return 200,{'Content-Type':'application/json'},json.dumps(sources['leg_pool']).encode(),url
    verifier.transport=fetch
    clock.advance(3601)  # First game is late; second original event is pregame.
    status=W.run_once(jobs,journal,verifier,'partial-v2-worker',WORKER_CODE)
    assert status['recording_complete'] is True and status['publication_status']=='partial'
    view=W.delivery_view(jobs,journal,jobs.find_job(spec['job_id']))
    D.validate_delivery(view,spec,inputs,settings,members,generation,jobs.authority_id)
    assert view['snapshot']['effective_status']=='succeeded'
    proof=view['attestation'];result=view['result']['payload']
    assert proof['status']=='partial' and len(proof['receipts'])==len(proof['rejections'])==1
    assert proof['receipts'][0]['source_receipt_id']==source['receipt_id']
    assert proof['rejections'][0]['source_receipt_id']==doc['receipts'][0]['receipt_id']
    assert proof['rejections'][0]['reason']=='publication_not_preevent'
    assert result['recording_complete'] is True
    assert result['accepted_receipts']==result['rejected_receipts']==1
    assert all(result[key] is False for key in W.FALSE_FLAGS)
    clock.advance(3601)
    replay=W.enqueue_pinned(jobs,journal,doc['envelope_id'],spec['job_id'],spec['definition_id'],verifier.policy)
    assert replay['attestation']==proof and replay['result']==view['result']
    assert (journal.receipts(),journal.generations())==frozen and len(calls)==1
