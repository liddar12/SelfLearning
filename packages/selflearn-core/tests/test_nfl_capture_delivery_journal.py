"""Offline strict intent, whole-group filtering and original-result contracts."""
import copy
import unittest
from unittest import mock

from selflearn_core.adapters import nfl_capture_delivery_journal as D
from selflearn_core.adapters.nfl_observed_resolution import resolve_observed
from selflearn_core.probability_receipts import fingerprint
from selflearn_core.store.supabase_store import StoreError
import test_nfl_capture_labels as F
import test_nfl_capture_evidence as E


def fixture():
    journal = F.journal_fixture()
    envelope, objects = F.good_capture()
    pointers = [{"version":1,"kind":"selflearn.evidence_object.v1","scope":"uploaded_only","object_id":key,
                 "raw_bytes":len(raw),"chunks":[{"chunk_id":key,"raw_bytes":len(raw)}]} for key,raw in sorted(objects.items())]
    request = D.make_capture_delivery_request(envelope,pointers,destination_id=fingerprint('destination'),producer_id='nfl-fixture')
    staged = F.bind(journal,envelope,objects)
    kernel = resolve_observed(journal.snapshot['receipts'],staged['source_receipts'],staged['observed_legs'],staged['game_observations'],
            observed_utc='2026-10-21T00:00:00Z')
    binding,obs,rev = D.make_capture_delivery_binding(staged,kernel['observation_proposals'],kernel['proposals'])
    return journal,request,staged,binding,obs,rev


def result(request,binding,**extra):
    value={"version":1,"kind":"nfl.capture_delivery_result.v1","delivery_id":request['delivery_id'],
      "request_id":fingerprint(request),"capture_id":request['capture_id'],"membership_id":binding['membership_id'],
      "status":"accepted","groups":copy.deepcopy(binding['groups']),"added_observations":4,"added_revisions":4,
      "input_watermark":"100","watermark":"108","delivery_seq":"1","recorded_utc":"2026-10-21T00:00:00Z",
      "durable":True,"scope":"observations_and_group_results","source_authentication":"not_performed"}
    value.update(extra)
    return value


class Transport:
    def __init__(self, response, *, stored=False): self.response=response; self.calls=[]; self.stored=stored
    def request(self,method,path,*,data):
        self.calls.append(copy.deepcopy(data))
        if self.stored and path.endswith('selflearn_journal_read_capture_delivery_v1'):
            return {'version':1,'result':{k:v for k,v in self.response.items() if k!='groups'},'groups':self.response['groups'],
                    'total_groups':len(self.response['groups']),'next_after':None}
        return copy.deepcopy(self.response)


