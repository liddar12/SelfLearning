"""Explicit bounded-history profile; no truncation, refit or serving proof."""
import copy
from unittest.mock import patch

import pytest

from selflearn_core.adapters import nfl_file_feedback as F
from selflearn_core.probability_receipts import fingerprint
from test_nfl_file_feedback import graph, capture, reseal, PREGAME, POSTGAME, EVALUATED
import test_nfl_capture_labels as L


PROFILE=F.PARTITION_PROFILE


@pytest.mark.parametrize("profile",[True,False,1,{},[],"", "partitioned_repository_history_v1"])
def test_unknown_profiles_never_select_a_larger_snapshot(profile):
    with pytest.raises(ValueError,match="snapshot_profile_unknown"):
        F.prepare(graph(),observed_utc=PREGAME,snapshot_profile=profile)
    state=F.prepare(graph(),observed_utc=PREGAME)["state"]
    with pytest.raises(ValueError,match="snapshot_profile_unknown"):
        F.evaluate(state,graph(),as_of_utc=EVALUATED,snapshot_profile=profile)


def test_string_subclasses_and_input_fields_are_not_profile_authority():
    class Named(str):pass
    with pytest.raises(ValueError,match="snapshot_profile_unknown"):
        F.prepare(graph(),observed_utc=PREGAME,snapshot_profile=Named(PROFILE))
    source=graph();source["snapshot_profile"]=PROFILE
    for profile in(None,PROFILE):
        with pytest.raises(ValueError):F.prepare(source,observed_utc=PREGAME,snapshot_profile=profile)
    state=F.prepare(graph(),observed_utc=PREGAME)["state"]
    state["snapshot_profile"]=PROFILE;reseal(state)
    with pytest.raises(ValueError):F.prepare(graph(),previous=state,observed_utc=POSTGAME,snapshot_profile=PROFILE)


def test_same_evidence_preserves_v1_state_ids_import_clocks_and_complete_correction_history():
    source=graph();first=F.prepare(source,observed_utc=PREGAME)
    assert F.prepare(source,observed_utc=PREGAME,snapshot_profile=PROFILE)==first
    state=first["state"]
    for captures,clock in(([capture()],POSTGAME),([capture(20,day=20)],"2026-10-21T15:00:00Z"),
                         ([capture(80,day=21)],"2026-10-22T15:00:00Z"),([capture()],"2026-10-23T15:00:00Z")):
        expected=F.prepare(source,previous=state,captures=captures,observed_utc=clock)
        actual=F.prepare(source,previous=state,captures=captures,observed_utc=clock,snapshot_profile=PROFILE)
        assert actual==expected
        assert actual["state"]["source_imports"]==first["state"]["source_imports"]
        assert actual["state"]["receipt_imports"]==first["state"]["receipt_imports"]
        assert actual["state"]["revisions"][:len(state["revisions"])]==state["revisions"]
        state=actual["state"]
    assert len(state["revisions"])==12
    assert F.evaluate(state,source,as_of_utc="2026-10-23T15:00:01Z",snapshot_profile=PROFILE)==\
           F.evaluate(state,source,as_of_utc="2026-10-23T15:00:01Z")


def test_same_capture_rebinds_every_new_forecast_vintage_without_receipt_subsampling():
    small=graph(L.journal_fixture(vintages=1));large=graph()
    old=F.prepare(small,captures=[capture()],observed_utc=POSTGAME)["state"]
    expected=F.prepare(large,previous=old,captures=[capture()],observed_utc="2026-10-21T15:00:00Z")
    actual=F.prepare(large,previous=old,captures=[capture()],observed_utc="2026-10-21T15:00:00Z",snapshot_profile=PROFILE)
    assert actual==expected
    assert len(actual["state"]["receipts"])==len(actual["state"]["revisions"])==4
    assert actual["capture_results"][0]["new_revisions"]==2
    assert len(actual["state"]["capture_records"])==2


def test_aggregate_canonical_budget_is_exact_including_keys_escapes_separators_and_aliases():
    shared={"unicode":"\u2603", "empty":[], "number":1.25}
    source={"source_objects":[shared,shared],"generations":[[True,False,None,3]]}
    size=len(F._encoded(source))
    assert size<128*1024*1024
    with patch.object(F,"MAX_BYTES",size):
        cloned=F._graph(source,snapshot_profile=PROFILE)
    assert cloned==source and cloned is not source
    assert cloned["source_objects"][0] is not cloned["source_objects"][1]
    with patch.object(F,"MAX_BYTES",size-1),pytest.raises(ValueError,match="work_limit"):
        F._graph(source,snapshot_profile=PROFILE)
    # Both records individually fit. Repeating an alias still consumes the
    # cumulative encoded budget; one shared object is never charged just once.
    assert len(F._encoded(shared))+4<size-1


def test_full_document_depth_not_32_extra_levels_per_partition():
    item=None
    for _ in range(30):item=[item]
    source={"source_objects":[item],"generations":[]}
    assert F._graph(source,snapshot_profile=PROFILE)==F.plain(source)
    source["source_objects"][0]=[item]
    for profile in(None,PROFILE):
        with pytest.raises(ValueError,match="work_limit"):F._graph(source,snapshot_profile=profile)


