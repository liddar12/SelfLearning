-- Additive trading storage for an existing shared Supabase project.
-- No existing NFL table, function, policy, grant, Auth setting or data is changed.
-- Enrollment is deliberately separate: trading_private.principals starts empty.
BEGIN;

DO $guard$
BEGIN
  IF to_regnamespace('trading') IS NOT NULL
     OR to_regnamespace('trading_private') IS NOT NULL
     OR EXISTS (
       SELECT 1 FROM pg_catalog.pg_proc p
       JOIN pg_catalog.pg_namespace n ON n.oid = p.pronamespace
       WHERE n.nspname = 'public' AND p.proname LIKE 'trade_bot_%'
     ) THEN
    RAISE EXCEPTION 'Shared trading objects already exist; inspect before applying this migration';
  END IF;
  IF to_regprocedure('auth.uid()') IS NULL
     OR NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'authenticated')
     OR NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'anon')
     OR NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'service_role') THEN
    RAISE EXCEPTION 'Supabase Auth and application roles are required';
  END IF;
  IF EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname='authenticated' AND (rolsuper OR rolbypassrls))
     OR pg_has_role('service_role','authenticated','USAGE')
     OR pg_has_role('anon','authenticated','USAGE') THEN
    RAISE EXCEPTION 'Application role inheritance or RLS bypass is incompatible with trading isolation';
  END IF;
END
$guard$;

CREATE SCHEMA trading;
CREATE SCHEMA trading_private;
REVOKE ALL ON SCHEMA trading, trading_private FROM PUBLIC, anon, authenticated, service_role;
GRANT USAGE ON SCHEMA trading, trading_private TO authenticated;

CREATE TABLE trading_private.principals (
  user_id uuid PRIMARY KEY,
  principal_kind text NOT NULL CHECK (principal_kind IN ('owner', 'worker')),
  enabled boolean NOT NULL DEFAULT false,
  enrolled_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
ALTER TABLE trading_private.principals ENABLE ROW LEVEL SECURITY;
CREATE POLICY principal_self_read ON trading_private.principals
  FOR SELECT TO authenticated USING (user_id = (SELECT auth.uid()));

CREATE FUNCTION trading_private.principal_kind()
RETURNS text LANGUAGE sql STABLE SECURITY INVOKER SET search_path = pg_catalog AS $fn$
  SELECT p.principal_kind FROM trading_private.principals p
  WHERE p.user_id = (SELECT auth.uid()) AND p.enabled
$fn$;

CREATE FUNCTION trading_private.require_principal(p_worker_only boolean DEFAULT false)
RETURNS text LANGUAGE plpgsql STABLE SECURITY INVOKER SET search_path = pg_catalog AS $fn$
DECLARE v_kind text;
BEGIN
  v_kind := trading_private.principal_kind();
  IF v_kind IS NULL OR (p_worker_only AND v_kind <> 'worker') THEN
    RAISE EXCEPTION 'Trading access is not authorized' USING ERRCODE = '42501';
  END IF;
  RETURN v_kind;
END
$fn$;

CREATE FUNCTION trading_private.valid_task(p_task text)
RETURNS boolean LANGUAGE sql IMMUTABLE SECURITY INVOKER SET search_path = pg_catalog AS $fn$
  SELECT p_task IS NOT NULL AND length(p_task) BETWEEN 1 AND 200 AND p_task !~ '[[:space:]]' AND (
    p_task IN ('scanner', 'trade_decision', 'price_action_trend')
    OR p_task ~ '^trading\.[a-z][a-z0-9_.-]*$'
  )
$fn$;

CREATE TABLE trading.predictions (
  id text COLLATE "C" PRIMARY KEY CHECK (length(id) BETWEEN 1 AND 200 AND id = btrim(id)),
  ts bigint NOT NULL CHECK (ts >= 0 AND ts <= 9007199254740991),
  model_version text NOT NULL CHECK (length(model_version) BETWEEN 1 AND 200 AND model_version = btrim(model_version)),
  task text NOT NULL CHECK (trading_private.valid_task(task)),
  features jsonb NOT NULL CHECK (jsonb_typeof(features) = 'object'),
  prediction jsonb NOT NULL,
  confidence double precision CHECK (confidence >= 0 AND confidence <= 1),
  horizon_s bigint NOT NULL CHECK (horizon_s BETWEEN 1 AND 315576000),
  cohort text CHECK (length(cohort) <= 200),
  meta jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(meta) = 'object'),
  received_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  actor_user_id uuid NOT NULL DEFAULT auth.uid()
);
CREATE INDEX trading_predictions_receipt_idx ON trading.predictions (received_at DESC, id COLLATE "C" DESC);
CREATE INDEX trading_predictions_task_receipt_idx ON trading.predictions (task, received_at DESC, id COLLATE "C" DESC);

