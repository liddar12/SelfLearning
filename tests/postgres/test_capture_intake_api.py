"""Actual NFL raw capture→ASGI→PostgreSQL quota AND delivery ledger.

Supabase Storage is explicitly FAKE/private in memory. This is not a native
multi-session, network HTTP, hosted credential/deployment or provider-auth test.
"""
import copy
from contextlib import contextmanager
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
from fastapi.testclient import TestClient

from services.web.application import create_app
from services.web.nfl_capture_intake import CaptureReceiver
from selflearn_core.adapters.nfl_capture_delivery_journal import make_capture_delivery_request
from selflearn_core.probability_receipts import fingerprint
from selflearn_core.store.capture_staging import CaptureStagingQuota, staging_namespace
from selflearn_core.store.supabase_evidence import SupabaseEvidenceStore
from selflearn_core.store.supabase_store import StoreConflict, StoreError
from test_adapter_integration import PostgresBridge, ROOT
from test_capture_delivery_adapter import port
from test_capture_outcomes_atomic import raw_producer, admit, stage

sys.path.insert(0,str(ROOT/'tests'))
try:
    from hosted.fakes import FakeStore, TEST_KEY
finally: sys.path.pop(0)

CAPTURE_KEY='fixture-only-nfl-capture-api-key-'+'c'*32
GENERATION_KEY='fixture-only-nfl-generation-api-key-'+'g'*32
ENV={'SELFLEARN_OPERATOR_TOKEN':TEST_KEY,'SELFLEARN_NFL_INGEST_TOKEN':GENERATION_KEY,'SELFLEARN_NFL_OUTCOME_INGEST_TOKEN':CAPTURE_KEY}
DEST=fingerprint({'base_url':'https://self-learning-beige.vercel.app'})
CHUNKS,DELIVERIES='/api/v1/nfl/capture-chunks','/api/v1/nfl/capture-deliveries'


class IntakeBridge(PostgresBridge):
    def __init__(self):
        if not os.environ.get('SELFLEARN_PGLITE_MODULE'): raise RuntimeError('Set SELFLEARN_PGLITE_MODULE to an installed reviewed module')
        self.process=subprocess.Popen(['node','tests/postgres/capture_intake_bridge.mjs'],cwd=ROOT,
            stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,encoding='utf-8',bufsize=1)
        self.calls,self.counter=[],0
        try:
            self.ready=self._read(timeout=30)
            if self.ready.get('ready') is not True or self.ready.get('engine')!='postgresql-wasm':
                raise RuntimeError('Actual capture intake bridge did not initialize')
        except Exception:
            self.close();raise

    def request(self,method,path,*,data):
        self.counter+=1
        request={'id':self.counter,'method':method,'path':path,'data':data}
        self.calls.append(copy.deepcopy(request))
        self.process.stdin.write(json.dumps(request,allow_nan=False)+'\n');self.process.stdin.flush()
        response=self._read();self.last_response=response
        if response.get('id')!=self.counter: raise RuntimeError('Bridge response identity mismatch')
        if response.get('ok') is not True:
            if response.get('code')=='PT409': raise StoreConflict()
            raise StoreError({'PT422':422,'PT429':429,'PT503':503,'42501':403}.get(response.get('code'),503))
        return response['result']

    def provision(self,namespace_id,*,max_total_bytes=64*1024*1024,max_chunks=65536,enabled=True):
        return self.request('TEST_ADMIN','/test/provision-capture-staging',data={
            'namespace_id':namespace_id,'max_total_bytes':max_total_bytes,'max_chunks':max_chunks,'enabled':enabled})

    def staging_summary(self,namespace_id):
        return self.request('TEST_ADMIN','/test/capture-staging-summary',data={'namespace_id':namespace_id})


class PrivateStorageFixture:
    """Strict path/private-readback fake, not durable Supabase Storage."""
    def __init__(self,events): self.events,self.calls,self.objects=events,[],{}
    def request(self,method,path,**kwargs):
        self.calls.append((method,path,kwargs));self.events.append('storage-'+method)
        if path=='/storage/v1/bucket/private-evidence': return {'id':'private-evidence','public':False}
        if method=='POST':
            assert path.startswith('/storage/v1/object/private-evidence/')
            key=path.removeprefix('/storage/v1/object/')
            if key in self.objects: raise StoreError(409)
            self.objects[key]=kwargs['raw'];return {'untrusted_upload_ack':True}
        assert method=='GET' and path.startswith('/storage/v1/object/authenticated/private-evidence/')
        key=path.removeprefix('/storage/v1/object/authenticated/')
        if key not in self.objects: raise StoreError(404)
        return self.objects[key]


