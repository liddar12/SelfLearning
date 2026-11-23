import assert from 'node:assert/strict';
import test from 'node:test';
import {createHash} from 'node:crypto';
import {openGameCaptureApplicationDatabase,serviceV2} from './capture_application_harness.mjs';
import {canonical,digest,asRole,appendObject} from './harness.mjs';
const read=(db,ids,{after=null,limit=500,watermark=null}={})=>serviceV2(db,'selflearn_journal_read_objects_v1',
  ['objects',canonical([...ids].sort()),after,limit,watermark]);

test('exact immutable object batches retain raw number lexemes, clocks and global watermark',async()=>{
  const db=await openGameCaptureApplicationDatabase();
  try {
    const raw=['{"number":1}','{"number":1.0}'],ids=raw.map(x=>'sha256:'+createHash('sha256').update(x).digest('hex'));
    for(let i=0;i<raw.length;i++)await asRole(db,'service_role',()=>db.query(
      'select public.selflearn_journal_append_object_v1($1,$2,$3,null)',['source_snapshot',ids[i],raw[i]]));
    const first=await read(db,ids,{limit:1});
    assert.equal(first.rows.length,1);assert.equal(first.rows[0].payload_json,raw[0]);assert.ok(first.next_after);
    const later={late:true};await appendObject(db,'source_snapshot',digest(later),later);
    const last=await read(db,ids,{after:first.next_after,limit:1,watermark:first.watermark});
    assert.equal(last.rows[0].payload_json,raw[1]);assert.equal(last.next_after,null);assert.equal(last.watermark,first.watermark);
    const direct=(await db.query('select object_id,payload_json,stored_utc from selflearn_private.journal_objects where object_id=any($1::text[])',[ids])).rows;
    for(const row of [...first.rows,...last.rows]) {
      assert.equal(row.payload_json,direct.find(x=>x.object_id===row.object_id).payload_json);
      assert.equal(Date.parse(row.received_utc),direct.find(x=>x.object_id===row.object_id).stored_utc.getTime());
    }
    assert.equal((await read(db,[digest(later)],{watermark:first.watermark})).rows.length,0);
    assert.equal((await read(db,[digest('missing')])).rows.length,0);
    for(const role of ['anon','authenticated'])await assert.rejects(asRole(db,role,()=>db.query(
      'select public.selflearn_journal_read_objects_v1($1,$2,null,10,null)',['objects',canonical([...ids].sort())])),e=>e.code==='42501');
  } finally {await db.close();}
});

test('object batch IDs/cursors are bounded and source snapshots share pages below 6MiB',async()=>{
  const db=await openGameCaptureApplicationDatabase();
  try {
    const ids=[];
    for(let i=0;i<5;i++) {
      const payload={index:i,padding:'x'.repeat(1500000)},id=digest(payload);
      await appendObject(db,'source_snapshot',id,payload);ids.push(id);
    }
    let after=null,watermark=null,pages=0,rows=0;
    do {
      const page=await read(db,ids,{after,watermark});
      assert.ok(Buffer.byteLength(JSON.stringify(page))<6*1024*1024);
      assert.ok(page.rows.length>0);pages++;rows+=page.rows.length;after=page.next_after;watermark=page.watermark;
    } while(after);
    assert.equal(rows,5);assert.equal(pages,2);
    for(const invalid of [[],[ids[0],ids[0]],[ids[0],'bad'],Array.from({length:257},(_,i)=>digest(i))]) {
      await assert.rejects(serviceV2(db,'selflearn_journal_read_objects_v1',['objects',canonical(invalid),null,500,null]),e=>e.code==='PT422');
    }
    await assert.rejects(read(db,ids,{watermark:'999999999'}),e=>e.code==='PT422');
    await assert.rejects(read(db,ids,{after:'-1'}),e=>e.code==='PT422');
    await assert.rejects(read(db,ids,{limit:501}),e=>e.code==='PT422');
  } finally {await db.close();}
});