CREATE TABLE trading.outcomes (
  prediction_id text COLLATE "C" PRIMARY KEY REFERENCES trading.predictions(id),
  resolved_ts bigint NOT NULL CHECK (resolved_ts >= 0 AND resolved_ts <= 9007199254740991),
  realized jsonb NOT NULL,
  meta jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(meta) = 'object'),
  received_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  actor_user_id uuid NOT NULL DEFAULT auth.uid()
);

-- Read-only registry shells preserve the common evidence contract. Promotion
-- has no app grant or write RPC in this migration.
CREATE TABLE trading.registry (
  version text PRIMARY KEY CHECK (length(version) BETWEEN 1 AND 200),
  task text NOT NULL CHECK (trading_private.valid_task(task)),
  config jsonb NOT NULL CHECK (jsonb_typeof(config) = 'object'),
  created_ts bigint NOT NULL CHECK (created_ts >= 0),
  status text NOT NULL DEFAULT 'candidate' CHECK (status IN ('candidate', 'live', 'retired')),
  autonomy integer NOT NULL DEFAULT 0 CHECK (autonomy BETWEEN 0 AND 4)
);
CREATE TABLE trading.registry_scores (
  version text NOT NULL REFERENCES trading.registry(version),
  recorded_ts bigint NOT NULL CHECK (recorded_ts >= 0),
  score jsonb NOT NULL CHECK (jsonb_typeof(score) = 'object'),
  PRIMARY KEY (version, recorded_ts)
);

ALTER TABLE trading.predictions ENABLE ROW LEVEL SECURITY;
ALTER TABLE trading.outcomes ENABLE ROW LEVEL SECURITY;
ALTER TABLE trading.registry ENABLE ROW LEVEL SECURITY;
ALTER TABLE trading.registry_scores ENABLE ROW LEVEL SECURITY;

CREATE POLICY trading_prediction_read ON trading.predictions FOR SELECT TO authenticated
  USING ((SELECT trading_private.principal_kind()) IN ('owner', 'worker'));
CREATE POLICY trading_prediction_append ON trading.predictions FOR INSERT TO authenticated
  WITH CHECK ((SELECT trading_private.principal_kind()) IN ('owner', 'worker') AND actor_user_id = (SELECT auth.uid()));
CREATE POLICY trading_outcome_read ON trading.outcomes FOR SELECT TO authenticated
  USING ((SELECT trading_private.principal_kind()) IN ('owner', 'worker'));
CREATE POLICY trading_outcome_append ON trading.outcomes FOR INSERT TO authenticated
  WITH CHECK ((SELECT trading_private.principal_kind()) = 'worker' AND actor_user_id = (SELECT auth.uid()));
CREATE POLICY trading_registry_read ON trading.registry FOR SELECT TO authenticated
  USING ((SELECT trading_private.principal_kind()) IN ('owner', 'worker'));
CREATE POLICY trading_registry_score_read ON trading.registry_scores FOR SELECT TO authenticated
  USING ((SELECT trading_private.principal_kind()) IN ('owner', 'worker'));

CREATE FUNCTION trading_private.stamp_prediction()
RETURNS trigger LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog AS $fn$
DECLARE v_kind text;
BEGIN
  v_kind := trading_private.require_principal();
  IF NEW.ts > floor(extract(epoch FROM clock_timestamp()))::bigint + 300 THEN
    RAISE EXCEPTION 'Prediction timestamp is in the future' USING ERRCODE = '22023';
  END IF;
  IF octet_length(NEW.features::text) + octet_length(NEW.prediction::text) + octet_length(NEW.meta::text) > 64000 THEN
    RAISE EXCEPTION 'Prediction payload is too large' USING ERRCODE = '22023';
  END IF;
  NEW.actor_user_id := auth.uid();
  NEW.received_at := clock_timestamp();
  NEW.meta := (NEW.meta - '_terminal' - '_storage') || jsonb_build_object(
    '_storage', jsonb_build_object('actor_user_id', NEW.actor_user_id, 'principal_kind', v_kind,
      'provenance', CASE WHEN v_kind = 'owner' THEN 'unverified_import' ELSE 'worker_submitted' END));
  IF v_kind = 'owner' THEN
    NEW.meta := NEW.meta || jsonb_build_object('_terminal',
      jsonb_build_object('owner_user_id', NEW.actor_user_id, 'provenance', 'unverified_import'));
  END IF;
  RETURN NEW;
