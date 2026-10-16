// Additive v2 fixture only; existing harnesses/migrations stay unchanged.
import { readFile } from 'node:fs/promises';
import { openCaptureDeliveryDatabase } from './capture_delivery_harness.mjs';
import { canonical, digest, receipt, appendGeneration, commitGeneration, asRole } from './harness.mjs';

const RPCS = new Set(['selflearn_journal_register_capture_application_v2','selflearn_journal_commit_capture_application_batch_v2',
  'selflearn_journal_stop_capture_application_v2','selflearn_journal_read_capture_application_v2','selflearn_journal_prepare_capture_membership_v2']);
export async function rpcV2(db,name,args=[]) {
  if (!RPCS.has(name)) throw new Error('Unsupported v2 test RPC');
  return (await db.query(`SELECT public.${name}(${args.map((_,i)=>'$'+(i+1)).join(',')}) AS result`,args)).rows[0].result;
}
export const serviceV2 = (db,name,args=[]) => asRole(db,'service_role',()=>rpcV2(db,name,args));

export const applicationMigration = new URL('../../supabase/migrations/20261016044206_capture_application_v2.sql', import.meta.url);
export async function openCaptureApplicationDatabase() {
  const db = await openCaptureDeliveryDatabase();
  try {
    await db.exec(await readFile(new URL('../../supabase/migrations/20261016040611_capture_staging_quotas_v1.sql',import.meta.url),'utf8'));
    const prior = await db.query("SELECT p.oid::regprocedure::text AS signature, pg_get_functiondef(p.oid) AS definition FROM pg_proc p WHERE p.pronamespace IN ('public'::regnamespace,'selflearn_private'::regnamespace) ORDER BY p.oid");
    await db.exec(await readFile(applicationMigration, 'utf8'));
    db.originalV1Functions = prior.rows;
    return db;
  } catch (error) {
    await db.close();
    throw error;
  }
}

export async function addMembers(db, count, { vintage = 'first', player = i => 'player-' + i, line = i => 40 + i % 2, week = 6,
                                            sourceExtra = {}, genericExtra = {}, accepted = true } = {}) {
  const members = [];
  for (let i = 0; i < count; i++) {
    const name = typeof player === 'function' ? player(i) : player;
    const threshold = typeof line === 'function' ? line(i) : line;
    const key = [2026, 'G1', name, 'wr_rec_yds', String(threshold), 'home', 'over', 'gte'];
    const generic = receipt(vintage + '-' + i, { event_id: 'nfl:2026:game:G1', correlation_group: 'nfl:2026:game:G1',
                                               contract_id: digest(key), ...genericExtra });
    const source = { version: 1, season: 2026, week, game_id: 'G1', player_id: name, market: 'wr_rec_yds',
      line: threshold, side: 'home', direction: 'over', comparison_operator: 'gte', contract_id: generic.contract_id };
    for (const k of ['base_prob','served_prob','base_model_version','calibrator_version','forecast_created_utc',
      'source_snapshot_id','source_timestamps','features','publication_status']) source[k] = generic[k];
    Object.assign(source, sourceExtra);
    source.receipt_id = digest(source);
    generic.source_receipt_id = source.receipt_id;
    delete generic.receipt_id;
    generic.receipt_id = digest(generic);
    members.push({ key, generic, source });
  }
  const document = { version: 1, season: 2026, week,
    receipts: members.map(m => ({ receipt_id: m.source.receipt_id, learning_receipt: m.source })), generations: [] };
  document.envelope_id = digest(document);
  await appendGeneration(db, document);
  await commitGeneration(db, document.envelope_id, accepted ? members.map(m => m.generic) : [],
    accepted ? [] : members.map(m => ({ source_receipt_id: m.source.receipt_id, reason: 'fixture_rejection' })));
  for (const member of members) member.document = document;
  return members;
}

