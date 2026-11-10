-- Initial hosted verification only. Run the ENTIRE file as one administrator
-- SQL request after the additive migration, before enrolling actual users.
-- Refuses nonempty trading tables. Creates no Auth users or schema objects;
-- all fixtures are rolled back. Output contains summary booleans only.
-- On any error, issue ROLLBACK before reusing the same database connection.
BEGIN;
SET LOCAL statement_timeout = '30s';
SET LOCAL lock_timeout = '5s';

DO $verification$
DECLARE
  c record;
  relation record;
  check_case record;
  role_name text;
  statement_text text;
  denied boolean;
  principal_count bigint;
  nfl_relation_count integer := 0;
  ts bigint := floor(extract(epoch FROM statement_timestamp()))::bigint - 3600;
  payload jsonb;
  saved jsonb;
  status jsonb;
  result_id text;
  cutoff timestamptz;
  predictions jsonb;
  resolved jsonb;
  unresolved jsonb;
BEGIN
  IF EXISTS (SELECT 1 FROM trading_private.principals)
     OR EXISTS (SELECT 1 FROM trading.predictions)
     OR EXISTS (SELECT 1 FROM trading.outcomes)
     OR EXISTS (SELECT 1 FROM trading.registry)
     OR EXISTS (SELECT 1 FROM trading.registry_scores) THEN
    RAISE EXCEPTION 'Initial verification requires empty trading tables';
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM pg_catalog.pg_constraint
    WHERE conrelid = 'trading.outcomes'::regclass
      AND contype = 'f' AND confrelid = 'trading.predictions'::regclass
  ) THEN
    RAISE EXCEPTION 'Trading outcome foreign key is missing';
  END IF;
  SELECT gen_random_uuid() AS owner_id, gen_random_uuid() AS worker_id,
    gen_random_uuid() AS unlisted_id,
    '__rollback_owner_' || gen_random_uuid()::text AS owner_record,
    '__rollback_worker_' || gen_random_uuid()::text AS worker_record,
    '__rollback_missing_' || gen_random_uuid()::text AS missing_record
  INTO STRICT c;
  INSERT INTO trading_private.principals (user_id, principal_kind, enabled)
  VALUES (c.owner_id, 'owner', true), (c.worker_id, 'worker', true);

  -- Permission checks use the actual hosted roles, not simulated RLS bypass.
  FOREACH role_name IN ARRAY ARRAY['anon', 'service_role'] LOOP
    EXECUTE format('SET LOCAL ROLE %I', role_name);
    PERFORM set_config('request.jwt.claim.sub', c.owner_id::text, true);
    PERFORM set_config('request.jwt.claims', jsonb_build_object('sub',c.owner_id,'role',role_name)::text, true);
    FOREACH statement_text IN ARRAY ARRAY[
      'SELECT public.trade_bot_status()',
      'SELECT public.trade_bot_list_predictions()',
      'SELECT * FROM trading.predictions',
      'SELECT * FROM trading_private.principals'
    ] LOOP
      denied := false;
      BEGIN
        EXECUTE statement_text;
      EXCEPTION WHEN insufficient_privilege THEN denied := true;
      END;
      IF NOT denied THEN RAISE EXCEPTION 'Excluded role unexpectedly gained trading access'; END IF;
    END LOOP;
    RESET ROLE;
  END LOOP;

  SET LOCAL ROLE authenticated;
  PERFORM set_config('request.jwt.claim.sub', c.unlisted_id::text, true);
  PERFORM set_config('request.jwt.claims', jsonb_build_object('sub',c.unlisted_id,'role','authenticated')::text, true);
  FOREACH statement_text IN ARRAY ARRAY[
    'SELECT public.trade_bot_status()',
    'SELECT public.trade_bot_list_predictions()',
    'SELECT public.trade_bot_list_resolved()',
    format('SELECT public.trade_bot_get_prediction(%L)', c.owner_record),
    format('SELECT public.trade_bot_list_unresolved(''scanner'', %s)', ts),
    'SELECT public.trade_bot_append_prediction(''{}''::jsonb)',
    'SELECT public.trade_bot_attach_outcome(''{}''::jsonb)'
  ] LOOP
    denied := false;
    BEGIN
      EXECUTE statement_text;
    EXCEPTION WHEN insufficient_privilege THEN denied := true;
    END;
    IF NOT denied THEN RAISE EXCEPTION 'Unlisted identity unexpectedly gained RPC access'; END IF;
  END LOOP;

  -- Owner import provenance and principal scope are enforced inside SQL.
  PERFORM set_config('request.jwt.claim.sub', c.owner_id::text, true);
  PERFORM set_config('request.jwt.claims', jsonb_build_object('sub',c.owner_id,'role','authenticated')::text, true);
  status := public.trade_bot_status();
  IF status->>'schema_version' IS DISTINCT FROM 'shared-trading-v1'
     OR status->>'principal_kind' IS DISTINCT FROM 'owner' THEN
    RAISE EXCEPTION 'Owner status contract failed';
  END IF;
  SELECT count(*) INTO principal_count FROM trading_private.principals;
  IF principal_count <> 1 OR NOT EXISTS (SELECT 1 FROM trading_private.principals WHERE user_id=c.owner_id) THEN
    RAISE EXCEPTION 'Principal self-read policy failed';
  END IF;
  payload := jsonb_build_object('id',c.owner_record,'ts',ts,'model_version','rollback-fixture-v1',
    'task','trading.verification','features','{}'::jsonb,'prediction',jsonb_build_object('fixture',true),
    'horizon_s',600,'confidence',0.5,'meta',jsonb_build_object(
      '_terminal',jsonb_build_object('owner_user_id',c.unlisted_id,'provenance','verified'),
      '_storage',jsonb_build_object('provenance','verified')));
  result_id := public.trade_bot_append_prediction(payload);
  saved := public.trade_bot_get_prediction(c.owner_record);
  IF result_id IS DISTINCT FROM c.owner_record OR saved->>'actor_user_id' IS DISTINCT FROM c.owner_id::text
     OR saved#>>'{meta,_terminal,owner_user_id}' IS DISTINCT FROM c.owner_id::text
     OR saved#>>'{meta,_terminal,provenance}' IS DISTINCT FROM 'unverified_import'
     OR saved#>>'{meta,_storage,provenance}' IS DISTINCT FROM 'unverified_import'
     OR (saved->>'received_at')::timestamptz < statement_timestamp() THEN
    RAISE EXCEPTION 'Owner import stamping or readback failed';
  END IF;

  FOREACH statement_text IN ARRAY ARRAY[
    format('INSERT INTO trading_private.principals(user_id,principal_kind,enabled) VALUES(%L,''worker'',true)',c.unlisted_id),
    'UPDATE trading_private.principals SET principal_kind=''worker''',
    'DELETE FROM trading_private.principals',
    format('SELECT public.trade_bot_attach_outcome(%L::jsonb)',jsonb_build_object('prediction_id',c.owner_record,'resolved_ts',ts+600,'realized','{}'::jsonb)),
    format('INSERT INTO trading.outcomes(prediction_id,resolved_ts,realized) VALUES(%L,%s,''{}'')',c.owner_record,ts+600),
    format('INSERT INTO trading.predictions(id,ts,model_version,task,features,prediction,horizon_s,received_at) VALUES(%L,%s,''fixture'',''scanner'',''{}'',''{}'',1,''2000-01-01'')',c.missing_record,ts),
    format('INSERT INTO trading.predictions(id,ts,model_version,task,features,prediction,horizon_s,actor_user_id) VALUES(%L,%s,''fixture'',''scanner'',''{}'',''{}'',1,%L)',c.missing_record,ts,c.worker_id),
    'SELECT * FROM public.predictions'
  ] LOOP
    denied := false;
    BEGIN
      EXECUTE statement_text;
    EXCEPTION WHEN insufficient_privilege THEN denied := true;
    END;
    IF NOT denied THEN RAISE EXCEPTION 'Owner escalation, receipt forgery, or NFL read unexpectedly succeeded'; END IF;
  END LOOP;

  -- Discover the existing NFL catalog; neither its names nor records are output.
  FOR relation IN
    SELECT p.oid FROM pg_catalog.pg_class p
    JOIN pg_catalog.pg_namespace n ON n.oid=p.relnamespace
    WHERE p.relkind IN ('r','p','v','m','f')
      AND ((n.nspname='public' AND p.relname IN ('predictions','outcomes','registry','registry_scores'))
        OR n.nspname='selflearn_private')
  LOOP
    nfl_relation_count := nfl_relation_count + 1;
    IF has_table_privilege(current_user,relation.oid,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
       OR has_any_column_privilege(current_user,relation.oid,'SELECT,INSERT,UPDATE,REFERENCES') THEN
      RAISE EXCEPTION 'Authenticated role has unexpected NFL table or column grants';
    END IF;
  END LOOP;
  IF nfl_relation_count < 4 THEN RAISE EXCEPTION 'Expected existing NFL catalog was not found'; END IF;

  -- Worker can resolve trading evidence, while its own submissions stay unverified.
  PERFORM set_config('request.jwt.claim.sub', c.worker_id::text, true);
  PERFORM set_config('request.jwt.claims', jsonb_build_object('sub',c.worker_id,'role','authenticated')::text, true);
  status := public.trade_bot_status();
  IF status->>'principal_kind' IS DISTINCT FROM 'worker' THEN RAISE EXCEPTION 'Worker status contract failed'; END IF;
  payload := jsonb_set(payload,'{id}',to_jsonb(c.worker_record));
  result_id := public.trade_bot_append_prediction(payload);
  saved := public.trade_bot_get_prediction(c.worker_record);
  IF result_id IS DISTINCT FROM c.worker_record OR saved->>'actor_user_id' IS DISTINCT FROM c.worker_id::text
     OR saved->'meta' ? '_terminal' OR saved#>>'{meta,_storage,provenance}' IS DISTINCT FROM 'worker_submitted' THEN
    RAISE EXCEPTION 'Worker provenance or readback failed';
  END IF;
  result_id := public.trade_bot_attach_outcome(jsonb_build_object('prediction_id',c.owner_record,
    'resolved_ts',ts+600,'realized',jsonb_build_object('fixture',true)));
  IF result_id IS DISTINCT FROM c.owner_record OR NOT EXISTS (
    SELECT 1 FROM trading.outcomes WHERE prediction_id=c.owner_record AND actor_user_id=c.worker_id
      AND meta#>>'{_storage,provenance}'='worker_submitted'
  ) THEN RAISE EXCEPTION 'Worker resolution or readback failed'; END IF;

  FOR check_case IN
    SELECT * FROM (VALUES
      (format('SELECT public.trade_bot_attach_outcome(%L::jsonb)',jsonb_build_object('prediction_id',c.missing_record,'resolved_ts',ts+600,'realized','{}'::jsonb)), '23503'),
      ('SELECT public.trade_bot_list_predictions(p_task=>''nfl.game'')', '22023'),
      (format('SELECT public.trade_bot_list_predictions(p_task=>%L)',E'trading.a\n'), '22023'),
      (format('SELECT public.trade_bot_append_prediction(%L::jsonb)',payload||jsonb_build_object('received_at','2000-01-01T00:00:00Z')), '22023'),
      (format('UPDATE trading.predictions SET confidence=1 WHERE id=%L',c.owner_record), '42501'),
      (format('DELETE FROM trading.outcomes WHERE prediction_id=%L',c.owner_record), '42501'),
      ('TRUNCATE trading.predictions CASCADE', '42501'),
      ('INSERT INTO trading.registry(version,task,config,created_ts) VALUES(''fixture'',''scanner'',''{}'',0)', '42501'),
      ('UPDATE trading.registry SET status=''live''', '42501')
    ) AS cases(statement_text, expected_state)
  LOOP
    denied := false;
    BEGIN
      EXECUTE check_case.statement_text;
    EXCEPTION WHEN OTHERS THEN
      IF SQLSTATE <> check_case.expected_state THEN RAISE; END IF;
      denied := true;
    END;
    IF NOT denied THEN RAISE EXCEPTION 'Worker scope, immutable evidence, or validation check unexpectedly succeeded'; END IF;
  END LOOP;
  RESET ROLE;

  UPDATE trading_private.principals SET enabled=false WHERE user_id=c.owner_id;
  SET LOCAL ROLE authenticated;
  PERFORM set_config('request.jwt.claim.sub', c.owner_id::text, true);
  PERFORM set_config('request.jwt.claims', jsonb_build_object('sub',c.owner_id,'role','authenticated')::text, true);
  denied := false;
  BEGIN
    PERFORM public.trade_bot_status();
  EXCEPTION WHEN insufficient_privilege THEN denied := true;
  END;
  IF NOT denied THEN RAISE EXCEPTION 'Disabled principal retained access'; END IF;
  RESET ROLE;

  -- statement_timestamp() remains at this command-message start. The fixture
  -- receipts are later, so the cutoff must exclude them while get/direct reads
  -- can see them. Positive paging is tested with separate disposable requests.
  -- Do not alter hosted triggers to backdate fixtures.
  SET LOCAL ROLE authenticated;
  PERFORM set_config('request.jwt.claim.sub',c.worker_id::text,true);
  PERFORM set_config('request.jwt.claims',jsonb_build_object('sub',c.worker_id,'role','authenticated')::text,true);
  cutoff := (public.trade_bot_status()->>'server_time')::timestamptz;
  predictions := public.trade_bot_list_predictions(p_task=>'trading.verification',p_as_of=>cutoff);
  resolved := public.trade_bot_list_resolved(p_task=>'trading.verification',p_as_of=>cutoff);
  unresolved := public.trade_bot_list_unresolved(p_task=>'trading.verification',
    p_now_ts=>floor(extract(epoch FROM cutoff))::bigint,p_as_of=>cutoff);
  IF predictions IS DISTINCT FROM '[]'::jsonb OR resolved IS DISTINCT FROM '[]'::jsonb
     OR unresolved IS DISTINCT FROM '[]'::jsonb THEN
    RAISE EXCEPTION 'Same-request receipt cutoff failed';
  END IF;
  RESET ROLE;
  PERFORM set_config('trade_bot.verification_summary', jsonb_build_object(
    'owner_import_and_provenance',true,'worker_append_and_resolution',true,
    'principal_self_read_only',true,'unlisted_denied',true,'anon_denied',true,
    'service_role_denied',true,'self_enrollment_denied',true,'owner_resolution_denied',true,
    'receipt_and_actor_forgery_denied',true,'immutable_evidence',true,'registry_promotion_denied',true,
    'task_boundary',true,'trading_foreign_key',true,'nfl_grants_absent',true,'nfl_read_denied',true,
    'disabled_principal_denied',true,'prediction_receipt_cutoff',true,
    'resolved_receipt_cutoff',true,'unresolved_receipt_cutoff',true)::text, true);
END;
$verification$;

SELECT current_setting('trade_bot.verification_summary')::jsonb AS shared_trading_verification;
ROLLBACK;

-- This result is read AFTER the rollback, proving no fixture principal or
-- trading record survived. No real user enrollment has occurred.
SELECT jsonb_build_object(
  'principals_empty_after_rollback',NOT EXISTS(SELECT 1 FROM trading_private.principals),
  'predictions_empty_after_rollback',NOT EXISTS(SELECT 1 FROM trading.predictions),
  'outcomes_empty_after_rollback',NOT EXISTS(SELECT 1 FROM trading.outcomes),
  'registry_empty_after_rollback',NOT EXISTS(SELECT 1 FROM trading.registry),
  'registry_scores_empty_after_rollback',NOT EXISTS(SELECT 1 FROM trading.registry_scores)
) AS rollback_cleanup;
