"""Synthetic shadow experiments; no Week 5/TNF fitting or production writes."""
import copy
from contextlib import redirect_stdout, redirect_stderr
import datetime as dt
import json
import io
import math
import os
from pathlib import Path
import random
import subprocess
import sys
import tempfile
import unittest

from selflearn_core.adapters.nfl_calibration import (build_cohort, fit_shadow_candidate,
    main, serve_base, validate_candidate, walk_forward_report)
from selflearn_core.adapters.nfl import adapt_prospective
from selflearn_core.probability_receipts import fingerprint, seal_receipt
from test_nfl_adapter import fixture
from test_nfl_prospective import inputs, journal_document, rehash


def stamp(value):
    return value.isoformat().replace('+00:00', 'Z')


def case(week=1, index=0, p=.4, hit=True, correction=None, offered=True, game=None):
    market=('qb_pass_yds','rb_rush_yds','wr_rec_yds')[index%3]
    game=game or f'fixture-w{week}-g{index//3}'
    card,_,_=fixture(player=f'fixture-player-{week}-{index}',game=game,probability=p,correction=correction)
    src=copy.deepcopy(card['cards'][0]['legs'][0]['learning_receipt'])
    created=dt.datetime(2026,9,1,12,tzinfo=dt.timezone.utc)+dt.timedelta(days=7*week)
    seen=created+dt.timedelta(minutes=1)
    kickoff=created+dt.timedelta(hours=24)
    final=kickoff+dt.timedelta(hours=4)
    src.update(week=week,market=market,forecast_created_utc=stamp(created),
               source_timestamps={'fixture':stamp(created-dt.timedelta(hours=1))})
    z=math.log(p/(1-p))
    src['features'].update(market=market,mu=59.5+20*z,sd=20.,z=z,p_team=.5,
                           calibration={'a':0.,'b':1.,'c':0.})
    src['base_prob']=1/(1+math.exp(-z)) if z>=0 else math.exp(z)/(1+math.exp(z))
    src['served_prob']=serve_base(src['base_prob'],correction)
    src['contract_id']=fingerprint([2026,game,src['player_id'],market,'59.5','home','over','gte'])
    src['receipt_id']=fingerprint({k:v for k,v in src.items() if k!='receipt_id'})
    receipt=seal_receipt({'version':1,'domain':'nfl','task':'nfl.parlay_leg',
        'contract_id':src['contract_id'],'event_id':f'nfl:2026:game:{game}',
        'correlation_group':f'nfl:2026:game:{game}',
        **{k:copy.deepcopy(src[k]) for k in ('base_prob','served_prob','base_model_version','calibrator_version',
             'forecast_created_utc','source_snapshot_id','source_timestamps','features','publication_status')},
        'first_seen_utc':stamp(seen),'event_start_utc':stamp(kickoff),'source_receipt_id':src['receipt_id'],
        'capture_provenance':{'kind':'nfl_generation_journal','population':'eligible_pool_prop_rungs_before_display_dedupe',
            'generation_id':fingerprint(['fixture',week]),'offered_card_ids':[f'card-{week}-{index}'] if offered else []}})
    resolution={'receipt_id':receipt['receipt_id'],'contract_id':receipt['contract_id'],'event_id':receipt['event_id'],
        'status':'FINAL','hit':hit,'source':'synthetic fixture FINAL','resolved_utc':stamp(final)}
    revision={'version':1,'revision_id':fingerprint(resolution),'supersedes':None,'reason':None,
              'resolution':resolution,'recorded_utc':stamp(final+dt.timedelta(minutes=1))}
    return receipt,src,revision


def corpus(weeks=(1,2,3),n=180,correction=None):
    rnd=random.Random(9931)
    triples=[]
    for w in weeks:
        for i in range(n):
            p=.2+.6*rnd.random()
            true_p=1/(1+math.exp(-(.9+math.log(p/(1-p)))))
            triples.append(case(w,i,p,rnd.random()<true_p,correction))
    return tuple([t[k] for t in triples] for k in range(3))


def reseal(receipt,**updates):
    payload={k:copy.deepcopy(v) for k,v in receipt.items() if k!='receipt_id'}
    payload.update(updates)
    return seal_receipt(payload)


