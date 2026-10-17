"""Actual file journal → old NFL diagnostic → real fitter; still always held."""
import json

from selflearn_core.adapters import nfl_owner_evidence as evidence
from selflearn_core.store.file_owner_intents import FileOwnerIntentJournal
from test_nfl_owner_evidence import AUTHORITY,ACTOR,documents,command,experiment as nfl_experiment


def test_file_owner_and_evidence_lookup_compose_with_existing_read_only_nfl_reader(tmp_path,nfl_experiment):
    docs=documents(nfl_experiment);bundle=evidence.prepare_manifest(docs)
    records={oid:json.dumps({'object_id':oid,'kind':'source_snapshot','payload':payload,
        'received_utc':'2026-10-15T01:00:00Z'}).encode()for oid,payload in bundle['objects'].items()}
    journal=FileOwnerIntentJournal(tmp_path/'owner',authority_id=AUTHORITY,actor_ref=ACTOR,
        evidence_reader=lambda oid:records[oid])
    cmd=command(docs,bundle);ack=journal.append_owner_intent(cmd)
    runtime={key:docs['context'][key]for key in('base_model_version','nfl_code_id','engine_code_id','context_id')}
    runtime['current_id']=docs['current']['current_id']
    result=evidence.NFLOwnerEvidenceReader(journal,authority_id=AUTHORITY,actor_ref=ACTOR,
        runtime_reader=lambda:runtime).verify(bundle['manifest_id'])
    assert result['head_event_id']==ack['event_id'] and result['owner_head_content_verified']is True,result
    assert result['domain_pins_bound']is True and result['evaluation_reexecuted']is True,result
    assert result['status']=='held' and all(result[key]is False for key in evidence.FLAGS)
    assert 'fixed_artifact_prospective_evidence_missing'in result['reason_codes']
