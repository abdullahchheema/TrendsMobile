-- Covison ERP — one-time database setup for a HOSTED Supabase project.
-- Run in Supabase Dashboard -> SQL Editor (as the default "postgres" user).
--
-- BEFORE RUNNING: replace  CHANGE_ME_TO_A_LONG_RANDOM_PASSWORD  with a new password made only of letters and
-- digits (no @ : / ? # % characters, so it can sit safely inside DATABASE_URL). Keep it for the Vercel environment.
-- The script is safe to run again.

-- 1. A dedicated, low-privilege database user for the Django application.
do $$
begin
  if not exists (select 1 from pg_roles where rolname = 'covison_app') then
    create role covison_app login nosuperuser nocreatedb nocreaterole noreplication;
  end if;
end$$;

alter role covison_app with password 'CHANGE_ME_TO_A_LONG_RANDOM_PASSWORD';

-- 2. Its own schema. All ERP tables live here, outside the "public" schema that Supabase exposes through its Data API.
grant covison_app to postgres;
create schema if not exists covison_app authorization covison_app;
alter role covison_app set search_path = covison_app, public;   -- poolers ignore connection options, so set it on the role
revoke all on schema covison_app from public, anon, authenticated, service_role;
grant usage on schema covison_app to postgres;
alter default privileges for role covison_app in schema covison_app grant all on tables to postgres;
alter default privileges for role covison_app in schema covison_app grant all on sequences to postgres;

-- 3. Small health function used by monitoring (no business data).
create schema if not exists api;
revoke all on schema api from public;
grant usage on schema api to anon, authenticated, service_role;
create or replace function api.health()
returns jsonb
language sql stable
set search_path = pg_catalog
as $$select jsonb_build_object('backend','Supabase','product','Covison ERP','currency','PKR')$$;
revoke all on function api.health() from public;
grant execute on function api.health() to anon, authenticated, service_role;

-- 4. Private storage buckets for PTA evidence and device photos (only the server's secret key can read or write them).
insert into storage.buckets (id, name, public, file_size_limit)
values ('pta-evidence', 'pta-evidence', false, 10485760), ('device-images', 'device-images', false, 10485760)
on conflict (id) do update set public = false, file_size_limit = excluded.file_size_limit;