export const memberMap = async (db, version = 2, weeks = [6]) =>
  (await db.query(`SELECT selflearn_private.capture_members_v${version}($1,$2::jsonb) AS members`, [2026, canonical(weeks)])).rows[0].members;

export function captureRequest(tag='first',weeks=[6]) {
  const observations={},sources=[],raw_objects=[],raw_pointers=[];
  const profiles=[...weeks.map(week=>['espn_scoreboard_v1',week]),['nflverse_stats_player_v1',null],['nflverse_snap_counts_v1',null]];
  for (const [index,[profile,week]] of profiles.entries()) {
    const raw_sha256=digest({raw:profile,tag,week});
    const source_url=profile==='espn_scoreboard_v1'?'https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard?seasontype=2&dates=2026&week='+week:
      profile==='nflverse_stats_player_v1'?'https://github.com/nflverse/nflverse-data/releases/download/stats_player/stats_player_week_2026.csv':
        'https://github.com/nflverse/nflverse-data/releases/download/snap_counts/snap_counts_2026.csv';
    const audit=profile==='espn_scoreboard_v1'?{events:1,normalized_games:1,final_games:1,nonfinal_games:0,malformed_events:0,
      duplicate_events:0,conflicting_game_ids:0,invalid_final_scores:0,quarantined_game_ids:[]}:
      {rows:1,duplicate_rows:0,outside_regular_week_rows:0,measurement_policy:'raw values retained; missing/negative cells are not coerced or graded'};
    const observation={version:1,kind:'nfl.outcome_source_observation.v1',evidence_class:'observed_source_snapshot',profile,
      season:2026,week,source_url,raw_sha256,raw_bytes:1,fetch_started_utc:`2026-09-03T${String(index+1).padStart(2,'0')}:00:00Z`,
      observed_utc:`2026-09-03T${String(index+1).padStart(2,'0')}:01:00Z`,upstream_updated_utc:null,http:{status:200},audit};
    const observation_id=digest(observation);observations[observation_id]=observation;
    sources.push({profile,season:2026,week,observation_id});raw_objects.push({raw_sha256,raw_bytes:1});
    raw_pointers.push({version:1,kind:'selflearn.evidence_object.v1',scope:'uploaded_only',object_id:raw_sha256,raw_bytes:1,
      chunks:[{chunk_id:raw_sha256,raw_bytes:1}]});
  }
  raw_objects.sort((a,b)=>a.raw_sha256.localeCompare(b.raw_sha256));raw_pointers.sort((a,b)=>a.object_id.localeCompare(b.object_id));
  const manifest={version:1,kind:'nfl.outcome_capture.v1',season:2026,weeks,started_utc:'2026-09-03T00:00:00Z',
    completed_utc:`2026-09-03T${String(sources.length+1).padStart(2,'0')}:00:00Z`,sources,clock_policy:'producer observations only; upstream issue and exact game-finish times unknown',
    publication_status:'source_capture_not_public_forecast_evidence',audit:{cross_week_conflicting_game_ids:[],quarantined_game_ids:[],normalized_games:weeks.length}};
  manifest.capture_id=digest(manifest);
  const envelope={version:1,kind:'nfl.capture_envelope.v1',scope:'uploaded_only',manifest,observations,raw_objects};
  const identity={version:1,kind:'nfl.capture_delivery.v1',destination_id:digest('destination'),producer_id:'sql-fixture',
    capture_id:manifest.capture_id,binding_policy:'nfl.capture_labels_v1'};
  return {...identity,kind:'nfl.capture_delivery_request.v1',envelope,raw_pointers,delivery_id:digest(identity)};
}

