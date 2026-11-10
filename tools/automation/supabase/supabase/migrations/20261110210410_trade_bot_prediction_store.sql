-- Candidate for a NEW, DEDICATED trading project only. Never apply to the
-- existing NFL/learning database or an unrelated project. This migration refuses
-- a nonempty public schema. It does not migrate, delete, or reconcile old data.
-- Base columns match SelfLearning main 46be142de18823ec86be2e33d8a421ce272616a8.
-- Adds server receipt timestamps and the RPC needed by the release SupabaseStore.
BEGIN;

DO $guard$
BEGIN
  IF EXISTS (
    SELECT 1 FROM pg_catalog.pg_class c
    JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p', 'v', 'm', 'f', 'S')
  ) OR pg_catalog.to_regnamespace('trade_bot_private') IS NOT NULL
    OR EXISTS (
      SELECT 1 FROM pg_catalog.pg_proc p
      JOIN pg_catalog.pg_namespace n ON n.oid = p.pronamespace
      WHERE n.nspname = 'public' AND p.proname = 'selflearn_status'
    ) THEN
    RAISE EXCEPTION 'Fresh dedicated project required; existing public objects must be reviewed separately';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'service_role' AND rolbypassrls) THEN
    RAISE EXCEPTION 'Expected Supabase service_role with BYPASSRLS';
  END IF;
END;
$guard$;

CREATE SCHEMA trade_bot_private;
REVOKE ALL ON SCHEMA trade_bot_private FROM PUBLIC, anon, authenticated, service_role;

CREATE TABLE public.predictions (
  id TEXT PRIMARY KEY CHECK (length(btrim(id)) BETWEEN 1 AND 200),
  ts BIGINT NOT NULL CHECK (ts >= 0),
  model_version TEXT NOT NULL CHECK (length(btrim(model_version)) BETWEEN 1 AND 200),
  task TEXT NOT NULL CHECK (length(btrim(task)) BETWEEN 1 AND 200),
  features JSONB NOT NULL CHECK (jsonb_typeof(features) = 'object'),
  prediction JSONB NOT NULL,
  confidence DOUBLE PRECISION CHECK (confidence >= 0 AND confidence <= 1),
  horizon_s BIGINT NOT NULL CHECK (horizon_s BETWEEN 1 AND 315576000),
  cohort TEXT CHECK (cohort IS NULL OR length(cohort) <= 200),
  meta JSONB NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(meta) = 'object'),
  received_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX idx_pred_task_ts ON public.predictions (task, ts);
CREATE INDEX idx_pred_received ON public.predictions (received_at DESC, id);

CREATE TABLE public.outcomes (
  prediction_id TEXT PRIMARY KEY REFERENCES public.predictions(id),
  resolved_ts BIGINT NOT NULL CHECK (resolved_ts >= 0),
  realized JSONB NOT NULL,
  meta JSONB NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(meta) = 'object'),
  received_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);

CREATE TABLE public.registry (
  version TEXT PRIMARY KEY,
  task TEXT NOT NULL,
  config JSONB NOT NULL,
  created_ts BIGINT NOT NULL,
  status TEXT NOT NULL DEFAULT 'candidate' CHECK (status IN ('candidate', 'live', 'retired')),
  autonomy INTEGER NOT NULL DEFAULT 0 CHECK (autonomy BETWEEN 0 AND 4)
);
CREATE TABLE public.registry_scores (
  version TEXT NOT NULL REFERENCES public.registry(version),
  recorded_ts BIGINT NOT NULL,
  score JSONB NOT NULL
);
CREATE INDEX idx_regscores_version ON public.registry_scores(version, recorded_ts);

-- Grants are separate from RLS. Browser roles have neither access nor policies.
ALTER TABLE public.predictions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.outcomes ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.registry ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.registry_scores ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.predictions, public.outcomes, public.registry, public.registry_scores
  FROM PUBLIC, anon, authenticated, service_role;
GRANT USAGE ON SCHEMA public TO service_role;
GRANT SELECT ON TABLE public.predictions, public.outcomes, public.registry, public.registry_scores TO service_role;
GRANT INSERT (id, ts, model_version, task, features, prediction, confidence, horizon_s, cohort, meta)
  ON TABLE public.predictions TO service_role;
GRANT INSERT (prediction_id, resolved_ts, realized, meta) ON TABLE public.outcomes TO service_role;
-- Registry mutation/promotion is deliberately absent. Main has no hosted
-- registry implementation; service grants should be added only with that code.

CREATE FUNCTION trade_bot_private.reject_mutation() RETURNS trigger
LANGUAGE plpgsql SET search_path = '' AS $body$
BEGIN
  RAISE EXCEPTION 'Prediction and outcome evidence is append-only';
END;
$body$;
REVOKE ALL ON FUNCTION trade_bot_private.reject_mutation() FROM PUBLIC, anon, authenticated, service_role;
CREATE TRIGGER prediction_immutable BEFORE UPDATE OR DELETE ON public.predictions
  FOR EACH ROW EXECUTE FUNCTION trade_bot_private.reject_mutation();
CREATE TRIGGER prediction_no_truncate BEFORE TRUNCATE ON public.predictions
  FOR EACH STATEMENT EXECUTE FUNCTION trade_bot_private.reject_mutation();
CREATE TRIGGER outcome_immutable BEFORE UPDATE OR DELETE ON public.outcomes
  FOR EACH ROW EXECUTE FUNCTION trade_bot_private.reject_mutation();
CREATE TRIGGER outcome_no_truncate BEFORE TRUNCATE ON public.outcomes
  FOR EACH STATEMENT EXECUTE FUNCTION trade_bot_private.reject_mutation();

CREATE FUNCTION public.selflearn_status() RETURNS jsonb
LANGUAGE sql STABLE SECURITY INVOKER SET search_path = '' AS $body$
  SELECT jsonb_build_object(
    'predictions', (SELECT count(*) FROM public.predictions),
    'outcomes', (SELECT count(*) FROM public.outcomes),
    'unresolved', (SELECT count(*) FROM public.predictions p WHERE NOT EXISTS
      (SELECT 1 FROM public.outcomes o WHERE o.prediction_id = p.id)),
    'latest_received_at', (SELECT max(received_at) FROM public.predictions),
    'schema_version', 'trade_bot_prediction_store_v1'
  );
$body$;
REVOKE ALL ON FUNCTION public.selflearn_status() FROM PUBLIC, anon, authenticated, service_role;
GRANT EXECUTE ON FUNCTION public.selflearn_status() TO service_role;

COMMENT ON COLUMN public.predictions.ts IS 'Client asserted event time; not proof of forward observation.';
COMMENT ON COLUMN public.predictions.received_at IS 'Database receipt time; not writable by the API service role.';
COMMENT ON TABLE public.registry IS 'Compatibility shell only. No hosted promotion writer is enabled.';
NOTIFY pgrst, 'reload schema';
COMMIT;
