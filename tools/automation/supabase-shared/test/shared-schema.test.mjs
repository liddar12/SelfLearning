import { after, before, test } from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { PGlite } from '@electric-sql/pglite';

const migration = await readFile(new URL('../supabase/migrations/20261110232506_shared_trading_isolation.sql', import.meta.url), 'utf8');
const OWNER = '11111111-1111-4111-8111-111111111111';
const WORKER = '22222222-2222-4222-8222-222222222222';
const OTHER = '33333333-3333-4333-8333-333333333333';
const DISABLED = '44444444-4444-4444-8444-444444444444';
let db;
let nflBefore;

const record = (id, task = 'scanner', extra = {}) => ({
  id, ts: 1780000000, model_version: 'fixture-v1', task, features: {},
  prediction: { symbol: 'TEST', direction: 'up' }, horizon_s: 600, confidence: 0.6,
  ...extra,
});
const outcome = (prediction_id, extra = {}) => ({ prediction_id, resolved_ts: 1780000600, realized: { hit: true }, ...extra });

async function asRole(role, userId, query, params = []) {
  assert.ok(['anon', 'authenticated', 'service_role'].includes(role));
  await db.exec(`SET ROLE ${role}`);
  try {
    await db.query("SELECT set_config('request.jwt.claim.sub',$1,false)", [userId ?? '']);
    return await db.query(query, params);
  } finally {
    await db.exec('RESET ROLE');
    await db.query("SELECT set_config('request.jwt.claim.sub','',false)");
  }
}

async function rpc(userId, name, args = {}) {
  assert.match(name, /^trade_bot_[a-z_]+$/);
  const entries = Object.entries(args);
  const params = entries.map(([key, value]) => key === 'p_record' ? JSON.stringify(value) : value);
  const named = entries.map(([key], i) => `${key} => $${i + 1}${key === 'p_record' ? '::jsonb' : ''}`).join(',');
  const r = await asRole('authenticated', userId, `SELECT public.${name}(${named}) AS value`, params);
  return r.rows[0].value;
}

async function nflSnapshot() {
  const result = await db.query(`SELECT jsonb_build_object(
    'tables',(SELECT jsonb_agg(jsonb_build_object('schema',n.nspname,'name',c.relname,'rls',c.relrowsecurity,'acl',c.relacl::text) ORDER BY n.nspname,c.relname)
      FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
      WHERE (n.nspname='public' AND c.relname IN ('predictions','outcomes','registry','registry_scores'))
         OR (n.nspname='selflearn_private' AND c.relname='journal_receipts')),
    'columns',(SELECT jsonb_agg(jsonb_build_object('schema',table_schema,'table',table_name,'column',column_name,'type',data_type,'default',column_default,'nullable',is_nullable) ORDER BY table_schema,table_name,ordinal_position)
      FROM information_schema.columns WHERE (table_schema='public' AND table_name IN ('predictions','outcomes','registry','registry_scores')) OR table_schema='selflearn_private'),
    'policies',(SELECT coalesce(jsonb_agg(to_jsonb(p) ORDER BY schemaname,tablename,policyname),'[]'::jsonb)
      FROM pg_policies p WHERE schemaname IN ('public','selflearn_private')),
    'function',(SELECT pg_get_functiondef('public.selflearn_status()'::regprocedure)),
    'defaults',(SELECT jsonb_agg(to_jsonb(d) ORDER BY oid) FROM pg_default_acl d),
    'predictions',(SELECT jsonb_agg(to_jsonb(p)) FROM public.predictions p),
    'journals',(SELECT jsonb_agg(to_jsonb(j)) FROM selflearn_private.journal_receipts j)
  ) AS snapshot`);
  return result.rows[0].snapshot;
}