export function applicationRequest(capture=captureRequest()) {
  const execution_policy='sealed_whole_groups_v2';
  return {version:2,kind:'nfl.capture_application_request.v2',execution_policy,capture_request:capture,
    application_id:digest({version:2,kind:'nfl.capture_application.v2',capture_intent_id:capture.delivery_id,execution_policy})};
}
export async function sealApplication(db,request,{facts={},maximum=500}={}) {
  const capture=request.capture_request;
  const members=await memberMap(db,2,capture.envelope.manifest.weeks);
  const groups=(await db.query('SELECT selflearn_private.capture_groups_v2($1::jsonb) AS groups',[canonical(members)])).rows[0].groups;
  const batches=[];let active=null;
  for(const g of groups) {
    const fact=facts[g.scope[2]];
    if(!fact) Object.assign(g,{status:'withheld',reason_codes:['fixture_unverified'],batch_ordinal:null,fact:null,fact_id:null});
    else {
      if(!active||active.member_count+g.receipt_ids.length>maximum) {
        const ordinal=batches.length;
        active={ordinal,step_id:digest({version:2,kind:'nfl.capture_application_step.v2',application_id:request.application_id,ordinal}),
          group_ids:[],member_count:0};batches.push(active);
      }
      Object.assign(g,{status:'planned',reason_codes:[],batch_ordinal:active.ordinal,fact,fact_id:digest(fact)});
      active.group_ids.push(g.group_id);active.member_count+=g.receipt_ids.length;
    }
  }
  const source_watermark=(await db.query('SELECT selflearn_private.observed_watermark_v1()::text AS value')).rows[0].value;
  const source_vector_id=(await db.query('SELECT selflearn_private.capture_source_vector_v2($1::json) AS value',[canonical(capture)])).rows[0].value;
  const seal={version:2,kind:'nfl.capture_application_seal.v2',application_id:request.application_id,request_id:digest(request),
    capture_intent_id:capture.delivery_id,capture_id:capture.capture_id,execution_policy:request.execution_policy,
    binding_policy:capture.binding_policy,season:2026,weeks:capture.envelope.manifest.weeks,source_watermark,
    membership_id:digest({binding_policy:capture.binding_policy,groups:groups.map(g=>g.membership_id)}),source_vector_id,
    limits:{members:500,observations:500,revisions:500,rpc_bytes:8388608},groups,batches};
  seal.seal_id=digest(seal);return seal;
}
export const registerApplication=(db,request,seal,watermark=seal.source_watermark)=>serviceV2(db,
  'selflearn_journal_register_capture_application_v2',[canonical(request),canonical(seal),watermark]);

