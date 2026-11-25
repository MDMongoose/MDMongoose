# MDMongoose — MDM without Madness

<img src="app/frontend/src/assets/mdmongoose-logo.svg" alt="MDMongoose logo" width="96" />

**MDMongoose** is master data management that runs entirely inside Databricks: **one hierarchy in one Delta
table (`MDMNodes`)**, a React UI on **Databricks Apps**, loading and exports in **Spark Declarative Pipelines**,
and matching, golden records and other heavy work in **serverless jobs**. Everything deploys from this one
**Declarative Automation Bundle** (formerly Databricks Asset Bundles).

```
 upstream ─► <staging table> ─► mdm_load pipeline ─► rec_<table> ─► merge ─► MDMNodes ─► mdm_core ─► mdm_export ─► views, files,
 (e.g. SDP)  (Delta, change      (change feed +       (AUTO CDC)            (one Delta   (matching,    pipeline      node_index,
             data feed ON)        expectations)                              table)       golden recs)               SCD2 history
                                                                               ▲
                                              React UI ─► FastAPI (Apps) ──────┘  SQL warehouse, one MERGE per save
```

## What is in the bundle

`databricks.yml` includes `resources/*.yml`. One deploy creates or updates all of the resources below. Every job
and pipeline is serverless, and every job writes one row per activity to the run log
`<catalog>.<core schema>.mdm_log`, linked to its job run.

### Jobs

| Job | What it does | Parameters | Safe to re-run? | Run it when |
|---|---|---|---|---|
| **mdm_init** | Creates or upgrades `MDMNodes` and `mdm_log`, makes sure the schemas and the volume exist, seeds the system and model nodes (and the demo, see below), grants the app access. Then runs the load pipeline, merges every Table, matches, refreshes the golden records and the exports. | `catalog` (default: the `catalog` variable), `demo` (`true` / `false`) | **Yes.** Seeded nodes are only inserted when missing, so edits made in the app survive; the UID counter is never reset. An upgrade of the table layout runs `OPTIMIZE … FULL` once, which can take minutes on a large table. | After the first deploy, and **after every deploy of a new version** — it adds new system definitions to existing NodeTypes without touching your edits. |
| **mdm_load** | Updates the load pipeline once for the chosen Table node(s) (incremental tables: their change feed; full-reload tables: a full refresh), then merges each Table's changes into its branch of `MDMNodes` — a *For each* task, up to 4 Tables at the same time — then, for Tables with *Run Golden After Load*, matches and refreshes the golden records that use them. Finally starts `mdm_export`. Tasks: `prepare` → `has_tables` → `load_tables` (one iteration per Table) → `after_load`. | `table_uid` (`all`, `due`, or Table node UIDs, comma-separated), `user`, `exports` (`all` / `none`) | **Yes.** An incremental load only reads the changes since the version it merged last; a run with nothing new changes nothing. Up to 4 runs at the same time; a run that would load a Table an earlier run is still loading waits for it. | At the end of your upstream data load (see [Triggering MDM after a data load](#triggering-mdm-after-a-data-load)). The app's *Load Childs* starts it too. |
| **mdm_core** | Matching, golden records and background actions. Matching and golden records run with the Master Data node's **Processing**: *One Machine* (all records on the job's driver; the default) or *Distributed* (spread over the job's Spark cluster, for very large tables). Both give the same result; *Exact keys + Splink* always uses One Machine. | `action` (`match`, `mdm.golden.refresh`, `mdm.export.run`, `mdm_custom.<module>.<fn>`), `uid` (node UIDs, comma-separated; `all` for `match`), `mode` (`incremental` / `full`), `preview` (`true` writes nothing), `golden` (`true`: refresh the golden records after matching), `exports` (`none` / `all` / Export node UIDs), `user`, `options` | **Yes.** Incremental matching only re-matches changed records and the entities they touch; steward decisions and golden UIDs are kept. `preview=true` only logs what would change. Runs on different Master Data nodes run in parallel; a run on the same node waits. | Started by the app (*Run Matching*, *Refresh Golden Records*, custom actions). By hand: `action=match, mode=full` after changing a Match Rule on many records. |
| **mdm_match_train** | Trains the Splink model of a Match Rule (strategy *Exact keys + Splink*). | `uid` (the Match Rule or its Master Data node), `activate` (`true`: use the new model at once; `false`: save it as a draft to compare), `user` | **Yes.** Every run saves a new model version; the active model only changes with `activate=true` (the first model is always active). | After steward decisions have built up, or after changing the compared properties. The app's *Train Match Model* starts it. |
| **mdm_export** | Writes the CSV / Excel files of the Export nodes to the export volume, then updates the export pipeline (materialized views, `node_index`, history). Each Export node gets *Last Run*. | `uid` (`all` or Export node UIDs), `user` | **Yes.** Files are overwritten and the views refreshed. One run at a time; further runs queue. | Started after every load and golden-record refresh. By hand after editing Export nodes outside the app. |
| **mdm_refresh** | Hourly: runs `mdm_load` for every Table whose *Schedule* (cron, UTC) fired in the last hour. | — | **Yes.** No table due = nothing happens. | **Paused** after deploy. Unpause it only if you use Table schedules instead of triggering loads from upstream. |

### Pipelines

| Pipeline | What it does |
|---|---|
| **mdm_load** | Metadata-driven: reads the Table nodes from `MDMNodes` and declares, per table, a `changes_<key>` view over the staging table's change data feed (with the Table's expectations) and a `rec_<key>` streaming table (AUTO CDC, SCD1, change data feed on). Target: the staging schema. |
| **mdm_export** | Publishes `nodes`, `node_edges`, `node_index` (UID lookup and search), `node_history` (SCD2) and one materialized view per Export node of type *Materialized view*. Target: the export schema. |