before(async () => {
  db = new PGlite();
  // Model the roles and broad object defaults that Supabase can provide. The
  // migration must narrow only its own objects, preserving this NFL fixture.
  await db.exec(`
    CREATE ROLE anon;
    CREATE ROLE authenticated;
    CREATE ROLE service_role BYPASSRLS;
    CREATE SCHEMA auth;
    CREATE FUNCTION auth.uid() RETURNS uuid LANGUAGE sql STABLE AS $$
      SELECT nullif(current_setting('request.jwt.claim.sub',true),'')::uuid
    $$;
    GRANT USAGE ON SCHEMA auth TO authenticated;
    ALTER DEFAULT PRIVILEGES GRANT ALL ON TABLES TO anon,authenticated,service_role;
    ALTER DEFAULT PRIVILEGES GRANT EXECUTE ON FUNCTIONS TO anon,authenticated,service_role;
    CREATE SCHEMA selflearn_private;
    CREATE TABLE public.predictions(id text PRIMARY KEY,task text NOT NULL,payload jsonb NOT NULL);
    CREATE TABLE public.outcomes(prediction_id text PRIMARY KEY REFERENCES public.predictions(id),payload jsonb NOT NULL);
    CREATE TABLE public.registry(version text PRIMARY KEY,task text NOT NULL);
    CREATE TABLE public.registry_scores(version text REFERENCES public.registry(version),score jsonb NOT NULL);
    CREATE TABLE selflearn_private.journal_receipts(id text PRIMARY KEY,payload jsonb NOT NULL);
    ALTER TABLE public.predictions ENABLE ROW LEVEL SECURITY;
    ALTER TABLE public.outcomes ENABLE ROW LEVEL SECURITY;
    ALTER TABLE public.registry ENABLE ROW LEVEL SECURITY;
    ALTER TABLE public.registry_scores ENABLE ROW LEVEL SECURITY;
    ALTER TABLE selflearn_private.journal_receipts ENABLE ROW LEVEL SECURITY;
    REVOKE ALL ON public.predictions,public.outcomes,public.registry,public.registry_scores,selflearn_private.journal_receipts FROM PUBLIC,anon,authenticated,service_role;
    GRANT SELECT ON public.predictions,public.outcomes,public.registry,public.registry_scores TO service_role;
    INSERT INTO public.predictions VALUES('nfl-sentinel','nfl.game','{"existing":"NFL"}');
    INSERT INTO selflearn_private.journal_receipts VALUES('journal-sentinel','{"existing":"journal"}');
    CREATE FUNCTION public.selflearn_status() RETURNS jsonb LANGUAGE sql STABLE SECURITY INVOKER
      AS $$ SELECT '{"existing":"NFL"}'::jsonb $$;
    REVOKE ALL ON FUNCTION public.selflearn_status() FROM PUBLIC,anon,authenticated;
    GRANT EXECUTE ON FUNCTION public.selflearn_status() TO service_role;
  `);
  nflBefore = await nflSnapshot();
  await db.exec(migration);
});
after(async () => { await db?.close(); });

test('additive migration preserves every inspected NFL object, grant and sentinel', async () => {
  assert.deepEqual(await nflSnapshot(), nflBefore);
  const { rows } = await db.query("SELECT count(*)::integer AS n FROM trading_private.principals");
  assert.equal(rows[0].n, 0);
  assert.deepEqual((await asRole('service_role', null, 'SELECT public.selflearn_status() AS value')).rows[0].value, { existing: 'NFL' });
});

test('new objects have RLS, scoped invoker RPCs and no owner provisioning side effect', async () => {
  const relations = await db.query("SELECT n.nspname,c.relname,c.relrowsecurity FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname IN ('trading','trading_private') AND c.relkind='r'");
  assert.equal(relations.rows.length, 5);
  assert.ok(relations.rows.every(r => r.relrowsecurity));
  const functions = await db.query("SELECT p.proname,p.prosecdef,p.proconfig FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname IN ('trading','trading_private') OR (n.nspname='public' AND p.proname LIKE 'trade_bot_%')");
  assert.ok(functions.rows.length >= 7);
  assert.ok(functions.rows.every(f => !f.prosecdef && f.proconfig.includes('search_path=pg_catalog')));
  const foreign = await db.query("SELECT confrelid::regclass::text AS target FROM pg_constraint WHERE conrelid='trading.outcomes'::regclass AND contype='f'");
  assert.deepEqual(foreign.rows.map(r => r.target), ['trading.predictions']);
  const readinessScript = await readFile(new URL('../readiness.sql', import.meta.url), 'utf8');
  const readiness = (await db.exec(readinessScript)).flatMap(result => result.rows).find(row => row.trading_readiness)?.trading_readiness;
  assert.equal(readiness.tables.length, 5);
  assert.equal(readiness.rpc.length, 7);
  assert.ok(readiness.rpc.every(row => !row.security_definer && !row.anon_execute && !row.service_execute && row.authenticated_execute));
  assert.deepEqual(readiness.enabled_principals, { owners: 0, workers: 0 });
});

