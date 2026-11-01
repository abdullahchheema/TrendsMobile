do $$begin
if not exists(select 1 from pg_roles where rolname='covison_app') then
create role covison_app login nosuperuser nocreatedb nocreaterole noreplication;
end if;
end$$;
create schema if not exists covison_app authorization covison_app;
revoke all on schema covison_app from public,anon,authenticated,service_role;
grant connect on database postgres to covison_app;
grant usage on schema covison_app to postgres;
alter default privileges for role covison_app in schema covison_app grant all on tables to postgres;
alter default privileges for role covison_app in schema covison_app grant all on sequences to postgres;
