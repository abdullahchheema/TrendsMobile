-- Run this in the Supabase SQL editor BEFORE update-database.ps1 when a release changes the website views.
-- (The copies in the "api" schema depend on the ERP's views, so they have to be removed first and re-created afterwards.)
-- Afterwards run supabase/hosted/web-views.sql again. The website is unable to read for the minute in between.
drop view if exists api.web_families, api.web_listings, api.web_variants, api.web_variant_stock, api.web_stock, api.web_phone_units, api.web_images;
