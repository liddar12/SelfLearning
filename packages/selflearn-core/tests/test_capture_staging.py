"""Adapter contract/fault tests; real SQL is tested separately, no cloud claim."""
import copy

import pytest

from selflearn_core.store.capture_staging import CaptureStagingQuota, staging_namespace, validate_reservation
from selflearn_core.store.supabase_store import StoreError
from selflearn_core.probability_receipts import fingerprint

DEST, CHUNK = fingerprint('destination'), fingerprint('chunk')
NS = staging_namespace(destination_id=DEST, producer_id='nfl2026')


def ack(size=4):
    return {'version':1,'kind':'selflearn.capture_stage_reservation.v1','namespace_id':NS,'chunk_id':CHUNK,'raw_bytes':size,
        'reservation_id':fingerprint({'version':1,'namespace_id':NS,'chunk_id':CHUNK,'raw_bytes':size}),
        'reserved_total_bytes':size,'reserved_chunks':1,'recorded_utc':'2026-10-16T00:00:00Z',
        'scope':'staging_budget_only','quota_reserved':True,'learning_accepted':False,'durable_ack':False}


def test_explicit_scope_no_io_and_exact_rpc_then_frozen_staging_only_ack():
    calls, result = [], ack()
    def request(*args,**kwargs): calls.append((args,kwargs)); return result
    port=CaptureStagingQuota(request,namespace_id=NS)
    assert calls==[]
    out=port.reserve(CHUNK,4)
    assert out==result and out is not result
    result['scope']='tampered'
    assert out['scope']=='staging_budget_only'
    assert calls==[(('POST','/rest/v1/rpc/selflearn_reserve_capture_stage_v1'),
                   {'data':{'p_namespace_id':NS,'p_chunk_id':CHUNK,'p_raw_bytes':4}})]


@pytest.mark.parametrize('field,value',[('version',True),('scope','learning'),('namespace_id',DEST),('chunk_id',DEST),
    ('raw_bytes',True),('raw_bytes',5),('reservation_id',DEST),('quota_reserved',1),('learning_accepted',True),
    ('durable_ack',True),('reserved_total_bytes',True),('reserved_total_bytes',3),('reserved_chunks',0),
    ('recorded_utc','not a clock')])
def test_untrusted_rpc_result_never_becomes_staging_permission(field,value):
    result=ack();result[field]=value
    with pytest.raises(StoreError) as error:
        CaptureStagingQuota(lambda *a,**k:result,namespace_id=NS).reserve(CHUNK,4)
    assert error.value.status==503


@pytest.mark.parametrize('status',[409,422,429,503,400,502,True])
def test_bounded_backend_error_mapping_does_not_echo_payload(status):
    def request(*args,**kwargs): raise StoreError(status)
    with pytest.raises(StoreError) as error: CaptureStagingQuota(request,namespace_id=NS).reserve(CHUNK,4)
    assert error.value.status==(status if type(status) is int and status in(409,422,429,503) else 503)


def test_invalid_configuration_and_raw_limits_fail_before_io():
    for destination,producer in [('bad','nfl2026'),(DEST,''),(DEST,'../other'),(DEST,True)]:
        with pytest.raises(ValueError): staging_namespace(destination_id=destination,producer_id=producer)
    for request,namespace in [(None,NS),(lambda:None,'bad')]:
        with pytest.raises(ValueError): CaptureStagingQuota(request,namespace_id=namespace)
    def forbidden(*a,**k): raise AssertionError('must not request')
    port=CaptureStagingQuota(forbidden,namespace_id=NS)
    for chunk,size in [('bad',1),(CHUNK,0),(CHUNK,1048577),(CHUNK,True)]:
        with pytest.raises(ValueError): port.reserve(chunk,size)
    with pytest.raises(ValueError): validate_reservation({},namespace_id=NS,chunk_id=CHUNK,raw_bytes=4)
    malformed=ack();malformed['extra']='ignored?'
    with pytest.raises(ValueError): validate_reservation(malformed,namespace_id=NS,chunk_id=CHUNK,raw_bytes=4)


def test_lost_reply_is_failure_without_retry_or_budget_refund():
    calls=[]
    def request(*args,**kwargs):calls.append(args);raise TimeoutError('SECRET_DIAGNOSTICS')
    with pytest.raises(StoreError) as error: CaptureStagingQuota(request,namespace_id=NS).reserve(CHUNK,4)
    assert error.value.status==503 and 'SECRET' not in str(error.value)
    assert len(calls)==1
