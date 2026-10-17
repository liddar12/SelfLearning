"""Actual raw producer→internal delivery job→atomic PostgreSQL result ledger.

Private chunks remain a named in-memory fixture; no hosted Storage, HTTP,
native concurrent database connections or producer authentication are implied.
"""
import base64
import copy
import hashlib

import pytest

from selflearn_core.adapters.nfl_capture_delivery_job import commit_capture_delivery
from selflearn_core.adapters.nfl_capture_delivery_journal import make_capture_delivery_request
from selflearn_core.probability_receipts import fingerprint
from selflearn_core.store.supabase_store import StoreError
from test_capture_delivery_adapter import DeliveryBridge, journals, port, raw_producer
from test_capture_outcomes_atomic import stage, admit, DELIVERED


def intent(captured):
    envelope,storage=stage(captured)
    request=make_capture_delivery_request(envelope,list(storage.pointers.values()),
                destination_id=fingerprint('job-test-destination'),producer_id='nfl-job-test-producer')
    return request,storage


def run(journal,request,storage,**kwargs):
    return commit_capture_delivery(journal,request,storage.read,clock=lambda:DELIVERED,**kwargs)


def forbidden(*args): raise AssertionError('original recovery must precede clock/raw callbacks')


def test_actual_lost_response_then_new_members_recovers_original_before_clock_or_raw(journals,raw_producer):
    journal,other,bridge=journals
    request,storage=intent(raw_producer['captures'][0])
    class LostReply:
        lost=False
        def request(self,method,path,*,data):
            result=bridge.request(method,path,data=data)
            if path.endswith('selflearn_journal_commit_capture_delivery_v1') and not self.lost:
                self.lost=True
                raise TimeoutError('test-only lost response after actual transaction')
            return result
    transport=LostReply();journal=port(transport)
    with pytest.raises(StoreError) as error: run(journal,request,storage)
    assert error.value.status==503
    original=other.get_capture_delivery(request['delivery_id'])
    assert (original['added_observations'],original['added_revisions'])==(4,4)
    complete=raw_producer['complete']
    admit(other,complete['source_objects'],[row['request']['payload'] for row in complete['generations']])
    recovered=commit_capture_delivery(journal,request,forbidden,clock=forbidden)
    assert recovered['status']=='recovered_original' and recovered['result']==original
    assert len(recovered['result']['groups'][0]['receipt_ids'])==4
    assert len(other.receipts())==6
    assert len(other.read_observed_snapshot()['observations'])==4


def test_actual_CAS_expansion_rebinds_full_six_receipt_group_and_raw_hashes(journals,raw_producer):
    journal,other,bridge=journals
    request,storage=intent(raw_producer['captures'][0])
    original=journal.commit_capture_delivery
    attempted=[]
    def racing_commit(request,binding,observations,proposals,*,watermark):
        attempted.append((len(observations),watermark))
        if len(attempted)==1:
            complete=raw_producer['complete']
            admit(other,complete['source_objects'],[row['request']['payload'] for row in complete['generations']])
        return original(request,binding,observations,proposals,watermark=watermark)
    journal.commit_capture_delivery=racing_commit
    result=run(journal,request,storage)
    assert result['attempts']==2
    assert [n for n,_ in attempted]==[4,6]
    assert attempted[0][1]!=attempted[1][1]
    assert len(storage.calls)==10
    assert len(result['result']['groups'][0]['receipt_ids'])==6
    assert (result['result']['added_observations'],result['result']['added_revisions'])==(6,6)


