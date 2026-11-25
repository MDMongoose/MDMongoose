"""MDMongoose backend (Databricks Apps): `uvicorn backend.main:app`."""

from .core.app import create_app

app = create_app()
