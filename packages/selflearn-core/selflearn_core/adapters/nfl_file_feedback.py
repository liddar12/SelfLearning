"""Prepared NFL feedback state for a serialized, repository-backed Actions job.

No IO, credentials, SQL, serving write or model adoption. The caller supplies the
complete frozen source graph, prior state, exact raw capture readers and its
clock. The returned state is NOT durable until the controller publishes it.
Generation ACK-shaped partitions are internal staging inputs for the unchanged
calibration kernel, never acknowledgements of hosted persistence/publication.
"""
from collections import Counter
import copy
import json
import math

from . import nfl_generation as generation
from . import nfl_capture_labels as labels
from . import nfl_observed_resolution as outcomes
from . import nfl_calibration as calibration
from .nfl import adapt_prospective
from .nfl_observed_journal import _validate_fences,_validate_commit
from ..outcome_revisions import validate_journal_resolution
from ..probability_receipts import fingerprint,instant,validate_probability_receipt
from ..store.supabase_journal import SupabaseReceiptJournal

NS={"domain":"nfl","task":"nfl.parlay_leg","producer_id":"nfl2026"}
BASIS="prepared_repository_internal_evidence_not_hosted_ACK_or_publication"
MAX_BYTES=128*1024*1024
MAX_RECEIPTS,MAX_GENERATIONS,MAX_REVISIONS=50000,4096,200000
FLAGS={"durable_ack":False,"working_live_loop":False,"model_adoption":False,
       "public_serving_verified":False,"provider_authenticated":False,"accuracy_improvement_verified":False}
STATE_KEYS={"version","kind","namespace","basis","sequence","previous_state_id","updated_utc",
    "source_object_ids","source_imports","receipts","receipt_imports","generation_records","observation_records","revisions","capture_records","state_id"}


def need(value,reason="invalid_file_feedback"):
    if not value:raise ValueError(reason)


def _encoded(value):return json.dumps(value,sort_keys=True,separators=(",",":"),ensure_ascii=True,allow_nan=False).encode()


def plain(value):
    """Snapshot bounded strict JSON, counting expanded aliases before encoding."""
    nodes,characters=0,0
    def visit(item,depth):
        nonlocal nodes,characters
        nodes+=1;need(nodes<=2000000 and depth<=32,"file_feedback_work_limit")
        kind=type(item)
        if kind is str:
            characters+=len(item);need(characters<=MAX_BYTES,"file_feedback_work_limit")
            return item
        if item is None or kind is bool:return item
        if kind is int:need(item.bit_length()<=4096,"file_feedback_work_limit");return item
        if kind is float:need(math.isfinite(item));return item
        if kind is list:return [visit(x,depth+1)for x in item]
        if kind is dict:
            need(all(type(k) is str for k in item));return {visit(k,depth+1):visit(v,depth+1)for k,v in item.items()}
        raise ValueError("invalid_file_feedback_json")
    result=visit(value,0);need(len(_encoded(result))<=MAX_BYTES,"file_feedback_work_limit");return result


def digest(value):need(type(value) is str and generation.HASH.fullmatch(value));return value


def _sealed(value):return {**value,"state_id":fingerprint(value)}