class NFLCalibrationTest(unittest.TestCase):
    def test_scalar_oracle_matches_base_logit_not_generic_confidence_platt(self):
        coeff={'a':.580223,'b':.969067}
        p=.374058
        expected=round(min(.95,max(.05,1/(1+math.exp(-(coeff['a']+coeff['b']*math.log(p/(1-p))))))),4)
        self.assertEqual(serve_base(p,coeff),expected)
        self.assertEqual(expected,.5203)
        self.assertNotEqual(expected,serve_base(expected,coeff))
        self.assertNotEqual(expected,round(1/(1+math.exp(-(coeff['a']*p+coeff['b']))),4))
        for bad in (True,0,1,float('nan'),float('inf')):
            with self.subTest(bad=bad),self.assertRaises(ValueError):serve_base(bad,coeff)
        for coeff in ({'a':0,'b':0},{'a':0,'b':-1},{'a':True,'b':1},{'a':0,'b':float('inf')},{'a':0,'b':1,'other':2}):
            with self.subTest(coeff=coeff),self.assertRaises(ValueError):serve_base(.5,coeff)

    def test_first_forecast_selected_before_outcome_join_and_population_explicit(self):
        first,src,rev=case()
        later=copy.deepcopy(src)
        later['forecast_created_utc']='2026-09-08T13:00:00Z'
        later['receipt_id']=fingerprint({k:v for k,v in later.items() if k!='receipt_id'})
        r2=reseal(first,source_receipt_id=later['receipt_id'],forecast_created_utc=later['forecast_created_utc'],
                  first_seen_utc='2026-09-08T13:01:00Z')
        final=copy.deepcopy(rev)
        final['resolution']['receipt_id']=r2['receipt_id']
        final['revision_id']=fingerprint(final['resolution'])
        report=build_cohort([r2,first],[later,src],[final],as_of_utc='2026-12-01T00:00:00Z',population='offered_my')
        self.assertEqual(report['n'],0)
        self.assertEqual(report['excluded']['unresolved_at_cutoff'],1)
        missing=build_cohort([r2,first],[later],[final],as_of_utc='2026-12-01T00:00:00Z',population='offered_my')
        self.assertEqual(missing['n'],0)
        self.assertIn('earliest_source_lineage_unverified',missing['excluded'])
        conflict=copy.deepcopy(final)
        conflict['resolution']['hit']=not rev['resolution']['hit']
        conflict['revision_id']=fingerprint(conflict['resolution'])
        disagree=build_cohort([first,r2],[src,later],[rev,conflict],
            as_of_utc='2026-12-01T00:00:00Z',population='offered_my')
        self.assertEqual(disagree['n'],0)
        self.assertIn('conflicting_contract_outcomes',disagree['excluded'])
        r,source,v=case(offered=False)
        offered=build_cohort([r],[source],[v],as_of_utc='2026-12-01T00:00:00Z',population='offered_my')
        pool=build_cohort([r],[source],[v],as_of_utc='2026-12-01T00:00:00Z',population='eligible_pool')
        self.assertEqual(offered['n'],0);self.assertEqual(pool['n'],1)
        with self.assertRaises(ValueError):build_cohort([r],[source],[v],as_of_utc='2026-12-01T00:00:00Z',population=None)

    def test_revision_asof_void_correction_and_fork(self):
        r,s,original=case()
        corrected=copy.deepcopy(original)
        corrected.update(revision_id='correction',supersedes=original['revision_id'],reason='official correction',
                         recorded_utc='2026-09-11T12:00:00Z')
        corrected['resolution'].update(status='VOID',hit=None,reason='did_not_play',
                                       authoritative=True,final_evidence_status='FINAL')
        earlier=build_cohort([r],[s],[original,corrected],as_of_utc='2026-09-10T00:00:00Z',population='offered_my')
        later=build_cohort([r],[s],[original,corrected],as_of_utc='2026-09-12T00:00:00Z',population='offered_my')
        self.assertEqual(earlier['n'],1);self.assertEqual(later['n'],0)
        self.assertEqual(later['excluded']['non_binary_latest_revision'],1)
        fork={**corrected,'revision_id':'fork','supersedes':None}
        invalid=build_cohort([r],[s],[original,fork],as_of_utc='2026-09-12T00:00:00Z',population='offered_my')
        self.assertEqual(invalid['n'],0);self.assertIn('revision_chain_fork',invalid['excluded'])

    def test_database_receipt_time_not_backdated_resolved_time_controls_eligibility(self):
        r,s,v=case()
        cutoff=v['recorded_utc']
        before=build_cohort([r],[s],[v],as_of_utc=cutoff,population='offered_my')
        self.assertEqual(before['n'],0)
        changed=copy.deepcopy(v);changed['recorded_utc']='2026-09-08T14:00:00Z'
        bad=build_cohort([r],[s],[changed],as_of_utc='2026-12-01T00:00:00Z',population='offered_my')
        self.assertEqual(bad['n'],0);self.assertIn('resolution_after_database_observation',bad['excluded'])
        future=copy.deepcopy(v)
        future['resolution']['evidence_observed_utc']='2026-11-01T00:00:00Z'
        bad=build_cohort([r],[s],[future],as_of_utc='2026-10-01T00:00:00Z',population='offered_my')
        self.assertEqual(bad['n'],0);self.assertIn('evidence_after_database_observation',bad['excluded'])
        previous=copy.deepcopy(v)
        previous['resolution'].update(source_updated_utc=previous['resolution']['resolved_utc'],
                                      evidence_observed_utc=previous['recorded_utc'])
        correction=copy.deepcopy(previous)
        correction.update(revision_id='later',supersedes=previous['revision_id'],reason='official correction',
                          recorded_utc='2026-09-12T00:00:00Z')
        correction['resolution']['source_updated_utc']='2026-09-09T15:00:00Z'
        bad=build_cohort([r],[s],[previous,correction],as_of_utc='2026-10-01T00:00:00Z',population='offered_my')
        self.assertEqual(bad['n'],0);self.assertIn('revision_evidence_clock_regressed',bad['excluded'])
        previous['resolution']['source_revision_id']='official-v1'
        correction=copy.deepcopy(previous)
        correction.update(revision_id='changed-official',supersedes=previous['revision_id'],reason='correction',
                          recorded_utc='2026-09-12T00:00:00Z')
        correction['resolution']['hit']=not previous['resolution']['hit']
        bad=build_cohort([r],[s],[previous,correction],as_of_utc='2026-10-01T00:00:00Z',population='offered_my')
        self.assertEqual(bad['n'],0);self.assertIn('conflicting_official_source_revision',bad['excluded'])

    def test_duplicates_order_and_future_revision_do_not_change_earlier_candidate(self):
        receipts,sources,revisions=corpus(n=110)
        cutoff='2026-10-01T00:00:00Z'
        a=fit_shadow_candidate(receipts,sources,revisions,cutoff_utc=cutoff,population='offered_my')
        future=copy.deepcopy(revisions[0]);future.update(revision_id='future',supersedes=revisions[0]['revision_id'],
            reason='later official correction',recorded_utc='2026-11-01T00:00:00Z')
        future['resolution']['hit']=not future['resolution']['hit']
        b=fit_shadow_candidate(list(reversed(receipts))+receipts,sources,[future]+list(reversed(revisions)),
                              cutoff_utc=cutoff,population='offered_my')
        self.assertEqual(a['candidate'],b['candidate'])
        self.assertEqual(a['cohort']['dataset_id'],b['cohort']['dataset_id'])
        self.assertFalse(a['candidate']['automatic_promotion'])
        self.assertEqual(a['candidate']['evidence_status'],'retrospective_reconstruction')
        self.assertNotIn('created_utc',a['candidate'])

    def test_cross_source_market_feature_and_generic_artifact_rejected(self):
        receipts,sources,revisions=corpus(n=100)
        candidate=fit_shadow_candidate(receipts,sources,revisions,cutoff_utc='2026-12-01T00:00:00Z',population='offered_my')['candidate']
        validate_candidate(candidate)
        for change in ({'kind':'generic.platt.v1'},{'transform':{'name':'sigmoid_a_confidence_plus_b'}},
                       {'state':'active'},{'automatic_promotion':True},{'coefficients':None},
                       {'base_model_version':fingerprint('other')}):
            modified={**copy.deepcopy(candidate),**change}
            modified['candidate_id']=fingerprint({k:v for k,v in modified.items() if k!='candidate_id'})
            with self.subTest(change=change),self.assertRaises(ValueError):
                validate_candidate(modified,expected_base_version=candidate['base_model_version'])
        r,s,v=case()
        bad=copy.deepcopy(s);bad['features']['market_prob']=.9
        bad['receipt_id']=fingerprint({k:v for k,v in bad.items() if k!='receipt_id'})
        altered=reseal(r,features=bad['features'],source_receipt_id=bad['receipt_id'])
        vv=copy.deepcopy(v);vv['resolution']['receipt_id']=altered['receipt_id']
        result=build_cohort([altered],[bad],[vv],as_of_utc='2026-12-01T00:00:00Z',population='offered_my')
        self.assertEqual(result['n'],0);self.assertTrue(result['excluded'])

    def test_walk_forward_uses_two_folds_three_weeks_and_shared_games(self):
        receipts,sources,revisions=corpus(n=240)
        original=copy.deepcopy((receipts,sources,revisions))
        report=walk_forward_report(receipts,sources,revisions,as_of_utc='2026-12-01T00:00:00Z',
                                   population='offered_my',bootstrap_replicates=200)
        self.assertEqual(len([f for f in report['folds'] if f['status']=='scored']),2)
        self.assertTrue(report['rows'])
        self.assertLess(report['paired_game_interval']['groups'],report['metrics']['n'])
        self.assertIsNotNone(report['paired_game_interval']['high'])
        self.assertFalse(report['automatic_promotion'])
        self.assertIn('not_fixed_artifact',report['evaluation_kind'])
        self.assertEqual(report['candidate']['state'],'shadow')
        self.assertEqual((receipts,sources,revisions),original)
        two=walk_forward_report(*corpus(weeks=(1,2),n=110),as_of_utc='2026-12-01T00:00:00Z',population='offered_my',bootstrap_replicates=200)
        self.assertFalse(two['shared_shadow_numeric_gate']['passed'])
        self.assertIn('fewer_than_two_held_out_week_folds',two['shared_shadow_numeric_gate']['failures'])

    def test_heldout_outcome_changes_never_change_earlier_fit(self):
        inputs=corpus(n=110)
        first=walk_forward_report(*inputs,as_of_utc='2026-12-01T00:00:00Z',population='offered_my',bootstrap_replicates=200)
        changed=copy.deepcopy(inputs)
        for r in changed[2]:
            if 'w3-' in r['resolution']['event_id']:
                r['resolution']['hit']=not r['resolution']['hit']
                r['revision_id']=fingerprint(r['resolution'])
        second=walk_forward_report(*changed,as_of_utc='2026-12-01T00:00:00Z',population='offered_my',bootstrap_replicates=200)
        for a,b in zip(first['folds'],second['folds']):
            self.assertEqual(a['coefficients'],b['coefficients'])
            self.assertEqual(a['candidate_id'],b['candidate_id'])

    def test_unresolved_first_forecast_still_sets_heldout_training_cutoff(self):
        receipts,sources,revisions=corpus(weeks=(1,2),n=110)
        early,source,_=case(week=2,index=999)
        source['forecast_created_utc']='2026-09-09T14:00:00Z'
        source['source_timestamps']={'fixture':'2026-09-09T13:00:00Z'}
        source['receipt_id']=fingerprint({k:v for k,v in source.items() if k!='receipt_id'})
        early=reseal(early,source_receipt_id=source['receipt_id'],
            forecast_created_utc=source['forecast_created_utc'],source_timestamps=source['source_timestamps'],
            first_seen_utc='2026-09-09T14:01:00Z')
        report=walk_forward_report(receipts+[early],sources+[source],revisions,
            as_of_utc='2026-12-01T00:00:00Z',population='offered_my',bootstrap_replicates=200)
        fold=report['folds'][0]
        self.assertEqual(fold['training_cutoff_utc'],source['forecast_created_utc'])
        self.assertEqual(fold['fit_n'],0)
        self.assertEqual(fold['status'],'skipped')
        self.assertEqual(report['captured_forecast_n'],report['cohort_n']+1)

    def test_candidate_manifest_rejects_resealed_invalid_chronology_and_population(self):
        candidate=fit_shadow_candidate(*corpus(n=100),cutoff_utc='2026-12-01T00:00:00Z',
                                       population='offered_my')['candidate']
        for key,value in (('first_seen_utc','2026-12-02T00:00:00Z'),
                          ('resolved_utc','2026-09-01T00:00:00Z'),
                          ('outcome_recorded_utc','2026-09-01T00:00:00Z'),
                          ('week',True),('season',True),('position','K'),
                          ('correlation_group','other-game'),('contract_id',[])):
            bad=copy.deepcopy(candidate)
            bad['training']['rows'][0][key]=value
            bad['training']['dataset_id']=fingerprint(bad['training']['rows'])
            bad['candidate_id']=fingerprint({k:v for k,v in bad.items() if k!='candidate_id'})
            with self.subTest(key=key),self.assertRaises(ValueError):validate_candidate(bad)
        committed=copy.deepcopy(candidate)
        committed['training']['membership_policy']='committed_generations_v1'
        for row in committed['training']['rows']:
            row['offering']={'object_id':fingerprint('synthetic-object'),'generation_id':fingerprint('synthetic-generation'),
                'captured_utc':row['first_seen_utc'],'recorded_utc':row['first_seen_utc'],'card_ids':['synthetic-card']}
        def rehash_candidate(value):
            value['training']['dataset_id']=fingerprint(value['training']['rows'])
            value['candidate_id']=fingerprint({k:v for k,v in value.items() if k!='candidate_id'})
        rehash_candidate(committed);validate_candidate(committed)
        for cards in ([],[''],[None],['same','same'],'not-a-list'):
            bad=copy.deepcopy(committed)
            bad['training']['rows'][0]['offering']['card_ids']=cards
            rehash_candidate(bad)
            with self.subTest(cards=cards),self.assertRaises(ValueError):validate_candidate(bad)

    def test_later_offering_is_joined_without_mutating_first_envelope(self):
        _,pool,games=inputs()
        first_doc=journal_document(pool,games)
        first=adapt_prospective(journal_docs=[first_doc])['receipts'][0]
        saved=copy.deepcopy(first)
        source=pool['players'][0]['rungs'][0]['learning_receipt']
        later=journal_document(pool,games,'2026-10-18T17:20:00Z')
        gen=later['generations'][0]
        gen['offered_cards']=[{'card_id':'later-card','receipt_ids':[source['receipt_id']]}]
        keys=('version','season','week','pool_generated_utc','source_snapshots',
              'offered_cards','selection_policy_version','joint_model_version')
        gen['generation_id']=fingerprint({k:gen[k] for k in keys})
        later['receipts'][0]['generation_id']=gen['generation_id']
        rehash(later)
        def record(doc,clock):
            return {'object_id':doc['envelope_id'],'payload':doc,'recorded_utc':clock,
                'ack':{'version':1,'object_id':doc['envelope_id'],'durable':True,
                    'receipts':[{'source_receipt_id':source['receipt_id'],'receipt_id':first['receipt_id'],'status':'recorded'}],
                    'rejections':[]}}
        first_record=record(first_doc,'2026-10-18T17:02:00Z')
        later_record=record(later,'2026-10-18T17:21:00Z')
        outcome={'receipt_id':first['receipt_id'],'contract_id':first['contract_id'],
            'event_id':first['event_id'],'status':'FINAL','hit':True,'source':'synthetic FINAL',
            'resolved_utc':'2026-10-18T22:00:00Z'}
        revision={'version':1,'revision_id':fingerprint(outcome),'supersedes':None,
            'reason':None,'resolution':outcome,'recorded_utc':'2026-10-18T22:01:00Z'}
        def cohort(records):
            return build_cohort([first],[source],[revision],as_of_utc='2026-10-20T00:00:00Z',
                                population='offered_my',generation_records=records)
        self.assertEqual(cohort([first_record])['n'],0)
        joined=cohort([first_record,later_record])
        self.assertEqual(joined['n'],1)
        self.assertEqual(joined['rows'][0]['offering']['card_ids'],['later-card'])
        self.assertEqual(joined['membership_policy'],'committed_generations_v1')
        self.assertEqual(first,saved)
        self.assertEqual(first['capture_provenance']['offered_card_ids'],[])
        future={**later_record,'recorded_utc':'2026-10-22T00:00:00Z'}
        self.assertEqual(cohort([first_record,future])['n'],0)
        duplicate={**later_record,'recorded_utc':'2026-10-18T17:22:00Z'}
        conflict=cohort([later_record,duplicate])
        self.assertEqual(conflict['n'],0)
        self.assertIn('conflicting_generation_commit',conflict['excluded'])
        self.assertEqual(cohort([later_record,duplicate]),cohort([duplicate,later_record]))

    def test_second_cycle_keeps_original_forecasts_and_applies_from_base(self):
        receipts,sources,revisions=corpus(n=120)
        before=copy.deepcopy((receipts,sources,revisions))
        first=fit_shadow_candidate(receipts,sources,revisions,cutoff_utc='2026-09-28T00:00:00Z',population='offered_my')['candidate']
        r2,s2,v2=corpus(weeks=(4,),n=120,correction=first['coefficients'])
        report=walk_forward_report(receipts+r2,sources+s2,revisions+v2,as_of_utc='2026-12-01T00:00:00Z',population='offered_my',bootstrap_replicates=200)
        self.assertEqual((receipts,sources,revisions),before)
        week4=[r for r in report['rows'] if r['week']==4]
        self.assertTrue(week4)
        fold=next(f for f in report['folds'] if f['week']==4)
        self.assertTrue(all(r['candidate_prob']==serve_base(r['base_prob'],fold['coefficients']) for r in week4))
        self.assertTrue(any(r['base_prob']!=r['served_prob'] for r in week4))
        self.assertTrue(any(r['candidate_prob']!=serve_base(r['served_prob'],fold['coefficients']) for r in week4))

    def test_empty_small_malformed_and_mixed_base_data_fail_honestly(self):
        empty=walk_forward_report([],[],[],as_of_utc='2026-12-01T00:00:00Z',population='offered_my',bootstrap_replicates=200)
        self.assertIsNone(empty['candidate']);self.assertIsNone(empty['metrics']['candidate_log_loss'])
        self.assertFalse(empty['shared_shadow_numeric_gate']['passed'])
        with self.assertRaisesRegex(ValueError,'insufficient_training'):
            fit_shadow_candidate(*corpus(n=2),cutoff_utc='2026-12-01T00:00:00Z',population='offered_my')
        r,s,v=case()
        report=build_cohort([r],[s],[None,{'resolution':{'receipt_id':[]}}],as_of_utc='2026-12-01T00:00:00Z',population='offered_my')
        self.assertEqual(report['n'],0);self.assertTrue(report['excluded'])

    def test_offline_cli_reads_explicit_bundle_without_writes_or_activation(self):
        bundle={'version':1,'receipts':[],'source_receipts':[],'revisions':[],'generation_records':[]}
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'bundle.json'
            path.write_text(json.dumps(bundle),encoding='utf-8')
            before=path.read_bytes()
            out,err=io.StringIO(),io.StringIO()
            with redirect_stdout(out),redirect_stderr(err):
                result=main(['--bundle',str(path),'--as-of-utc','2026-12-01T00:00:00Z',
                    '--population','offered_my','--bootstrap-replicates','200'])
            self.assertEqual(result,0);self.assertEqual(err.getvalue(),'')
            record=json.loads(out.getvalue())
            self.assertEqual(record['input_bundle_id'],fingerprint(bundle))
            self.assertIsNone(record['report']['candidate'])
            self.assertFalse(record['report']['automatic_promotion'])
            self.assertEqual(path.read_bytes(),before)
            self.assertEqual(list(Path(directory).iterdir()),[path])
            for bad in ('{"version":1,"version":1}', '{"version":NaN}',
                        json.dumps({**bundle,'version':True}),json.dumps({**bundle,'secret':'PRIVATE'})):
                path.write_text(bad,encoding='utf-8')
                out,err=io.StringIO(),io.StringIO()
                with redirect_stdout(out),redirect_stderr(err):
                    result=main(['--bundle',str(path),'--as-of-utc','2026-12-01T00:00:00Z',
                                 '--population','offered_my'])
                self.assertEqual(result,2);self.assertEqual(out.getvalue(),'')
                self.assertEqual(json.loads(err.getvalue())['reason'],'invalid_or_unavailable_evaluation_bundle')
                self.assertNotIn('PRIVATE',err.getvalue());self.assertNotIn(str(path),err.getvalue())

    @unittest.skipUnless(os.environ.get('NFL2026_CHECKOUT'),'set NFL2026_CHECKOUT for actual serving parity')
    def test_actual_nfl_serving_function_parity(self):
        checkout=Path(os.environ['NFL2026_CHECKOUT']).resolve()
        probes=[(p,c) for p in (.0000001,.049999,.374058,.5,.9000001,.9999999)
                for c in (None,{'a':.580223,'b':.969067},{'a':-2.,'b':1.3})]
        code='''import json,sys
sys.path.insert(0,sys.argv[1])
from scripts.learning_receipts import serve_probability
print(json.dumps([serve_probability(p,c) for p,c in json.load(sys.stdin)]))'''
        result=subprocess.run([sys.executable,'-c',code,str(checkout)],input=json.dumps(probes),capture_output=True,text=True,check=True,timeout=30)
        self.assertEqual(json.loads(result.stdout),[serve_base(p,c) for p,c in probes])


if __name__=='__main__': unittest.main()