export function memberProof(member,request,actual=80) {
  const source_observations={};
  for(const [source,profile] of [['espn.scoreboard','espn_scoreboard_v1'],['nflverse.stats_player','nflverse_stats_player_v1']]) {
    const ref=request.envelope.manifest.sources.find(s=>s.profile===profile);
    source_observations[source]={source,observation_id:ref.observation_id,observed_utc:request.envelope.observations[ref.observation_id].observed_utc};
  }
  const measurement={version:1,canonical_contract:member.key,week:6,actual,played:true,status:'FINAL',hit:actual>=Number(member.key[4]),
    reason:null,participation_basis:'stat_row',game:{season:2026,week:6,game_id:'G1',kickoff_utc:member.generic.event_start_utc,home:'SEA',away:'LAR',state:'FINAL'}};
  const measurement_id=digest(measurement),basis='nfl.observed_resolution_v2';
  const observation={version:1,kind:'nfl.observed_measurement.v1',evidence_basis:basis,receipt_id:member.generic.receipt_id,
    contract_id:member.generic.contract_id,event_id:member.generic.event_id,measurement,measurement_id,source_observations,
    evidence_observation_id:digest({measurement_id,components:source_observations}),observed_utc:source_observations['nflverse.stats_player'].observed_utc};
  observation.observation_id=digest(observation);
  const resolution={receipt_id:observation.receipt_id,contract_id:observation.contract_id,event_id:observation.event_id,evidence_basis:basis,
    measurement_id,measurement,evidence_observation_id:observation.evidence_observation_id,source_observations,observation_watermarks:source_observations,
    resolved_utc:observation.observed_utc,evidence_observed_utc:observation.observed_utc,status:'FINAL',hit:measurement.hit,actual,reason:null,
    participation_basis:'stat_row',source:'nflverse.stats_player',authoritative:true,final_evidence_status:'FINAL'};
  const revision={version:1,revision_id:basis+':'+digest({receipt_id:observation.receipt_id,observation_id:observation.evidence_observation_id,supersedes:null}),
    supersedes:null,reason:null,resolution};
  return {observation,revision};
}
export function commonFact(p) {
  const {week,actual,played,status,reason,participation_basis,game}=p.observation.measurement;
  return {week,actual,played,status,reason,participation_basis,game,source_observations:p.observation.source_observations};
}
export function stepPayload(seal,ordinal,proofs,{withheld=[]}={}) {
  const assignment=seal.batches[ordinal],groups=[];
  const observations=[],revisions=[];
  for(const id of assignment.group_ids) {
    const sealed=seal.groups.find(g=>g.group_id===id),held=withheld.includes(id);
    const owned=proofs.filter(p=>sealed.receipt_ids.includes(p.observation.receipt_id));
    groups.push({group_id:id,...Object.fromEntries(['scope','receipt_ids','source_receipt_ids','generation_object_ids','membership_id'].map(k=>[k,sealed[k]])),
      status:held?'withheld':'accepted',reason_codes:held?['fixture_kernel_withheld']:[],
      observation_ids:held?[]:owned.map(p=>p.observation.observation_id).sort(),revision_ids:held?[]:owned.map(p=>p.revision.revision_id).sort()});
    if(!held) {observations.push(...owned.map(p=>p.observation));revisions.push(...owned.map(p=>p.revision));}
  }
  return {groups,batch:{version:1,observations,revisions}};
}
export const commitStep=(db,request,seal,ordinal,payload,watermark)=>serviceV2(db,'selflearn_journal_commit_capture_application_batch_v2',
  [request.application_id,seal.batches[ordinal].step_id,canonical(payload.groups),canonical(payload.batch),watermark]);
export const readApplication=(db,id,view='status',{key=null,after=null,limit=['groups','batches'].includes(view)?100:1,watermark=null}={})=>
  serviceV2(db,'selflearn_journal_read_capture_application_v2',[id,view,key,after,limit,watermark]);
export function moveCaptureDay(capture,day) {
  const next=structuredClone(capture),env=next.envelope;
  for(const ref of env.manifest.sources) {
    const value=env.observations[ref.observation_id];delete env.observations[ref.observation_id];
    for(const field of ['fetch_started_utc','observed_utc']) value[field]=value[field].replace('2026-09-03','2026-09-'+String(day).padStart(2,'0'));
    ref.observation_id=digest(value);env.observations[ref.observation_id]=value;
  }
  for(const field of ['started_utc','completed_utc'])env.manifest[field]=env.manifest[field].replace('2026-09-03','2026-09-'+String(day).padStart(2,'0'));
  delete env.manifest.capture_id;env.manifest.capture_id=digest(env.manifest);next.capture_id=env.manifest.capture_id;
  const {delivery_id,envelope,raw_pointers,kind,...identity}=next;next.delivery_id=digest({...identity,kind:'nfl.capture_delivery.v1'});
  return next;
}
export function resealCapture(capture) {
  const next=structuredClone(capture),env=next.envelope,observations={};
  for(const ref of env.manifest.sources) {
    const observation=env.observations[ref.observation_id];ref.observation_id=digest(observation);observations[ref.observation_id]=observation;
  }
  env.observations=observations;delete env.manifest.capture_id;env.manifest.capture_id=digest(env.manifest);next.capture_id=env.manifest.capture_id;
  const {delivery_id,envelope,raw_pointers,kind,...identity}=next;next.delivery_id=digest({...identity,kind:'nfl.capture_delivery.v1'});return next;
}