def _state(value):
    value=plain(value)
    need(type(value) is dict and set(value)==STATE_KEYS and type(value["version"]) is int and value["version"]==1
         and value["kind"]=="nfl.file_feedback_state.v1"and value["namespace"]==NS and value["basis"]==BASIS)
    need(value["state_id"]==fingerprint({k:v for k,v in value.items()if k!="state_id"}),"file_feedback_state_hash_mismatch")
    need(type(value["sequence"]) is int and 1<=value["sequence"]<=1000000000)
    if value["previous_state_id"] is not None:digest(value["previous_state_id"])
    observed=instant(value["updated_utc"])
    for key,maximum in(("receipts",MAX_RECEIPTS),("receipt_imports",MAX_RECEIPTS),("generation_records",MAX_GENERATIONS),
        ("observation_records",MAX_REVISIONS),("revisions",MAX_REVISIONS),("source_object_ids",3*MAX_GENERATIONS),
        ("source_imports",3*MAX_GENERATIONS),("capture_records",4096)):
        need(type(value[key]) is list and len(value[key])<=maximum,"file_feedback_work_limit")
    source_ids=set(value["source_object_ids"])
    need(value["source_object_ids"]==sorted(source_ids))
    for oid in value["source_object_ids"]:digest(oid)
    source_imports={}
    for row in value["source_imports"]:
        need(type(row) is dict and set(row)=={"object_id","first_imported_utc"})
        oid=digest(row["object_id"]);need(oid in source_ids and oid not in source_imports)
        clock=instant(row["first_imported_utc"]);need(clock<=observed);source_imports[oid]=clock
    need(set(source_imports)==source_ids)
    by_receipt={}
    for row in value["receipts"]:
        validate_probability_receipt(row)
        need(row["domain"]==NS["domain"]and row["task"]==NS["task"]and row["receipt_id"]not in by_receipt)
        need(instant(row["first_seen_utc"])<=observed)
        by_receipt[row["receipt_id"]]=row
    need(len({r["source_receipt_id"]for r in by_receipt.values()})==len(by_receipt))
    imported=set()
    for row in value["receipt_imports"]:
        need(type(row) is dict and set(row)=={"receipt_id","first_imported_utc"}and row["receipt_id"]in by_receipt and row["receipt_id"]not in imported)
        need(instant(by_receipt[row["receipt_id"]]["first_seen_utc"])<=instant(row["first_imported_utc"])<=observed)
        imported.add(row["receipt_id"])
    need(imported==set(by_receipt))
    known_generations,accepted=set(),set()
    for row in value["generation_records"]:
        need(type(row) is dict and set(row)=={"object_id","payload","ack","recorded_utc"}and row["object_id"]not in known_generations)
        doc=row["payload"];need(type(doc) is dict and doc.get("envelope_id")==row["object_id"]and row["object_id"]==fingerprint({k:v for k,v in doc.items()if k!="envelope_id"}))
        SupabaseReceiptJournal._ack(row["ack"],row["object_id"],doc)
        need(len(doc.get("generations",[]))==1)
        manifest=doc["generations"][0]
        need(instant(manifest["recorded_utc"])<=instant(row["recorded_utc"])<=observed)
        need(all(oid in source_imports and source_imports[oid]<=instant(row["recorded_utc"])
                 for oid in manifest["source_snapshots"].values()))
        for member in row["ack"]["receipts"]:
            stored=by_receipt.get(member["receipt_id"])
            need(stored is not None and stored["source_receipt_id"]==member["source_receipt_id"])
            accepted.add(member["receipt_id"])
        known_generations.add(row["object_id"])
    need(accepted==set(by_receipt),"file_feedback_generation_partition_missing")
    revision_ids=set()
    for row in value["revisions"]:
        need(type(row) is dict and set(row)=={"version","revision_id","supersedes","reason","resolution","recorded_utc"}
             and type(row["version"]) is int and row["version"]==1 and row["revision_id"]not in revision_ids)
        validate_journal_resolution(by_receipt[row["resolution"]["receipt_id"]],row["resolution"])
        need(instant(row["resolution"]["resolved_utc"])<=instant(row["recorded_utc"])<=observed)
        revision_ids.add(row["revision_id"])
    observations=[];ids=set()
    for row in value["observation_records"]:
        need(type(row) is dict and set(row)=={"observation_id","payload","recorded_utc"}
             and row["observation_id"]==row["payload"].get("observation_id")and row["observation_id"]not in ids)
        need(instant(row["payload"]["observed_utc"])<=instant(row["recorded_utc"])<=observed)
        ids.add(row["observation_id"]);observations.append(row["payload"])
    _validate_fences(value["receipts"],value["revisions"],observations,observed)
    captures=set()
    for row in value["capture_records"]:
        need(type(row) is dict and set(row)=={"capture_id","membership_id","recorded_utc"})
        key=(digest(row["capture_id"]),digest(row["membership_id"]));need(key not in captures)
        need(instant(row["recorded_utc"])<=observed);captures.add(key)
    return value


class _View:
    def __init__(self,state,objects):self.state,self.objects=state,objects
    def get_object(self,oid):return self.objects.get(oid)
    def read_observed_snapshot(self):
        # A LOCAL logical fence for the pure binding kernel, never a DB token.
        return {"version":1,"watermark":str(self.state["sequence"]),"receipts":self.state["receipts"],
            "generation_records":self.state["generation_records"],"revisions":self.state["revisions"],
            "observations":[r["payload"]for r in self.state["observation_records"]],"observation_records":self.state["observation_records"]}


