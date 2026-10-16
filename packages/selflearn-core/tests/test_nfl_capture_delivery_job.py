"""Internal flow/fault tests; the memory ledger is NOT SQL durability proof."""
import copy
import unittest
from unittest.mock import patch

from selflearn_core.adapters import nfl_capture_delivery_job as job
from selflearn_core.adapters import nfl_capture_delivery_journal as D
from selflearn_core.probability_receipts import fingerprint
from selflearn_core.store.receipt_journal import JournalConflict
from test_nfl_capture_job import MemoryJournal, PROCESSING
from test_nfl_capture_labels import journal_fixture, good_capture
import test_nfl_capture_evidence as E


def request(envelope,objects):
    pointers=[{'version':1,'kind':'selflearn.evidence_object.v1','scope':'uploaded_only','object_id':key,
               'raw_bytes':len(raw),'chunks':[{'chunk_id':key,'raw_bytes':len(raw)}]} for key,raw in sorted(objects.items())]
    return D.make_capture_delivery_request(envelope,pointers,destination_id=fingerprint('test-destination'),producer_id='nfl-test-producer')


class MemoryDeliveryJournal(MemoryJournal):
    def __init__(self,**kwargs):
        super().__init__(**kwargs)
        self.results,self.intents={},{}
        self.result_reads,self.delivery_writes=[],[]
        self.lose_delivery_response=False

    def get_capture_delivery(self,delivery_id):
        self.result_reads.append(delivery_id)
        result=copy.deepcopy(self.results.get(delivery_id))
        return None if result is None else D._result(result,delivery_id=delivery_id)

    def commit_capture_delivery(self,intent,binding,observations,proposals,*,watermark):
        self.delivery_writes.append(copy.deepcopy((intent,binding,observations,proposals,watermark)))
        delivery_id=intent['delivery_id']
        if delivery_id in self.results:
            if fingerprint(intent)!=fingerprint(self.intents[delivery_id]): raise JournalConflict('intent conflict')
            return copy.deepcopy(self.results[delivery_id])
        committed=self.commit_observed(observations,proposals,watermark=watermark)
        groups=copy.deepcopy(binding['groups'])
        status=('no_supported_membership' if not groups else 'accepted' if all(g['status']=='accepted' for g in groups)
                else 'partial' if any(g['status']=='accepted' for g in groups) else 'withheld')
        result={'version':1,'kind':'nfl.capture_delivery_result.v1','delivery_id':delivery_id,'request_id':fingerprint(intent),
          'capture_id':intent['capture_id'],'membership_id':binding['membership_id'],'status':status,'groups':groups,
          'added_observations':committed['added_observations'],'added_revisions':committed['added_revisions'],
          'input_watermark':watermark,'watermark':committed['watermark'],'delivery_seq':str(len(self.results)+1),
          'recorded_utc':PROCESSING,'durable':True,'scope':'observations_and_group_results','source_authentication':'not_performed'}
        D._result(result,request=intent)
        self.results[delivery_id],self.intents[delivery_id]=copy.deepcopy(result),copy.deepcopy(intent)
        if self.lose_delivery_response: raise TimeoutError('lost after both fake streams/result persisted')
        return copy.deepcopy(result)


def run(journal,intent,objects,**kwargs):
    return job.commit_capture_delivery(journal,intent,lambda key:E.stream(objects[key]),clock=lambda:PROCESSING,**kwargs)