@contextmanager
def intake_context(raw_producer,*,producer_id='nfl2026',destination_id=DEST,maximum=64*1024*1024,max_chunks=65536,
                   admit_receipts=True,journal_transport=None):
    bridge=IntakeBridge()
    try:
        namespace=staging_namespace(destination_id=destination_id,producer_id=producer_id)
        bridge.provision(namespace,max_total_bytes=maximum,max_chunks=max_chunks)
        events=[]
        storage=PrivateStorageFixture(events)
        def quota_request(method,path,*,data): events.append('quota');return bridge.request(method,path,data=data)
        quota=CaptureStagingQuota(quota_request,namespace_id=namespace)
        evidence=SupabaseEvidenceStore(storage.request,bucket='private-evidence',namespace='nfl-'+namespace[7:31])
        journal,other=port(journal_transport or bridge),port(bridge)
        if admit_receipts:
            initial=raw_producer['initial']
            admit(other,initial['source_objects'],[row['request']['payload'] for row in initial['generations']])
        receiver=CaptureReceiver(journal,evidence,quota,destination_id=destination_id,producer_id=producer_id)
        @contextmanager
        def factory(): yield receiver
        with TestClient(create_app(store=FakeStore(),env=ENV,capture_factory=factory)) as client:
            yield {'client':client,'journal':journal,'other':other,'bridge':bridge,'storage':storage,'quota':quota,
                   'receiver':receiver,'namespace_id':namespace,'destination_id':destination_id,'producer_id':producer_id,'events':events}
    finally: bridge.close()


def post(client,path,value):
    return client.post(path,json=value,headers={'Authorization':'Bearer '+CAPTURE_KEY})


def upload_capture(context,captured):
    envelope,staged=stage(captured)
    for dto in staged.chunks.values():
        response=post(context['client'],CHUNKS,dto)
        assert response.status_code==200,response.text
        assert response.json()['durable_ack'] is False and response.json()['learning_accepted'] is False
    return make_capture_delivery_request(envelope,list(staged.pointers.values()),
              destination_id=context['destination_id'],producer_id=context['producer_id'])


def test_actual_API_quota_before_storage_full_correction_restore_VOID_and_pinned_history(raw_producer):
    with intake_context(raw_producer) as context:
        journal=context['other'];original=copy.deepcopy(journal.receipts());first=None
        for index in (0,1,2,0,3,4):
            request=upload_capture(context,raw_producer['captures'][index])
            response=post(context['client'],DELIVERIES,request)
            assert response.status_code==200,response.text
            result=response.json()['result']
            assert result['durable'] is True and result['source_authentication']=='not_performed'
            fetched=context['client'].get(DELIVERIES+'/'+request['delivery_id'],headers={'Authorization':'Bearer '+CAPTURE_KEY})
            assert fetched.status_code==200 and fetched.json()==result
            if first is None: first=result
        final=journal.read_observed_snapshot()
        assert len(final['observations'])==20 and len(final['revisions'])==16
        assert journal.receipts()==original
        assert all(row['resolution']['status']=='VOID' for row in final['revisions'][-4:])
        pinned=journal._load_observed_snapshot(first['watermark'])
        assert len(pinned['observations'])==len(pinned['revisions'])==4
        assert all(row['resolution']['hit'] is True for row in pinned['revisions'])
        assert context['events'][0]=='quota' and context['events'].index('quota')<context['events'].index('storage-POST')
        usage=context['bridge'].staging_summary(context['namespace_id'])
        assert usage['reserved_chunks']==len(context['storage'].objects)
        assert usage['reserved_total_bytes']==sum(len(raw) for raw in context['storage'].objects.values())


def test_actual_API_exhausted_quota_has_no_private_write_or_learning_ack(raw_producer):
    with intake_context(raw_producer,maximum=1) as context:
        from selflearn_core.evidence_chunks import make_chunk
        response=post(context['client'],CHUNKS,make_chunk(b'over budget'))
        assert response.status_code==429,response.text
        assert context['storage'].calls==[]
        usage=context['bridge'].staging_summary(context['namespace_id'])
        assert usage['reserved_chunks']==usage['reserved_total_bytes']==0


def test_actual_API_lost_delivery_response_recovers_original_after_new_members_without_raw_reads(raw_producer):
    with intake_context(raw_producer) as context:
        journal,bridge=context['journal'],context['bridge']
        original_request=journal._request
        lost=False
        def lose_once(method,path,**kwargs):
            nonlocal lost
            result=original_request(method,path,**kwargs)
            if path.endswith('selflearn_journal_commit_capture_delivery_v1') and not lost:
                lost=True;raise TimeoutError('SECRET_AFTER_REAL_COMMIT')
            return result
        journal._request=lose_once
        request=upload_capture(context,raw_producer['captures'][0])
        response=post(context['client'],DELIVERIES,request)
        assert response.status_code==503 and 'SECRET' not in response.text
        original=context['other'].get_capture_delivery(request['delivery_id'])
        complete=raw_producer['complete']
        admit(context['other'],complete['source_objects'],[row['request']['payload'] for row in complete['generations']])
        calls=len(context['storage'].calls)
        response=post(context['client'],DELIVERIES,request)
        assert response.status_code==200,response.text
        assert response.json()['status']=='recovered_original' and response.json()['result']==original
        assert len(original['groups'][0]['receipt_ids'])==4 and len(context['other'].receipts())==6
        assert len(context['storage'].calls)==calls