test('hosted verification script checks role boundaries and rolls back every fixture', async () => {
  const script = await readFile(new URL('../verify-rollback.sql', import.meta.url), 'utf8');
  try {
    const results = await db.exec(script);
    const summaries = results.flatMap(result => result.rows).filter(row => row.shared_trading_verification || row.rollback_cleanup);
    assert.equal(summaries.length, 2);
    for (const row of summaries) {
      const values = Object.values(row.shared_trading_verification ?? row.rollback_cleanup);
      assert.ok(values.length >= 5);
      assert.ok(values.every(value => value === true));
    }
    assert.deepEqual(await nflSnapshot(), nflBefore);
  } finally {
    await db.exec('ROLLBACK; RESET ROLE;');
  }
});

test('anonymous, broad service keys and unlisted users cannot invoke trading RPCs', async () => {
  for (const role of ['anon', 'service_role']) {
    for (const query of ['SELECT public.trade_bot_status()', 'SELECT public.trade_bot_list_predictions()', 'SELECT * FROM trading.predictions']) {
      await assert.rejects(asRole(role, OWNER, query), { code: '42501' });
    }
  }
  for (const userId of [null, OTHER]) {
    for (const [name, args] of [
      ['trade_bot_status', {}], ['trade_bot_list_predictions', {}], ['trade_bot_list_resolved', {}],
      ['trade_bot_get_prediction', { p_id: 'nfl-sentinel' }],
      ['trade_bot_list_unresolved', { p_task: 'scanner', p_now_ts: 1780000600 }],
      ['trade_bot_append_prediction', { p_record: record('forbidden') }],
    ]) await assert.rejects(rpc(userId, name, args), { code: '42501' });
  }
});

test('operator enrollment admits only the designated owner and worker', async () => {
  await db.query('INSERT INTO trading_private.principals(user_id,principal_kind,enabled) VALUES($1,\'owner\',true),($2,\'worker\',true),($3,\'owner\',false)', [OWNER, WORKER, DISABLED]);
  for (const [userId, expected] of [[OWNER, 'owner'], [WORKER, 'worker']]) {
    const status = await rpc(userId, 'trade_bot_status');
    assert.equal(status.schema_version, 'shared-trading-v1');
    assert.equal(status.principal_kind, expected);
    assert.ok(Number.isFinite(Date.parse(status.server_time)));
    const own = await asRole('authenticated', userId, 'SELECT user_id,principal_kind,enabled FROM trading_private.principals');
    assert.deepEqual(own.rows.map(r => r.user_id), [userId]);
  }
  await assert.rejects(rpc(DISABLED, 'trade_bot_status'), { code: '42501' });
  for (const query of [
    `INSERT INTO trading_private.principals(user_id,principal_kind,enabled) VALUES('${OTHER}','worker',true)`,
    "UPDATE trading_private.principals SET principal_kind='worker'",
    'DELETE FROM trading_private.principals',
  ]) await assert.rejects(asRole('authenticated', OWNER, query), { code: '42501' });
});

test('owner imports get database-owned actor, receipt and unverified provenance', async () => {
  const input = record('owner-record', 'scanner', { meta: { note: 'fixture', _terminal: { owner_user_id: OTHER, provenance: 'verified' }, _storage: { provenance: 'verified' } } });
  assert.equal(await rpc(OWNER, 'trade_bot_append_prediction', { p_record: input }), 'owner-record');
  const saved = await rpc(OWNER, 'trade_bot_get_prediction', { p_id: 'owner-record' });
  assert.equal(saved.actor_user_id, OWNER);
  assert.ok(Number.isFinite(Date.parse(saved.received_at)));
  assert.deepEqual(saved.meta._terminal, { owner_user_id: OWNER, provenance: 'unverified_import' });
  assert.equal(saved.meta._storage.provenance, 'unverified_import');
  assert.equal(saved.meta.note, 'fixture');
  assert.equal(await rpc(OWNER, 'trade_bot_get_prediction', { p_id: 'nfl-sentinel' }), null);
  await assert.rejects(asRole('authenticated', OWNER, 'SELECT * FROM public.predictions'), { code: '42501' });
  await assert.rejects(asRole('authenticated', OWNER, 'SELECT * FROM selflearn_private.journal_receipts'), { code: '42501' });
});