Both are started by the jobs; you do not need to run them yourself.

### App, schemas and volume

| Resource | What |
|---|---|
| **App `mdm`** (`app_name`) | The React UI and the FastAPI backend. Its source is the repository root: on deploy Databricks Apps runs `npm install`, `pip install -r requirements.txt` and `npm run build`, so nothing has to be built first. It gets the SQL warehouse and *Can manage run* on `mdm_core`, `mdm_load`, `mdm_export` and `mdm_match_train`. `databricks bundle run mdm` deploys and starts it. |
| **Schema `mdm_core`** | `MDMNodes` (clustered by `path_key`) and the run log `mdm_log` |
| **Schema `mdm_staging`** | Your source tables and the load snapshots `rec_*` |
| **Schema `mdm_export`** | Materialized views, views, the search index and the export volume |
| **Volume `exports`** | CSV and Excel files written by background exports |

### Variables

| Variable | Default | Meaning |
|---|---|---|
| `catalog` | `workspace` | Unity Catalog catalog of every MDM object |
| `core_schema_name` | `mdm_core` | Schema of `MDMNodes` and `mdm_log` |
| `staging_schema_name` | `mdm_staging` | Schema of the source tables and `rec_*` (target of the `mdm_load` pipeline) |
| `export_schema_name` | `mdm_export` | Schema of the exported views, the search index and the export volume (target of the `mdm_export` pipeline) |
| `export_volume` | `exports` | Volume for exported CSV / Excel files |
| `demo` | `"true"` | Seed the demo data — set `"false"` for a real installation |
| `app_name` | `mdm` | Name of the Databricks App |
| `warehouse_id` | lookup of *Serverless Starter Warehouse* | SQL warehouse the app reads and writes `MDMNodes` with |

Override a variable per target in `databricks.yml`, or on the command line:
`databricks bundle deploy -t uat --var="catalog=uat_catalog"`.

### Targets

Each target is one MDM installation.

| Target | Mode | Workspace path | Notes |
|---|---|---|---|
| `free` (default) | — | `~/.bundle/mdm/free` | Databricks Free Edition or any serverless workspace. No name prefixes, so schema and table names stay fixed. |
| `dev` | development | `~/.bundle/mdm/dev` | Resources prefixed `[dev <user>]`, schedules paused, app `mdm-dev`. |
| `uat` | — | `/Workspace/Shared/.bundle/mdm/uat` | Names prefixed `[uat] `, app `mdm-uat`. |
| `prod` | production | `/Workspace/Shared/.bundle/mdm/prod` | |

Jobs and pipelines refer to the schemas as `${resources.schemas.<key>.name}`, so a prefix a target adds is
followed everywhere. The app reads the schema and volume names — and the target, shown as *Environment* in the
logo tooltip — from the arguments of the `mdm_core` job, so `app.yaml` has nothing per target.

## Deployment

### From a Git folder in the workspace (no local tools)

