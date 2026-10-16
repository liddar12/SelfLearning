// Fixed local-only RPC bridge: no network, arbitrary SQL or admin commands.
import {createInterface} from 'node:readline';
import {openJobDatabase,service} from './learning_jobs_harness.mjs';
const db=await openJobDatabase();const lines=createInterface({input:process.stdin,crlfDelay:Infinity});
const version=(await db.query('SELECT version() AS version')).rows[0].version;
process.stdout.write(JSON.stringify({ready:true,engine:'postgresql-wasm',version})+'\n');
try{
 for await(const line of lines){
  let value;
  try{
   if(Buffer.byteLength(line,'utf8')>4*1024*1024)throw new Error('fixture_request_limit');
   value=JSON.parse(line);
   if(value.method!=='POST'||typeof value.path!=='string'||!value.path.startsWith('/rest/v1/rpc/'))throw new Error('fixture_request_denied');
   const result=await service(db,value.path.slice('/rest/v1/rpc/'.length),value.data);
   process.stdout.write(JSON.stringify({id:value.id,ok:true,result})+'\n');
  }catch(error){process.stdout.write(JSON.stringify({id:value?.id,ok:false,code:error.code??'FIXTURE_DENIED'})+'\n');}
 }
}finally{await db.close();}
