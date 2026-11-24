-- PostgREST's schema discovery uses authenticator before request role
-- settings apply. Keep service requests at their prior eight-second default.
begin;

alter role service_role set statement_timeout to '8s';
alter role authenticator set statement_timeout to '30s';
notify pgrst,'reload schema';

commit;
