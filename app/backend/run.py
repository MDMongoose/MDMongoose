import os
import sys
from pathlib import Path

import uvicorn

# Started as `python app/backend/run.py` from the repo root (Databricks Apps) or as
# `python -m backend.run` from app/ (local); either way `backend` must be importable.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

if __name__ == "__main__":
    uvicorn.run("backend.main:app", host="0.0.0.0", port=int(os.getenv("DATABRICKS_APP_PORT", "8000")),
                proxy_headers=True, forwarded_allow_ips="*")
