"""Where MDM's objects live in Unity Catalog. Set per bundle target (databricks.yml variables):

    <catalog>.<core>.mdmnodes, .mdm_log          the hierarchy and the run log
    /Volumes/<catalog>/<export>/<volume>/...     export files (CSV / Excel)
    <catalog>.<staging>.rec_*, demo sources       load snapshots of the source tables
    <catalog>.<export>.<Object Name>, node_index materialized views, views, the search index

The app reads them from environment variables, jobs from their arguments, pipelines from their configuration.
"""

from __future__ import annotations

import os

CATALOG = os.getenv("MDM_CATALOG", "workspace")
CORE = os.getenv("MDM_CORE_SCHEMA", "mdm_core")
STAGING = os.getenv("MDM_STAGING_SCHEMA", "mdm_staging")
EXPORT = os.getenv("MDM_EXPORT_SCHEMA", "mdm_export")
VOLUME = os.getenv("MDM_EXPORT_VOLUME", "exports")
TARGET = os.getenv("MDM_TARGET", "")  # bundle target (free, dev, uat, prod): shown in the app as the environment


def configure(catalog: str | None = None, core: str | None = None, staging_schema: str | None = None,
              export: str | None = None, volume: str | None = None) -> None:
    """Set the names (jobs and pipelines call this with their parameters); empty values keep the defaults."""
    global CATALOG, CORE, STAGING, EXPORT, VOLUME
    CATALOG = catalog or CATALOG
    CORE = core or CORE
    STAGING = staging_schema or STAGING
    EXPORT = export or EXPORT
    VOLUME = volume or VOLUME


def nodes_table(catalog: str | None = None) -> str:
    return f"{catalog or CATALOG}.{CORE}.mdmnodes"


def volume_path(catalog: str | None = None) -> str:
    return f"/Volumes/{catalog or CATALOG}/{EXPORT}/{VOLUME}"
