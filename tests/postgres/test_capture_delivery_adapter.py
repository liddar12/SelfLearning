"""Actual raw NFL capture to delivery port to additive PostgreSQL transactions.

No native concurrency, HTTP hosting, scoped producer auth or real Storage.
Missing local WASM/NFL fixture dependencies fail explicitly, never skip.
"""
import copy
import os
import subprocess

import pytest

from selflearn_core.adapters.nfl_capture_delivery_journal import (
    NFLCaptureDeliveryJournal, make_capture_delivery_request, make_capture_delivery_binding,
)
from selflearn_core.adapters.nfl_capture_labels import bind_capture_labels
from selflearn_core.adapters.nfl_observed_resolution import resolve_observed
from selflearn_core.probability_receipts import fingerprint
from selflearn_core.store.receipt_journal import JournalConflict
from selflearn_core.store.supabase_journal import _canonical, _revision_envelope
from selflearn_core.store.supabase_store import StoreError
from test_adapter_integration import PostgresBridge, ROOT
from test_capture_outcomes_atomic import raw_producer, admit, stage, DELIVERED


class DeliveryBridge(PostgresBridge):
    def __init__(self):
        if not os.environ.get('SELFLEARN_PGLITE_MODULE'):
            raise RuntimeError('Set SELFLEARN_PGLITE_MODULE to an installed reviewed module')
        self.process=subprocess.Popen(['node','tests/postgres/capture_delivery_bridge.mjs'],cwd=ROOT,
            stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,encoding='utf-8',bufsize=1)
        self.calls,self.counter=[],0
        try:
            self.ready=self._read(timeout=30)
            if self.ready.get('ready') is not True or self.ready.get('engine')!='postgresql-wasm':
                raise RuntimeError('Actual additive delivery bridge did not initialize')
        except Exception:
            self.close()
            raise

    def _read(self,**kwargs):
        value=super()._read(**kwargs)
        self.last_response=value
        return value


def port(bridge):
    return NFLCaptureDeliveryJournal('https://offline.supabase.invalid','sb_secret_offline_delivery_fixture',transport=bridge,page_size=1)


@pytest.fixture
def journals(raw_producer):
    bridge=DeliveryBridge()
    try:
        journal,other=port(bridge),port(bridge)
        initial=raw_producer['initial']
        admit(journal,initial['source_objects'],[row['request']['payload'] for row in initial['generations']])
        yield journal,other,bridge
    finally:
        bridge.close()


def prepare(journal,captured):
    envelope,storage=stage(captured)
    request=make_capture_delivery_request(envelope,list(storage.pointers.values()),
                destination_id=fingerprint('offline-destination'),producer_id='nfl-test-producer')
    staged=bind_capture_labels(journal,envelope,storage.read)
    snapshot=journal.read_observed_snapshot()
    kernel=resolve_observed(snapshot['receipts'],staged['source_receipts'],staged['observed_legs'],staged['game_observations'],
             observed_utc=DELIVERED,existing_revisions=snapshot['revisions'],existing_observations=snapshot['observations'])
    binding,obs,rev=make_capture_delivery_binding(staged,kernel['observation_proposals'],kernel['proposals'])
    return request,binding,obs,rev,snapshot


def commit(journal,prepared):
    request,binding,obs,rev,snapshot=prepared
    return journal.commit_capture_delivery(request,binding,obs,rev,watermark=snapshot['watermark'])


def raw_commit(bridge,prepared,**replace):
    request,binding,obs,rev,snapshot=copy.deepcopy(prepared)
    data={'p_request_json':_canonical(request),'p_binding_json':_canonical(binding),
          'p_batch_json':_canonical({'version':1,'observations':obs,'revisions':[_revision_envelope(**row) for row in rev]}),
          'p_watermark':snapshot['watermark']}
    data.update(replace)
    return bridge.request('POST','/rest/v1/rpc/selflearn_journal_commit_capture_delivery_v1',data=data)


