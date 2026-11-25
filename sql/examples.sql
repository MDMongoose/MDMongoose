-- Handy queries against the single hierarchy table and the export views.

-- One node and its properties
SELECT uid, name, types, parent_uid, properties FROM workspace.mdm_core.mdmnodes WHERE uid = 'MDM-1KY';

-- Everything below a node (no recursion: path holds all ancestors)
SELECT uid, name, depth FROM workspace.mdm_core.mdmnodes
WHERE array_contains(path, 'MDM-1KY') AND NOT is_deleted ORDER BY depth, name;

-- Golden records as a table (the demo Export node "Customer golden records", a materialized view)
SELECT * FROM workspace.mdm_export.customer_golden_records;

-- Who changed what (SCD2 history from the change data feed)
SELECT uid, name, modified_by, __START_AT, __END_AT FROM workspace.mdm_export.node_history
WHERE uid = 'MDM-1KF' ORDER BY __START_AT;