END
$fn$;

CREATE FUNCTION trading_private.stamp_outcome()
RETURNS trigger LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog AS $fn$
DECLARE v_prediction_ts bigint;
BEGIN
  PERFORM trading_private.require_principal(true);
  SELECT p.ts INTO v_prediction_ts FROM trading.predictions p WHERE p.id = NEW.prediction_id;
  IF v_prediction_ts IS NULL THEN
    RAISE EXCEPTION 'Trading prediction does not exist' USING ERRCODE = '23503';
  END IF;
  IF NEW.resolved_ts < v_prediction_ts OR NEW.resolved_ts > floor(extract(epoch FROM clock_timestamp()))::bigint + 300 THEN
    RAISE EXCEPTION 'Outcome timestamp is invalid' USING ERRCODE = '22023';
  END IF;
  IF octet_length(NEW.realized::text) + octet_length(NEW.meta::text) > 64000 THEN
    RAISE EXCEPTION 'Outcome payload is too large' USING ERRCODE = '22023';
  END IF;
  NEW.actor_user_id := auth.uid();
  NEW.received_at := clock_timestamp();
  NEW.meta := (NEW.meta - '_storage') || jsonb_build_object('_storage',
    jsonb_build_object('actor_user_id', NEW.actor_user_id, 'principal_kind', 'worker', 'provenance', 'worker_submitted'));
  RETURN NEW;
END
$fn$;

CREATE FUNCTION trading_private.reject_mutation()
RETURNS trigger LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog AS $fn$
BEGIN
  RAISE EXCEPTION 'Trading evidence is append-only' USING ERRCODE = '55000';
END
$fn$;

CREATE TRIGGER trading_stamp_prediction BEFORE INSERT ON trading.predictions
  FOR EACH ROW EXECUTE FUNCTION trading_private.stamp_prediction();
CREATE TRIGGER trading_stamp_outcome BEFORE INSERT ON trading.outcomes
  FOR EACH ROW EXECUTE FUNCTION trading_private.stamp_outcome();
CREATE TRIGGER trading_predictions_immutable BEFORE UPDATE OR DELETE ON trading.predictions
  FOR EACH ROW EXECUTE FUNCTION trading_private.reject_mutation();
CREATE TRIGGER trading_outcomes_immutable BEFORE UPDATE OR DELETE ON trading.outcomes
  FOR EACH ROW EXECUTE FUNCTION trading_private.reject_mutation();
CREATE TRIGGER trading_predictions_no_truncate BEFORE TRUNCATE ON trading.predictions
  FOR EACH STATEMENT EXECUTE FUNCTION trading_private.reject_mutation();
CREATE TRIGGER trading_outcomes_no_truncate BEFORE TRUNCATE ON trading.outcomes
  FOR EACH STATEMENT EXECUTE FUNCTION trading_private.reject_mutation();

CREATE FUNCTION trading_private.validate_read(
  p_task text, p_limit integer, p_before_received_at timestamptz, p_before_id text, p_as_of timestamptz
)
RETURNS void LANGUAGE plpgsql STABLE SECURITY INVOKER SET search_path = pg_catalog AS $fn$
BEGIN
  PERFORM trading_private.require_principal();
  IF p_task IS NOT NULL AND NOT trading_private.valid_task(p_task) THEN
    RAISE EXCEPTION 'Invalid trading task' USING ERRCODE = '22023';
  END IF;
  IF p_limit IS NULL OR p_limit < 1 OR p_limit > 500 THEN
    RAISE EXCEPTION 'Trading page limit must be from 1 to 500' USING ERRCODE = '22023';
  END IF;
  IF (p_before_received_at IS NULL) <> (p_before_id IS NULL)
     OR (p_before_received_at IS NOT NULL AND NOT isfinite(p_before_received_at))
     OR (p_before_id IS NOT NULL AND (length(p_before_id) NOT BETWEEN 1 AND 200 OR p_before_id <> btrim(p_before_id))) THEN
    RAISE EXCEPTION 'Invalid trading page cursor' USING ERRCODE = '22023';
  END IF;
  IF p_as_of IS NULL OR NOT isfinite(p_as_of) OR p_as_of > statement_timestamp() THEN
    RAISE EXCEPTION 'Invalid trading snapshot time' USING ERRCODE = '22023';
  END IF;