def test_actual_original_lost_reply_recovers_after_new_vintage_not_reinterpreted(journals,raw_producer):
    journal,other,bridge=journals
    prepared=prepare(journal,raw_producer['captures'][0])
    original=commit(journal,prepared)
    assert original['status']=='accepted'
    assert (original['added_observations'],original['added_revisions'])==(4,4)
    assert len(original['groups'][0]['receipt_ids'])==4
    assert journal.get_capture_delivery(prepared[0]['delivery_id'])==original
    complete=raw_producer['complete']
    admit(other,complete['source_objects'],[row['request']['payload'] for row in complete['generations']])
    expanded=prepare(journal,raw_producer['captures'][0])
    assert len(expanded[1]['groups'][0]['receipt_ids'])==6
    recovered=commit(journal,expanded)
    assert _canonical(recovered)==_canonical(original)
    assert len(journal.read_observed_snapshot()['observations'])==4
    new_capture=commit(journal,prepare(journal,raw_producer['captures'][1]))
    assert new_capture['delivery_id']!=original['delivery_id']
    assert len(new_capture['groups'][0]['receipt_ids'])==6
    assert (new_capture['added_observations'],new_capture['added_revisions'])==(6,2)


def test_actual_withheld_same_fence_result_is_original_and_ledger_clock_separate(journals,raw_producer):
    journal,_,_=journals
    prepared=prepare(journal,raw_producer['captures'][0])
    request,binding,obs,rev,snapshot=prepared
    held=copy.deepcopy(binding)
    held['groups'][0].update(status='withheld',reason_codes=['kernel_group_incomplete'],observation_ids=[],revision_ids=[])
    original=journal.commit_capture_delivery(request,held,[],[],watermark=snapshot['watermark'])
    assert original['status']=='withheld' and original['added_observations']==original['added_revisions']==0
    assert original['watermark']==snapshot['watermark']
    assert original['delivery_seq']=='1'
    assert commit(journal,prepared)==original
    assert journal.read_observed_snapshot()['watermark']==snapshot['watermark']


def test_actual_request_identity_conflict_and_provenance_wrong_capture_reject_before_writes(journals,raw_producer):
    journal,_,bridge=journals
    prepared=prepare(journal,raw_producer['captures'][0])
    other=prepare(journal,raw_producer['captures'][1])
    with pytest.raises(StoreError) as err:
        raw_commit(bridge,other,p_request_json=_canonical(prepared[0]))
    assert err.value.status==422,bridge.last_response
    assert journal.read_observed_snapshot()['observations']==[]
    original=commit(journal,prepared)
    altered=copy.deepcopy(prepared[0]);altered['raw_pointers'][0]['chunks'][0]['chunk_id']=fingerprint('wrong')
    with pytest.raises(StoreError): raw_commit(bridge,prepared,p_request_json=_canonical(altered))
    assert journal.get_capture_delivery(original['delivery_id'])==original


def test_actual_member_omission_stale_CAS_and_bad_revision_leave_no_delivery_or_evidence(journals,raw_producer):
    journal,other,bridge=journals
    prepared=prepare(journal,raw_producer['captures'][0])
    request,binding,obs,rev,snapshot=prepared
    missing=copy.deepcopy(binding);missing['groups']=[]
    missing['membership_id']=fingerprint({'binding_policy':'nfl.capture_labels_v1','groups':[]})
    with pytest.raises(StoreError) as err: raw_commit(bridge,prepared,p_binding_json=_canonical(missing))
    assert err.value.status==422,bridge.last_response
    bad=copy.deepcopy(rev);bad[0]['resolution'].pop('measurement_id')
    with pytest.raises(StoreError): raw_commit(bridge,(request,binding,obs,bad,snapshot))
    after=journal.read_observed_snapshot()
    assert after['watermark']==snapshot['watermark'] and after['observations']==[] and after['revisions']==[]
    assert journal.get_capture_delivery(request['delivery_id']) is None
    complete=raw_producer['complete']
    admit(other,complete['source_objects'],[row['request']['payload'] for row in complete['generations']])
    with pytest.raises(JournalConflict): journal.commit_capture_delivery(request,binding,obs,rev,watermark=snapshot['watermark'])
    assert journal.get_capture_delivery(request['delivery_id']) is None