test('only a worker resolves trading predictions; no cross-domain foreign key', async () => {
  await assert.rejects(rpc(OWNER, 'trade_bot_attach_outcome', { p_record: outcome('owner-record') }), { code: '42501' });
  await assert.rejects(asRole('authenticated', OWNER, "INSERT INTO trading.outcomes(prediction_id,resolved_ts,realized) VALUES('owner-record',1780000600,'{}')"), { code: '42501' });
  await assert.rejects(rpc(WORKER, 'trade_bot_attach_outcome', { p_record: outcome('nfl-sentinel') }), { code: '23503' });
  assert.equal(await rpc(WORKER, 'trade_bot_attach_outcome', { p_record: outcome('owner-record') }), 'owner-record');
  const resolved = await rpc(OWNER, 'trade_bot_list_resolved');
  const row = resolved.find(p => p.id === 'owner-record');
  assert.equal(row.outcomes.length, 1);
  assert.equal(row.outcomes[0].prediction_id, row.id);
  assert.equal(row.outcomes[0].actor_user_id, WORKER);
  assert.equal(row.outcomes[0].meta._storage.provenance, 'worker_submitted');
  assert.deepEqual(row.outcomes[0].realized, { hit: true });
});

test('worker submissions do not inherit caller claims of verified forward evidence', async () => {
  await rpc(WORKER, 'trade_bot_append_prediction', { p_record: record('worker-record', 'trade_decision', { meta: { _terminal: { provenance: 'verified' }, _storage: { provenance: 'verified_forward' } } }) });
  const saved = await rpc(WORKER, 'trade_bot_get_prediction', { p_id: 'worker-record' });
  assert.equal(saved.actor_user_id, WORKER);
  assert.equal(saved.meta._terminal, undefined);
  assert.equal(saved.meta._storage.provenance, 'worker_submitted');
  assert.equal(saved.meta._storage.principal_kind, 'worker');
});

test('SQL task boundary rejects foreign tasks, whitespace and manipulated task names', async () => {
  const badTasks = ['nfl.game', 'wc.match', 'public.predictions', 'trading.a\n', 'trading.a b', 'trading.a\t', 'trading.', 'trading.A', "trading.a'; SELECT 1;--", '', ' trading.a', 'trading.' + 'a'.repeat(200)];
  for (const task of badTasks) {
    await assert.rejects(rpc(OWNER, 'trade_bot_append_prediction', { p_record: record('bad-task', task) }));
    await assert.rejects(rpc(OWNER, 'trade_bot_list_predictions', { p_task: task }), { code: '22023' });
  }
  for (const task of ['scanner', 'trade_decision', 'price_action_trend', 'trading.stock_intraday', 'trading.options-vertical.v1']) {
    await rpc(OWNER, 'trade_bot_append_prediction', { p_record: record(`allowed-${task}`, task) });
    assert.ok((await rpc(OWNER, 'trade_bot_list_predictions', { p_task: task })).every(p => p.task === task));
  }
});

test('RPC payload validation rejects malformed, unknown and receipt-forging inputs', async () => {
  const invalid = [
    null, [], {}, record('bad', 'scanner', { ts: '1780000000' }),
    record('bad', 'scanner', { ts: 1.5 }), record('bad', 'scanner', { horizon_s: 0 }),
    record('bad', 'scanner', { ts: 9007199254740992 }),
    record('bad', 'scanner', { ts: Math.floor(Date.now() / 1000) + 10000 }),
    record('bad', 'scanner', { confidence: '0.5' }), record('bad', 'scanner', { confidence: 1.1 }),
    record('bad', 'scanner', { features: [] }), record('bad', 'scanner', { cohort: {} }),
    record('bad', 'scanner', { meta: [] }), record('bad', 'scanner', { id: ' ' }),
    record('bad', 'scanner', { received_at: '2000-01-01' }),
    record('bad', 'scanner', { actor_user_id: OTHER }),
    record('bad', 'scanner', { schema: 'public' }),
    record('bad', 'scanner', { prediction: 'a'.repeat(65000) }),
  ];
  for (const input of invalid) await assert.rejects(rpc(OWNER, 'trade_bot_append_prediction', { p_record: input }));
  for (const input of [outcome('worker-record', { received_at: '2000-01-01' }), outcome('worker-record', { resolved_ts: 1 }), outcome('worker-record', { resolved_ts: '1780000600' })]) {
    await assert.rejects(rpc(WORKER, 'trade_bot_attach_outcome', { p_record: input }));
  }
});