@pytest.mark.parametrize('counts',[(0,0),(0,4),(4,0)])
def test_actual_port_refuses_partial_or_zero_ACK_then_recovers_true_original(journals,raw_producer,counts):
    _,other,bridge=journals
    request,storage=intent(raw_producer['captures'][0])
    class BadReply:
        def request(self,method,path,*,data):
            result=bridge.request(method,path,data=data)
            if path.endswith('selflearn_journal_commit_capture_delivery_v1'):
                result=copy.deepcopy(result)
                result.update(added_observations=counts[0],added_revisions=counts[1])
                if sum(counts)==0: result['watermark']=result['input_watermark']
            return result
    with pytest.raises(StoreError) as error: run(port(BadReply()),request,storage)
    assert error.value.status==502
    stored=other.get_capture_delivery(request['delivery_id'])
    assert (stored['added_observations'],stored['added_revisions'])==(4,4)
    recovered=commit_capture_delivery(other,request,forbidden,clock=forbidden)
    assert recovered['status']=='recovered_original' and recovered['result']==stored


def unknown_measurement(captured):
    """Reseal a missing cell capture, without producer-normalized label input."""
    value=copy.deepcopy(captured)
    rebuilt={}
    for ref in value['manifest']['sources']:
        observation=value['observations'][ref['observation_id']]
        if observation['profile']=='nflverse_stats_player_v1':
            raw=base64.b64decode(value['raw'].pop(observation['raw_sha256']),validate=True)
            changed=raw.replace(b',80\r\n',b',NA\r\n')
            assert changed!=raw
            raw_id='sha256:'+hashlib.sha256(changed).hexdigest()
            value['raw'][raw_id]=base64.b64encode(changed).decode()
            observation.update(raw_sha256=raw_id,raw_bytes=len(changed))
        oid=fingerprint(observation);rebuilt[oid]=observation;ref['observation_id']=oid
    value['observations']=rebuilt
    value['manifest']['capture_id']=fingerprint({key:item for key,item in value['manifest'].items() if key!='capture_id'})
    return value


def test_actual_withheld_and_empty_results_are_explicit_and_recoverable(journals,raw_producer):
    journal,_,_=journals
    request,storage=intent(unknown_measurement(raw_producer['captures'][0]))
    before=journal.read_observed_snapshot()['watermark']
    held=run(journal,request,storage)['result']
    assert held['status']=='withheld' and held['added_observations']==held['added_revisions']==0
    assert held['watermark']==before
    assert commit_capture_delivery(journal,request,forbidden,clock=forbidden)['result']==held
    bridge=DeliveryBridge()
    try:
        empty=port(bridge);request,storage=intent(raw_producer['captures'][0])
        result=run(empty,request,storage)['result']
        assert result['status']=='no_supported_membership'
        assert result['groups']==[] and result['watermark']=='0' and result['delivery_seq']=='1'
        assert commit_capture_delivery(empty,request,forbidden,clock=forbidden)['result']==result
    finally: bridge.close()


def test_actual_tamper_metadata_and_bound_failures_create_no_evidence_or_result(journals,raw_producer):
    journal,_,bridge=journals
    request,storage=intent(raw_producer['captures'][0])
    before=journal.read_observed_snapshot()['watermark']
    malformed=copy.deepcopy(request);malformed['capture_id']=fingerprint('wrong')
    calls=len(bridge.calls)
    with pytest.raises(ValueError): run(journal,malformed,storage)
    assert len(bridge.calls)==calls
    bad_pointer=copy.deepcopy(request)
    bad_pointer['raw_pointers'][0]['chunks'][0]['chunk_id']=fingerprint('missing-request-chunk')
    with pytest.raises((ValueError,RuntimeError)):
        run(journal,bad_pointer,storage)
    with pytest.raises(ValueError): commit_capture_delivery(journal,request,lambda _:iter([b'bad']),clock=lambda:DELIVERED)
    with pytest.raises(ValueError): run(journal,request,storage,max_attempts=True)
    with pytest.raises(ValueError): run(journal,request,storage,max_total_bytes=64*1024*1024+1)
    after=journal.read_observed_snapshot()
    assert after['watermark']==before and after['observations']==[] and after['revisions']==[]
    assert journal.get_capture_delivery(request['delivery_id']) is None