END
$fn$;

CREATE FUNCTION public.trade_bot_status()
RETURNS jsonb LANGUAGE plpgsql STABLE SECURITY INVOKER SET search_path = pg_catalog AS $fn$
DECLARE v_kind text;
BEGIN
  v_kind := trading_private.require_principal();
  RETURN jsonb_build_object('schema_version', 'shared-trading-v1', 'server_time', statement_timestamp(), 'principal_kind', v_kind);
END
$fn$;

CREATE FUNCTION public.trade_bot_append_prediction(p_record jsonb)
RETURNS text LANGUAGE plpgsql VOLATILE SECURITY INVOKER SET search_path = pg_catalog AS $fn$
DECLARE v_id text;
BEGIN
  PERFORM trading_private.require_principal();
  IF jsonb_typeof(p_record) IS DISTINCT FROM 'object' OR octet_length(p_record::text) > 64000 THEN
    RAISE EXCEPTION 'Invalid prediction record' USING ERRCODE = '22023';
  END IF;
  IF NOT (p_record ?& ARRAY['id','ts','model_version','task','features','prediction','horizon_s'])
     OR EXISTS (SELECT 1 FROM jsonb_object_keys(p_record) AS x(k)
       WHERE k <> ALL(ARRAY['id','ts','model_version','task','features','prediction','confidence','horizon_s','cohort','meta'])) THEN
    RAISE EXCEPTION 'Invalid prediction fields' USING ERRCODE = '22023';
  END IF;
  IF jsonb_typeof(p_record->'id') IS DISTINCT FROM 'string'
     OR length(p_record->>'id') NOT BETWEEN 1 AND 200 OR p_record->>'id' <> btrim(p_record->>'id')
     OR jsonb_typeof(p_record->'model_version') IS DISTINCT FROM 'string'
     OR length(p_record->>'model_version') NOT BETWEEN 1 AND 200 OR p_record->>'model_version' <> btrim(p_record->>'model_version')
     OR jsonb_typeof(p_record->'task') IS DISTINCT FROM 'string' OR NOT trading_private.valid_task(p_record->>'task')
     OR jsonb_typeof(p_record->'features') IS DISTINCT FROM 'object' THEN
    RAISE EXCEPTION 'Invalid prediction identity or task' USING ERRCODE = '22023';
  END IF;
  IF jsonb_typeof(p_record->'ts') IS DISTINCT FROM 'number' OR (p_record->>'ts') !~ '^[0-9]+$'
     OR jsonb_typeof(p_record->'horizon_s') IS DISTINCT FROM 'number' OR (p_record->>'horizon_s') !~ '^[0-9]+$' THEN
    RAISE EXCEPTION 'Invalid prediction time' USING ERRCODE = '22023';
  END IF;
  IF (p_record->>'ts')::numeric > 9007199254740991
     OR (p_record->>'horizon_s')::numeric NOT BETWEEN 1 AND 315576000 THEN
    RAISE EXCEPTION 'Invalid prediction time range' USING ERRCODE = '22023';
  END IF;
  IF p_record->'confidence' IS NOT NULL AND p_record->'confidence' <> 'null'::jsonb THEN
    IF jsonb_typeof(p_record->'confidence') IS DISTINCT FROM 'number' THEN
      RAISE EXCEPTION 'Invalid prediction confidence' USING ERRCODE = '22023';
    END IF;
    IF (p_record->>'confidence')::numeric < 0 OR (p_record->>'confidence')::numeric > 1 THEN
      RAISE EXCEPTION 'Invalid prediction confidence' USING ERRCODE = '22023';
    END IF;
  END IF;
  IF p_record->'cohort' IS NOT NULL AND p_record->'cohort' <> 'null'::jsonb
     AND (jsonb_typeof(p_record->'cohort') IS DISTINCT FROM 'string' OR length(p_record->>'cohort') > 200) THEN
    RAISE EXCEPTION 'Invalid prediction cohort' USING ERRCODE = '22023';
  END IF;
  IF p_record->'meta' IS NOT NULL AND p_record->'meta' <> 'null'::jsonb AND jsonb_typeof(p_record->'meta') <> 'object' THEN
    RAISE EXCEPTION 'Invalid prediction metadata' USING ERRCODE = '22023';
  END IF;
  INSERT INTO trading.predictions(id,ts,model_version,task,features,prediction,confidence,horizon_s,cohort,meta)
  VALUES (p_record->>'id',(p_record->>'ts')::bigint,p_record->>'model_version',p_record->>'task',
    p_record->'features',p_record->'prediction',(p_record->>'confidence')::double precision,
    (p_record->>'horizon_s')::bigint,p_record->>'cohort',coalesce(nullif(p_record->'meta','null'::jsonb),'{}'::jsonb))
  RETURNING id INTO v_id;
  RETURN v_id;