def prepare(graph,*,previous=None,captures=(),observed_utc):
    """Prepare complete forecast/correction state; caller must persist the result.

    Each capture is (verified envelope metadata, trusted raw-chunk reader).
    Existing state clocks/IDs and original forecast envelopes are preserved.
    A same capture is rebound after new forecast membership, not ACK-cached.
    """
    observed=instant(observed_utc);graph=plain(graph)
    need(type(captures) in(list,tuple)and len(captures)<=32,"file_feedback_work_limit")
    need(all(type(pair) in(list,tuple)and len(pair)==2 and callable(pair[1])for pair in captures))
    need(type(graph) is dict and set(graph)=={"source_objects","generations"})
    need(type(graph["source_objects"]) is list and len(graph["source_objects"])<=3*MAX_GENERATIONS)
    need(type(graph["generations"]) is list and len(graph["generations"])<=MAX_GENERATIONS)
    state=_state(previous)if previous is not None else {"version":1,"kind":"nfl.file_feedback_state.v1","namespace":dict(NS),
        "basis":BASIS,"sequence":0,"previous_state_id":None,"updated_utc":observed_utc,"source_object_ids":[],"source_imports":[],
        "receipts":[],"receipt_imports":[],"generation_records":[],"observation_records":[],"revisions":[],"capture_records":[]}
    need(instant(state["updated_utc"])<=observed,"file_feedback_clock_regressed")
    old=copy.deepcopy(state);objects={};source_ids=set()
    source_imports={r["object_id"]:r["first_imported_utc"]for r in state["source_imports"]}
    for request in graph["source_objects"]:
        need(type(request) is dict and set(request)=={"version","kind","object_id","payload"}
            and type(request["version"]) is int and request["version"]==1 and request["kind"]=="source_snapshot")
        oid=digest(request["object_id"]);need(oid==fingerprint(request["payload"])and oid not in objects,"file_feedback_source_conflict")
        objects[oid]={"object_id":oid,"kind":"source_snapshot","payload":request["payload"],"received_utc":source_imports.get(oid,observed_utc)}
        source_ids.add(oid)
    need(set(state["source_object_ids"])<=source_ids,"file_feedback_input_history_missing")
    docs={}
    for entry in graph["generations"]:
        need(type(entry) is dict and set(entry)=={"request","expected_receipt_ids"})
        request=entry["request"];need(type(request) is dict and set(request)=={"version","kind","object_id","payload"}
            and type(request["version"]) is int and request["version"]==1 and request["kind"]=="generation")
        oid=digest(request["object_id"]);need(oid not in docs and oid not in objects)
        doc=request["payload"];need(entry["expected_receipt_ids"]==sorted(row["receipt_id"]for row in doc["receipts"]))
        objects[oid]={"object_id":oid,"kind":"generation","payload":doc};docs[oid]=doc
    known_generations={r["object_id"]:r for r in state["generation_records"]}
    need(set(known_generations)<=set(docs),"file_feedback_input_history_missing")
    view=_View(state,objects)
    used=set()
    for oid,doc in docs.items():
        generation.validate_generation(view,oid,now_utc=observed_utc)
        used.update(doc["generations"][0]["source_snapshots"].values())
        if oid in known_generations:need(fingerprint(known_generations[oid]["payload"])==fingerprint(doc))
    need(used==source_ids,"file_feedback_orphan_source_object")
    batch=adapt_prospective(journal_docs=list(docs.values()),existing_receipts=state["receipts"])
    by_source={r["source_receipt_id"]:r for r in batch["receipts"]}
    existing={r["receipt_id"]for r in state["receipts"]}
    need(existing<={r["receipt_id"]for r in batch["receipts"]},"file_feedback_prior_forecast_missing")
    state["receipts"]=batch["receipts"]
    state["receipt_imports"].extend({"receipt_id":r["receipt_id"],"first_imported_utc":observed_utc}for r in state["receipts"]if r["receipt_id"]not in existing)
    for oid,doc in sorted(docs.items(),key=lambda item:(instant(item[1]["generations"][0]["recorded_utc"]),item[0])):
        if oid in known_generations:continue
        accepted=[];rejected=[]
        for row in doc["receipts"]:
            sid=row["receipt_id"]
            if sid in by_source:accepted.append({"source_receipt_id":sid,"receipt_id":by_source[sid]["receipt_id"],"status":"recorded"})
            else:rejected.append({"source_receipt_id":sid,"reason":"quarantined_forecast_contract"})
        ack={"version":1,"object_id":oid,"durable":True,"receipts":accepted,"rejections":rejected}
        state["generation_records"].append({"object_id":oid,"payload":doc,"ack":ack,"recorded_utc":observed_utc})
    state["source_object_ids"]=sorted(source_ids)
    state["source_imports"]=[{"object_id":oid,"first_imported_utc":source_imports.get(oid,observed_utc)}for oid in sorted(source_ids)]
    summaries=[];seen_captures={(r["capture_id"],r["membership_id"])for r in state["capture_records"]}
    for envelope,raw_chunks in captures:
        binding=labels.bind_capture_labels(view,envelope,raw_chunks)
        need(instant(envelope["manifest"]["completed_utc"])<=observed,"file_feedback_future_capture")
        resolved=outcomes.resolve_observed(state["receipts"],binding["source_receipts"],binding["observed_legs"],binding["game_observations"],
            observed_utc=observed_utc,existing_revisions=state["revisions"],existing_observations=[r["payload"]for r in state["observation_records"]])
        snapshot=view.read_observed_snapshot();ids={r["observation_id"]for r in state["observation_records"]};revision_ids={r["revision_id"]for r in state["revisions"]}
        observations=[r for r in resolved["observation_proposals"]if r["observation_id"]not in ids]
        revisions=[{"version":1,**r,"recorded_utc":observed_utc}for r in resolved["proposals"]if r["revision_id"]not in revision_ids]
        _validate_commit(snapshot,observations,revisions,observed)
        state["observation_records"].extend({"observation_id":r["observation_id"],"payload":r,"recorded_utc":observed_utc}for r in observations)
        state["revisions"].extend(revisions)
        key=(binding["capture_id"],binding["membership_id"])
        if key not in seen_captures:
            state["capture_records"].append({"capture_id":key[0],"membership_id":key[1],"recorded_utc":observed_utc});seen_captures.add(key)
        summaries.append({"capture_id":key[0],"membership_id":key[1],"new_observations":len(observations),"new_revisions":len(revisions),
            "binding_status":binding["status"],"withheld":dict(sorted(Counter(r["reason"]for r in binding["rejections"]).items())),
            "resolution_exclusions":resolved["summary"]["exclusion_reasons"]})
    changed=previous is None or fingerprint({k:v for k,v in old.items()if k!="state_id"})!=fingerprint({k:v for k,v in state.items()if k!="state_id"})
    if changed:
        state.update(sequence=old["sequence"]+1,previous_state_id=old.get("state_id"),updated_utc=observed_utc);state.pop("state_id",None);state=_sealed(state)
    state=_state(state)
    return {"version":1,"kind":"nfl.file_feedback_preparation.v1","state":state,"changed":changed,"capture_results":summaries,
        "forecast_quarantine":batch["summary"]["quarantine_reasons"],**FLAGS}


