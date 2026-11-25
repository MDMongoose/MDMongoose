-- Fallback if mdm_init could not grant the app access automatically.
-- Names shown are the free-target defaults; use your target's catalog / *_schema_name values (databricks.yml).
-- Replace <app-sp> with the app's service principal application id
-- (Compute → Apps → mdm → Authorization) and `workspace` with your catalog.
GRANT USE CATALOG ON CATALOG `workspace` TO `<app-sp>`;
GRANT USE SCHEMA, SELECT, MODIFY ON SCHEMA `workspace`.`mdm_core` TO `<app-sp>`;
GRANT USE SCHEMA, SELECT ON SCHEMA `workspace`.`mdm_staging` TO `<app-sp>`;
GRANT USE SCHEMA, SELECT, CREATE TABLE, READ VOLUME, WRITE VOLUME ON SCHEMA `workspace`.`mdm_export` TO `<app-sp>`;  -- CREATE TABLE: View exports