END
$fn$;

CREATE FUNCTION public.trade_bot_attach_outcome(p_record jsonb)
RETURNS text LANGUAGE plpgsql VOLATILE SECURITY INVOKER SET search_path = pg_catalog AS $fn$
DECLARE v_id text;
BEGIN
  PERFORM trading_private.require_principal(true);
  IF jsonb_typeof(p_record) IS DISTINCT FROM 'object' OR octet_length(p_record::text) > 64000 THEN
    RAISE EXCEPTION 'Invalid outcome record' USING ERRCODE = '22023';
  END IF;
  IF NOT (p_record ?& ARRAY['prediction_id','resolved_ts','realized'])
     OR EXISTS (SELECT 1 FROM jsonb_object_keys(p_record) AS x(k)
       WHERE k <> ALL(ARRAY['prediction_id','resolved_ts','realized','meta'])) THEN
    RAISE EXCEPTION 'Invalid outcome fields' USING ERRCODE = '22023';
  END IF;
  IF jsonb_typeof(p_record->'prediction_id') IS DISTINCT FROM 'string'
     OR length(p_record->>'prediction_id') NOT BETWEEN 1 AND 200
     OR p_record->>'prediction_id' <> btrim(p_record->>'prediction_id')
     OR jsonb_typeof(p_record->'resolved_ts') IS DISTINCT FROM 'number' OR (p_record->>'resolved_ts') !~ '^[0-9]+$' THEN
    RAISE EXCEPTION 'Invalid outcome identity or time' USING ERRCODE = '22023';
  END IF;
  IF (p_record->>'resolved_ts')::numeric > 9007199254740991 THEN
    RAISE EXCEPTION 'Invalid outcome time range' USING ERRCODE = '22023';
  END IF;
  IF p_record->'meta' IS NOT NULL AND p_record->'meta' <> 'null'::jsonb AND jsonb_typeof(p_record->'meta') <> 'object' THEN
    RAISE EXCEPTION 'Invalid outcome metadata' USING ERRCODE = '22023';
  END IF;
  INSERT INTO trading.outcomes(prediction_id,resolved_ts,realized,meta)
  VALUES (p_record->>'prediction_id',(p_record->>'resolved_ts')::bigint,p_record->'realized',
    coalesce(nullif(p_record->'meta','null'::jsonb),'{}'::jsonb))
  RETURNING prediction_id INTO v_id;
  RETURN v_id;
END
$fn$;

CREATE FUNCTION public.trade_bot_list_predictions(
  p_task text DEFAULT NULL, p_limit integer DEFAULT 100,
  p_before_received_at timestamptz DEFAULT NULL, p_before_id text DEFAULT NULL,
  p_as_of timestamptz DEFAULT statement_timestamp()
)
RETURNS jsonb LANGUAGE plpgsql STABLE SECURITY INVOKER SET search_path = pg_catalog AS $fn$
DECLARE v_rows jsonb;
BEGIN
  PERFORM trading_private.validate_read(p_task,p_limit,p_before_received_at,p_before_id,p_as_of);
  SELECT coalesce(jsonb_agg(to_jsonb(p) ORDER BY p.received_at DESC,p.id COLLATE "C" DESC),'[]'::jsonb)
  INTO v_rows FROM (
    SELECT * FROM trading.predictions p
    WHERE (p_task IS NULL OR p.task=p_task) AND p.received_at <= p_as_of
      AND (p_before_received_at IS NULL OR (p.received_at,p.id COLLATE "C") < (p_before_received_at,p_before_id COLLATE "C"))
    ORDER BY p.received_at DESC,p.id COLLATE "C" DESC LIMIT p_limit
  ) p;
  RETURN v_rows;