1. Create a Git folder from this repository (Workspace → Create → Git folder).
2. Open `databricks.yml`, then the **Deployments** panel (rocket icon). Choose the target and click **Deploy**.
3. In the same panel, under *Bundle resources*, run the job **mdm_init** and wait for it to finish.
4. Start the app **mdm** (Compute → Apps → *mdm*) and open its URL.

### With the Databricks CLI

```bash
databricks bundle deploy -t <target>
databricks bundle run -t <target> mdm_init   # table, seeds, (demo), first load, matching, golden records, exports
databricks bundle run -t <target> mdm        # deploys and starts the app
```

### Existing schemas

A deploy does not take over a schema or volume that already exists — it fails with *already exists*. Bind each
existing one once per target, then deploy:

```bash
databricks bundle deployment bind mdm_core    <catalog>.<core_schema_name>    -t <target>
databricks bundle deployment bind mdm_staging <catalog>.<staging_schema_name> -t <target>
databricks bundle deployment bind mdm_export  <catalog>.<export_schema_name>  -t <target>
databricks bundle deployment bind exports     <catalog>.<export_schema_name>.<export_volume> -t <target>
```

> **Warning:** `databricks bundle destroy` drops the created or bound schemas **with all their tables and data**.

### Permissions

If `mdm_init` prints that it could not grant the app access, run `sql/grants.sql` once. Admins of the app come
from the `admins` group, or from `MDM_ADMINS` in `app.yaml`.

### App speed settings (`app.yaml`)

| Setting | Default | What |
|---|---|---|
| `MDM_WARM_START` | `true` | At start, and after a job's write emptied the app's cache, read the top four levels and the first 120 property pages, so the first user finds them cached. |
| `MDM_KEEP_WARM` | empty (off) | A time window such as `08:00-18:00`: every 4 minutes inside it the app sends `SELECT 1`, so the SQL warehouse does not auto-stop during working hours. Costs warehouse time. |
| `MDM_KEEP_WARM_TZ`, `MDM_KEEP_WARM_DAYS` | `Europe/Budapest`, `Mon-Fri` | The time zone and the days of that window. |
| `MDM_SLOW_MS` | `1500` | API requests slower than this are written to the app log with their slowest SQL statements. Every API response has a `Server-Timing` header (browser dev tools → Network → Timing): total time, SQL time and number of statements. |

The definitions (NodeTypes, Actions, the Enums — not their values — and the permission nodes) are read together in a
few statements and refreshed every 30 seconds in the background; the check whether a job changed `MDMNodes`
also runs in the background while the app is in use. A page view never waits for either.

Enum values and the nodes a typed Link may point to are never loaded as whole lists: a dropdown searches them in
the database 40 at a time, a typed name ("Hungary") is looked up by that name, and a page names all the values it
shows with one batched lookup.

### Upgrading

1. Pull the new version into the Git folder (or check it out locally).
2. Deploy again.
3. Run **mdm_init** once: it adds new system definitions, upgrades the table layout if needed and leaves your
   edits alone.

### Databricks Free Edition

Use the default target `free`; the catalog `workspace` exists there.

- Everything is serverless; the app uses the *Serverless Starter Warehouse*.
- Apps stop automatically after a while. Start the app again from the Apps page — all data stays in Delta.
- Background actions start a serverless job: expect about a minute of start-up. Saves in the app are direct
  and take a few seconds.
- Keep **mdm_refresh** paused unless you need scheduled loads: Free Edition has a daily compute quota.

## Triggering MDM after a data load

MDMongoose does not load from source systems itself. Your lakehouse — typically a **Spark Declarative
Pipeline** — lands each source in a staging table; MDMongoose picks up the **changes** from there.

### 1. Prepare the staging table

The load reads the staging table's **change data feed**, so it must be a Delta table with the change data feed
on. In a Spark Declarative Pipeline, set it on the table that MDM reads:

```python
from pyspark import pipelines as dp

@dp.table(name="src_sap_kna1", table_properties={"delta.enableChangeDataFeed": "true"})
def src_sap_kna1():
    return spark.readStream.table("bronze.sap_kna1")   # your ingestion
```

For a table written outside a pipeline:
`ALTER TABLE <catalog>.<schema>.src_sap_kna1 SET TBLPROPERTIES (delta.enableChangeDataFeed = true)`.

