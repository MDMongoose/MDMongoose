"""MDMongoose app backend. The mdm package (repo/src) is shared with the jobs and pipelines."""

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2] / "src"
if (_SRC / "mdm").is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