END
$fn$;

CREATE FUNCTION public.trade_bot_list_resolved(
  p_task text DEFAULT NULL, p_limit integer DEFAULT 100,
  p_before_received_at timestamptz DEFAULT NULL, p_before_id text DEFAULT NULL,
  p_as_of timestamptz DEFAULT statement_timestamp()
)
RETURNS jsonb LANGUAGE plpgsql STABLE SECURITY INVOKER SET search_path = pg_catalog AS $fn$
DECLARE v_rows jsonb;
BEGIN
  PERFORM trading_private.validate_read(p_task,p_limit,p_before_received_at,p_before_id,p_as_of);
  SELECT coalesce(jsonb_agg(p.record ORDER BY p.received_at DESC,p.id COLLATE "C" DESC),'[]'::jsonb)
  INTO v_rows FROM (
    SELECT p.id,p.received_at,to_jsonb(p)||jsonb_build_object('outcomes',jsonb_build_array(to_jsonb(o))) AS record
    FROM trading.predictions p JOIN trading.outcomes o ON o.prediction_id=p.id
    WHERE (p_task IS NULL OR p.task=p_task) AND p.received_at <= p_as_of AND o.received_at <= p_as_of
      AND (p_before_received_at IS NULL OR (p.received_at,p.id COLLATE "C") < (p_before_received_at,p_before_id COLLATE "C"))
    ORDER BY p.received_at DESC,p.id COLLATE "C" DESC LIMIT p_limit
  ) p;
  RETURN v_rows;
END
$fn$;

CREATE FUNCTION public.trade_bot_get_prediction(p_id text)
RETURNS jsonb LANGUAGE plpgsql STABLE SECURITY INVOKER SET search_path = pg_catalog AS $fn$
DECLARE v_record jsonb;
BEGIN
  PERFORM trading_private.require_principal();
  IF p_id IS NULL OR length(p_id) NOT BETWEEN 1 AND 200 OR p_id <> btrim(p_id) THEN
    RAISE EXCEPTION 'Invalid trading prediction ID' USING ERRCODE = '22023';
  END IF;
  SELECT to_jsonb(p) INTO v_record FROM trading.predictions p WHERE p.id=p_id;
  RETURN v_record;
END
$fn$;

CREATE FUNCTION public.trade_bot_list_unresolved(
  p_task text, p_now_ts bigint, p_limit integer DEFAULT 100,
  p_before_received_at timestamptz DEFAULT NULL, p_before_id text DEFAULT NULL,
  p_as_of timestamptz DEFAULT statement_timestamp()
)
RETURNS jsonb LANGUAGE plpgsql STABLE SECURITY INVOKER SET search_path = pg_catalog AS $fn$
DECLARE v_rows jsonb;
BEGIN
  PERFORM trading_private.validate_read(p_task,p_limit,p_before_received_at,p_before_id,p_as_of);
  IF NOT trading_private.valid_task(p_task) OR p_now_ts IS NULL OR p_now_ts < 0
     OR p_now_ts > floor(extract(epoch FROM p_as_of))::bigint THEN
    RAISE EXCEPTION 'Invalid unresolved trading task or time' USING ERRCODE = '22023';
  END IF;
  SELECT coalesce(jsonb_agg(to_jsonb(p) ORDER BY p.received_at DESC,p.id COLLATE "C" DESC),'[]'::jsonb)
  INTO v_rows FROM (
    SELECT p.* FROM trading.predictions p
    WHERE p.task=p_task AND p.received_at <= p_as_of AND p.ts + p.horizon_s <= p_now_ts
      AND NOT EXISTS (SELECT 1 FROM trading.outcomes o WHERE o.prediction_id=p.id AND o.received_at <= p_as_of)
      AND (p_before_received_at IS NULL OR (p.received_at,p.id COLLATE "C") < (p_before_received_at,p_before_id COLLATE "C"))
    ORDER BY p.received_at DESC,p.id COLLATE "C" DESC LIMIT p_limit
  ) p;
  RETURN v_rows;
END
$fn$;