class DeliveryJobTests(unittest.TestCase):
    def test_complete_raw_flow_then_original_recovery_before_clock_or_raw(self):
        journal=MemoryDeliveryJournal();envelope,objects=good_capture();intent=request(envelope,objects)
        first=run(journal,intent,objects)
        self.assertEqual(first['status'],'recorded_or_recovered')
        self.assertEqual(first['result']['status'],'accepted')
        self.assertEqual((first['result']['added_observations'],first['result']['added_revisions']),(4,4))
        def forbidden(*args): raise AssertionError('recovery must precede clock/raw/snapshot')
        with patch.object(journal,'read_observed_snapshot',side_effect=forbidden):
            recovered=job.commit_capture_delivery(journal,intent,forbidden,clock=forbidden)
        self.assertEqual(recovered['status'],'recovered_original')
        self.assertEqual(recovered['result'],first['result'])

    def test_lost_reply_then_expanded_membership_does_not_reinterpret_original_result(self):
        journal=MemoryDeliveryJournal();envelope,objects=good_capture();intent=request(envelope,objects)
        journal.lose_delivery_response=True
        with self.assertRaises(TimeoutError): run(journal,intent,objects)
        original=copy.deepcopy(journal.results[intent['delivery_id']])
        newer=journal_fixture(vintages=3,watermark='200')
        journal.snapshot,journal.objects=newer.snapshot,newer.objects
        recovered=run(journal,intent,objects)
        self.assertEqual(recovered['result'],original)
        self.assertEqual(len(recovered['result']['groups'][0]['receipt_ids']),4)
        self.assertEqual(len(journal.delivery_writes),1)

    def test_CAS_rebinds_every_member_and_all_raw_sources(self):
        journal=MemoryDeliveryJournal();envelope,objects=good_capture();intent=request(envelope,objects)
        def expand(authority):
            authority.before_commit=None
            newer=journal_fixture(vintages=3,watermark='200')
            authority.snapshot,authority.objects=newer.snapshot,newer.objects
        journal.before_commit=expand
        reads=[]
        def raw(key): reads.append(key);return E.stream(objects[key])
        result=job.commit_capture_delivery(journal,intent,raw,clock=lambda:PROCESSING)
        self.assertEqual(result['attempts'],2)
        self.assertEqual([len(row[2]) for row in journal.delivery_writes],[4,6])
        self.assertEqual([row[4] for row in journal.delivery_writes],['100','200'])
        self.assertEqual(len(result['result']['groups'][0]['receipt_ids']),6)
        self.assertEqual(len(reads),10)

    def test_whole_group_kernel_quarantine_keeps_independent_good_group(self):
        journal=MemoryDeliveryJournal()
        other=journal_fixture(player='Other Receiver',player_id='espn-other')
        journal.snapshot['receipts']+=other.snapshot['receipts']
        journal.snapshot['generation_records']+=other.snapshot['generation_records'];journal.objects.update(other.objects)
        stats=E.STATS.replace(b',,,NA',b',,,80')+b'2026,6,REG,WR,SEA,Other Receiver,,,80\r\n'
        envelope,objects=E.fixture(stats=stats,stats_audit=E.csv_audit(4));intent=request(envelope,objects)
        original=job.resolve_observed
        def incomplete(*args,**kwargs):
            result=original(*args,**kwargs)
            result['observation_proposals']=result['observation_proposals'][1:]
            return result
        with patch.object(job,'resolve_observed',side_effect=incomplete): result=run(journal,intent,objects)
        groups=result['result']['groups']
        self.assertEqual(sorted(g['status'] for g in groups),['accepted','withheld'])
        self.assertEqual(result['result']['status'],'partial')
        self.assertEqual(len(journal.delivery_writes[0][2]),4)
        held=next(g for g in groups if g['status']=='withheld')
        self.assertEqual(held['observation_ids'],[]);self.assertEqual(held['revision_ids'],[])

    def test_withheld_and_empty_population_are_explicit_original_results(self):
        for journal,pair,status in ((MemoryDeliveryJournal(),E.fixture(),'withheld'),
                                    (MemoryDeliveryJournal(vintages=0),good_capture(),'no_supported_membership')):
            envelope,objects=pair;intent=request(envelope,objects)
            result=run(journal,intent,objects)['result']
            self.assertEqual(result['status'],status)
            self.assertEqual((result['added_observations'],result['added_revisions']),(0,0))
            self.assertEqual(result['input_watermark'],result['watermark'])
            self.assertEqual(run(journal,intent,objects)['status'],'recovered_original')

    def test_request_metadata_mutation_is_rejected_before_io_and_callback_cannot_rewrite_frozen_intent(self):
        journal=MemoryDeliveryJournal();envelope,objects=good_capture();intent=request(envelope,objects)
        bad=copy.deepcopy(intent);bad['producer_id']='different'
        with self.assertRaises(ValueError): run(journal,bad,objects)
        self.assertEqual(journal.result_reads,[])
        expected=fingerprint(intent)
        def raw(key): intent['producer_id']='caller changed';return E.stream(objects[key])
        result=job.commit_capture_delivery(journal,intent,raw,clock=lambda:PROCESSING)
        self.assertEqual(result['result']['request_id'],expected)

    def test_tampered_raw_bytes_and_unlocatable_extra_proofs_never_commit(self):
        journal=MemoryDeliveryJournal();envelope,objects=good_capture();intent=request(envelope,objects)
        with self.assertRaises(ValueError):
            job.commit_capture_delivery(journal,intent,lambda key:iter([b'bad']),clock=lambda:PROCESSING)
        self.assertEqual(journal.delivery_writes,[])
        original=job.resolve_observed
        def extra(*args,**kwargs):
            result=original(*args,**kwargs)
            row=copy.deepcopy(result['observation_proposals'][0]);row['receipt_id']=fingerprint('extra')
            result['observation_proposals'].append(row)
            return result
        with patch.object(job,'resolve_observed',side_effect=extra),self.assertRaises(ValueError): run(journal,intent,objects)
        self.assertEqual(journal.delivery_writes,[])

    def test_wrong_chunk_pointer_cannot_hide_behind_correct_whole_object_reader(self):
        journal=MemoryDeliveryJournal();envelope,objects=good_capture();intent=request(envelope,objects)
        intent['raw_pointers'][0]['chunks'][0]['chunk_id']=fingerprint('missing-chunk')
        with self.assertRaises((ValueError,job.CaptureCommitError)):
            run(journal,intent,objects)
        self.assertEqual(journal.delivery_writes,[])
        self.assertEqual(journal.results,{})

    def test_limits_invalid_boundaries_and_conflict_exhaustion_never_return_success(self):
        journal=MemoryDeliveryJournal();envelope,objects=good_capture();intent=request(envelope,objects)
        for attempts in (True,0,6,1.5):
            with self.assertRaises(ValueError): run(journal,intent,objects,max_attempts=attempts)
        for limit in (True,-1,64*1024*1024+1):
            with self.assertRaises(ValueError): run(journal,intent,objects,max_total_bytes=limit)
        for authority,reader in ((None,lambda _:()),(journal,None)):
            with self.assertRaises(ValueError): job.commit_capture_delivery(authority,intent,reader)
        self.assertEqual(journal.result_reads,[])
        with patch.object(job,'MAX_BATCH_ITEMS',3),self.assertRaisesRegex(job.CaptureCommitError,'item_limit'): run(journal,intent,objects)
        with patch.object(job,'MAX_BATCH_BYTES',8),self.assertRaisesRegex(job.CaptureCommitError,'byte_limit'): run(journal,intent,objects)
        def conflict(authority): authority.snapshot['watermark']=str(int(authority.snapshot['watermark'])+1)
        journal.before_commit=conflict
        with self.assertRaisesRegex(JournalConflict,'capture_delivery_conflict_retry_exhausted'): run(journal,intent,objects,max_attempts=2)
        self.assertEqual(journal.results,{})

    def test_bad_snapshot_watermark_binding_and_result_identity_fail_closed(self):
        envelope,objects=good_capture();intent=request(envelope,objects)
        journal=MemoryDeliveryJournal()
        with patch.object(journal,'read_observed_snapshot',return_value={}):
            with self.assertRaisesRegex(job.CaptureCommitError,'invalid_capture_delivery_snapshot'): run(journal,intent,objects)
        binder=job.bind_capture_labels
        def bad_fence(*args,**kwargs): result=binder(*args,**kwargs);result['membership_watermark']='999';return result
        with patch.object(job,'bind_capture_labels',side_effect=bad_fence),self.assertRaisesRegex(job.CaptureCommitError,'binding_watermark'): run(journal,intent,objects)
        with patch.object(journal,'commit_capture_delivery',return_value={'durable':True}),self.assertRaisesRegex(job.CaptureCommitError,'identity_mismatch'): run(journal,intent,objects)
        journal.get_capture_delivery=lambda _: {'durable':True}
        with self.assertRaisesRegex(job.CaptureCommitError,'identity_mismatch'): run(journal,intent,objects)
        with self.assertRaises(ValueError): run(journal,{},objects)


if __name__=='__main__': unittest.main()