test('column grants and immutable triggers defend writes outside the RPC path', async () => {
  await asRole('authenticated', OWNER, `INSERT INTO trading.predictions(id,ts,model_version,task,features,prediction,horizon_s,meta)
    VALUES('direct-owner',1780000000,'fixture','scanner','{}','{}',600,'{"_terminal":{"provenance":"verified"}}')`);
  assert.equal((await rpc(OWNER, 'trade_bot_get_prediction', { p_id: 'direct-owner' })).meta._terminal.provenance, 'unverified_import');
  for (const userId of [OWNER, WORKER]) {
    for (const query of [
      "INSERT INTO trading.predictions(id,ts,model_version,task,features,prediction,horizon_s,received_at) VALUES('forged',0,'m','scanner','{}','{}',1,'2000-01-01')",
      `INSERT INTO trading.predictions(id,ts,model_version,task,features,prediction,horizon_s,actor_user_id) VALUES('forged',0,'m','scanner','{}','{}',1,'${OTHER}')`,
      "UPDATE trading.predictions SET confidence=1 WHERE id='owner-record'",
      "DELETE FROM trading.outcomes WHERE prediction_id='owner-record'",
      'TRUNCATE trading.predictions CASCADE',
      "INSERT INTO trading.registry(version,task,config,created_ts) VALUES('bad','scanner','{}',0)",
      "UPDATE trading.registry SET status='live'",
    ]) await assert.rejects(asRole('authenticated', userId, query), { code: '42501' });
  }
  for (const query of ["UPDATE trading.predictions SET confidence=1 WHERE id='owner-record'", "DELETE FROM trading.outcomes WHERE prediction_id='owner-record'", 'TRUNCATE trading.predictions CASCADE']) {
    await assert.rejects(db.exec(query), /append-only/);
  }
  await assert.rejects(rpc(OWNER, 'trade_bot_append_prediction', { p_record: record('owner-record') }), { code: '23505' });
});

test('keyset pages are stable across equal receipt times and a fixed snapshot', async () => {
  // Controlled historical fixture: equal valid receipts exercise C ordering.
  // This bypass exists only in this disposable database, never in app code.
  const ids = ['page-A', 'page-a', 'page-\ue000', 'page-\ud800\udc00'];
  await db.exec('ALTER TABLE trading.predictions DISABLE TRIGGER trading_stamp_prediction');
  try {
    for (const id of ids) await db.query("INSERT INTO trading.predictions(id,ts,model_version,task,features,prediction,horizon_s,received_at,actor_user_id) VALUES($1,1780000000,'fixture','trading.pagination','{}','{}',600,'2026-01-01T00:00:00Z',$2)", [id, WORKER]);
  } finally { await db.exec('ALTER TABLE trading.predictions ENABLE TRIGGER trading_stamp_prediction'); }
  const snapshot = (await rpc(WORKER, 'trade_bot_status')).server_time;
  await rpc(WORKER, 'trade_bot_append_prediction', { p_record: record('after-snapshot', 'trading.pagination') });
  const first = await rpc(WORKER, 'trade_bot_list_predictions', { p_task: 'trading.pagination', p_limit: 2, p_as_of: snapshot });
  const second = await rpc(WORKER, 'trade_bot_list_predictions', { p_task: 'trading.pagination', p_limit: 2, p_as_of: snapshot, p_before_received_at: first.at(-1).received_at, p_before_id: first.at(-1).id });
  const all = [...first, ...second];
  assert.deepEqual(all.map(p => p.id), [...ids].sort((a, b) => Buffer.compare(Buffer.from(b), Buffer.from(a))));
  assert.equal(new Set(all.map(p => p.id)).size, 4);
  assert.ok(!all.some(p => p.id === 'after-snapshot'));
  assert.deepEqual(await rpc(WORKER, 'trade_bot_list_predictions', { p_task: 'trading.pagination', p_as_of: snapshot, p_before_received_at: all.at(-1).received_at, p_before_id: all.at(-1).id }), []);
});