-- Supabase may have broad defaults for newly created objects. Replace grants
-- only on the objects created here. Do not alter project-wide defaults.
REVOKE ALL ON ALL TABLES IN SCHEMA trading, trading_private FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA trading, trading_private FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA trading, trading_private FROM PUBLIC, anon, authenticated, service_role;
GRANT SELECT (user_id,principal_kind,enabled) ON trading_private.principals TO authenticated;
GRANT SELECT ON trading.predictions,trading.outcomes,trading.registry,trading.registry_scores TO authenticated;
GRANT INSERT (id,ts,model_version,task,features,prediction,confidence,horizon_s,cohort,meta)
  ON trading.predictions TO authenticated;
GRANT INSERT (prediction_id,resolved_ts,realized,meta) ON trading.outcomes TO authenticated;
GRANT EXECUTE ON FUNCTION trading_private.principal_kind(),trading_private.require_principal(boolean),
  trading_private.valid_task(text),trading_private.validate_read(text,integer,timestamptz,text,timestamptz)
  TO authenticated;

REVOKE ALL ON FUNCTION public.trade_bot_status(),public.trade_bot_append_prediction(jsonb),
  public.trade_bot_attach_outcome(jsonb),
  public.trade_bot_list_predictions(text,integer,timestamptz,text,timestamptz),
  public.trade_bot_list_resolved(text,integer,timestamptz,text,timestamptz),
  public.trade_bot_get_prediction(text),
  public.trade_bot_list_unresolved(text,bigint,integer,timestamptz,text,timestamptz)
  FROM PUBLIC,anon,authenticated,service_role;
GRANT EXECUTE ON FUNCTION public.trade_bot_status(),public.trade_bot_append_prediction(jsonb),
  public.trade_bot_attach_outcome(jsonb),
  public.trade_bot_list_predictions(text,integer,timestamptz,text,timestamptz),
  public.trade_bot_list_resolved(text,integer,timestamptz,text,timestamptz),
  public.trade_bot_get_prediction(text),
  public.trade_bot_list_unresolved(text,bigint,integer,timestamptz,text,timestamptz)
  TO authenticated;

-- Catch inherited grants or unexpected project defaults instead of silently
-- claiming isolation. This checks only the new objects and changes no roles.
DO $access_guard$
BEGIN
  IF EXISTS (
    SELECT 1 FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
    WHERE n.nspname IN ('trading','trading_private') AND c.relkind='r' AND (
      has_table_privilege('service_role',c.oid,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
      OR has_any_column_privilege('service_role',c.oid,'SELECT,INSERT,UPDATE,REFERENCES')
      OR has_table_privilege('anon',c.oid,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
      OR has_any_column_privilege('anon',c.oid,'SELECT,INSERT,UPDATE,REFERENCES')
      OR has_table_privilege('authenticated',c.oid,'INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
      OR has_any_column_privilege('authenticated',c.oid,'UPDATE,REFERENCES')
    )
  ) OR has_any_column_privilege('authenticated','trading_private.principals','INSERT')
    OR has_any_column_privilege('authenticated','trading.registry','INSERT')
    OR has_any_column_privilege('authenticated','trading.registry_scores','INSERT')
    OR has_column_privilege('authenticated','trading.predictions','received_at','INSERT')
    OR has_column_privilege('authenticated','trading.predictions','actor_user_id','INSERT')
    OR has_column_privilege('authenticated','trading.outcomes','received_at','INSERT')
    OR has_column_privilege('authenticated','trading.outcomes','actor_user_id','INSERT')
    OR has_schema_privilege('authenticated','trading','CREATE')
    OR has_schema_privilege('authenticated','trading_private','CREATE')
    OR EXISTS (
      SELECT 1 FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
      WHERE n.nspname='public' AND p.proname LIKE 'trade_bot_%' AND (
        has_function_privilege('anon',p.oid,'EXECUTE') OR has_function_privilege('service_role',p.oid,'EXECUTE')
      )
    ) THEN
    RAISE EXCEPTION 'Unexpected effective grants prevent trading isolation';
  END IF;
END
$access_guard$;

COMMENT ON SCHEMA trading IS 'Isolated trading evidence in the shared learning project. App access uses authenticated JWTs and an explicit principal allowlist.';
COMMENT ON TABLE trading_private.principals IS 'Operator-managed Auth-user allowlist. Initially empty. Verify the Auth identity before enabling a principal. No application grants permit enrollment.';
COMMENT ON FUNCTION public.trade_bot_status() IS 'shared-trading-v1. Does not report broker, strategy, hosted worker or live eligibility.';

NOTIFY pgrst, 'reload schema';
COMMIT;
