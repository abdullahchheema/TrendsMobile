-- Infrastructure only. Business tables are added by reviewed CRM migrations.
create schema if not exists api;
revoke all on schema api from public;
grant usage on schema api to anon, authenticated, service_role;
create or replace function api.health()
returns jsonb
language sql stable
set search_path = pg_catalog
as $$select jsonb_build_object('backend','Supabase','product','Covison ERP','deployment','shared LAN','currency','PKR')$$;
revoke all on function api.health() from public;
grant execute on function api.health() to anon, authenticated, service_role;
-- The server stores files locally. Objects remain inaccessible to browser clients
-- until the CRM adds explicit shop/user policies; service-role access is server only.
insert into storage.buckets (id,name,public,file_size_limit)
values ('pta-evidence','pta-evidence',false,10485760),('device-images','device-images',false,10485760)
on conflict (id) do update set public=false,file_size_limit=excluded.file_size_limit;
notify pgrst,'reload schema';