def test_strict_record_json_cycle_and_aggregate_aliases_are_not_sanitized():
    cycle=[];cycle.append(cycle)
    for item in(float("nan"),float("inf"),1<<4097,(1,),{1:1},object(),cycle):
        with pytest.raises(ValueError):F._graph({"source_objects":[item],"generations":[]},snapshot_profile=PROFILE)
    class Root(dict):pass
    class Rows(list):pass
    for source in(Root(source_objects=[],generations=[]),{"source_objects":Rows(),"generations":[]},
                  {"source_objects":[],"generations":[],"ignored":0}):
        with pytest.raises(ValueError):F._graph(source,snapshot_profile=PROFILE)


def test_collection_limits_and_root_shape_are_checked_before_cloning_any_record():
    for key,maximum in F.GRAPH_LIST_LIMITS:
        source={"source_objects":[],"generations":[]};source[key]=[None]*(maximum+1)
        with patch.object(F,"plain",side_effect=AssertionError("cloned before collection bound")), \
                patch.object(F,"_plain_with_size",side_effect=AssertionError("cloned before collection bound")):
            with pytest.raises(ValueError,match="work_limit"):F._graph(source,snapshot_profile=PROFILE)
    state=F.prepare(graph(),observed_utc=PREGAME)["state"]
    for key,maximum in F.STATE_LIST_LIMITS:
        oversized={**state,key:[None]*(maximum+1)}
        with patch.object(F,"plain",side_effect=AssertionError("cloned before collection bound")), \
                patch.object(F,"_plain_with_size",side_effect=AssertionError("cloned before collection bound")):
            with pytest.raises(ValueError,match="work_limit"):F._state(oversized,snapshot_profile=PROFILE)


def test_complete_history_above_two_million_nodes_passes_only_explicit_profile():
    # Two genuine tiny generations with inert envelope metadata give >2M
    # expanded nodes at ~4 MiB, below the unmodified per-record node guard and
    # each producer request's 3 MiB byte limit. This is a synthetic capacity
    # test, not a claim that real production has this padding field.
    source=graph()
    for entry in source["generations"]:
        doc=entry["request"]["payload"]
        doc["capacity_padding"]=[0]*1001000
        doc["envelope_id"]=fingerprint({k:v for k,v in doc.items()if k!="envelope_id"})
        entry["request"]["object_id"]=doc["envelope_id"]
        assert len(F._encoded(entry["request"]))<3*1024*1024
    with pytest.raises(ValueError,match="work_limit"):F.prepare(source,observed_utc=PREGAME)
    prepared=F.prepare(source,observed_utc=PREGAME,snapshot_profile=PROFILE)
    state=prepared["state"]
    assert len(state["receipts"])==4 and len(state["generation_records"])==2
    assert state["revisions"]==[]
    for original,stored in zip(source["generations"],state["generation_records"]):
        assert stored["payload"]==original["request"]["payload"]
    with pytest.raises(ValueError,match="work_limit"):F._state(state)
    assert F._state(state,snapshot_profile=PROFILE)==state
    # Each logical record retains the old node guard: merging the same two
    # padding lists into ONE record must not acquire a larger per-object limit.
    excessive={"source_objects":[[0]*2000000],"generations":[]}
    with pytest.raises(ValueError,match="work_limit"):F._graph(excessive,snapshot_profile=PROFILE)


@pytest.mark.parametrize("change",["missing_generation","missing_source","extra","tampered_source"])
def test_complete_graph_checks_still_precede_shared_fitting(change):
    source=graph();state=F.prepare(source,observed_utc=PREGAME,snapshot_profile=PROFILE)["state"]
    bad=copy.deepcopy(source)
    if change=="missing_generation":bad["generations"].pop()
    elif change=="missing_source":bad["source_objects"].pop()
    elif change=="extra":bad["source_objects"].append({"version":1,"kind":"source_snapshot","object_id":fingerprint({"x":1}),"payload":{"x":1}})
    else:bad["source_objects"][0]["payload"]={"tampered":1}
    with patch.object(F.calibration,"walk_forward_report")as fitter:
        with pytest.raises(ValueError):F.evaluate(state,bad,as_of_utc=EVALUATED,snapshot_profile=PROFILE)
        fitter.assert_not_called()


@pytest.mark.parametrize("change",["receipt_clock","missing_partition","hash","clock_regressed"])
def test_prior_state_hash_membership_and_clock_checks_are_not_bypassed(change):
    source=graph();state=F.prepare(source,observed_utc=PREGAME)["state"]
    bad=copy.deepcopy(state);clock=POSTGAME
    if change=="receipt_clock":bad["receipt_imports"][0]["first_imported_utc"]="2026-10-16T00:00:00Z"
    elif change=="missing_partition":bad["generation_records"].pop()
    elif change=="hash":bad["sequence"]+=1
    else:clock="2026-10-16T00:00:00Z"
    if change!="hash":reseal(bad)
    before=copy.deepcopy(bad)
    with pytest.raises((ValueError,KeyError)):F.prepare(source,previous=bad,observed_utc=clock,snapshot_profile=PROFILE)
    assert bad==before
