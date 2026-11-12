-- Expose the website's read-only views through the "api" schema (the one the Supabase Data API serves).
-- The ERP's own tables and views live in the private "covison_app" schema, which anon can never read.
-- Run in the Supabase SQL editor AFTER update-database.ps1 has applied the migrations. Safe to run again.
-- Also run it again after any ERP update whose release notes say the website views changed.

create schema if not exists api;
grant usage on schema api to anon;

do $$
declare v text;
begin
  foreach v in array array['web_families','web_listings','web_variants','web_images'] loop
    if exists (select 1 from information_schema.views where table_schema = 'covison_app' and table_name = v) then
      execute format('create or replace view api.%I as select * from covison_app.%I', v, v);
      execute format('grant select on api.%I to anon', v);
      raise notice 'exposed: api.%', v;
    else
      raise notice 'MISSING in covison_app (run update-database.ps1 first): %', v;
    end if;
  end loop;
end $$;

-- The per-shop views name the shops that hold stock. The public website must never learn that, so they are NOT exposed through the Data API
-- (anyone holding the public key could read them). The website does not use them. Safe to run again.
drop view if exists api.web_variant_stock, api.web_stock, api.web_phone_units;