def evaluate(state,graph,*,as_of_utc,population="eligible_pool",bootstrap_replicates=200):
    """Actual shared fitter; no alternate formula, public record or adoption."""
    state=_state(state);need(population in("eligible_pool","offered_my"))
    need(instant(as_of_utc)>=instant(state["updated_utc"]),"file_feedback_evaluation_clock_regressed")
    need(type(bootstrap_replicates) is int and 200<=bootstrap_replicates<=2000)
    # Rejoin the COMPLETE graph rather than accepting a selected subset of
    # receipts that happens to agree with a caller's claimed state hash.
    checked=prepare(graph,previous=state,observed_utc=state["updated_utc"])
    need(not checked["changed"],"file_feedback_evaluation_graph_changed")
    graph=plain(graph)
    sources={}
    for row in graph["generations"]:
        for item in row["request"]["payload"]["receipts"]:
            source=item["learning_receipt"];sid=source["receipt_id"]
            if sid in sources:need(fingerprint(sources[sid])==fingerprint(source))
            sources[sid]=source
    report=calibration.walk_forward_report(state["receipts"],[sources[sid]for sid in sorted(sources)],state["revisions"],
        as_of_utc=as_of_utc,population=population,bootstrap_replicates=bootstrap_replicates,generation_records=state["generation_records"])
    # Kernel reports contain tuple cohort keys; persist ordinary strict JSON.
    report=json.loads(_encoded(report))
    imports={r["receipt_id"]:r["first_imported_utc"]for r in state["receipt_imports"]}
    prospective=sum(instant(imports[r["receipt_id"]])<instant(r["event_start_utc"])for r in state["receipts"])
    value={"version":1,"kind":"nfl.file_feedback_evaluation.v1","state_id":state["state_id"],"population":population,
        "as_of_utc":as_of_utc,"report":report,"first_imported_before_kickoff":prospective,
        "late_imported_receipts":len(state["receipts"])-prospective,"basis":BASIS,**FLAGS}
    return {**value,"evaluation_id":fingerprint(value)}
