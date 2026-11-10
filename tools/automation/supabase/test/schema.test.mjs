import { after, before, test } from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { PGlite } from '@electric-sql/pglite';

const migration = await readFile(new URL('../supabase/migrations/20261110210410_trade_bot_prediction_store.sql', import.meta.url), 'utf8');
let db;
const insert = `INSERT INTO public.predictions(id,ts,model_version,task,features,prediction,horizon_s)
  VALUES ('p1',1780000000,'m1','scanner','{}','{"symbol":"SPY"}',600)`;

async function asRole(role, sql) {
  await db.exec(`SET ROLE ${role}`);
  try { return await db.query(sql); } finally { await db.exec('RESET ROLE'); }
}

before(async () => {
  db = new PGlite();
  await db.exec('CREATE ROLE anon; CREATE ROLE authenticated; CREATE ROLE service_role BYPASSRLS;');
  await db.exec(migration);
});
after(async () => { await db?.close(); });

test('schema has receipt timestamps, four RLS tables and correct foreign key', async () => {
  const rls = await db.query("SELECT relname,relrowsecurity FROM pg_class WHERE relname IN ('predictions','outcomes','registry','registry_scores') ORDER BY relname");
  assert.equal(rls.rows.length, 4); assert.ok(rls.rows.every((r) => r.relrowsecurity));
  const foreign = await db.query("SELECT confrelid::regclass::text AS target FROM pg_constraint WHERE conrelid='public.outcomes'::regclass AND contype='f'");
  assert.equal(foreign.rows[0].target, 'predictions');
  const policy = await db.query("SELECT * FROM pg_policies WHERE schemaname='public'");
  assert.equal(policy.rows.length, 0);
});

test('service role appends and reads records with database-owned receipt time', async () => {
  await asRole('service_role', insert);
  const { rows } = await asRole('service_role', "SELECT id,received_at FROM public.predictions WHERE id='p1'");
  assert.equal(rows[0].id, 'p1'); assert.ok(rows[0].received_at);
  await asRole('service_role', `INSERT INTO public.outcomes(prediction_id,resolved_ts,realized) VALUES ('p1',1780000600,'{"hit":true}')`);
  const status = await asRole('service_role', 'SELECT public.selflearn_status() AS status');
  assert.equal(status.rows[0].status.predictions, 1); assert.equal(status.rows[0].status.outcomes, 1);
  assert.equal(status.rows[0].status.unresolved, 0);
});

test('anonymous and signed-in browser roles cannot read, insert or invoke status', async () => {
  for (const role of ['anon', 'authenticated']) {
    for (const sql of ['SELECT * FROM public.predictions', insert.replace("'p1'", "'forbidden'"), 'SELECT public.selflearn_status()']) {
      await assert.rejects(asRole(role, sql), /permission denied/);
    }
  }
});

test('service role cannot forge receipt time, edit, delete, truncate or promote', async () => {
  for (const sql of [
    `INSERT INTO public.predictions(id,ts,model_version,task,features,prediction,horizon_s,received_at) VALUES('forged',0,'m','scanner','{}','{}',1,'2000-01-01')`,
    "UPDATE public.predictions SET ts=1 WHERE id='p1'", "DELETE FROM public.predictions WHERE id='p1'",
    'TRUNCATE public.predictions CASCADE', "UPDATE public.registry SET status='live'",
    "INSERT INTO public.registry(version,task,config,created_ts) VALUES('bad','scanner','{}',0)",
  ]) await assert.rejects(asRole('service_role', sql), /permission denied/);
});

test('database constraints reject duplicate IDs, missing predictions and invalid confidence', async () => {
  await assert.rejects(asRole('service_role', insert), /duplicate key/);
  await assert.rejects(asRole('service_role', `INSERT INTO public.outcomes(prediction_id,resolved_ts,realized) VALUES ('missing',1,'{}')`), /foreign key/);
  await assert.rejects(asRole('service_role', `INSERT INTO public.predictions(id,ts,model_version,task,features,prediction,horizon_s,confidence) VALUES('bad',0,'m','scanner','{}','{}',1,1.1)`), /check constraint/);
  await assert.rejects(asRole('service_role', insert.replace("'p1'", "'zero'").replace(',600)', ',0)')), /check constraint/);
});

test('immutable triggers also prevent accidental administrator mutation', async () => {
  for (const sql of ["UPDATE public.predictions SET ts=1 WHERE id='p1'", "DELETE FROM public.outcomes WHERE prediction_id='p1'", 'TRUNCATE public.predictions CASCADE']) {
    await assert.rejects(db.exec(sql), /append-only/);
  }
});

test('migration refuses an existing public schema and rolls back cleanly', async () => {
  await assert.rejects(db.exec(migration), /Fresh dedicated project required/);
  await db.exec('ROLLBACK');
  const result = await asRole('service_role', 'SELECT count(*)::integer AS n FROM public.predictions');
  assert.equal(result.rows[0].n, 1);
});