class DeliveryTests(unittest.TestCase):
    def test_stable_presend_intent_and_snapshot(self):
        _,request,_,binding,_,_=fixture()
        rebuilt=D._request(request)
        self.assertEqual(rebuilt,request)
        request['raw_pointers'].reverse()
        with self.assertRaises(ValueError): D._request(request)
        self.assertNotIn('membership_id',rebuilt)
        self.assertEqual(binding['groups'][0]['status'],'accepted')

    def test_pointer_metadata_rejects_bad_shape_missing_duplicate_budget(self):
        _,request,*_=fixture()
        for change in ('missing','duplicate','badframe','url','boolean','wrongid'):
            bad=copy.deepcopy(request)
            if change=='missing': bad['raw_pointers'].pop()
            if change=='duplicate': bad['raw_pointers'].append(copy.deepcopy(bad['raw_pointers'][0]))
            if change=='badframe': bad['raw_pointers'][0]['chunks'][0]['raw_bytes']+=1
            if change=='url': bad['raw_pointers'][0]['url']='https://invalid.example'
            if change=='boolean': bad['version']=True
            if change=='wrongid': bad['delivery_id']=fingerprint('other')
            with self.subTest(change=change),self.assertRaises(ValueError): D._request(bad)

    def test_missing_member_holds_whole_group_not_partial_proofs(self):
        _,_,staged,_,obs,rev=fixture()
        binding,filtered_obs,filtered_rev=D.make_capture_delivery_binding(staged,obs[1:],rev)
        self.assertEqual((filtered_obs,filtered_rev),([],[]))
        self.assertEqual(binding['groups'][0]['status'],'withheld')
        self.assertEqual(binding['groups'][0]['reason_codes'],['kernel_group_incomplete'])

    def test_extra_duplicate_or_unlocatable_proof_fails_closed(self):
        _,_,staged,_,obs,rev=fixture()
        bad=copy.deepcopy(obs[0]);bad['receipt_id']=fingerprint('unknown')
        for rows,proposals in ((obs+[obs[0]],rev),(obs+[bad],rev),(obs,rev+[rev[0]])):
            with self.assertRaises(ValueError): D.make_capture_delivery_binding(staged,rows,proposals)

    def test_old_result_recovery_not_reinterpreted_as_current_membership(self):
        _,request,_,binding,obs,rev=fixture()
        original=result(request,binding)
        transport=Transport(original,stored=True)
        journal=D.NFLCaptureDeliveryJournal('https://offline.supabase.invalid','sb_secret_offline',transport=transport)
        ack=journal.commit_capture_delivery(request,{'current_membership':'different'},[],[],watermark='999')
        self.assertEqual(ack,original)
        self.assertEqual(transport.calls[0]['p_watermark'],'999')
        self.assertIsNone(journal._observed_snapshot)

    def test_fresh_zero_or_partial_count_ack_requires_durable_readback(self):
        fixture_journal,request,_,binding,obs,rev=fixture()
        for a,b in ((0,0),(0,4),(4,0),(3,4),(4,3)):
            ack=result(request,binding,added_observations=a,added_revisions=b,watermark='100' if a+b==0 else '108')
            transport=Transport(ack)
            journal=D.NFLCaptureDeliveryJournal('https://offline.supabase.invalid','sb_secret_offline',transport=transport)
            journal._observed_snapshot=copy.deepcopy(fixture_journal.snapshot)
            with self.subTest(counts=(a,b)),self.assertRaises(StoreError):
                journal.commit_capture_delivery(request,binding,obs,rev,watermark='100')

    def test_same_fence_original_withheld_result_recovers_only_by_readback(self):
        fixture_journal,request,_,binding,obs,rev=fixture()
        held=copy.deepcopy(binding)
        held['groups'][0].update(status='withheld',reason_codes=['kernel_group_incomplete'],observation_ids=[],revision_ids=[])
        original=result(request,held,status='withheld',added_observations=0,added_revisions=0,watermark='100')
        transport=Transport(original,stored=True)
        journal=D.NFLCaptureDeliveryJournal('https://offline.supabase.invalid','sb_secret_offline',transport=transport)
        journal._observed_snapshot=copy.deepcopy(fixture_journal.snapshot)
        self.assertEqual(journal.commit_capture_delivery(request,binding,obs,rev,watermark='100'),original)
        self.assertEqual(len(transport.calls),2)

    def test_result_schema_type_identity_count_clock_hash_fail_closed(self):
        _,request,_,binding,_,_=fixture()
        for key,value in [('durable',1),('added_observations',True),('added_revisions',5),('delivery_seq','0'),
                          ('watermark','100'),('recorded_utc','2026-01-01T00:00:00Z'),('membership_id',fingerprint('bad'))]:
            with self.subTest(key=key),self.assertRaises(ValueError): D._result(result(request,binding,**{key:value}),request=request)

    def test_complete_paged_result_and_missing(self):
        _,request,_,binding,_,_=fixture()
        ack=result(request,binding); summary={k:v for k,v in ack.items() if k!='groups'}
        transport=Transport({'version':1,'result':summary,'groups':ack['groups'],'total_groups':1,'next_after':None})
        journal=D.NFLCaptureDeliveryJournal('https://offline.supabase.invalid','sb_secret_offline',transport=transport,page_size=1)
        self.assertEqual(journal.get_capture_delivery(request['delivery_id']),ack)
        transport.response={'version':1,'result':None,'groups':[],'total_groups':0,'next_after':None}
        self.assertIsNone(journal.get_capture_delivery(request['delivery_id']))
        transport.response={'version':1,'result':summary,'groups':[],'total_groups':1,'next_after':None}
        with self.assertRaises(StoreError): journal.get_capture_delivery(request['delivery_id'])

    def test_malformed_ack_or_exception_never_returns_success(self):
        _,request,_,binding,obs,rev=fixture()
        transport=Transport({'version':1})
        journal=D.NFLCaptureDeliveryJournal('https://offline.supabase.invalid','sb_secret_offline',transport=transport)
        with self.assertRaises(StoreError): journal.commit_capture_delivery(request,binding,obs,rev,watermark='100')

    def test_helper_guards_bound_before_sending(self):
        _,request,_,binding,obs,rev=fixture()
        for value in (None,True,'sha256:no'):
            with self.assertRaises(ValueError): D._hash(value)
        for value in (None,True,'00','-1'):
            with self.assertRaises(ValueError): D._sequence(value)
        with self.assertRaises(ValueError): D._bounded('x'*(D.MAX_REQUEST_BYTES+1),D.MAX_REQUEST_BYTES)
        for producer in ('','contains space','ümlaut',True):
            with self.assertRaises(ValueError):
                D.make_capture_delivery_request(request['envelope'],request['raw_pointers'],destination_id=request['destination_id'],producer_id=producer)
        with self.assertRaises(ValueError): D._request([])
        for pointers in (None,request['raw_pointers']*7):
            with self.assertRaises(ValueError):
                D.make_capture_delivery_request(request['envelope'],pointers,destination_id=request['destination_id'],producer_id=request['producer_id'])
        huge=[]
        for tag in ('big1','big2'):
            huge.append({'version':1,'kind':'selflearn.evidence_object.v1','scope':'uploaded_only','object_id':fingerprint(tag),
              'raw_bytes':33*1024*1024,'chunks':[{'chunk_id':fingerprint(tag),'raw_bytes':1024*1024} for _ in range(33)]})
        with self.assertRaises(ValueError):
            D.make_capture_delivery_request(request['envelope'],huge,destination_id=request['destination_id'],producer_id=request['producer_id'])

    def test_strict_group_and_identifier_shape_guards(self):
        _,request,_,binding,_,_=fixture()
        good=binding['groups'][0]
        for values in (None,[],[True],[''],[fingerprint('b'),fingerprint('a')],[fingerprint('a')]*2):
            with self.subTest(values=values),self.assertRaises(ValueError): D._ids(values,empty=False)
        variants=[None,{}, {**good,'scope':[True,*good['scope'][1:]]}, {**good,'source_receipt_ids':good['source_receipt_ids'][:-1]},
                  {**good,'membership_id':fingerprint('wrong')}, {**good,'reason_codes':['NOT_VALID']},
                  {**good,'status':'withheld','reason_codes':['held']}, {**good,'observation_ids':[]},
                  {**good,'revision_ids':['z','a']}]
        for value in variants:
            with self.subTest(value=value),self.assertRaises(ValueError): D._group(value)

    def test_binding_population_and_status_guards(self):
        _,_,staged,_,obs,rev=fixture()
        with self.assertRaises(ValueError): D.make_capture_delivery_binding({},obs,rev)
        for mutate in (lambda s:s['groups'].append(copy.deepcopy(s['groups'][0])),
                       lambda s:s['groups'][0].update(status='accepted'),
                       lambda s:s.update(membership_id=fingerprint('wrong'))):
            bad=copy.deepcopy(staged);mutate(bad)
            with self.assertRaises(ValueError): D.make_capture_delivery_binding(bad,obs,rev)
        bad=copy.deepcopy(staged)
        other=copy.deepcopy(bad['groups'][0]);other['group_id']=fingerprint('other')
        bad['groups'].append(other)
        with self.assertRaises(ValueError): D.make_capture_delivery_binding(bad,obs,rev)
        bad=copy.deepcopy(staged);bad['groups'][0]['receipt_ids']=[fingerprint(n) for n in range(501)]
        with self.assertRaises(ValueError): D.make_capture_delivery_binding(bad,[],[])
        bad_rev=copy.deepcopy(rev);bad_rev[0]['resolution']['receipt_id']=fingerprint('extra')
        with self.assertRaises(ValueError): D.make_capture_delivery_binding(staged,obs,bad_rev)

    def test_result_self_consistency_missing_wrong_identity_and_duplicates(self):
        _,request,_,binding,_,_=fixture()
        ack=result(request,binding)
        for mutate in (lambda r:r.pop('durable'),lambda r:r.update(delivery_id=fingerprint('other')),
                       lambda r:r.update(request_id=fingerprint('other')),lambda r:r.update(groups=None),
                       lambda r:r['groups'].append(copy.deepcopy(r['groups'][0])),lambda r:r.update(status='partial')):
            bad=copy.deepcopy(ack);mutate(bad)
            with self.assertRaises(ValueError): D._result(bad,request=request,delivery_id=request['delivery_id'])

    def test_fresh_valid_ack_loads_missing_pinned_snapshot_and_checks_exact_counts(self):
        fixture_journal,request,_,binding,obs,rev=fixture()
        ack=result(request,binding);transport=Transport(ack)
        journal=D.NFLCaptureDeliveryJournal('https://offline.supabase.invalid','sb_secret_offline',transport=transport)
        with mock.patch.object(journal,'_load_observed_snapshot',return_value=copy.deepcopy(fixture_journal.snapshot)) as load:
            self.assertEqual(journal.commit_capture_delivery(request,binding,obs,rev,watermark='100'),ack)
            load.assert_called_once_with('100')
        with self.assertRaises(ValueError): journal.commit_capture_delivery(request,None,obs,rev,watermark='100')
        with self.assertRaises(ValueError): journal.commit_capture_delivery(request,binding,obs*126,rev,watermark='100')

    def test_pagination_two_pages_and_changed_summary_cursor_missing_rows_rejected(self):
        _,request,_,binding,_,_=fixture()
        ack=result(request,binding)
        other=copy.deepcopy(ack['groups'][0]);other['scope'][2]='other-player';other['group_id']=fingerprint(other['scope'])
        for key in ('receipt_ids','source_receipt_ids','generation_object_ids','observation_ids'):
            other[key]=sorted(fingerprint([key,n]) for n in range(len(other[key])))
        other['revision_ids']=sorted('other-'+str(n) for n in range(len(other['receipt_ids'])))
        other['membership_id']=fingerprint({'binding_policy':D.POLICY,**{key:other[key] for key in ('scope','receipt_ids','source_receipt_ids','generation_object_ids')}})
        ack['groups'].append(other);ack['membership_id']=fingerprint({'binding_policy':D.POLICY,'groups':[g['membership_id'] for g in ack['groups']]})
        summary={key:value for key,value in ack.items() if key!='groups'}
        first={'version':1,'result':summary,'groups':[ack['groups'][0]],'total_groups':2,'next_after':'1'}
        second={'version':1,'result':summary,'groups':[other],'total_groups':2,'next_after':None}
        class Pages:
            def __init__(self,pages): self.pages=iter(copy.deepcopy(pages))
            def request(self,*args,**kwargs): return next(self.pages)
        def journal(pages):
            return D.NFLCaptureDeliveryJournal('https://offline.supabase.invalid','sb_secret_offline',transport=Pages(pages),page_size=1)
        self.assertEqual(journal([first,second]).get_capture_delivery(request['delivery_id']),ack)
        variants=[{**first,'groups':None},{**first,'result':None},{**first,'result':{}},{**first,'groups':ack['groups']},
                  {**first,'next_after':'2'},{**first,'next_after':'1','groups':[]},
                  {**first,'total_groups':0},{**first,'next_after':None}]
        for page in variants:
            with self.subTest(page=page),self.assertRaises(StoreError): journal([page]).get_capture_delivery(request['delivery_id'])
        changed=copy.deepcopy(second);changed['result']['recorded_utc']='2026-10-22T00:00:00Z'
        with self.assertRaises(StoreError): journal([first,changed]).get_capture_delivery(request['delivery_id'])


if __name__=='__main__': unittest.main()
