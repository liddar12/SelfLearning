"""Pinned metadata validation replays the generic job state machine."""
import copy

import pytest

from selflearn_core import learning_jobs as dto
from selflearn_core.adapters import nfl_publication_delivery as D
from selflearn_core.probability_receipts import fingerprint

AUTHORITY=fingerprint('producer configured authority')
POLICY={'version':1,'kind':'nfl.pool_http_publication.v1','origin':'https://public.example.com','path':'/data/leg_pool.json'}
CODE=fingerprint('operator pinned server code declaration')


def example():
    spec,inputs,settings=D.definition(fingerprint('original envelope'),fingerprint('original manifest'),
        '2030-01-01T10:00:00Z',['2030-01-01T12:00:00Z'],POLICY,CODE,AUTHORITY)
    current=dto.complete_snapshot(spec,[],authority_id=AUTHORITY,
        registered_utc='2030-01-01T10:00:01Z',observed_utc='2030-01-01T10:00:02Z')
    return spec,inputs,settings,current


def test_definition_preserves_existing_v1_identity_bytes():
    spec,inputs,settings,_=example()
    expected=dto.seal_spec({'version':1,'kind':'selflearn.job_definition.v1',
        'namespace':{'domain':'nfl','task':'publication.recording','producer_id':'nfl2026'},
        'operation':'nfl.publication.record','input_object_id':fingerprint(inputs),
        'policy_object_id':fingerprint(settings),'code_id':CODE,
        'not_before_utc':'2030-01-01T10:00:00Z','not_after_utc':'2030-01-01T13:00:00.000000Z',
        'lease_seconds':60,'max_runtime_seconds':3600,'max_attempts':8,'max_steps':1},authority_id=AUTHORITY)
    assert dto.canonical(spec)==dto.canonical(expected)


@pytest.mark.parametrize('field,value',[
    ('state',{'status':'succeeded'}),('effective_status','succeeded'),('authority_id',fingerprint('other')),
    ('activation',True),('exactly_once_compute',True),('head_event_id',fingerprint('forged event')),
    ('observed_utc','2029-01-01T00:00:00Z'),('version',True)])
def test_full_snapshot_rejects_forged_state_and_claims(field,value):
    spec,_,_,current=example();current[field]=value
    with pytest.raises(ValueError):D.validate_snapshot(current,spec,AUTHORITY)


def test_progress_rejects_rollback_and_accepts_real_claim_history():
    spec,_,_,initial=example()
    command=dto.seal_command({'version':1,'kind':'selflearn.job_command.v1','job_id':spec['job_id'],
        'expected_head_event_id':None,'action':'claim','worker_id':'actual-worker',
        'lease_id':None,'object_id':None,'reason_code':None},authority_id=AUTHORITY)
    event=dto.make_event(spec,[],command,authority_id=AUTHORITY,
        registered_utc=initial['registered_utc'],recorded_utc='2030-01-01T10:00:03Z')
    claimed=dto.complete_snapshot(spec,[event],authority_id=AUTHORITY,
        registered_utc=initial['registered_utc'],observed_utc='2030-01-01T10:00:04Z')
    assert D.validate_progress(initial,claimed,spec,AUTHORITY)==claimed
    with pytest.raises(ValueError):D.validate_progress(claimed,initial,spec,AUTHORITY)
    altered=copy.deepcopy(claimed);altered['events'][0]['command']['worker_id']='forged-worker'
    with pytest.raises(ValueError):D.validate_snapshot(altered,spec,AUTHORITY)


@pytest.mark.parametrize('field,value',[
    ('authority_id',fingerprint('other')),('code_id',None),('version',True),
    ('settings',{'version':1}),('policy',{**POLICY,'origin':'https://other.example.com'})])
def test_declaration_requires_explicit_authority_policy_and_fixed_settings(field,value):
    declared={'version':2,'authority_id':AUTHORITY,'code_id':CODE,'policy':POLICY,'settings':D.settings(POLICY)}
    assert D.validate_declaration(declared,POLICY,AUTHORITY)==declared
    declared[field]=value
    with pytest.raises(ValueError):D.validate_declaration(declared,POLICY,AUTHORITY)
