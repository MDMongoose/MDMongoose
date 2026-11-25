"""mdm_init: make sure the schemas and the export volume exist (deploy creates them), create/upgrade MDMNodes (path_key layout +
OPTIMIZE), seed nodes, CDF demo sources, grants.

Idempotent: seeded nodes are only inserted when missing, so edits made in the
UI survive a redeploy; the UID counter is never reset.
"""

from _common import log_sink, repo_for, setup

args, spark = setup("demo", "app_name", demo="true")
from mdm import names  # noqa: E402
from mdm.seed import DEMO_SOURCE_ROWS, all_seed, upgrade_existing, upgrade_release  # noqa: E402

cat = args.catalog
demo = args.demo.lower() == "true"
# existing schemas and the volume are used as they are; missing ones are created
for schema, comment in ((names.CORE, "MDMNodes — the hierarchy and the run log"),
                        (names.STAGING, "Source tables and load snapshots (rec_*)"),
                        (names.EXPORT, "Materialized views, views, the search index and the export files volume")):
    spark.sql(f"CREATE SCHEMA IF NOT EXISTS `{cat}`.`{schema}` COMMENT '{comment}'")
spark.sql(f"CREATE VOLUME IF NOT EXISTS `{cat}`.`{names.EXPORT}`.`{names.VOLUME}` "
          "COMMENT 'Export files written by background export runs'")
print(f"schemas {cat}.{names.CORE}, {names.STAGING}, {names.EXPORT} and volume {names.volume_path(cat)} ready")
repo = repo_for(spark, cat)
for note in repo.ensure_table():
    print(note)
sink = log_sink(repo)  # creates mdm_log
from mdm import runlog  # noqa: E402

with runlog.activity(sink, "init", None, "demo" if demo else "plain", "mdm_init", args.job_run_id) as lg:
    seeds = all_seed(cat, names.STAGING, demo)
    added = repo.insert_missing(seeds, "mdm_init")
    print(f"seeded {added} new node(s) into {repo.table}")
    upgraded = upgrade_existing(repo, seeds)
    print(f"upgraded {upgraded} existing definition node(s)")
    notes = upgrade_release(repo, seeds)
    for note in notes:
        print(note)
    lg.stats.update(seeded=added, upgraded=upgraded, notes=notes)

if demo:
    # demo sources: created with change data feed on; on an existing deployment the new demo rows
    # are MERGEd in by key (rows you changed or deleted stay as they are)
    for table, spec in DEMO_SOURCE_ROWS.items():
        full = f"{cat}.{names.STAGING}.{table}"
        df = spark.createDataFrame(spec["rows"], ", ".join(f"`{c}` STRING" for c in spec["columns"]))
        if not spark.catalog.tableExists(full):
            df.write.option("delta.enableChangeDataFeed", "true").saveAsTable(full)
            spark.sql(f"ALTER TABLE {full} SET TBLPROPERTIES (delta.enableChangeDataFeed = true)")
            print(f"created demo source {full}: {len(spec['rows'])} rows (change data feed on)")
            continue
        spark.sql(f"ALTER TABLE {full} SET TBLPROPERTIES (delta.enableChangeDataFeed = true)")
        have = set(spark.table(full).columns)
        cols = [c for c in spec["columns"] if c in have]
        key = spec["columns"][0]
        df.createOrReplaceTempView("_demo_rows")
        before = spark.table(full).count()
        spark.sql(f"MERGE INTO {full} t USING _demo_rows s ON t.`{key}` = s.`{key}` "
                  f"WHEN NOT MATCHED THEN INSERT ({', '.join(f'`{c}`' for c in cols)}) "
                  f"VALUES ({', '.join(f's.`{c}`' for c in cols)})")
        print(f"demo source {full}: {spark.table(full).count() - before} new row(s) added")

if args.app_name:
    try:
        from databricks.sdk import WorkspaceClient
        sp = WorkspaceClient().apps.get(args.app_name).service_principal_client_id
        stmts = [f"GRANT USE CATALOG ON CATALOG `{cat}` TO `{sp}`"]
        for schema, privs in ((names.CORE, "USE SCHEMA, SELECT, MODIFY"),
                              (names.STAGING, "USE SCHEMA, SELECT"),
                              (names.EXPORT, "USE SCHEMA, SELECT, CREATE TABLE, READ VOLUME, WRITE VOLUME")):  # CREATE TABLE: View exports
            stmts.append(f"GRANT {privs} ON SCHEMA `{cat}`.`{schema}` TO `{sp}`")
        for s in stmts:
            spark.sql(s)
        print(f"granted the app service principal {sp} access")
    except Exception as e:  # noqa: BLE001
        print(f"WARNING: could not grant the app access automatically ({e}). Run the GRANTs in README.md.")