The table can be in any catalog and schema that the app and the load pipeline can read; `mdm_staging` is the default. Use a streaming
table or a regular Delta table — the load works on its inserts, updates and deletes. In the app, create a
**Table node** for it with *Configure Load* (it refuses a table without the change data feed), and tick
**Run Golden After Load** if every load should also match and refresh the golden records. Note the Table
node's **UID**.

### 2. Start `mdm_load` when the upstream load has finished

**Option A — in the same Lakeflow job (recommended).** Add a task after your pipeline task that runs the
`mdm_load` job for the Table node:

```yaml
# in your own bundle
variables:
  mdm_load_job_id:
    lookup:
      job: mdm_load            # in uat: "[uat] mdm_load"

resources:
  jobs:
    sap_customers:
      name: sap_customers
      tasks:
        - task_key: ingest
          pipeline_task:
            pipeline_id: ${resources.pipelines.sap_ingest.id}
        - task_key: mdm_load
          depends_on: [{ task_key: ingest }]
          run_job_task:
            job_id: ${var.mdm_load_job_id}
            job_parameters:
              table_uid: MDM-XXXX   # the Table node(s), comma-separated; "all" loads every Table
```

The MDM step runs only when the ingest succeeded, and it shows in the same job run.

**Option B — from code or the CLI**, for example at the end of a notebook or from an orchestrator:

```bash
databricks jobs run-now --json '{"job_id": <mdm_load job id>, "job_parameters": {"table_uid": "MDM-XXXX"}}'
```

```python
from databricks.sdk import WorkspaceClient
WorkspaceClient().jobs.run_now(job_id=<mdm_load job id>, job_parameters={"table_uid": "MDM-XXXX"})
```

**Option C — on a schedule.** Set a *Schedule* (cron, UTC) on the Table node and unpause **mdm_refresh**. Use it
only when the upstream load runs at a known time.

### What happens next

`mdm_load` runs one load at a time and queues the rest, so several upstream jobs can trigger it safely. For each
run it:

1. updates the `mdm_load` pipeline for the table (only the new changes for an incremental table),
2. merges the changes into the Table's branch of `MDMNodes` — values people edited in the app (*UserDefined*)
   are kept,
3. matches and refreshes the golden records, if *Run Golden After Load* is ticked,
4. starts `mdm_export`, so views, files and `node_history` follow.

Each step writes to `mdm_log`; the job run shows the details.

## Demo data

With `demo = "true"`, `mdm_init` creates a complete example: source tables in the staging schema (change data
feed on), their Table nodes, Match and Survivorship Rules, and the golden records built from them. The data is
generated deterministically by `src/mdm/demo_data.py` — invented companies and people, `.example` mail domains.

| Master Data | Sources (staging schema) | Rows | Golden records |
|---|---|---|---|
| **Customer** | `src_sap_kna1` (SAP), `src_sfdc_account` (Salesforce), `src_shop_customers` (webshop) | 831 + 568 + 362 | 1 004 |
| **Supplier** | `src_sap_lfa1` (SAP), `src_coupa_suppliers` (Coupa) | 179 + 133 | 200 |
| **Employee** | `src_workday_workers` (Workday), `src_ad_users` (Active Directory) | 300 + 291 | 300 |
| **Product** | entered by hand, no source: Division › Category › Subcategory › Product Line › Product Family › Product | 50 products, 6 levels | — |

The sources disagree the way real ones do: the same company with different spellings and legal-form variants
(`GmbH` / `Gmbh`, `s.r.o.` / `s. r. o.`), upper-case SAP names, missing or space-formatted VAT IDs, local and
English city names, typos, duplicate SAP accounts, stale AD departments, terminated workers without an AD
account. Customers match on VAT ID, then on a fuzzy name within the same country; suppliers on VAT ID and name;
employees on Employee ID only. The *Countries* enum has 7 countries: HU, DE, AT, CH, CZ, SK, RO.

Running `mdm_init` again on an existing deployment adds new demo rows to the demo source tables by key (rows you
changed or deleted stay as they are) and refreshes the Customer, Supplier and Employee golden records.

To try the load yourself, change a row in a demo source table, then run `mdm_load` for its Table node — or click
*Load Childs* on the Table node in the app.

For a real installation, deploy with `--var="demo=false"`.

## More

- How to add an action with its own dialog: [app/customization.md](app/customization.md).
- Custom survivorship functions and background action handlers live in `src/mdm_custom/`.