test('resolved and unresolved scans honor both prediction and outcome receipt cutoffs', async () => {
  await rpc(WORKER, 'trade_bot_append_prediction', { p_record: record('snapshot-outcome', 'trading.snapshot') });
  const snapshot = (await rpc(WORKER, 'trade_bot_status')).server_time;
  await rpc(WORKER, 'trade_bot_attach_outcome', { p_record: outcome('snapshot-outcome') });
  assert.deepEqual(await rpc(WORKER, 'trade_bot_list_resolved', { p_task: 'trading.snapshot', p_as_of: snapshot }), []);
  assert.deepEqual((await rpc(WORKER, 'trade_bot_list_unresolved', { p_task: 'trading.snapshot', p_now_ts: 1780000600, p_as_of: snapshot })).map(p => p.id), ['snapshot-outcome']);
  assert.deepEqual((await rpc(WORKER, 'trade_bot_list_resolved', { p_task: 'trading.snapshot' })).map(p => p.id), ['snapshot-outcome']);
  assert.deepEqual(await rpc(WORKER, 'trade_bot_list_unresolved', { p_task: 'trading.snapshot', p_now_ts: 1780000600 }), []);
});

test('invalid page bounds, cursor halves and future snapshots fail explicitly', async () => {
  for (const args of [
    { p_limit: 0 }, { p_limit: 501 }, { p_limit: null },
    { p_before_id: 'x' }, { p_before_received_at: '2026-01-01' },
    { p_before_received_at: 'infinity', p_before_id: 'x' },
    { p_as_of: null }, { p_as_of: 'infinity' }, { p_as_of: '2999-01-01' },
  ]) await assert.rejects(rpc(OWNER, 'trade_bot_list_predictions', args), { code: '22023' });
  await assert.rejects(rpc(WORKER, 'trade_bot_list_unresolved', { p_task: null, p_now_ts: 1780000600 }), { code: '22023' });
  await assert.rejects(rpc(WORKER, 'trade_bot_list_unresolved', { p_task: 'scanner', p_now_ts: 9007199254740991 }), { code: '22023' });
});

test('disabling a principal immediately blocks subsequent RPC reads and writes', async () => {
  await db.query('UPDATE trading_private.principals SET enabled=false WHERE user_id=$1', [OWNER]);
  try {
    await assert.rejects(rpc(OWNER, 'trade_bot_status'), { code: '42501' });
    await assert.rejects(rpc(OWNER, 'trade_bot_list_predictions'), { code: '42501' });
    await assert.rejects(rpc(OWNER, 'trade_bot_append_prediction', { p_record: record('disabled-write') }), { code: '42501' });
  } finally { await db.query('UPDATE trading_private.principals SET enabled=true WHERE user_id=$1', [OWNER]); }
});

test('reapplication refuses existing trading objects without changing NFL or trading data', async () => {
  const before = await db.query('SELECT count(*)::integer AS n FROM trading.predictions');
  await assert.rejects(db.exec(migration), /Shared trading objects already exist/);
  await db.exec('ROLLBACK');
  assert.equal((await db.query('SELECT count(*)::integer AS n FROM trading.predictions')).rows[0].n, before.rows[0].n);
  assert.deepEqual(await nflSnapshot(), nflBefore);
});

test('unsafe role attributes or inherited grants abort atomically', async () => {
  const unsafe = new PGlite();
  try {
    await unsafe.exec(`CREATE ROLE anon; CREATE ROLE authenticated BYPASSRLS; CREATE ROLE service_role BYPASSRLS;
      CREATE SCHEMA auth; CREATE FUNCTION auth.uid() RETURNS uuid LANGUAGE sql STABLE AS $$ SELECT null::uuid $$;
      GRANT USAGE ON SCHEMA auth TO authenticated;`);
    await assert.rejects(unsafe.exec(migration), /role inheritance or RLS bypass/);
    await unsafe.exec('ROLLBACK');
    assert.equal((await unsafe.query("SELECT to_regnamespace('trading') AS value")).rows[0].value, null);
    await unsafe.exec('ALTER ROLE authenticated NOBYPASSRLS; GRANT authenticated TO service_role;');
    await assert.rejects(unsafe.exec(migration), /role inheritance or RLS bypass/);
    await unsafe.exec('ROLLBACK');
    await unsafe.exec('REVOKE authenticated FROM service_role; CREATE ROLE inherited_reader; GRANT inherited_reader TO service_role; ALTER DEFAULT PRIVILEGES GRANT SELECT ON TABLES TO inherited_reader;');
    await assert.rejects(unsafe.exec(migration), /Unexpected effective grants/);
    await unsafe.exec('ROLLBACK');
    assert.equal((await unsafe.query("SELECT to_regnamespace('trading') AS value")).rows[0].value, null);
  } finally { await unsafe.close(); }
});
